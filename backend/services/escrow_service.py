import logging
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from backend.domain.models import Deal, EscrowWallet
from backend.domain.enums import DealStatus
from backend.core.state_machine import validate_transition
from backend.services.wallet_service import WalletService
from backend.services.deal_service import DealService
from backend.services.audit_service import AuditService

logger = logging.getLogger("EscrowService")


class EscrowService:
    @staticmethod
    async def create_escrow_wallet_for_deal(session: AsyncSession, deal_id: str, actor: str) -> EscrowWallet:
        deal = await DealService.get_deal(session, deal_id)

        # 1. Enforce terms agreement & lock check
        if not (deal.buyer_agreed and deal.seller_agreed and deal.terms_locked):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot generate escrow wallet: Both buyer and seller must agree to lock terms first."
            )

        # 2. Check if escrow wallet already generated (Idempotency)
        if deal.escrow_wallet:
            return deal.escrow_wallet

        # 3. State transition: AGREED -> ESCROW_CREATING
        validate_transition(deal.status, DealStatus.ESCROW_CREATING)
        deal.status = DealStatus.ESCROW_CREATING
        await session.flush()

        # 4. Generate wallet & encrypt key at rest
        address, encrypted_key = WalletService.generate_wallet()

        escrow_wallet = EscrowWallet(
            deal_id=deal.id,
            address=address,
            network=deal.network,
            encrypted_private_key=encrypted_key
        )
        session.add(escrow_wallet)
        await session.flush()

        # 5. State transition: ESCROW_CREATING -> WAITING_FOR_PAYMENT
        validate_transition(deal.status, DealStatus.WAITING_FOR_PAYMENT)
        deal.status = DealStatus.WAITING_FOR_PAYMENT

        await AuditService.log_action(
            session=session,
            action="ESCROW_WALLET_CREATED",
            actor=actor,
            deal_id=deal.id,
            metadata={
                "escrow_address": address,
                "network": deal.network,
                "status": deal.status.value
            }
        )

        await session.commit()
        await session.refresh(deal)
        return escrow_wallet
