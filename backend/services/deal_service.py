import logging
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
from backend.domain.models import Deal, EscrowWallet, BlockchainTransaction
from backend.domain.enums import DealStatus
from backend.domain.schemas import DealCreate, DealAgree
from backend.core.state_machine import validate_transition
from backend.services.audit_service import AuditService

logger = logging.getLogger("DealService")


class DealService:
    @staticmethod
    async def create_deal(session: AsyncSession, deal_data: DealCreate, actor: str) -> Deal:
        deal = Deal(
            buyer_id=deal_data.buyer_id,
            seller_id=deal_data.seller_id,
            amount=deal_data.amount.strip(),
            token=deal_data.token.strip().upper(),
            network=deal_data.network.strip().upper(),
            status=DealStatus.WAITING_FOR_AGREEMENT,
            buyer_agreed=False,
            seller_agreed=False,
            terms_locked=False,
            seller_payout_address=deal_data.seller_payout_address,
            buyer_refund_address=deal_data.buyer_refund_address
        )
        session.add(deal)
        await session.flush()

        await AuditService.log_action(
            session=session,
            action="DEAL_CREATED",
            actor=actor,
            deal_id=deal.id,
            metadata={
                "buyer_id": deal.buyer_id,
                "seller_id": deal.seller_id,
                "amount": deal.amount,
                "token": deal.token,
                "network": deal.network
            }
        )
        await session.commit()
        await session.refresh(deal)
        return deal

    @staticmethod
    async def get_deal(session: AsyncSession, deal_id: str) -> Deal:
        stmt = (
            select(Deal)
            .where(Deal.id == deal_id)
            .options(
                selectinload(Deal.escrow_wallet),
                selectinload(Deal.transactions)
            )
        )
        result = await session.execute(stmt)
        deal = result.scalar_one_or_none()
        if not deal:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Deal '{deal_id}' not found"
            )
        return deal

    @staticmethod
    async def agree_deal(session: AsyncSession, deal_id: str, agree_data: DealAgree) -> Deal:
        deal = await DealService.get_deal(session, deal_id)

        if deal.terms_locked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Deal terms are already locked. Terms cannot be renegotiated without creating a new deal."
            )

        actor_id = agree_data.user_id

        if actor_id == deal.buyer_id:
            deal.buyer_agreed = True
            if agree_data.buyer_refund_address:
                deal.buyer_refund_address = agree_data.buyer_refund_address
        elif actor_id == deal.seller_id:
            deal.seller_agreed = True
            if agree_data.seller_payout_address:
                deal.seller_payout_address = agree_data.seller_payout_address
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the registered buyer or seller can agree to deal terms."
            )

        # Check if both parties agreed
        if deal.buyer_agreed and deal.seller_agreed:
            validate_transition(deal.status, DealStatus.AGREED)
            deal.status = DealStatus.AGREED
            deal.terms_locked = True

            await AuditService.log_action(
                session=session,
                action="TERMS_LOCKED",
                actor=actor_id,
                deal_id=deal.id,
                metadata={"status": deal.status.value, "amount": deal.amount, "token": deal.token}
            )
        else:
            await AuditService.log_action(
                session=session,
                action="PARTY_AGREED",
                actor=actor_id,
                deal_id=deal.id,
                metadata={"buyer_agreed": deal.buyer_agreed, "seller_agreed": deal.seller_agreed}
            )

        await session.commit()
        await session.refresh(deal)
        return deal

    @staticmethod
    async def update_status(session: AsyncSession, deal: Deal, target_status: DealStatus, actor: str, meta: Optional[dict] = None) -> Deal:
        validate_transition(deal.status, target_status)
        old_status = deal.status
        deal.status = target_status

        await AuditService.log_action(
            session=session,
            action="STATUS_UPDATED",
            actor=actor,
            deal_id=deal.id,
            metadata={"from": old_status.value, "to": target_status.value, **(meta or {})}
        )
        await session.commit()
        await session.refresh(deal)
        return deal
