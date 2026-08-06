"""Tests for the Billing meter readout SDK helpers.

Batch 3 sub-item 1 (CR-002, 2026-05-27). Mirrors the Rust-side surface in
`services/api/src/routes/billing_meter.rs`. These tests exercise the SDK
parsing layer + URL construction against a mock HTTP transport (offline;
no live API roundtrip). Round-trip tests against the live endpoint live
in the Rust integration suite.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from nanorix.billing import (
    AsyncBilling,
    Billing,
    BillingMeterUsage,
    CostForecast,
    PatternUsage,
    ShapeUsage,
    _parse_billing_meter_usage,
)


# ── Mock HTTP transport ───────────────────────────────────────────


class FakeHttp:
    """Records the most recent request + returns canned JSON."""

    def __init__(self, response: Any):
        self.response = response
        self.last_method: str | None = None
        self.last_path: str | None = None
        self.last_params: Dict[str, str] | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, str] | None = None,
        **_: Any,
    ) -> Any:
        self.last_method = method
        self.last_path = path
        self.last_params = params or {}
        return self.response


class FakeAsyncHttp:
    """Async parity for FakeHttp."""

    def __init__(self, response: Any):
        self.response = response
        self.last_method: str | None = None
        self.last_path: str | None = None
        self.last_params: Dict[str, str] | None = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, str] | None = None,
        **_: Any,
    ) -> Any:
        self.last_method = method
        self.last_path = path
        self.last_params = params or {}
        return self.response


# ── Canonical fixture payloads ────────────────────────────────────


def make_usage_payload() -> Dict[str, Any]:
    return {
        "from": "2026-05-01T00:00:00Z",
        "to": "2026-05-31T00:00:00Z",
        "tier": "starter",
        "capsule_count": 250,
        "auditproof_count": 350,
        "by_pattern": [
            {"pattern_tag": "pa", "auditproof_count": 100},
            {"pattern_tag": "rcm_claim", "auditproof_count": 150},
            {"pattern_tag": "untagged", "auditproof_count": 100},
        ],
        "by_shape": [
            {"shape": "small", "capsule_count": 200, "auditproof_count": 280},
            {"shape": "medium", "capsule_count": 40, "auditproof_count": 60},
            {"shape": "large", "capsule_count": 10, "auditproof_count": 10},
        ],
        "by_capsulefile": [
            {
                "capsulefile_id": "cf_abc123",
                "capsule_count": 200,
                "auditproof_count": 280,
            },
        ],
        "disclaimer": (
            "Usage counts are structural facts derived from emitted AuditProofs. "
            "The auditor remains the certifying authority for any compliance program."
        ),
    }


def make_forecast_payload(*, tier: str = "starter") -> Dict[str, Any]:
    base = {
        "billing_cycle_start": "2026-05-01T00:00:00Z",
        "billing_cycle_end": "2026-05-31T00:00:00Z",
        "tier": tier,
        "current_cycle_auditproof_count": 500,
        "current_cycle_spend_cents": 2_000,
        "forecasted_cycle_spend_cents": 6_000,
        "disclaimer": "Usage counts are structural facts...",
    }
    if tier in {"starter", "business"}:
        base["cdp_rate_cents"] = 4 if tier == "starter" else 3
    return base


# ── Sync Billing tests ────────────────────────────────────────────


def test_get_usage_meter_no_params() -> None:
    """Calling without dates omits query parameters entirely."""
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    usage = billing.get_usage_meter()
    assert http.last_method == "GET"
    assert http.last_path == "/v1/billing/meter/usage"
    assert http.last_params == {}
    assert isinstance(usage, BillingMeterUsage)
    assert usage.capsule_count == 250
    assert usage.auditproof_count == 350


def test_get_usage_meter_with_date_range() -> None:
    """from_date + to_date land as `from` + `to` query parameters."""
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    billing.get_usage_meter(from_date="2026-05-01", to_date="2026-05-31")
    assert http.last_params == {"from": "2026-05-01", "to": "2026-05-31"}


def test_get_usage_meter_maps_reserved_from_keyword() -> None:
    """API field `from` maps to Python attribute `from_` (reserved keyword)."""
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    usage = billing.get_usage_meter()
    assert usage.from_ == "2026-05-01T00:00:00Z"
    assert usage.to == "2026-05-31T00:00:00Z"


def test_get_usage_meter_parses_by_pattern_rows() -> None:
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    usage = billing.get_usage_meter()
    assert len(usage.by_pattern) == 3
    pa = next(r for r in usage.by_pattern if r.pattern_tag == "pa")
    assert pa.auditproof_count == 100
    # `untagged` sentinel for receipts without pattern_tag
    untagged = next(r for r in usage.by_pattern if r.pattern_tag == "untagged")
    assert untagged.auditproof_count == 100


def test_get_usage_meter_parses_by_shape_rows() -> None:
    """Shape rows mirror cold-start-benchmark taxonomy."""
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    usage = billing.get_usage_meter()
    shapes = {r.shape for r in usage.by_shape}
    assert shapes == {"small", "medium", "large"}


def test_get_usage_by_shape_only() -> None:
    payload = [
        {"shape": "small", "capsule_count": 100, "auditproof_count": 150},
        {"shape": "large", "capsule_count": 5, "auditproof_count": 5},
    ]
    http = FakeHttp(payload)
    billing = Billing(http)
    rows = billing.get_usage_by_shape()
    assert http.last_path == "/v1/billing/meter/usage/by-shape"
    assert len(rows) == 2
    assert all(isinstance(r, ShapeUsage) for r in rows)


def test_get_usage_by_pattern_only() -> None:
    payload = [
        {"pattern_tag": "pa", "auditproof_count": 100},
        {"pattern_tag": "agent_step", "auditproof_count": 50},
        {"pattern_tag": "untagged", "auditproof_count": 10},
    ]
    http = FakeHttp(payload)
    billing = Billing(http)
    rows = billing.get_usage_by_pattern()
    assert http.last_path == "/v1/billing/meter/usage/by-pattern"
    assert len(rows) == 3
    assert all(isinstance(r, PatternUsage) for r in rows)
    # untagged sentinel must be parseable.
    assert any(r.pattern_tag == "untagged" for r in rows)


def test_get_cost_forecast_starter() -> None:
    http = FakeHttp(make_forecast_payload(tier="starter"))
    billing = Billing(http)
    forecast = billing.get_cost_forecast()
    assert http.last_path == "/v1/billing/meter/cost-forecast"
    assert isinstance(forecast, CostForecast)
    assert forecast.tier == "starter"
    assert forecast.cdp_rate_cents == 4
    assert forecast.current_cycle_spend_cents == 2_000
    assert forecast.forecasted_cycle_spend_cents == 6_000


def test_get_cost_forecast_free_tier_omits_rate() -> None:
    """Free tier has no per-CDP rate → field omitted in JSON."""
    payload: Dict[str, Any] = {
        "billing_cycle_start": "2026-05-01T00:00:00Z",
        "billing_cycle_end": "2026-05-31T00:00:00Z",
        "tier": "free",
        "current_cycle_auditproof_count": 50,
        "current_cycle_spend_cents": 0,
        "forecasted_cycle_spend_cents": 0,
        "disclaimer": "...",
    }
    http = FakeHttp(payload)
    billing = Billing(http)
    forecast = billing.get_cost_forecast()
    assert forecast.cdp_rate_cents is None
    assert forecast.current_cycle_spend_cents == 0


def test_disclaimer_carries_no_forbidden_vocabulary() -> None:
    """Pre-publish gate scan — STRUCTURAL FACTS only.

    Per feedback_narrow_signed_claim_auditor_certifies.md, billing meter
    output NEVER asserts compliance satisfaction.
    """
    http = FakeHttp(make_usage_payload())
    billing = Billing(http)
    usage = billing.get_usage_meter()
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in usage.disclaimer
        assert forbidden.lower() not in usage.disclaimer.lower()


# ── Async parity tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_get_usage_meter() -> None:
    http = FakeAsyncHttp(make_usage_payload())
    billing = AsyncBilling(http)
    usage = await billing.get_usage_meter()
    assert http.last_method == "GET"
    assert http.last_path == "/v1/billing/meter/usage"
    assert isinstance(usage, BillingMeterUsage)
    assert usage.capsule_count == 250


@pytest.mark.asyncio
async def test_async_get_cost_forecast() -> None:
    http = FakeAsyncHttp(make_forecast_payload(tier="business"))
    billing = AsyncBilling(http)
    forecast = await billing.get_cost_forecast()
    assert forecast.tier == "business"
    assert forecast.cdp_rate_cents == 3


# ── Parse-fixture round trip ──────────────────────────────────────


def test_parse_billing_meter_usage_handles_from_keyword() -> None:
    """_parse_billing_meter_usage handles the `from` → `from_` keyword swap."""
    payload = make_usage_payload()
    usage = _parse_billing_meter_usage(payload)
    assert usage.from_ == payload["from"]
    assert usage.to == payload["to"]


def test_parse_billing_meter_usage_idempotent_when_from_underscore_present() -> None:
    """Idempotent when payload already uses `from_` (defensive coding)."""
    payload = make_usage_payload()
    # Simulate a caller / proxy that already remapped the field.
    payload["from_"] = payload.pop("from")
    usage = _parse_billing_meter_usage(payload)
    assert usage.from_ == "2026-05-01T00:00:00Z"
