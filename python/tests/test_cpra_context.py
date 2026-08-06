"""Tests for nanorix.compliance.CPRAContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import CPRAContext


# ── Required fields + framework identity ───────────────────────


def test_cpra_minimal_context_emits_required_keys():
    ctx = CPRAContext(
        business_organization="Acme SaaS",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="does_not_use_admt",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "CPRA"
    assert meta["nanorix:framework_version"] == "2023-operative"
    assert meta["business_organization"] == "Acme SaaS"
    assert meta["business_role"] == "business"
    assert meta["sensitive_pi_in_scope"] == "true"
    assert meta["admt_designation"] == "does_not_use_admt"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "CA"
    # Optional fields absent in minimal construction.
    assert "spi_categories" not in meta
    assert "retention_period" not in meta
    assert "purpose_of_processing" not in meta
    assert "privacy_notice_reference" not in meta
    assert "right_to_correct_workflow_reference" not in meta
    assert "right_to_limit_spi_workflow_reference" not in meta
    assert "admt_opt_out_workflow_reference" not in meta
    assert "workforce_member_role" not in meta


def test_cpra_framework_id_property_returns_canonical_identifier():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    assert ctx.framework_id == "CPRA"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_cpra_jurisdiction_state_property_returns_CA():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    assert ctx.jurisdiction_state == "CA"
    assert (
        ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]
    )


def test_cpra_jurisdiction_state_always_in_metadata():
    """CPRA is by construction a California-jurisdictional helper.
    The structural jurisdiction_state="CA" key must always emit."""
    for admt in [
        "uses_admt",
        "uses_admt_consequential_only",
        "does_not_use_admt",
        "admt_determination_pending",
    ]:
        ctx = CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope=True,
            admt_designation=admt,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "CA"


def test_cpra_spi_category_set_property_returns_taxonomy():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    cats = ctx.spi_category_set
    # CPRA-introduced sensitive categories present.
    assert "ssn" in cats
    assert "precise_geolocation" in cats
    assert "genetic_data" in cats
    assert "biometric_for_identification" in cats
    assert "health" in cats
    assert "sex_life_or_sexual_orientation" in cats
    # Escape hatches present.
    assert "no_spi_processed" in cats
    assert "other" in cats


def test_cpra_admt_designation_set_property_returns_taxonomy():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    designations = ctx.admt_designation_set
    assert "uses_admt" in designations
    assert "uses_admt_consequential_only" in designations
    assert "does_not_use_admt" in designations
    assert "admt_determination_pending" in designations


# ── Business-role validation ───────────────────────────────────


@pytest.mark.parametrize(
    "role",
    ["business", "service_provider", "contractor", "third_party"],
)
def test_cpra_all_four_business_roles_accepted(role):
    ctx = CPRAContext(
        business_organization="X",
        business_role=role,
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    assert ctx.to_metadata()["business_role"] == role


def test_cpra_invalid_business_role_rejected():
    with pytest.raises(ValueError, match="business_role"):
        CPRAContext(
            business_organization="X",
            business_role="data_subject",  # not CPRA-aligned
            sensitive_pi_in_scope=False,
            admt_designation="does_not_use_admt",
        )


# ── ADMT-designation validation ────────────────────────────────


@pytest.mark.parametrize(
    "admt",
    [
        "uses_admt",
        "uses_admt_consequential_only",
        "does_not_use_admt",
        "admt_determination_pending",
    ],
)
def test_cpra_all_admt_designations_accepted(admt):
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation=admt,
    )
    assert ctx.to_metadata()["admt_designation"] == admt


def test_cpra_invalid_admt_designation_rejected():
    with pytest.raises(ValueError, match="admt_designation"):
        CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope=False,
            admt_designation="manual_decisions_only",  # not canonical
        )


# ── Sensitive-PI-in-scope flag ─────────────────────────────────


def test_cpra_sensitive_pi_true_emits_string_true():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="does_not_use_admt",
    )
    assert ctx.to_metadata()["sensitive_pi_in_scope"] == "true"


def test_cpra_sensitive_pi_false_emits_string_false():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    assert ctx.to_metadata()["sensitive_pi_in_scope"] == "false"


def test_cpra_sensitive_pi_in_scope_non_bool_rejected():
    with pytest.raises(ValueError, match="sensitive_pi_in_scope"):
        CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope="true",  # type: ignore[arg-type]
            admt_designation="does_not_use_admt",
        )


# ── Empty required-field rejection ─────────────────────────────


def test_cpra_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="business_organization"):
        CPRAContext(
            business_organization="",
            business_role="business",
            sensitive_pi_in_scope=False,
            admt_designation="does_not_use_admt",
        )
    with pytest.raises(ValueError, match="business_role"):
        CPRAContext(
            business_organization="X",
            business_role="",
            sensitive_pi_in_scope=False,
            admt_designation="does_not_use_admt",
        )
    with pytest.raises(ValueError, match="admt_designation"):
        CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope=False,
            admt_designation="",
        )


# ── SPI categories ─────────────────────────────────────────────


def test_cpra_spi_categories_optional():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    assert "spi_categories" not in ctx.to_metadata()


def test_cpra_spi_categories_comma_joined_when_present():
    ctx = CPRAContext(
        business_organization="X",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="uses_admt_consequential_only",
        spi_categories=["ssn", "health", "precise_geolocation"],
    )
    meta = ctx.to_metadata()
    assert meta["spi_categories"] == "ssn,health,precise_geolocation"


def test_cpra_spi_categories_invalid_value_rejected():
    with pytest.raises(ValueError, match="spi_categories"):
        CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope=True,
            admt_designation="does_not_use_admt",
            spi_categories=["ssn", "credit_score"],  # not canonical
        )


def test_cpra_spi_categories_comma_in_item_rejected():
    with pytest.raises(ValueError, match="spi_categories"):
        CPRAContext(
            business_organization="X",
            business_role="business",
            sensitive_pi_in_scope=True,
            admt_designation="does_not_use_admt",
            spi_categories=["ssn,passport_number"],
        )


# ── Optional fields ────────────────────────────────────────────


def test_cpra_optional_fields_when_set_appear_in_metadata():
    ctx = CPRAContext(
        business_organization="Acme SaaS",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="uses_admt_consequential_only",
        spi_categories=["ssn"],
        retention_period="session_only",
        purpose_of_processing="fraud_prevention",
        privacy_notice_reference="privacy-notice-cpra-2026",
        right_to_correct_workflow_reference="correct-2026",
        right_to_limit_spi_workflow_reference="limit-spi-2026",
        admt_opt_out_workflow_reference="admt-optout-2026",
        workforce_member_role="ml_engineer",
    )
    meta = ctx.to_metadata()
    assert meta["retention_period"] == "session_only"
    assert meta["purpose_of_processing"] == "fraud_prevention"
    assert meta["privacy_notice_reference"] == "privacy-notice-cpra-2026"
    assert meta["right_to_correct_workflow_reference"] == "correct-2026"
    assert (
        meta["right_to_limit_spi_workflow_reference"]
        == "limit-spi-2026"
    )
    assert meta["admt_opt_out_workflow_reference"] == "admt-optout-2026"
    assert meta["workforce_member_role"] == "ml_engineer"


# ── Wire-constraint enforcement ────────────────────────────────


def test_cpra_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        CPRAContext(
            business_organization="A" * 257,
            business_role="business",
            sensitive_pi_in_scope=False,
            admt_designation="does_not_use_admt",
        )


def test_cpra_frozen_dataclass_mutation_raises():
    ctx = CPRAContext(
        business_organization="A",
        business_role="business",
        sensitive_pi_in_scope=False,
        admt_designation="does_not_use_admt",
    )
    with pytest.raises(Exception):
        ctx.business_organization = "B"  # type: ignore[misc]


def test_cpra_metadata_json_round_trip_preserves_dict():
    ctx = CPRAContext(
        business_organization="Acme",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="uses_admt_consequential_only",
        spi_categories=["ssn"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── CCPA + CPRA paired workflow (most California cohorts) ──────


def test_cpra_paired_with_ccpa_context_merge_works():
    """California post-2023 cohort: workload commonly falls under both
    CCPA (base statute) and CPRA (expansion). Customer wraps with both
    and merges."""
    from nanorix.compliance import CCPAContext

    ccpa = CCPAContext(
        business_organization="Acme SaaS",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
    )
    cpra = CPRAContext(
        business_organization="Acme SaaS",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="uses_admt_consequential_only",
    )
    merged = {**ccpa.to_metadata(), **cpra.to_metadata()}
    # CPRA wins by default (right-hand spread); both substantive
    # field sets are preserved (overlap on shared keys but distinct
    # SPI / ADMT / threshold fields).
    assert merged["nanorix:framework"] == "CPRA"
    assert merged["business_organization"] == "Acme SaaS"
    assert merged["sensitive_pi_in_scope"] == "true"
    assert merged["admt_designation"] == "uses_admt_consequential_only"
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "CA"


# ── Three-role + factual-language discipline ───────────────────


def test_cpra_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import cpra as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # California-specific enforcement / counsel disclaimer language.
    assert (
        "regulatory counsel" in doc.lower()
        or "California Privacy Protection Agency" in doc
        or "California Attorney General" in doc
    )


def test_cpra_class_docstring_contains_disclaimer_language():
    doc = CPRAContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_cpra_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import cpra as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
