from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from backend.domain.enums import DealStatus, TransactionType, TransactionStatus


class DealCreate(BaseModel):
    buyer_id: str = Field(..., description="Unique ID of the buyer (e.g., Discord ID)")
    seller_id: Optional[str] = Field(None, description="Unique ID of the seller")
    amount: str = Field(..., description="Exact amount required for the escrow deal")
    token: str = Field(default="ETH", description="Asset symbol (e.g. ETH)")
    network: str = Field(default="SEPOLIA", description="Blockchain network (e.g. SEPOLIA)")
    seller_payout_address: Optional[str] = Field(None, description="Seller wallet address for payout")
    buyer_refund_address: Optional[str] = Field(None, description="Buyer wallet address for refund")


class DealAgree(BaseModel):
    user_id: str = Field(..., description="User ID of the agreeing party (buyer or seller)")
    seller_payout_address: Optional[str] = Field(None, description="Seller payout address if provided during agreement")
    buyer_refund_address: Optional[str] = Field(None, description="Buyer refund address if provided during agreement")


class EscrowWalletResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    address: str
    network: str
    created_at: datetime


class BlockchainTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    tx_hash: str
    type: TransactionType
    from_address: str
    to_address: str
    amount: str
    token: str
    network: str
    confirmations: int
    status: TransactionStatus
    block_number: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class DealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    buyer_id: str
    seller_id: Optional[str]
    amount: str
    token: str
    network: str
    status: DealStatus
    buyer_agreed: bool
    seller_agreed: bool
    terms_locked: bool
    seller_payout_address: Optional[str]
    buyer_refund_address: Optional[str]
    escrow_address: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    funded_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DepositInfoResponse(BaseModel):
    deal_id: str
    status: DealStatus
    amount: str
    token: str
    network: str
    deposit_address: Optional[str]


class ReleaseRequest(BaseModel):
    requester_id: str = Field(..., description="User ID requesting release (must be buyer or authorized staff)")
    destination_address: Optional[str] = Field(None, description="Destination seller address (if not already set in deal)")


class RefundRequest(BaseModel):
    requester_id: str = Field(..., description="User ID requesting refund (must be authorized staff/admin)")
    destination_address: Optional[str] = Field(None, description="Destination buyer address (if not already set in deal)")
    reason: Optional[str] = Field(None, description="Reason for refunding the deal")


class DisputeRequest(BaseModel):
    requester_id: str = Field(..., description="User ID initiating dispute (buyer or seller)")
    reason: str = Field(..., description="Explanation of the dispute")


class DealStatusResponse(BaseModel):
    deal_id: str
    status: DealStatus
    terms_locked: bool
    funded: bool
    confirmations: int
    required_confirmations: int
    escrow_balance: str
    transactions_count: int
