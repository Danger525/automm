import logging
from typing import Dict, Any, Tuple
from eth_account import Account
from web3 import Web3
from backend.core.security import encrypt_private_key, decrypt_private_key
from backend.core.config import settings

logger = logging.getLogger("WalletService")

# Enable mnemonic / HD features in eth_account if needed
Account.enable_unaudited_hdwallet_features()


class WalletService:
    """
    Manages generation, storage, encryption, and transaction signing for escrow wallets.
    Designed with a provider boundary so that KMS / Vault / HSM can be plugged in later.
    """

    @staticmethod
    def generate_wallet() -> Tuple[str, str]:
        """
        Generates a new secp256k1 keypair for EVM networks.
        Returns:
            (public_checksum_address, encrypted_private_key_ciphertext)
        """
        acct = Account.create()
        address = Web3.to_checksum_address(acct.address)
        raw_key = acct.key.hex()
        raw_key_hex = raw_key if raw_key.startswith("0x") else f"0x{raw_key}"

        # Immediately encrypt private key at rest
        encrypted_key = encrypt_private_key(raw_key_hex)

        # Ensure raw key is scrubbed from local scope
        del raw_key_hex
        del acct

        logger.info(f"Generated new escrow wallet address: {address}")
        return address, encrypted_key

    @staticmethod
    def sign_transaction(encrypted_private_key: str, transaction_dict: Dict[str, Any]) -> str:
        """
        Decrypts key strictly in-memory, signs the raw transaction, and returns the raw hex string.
        The decrypted key is never logged or exposed.
        """
        raw_key_hex = decrypt_private_key(encrypted_private_key)
        try:
            signed_tx = Account.sign_transaction(transaction_dict, raw_key_hex)
            raw_tx_hex = signed_tx.raw_transaction.hex()
            return raw_tx_hex
        finally:
            # Memory hygiene
            del raw_key_hex

    @staticmethod
    def validate_address(address: str) -> bool:
        """Validate EVM address formatting and checksum."""
        if not address or not isinstance(address, str):
            return False
        return Web3.is_address(address)
