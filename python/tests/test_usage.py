"""
Batch 33-foundation tests — Usage resource + cursor pagination.

Wires `/v1/usage`, `/v1/usage/estimate`, `/v1/usage/items`,
`/v1/usage/items.csv` (Batch 32 sub-A + sub-B endpoints) into the SDK.
Pins the auto-pagination semantics + CSV roundtrip + AuditProofs alias.
"""

from __future__ import annotations

import httpx

from nanorix._pagination import SyncPage
from nanorix.resources.usage import UsageItem


def _usage_item_dict(idx: int, billable: bool = True) -> dict:
    """Build a fake usage item dict matching the API wire shape."""
    return {
        "id": f"00000000-0000-0000-0000-{idx:012d}",
        "capsule_id": f"00000000-1111-0000-0000-{idx:012d}",
        "capsulefile_id": f"cf_test{idx}",
        "capsulefile_version": 1,
        "created_at": "2026-05-05T12:00:00+00:00",
        "signing_authority_id": "us-kms-nanorix-v1" if billable else "us-kms-nanorix-test-v1",
        "data_classification": "PHI",
        "billable": billable,
        "cdp_charge_cents": 4 if billable else 0,
    }


def test_usage_summary_returns_dict(mock_api, client):
    mock_api.get("/v1/usage").mock(
        return_value=httpx.Response(
            200,
            json={
                "summary": {"total_cdps": 42, "total_compute_minutes": 7.5, "total_capsules": 50},
                "daily": [],
            },
        )
    )
    out = client.usage.summary()
    assert out["summary"]["total_cdps"] == 42


def test_usage_estimate_returns_dict(mock_api, client):
    mock_api.get("/v1/usage/estimate").mock(
        return_value=httpx.Response(
            200,
            json={
                "period_start": "2026-05-01T00:00:00Z",
                "period_end": "2026-05-31T00:00:00Z",
                "tier": "starter",
                "platform_fee": 9900,
                "cdp_count": 42,
                "cdp_charge": 168,
                "compute_minutes": 7.5,
                "compute_charge": 375,
                "total_estimate": 10443,
            },
        )
    )
    out = client.usage.estimate()
    assert out["tier"] == "starter"
    assert out["total_estimate"] == 10443


def test_usage_list_items_single_page_no_more(mock_api, client):
    """Single page, has_more=false → iterator stops cleanly."""
    mock_api.get("/v1/usage/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [_usage_item_dict(i) for i in range(3)],
                "has_more": False,
            },
        )
    )
    items = list(client.usage.list_items(limit=10))
    assert len(items) == 3
    assert all(isinstance(i, UsageItem) for i in items)
    assert items[0].billable is True
    assert items[0].cdp_charge_cents == 4


def test_usage_list_items_auto_pagination_advances_cursor(mock_api, client):
    """Has_more=true on first page → second page fetched with starting_after=last.id."""
    page_1 = [_usage_item_dict(i) for i in range(3)]
    page_2 = [_usage_item_dict(i) for i in range(3, 5)]

    route = mock_api.get("/v1/usage/items")

    def handler(request):
        params = dict(request.url.params)
        if "starting_after" not in params:
            return httpx.Response(200, json={"object": "list", "data": page_1, "has_more": True})
        # second page expects starting_after = last id from page_1
        assert params["starting_after"] == page_1[-1]["id"]
        return httpx.Response(200, json={"object": "list", "data": page_2, "has_more": False})

    route.mock(side_effect=handler)
    items = list(client.usage.list_items(limit=3))
    assert len(items) == 5
    # Verify cursor advance happened (mock would assert if not)


def test_usage_list_items_test_authority_renders_zero_charge(mock_api, client):
    """Per D2 LOCKED: test-authority CDPs are non-billable; cdp_charge_cents=0."""
    mock_api.get("/v1/usage/items").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [_usage_item_dict(0, billable=False)],
                "has_more": False,
            },
        )
    )
    items = list(client.usage.list_items())
    assert items[0].billable is False
    assert items[0].cdp_charge_cents == 0
    assert items[0].signing_authority_id == "us-kms-nanorix-test-v1"


def test_usage_export_csv_returns_bytes(mock_api, client):
    """CSV endpoint returns text/csv bytes."""
    csv_body = (
        b"id,capsule_id,capsulefile_id,capsulefile_version,created_at,"
        b"signing_authority_id,data_classification,billable,cdp_charge_cents\n"
        b"00000000-0000-0000-0000-000000000001,cap_1,cf_x,1,2026-05-05T12:00:00+00:00,"
        b"us-kms-nanorix-v1,PHI,true,4\n"
    )
    mock_api.get("/v1/usage/items.csv").mock(
        return_value=httpx.Response(200, content=csv_body, headers={"content-type": "text/csv"})
    )
    out = client.usage.export_csv(limit=10)
    assert isinstance(out, bytes)
    assert out.startswith(b"id,capsule_id,")
    assert b"us-kms-nanorix-v1" in out


def test_audit_proofs_alias_points_to_cdp_resource(client):
    """: client.audit_proofs is alias for client.cdp; same instance."""
    assert client.audit_proofs is client.cdp


def test_sync_page_stop_iteration_after_exhaustion():
    """SyncPage raises StopIteration when has_more=false and buffer drained."""
    pages_returned = [{"data": [{"id": "a", "v": 1}], "has_more": False}]

    def fetch(cursor, limit):
        return pages_returned.pop(0)

    page = SyncPage(
        fetch_page=fetch,
        parse_item=lambda d: d,
        limit=10,
    )
    consumed = list(page)
    assert len(consumed) == 1
    # Second iteration should be empty (already exhausted)
    assert list(page) == []
