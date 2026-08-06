"""
Main client classes. Users interact with these.

Usage:
    import nanorix

    # Sync
    client = nanorix.Client(api_key="nrx_live_...")

    # Async
    client = nanorix.AsyncClient(api_key="nrx_live_...")
"""
from typing import Any, Dict, cast

from nanorix._constants import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, MAX_RETRIES
from nanorix._http import AsyncHttpTransport, HttpTransport
from nanorix.billing import AsyncBilling, Billing
from nanorix.resources.capsules import AsyncCapsules, Capsules
from nanorix.resources.cdp import AsyncCdpResource, CdpResource
from nanorix.resources.capsulefiles import AsyncCapsulefiles, Capsulefiles
from nanorix.resources.egress_profiles import AsyncEgressProfiles, EgressProfiles
from nanorix.resources.usage import AsyncUsage, Usage
from nanorix.resources.webhooks import AsyncWebhooks, Webhooks


class Client:
    """
    Synchronous Nanorix API client.

    Usage:
        client = nanorix.Client(api_key="nrx_live_...")
        capsulefile = client.capsulefiles.create(...)
        result, cdp = client.capsules.run(capsulefile=capsulefile.id, ...)
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if not api_key or not api_key.startswith(("nrx_live_", "nrx_test_")):
            raise ValueError(
                "Invalid API key. Keys must start with 'nrx_live_' or 'nrx_test_'. "
                "Get your key at https://app.nanorix.io/settings/api-keys"
            )

        self._http = HttpTransport(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

        self.capsulefiles = Capsulefiles(self._http)
        self.capsules = Capsules(self._http)
        self.cdp = CdpResource(self._http)
        self.audit_proofs = self.cdp  # brand alias; same interface
        self.egress_profiles = EgressProfiles(self._http)
        self.usage = Usage(self._http)
        # Billing meter readout
        # surface mirroring Stripe metered billing for programmatic
        # consumption. By-pattern · by-shape · cost-forecast endpoints.
        self.billing = Billing(self._http)
        self.webhooks = Webhooks(self._http)

    def health(self) -> Dict[str, Any]:
        """Check API health status. GET /v1/health."""
        return cast(Dict[str, Any], self._http.request("GET", "/v1/health"))

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class AsyncClient:
    """
    Asynchronous Nanorix API client.

    Usage:
        async with nanorix.AsyncClient(api_key="nrx_live_...") as client:
            capsulefile = await client.capsulefiles.create(...)
            result, cdp = await client.capsules.run(capsulefile=capsulefile.id, ...)
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        if not api_key or not api_key.startswith(("nrx_live_", "nrx_test_")):
            raise ValueError(
                "Invalid API key. Keys must start with 'nrx_live_' or 'nrx_test_'. "
                "Get your key at https://app.nanorix.io/settings/api-keys"
            )

        self._http = AsyncHttpTransport(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

        self.capsulefiles = AsyncCapsulefiles(self._http)
        self.capsules = AsyncCapsules(self._http)
        self.cdp = AsyncCdpResource(self._http)
        self.audit_proofs = self.cdp  # brand alias; same interface
        self.egress_profiles = AsyncEgressProfiles(self._http)
        self.usage = AsyncUsage(self._http)
        # Async parity for the
        # billing meter readout surface.
        self.billing = AsyncBilling(self._http)
        self.webhooks = AsyncWebhooks(self._http)

    async def health(self) -> Dict[str, Any]:
        """Check API health status (async). GET /v1/health."""
        return cast(Dict[str, Any], await self._http.request("GET", "/v1/health"))

    async def close(self) -> None:
        await self._http.close()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
