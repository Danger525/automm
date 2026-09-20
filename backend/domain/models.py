import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    Integer,
    ForeignKey,
    Text,
    Enum as SQLEnum
)
from sqlalchemy.orm import relationship
from backend.database.base import Base
from backend.domain.enums import DealStatus, TransactionType, TransactionStatus


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Deal(Base):
    __tablename__ = "deals"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    buyer_id = Column(String(100), nullable=False, index=True)
    seller_id = Column(String(100), nullable=True, index=True)
    amount = Column(String(50), nullable=False)
    token = Column(String(20), nullable=False)
    network = Column(String(50), nullable=False)
    status = Column(SQLEnum(DealStatus), default=DealStatus.CREATED, nullable=False, index=True)

    buyer_agreed = Column(Boolean, default=False, nullable=False)
    seller_agreed = Column(Boolean, default=False, nullable=False)
    terms_locked = Column(Boolean, default=False, nullable=False)

    seller_payout_address = Column(String(100), nullable=True)
    buyer_refund_address = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    funded_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    escrow_wallet = relationship("EscrowWallet", uselist=False, back_populates="deal", cascade="all, delete-orphan")
    transactions = relationship("BlockchainTransaction", back_populates="deal", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="deal", cascade="all, delete-orphan")


class EscrowWallet(Base):
    __tablename__ = "escrow_wallets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    deal_id = Column(String(36), ForeignKey("deals.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    address = Column(String(100), nullable=False, index=True)
    network = Column(String(50), nullable=False)
    encrypted_private_key = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    deal = relationship("Deal", back_populates="escrow_wallet")


class BlockchainTransaction(Base):
    __tablename__ = "blockchain_transactions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    deal_id = Column(String(36), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True)
    tx_hash = Column(String(100), unique=True, nullable=False, index=True)
    type = Column(SQLEnum(TransactionType), nullable=False, index=True)
    from_address = Column(String(100), nullable=False)
    to_address = Column(String(100), nullable=False)
    amount = Column(String(50), nullable=False)
    token = Column(String(20), nullable=False)
    network = Column(String(50), nullable=False)
    confirmations = Column(Integer, default=0, nullable=False)
    status = Column(SQLEnum(TransactionStatus), default=TransactionStatus.DETECTED, nullable=False)
    block_number = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    deal = relationship("Deal", back_populates="transactions")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    deal_id = Column(String(36), ForeignKey("deals.id", ondelete="CASCADE"), nullable=True, index=True)
    action = Column(String(100), nullable=False)
    actor = Column(String(100), nullable=False)
    metadata_json = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    deal = relationship("Deal", back_populates="audit_logs")
