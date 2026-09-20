import asyncio
import os
import sys
from decimal import Decimal

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from web3 import Web3
from backend.blockchain.evm_adapter import EVMAdapter
from backend.services.wallet_service import WalletService
from backend.core.config import settings


async def main():
    print("=" * 60)
    print("AutoMM Escrow Backend — Live Testnet Integration Check")
    print("=" * 60)
    print(f"Network: {settings.BLOCKCHAIN_NETWORK}")
    print(f"Chain ID: {settings.CHAIN_ID}")
    print(f"RPC URL: {settings.BLOCKCHAIN_RPC_URL}")
    print("-" * 60)

    adapter = EVMAdapter()
    connected = await adapter.is_connected()
    print(f"1. RPC Connection Status: {'[SUCCESS] CONNECTED' if connected else '[FAIL] UNABLE TO CONNECT'}")
    if not connected:
        print("Please check your BLOCKCHAIN_RPC_URL in .env")
        sys.exit(1)

    latest_block = await adapter.get_latest_block_number()
    print(f"2. Current Testnet Block: #{latest_block}")

    # Test wallet generation & validation
    escrow_addr, enc_key = WalletService.generate_wallet()
    print(f"3. Generated Escrow Address: {escrow_addr}")
    print(f"   Encrypted Key Ciphertext: {enc_key[:20]}...[TRUNCATED]")

    # Check live balance of generated address (expected: 0.0 ETH)
    balance = await adapter.get_balance(escrow_addr)
    print(f"4. Live Escrow Balance on Testnet: {balance} {settings.SUPPORTED_TOKEN_SYMBOL}")

    # Estimate standard transfer fee
    dummy_to = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
    fee_est = await adapter.estimate_fee(escrow_addr, dummy_to, Decimal("0.001"))
    print(f"5. Gas & Fee Estimation for standard transfer:")
    print(f"   Gas Limit: {fee_est['gas_limit']}")
    print(f"   Gas Price: {fee_est['gas_price']} wei ({Web3.from_wei(fee_est['gas_price'], 'gwei')} Gwei)")
    print(f"   Estimated Fee: {fee_est['estimated_fee_eth']:.8f} {settings.SUPPORTED_TOKEN_SYMBOL}")

    print("=" * 60)
    print("All live testnet primitives verified successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
