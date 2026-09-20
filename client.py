import os
import logging
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger("AutoMMClient")

AUTOMM_API_URL = os.environ.get("AUTOMM_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/")
BACKEND_SECRET = os.environ.get("BACKEND_SECRET", "automm-dev-secret-key-change-in-production")


class AutoMMApiError(Exception):
    """Custom exception with user-friendly error messages from backend."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AutoMMClient:
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = (base_url or AUTOMM_API_URL).rstrip("/")

    async def _request(self, method: str, path: str, json: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        req_headers = headers or {}
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.request(method, url, json=json, headers=req_headers)
                if resp.is_error:
                    detail = "Backend request failed"
                    try:
                        err_json = resp.json()
                        detail = err_json.get("detail", detail)
                    except Exception:
                        detail = resp.text or detail
                    raise AutoMMApiError(detail, status_code=resp.status_code)
                return resp.json()
        except httpx.ConnectError:
            raise AutoMMApiError("Cannot connect to AutoMM Escrow Backend. Is the backend server running?")
        except httpx.TimeoutException:
            raise AutoMMApiError("Request to AutoMM Escrow Backend timed out.")
        except AutoMMApiError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error calling backend {url}: {e}")
            raise AutoMMApiError(f"Backend communication error: {str(e)}")

    async def create_deal(
        self,
        buyer_id: str,
        seller_id: Optional[str] = None,
        amount: str = "0",
        token: str = "ETH",
        network: str = "SEPOLIA"
    ) -> Dict[str, Any]:
        payload = {
            "buyer_id": str(buyer_id),
            "seller_id": str(seller_id) if seller_id else None,
            "amount": str(amount),
            "token": token.upper(),
            "network": network.upper()
        }
        return await self._request("POST", "/deals", json=payload)

    async def add_seller(self, deal_id: str, seller_id: str) -> Dict[str, Any]:
        payload = {"seller_id": str(seller_id)}
        return await self._request("POST", f"/deals/{deal_id}/seller", json=payload)

    async def set_terms(self, deal_id: str, amount: str, token: str = "ETH", network: str = "SEPOLIA") -> Dict[str, Any]:
        payload = {
            "amount": str(amount),
            "token": token.upper(),
            "network": network.upper()
        }
        return await self._request("PATCH", f"/deals/{deal_id}/terms", json=payload)

    async def agree(
        self,
        deal_id: str,
        user_id: str,
        buyer_refund_address: Optional[str] = None,
        seller_payout_address: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "user_id": str(user_id),
            "buyer_refund_address": buyer_refund_address,
            "seller_payout_address": seller_payout_address
        }
        return await self._request("POST", f"/deals/{deal_id}/agree", json=payload)

    async def create_escrow(self, deal_id: str, actor_id: Optional[str] = None) -> Dict[str, Any]:
        headers = {"x-actor-id": str(actor_id)} if actor_id else {}
        return await self._request("POST", f"/deals/{deal_id}/escrow", headers=headers)

    async def get_deal(self, deal_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/deals/{deal_id}")

    async def get_deposit(self, deal_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/deals/{deal_id}/deposit")

    async def get_status(self, deal_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/deals/{deal_id}/status")

    async def mark_delivered(self, deal_id: str, seller_id: str) -> Dict[str, Any]:
        payload = {"seller_id": str(seller_id)}
        return await self._request("POST", f"/deals/{deal_id}/deliver", json=payload)

    async def release(self, deal_id: str, requester_id: str, destination_address: Optional[str] = None) -> Dict[str, Any]:
        payload = {
            "requester_id": str(requester_id),
            "destination_address": destination_address
        }
        return await self._request("POST", f"/deals/{deal_id}/release", json=payload)

    async def refund(
        self,
        deal_id: str,
        requester_id: Optional[str] = None,
        reason: Optional[str] = None,
        destination_address: Optional[str] = None
    ) -> Dict[str, Any]:
        req_id = requester_id or BACKEND_SECRET
        payload = {
            "requester_id": str(req_id),
            "reason": reason or "Dispute resolved by staff",
            "destination_address": destination_address
        }
        return await self._request("POST", f"/deals/{deal_id}/refund", json=payload)

    async def open_dispute(self, deal_id: str, requester_id: str, reason: str) -> Dict[str, Any]:
        payload = {
            "requester_id": str(requester_id),
            "reason": reason
        }
        return await self._request("POST", f"/deals/{deal_id}/dispute", json=payload)
