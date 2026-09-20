import pytest
import pytest_asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from backend.database.base import Base
from backend.domain.models import Deal, EscrowWallet, BlockchainTransaction
from backend.domain.enums import DealStatus, TransactionType, TransactionStatus
from backend.domain.schemas import DealCreate, DealAgree
from backend.core.security import encrypt_private_key, decrypt_private_key
from backend.core.state_machine import can_transition, validate_transition, InvalidStateTransitionError
from backend.services.wallet_service import WalletService
from backend.services.deal_service import DealService
from backend.services.escrow_service import EscrowService
from backend.services.payout_service import PayoutService
from backend.services.refund_service import RefundService
from backend.services.monitor_service import TransactionMonitor
from backend.blockchain.evm_adapter import EVMAdapter
from backend.core.config import settings


@pytest_asyncio.fixture
async def async_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session

    await engine.dispose()


# 1. Wallet Generation, Encryption & Decryption
def test_wallet_generation_and_encryption():
    address, encrypted_key = WalletService.generate_wallet()
    assert address.startswith("0x")
    assert len(address) == 42
    assert WalletService.validate_address(address) is True

    # Ciphertext should not match raw hex
    assert encrypted_key != address
    assert not encrypted_key.startswith("0x")

    # Decrypt round-trip
    decrypted = decrypt_private_key(encrypted_key)
    assert decrypted.startswith("0x")
    assert len(decrypted) == 66  # 32 bytes in hex + 0x prefix


# 2. Address Validation
def test_address_validation():
    assert WalletService.validate_address("0x742d35Cc6634C0532925a3b844Bc454e4438f44e") is True
    assert WalletService.validate_address("invalid-address") is False
    assert WalletService.validate_address("") is False
    assert WalletService.validate_address(None) is False


# 3. State Machine Transitions
def test_state_machine_transitions():
    # Valid transitions
    assert can_transition(DealStatus.CREATED, DealStatus.WAITING_FOR_AGREEMENT) is True
    assert can_transition(DealStatus.WAITING_FOR_AGREEMENT, DealStatus.AGREED) is True
    assert can_transition(DealStatus.AGREED, DealStatus.ESCROW_CREATING) is True
    assert can_transition(DealStatus.ESCROW_CREATING, DealStatus.WAITING_FOR_PAYMENT) is True
    assert can_transition(DealStatus.WAITING_FOR_PAYMENT, DealStatus.PAYMENT_DETECTED) is True
    assert can_transition(DealStatus.PAYMENT_DETECTED, DealStatus.FUNDED) is True
    assert can_transition(DealStatus.FUNDED, DealStatus.RELEASE_PENDING) is True

    # Invalid transitions
    assert can_transition(DealStatus.CREATED, DealStatus.FUNDED) is False
    assert can_transition(DealStatus.WAITING_FOR_PAYMENT, DealStatus.RELEASED) is False
    assert can_transition(DealStatus.COMPLETED, DealStatus.REFUNDED) is False

    with pytest.raises(InvalidStateTransitionError):
        validate_transition(DealStatus.CREATED, DealStatus.COMPLETED)


# 4. Deal Creation, Agreement & Terms Locking
@pytest.mark.asyncio
async def test_deal_creation_and_agreement(async_db: AsyncSession):
    deal_in = DealCreate(
        buyer_id="buyer_123",
        seller_id="seller_456",
        amount="0.05",
        token="ETH",
        network="SEPOLIA"
    )
    deal = await DealService.create_deal(async_db, deal_in, actor="buyer_123")
    assert deal.id is not None
    assert deal.status == DealStatus.WAITING_FOR_AGREEMENT
    assert deal.terms_locked is False

    # Buyer agrees
    deal = await DealService.agree_deal(
        async_db,
        deal.id,
        DealAgree(user_id="buyer_123", buyer_refund_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e")
    )
    assert deal.buyer_agreed is True
    assert deal.seller_agreed is False
    assert deal.terms_locked is False

    # Seller agrees -> Both agreed -> Terms locked
    deal = await DealService.agree_deal(
        async_db,
        deal.id,
        DealAgree(user_id="seller_456", seller_payout_address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")
    )
    assert deal.seller_agreed is True
    assert deal.terms_locked is True
    assert deal.status == DealStatus.AGREED


# 5. Escrow Wallet Creation (Only after terms locked)
@pytest.mark.asyncio
async def test_escrow_wallet_creation_strictly_after_agreement(async_db: AsyncSession):
    deal_in = DealCreate(
        buyer_id="b1",
        seller_id="s1",
        amount="1.0",
        token="ETH",
        network="SEPOLIA"
    )
    deal = await DealService.create_deal(async_db, deal_in, actor="b1")

    # Attempt to create wallet before agreement -> should fail (HTTP 400)
    with pytest.raises(Exception) as exc_info:
        await EscrowService.create_escrow_wallet_for_deal(async_db, deal.id, actor="b1")
    assert "agree to lock terms first" in str(exc_info.value)

    # Agree both
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="b1"))
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="s1"))

    # Now creation succeeds
    wallet = await EscrowService.create_escrow_wallet_for_deal(async_db, deal.id, actor="b1")
    assert wallet.address.startswith("0x")
    assert deal.status == DealStatus.WAITING_FOR_PAYMENT


# 6. Underpayment & Deposit Verification
@pytest.mark.asyncio
async def test_deposit_verification_underpayment_and_success():
    mock_gateway = MagicMock(spec=EVMAdapter)
    mock_gateway.to_checksum = lambda a: a

    # Underpayment test
    mock_gateway.verify_deposit = AsyncMock(return_value=(False, "Underpayment: transferred 0.01 < expected 0.05", 1))
    is_valid, reason, confs = await mock_gateway.verify_deposit("0xtx", "0xaddr", Decimal("0.05"))
    assert is_valid is False
    assert "Underpayment" in reason

    # Success test
    mock_gateway.verify_deposit = AsyncMock(return_value=(True, "Valid deposit", 3))
    is_valid, reason, confs = await mock_gateway.verify_deposit("0xtx", "0xaddr", Decimal("0.05"))
    assert is_valid is True
    assert confs == 3


# 7. Payout Idempotency (Double Payout Protection)
@pytest.mark.asyncio
async def test_payout_idempotency(async_db: AsyncSession):
    deal_in = DealCreate(
        buyer_id="buyer_x",
        seller_id="seller_y",
        amount="0.1",
        token="ETH",
        network="SEPOLIA",
        seller_payout_address="0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
    )
    deal = await DealService.create_deal(async_db, deal_in, actor="buyer_x")
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="buyer_x"))
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="seller_y"))
    await EscrowService.create_escrow_wallet_for_deal(async_db, deal.id, actor="buyer_x")

    # Manually transition to FUNDED
    deal.status = DealStatus.FUNDED
    await async_db.commit()

    # Mock gateway
    mock_gateway = MagicMock(spec=EVMAdapter)
    mock_gateway.validate_address.return_value = True
    mock_gateway.to_checksum = lambda a: a
    mock_gateway.get_balance = AsyncMock(return_value=Decimal("0.1"))
    mock_gateway.broadcast_signed_transaction = AsyncMock(return_value="0xreal_payout_hash_123")

    mock_w3 = MagicMock()
    mock_w3.eth.get_transaction_count.return_value = 0
    mock_w3.eth.gas_price = 1000000000
    mock_w3.eth.get_balance.return_value = int(Decimal("0.1") * Decimal(10**18))
    mock_gateway.w3 = mock_w3

    payout_service = PayoutService(gateway=mock_gateway)

    # First payout execution
    tx1 = await payout_service.execute_payout(async_db, deal.id, requester_id="buyer_x")
    assert tx1.tx_hash == "0xreal_payout_hash_123"
    assert tx1.type == TransactionType.PAYOUT

    # Second payout execution (Immediate Idempotency check)
    tx2 = await payout_service.execute_payout(async_db, deal.id, requester_id="buyer_x")
    assert tx2.tx_hash == "0xreal_payout_hash_123"

    # Ensure broadcast was called ONLY ONCE
    assert mock_gateway.broadcast_signed_transaction.call_count == 1


# 8. Refund Execution & Double Refund Protection
@pytest.mark.asyncio
async def test_refund_authorization_and_idempotency(async_db: AsyncSession):
    deal_in = DealCreate(
        buyer_id="b_refund",
        seller_id="s_refund",
        amount="0.2",
        token="ETH",
        network="SEPOLIA",
        buyer_refund_address="0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    )
    deal = await DealService.create_deal(async_db, deal_in, actor="b_refund")
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="b_refund"))
    await DealService.agree_deal(async_db, deal.id, DealAgree(user_id="s_refund"))
    await EscrowService.create_escrow_wallet_for_deal(async_db, deal.id, actor="b_refund")

    deal.status = DealStatus.DISPUTED
    await async_db.commit()

    mock_gateway = MagicMock(spec=EVMAdapter)
    mock_gateway.validate_address.return_value = True
    mock_gateway.to_checksum = lambda a: a
    mock_gateway.get_balance = AsyncMock(return_value=Decimal("0.2"))
    mock_gateway.broadcast_signed_transaction = AsyncMock(return_value="0xrefund_hash_abc")

    mock_w3 = MagicMock()
    mock_w3.eth.get_transaction_count.return_value = 0
    mock_w3.eth.gas_price = 1000000000
    mock_w3.eth.get_balance.return_value = int(Decimal("0.2") * Decimal(10**18))
    mock_gateway.w3 = mock_w3

    refund_service = RefundService(gateway=mock_gateway)

    # Unauthorized requester should fail (HTTP 403)
    with pytest.raises(Exception) as exc_info:
        await refund_service.execute_refund(async_db, deal.id, requester_id="random_user")
    assert "Refunds can only be authorized" in str(exc_info.value)

    # Authorized admin request succeeds
    r_tx1 = await refund_service.execute_refund(async_db, deal.id, requester_id=settings.BACKEND_SECRET)
    assert r_tx1.tx_hash == "0xrefund_hash_abc"
    assert r_tx1.type == TransactionType.REFUND

    # Second refund attempt is idempotent
    r_tx2 = await refund_service.execute_refund(async_db, deal.id, requester_id=settings.BACKEND_SECRET)
    assert r_tx2.tx_hash == "0xrefund_hash_abc"
    assert mock_gateway.broadcast_signed_transaction.call_count == 1
