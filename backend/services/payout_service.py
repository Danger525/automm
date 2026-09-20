import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from fastapi import HTTPException, status
from backend.domain.models import Deal, BlockchainTransaction
from backend.domain.enums import DealStatus, TransactionType, TransactionStatus
from backend.blockchain.evm_adapter import EVMAdapter, ERC20_MINIMAL_ABI
from backend.services.wallet_service import WalletService
from backend.services.deal_service import DealService
from backend.services.audit_service import AuditService
from backend.core.state_machine import validate_transition
from backend.core.config import settings

logger = logging.getLogger("PayoutService")


class PayoutService:
    def __init__(self, gateway: Optional[EVMAdapter] = None):
        self.gateway = gateway or EVMAdapter()

    async def execute_payout(
        self,
        session: AsyncSession,
        deal_id: str,
        requester_id: str,
        destination_address: Optional[str] = None
    ) -> BlockchainTransaction:
        deal = await DealService.get_deal(session, deal_id)

        # 1. Idempotency check: Return existing payout transaction if already executed
        stmt = select(BlockchainTransaction).where(
            BlockchainTransaction.deal_id == deal.id,
            BlockchainTransaction.type == TransactionType.PAYOUT
        )
        existing_payout = (await session.execute(stmt)).scalar_one_or_none()
        if existing_payout:
            logger.info(f"Payout already initiated for deal {deal.id}: {existing_payout.tx_hash}")
            return existing_payout

        # 2. Authorization check: Requester must be the buyer or admin staff
        is_admin = requester_id == settings.BACKEND_SECRET
        if requester_id != deal.buyer_id and not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the buyer or an authorized admin can release escrow funds."
            )

        # 3. Check deal state
        allowed_statuses = {DealStatus.FUNDED, DealStatus.DELIVERING, DealStatus.DELIVERED, DealStatus.DISPUTED}
        if deal.status not in allowed_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot release funds: Deal status is '{deal.status.value}'. Must be FUNDED or DELIVERED."
            )

        if not deal.escrow_wallet:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Escrow wallet does not exist for this deal."
            )

        # 4. Destination address validation
        payout_addr = destination_address or deal.seller_payout_address
        if not payout_addr or not self.gateway.validate_address(payout_addr):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid or missing seller destination address: '{payout_addr}'"
            )

        # 5. Transition to RELEASE_PENDING
        validate_transition(deal.status, DealStatus.RELEASE_PENDING)
        deal.status = DealStatus.RELEASE_PENDING
        await session.commit()

        # 6. Check on-chain balance of escrow wallet
        escrow_addr = deal.escrow_wallet.address
        token_addr = settings.SUPPORTED_TOKEN_ADDRESS
        balance = await self.gateway.get_balance(escrow_addr, token_address=token_addr)
        expected_amount = Decimal(deal.amount)

        if balance <= 0:
            # Revert state back to FUNDED so user can retry or fund gas
            deal.status = DealStatus.FUNDED
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Escrow wallet balance is 0. Cannot process payout."
            )

        w3 = self.gateway.w3
        from_checksum = self.gateway.to_checksum(escrow_addr)
        to_checksum = self.gateway.to_checksum(payout_addr)
        nonce = await asyncio.to_thread(w3.eth.get_transaction_count, from_checksum)
        gas_price = await asyncio.to_thread(lambda: w3.eth.gas_price)
        chain_id = settings.CHAIN_ID

        # 7. Construct transaction
        if not token_addr:
            # Native currency transfer
            gas_limit = 21000
            total_gas_cost = gas_limit * gas_price
            balance_wei = await asyncio.to_thread(w3.eth.get_balance, from_checksum)

            # Send full available balance minus gas (or target deal amount if balance covers gas separately)
            amount_to_send_wei = int(expected_amount * Decimal(10**18))
            if balance_wei < amount_to_send_wei + total_gas_cost:
                # Deduct gas from payout amount if exact balance was deposited
                amount_to_send_wei = balance_wei - total_gas_cost

            if amount_to_send_wei <= 0:
                deal.status = DealStatus.FUNDED
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Insufficient escrow balance to cover transfer after network gas fees."
                )

            tx_dict = {
                "nonce": nonce,
                "to": to_checksum,
                "value": amount_to_send_wei,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "chainId": chain_id
            }
            actual_amount_str = str(Decimal(amount_to_send_wei) / Decimal(10**18))
        else:
            # ERC-20 transfer
            token_checksum = self.gateway.to_checksum(token_addr)
            contract = w3.eth.contract(address=token_checksum, abi=ERC20_MINIMAL_ABI)
            decimals = await asyncio.to_thread(contract.functions.decimals().call)
            token_units = int(expected_amount * Decimal(10**decimals))

            tx_data = contract.functions.transfer(to_checksum, token_units).build_transaction({
                "from": from_checksum,
                "nonce": nonce,
                "gasPrice": gas_price,
                "chainId": chain_id
            })
            tx_dict = tx_data
            actual_amount_str = deal.amount

        # 8. Sign transaction using encrypted private key
        try:
            raw_tx_hex = WalletService.sign_transaction(
                deal.escrow_wallet.encrypted_private_key,
                tx_dict
            )
        except Exception as e:
            deal.status = DealStatus.FUNDED
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to sign payout transaction: {e}"
            )

        # 9. Broadcast transaction to blockchain
        try:
            tx_hash = await self.gateway.broadcast_signed_transaction(raw_tx_hex)
        except Exception as e:
            deal.status = DealStatus.FUNDED
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to broadcast payout transaction to RPC: {e}"
            )

        # 10. Record BlockchainTransaction
        payout_tx = BlockchainTransaction(
            deal_id=deal.id,
            tx_hash=tx_hash,
            type=TransactionType.PAYOUT,
            from_address=escrow_addr,
            to_address=payout_addr,
            amount=actual_amount_str,
            token=deal.token,
            network=deal.network,
            confirmations=0,
            status=TransactionStatus.CONFIRMING
        )
        session.add(payout_tx)

        # 11. Advance deal status: RELEASED -> COMPLETED
        validate_transition(deal.status, DealStatus.RELEASED)
        deal.status = DealStatus.RELEASED
        validate_transition(deal.status, DealStatus.COMPLETED)
        deal.status = DealStatus.COMPLETED
        deal.completed_at = datetime.now(timezone.utc)

        await AuditService.log_action(
            session=session,
            action="PAYOUT_BROADCAST",
            actor=requester_id,
            deal_id=deal.id,
            metadata={
                "tx_hash": tx_hash,
                "to_address": payout_addr,
                "amount": actual_amount_str,
                "token": deal.token
            }
        )
        await session.commit()
        await session.refresh(payout_tx)
        return payout_tx
