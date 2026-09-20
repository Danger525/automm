import asyncio
import logging
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from web3 import Web3
from web3.exceptions import TransactionNotFound, BlockNotFound
from backend.blockchain.base import BlockchainGateway
from backend.core.config import settings

logger = logging.getLogger("EVMAdapter")

ERC20_MINIMAL_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "success", "type": "bool"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]


class EVMAdapter(BlockchainGateway):
    """Concrete EVM blockchain gateway for testnets (Sepolia, Amoy, etc.)."""

    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url or settings.BLOCKCHAIN_RPC_URL
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

    def validate_address(self, address: str) -> bool:
        if not address or not isinstance(address, str):
            return False
        return self.w3.is_address(address)

    def to_checksum(self, address: str) -> str:
        return self.w3.to_checksum_address(address)

    async def is_connected(self) -> bool:
        return await asyncio.to_thread(self.w3.is_connected)

    async def get_latest_block_number(self) -> int:
        return await asyncio.to_thread(lambda: self.w3.eth.block_number)

    async def get_balance(self, address: str, token_address: Optional[str] = None) -> Decimal:
        checksum = self.to_checksum(address)
        if not token_address:
            # Native currency (e.g. Sepolia ETH, Amoy POL)
            wei_balance = await asyncio.to_thread(self.w3.eth.get_balance, checksum)
            return Decimal(wei_balance) / Decimal(10**18)
        else:
            token_checksum = self.to_checksum(token_address)
            contract = self.w3.eth.contract(address=token_checksum, abi=ERC20_MINIMAL_ABI)
            raw_balance = await asyncio.to_thread(contract.functions.balanceOf(checksum).call)
            decimals = await asyncio.to_thread(contract.functions.decimals().call)
            return Decimal(raw_balance) / Decimal(10**decimals)

    async def get_transaction(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        try:
            tx = await asyncio.to_thread(self.w3.eth.get_transaction, tx_hash)
            receipt = None
            try:
                receipt = await asyncio.to_thread(self.w3.eth.get_transaction_receipt, tx_hash)
            except TransactionNotFound:
                pass

            tx_dict = dict(tx)
            if receipt:
                tx_dict["receipt"] = dict(receipt)
                tx_dict["status"] = receipt.status
                tx_dict["blockNumber"] = receipt.blockNumber
            return tx_dict
        except TransactionNotFound:
            return None
        except Exception as e:
            logger.warning(f"Error fetching transaction {tx_hash}: {e}")
            return None

    async def get_confirmations(self, tx_hash: str) -> int:
        try:
            receipt = await asyncio.to_thread(self.w3.eth.get_transaction_receipt, tx_hash)
            if not receipt or receipt.blockNumber is None:
                return 0
            latest_block = await self.get_latest_block_number()
            if latest_block < receipt.blockNumber:
                return 0
            return latest_block - receipt.blockNumber + 1
        except TransactionNotFound:
            return 0
        except Exception as e:
            logger.warning(f"Error calculating confirmations for {tx_hash}: {e}")
            return 0

    async def estimate_fee(self, from_address: str, to_address: str, amount: Decimal) -> Dict[str, Any]:
        """Estimate gas limit and gas price for an EVM transaction."""
        from_checksum = self.to_checksum(from_address)
        to_checksum = self.to_checksum(to_address)
        value_wei = int(amount * Decimal(10**18))

        gas_price = await asyncio.to_thread(lambda: self.w3.eth.gas_price)
        tx_data = {
            "from": from_checksum,
            "to": to_checksum,
            "value": value_wei,
        }
        try:
            gas_limit = await asyncio.to_thread(self.w3.eth.estimate_gas, tx_data)
        except Exception:
            gas_limit = 21000  # standard transfer default

        return {
            "gas_limit": gas_limit,
            "gas_price": gas_price,
            "estimated_fee_eth": Decimal(gas_limit * gas_price) / Decimal(10**18)
        }

    async def broadcast_signed_transaction(self, raw_tx_hex: str) -> str:
        """Broadcast raw signed transaction bytes/hex to testnet."""
        raw_bytes = bytes.fromhex(raw_tx_hex.removeprefix("0x"))
        tx_hash_bytes = await asyncio.to_thread(self.w3.eth.send_raw_transaction, raw_bytes)
        return self.w3.to_hex(tx_hash_bytes)

    async def verify_deposit(
        self,
        tx_hash: str,
        expected_to_address: str,
        expected_amount: Decimal,
        token_address: Optional[str] = None
    ) -> Tuple[bool, str, int]:
        """
        Verify on-chain deposit against expected recipient address, amount, and receipt status.
        """
        tx = await self.get_transaction(tx_hash)
        if not tx:
            return False, "Transaction not found on blockchain", 0

        receipt = tx.get("receipt")
        if not receipt:
            return False, "Transaction is pending in mempool (not yet mined)", 0

        if receipt.get("status") != 1:
            return False, "Transaction failed / reverted on-chain", 0

        confirmations = await self.get_confirmations(tx_hash)
        expected_checksum = self.to_checksum(expected_to_address)

        if not token_address:
            # Native transfer check
            to_addr = tx.get("to")
            if not to_addr or self.to_checksum(to_addr) != expected_checksum:
                return False, f"Destination address mismatch. Expected {expected_checksum}, got {to_addr}", confirmations

            transferred_wei = tx.get("value", 0)
            transferred_eth = Decimal(transferred_wei) / Decimal(10**18)

            if transferred_eth < expected_amount:
                return False, f"Underpayment: transferred {transferred_eth} < expected {expected_amount}", confirmations

            return True, "Valid deposit", confirmations
        else:
            # Token transfer verification
            # Verify recipient contract is the token address
            token_checksum = self.to_checksum(token_address)
            if self.to_checksum(tx.get("to")) != token_checksum:
                return False, "Transaction was not directed to the supported token contract", confirmations

            # Decode ERC20 Transfer logs
            contract = self.w3.eth.contract(address=token_checksum, abi=ERC20_MINIMAL_ABI)
            events = contract.events.Transfer().process_receipt(receipt)
            found_valid_transfer = False
            for evt in events:
                if self.to_checksum(evt.args._to) == expected_checksum:
                    decimals = await asyncio.to_thread(contract.functions.decimals().call)
                    amount_token = Decimal(evt.args._value) / Decimal(10**decimals)
                    if amount_token >= expected_amount:
                        found_valid_transfer = True
                        break

            if not found_valid_transfer:
                return False, "Matching ERC-20 transfer event not found in transaction logs", confirmations

            return True, "Valid token deposit", confirmations
