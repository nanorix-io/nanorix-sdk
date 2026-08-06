"""Tests for nanorix.compliance.CCPAContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import CCPAContext


# ── Required fields + framework identity ───────────────────────


def test_ccpa_minimal_context_emits_required_keys():
    ctx = CCPAContext(
        business_organization="Acme SaaS",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "CCPA"
    assert meta["nanorix:framework_version"] == "2020-operative"
    assert meta["business_organization"] == "Acme SaaS"
    assert meta["business_role"] == "business"
    assert meta["threshold_basis"] == "annual_revenue_25m"
    assert meta["consumer_data_in_scope"] == "true"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "CA"
    # Optional fields absent in minimal construction.
    assert "pi_categories" not in meta
    assert "purpose_of_processing" not in meta
    assert "privacy_notice_reference" not in meta
    assert "consumer_request_workflow_reference" not in meta
    assert "service_provider_contract_reference" not in meta
    assert "workforce_member_role" not in meta


def test_ccpa_framework_id_property_returns_canonical_identifier():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    assert ctx.framework_id == "CCPA"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_ccpa_jurisdiction_state_property_returns_CA():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    assert ctx.jurisdiction_state == "CA"
    assert ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]


def test_ccpa_jurisdiction_state_always_in_metadata():
    """CCPA is by construction a California-jurisdictional helper.
    The structural jurisdiction_state="CA" key must always emit."""
    for basis in [
        "annual_revenue_25m",
        "consumer_records_100k",
        "revenue_share_50_pct_from_selling_pi",
        "not_subject",
        "other",
    ]:
        ctx = CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis=basis,
            consumer_data_in_scope=True,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "CA"


def test_ccpa_threshold_basis_set_property_returns_taxonomy():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    bases = ctx.threshold_basis_set
    assert "annual_revenue_25m" in bases
    assert "consumer_records_100k" in bases
    assert "revenue_share_50_pct_from_selling_pi" in bases
    assert "not_subject" in bases
    assert "other" in bases


def test_ccpa_pi_category_set_property_returns_taxonomy():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    cats = ctx.pi_category_set
    # §1798.140(v)(1)(A)-(K) categories present.
    assert "identifiers" in cats
    assert "biometric_information" in cats
    assert "geolocation_data" in cats
    assert "inferences" in cats
    # Escape hatches present.
    assert "no_pi_processed" in cats
    assert "other" in cats


# ── Business-role validation ───────────────────────────────────


@pytest.mark.parametrize(
    "role",
    ["business", "service_provider", "contractor", "third_party"],
)
def test_ccpa_all_four_business_roles_accepted(role):
    ctx = CCPAContext(
        business_organization="X",
        business_role=role,
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    assert ctx.to_metadata()["business_role"] == role


def test_ccpa_invalid_business_role_rejected():
    with pytest.raises(ValueError, match="business_role"):
        CCPAContext(
            business_organization="X",
            business_role="processor",  # not CCPA-aligned
            threshold_basis="not_subject",
            consumer_data_in_scope=False,
        )


# ── Threshold-basis validation ─────────────────────────────────


@pytest.mark.parametrize(
    "basis",
    [
        "annual_revenue_25m",
        "consumer_records_100k",
        "revenue_share_50_pct_from_selling_pi",
        "not_subject",
        "other",
    ],
)
def test_ccpa_all_threshold_bases_accepted(basis):
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis=basis,
        consumer_data_in_scope=True,
    )
    assert ctx.to_metadata()["threshold_basis"] == basis


def test_ccpa_invalid_threshold_basis_rejected():
    with pytest.raises(ValueError, match="threshold_basis"):
        CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis="revenue_10m",  # not canonical
            consumer_data_in_scope=False,
        )


# ── Consumer-data-in-scope flag ────────────────────────────────


def test_ccpa_consumer_data_true_emits_string_true():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
    )
    assert ctx.to_metadata()["consumer_data_in_scope"] == "true"


def test_ccpa_consumer_data_false_emits_string_false():
    """Customer may declare workload does not process California-
    resident PI; structural flag still emits 'false'."""
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    assert ctx.to_metadata()["consumer_data_in_scope"] == "false"


def test_ccpa_consumer_data_in_scope_non_bool_rejected():
    with pytest.raises(ValueError, match="consumer_data_in_scope"):
        CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis="not_subject",
            consumer_data_in_scope="true",  # type: ignore[arg-type]
        )


# ── Empty required-field rejection ─────────────────────────────


def test_ccpa_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="business_organization"):
        CCPAContext(
            business_organization="",
            business_role="business",
            threshold_basis="not_subject",
            consumer_data_in_scope=False,
        )
    with pytest.raises(ValueError, match="business_role"):
        CCPAContext(
            business_organization="X",
            business_role="",
            threshold_basis="not_subject",
            consumer_data_in_scope=False,
        )
    with pytest.raises(ValueError, match="threshold_basis"):
        CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis="",
            consumer_data_in_scope=False,
        )


# ── PI categories ──────────────────────────────────────────────


def test_ccpa_pi_categories_optional():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    assert "pi_categories" not in ctx.to_metadata()


def test_ccpa_pi_categories_comma_joined_when_present():
    ctx = CCPAContext(
        business_organization="X",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
        pi_categories=["identifiers", "geolocation_data"],
    )
    meta = ctx.to_metadata()
    assert meta["pi_categories"] == "identifiers,geolocation_data"


def test_ccpa_pi_categories_invalid_value_rejected():
    with pytest.raises(ValueError, match="pi_categories"):
        CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis="annual_revenue_25m",
            consumer_data_in_scope=True,
            pi_categories=["identifiers", "social_security_numbers"],
        )


def test_ccpa_pi_categories_comma_in_item_rejected():
    with pytest.raises(ValueError, match="pi_categories"):
        CCPAContext(
            business_organization="X",
            business_role="business",
            threshold_basis="annual_revenue_25m",
            consumer_data_in_scope=True,
            pi_categories=["identifiers,geolocation"],
        )


# ── Optional fields ────────────────────────────────────────────


def test_ccpa_optional_fields_when_set_appear_in_metadata():
    ctx = CCPAContext(
        business_organization="Acme SaaS",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
        pi_categories=["identifiers"],
        purpose_of_processing="service_delivery",
        privacy_notice_reference="privacy-notice-2026-v3",
        consumer_request_workflow_reference="dsar-workflow-2026",
        service_provider_contract_reference="sp-contract-2026-001",
        workforce_member_role="consumer_rights_handler",
    )
    meta = ctx.to_metadata()
    assert meta["purpose_of_processing"] == "service_delivery"
    assert meta["privacy_notice_reference"] == "privacy-notice-2026-v3"
    assert (
        meta["consumer_request_workflow_reference"]
        == "dsar-workflow-2026"
    )
    assert (
        meta["service_provider_contract_reference"]
        == "sp-contract-2026-001"
    )
    assert meta["workforce_member_role"] == "consumer_rights_handler"


# ── Wire-constraint enforcement ────────────────────────────────


def test_ccpa_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        CCPAContext(
            business_organization="A" * 257,
            business_role="business",
            threshold_basis="not_subject",
            consumer_data_in_scope=False,
        )


def test_ccpa_frozen_dataclass_mutation_raises():
    ctx = CCPAContext(
        business_organization="A",
        business_role="business",
        threshold_basis="not_subject",
        consumer_data_in_scope=False,
    )
    with pytest.raises(Exception):
        ctx.business_organization = "B"  # type: ignore[misc]


def test_ccpa_metadata_json_round_trip_preserves_dict():
    ctx = CCPAContext(
        business_organization="Acme",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
        pi_categories=["identifiers"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── CCPA + HIPAA paired workflow (California healthcare AI) ────


def test_ccpa_paired_with_hipaa_context_merge_works():
    """California-jurisdictional healthcare AI cohort: workload falls
    under both HIPAA (vertical) and CCPA (state-jurisdictional).
    Customer wraps with both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme California Hospital",
        minimum_necessary_purpose="Clinical decision support",
    )
    ccpa = CCPAContext(
        business_organization="Acme California Hospital",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
    )
    merged = {**hipaa.to_metadata(), **ccpa.to_metadata()}
    # CCPA wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "CCPA"
    # Both frameworks' substantive fields present.
    assert merged["covered_entity"] == "Acme California Hospital"
    assert merged["business_organization"] == "Acme California Hospital"
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "CA"


# ── Three-role + factual-language discipline ───────────────────


def test_ccpa_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing."""
    from nanorix.compliance import ccpa as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # California-specific enforcement / counsel disclaimer language.
    assert (
        "regulatory counsel" in doc.lower()
        or "California Privacy Protection Agency" in doc
        or "California Attorney General" in doc
    )


def test_ccpa_class_docstring_contains_disclaimer_language():
    doc = CCPAContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_ccpa_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import ccpa as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
