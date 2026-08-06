"""Webhooks resource — sync and async implementations + signature verification."""
from __future__ import annotations
import hashlib
import hmac
from typing import Any, Dict, List, Optional, cast

from nanorix._http import AsyncHttpTransport, HttpTransport


class Webhook:
    """Static utility for verifying webhook signatures."""

    @staticmethod
    def verify_signature(
        payload: bytes,
        signature: str,
        secret: str,
        timestamp: int,
    ) -> bool:
        """Verify an incoming webhook signature.

        Args:
            payload: Raw request body bytes.
            signature: Value of X-Nanorix-Signature header (e.g., "sha256=abc123...").
            secret: Your webhook endpoint secret (from create response).
            timestamp: Value of X-Nanorix-Timestamp header (Unix seconds).

        Returns:
            True if the signature is valid.
        """
        import time

        if not signature.startswith("sha256="):
            return False

        # Reject timestamps older than 5 minutes to prevent replay attacks
        now = int(time.time())
        if abs(now - timestamp) > 300:
            return False

        expected_sig = signature[7:]  # strip "sha256=" prefix
        message = f"{timestamp}.{payload.decode('utf-8')}"
        computed = hmac.new(
            secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, expected_sig)


class Webhooks:
    """Sync webhook endpoint operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def create(
        self,
        url: str,
        events: List[str],
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a webhook endpoint. Returns endpoint with secret (shown once)."""
        body: Dict[str, Any] = {"url": url, "events": events}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], self._http.request("POST", "/v1/webhooks", json=body))

    def list(self) -> List[Dict[str, Any]]:
        """List all webhook endpoints."""
        return cast(List[Dict[str, Any]], self._http.request("GET", "/v1/webhooks"))

    def get(self, webhook_id: str) -> Dict[str, Any]:
        """Get webhook endpoint details."""
        return cast(Dict[str, Any], self._http.request("GET", f"/v1/webhooks/{webhook_id}"))

    def update(
        self,
        webhook_id: str,
        *,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update a webhook endpoint."""
        body: Dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        if active is not None:
            body["active"] = active
        return cast(Dict[str, Any], self._http.request("PUT", f"/v1/webhooks/{webhook_id}", json=body))

    def delete(self, webhook_id: str) -> None:
        """Delete a webhook endpoint."""
        self._http.request("DELETE", f"/v1/webhooks/{webhook_id}")

    def test(self, webhook_id: str) -> Dict[str, Any]:
        """Send a test event to verify the webhook URL works."""
        return cast(Dict[str, Any], self._http.request("POST", f"/v1/webhooks/{webhook_id}/test"))

    def deliveries(self, webhook_id: str) -> List[Dict[str, Any]]:
        """List recent deliveries for debugging."""
        return cast(List[Dict[str, Any]], self._http.request("GET", f"/v1/webhooks/{webhook_id}/deliveries"))


class AsyncWebhooks:
    """Async webhook endpoint operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def create(self, url: str, events: List[str], *, description: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"url": url, "events": events}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], await self._http.request("POST", "/v1/webhooks", json=body))

    async def list(self) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], await self._http.request("GET", "/v1/webhooks"))

    async def get(self, webhook_id: str) -> Dict[str, Any]:
        return cast(Dict[str, Any], await self._http.request("GET", f"/v1/webhooks/{webhook_id}"))

    async def update(
        self,
        webhook_id: str,
        *,
        url: Optional[str] = None,
        events: Optional[List[str]] = None,
        description: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        if active is not None:
            body["active"] = active
        return cast(
            Dict[str, Any],
            await self._http.request("PUT", f"/v1/webhooks/{webhook_id}", json=body),
        )

    async def delete(self, webhook_id: str) -> None:
        await self._http.request("DELETE", f"/v1/webhooks/{webhook_id}")

    async def test(self, webhook_id: str) -> Dict[str, Any]:
        return cast(Dict[str, Any], await self._http.request("POST", f"/v1/webhooks/{webhook_id}/test"))

    async def deliveries(self, webhook_id: str) -> List[Dict[str, Any]]:
        return cast(List[Dict[str, Any]], await self._http.request("GET", f"/v1/webhooks/{webhook_id}/deliveries"))
