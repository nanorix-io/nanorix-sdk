"""CDP resource tests."""
import httpx
from nanorix._models import CdpAccess, VerificationCdp

FULL_CDP = {
    "capsule_id": "cap_cdp001",
    "version": "2.0",
    "activity": [
        {"event": "capsule_started", "at": "2026-03-25T00:00:00Z"},
    ],
    "destruction_trigger": "user_explicit",
    "document_hash": "abcdef1234567890",
}

VERIFICATION_CDP = {
    "capsule_id": "cap_cdp001",
    "version": "2.0",
    "verify": "https://nanorix.io/verify/cap_cdp001",
    "document_hash": "abcdef1234567890",
    "destruction": {"trigger": "user_explicit", "chain": "8/8 steps passed"},
}


def test_get_cdp_full_and_verification(mock_api, client):
    mock_api.get("/v1/capsules/cap_cdp001/cdp").mock(
        return_value=httpx.Response(200, json=FULL_CDP)
    )
    mock_api.get("/v1/capsules/cap_cdp001/cdp/verify").mock(
        return_value=httpx.Response(200, json=VERIFICATION_CDP)
    )

    cdp = client.cdp.get("cap_cdp001")
    assert isinstance(cdp, CdpAccess)
    assert cdp.id == "cap_cdp001"

    full = cdp.full()
    assert full.capsule_id == "cap_cdp001"
    assert full.destruction_trigger == "user_explicit"

    verify = cdp.verification()
    assert verify.capsule_id == "cap_cdp001"


def test_cdp_verify_public(mock_api, client):
    mock_api.get("/v1/capsules/cap_cdp001/cdp/verify").mock(
        return_value=httpx.Response(200, json=VERIFICATION_CDP)
    )

    result = client.cdp.verify("cap_cdp001")
    assert isinstance(result, VerificationCdp)
    assert result.capsule_id == "cap_cdp001"
    assert result.version == "2.0"


def test_cdp_access_verify_url_from_response():
    cdp = CdpAccess(
        full_data=FULL_CDP,
        verification_data=VERIFICATION_CDP,
        capsule_id="cap_cdp001",
    )
    assert cdp.verify_url == "https://nanorix.io/verify/cap_cdp001"


def test_cdp_access_verify_url_fallback():
    cdp = CdpAccess(
        full_data=FULL_CDP,
        verification_data={"capsule_id": "cap_no_url"},
        capsule_id="cap_no_url",
    )
    assert cdp.verify_url == "https://nanorix.io/verify/cap_no_url"


def test_cdp_access_to_dict():
    cdp = CdpAccess(
        full_data=FULL_CDP,
        verification_data=VERIFICATION_CDP,
        capsule_id="cap_cdp001",
    )
    raw = cdp.to_dict()
    assert raw == FULL_CDP
    assert raw["capsule_id"] == "cap_cdp001"
