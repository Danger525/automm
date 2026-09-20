import logging
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from backend.database.session import get_db
from backend.domain.models import Deal, BlockchainTransaction
from backend.domain.enums import DealStatus, TransactionType
from backend.domain.schemas import (
    DealCreate,
    DealAgree,
    DealResponse,
    DepositInfoResponse,
    BlockchainTransactionResponse,
    ReleaseRequest,
    RefundRequest,
    DisputeRequest,
    DealStatusResponse
)
from backend.services.deal_service import DealService
from backend.services.escrow_service import EscrowService
from backend.services.payout_service import PayoutService
from backend.services.refund_service import RefundService
from backend.services.audit_service import AuditService
from backend.blockchain.evm_adapter import EVMAdapter
from backend.api.deps import get_blockchain_gateway, get_payout_service, get_refund_service
from backend.core.config import settings

logger = logging.getLogger("DealsEndpoint")

router = APIRouter()


def format_deal_response(deal: Deal) -> DealResponse:
    escrow_addr = deal.escrow_wallet.address if deal.escrow_wallet else None
    return DealResponse(
        id=deal.id,
        buyer_id=deal.buyer_id,
        seller_id=deal.seller_id,
        amount=deal.amount,
        token=deal.token,
        network=deal.network,
        status=deal.status,
        buyer_agreed=deal.buyer_agreed,
        seller_agreed=deal.seller_agreed,
        terms_locked=deal.terms_locked,
        seller_payout_address=deal.seller_payout_address,
        buyer_refund_address=deal.buyer_refund_address,
        escrow_address=escrow_addr,
        created_at=deal.created_at,
        updated_at=deal.updated_at,
        funded_at=deal.funded_at,
        completed_at=deal.completed_at
    )


@router.post("", response_model=DealResponse, status_code=status.HTTP_201_CREATED)
async def create_deal(
    deal_in: DealCreate,
    db: AsyncSession = Depends(get_db),
    x_actor_id: Optional[str] = Header(None)
):
    """Create a new escrow deal."""
    actor = x_actor_id or deal_in.buyer_id
    deal = await DealService.create_deal(db, deal_in, actor=actor)
    return format_deal_response(deal)


@router.get("/{deal_id}", response_model=DealResponse)
async def get_deal(
    deal_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Retrieve deal details by ID."""
    deal = await DealService.get_deal(db, deal_id)
    return format_deal_response(deal)


@router.post("/{deal_id}/agree", response_model=DealResponse)
async def agree_deal(
    deal_id: str,
    agree_in: DealAgree,
    db: AsyncSession = Depends(get_db)
):
    """
    Record agreement from buyer or seller.
    When both parties agree, terms are locked.
    """
    deal = await DealService.agree_deal(db, deal_id, agree_in)
    return format_deal_response(deal)


@router.post("/{deal_id}/escrow", response_model=DepositInfoResponse)
async def generate_escrow(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    x_actor_id: Optional[str] = Header(None)
):
    """
    Generate an on-demand escrow deposit wallet after both parties have locked terms.
    """
    deal = await DealService.get_deal(db, deal_id)
    actor = x_actor_id or deal.buyer_id
    wallet = await EscrowService.create_escrow_wallet_for_deal(db, deal_id, actor=actor)

    return DepositInfoResponse(
        deal_id=deal.id,
        status=deal.status,
        amount=deal.amount,
        token=deal.token,
        network=deal.network,
        deposit_address=wallet.address
    )


@router.get("/{deal_id}/deposit", response_model=DepositInfoResponse)
async def get_deposit_info(
    deal_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get active deposit instructions and escrow wallet address for the deal."""
    deal = await DealService.get_deal(db, deal_id)
    escrow_addr = deal.escrow_wallet.address if deal.escrow_wallet else None

    return DepositInfoResponse(
        deal_id=deal.id,
        status=deal.status,
        amount=deal.amount,
        token=deal.token,
        network=deal.network,
        deposit_address=escrow_addr
    )


@router.get("/{deal_id}/transactions", response_model=List[BlockchainTransactionResponse])
async def get_deal_transactions(
    deal_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Retrieve all blockchain transactions associated with the deal (deposits, payouts, refunds)."""
    deal = await DealService.get_deal(db, deal_id)
    stmt = select(BlockchainTransaction).where(BlockchainTransaction.deal_id == deal.id)
    results = await db.execute(stmt)
    return results.scalars().all()


@router.post("/{deal_id}/release", response_model=BlockchainTransactionResponse)
async def release_funds(
    deal_id: str,
    release_in: ReleaseRequest,
    db: AsyncSession = Depends(get_db),
    payout_service: PayoutService = Depends(get_payout_service)
):
    """
    Release escrowed crypto to the seller.
    Strictly verifies deal is FUNDED/DELIVERED, signer authorization, and idempotency.
    """
    tx = await payout_service.execute_payout(
        session=db,
        deal_id=deal_id,
        requester_id=release_in.requester_id,
        destination_address=release_in.destination_address
    )
    return tx


@router.post("/{deal_id}/refund", response_model=BlockchainTransactionResponse)
async def refund_funds(
    deal_id: str,
    refund_in: RefundRequest,
    db: AsyncSession = Depends(get_db),
    refund_service: RefundService = Depends(get_refund_service)
):
    """
    Refund escrowed crypto back to the buyer following a dispute or cancellation.
    Requires administrative / dispute mediator authorization.
    """
    tx = await refund_service.execute_refund(
        session=db,
        deal_id=deal_id,
        requester_id=refund_in.requester_id,
        destination_address=refund_in.destination_address,
        reason=refund_in.reason
    )
    return tx


@router.post("/{deal_id}/dispute", response_model=DealResponse)
async def dispute_deal(
    deal_id: str,
    dispute_in: DisputeRequest,
    db: AsyncSession = Depends(get_db)
):
    """Raise a dispute on a funded deal."""
    deal = await DealService.get_deal(db, deal_id)

    if dispute_in.requester_id not in (deal.buyer_id, deal.seller_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only buyer or seller can dispute this deal."
        )

    updated_deal = await DealService.update_status(
        session=db,
        deal=deal,
        target_status=DealStatus.DISPUTED,
        actor=dispute_in.requester_id,
        meta={"reason": dispute_in.reason}
    )
    return format_deal_response(updated_deal)


@router.get("/{deal_id}/status", response_model=DealStatusResponse)
async def get_deal_status(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
    gateway: EVMAdapter = Depends(get_blockchain_gateway)
):
    """Get live on-chain status, escrow balance, and confirmation counts."""
    deal = await DealService.get_deal(db, deal_id)
    escrow_balance = "0"
    latest_confirmations = 0

    if deal.escrow_wallet:
        try:
            bal = await gateway.get_balance(
                deal.escrow_wallet.address,
                token_address=settings.SUPPORTED_TOKEN_ADDRESS
            )
            escrow_balance = str(bal)
        except Exception as e:
            logger.warning(f"Error checking balance for status: {e}")

    stmt = select(BlockchainTransaction).where(
        BlockchainTransaction.deal_id == deal.id,
        BlockchainTransaction.type == TransactionType.DEPOSIT
    )
    deposit_txs = (await db.execute(stmt)).scalars().all()
    if deposit_txs:
        latest_confirmations = max(t.confirmations for t in deposit_txs)

    return DealStatusResponse(
        deal_id=deal.id,
        status=deal.status,
        terms_locked=deal.terms_locked,
        funded=deal.status in (DealStatus.FUNDED, DealStatus.DELIVERING, DealStatus.DELIVERED, DealStatus.RELEASED, DealStatus.COMPLETED),
        confirmations=latest_confirmations,
        required_confirmations=settings.CONFIRMATION_THRESHOLD,
        escrow_balance=escrow_balance,
        transactions_count=len(deal.transactions)
    )
