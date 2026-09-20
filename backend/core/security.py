import base64
import os
from cryptography.fernet import Fernet
from fastapi import HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from backend.core.config import settings

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def get_fernet_cipher() -> Fernet:
    """Initialize Fernet cipher with configured encryption key."""
    key = settings.ESCROW_ENCRYPTION_KEY.encode()
    try:
        return Fernet(key)
    except Exception:
        # Fallback to deterministic 32-byte urlsafe key if provided key is improperly padded
        padded = base64.urlsafe_b64encode(key.ljust(32, b"0")[:32])
        return Fernet(padded)


def encrypt_private_key(private_key_hex: str) -> str:
    """Encrypt a private key string before storing in database."""
    cipher = get_fernet_cipher()
    encrypted_bytes = cipher.encrypt(private_key_hex.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")


def decrypt_private_key(encrypted_private_key: str) -> str:
    """Decrypt an encrypted private key string for signing."""
    cipher = get_fernet_cipher()
    decrypted_bytes = cipher.decrypt(encrypted_private_key.encode("utf-8"))
    return decrypted_bytes.decode("utf-8")


async def verify_api_key(api_key: str = Security(api_key_header)) -> bool:
    """Verify API Key for protected administrative/system operations."""
    if not api_key or api_key != settings.BACKEND_SECRET:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header"
        )
    return True
