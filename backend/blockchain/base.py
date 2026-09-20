from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Tuple
from decimal import Decimal


class BlockchainGateway(ABC):
    """Generic interface for blockchain adapters."""

    @abstractmethod
    def validate_address(self, address: str) -> bool:
        """Validate if the string is a syntactically and checksum-valid blockchain address."""
        pass

    @abstractmethod
    async def get_balance(self, address: str, token_address: Optional[str] = None) -> Decimal:
        """Get the spendable balance of the address in human units (e.g. ETH)."""
        pass

    @abstractmethod
    async def get_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        """Fetch raw transaction and receipt details by hash."""
        pass

    @abstractmethod
    async def get_confirmations(self, tx_hash: str) -> int:
        """Calculate number of block confirmations for a transaction."""
        pass

    @abstractmethod
    async def estimate_fee(self, from_address: str, to_address: str, amount: Decimal) -> Dict[str, Any]:
        """Estimate required network gas/fee for the transfer."""
        pass

    @abstractmethod
    async def broadcast_signed_transaction(self, raw_tx_hex: str) -> str:
        """Broadcast signed raw transaction to the network and return tx hash."""
        pass

    @abstractmethod
    async def verify_deposit(
        self,
        tx_hash: str,
        expected_to_address: str,
        expected_amount: Decimal,
        token_address: Optional[str] = None
    ) -> Tuple[bool, str, int]:
        """
        Verify that a given transaction hash is an actual valid transfer to the expected address.
        Returns: (is_valid, reason, current_confirmations)
        """
        pass
