"""Billing meter readout helpers — Batch 3 sub-item 1 (CR-002, 2026-05-27).

Programmatic surface mirroring Stripe metered billing data via the Nanorix
API. Customers — particularly mid-pilot pharma + Mayo + agent customers —
consistently ask: "show me spend by capsule shape per tenant per month."
Stripe metered billing carries the line items; this module exposes the
same data via the Nanorix API for programmatic consumption (dashboards,
cost-forecast scripts, CFO reconciliation pipelines).

## Discipline (CCO policy + feedback_narrow_signed_claim_auditor_certifies.md)

- Usage counts + cost forecasts are STRUCTURAL FACTS — not compliance
  adjudication. The auditor remains the certifying authority.
- Forbidden vocabulary anywhere in module output: COMPLIANT / SATISFIED /
  PASSED / MEETS. Output is "counted N receipts with tag X", never
  "X satisfies Y."

## Usage

```python
import nanorix

client = nanorix.Client(api_key="nrx_live_...")

# Full breakdown (current cycle by default)
usage = client.billing.get_usage_meter()
print(f"Total AuditProofs: {usage.auditproof_count}")
for row in usage.by_pattern:
    print(f"  {row.pattern_tag}: {row.auditproof_count}")

# Shape-only slice
shape_rows = client.billing.get_usage_by_shape()
for row in shape_rows:
    print(f"{row.shape}: {row.capsule_count} capsules")

# Pattern-only slice
pattern_rows = client.billing.get_usage_by_pattern()

# Cost forecast
forecast = client.billing.get_cost_forecast()
print(f"Current spend: ${forecast.current_cycle_spend_cents / 100:.2f}")
print(f"Forecasted:    ${forecast.forecasted_cycle_spend_cents / 100:.2f}")

# Pinned date range
custom = client.billing.get_usage_meter(
    from_date="2026-04-01",
    to_date="2026-04-30",
)
```
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from nanorix._http import AsyncHttpTransport, HttpTransport


class PatternUsage(BaseModel):
    """One row in the by-pattern breakdown.

    `pattern_tag` mirrors the closed-enum wire form (snake_case).
    The sentinel `untagged` covers receipts without a `pattern_tag` field
    (pre-Wave-N proofs or receipts where the customer omitted the tag).
    """

    model_config = ConfigDict(extra="allow")

    pattern_tag: str
    auditproof_count: int


class ShapeUsage(BaseModel):
    """One row in the by-shape breakdown.

    Shape bucket: `small` (<1GB) / `medium` (1-4GB) / `large` (>4GB) /
    `unknown` (capsules with no linked Capsulefile). Mirrors the
    cold-start-benchmark taxonomy.
    """

    model_config = ConfigDict(extra="allow")

    shape: str
    capsule_count: int
    auditproof_count: int


class CapsulefileUsage(BaseModel):
    """One row in the by-capsulefile breakdown (top 50 by capsule_count)."""

    model_config = ConfigDict(extra="allow")

    capsulefile_id: str
    capsule_count: int
    auditproof_count: int


class BillingMeterUsage(BaseModel):
    """Top-level billing-meter usage breakdown.

    Returned by `Billing.get_usage_meter()`. All counts are STRUCTURAL
    FACTS — production-authority filter applied (test-authority capsules
    NON-billable per D2 LOCKED). `disclaimer` carries the canonical
    "auditor adjudicates" surface.
    """

    model_config = ConfigDict(extra="allow")

    from_: str  # API field `from`
    to: str
    tier: str
    capsule_count: int
    auditproof_count: int
    by_pattern: List[PatternUsage]
    by_shape: List[ShapeUsage]
    by_capsulefile: List[CapsulefileUsage]
    disclaimer: str


class CostForecast(BaseModel):
    """Per-tenant cost forecast for the current billing cycle.

    `current_cycle_spend_cents = cdp_count × tier_rate_cents`.
    `forecasted_cycle_spend_cents = (cdp_count / elapsed_days) × cycle_days`.

    For Free + Enterprise tiers, `cdp_rate_cents` is omitted and both
    spend fields are 0 (Free tier hard-caps at 100 / cycle; Enterprise
    is custom-billed).
    """

    model_config = ConfigDict(extra="allow")

    billing_cycle_start: str
    billing_cycle_end: str
    tier: str
    current_cycle_auditproof_count: int
    cdp_rate_cents: Optional[int] = None
    current_cycle_spend_cents: int
    forecasted_cycle_spend_cents: int
    disclaimer: str


def _build_meter_params(
    *,
    from_date: Optional[str],
    to_date: Optional[str],
) -> Dict[str, str]:
    params: Dict[str, str] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return params


def _parse_billing_meter_usage(payload: Dict[str, Any]) -> BillingMeterUsage:
    """Pydantic-coerce the API JSON, mapping `from` (reserved kw) → `from_`."""
    coerced = dict(payload)
    if "from" in coerced and "from_" not in coerced:
        coerced["from_"] = coerced.pop("from")
    return BillingMeterUsage.model_validate(coerced)


class Billing:
    """Synchronous billing meter operations.

    Wires four endpoints (Batch 3 sub-item 1):
    - GET /v1/billing/meter/usage
    - GET /v1/billing/meter/usage/by-shape
    - GET /v1/billing/meter/usage/by-pattern
    - GET /v1/billing/meter/cost-forecast
    """

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def get_usage_meter(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> BillingMeterUsage:
        """Full billing meter breakdown over a window.

        Args:
            from_date: Inclusive cycle start (ISO-8601 `YYYY-MM-DD`).
                Defaults to the customer's current billing cycle start.
            to_date: Exclusive cycle end (ISO-8601 `YYYY-MM-DD`).
                Defaults to `billing_cycle_start + 30 days`.

        Returns:
            Full breakdown by pattern_tag, shape, and capsulefile_id.

        Discipline: counts are STRUCTURAL FACTS; per
        `feedback_narrow_signed_claim_auditor_certifies.md`, never asserts
        compliance satisfaction.
        """
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = self._http.request("GET", "/v1/billing/meter/usage", params=params)
        return _parse_billing_meter_usage(raw)

    def get_usage_by_shape(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[ShapeUsage]:
        """Thin slice — by-shape breakdown only.

        Shape buckets mirror the cold-start-benchmark taxonomy:
        `small` (<1GB) / `medium` (1-4GB) / `large` (>4GB) / `unknown`.
        """
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = self._http.request(
            "GET", "/v1/billing/meter/usage/by-shape", params=params
        )
        return [ShapeUsage.model_validate(r) for r in raw]

    def get_usage_by_pattern(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[PatternUsage]:
        """Thin slice — by-pattern_tag breakdown only.

        Returns the closed-enum pattern_tag rows + the
        `untagged` sentinel for receipts without a tag.
        """
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = self._http.request(
            "GET", "/v1/billing/meter/usage/by-pattern", params=params
        )
        return [PatternUsage.model_validate(r) for r in raw]

    def get_cost_forecast(self) -> CostForecast:
        """Current-cycle cost forecast at the customer's tier rate.

        Forecast = `(cdp_count / elapsed_cycle_days) × cycle_days × rate`.
        Reconciles to `usage::get_estimate` per-tier rate table; the
        rate-table-reconciliation invariant is pinned by the Rust-side
        test `cost_forecast_rate_table_reconciles_to_estimate_table`.
        """
        raw = self._http.request("GET", "/v1/billing/meter/cost-forecast")
        return CostForecast.model_validate(raw)


class AsyncBilling:
    """Asynchronous billing meter operations.

    Async parity of `Billing`. Endpoints + return types identical.
    """

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def get_usage_meter(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> BillingMeterUsage:
        """Async: full billing meter breakdown over a window."""
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = await self._http.request("GET", "/v1/billing/meter/usage", params=params)
        return _parse_billing_meter_usage(raw)

    async def get_usage_by_shape(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[ShapeUsage]:
        """Async: by-shape breakdown only."""
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = await self._http.request(
            "GET", "/v1/billing/meter/usage/by-shape", params=params
        )
        return [ShapeUsage.model_validate(r) for r in raw]

    async def get_usage_by_pattern(
        self,
        *,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[PatternUsage]:
        """Async: by-pattern_tag breakdown only."""
        params = _build_meter_params(from_date=from_date, to_date=to_date)
        raw = await self._http.request(
            "GET", "/v1/billing/meter/usage/by-pattern", params=params
        )
        return [PatternUsage.model_validate(r) for r in raw]

    async def get_cost_forecast(self) -> CostForecast:
        """Async: current-cycle cost forecast."""
        raw = await self._http.request("GET", "/v1/billing/meter/cost-forecast")
        return CostForecast.model_validate(raw)


__all__ = [
    "Billing",
    "AsyncBilling",
    "BillingMeterUsage",
    "CostForecast",
    "PatternUsage",
    "ShapeUsage",
    "CapsulefileUsage",
]
