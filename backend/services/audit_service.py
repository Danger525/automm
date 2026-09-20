import json
import logging
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from backend.domain.models import AuditLog

logger = logging.getLogger("AuditService")

SENSITIVE_KEYS = {
    "private_key",
    "encrypted_private_key",
    "key",
    "secret",
    "seed",
    "mnemonic",
    "password",
    "token"
}


def sanitize_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not meta:
        return {}
    clean = {}
    for k, v in meta.items():
        if any(secret_term in k.lower() for secret_term in SENSITIVE_KEYS):
            clean[k] = "[REDACTED]"
        elif isinstance(v, dict):
            clean[k] = sanitize_metadata(v)
        else:
            clean[k] = v
    return clean


class AuditService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        action: str,
        actor: str,
        deal_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> AuditLog:
        clean_meta = sanitize_metadata(metadata)
        entry = AuditLog(
            deal_id=deal_id,
            action=action,
            actor=actor,
            metadata_json=json.dumps(clean_meta) if clean_meta else None
        )
        session.add(entry)
        logger.info(f"AUDIT | action={action} | actor={actor} | deal_id={deal_id}")
        return entry
