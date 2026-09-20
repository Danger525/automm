from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database.session import get_db
from backend.blockchain.evm_adapter import EVMAdapter
from backend.services.payout_service import PayoutService
from backend.services.refund_service import RefundService

# Singleton adapter
_evm_gateway = EVMAdapter()
_payout_service = PayoutService(gateway=_evm_gateway)
_refund_service = RefundService(gateway=_evm_gateway)


def get_blockchain_gateway() -> EVMAdapter:
    return _evm_gateway


def get_payout_service() -> PayoutService:
    return _payout_service


def get_refund_service() -> RefundService:
    return _refund_service
