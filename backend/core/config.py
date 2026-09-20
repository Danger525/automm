import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    PROJECT_NAME: str = "AutoMM Crypto Escrow Backend"
    API_V1_STR: str = "/api/v1"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./escrow.db"

    # Blockchain (EVM Testnet)
    BLOCKCHAIN_RPC_URL: str = "https://ethereum-sepolia-rpc.publicnode.com"
    BLOCKCHAIN_NETWORK: str = "SEPOLIA"
    CHAIN_ID: int = 11155111
    CONFIRMATION_THRESHOLD: int = 2
    POLL_INTERVAL_SECONDS: int = 8

    # Asset Configuration
    SUPPORTED_TOKEN_SYMBOL: str = "ETH"
    SUPPORTED_TOKEN_ADDRESS: Optional[str] = None  # None = Native coin (ETH/POL)
    SUPPORTED_TOKEN_DECIMALS: int = 18

    # Encryption key for private keys at rest (Fernet URL-safe base64 32-byte key)
    ESCROW_ENCRYPTION_KEY: str = "gH8K_yZ9gV8qL1W2e3R4t5Y6u7I8o9P0a1S2d3F4g5H="

    # Security / Auth
    BACKEND_SECRET: str = "automm-dev-secret-key-change-in-production"


settings = Settings()
