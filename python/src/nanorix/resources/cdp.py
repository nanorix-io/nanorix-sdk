"""CDP resource — sync and async implementations."""
from __future__ import annotations

from nanorix._http import AsyncHttpTransport, HttpTransport
from nanorix._models import CdpAccess, VerificationCdp


class CdpResource:
    """Synchronous CDP operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def get(self, capsule_id: str) -> CdpAccess:
        """Get CDP for a destroyed capsule (requires auth)."""
        full = self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp")
        verify = self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp/verify")
        return CdpAccess(
            full_data=full,
            verification_data=verify,
            capsule_id=capsule_id,
        )

    def verify(self, capsule_id: str) -> VerificationCdp:
        """Get verification CDP (public, no auth required)."""
        result = self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp/verify")
        return VerificationCdp.model_validate(result)


class AsyncCdpResource:
    """Asynchronous CDP operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def get(self, capsule_id: str) -> CdpAccess:
        """Get CDP for a destroyed capsule (async, requires auth)."""
        full = await self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp")
        verify = await self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp/verify")
        return CdpAccess(
            full_data=full,
            verification_data=verify,
            capsule_id=capsule_id,
        )

    async def verify(self, capsule_id: str) -> VerificationCdp:
        """Get verification CDP (async, public)."""
        result = await self._http.request("GET", f"/v1/capsules/{capsule_id}/cdp/verify")
        return VerificationCdp.model_validate(result)
