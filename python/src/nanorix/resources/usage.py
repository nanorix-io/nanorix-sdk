"""
Usage resource — wires `/v1/usage`, `/v1/usage/estimate`, `/v1/usage/items`.

`/v1/usage/items` is the Stripe-pattern cursor-paginated per-CDP listing
shipped. SDK auto-pages via `SyncPage` / `AsyncPage`.
`/v1/usage/items.csv` is exposed via `.export_csv()`
returning the raw CSV bytes for direct save / pipeline injection.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, cast

from pydantic import BaseModel, ConfigDict

from nanorix._http import AsyncHttpTransport, HttpTransport
from nanorix._pagination import AsyncPage, SyncPage


class UsageItem(BaseModel):
    """One CDP in the per-CDP usage listing.

    Mirrors the API response shape from `/v1/usage/items`. The
    `billable` flag is server-derived from `signing_authority_id`
    (production authority only); `cdp_charge_cents` is the per-CDP
    USD-cent rate at the customer's tier (test-authority CDPs always 0).
    """

    model_config = ConfigDict(extra="allow")

    id: str
    capsule_id: str
    capsulefile_id: Optional[str] = None
    capsulefile_version: Optional[int] = None
    created_at: str
    signing_authority_id: str
    data_classification: Optional[str] = None
    billable: bool
    cdp_charge_cents: int = 0


class Usage:
    """Synchronous usage operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def summary(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return aggregate usage summary + daily breakdown.

        GET /v1/usage. Returns raw dict per existing v0.4.0 shape.
        """
        params: Dict[str, str] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return cast(Dict[str, Any], self._http.request("GET", "/v1/usage", params=params))

    def estimate(self) -> Dict[str, Any]:
        """Return current billing-cycle cost estimate.

        GET /v1/usage/estimate.
        """
        return cast(Dict[str, Any], self._http.request("GET", "/v1/usage/estimate"))

    def list_items(
        self,
        *,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> SyncPage[UsageItem]:
        """Auto-paginated per-CDP listing.

        Stripe-pattern cursor; iterate to consume all CDPs:

            for item in client.usage.list_items():
                if item.billable:
                    print(item.id, item.cdp_charge_cents)

        Per-page roundtrip; transparently chains pages via
        `starting_after = last.id`. Stops when server returns
        `has_more=false`.

        Args:
            limit: Per-page limit, clamped server-side to 1..=100.
            starting_after: Optional initial cursor (resume from a
                specific CDP); typically not needed for full iteration.
        """

        def fetch(cursor: Optional[str], page_limit: int) -> Dict[str, Any]:
            params: Dict[str, str] = {"limit": str(page_limit)}
            if cursor:
                params["starting_after"] = cursor
            return cast(Dict[str, Any], self._http.request("GET", "/v1/usage/items", params=params))

        page = SyncPage(
            fetch_page=fetch,
            parse_item=lambda d: UsageItem.model_validate(d),
            limit=limit,
        )
        if starting_after:
            page._cursor = starting_after
        return page

    def export_csv(
        self,
        *,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> bytes:
        """Export per-CDP listing as CSV bytes.

        GET /v1/usage/items.csv. Returns one page (limit-bound). For
        full historical export, iterate `list_items()` page-by-page and
        stitch CSV bodies, or call repeatedly with `starting_after`.

        Returns:
            Raw CSV bytes (text/csv, RFC 4180 columns: id, capsule_id,
            capsulefile_id, capsulefile_version, created_at,
            signing_authority_id, data_classification, billable,
            cdp_charge_cents).
        """
        params: Dict[str, str] = {"limit": str(limit)}
        if starting_after:
            params["starting_after"] = starting_after
        return cast(bytes, self._http.request("GET", "/v1/usage/items.csv", params=params, raw_response=True))


class AsyncUsage:
    """Asynchronous usage operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def summary(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return cast(Dict[str, Any], await self._http.request("GET", "/v1/usage", params=params))

    async def estimate(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], await self._http.request("GET", "/v1/usage/estimate"))

    def list_items(
        self,
        *,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> AsyncPage[UsageItem]:
        """Async auto-paginated per-CDP listing. Returns AsyncPage."""

        async def fetch(cursor: Optional[str], page_limit: int) -> Dict[str, Any]:
            params: Dict[str, str] = {"limit": str(page_limit)}
            if cursor:
                params["starting_after"] = cursor
            return cast(Dict[str, Any], await self._http.request("GET", "/v1/usage/items", params=params))

        page = AsyncPage(
            fetch_page=fetch,
            parse_item=lambda d: UsageItem.model_validate(d),
            limit=limit,
        )
        if starting_after:
            page._cursor = starting_after
        return page

    async def export_csv(
        self,
        *,
        limit: int = 100,
        starting_after: Optional[str] = None,
    ) -> bytes:
        params: Dict[str, str] = {"limit": str(limit)}
        if starting_after:
            params["starting_after"] = starting_after
        return cast(bytes, await self._http.request(
                        "GET", "/v1/usage/items.csv", params=params, raw_response=True
                    ))
