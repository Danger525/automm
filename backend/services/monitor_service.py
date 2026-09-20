import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from backend.domain.models import Deal, EscrowWallet, BlockchainTransaction
from backend.domain.enums import DealStatus, TransactionType, TransactionStatus
from backend.blockchain.evm_adapter import EVMAdapter
from backend.core.config import settings
from backend.core.state_machine import validate_transition
from backend.services.audit_service import AuditService
from backend.database.session import AsyncSessionLocal

logger = logging.getLogger("TransactionMonitor")


class TransactionMonitor:
    def __init__(self, gateway: Optional[EVMAdapter] = None):
        self.gateway = gateway or EVMAdapter()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._poll_loop())
            logger.info("Transaction Monitor background service started.")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Transaction Monitor stopped.")

    async def _poll_loop(self):
        while self._running:
            try:
                async with AsyncSessionLocal() as session:
                    await self.check_active_escrows(session)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in transaction monitor loop: {e}", exc_info=True)

            await asyncio.sleep(settings.POLL_INTERVAL_SECONDS)

    async def check_active_escrows(self, session: AsyncSession):
        """Scans deals waiting for payment or confirming payment."""
        stmt = (
            select(Deal)
            .where(Deal.status.in_([DealStatus.WAITING_FOR_PAYMENT, DealStatus.PAYMENT_DETECTED]))
            .options(
                selectinload(Deal.escrow_wallet),
                selectinload(Deal.transactions)
            )
        )
        result = await session.execute(stmt)
        deals = result.scalars().all()

        for deal in deals:
            try:
                await self.process_deal_escrow(session, deal)
            except Exception as e:
                logger.warning(f"Error processing deal {deal.id} in monitor: {e}")

    async def process_deal_escrow(self, session: AsyncSession, deal: Deal):
        if not deal.escrow_wallet:
            return

        escrow_addr = deal.escrow_wallet.address
        expected_amount = Decimal(deal.amount)

        # 1. First, check existing detected transactions for confirmation progression
        deposit_txs = [t for t in deal.transactions if t.type == TransactionType.DEPOSIT]
        for tx_rec in deposit_txs:
            confirmations = await self.gateway.get_confirmations(tx_rec.tx_hash)
            tx_rec.confirmations = confirmations

            if confirmations >= settings.CONFIRMATION_THRESHOLD:
                tx_rec.status = TransactionStatus.CONFIRMED
                if deal.status != DealStatus.FUNDED:
                    validate_transition(deal.status, DealStatus.FUNDED)
                    deal.status = DealStatus.FUNDED
                    deal.funded_at = datetime.now(timezone.utc)
                    await AuditService.log_action(
                        session=session,
                        action="DEAL_FUNDED",
                        actor="SYSTEM_MONITOR",
                        deal_id=deal.id,
                        metadata={
                            "tx_hash": tx_rec.tx_hash,
                            "confirmations": confirmations,
                            "amount": deal.amount
                        }
                    )
                    await session.commit()
                    logger.info(f"Deal {deal.id} marked FUNDED with {confirmations} confirmations.")
                    return
            elif confirmations > 0:
                tx_rec.status = TransactionStatus.CONFIRMING
                await session.commit()
            else:
                tx_rec.status = TransactionStatus.DETECTED
                await session.commit()

        # 2. On-chain balance check (if not yet detected via explicit tx)
        current_balance = await self.gateway.get_balance(
            escrow_addr,
            token_address=settings.SUPPORTED_TOKEN_ADDRESS
        )

        if current_balance >= expected_amount and not deposit_txs:
            # Funds arrived! Let's search recent block transactions for the tx_hash to escrow_addr
            discovered_hash = await self._discover_tx_hash_for_address(escrow_addr)
            tx_hash_str = discovered_hash or f"onchain-deposit-{escrow_addr[:10]}-{deal.id[:8]}"

            # Add transaction record
            confirmations = await self.gateway.get_confirmations(discovered_hash) if discovered_hash else 1
            tx_status = (
                TransactionStatus.CONFIRMED
                if confirmations >= settings.CONFIRMATION_THRESHOLD
                else TransactionStatus.CONFIRMING
            )

            new_tx = BlockchainTransaction(
                deal_id=deal.id,
                tx_hash=tx_hash_str,
                type=TransactionType.DEPOSIT,
                from_address="0x" + "0" * 40,
                to_address=escrow_addr,
                amount=str(current_balance),
                token=deal.token,
                network=deal.network,
                confirmations=confirmations,
                status=tx_status
            )
            session.add(new_tx)

            if confirmations >= settings.CONFIRMATION_THRESHOLD:
                validate_transition(deal.status, DealStatus.FUNDED)
                deal.status = DealStatus.FUNDED
                deal.funded_at = datetime.now(timezone.utc)
            else:
                validate_transition(deal.status, DealStatus.PAYMENT_DETECTED)
                deal.status = DealStatus.PAYMENT_DETECTED

            await AuditService.log_action(
                session=session,
                action="DEPOSIT_DETECTED",
                actor="SYSTEM_MONITOR",
                deal_id=deal.id,
                metadata={
                    "balance": str(current_balance),
                    "expected": str(expected_amount),
                    "tx_hash": tx_hash_str,
                    "confirmations": confirmations
                }
            )
            await session.commit()

    async def _discover_tx_hash_for_address(self, address: str) -> Optional[str]:
        """Scans the latest blocks to locate the transaction hash sent to target address."""
        try:
            latest_block = await self.gateway.get_latest_block_number()
            # Scan last 25 blocks
            start_block = max(0, latest_block - 25)
            w3 = self.gateway.w3
            checksum_addr = self.gateway.to_checksum(address)

            for b_num in range(latest_block, start_block, -1):
                block = await asyncio.to_thread(w3.eth.get_block, b_num, full_transactions=True)
                for tx in block.transactions:
                    if tx.get("to") and self.gateway.to_checksum(tx.get("to")) == checksum_addr:
                        return tx.hash.hex()
        except Exception as e:
            logger.debug(f"Could not discover tx hash automatically via block scan: {e}")
        return None

    async def process_user_submitted_deposit(self, session: AsyncSession, deal: Deal, tx_hash: str) -> BlockchainTransaction:
        """
        Directly verify a transaction hash submitted by buyer or webhook against the blockchain.
        Does NOT trust user input alone; performs strict on-chain validation.
        """
        if not deal.escrow_wallet:
            raise ValueError("Escrow wallet not generated for deal")

        # Verify against live blockchain
        is_valid, reason, confirmations = await self.gateway.verify_deposit(
            tx_hash=tx_hash,
            expected_to_address=deal.escrow_wallet.address,
            expected_amount=Decimal(deal.amount),
            token_address=settings.SUPPORTED_TOKEN_ADDRESS
        )

        if not is_valid:
            raise ValueError(f"Blockchain deposit verification failed: {reason}")

        # Check for duplicate transaction in database
        stmt = select(BlockchainTransaction).where(BlockchainTransaction.tx_hash == tx_hash)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        tx_data = await self.gateway.get_transaction(tx_hash)
        from_addr = tx_data.get("from", "unknown") if tx_data else "unknown"

        tx_status = (
            TransactionStatus.CONFIRMED
            if confirmations >= settings.CONFIRMATION_THRESHOLD
            else TransactionStatus.CONFIRMING if confirmations > 0 else TransactionStatus.DETECTED
        )

        btx = BlockchainTransaction(
            deal_id=deal.id,
            tx_hash=tx_hash,
            type=TransactionType.DEPOSIT,
            from_address=from_addr,
            to_address=deal.escrow_wallet.address,
            amount=deal.amount,
            token=deal.token,
            network=deal.network,
            confirmations=confirmations,
            status=tx_status,
            block_number=tx_data.get("blockNumber") if tx_data else None
        )
        session.add(btx)

        if confirmations >= settings.CONFIRMATION_THRESHOLD:
            if deal.status != DealStatus.FUNDED:
                validate_transition(deal.status, DealStatus.FUNDED)
                deal.status = DealStatus.FUNDED
                deal.funded_at = datetime.now(timezone.utc)
        else:
            if deal.status == DealStatus.WAITING_FOR_PAYMENT:
                validate_transition(deal.status, DealStatus.PAYMENT_DETECTED)
                deal.status = DealStatus.PAYMENT_DETECTED

        await AuditService.log_action(
            session=session,
            action="DEPOSIT_VERIFIED_ON_CHAIN",
            actor="SYSTEM_MONITOR",
            deal_id=deal.id,
            metadata={"tx_hash": tx_hash, "confirmations": confirmations, "status": tx_status.value}
        )
        await session.commit()
        await session.refresh(btx)
        return btx
