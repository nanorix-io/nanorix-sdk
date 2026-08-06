"""Tests for nanorix.compliance.TexasSB14Context."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import TexasSB14Context


# ── Required fields + framework identity ───────────────────────


def test_texas_sb_14_minimal_context_emits_required_keys():
    ctx = TexasSB14Context(
        deployer_organization="Acme Texas Hospital",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "TX_SB_14"
    assert meta["nanorix:framework_version"] == "2025-enacted"
    assert meta["deployer_organization"] == "Acme Texas Hospital"
    assert meta["deployer_role"] == "deployer"
    assert (
        meta["high_risk_use_case"]
        == "healthcare_clinical_decision_support"
    )
    assert meta["impact_assessment_completed"] == "true"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "TX"
    # Optional fields absent in minimal construction.
    assert "affected_categories" not in meta
    assert "impact_assessment_reference" not in meta
    assert "notice_to_consumers_reference" not in meta
    assert "bias_audit_reference" not in meta
    assert "workforce_member_role" not in meta


def test_texas_sb_14_framework_id_property_returns_canonical_identifier():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="developer",
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    assert ctx.framework_id == "TX_SB_14"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_texas_sb_14_jurisdiction_state_property_returns_TX():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="developer",
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    assert ctx.jurisdiction_state == "TX"
    assert ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]


def test_texas_sb_14_jurisdiction_state_always_in_metadata():
    """Texas SB 14 is by construction a Texas-jurisdictional helper.
    The structural jurisdiction_state="TX" key must always emit."""
    for use_case in [
        "healthcare_clinical_decision_support",
        "not_high_risk",
        "other_high_risk",
    ]:
        ctx = TexasSB14Context(
            deployer_organization="X",
            deployer_role="deployer",
            high_risk_use_case=use_case,
            impact_assessment_completed=True,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "TX"


def test_texas_sb_14_high_risk_use_case_set_property_returns_taxonomy():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="developer",
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    use_cases = ctx.high_risk_use_case_set
    # Healthcare-adjacent use cases present.
    assert "healthcare_clinical_decision_support" in use_cases
    assert "healthcare_treatment_recommendation" in use_cases
    assert "healthcare_benefits_determination" in use_cases
    # Cross-vertical use cases present.
    assert "employment_decision" in use_cases
    assert "education_decision" in use_cases
    # Escape hatches present.
    assert "not_high_risk" in use_cases
    assert "other_high_risk" in use_cases


# ── Deployer role validation ───────────────────────────────────


@pytest.mark.parametrize(
    "role", ["developer", "deployer", "distributor"]
)
def test_texas_sb_14_all_three_deployer_roles_accepted(role):
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role=role,
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    assert ctx.to_metadata()["deployer_role"] == role


def test_texas_sb_14_invalid_deployer_role_rejected():
    with pytest.raises(ValueError, match="deployer_role"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="operator",  # not TRAIGA-aligned
            high_risk_use_case="not_high_risk",
            impact_assessment_completed=False,
        )


# ── High-risk use case validation ──────────────────────────────


@pytest.mark.parametrize(
    "use_case",
    [
        "healthcare_clinical_decision_support",
        "healthcare_treatment_recommendation",
        "healthcare_benefits_determination",
        "employment_decision",
        "education_decision",
        "financial_services_decision",
        "housing_decision",
        "government_services_decision",
        "criminal_justice_decision",
        "insurance_decision",
        "not_high_risk",
        "other_high_risk",
    ],
)
def test_texas_sb_14_all_canonical_use_cases_accepted(use_case):
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="deployer",
        high_risk_use_case=use_case,
        impact_assessment_completed=True,
    )
    assert ctx.to_metadata()["high_risk_use_case"] == use_case


def test_texas_sb_14_invalid_high_risk_use_case_rejected():
    with pytest.raises(ValueError, match="high_risk_use_case"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="deployer",
            high_risk_use_case="entertainment_decision",  # not canonical
            impact_assessment_completed=False,
        )


# ── Impact assessment flag ─────────────────────────────────────


def test_texas_sb_14_impact_assessment_true_emits_string_true():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
    )
    assert ctx.to_metadata()["impact_assessment_completed"] == "true"


def test_texas_sb_14_impact_assessment_false_emits_string_false():
    """Customer may declare impact assessment incomplete (e.g.,
    workload deployed pre-assessment with assessment scheduled).
    The structural flag still emits 'false'."""
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=False,
    )
    assert ctx.to_metadata()["impact_assessment_completed"] == "false"


def test_texas_sb_14_impact_assessment_non_bool_rejected():
    with pytest.raises(ValueError, match="impact_assessment_completed"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="deployer",
            high_risk_use_case="not_high_risk",
            impact_assessment_completed="true",  # type: ignore[arg-type]
        )


# ── Empty required-field rejection ─────────────────────────────


def test_texas_sb_14_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="deployer_organization"):
        TexasSB14Context(
            deployer_organization="",
            deployer_role="deployer",
            high_risk_use_case="not_high_risk",
            impact_assessment_completed=False,
        )
    with pytest.raises(ValueError, match="deployer_role"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="",
            high_risk_use_case="not_high_risk",
            impact_assessment_completed=False,
        )
    with pytest.raises(ValueError, match="high_risk_use_case"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="deployer",
            high_risk_use_case="",
            impact_assessment_completed=False,
        )


# ── Affected categories ────────────────────────────────────────


def test_texas_sb_14_affected_categories_optional():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="deployer",
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    assert "affected_categories" not in ctx.to_metadata()


def test_texas_sb_14_affected_categories_comma_joined_when_present():
    ctx = TexasSB14Context(
        deployer_organization="X",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
        affected_categories=[
            "texas_residents",
            "texas_medicaid_beneficiaries",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["affected_categories"] == (
        "texas_residents,texas_medicaid_beneficiaries"
    )


def test_texas_sb_14_affected_categories_comma_in_item_rejected():
    with pytest.raises(ValueError, match="affected_categories"):
        TexasSB14Context(
            deployer_organization="X",
            deployer_role="deployer",
            high_risk_use_case="not_high_risk",
            impact_assessment_completed=False,
            affected_categories=[
                "texas_residents,under_18",  # comma in item
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_texas_sb_14_optional_fields_when_set_appear_in_metadata():
    ctx = TexasSB14Context(
        deployer_organization="Acme Texas Hospital",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
        affected_categories=["texas_residents"],
        impact_assessment_reference="AIA-2026-Q2-TX-001",
        notice_to_consumers_reference="notice-2026-clinical-ai",
        bias_audit_reference="bias-audit-2026-Q1",
        workforce_member_role="attending_physician",
    )
    meta = ctx.to_metadata()
    assert meta["impact_assessment_reference"] == "AIA-2026-Q2-TX-001"
    assert (
        meta["notice_to_consumers_reference"]
        == "notice-2026-clinical-ai"
    )
    assert meta["bias_audit_reference"] == "bias-audit-2026-Q1"
    assert meta["workforce_member_role"] == "attending_physician"


# ── Wire-constraint enforcement ────────────────────────────────


def test_texas_sb_14_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        TexasSB14Context(
            deployer_organization="A" * 257,
            deployer_role="deployer",
            high_risk_use_case="not_high_risk",
            impact_assessment_completed=False,
        )


def test_texas_sb_14_frozen_dataclass_mutation_raises():
    ctx = TexasSB14Context(
        deployer_organization="A",
        deployer_role="deployer",
        high_risk_use_case="not_high_risk",
        impact_assessment_completed=False,
    )
    with pytest.raises(Exception):
        ctx.deployer_organization = "B"  # type: ignore[misc]


def test_texas_sb_14_metadata_json_round_trip_preserves_dict():
    ctx = TexasSB14Context(
        deployer_organization="Acme",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
        affected_categories=["texas_residents"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── HIPAA + Texas SB 14 paired workflow (Texas healthcare AI) ──


def test_texas_sb_14_paired_with_hipaa_context_merge_works():
    """Texas-jurisdictional healthcare AI cohort: workload falls under
    both HIPAA (vertical) and Texas SB 14 (jurisdictional). Customer
    wraps with both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Texas Hospital",
        minimum_necessary_purpose="Clinical decision support",
    )
    tx = TexasSB14Context(
        deployer_organization="Acme Texas Hospital",
        deployer_role="deployer",
        high_risk_use_case="healthcare_clinical_decision_support",
        impact_assessment_completed=True,
    )
    merged = {**hipaa.to_metadata(), **tx.to_metadata()}
    # Texas SB 14 wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "TX_SB_14"
    # Both frameworks' substantive fields present.
    assert merged["covered_entity"] == "Acme Texas Hospital"
    assert merged["deployer_organization"] == "Acme Texas Hospital"
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "TX"


# ── Three-role + factual-language discipline ───────────────────


def test_texas_sb_14_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import texas_sb_14 as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # Texas-specific enforcement / counsel disclaimer language.
    assert "regulatory counsel" in doc.lower() or (
        "Attorney General" in doc
    )


def test_texas_sb_14_class_docstring_contains_disclaimer_language():
    doc = TexasSB14Context.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_texas_sb_14_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import texas_sb_14 as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
