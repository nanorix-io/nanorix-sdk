"""Tests for nanorix.compliance helpers + Capsules.create() metadata plumbing."""
from __future__ import annotations

import json

import httpx
import pytest

from nanorix.compliance import GdprContext, HipaaContext, Soc2Context

# ── HipaaContext ───────────────────────────────────────────────


def test_hipaa_minimal_context_to_metadata_emits_required_keys():
    ctx = HipaaContext(
        covered_entity="Acme Hospital",
        minimum_necessary_purpose="Inference for radiology workflow",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "HIPAA"
    assert meta["nanorix:framework_version"] == "2013-Omnibus"
    assert meta["covered_entity"] == "Acme Hospital"
    assert meta["minimum_necessary_purpose"] == "Inference for radiology workflow"
    assert meta["data_classification"] == "PHI"  # default
    # Optional fields absent
    assert "business_associate" not in meta
    assert "baa_reference" not in meta
    assert "workforce_member_role" not in meta


def test_hipaa_optional_fields_when_set_appear_in_metadata():
    ctx = HipaaContext(
        covered_entity="Acme Hospital",
        minimum_necessary_purpose="Radiology inference",
        data_classification="PHI-radiology",
        business_associate="ba_12345",
        baa_reference="BAA-2026-Q2",
        workforce_member_role="attending_radiologist",
    )
    meta = ctx.to_metadata()
    assert meta["data_classification"] == "PHI-radiology"
    assert meta["business_associate"] == "ba_12345"
    assert meta["baa_reference"] == "BAA-2026-Q2"
    assert meta["workforce_member_role"] == "attending_radiologist"


def test_hipaa_empty_required_field_raises():
    with pytest.raises(ValueError, match="covered_entity"):
        HipaaContext(covered_entity="", minimum_necessary_purpose="X")
    with pytest.raises(ValueError, match="minimum_necessary_purpose"):
        HipaaContext(covered_entity="X", minimum_necessary_purpose="")


def test_hipaa_oversized_value_raises_at_construction():
    # 257-char value exceeds the 256-char wire limit per validation.rs.
    with pytest.raises(ValueError, match="256-char limit"):
        HipaaContext(
            covered_entity="A" * 257,
            minimum_necessary_purpose="X",
        )


def test_hipaa_frozen_dataclass_mutation_raises():
    ctx = HipaaContext(covered_entity="A", minimum_necessary_purpose="X")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        ctx.covered_entity = "B"  # type: ignore[misc]


def test_hipaa_metadata_json_round_trip_preserves_dict():
    ctx = HipaaContext(
        covered_entity="Acme",
        minimum_necessary_purpose="X",
        business_associate="ba_1",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── GdprContext ────────────────────────────────────────────────


def test_gdpr_minimal_context_to_metadata():
    ctx = GdprContext(
        data_controller="Acme GmbH",
        legal_basis="contract",
        processing_purpose="Customer support automation",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "GDPR"
    assert meta["nanorix:framework_version"] == "2016-679"
    assert meta["data_controller"] == "Acme GmbH"
    assert meta["legal_basis"] == "contract"
    assert meta["data_classification"] == "personal_data"  # default


def test_gdpr_legal_basis_must_be_in_closed_enum():
    with pytest.raises(ValueError, match="Article 6"):
        GdprContext(
            data_controller="X",
            legal_basis="hand_wave",  # not in the closed enum
            processing_purpose="X",
        )


def test_gdpr_all_six_legal_bases_accepted():
    for basis in [
        "consent",
        "contract",
        "legal_obligation",
        "vital_interests",
        "public_task",
        "legitimate_interests",
    ]:
        ctx = GdprContext(
            data_controller="X",
            legal_basis=basis,
            processing_purpose="Y",
        )
        assert ctx.to_metadata()["legal_basis"] == basis


# ── Soc2Context ────────────────────────────────────────────────


def test_soc2_minimal_context_to_metadata():
    ctx = Soc2Context(
        service_organization="Acme SaaS",
        trust_service_criteria=["security", "availability"],
        data_classification="tenant_data",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "SOC 2"
    assert meta["nanorix:framework_version"] == "TSC-2017"
    assert meta["service_organization"] == "Acme SaaS"
    # Comma-joined per G4 adjudication
    assert meta["trust_service_criteria"] == "security,availability"
    assert meta["data_classification"] == "tenant_data"


def test_soc2_all_five_trust_criteria_accepted():
    ctx = Soc2Context(
        service_organization="X",
        trust_service_criteria=[
            "security",
            "availability",
            "processing_integrity",
            "confidentiality",
            "privacy",
        ],
        data_classification="X",
    )
    joined = ctx.to_metadata()["trust_service_criteria"]
    assert joined == (
        "security,availability,processing_integrity,confidentiality,privacy"
    )


def test_soc2_invalid_trust_criterion_rejected():
    with pytest.raises(ValueError, match="trust_service_criteria"):
        Soc2Context(
            service_organization="X",
            trust_service_criteria=["security", "compliance"],  # not in set
            data_classification="X",
        )


def test_soc2_empty_trust_service_criteria_rejected():
    with pytest.raises(ValueError, match="trust_service_criteria"):
        Soc2Context(
            service_organization="X",
            trust_service_criteria=[],
            data_classification="X",
        )


# ── Capsules.create() metadata plumbing ────────────────────────

TEST_CAPSULE_RESPONSE = {
    "id": "cap_test001",
    "status": "active",
    "capsulefile_id": "cf_abc",
    "max_lifetime_seconds": 300,
    "created_at": "2026-05-03T00:00:00Z",
}


def test_create_capsule_threads_metadata_to_request_body(mock_api, client):
    route = mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=TEST_CAPSULE_RESPONSE)
    )

    ctx = HipaaContext(
        covered_entity="Acme Hospital",
        minimum_necessary_purpose="Radiology",
    )
    client.capsules.create(capsulefile="cf_abc", metadata=ctx.to_metadata())

    body = json.loads(route.calls[0].request.content)
    assert "metadata" in body
    assert body["metadata"]["nanorix:framework"] == "HIPAA"
    assert body["metadata"]["covered_entity"] == "Acme Hospital"


def test_create_capsule_omits_metadata_when_none(mock_api, client):
    route = mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=TEST_CAPSULE_RESPONSE)
    )

    client.capsules.create(capsulefile="cf_abc")
    body = json.loads(route.calls[0].request.content)
    assert "metadata" not in body
