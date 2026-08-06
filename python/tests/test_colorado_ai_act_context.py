"""Tests for nanorix.compliance.ColoradoAIActContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import ColoradoAIActContext


# ── Required fields + framework identity ───────────────────────


def test_colorado_ai_act_minimal_context_emits_required_keys():
    ctx = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "CO_SB24_205"
    assert meta["nanorix:framework_version"] == "2026-02"
    assert meta["organization"] == "Acme Colorado Hospital"
    assert meta["actor_role"] == "deployer"
    assert meta["high_risk_ai_system"] == "true"
    assert meta["impact_assessment_completed"] == "true"
    assert meta["consumer_notice_provided"] == "true"
    assert meta["consequential_decision_category"] == "healthcare"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "CO"
    # Optional fields absent in minimal construction.
    assert "consumer_notice_timing" not in meta
    assert "affected_consumer_categories" not in meta
    assert "impact_assessment_reference" not in meta
    assert "risk_management_policy_reference" not in meta
    assert "developer_disclosure_reference" not in meta
    assert "adverse_decision_explanation_reference" not in meta
    assert "appeal_workflow_reference" not in meta
    assert "workforce_member_role" not in meta


def test_colorado_ai_act_framework_id_property_returns_canonical_identifier():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="developer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    assert ctx.framework_id == "CO_SB24_205"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_colorado_ai_act_jurisdiction_state_property_returns_CO():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="developer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    assert ctx.jurisdiction_state == "CO"
    assert ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]


def test_colorado_ai_act_jurisdiction_state_always_in_metadata():
    """Colorado AI Act is by construction a Colorado-jurisdictional
    helper. The structural jurisdiction_state="CO" key must always
    emit."""
    for category in [
        "healthcare",
        "employment",
        "financial_or_lending",
        "not_consequential_decision",
    ]:
        ctx = ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=category != "not_consequential_decision",
            impact_assessment_completed=True,
            consumer_notice_provided=True,
            consequential_decision_category=category,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "CO"


def test_colorado_ai_act_consequential_decision_category_set_property_returns_taxonomy():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    categories = ctx.consequential_decision_category_set
    # § 6-1-1701(3) enumerated categories.
    assert "education" in categories
    assert "employment" in categories
    assert "financial_or_lending" in categories
    assert "essential_government_service" in categories
    assert "healthcare" in categories
    assert "housing" in categories
    assert "insurance" in categories
    assert "legal_service" in categories
    # Escape hatches.
    assert "not_consequential_decision" in categories
    assert "other" in categories


def test_colorado_ai_act_consumer_notice_timing_set_property_returns_taxonomy():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    timings = ctx.consumer_notice_timing_set
    assert "before_consequential_decision" in timings
    assert "concurrent_with_decision" in timings
    assert "post_decision_adverse_explanation" in timings
    assert "no_notice_provided" in timings
    assert "not_required" in timings
    assert "other" in timings


def test_colorado_ai_act_actor_role_set_property_returns_taxonomy():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    roles = ctx.actor_role_set
    assert "developer" in roles
    assert "deployer" in roles
    assert "developer_and_deployer" in roles


# ── Actor role validation ──────────────────────────────────────


@pytest.mark.parametrize(
    "role", ["developer", "deployer", "developer_and_deployer"]
)
def test_colorado_ai_act_all_actor_roles_accepted(role):
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role=role,
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    assert ctx.to_metadata()["actor_role"] == role


def test_colorado_ai_act_invalid_actor_role_rejected():
    with pytest.raises(ValueError, match="actor_role"):
        ColoradoAIActContext(
            organization="X",
            actor_role="distributor",  # not Colorado-AI-Act-aligned
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="not_consequential_decision",
        )


# ── Consequential decision category validation ─────────────────


@pytest.mark.parametrize(
    "category",
    [
        "education",
        "employment",
        "financial_or_lending",
        "essential_government_service",
        "healthcare",
        "housing",
        "insurance",
        "legal_service",
        "not_consequential_decision",
        "other",
    ],
)
def test_colorado_ai_act_all_categories_accepted(category):
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=category != "not_consequential_decision",
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category=category,
    )
    assert ctx.to_metadata()["consequential_decision_category"] == category


def test_colorado_ai_act_invalid_category_rejected():
    with pytest.raises(
        ValueError, match="consequential_decision_category"
    ):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="entertainment",  # not §6-1-1701(3)
        )


# ── Required-boolean enforcement ───────────────────────────────


def test_colorado_ai_act_required_booleans_emit_string_true_false():
    """Colorado AI Act has three load-bearing booleans tied directly
    to §§ 6-1-1702 + 6-1-1703 statutory obligations: high-risk-AI-
    System scope, impact-assessment completion, and consumer-notice
    provision. All must emit as lowercase strings."""
    ctx_all_true = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    meta_true = ctx_all_true.to_metadata()
    assert meta_true["high_risk_ai_system"] == "true"
    assert meta_true["impact_assessment_completed"] == "true"
    assert meta_true["consumer_notice_provided"] == "true"

    ctx_all_false = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    meta_false = ctx_all_false.to_metadata()
    assert meta_false["high_risk_ai_system"] == "false"
    assert meta_false["impact_assessment_completed"] == "false"
    assert meta_false["consumer_notice_provided"] == "false"


def test_colorado_ai_act_high_risk_ai_system_non_bool_rejected():
    with pytest.raises(ValueError, match="high_risk_ai_system"):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system="true",  # type: ignore[arg-type]
            impact_assessment_completed=True,
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
        )


def test_colorado_ai_act_impact_assessment_completed_non_bool_rejected():
    with pytest.raises(
        ValueError, match="impact_assessment_completed"
    ):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=True,
            impact_assessment_completed="true",  # type: ignore[arg-type]
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
        )


def test_colorado_ai_act_consumer_notice_provided_non_bool_rejected():
    with pytest.raises(ValueError, match="consumer_notice_provided"):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=True,
            impact_assessment_completed=True,
            consumer_notice_provided="true",  # type: ignore[arg-type]
            consequential_decision_category="healthcare",
        )


def test_colorado_ai_act_required_booleans_enforce_bool_shape_not_truthy():
    """Colorado-AI-Act-specific test: high-risk + impact-assessment +
    consumer-notice fields enforce required boolean shape per
    §§ 6-1-1702 / 6-1-1703 discipline. Customers must NOT pass truthy
    non-bool values."""
    with pytest.raises(ValueError, match="high_risk_ai_system"):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=1,  # type: ignore[arg-type]
            impact_assessment_completed=True,
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
        )
    with pytest.raises(
        ValueError, match="impact_assessment_completed"
    ):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=True,
            impact_assessment_completed=1,  # type: ignore[arg-type]
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
        )


# ── Empty required-field rejection ─────────────────────────────


def test_colorado_ai_act_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="organization"):
        ColoradoAIActContext(
            organization="",
            actor_role="deployer",
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="not_consequential_decision",
        )
    with pytest.raises(ValueError, match="actor_role"):
        ColoradoAIActContext(
            organization="X",
            actor_role="",
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="not_consequential_decision",
        )
    with pytest.raises(
        ValueError, match="consequential_decision_category"
    ):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="",
        )


# ── Consumer notice timing (optional with enum validation) ─────


def test_colorado_ai_act_consumer_notice_timing_optional():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    assert "consumer_notice_timing" not in ctx.to_metadata()


@pytest.mark.parametrize(
    "timing",
    [
        "before_consequential_decision",
        "concurrent_with_decision",
        "post_decision_adverse_explanation",
        "no_notice_provided",
        "not_required",
        "other",
    ],
)
def test_colorado_ai_act_all_notice_timings_accepted(timing):
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
        consumer_notice_timing=timing,
    )
    assert ctx.to_metadata()["consumer_notice_timing"] == timing


def test_colorado_ai_act_invalid_notice_timing_rejected():
    with pytest.raises(ValueError, match="consumer_notice_timing"):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=True,
            impact_assessment_completed=True,
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
            consumer_notice_timing="delayed_by_one_year",  # not canonical
        )


# ── Affected consumer categories ───────────────────────────────


def test_colorado_ai_act_affected_consumer_categories_optional():
    ctx = ColoradoAIActContext(
        organization="X",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    assert "affected_consumer_categories" not in ctx.to_metadata()


def test_colorado_ai_act_affected_consumer_categories_comma_joined_when_present():
    ctx = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
        affected_consumer_categories=[
            "colorado_residents",
            "colorado_medicaid_beneficiaries",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["affected_consumer_categories"] == (
        "colorado_residents,colorado_medicaid_beneficiaries"
    )


def test_colorado_ai_act_affected_consumer_categories_comma_in_item_rejected():
    with pytest.raises(
        ValueError, match="affected_consumer_categories"
    ):
        ColoradoAIActContext(
            organization="X",
            actor_role="deployer",
            high_risk_ai_system=True,
            impact_assessment_completed=True,
            consumer_notice_provided=True,
            consequential_decision_category="healthcare",
            affected_consumer_categories=[
                "colorado_residents,under_18",  # comma in item
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_colorado_ai_act_optional_fields_when_set_appear_in_metadata():
    ctx = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
        consumer_notice_timing="before_consequential_decision",
        affected_consumer_categories=["colorado_residents"],
        impact_assessment_reference="IA-2026-Q1-CO-001",
        risk_management_policy_reference="rmp-2026-v2",
        adverse_decision_explanation_reference="adv-expl-2026",
        appeal_workflow_reference="appeal-flow-2026",
        workforce_member_role="attending_physician",
    )
    meta = ctx.to_metadata()
    assert meta["consumer_notice_timing"] == "before_consequential_decision"
    assert meta["impact_assessment_reference"] == "IA-2026-Q1-CO-001"
    assert meta["risk_management_policy_reference"] == "rmp-2026-v2"
    assert (
        meta["adverse_decision_explanation_reference"] == "adv-expl-2026"
    )
    assert meta["appeal_workflow_reference"] == "appeal-flow-2026"
    assert meta["workforce_member_role"] == "attending_physician"


def test_colorado_ai_act_developer_disclosure_reference_emits_when_developer_role():
    """Developer disclosure reference is applicable when actor_role
    includes 'developer'. Helper records it as an optional field."""
    ctx = ColoradoAIActContext(
        organization="Acme Colorado AI Co",
        actor_role="developer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=False,  # developer typically does not
        consequential_decision_category="healthcare",
        developer_disclosure_reference="dev-disclosure-v3-2026",
    )
    meta = ctx.to_metadata()
    assert (
        meta["developer_disclosure_reference"]
        == "dev-disclosure-v3-2026"
    )


# ── Wire-constraint enforcement ────────────────────────────────


def test_colorado_ai_act_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        ColoradoAIActContext(
            organization="A" * 257,
            actor_role="deployer",
            high_risk_ai_system=False,
            impact_assessment_completed=False,
            consumer_notice_provided=False,
            consequential_decision_category="not_consequential_decision",
        )


def test_colorado_ai_act_frozen_dataclass_mutation_raises():
    ctx = ColoradoAIActContext(
        organization="A",
        actor_role="deployer",
        high_risk_ai_system=False,
        impact_assessment_completed=False,
        consumer_notice_provided=False,
        consequential_decision_category="not_consequential_decision",
    )
    with pytest.raises(Exception):
        ctx.organization = "B"  # type: ignore[misc]


def test_colorado_ai_act_metadata_json_round_trip_preserves_dict():
    ctx = ColoradoAIActContext(
        organization="Acme",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
        affected_consumer_categories=["colorado_residents"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Three-role + factual-language discipline ───────────────────


def test_colorado_ai_act_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer framing
    ."""
    from nanorix.compliance import colorado_ai_act as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    assert (
        "regulatory counsel" in doc.lower()
        or "Attorney General" in doc
    )


def test_colorado_ai_act_class_docstring_contains_disclaimer_language():
    doc = ColoradoAIActContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_colorado_ai_act_module_docstring_surfaces_consequential_decision_scope():
    """Colorado-AI-Act-specific: § 6-1-1701(3) Consequential Decision
    scope is materially distinct from other state AI laws (eight
    enumerated categories with substantial-factor scope). The module
    docstring must surface this."""
    from nanorix.compliance import colorado_ai_act as mod

    doc = mod.__doc__ or ""
    assert "consequential decision" in doc.lower()
    assert "healthcare" in doc.lower()
    assert "substantial factor" in doc.lower()


def test_colorado_ai_act_module_docstring_surfaces_attorney_general_enforcement():
    """Colorado-AI-Act-specific: the Colorado Attorney General is the
    EXCLUSIVE enforcement authority under § 6-1-1706 (no private right
    of action, unlike BIPA). Materially distinct enforcement posture."""
    from nanorix.compliance import colorado_ai_act as mod

    doc = mod.__doc__ or ""
    assert "Attorney General" in doc
    assert "exclusive" in doc.lower() or "no private right" in doc.lower()


def test_colorado_ai_act_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import colorado_ai_act as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


# ── Paired-framework workflows ─────────────────────────────────


def test_colorado_ai_act_paired_with_hipaa_context_merge_works():
    """Colorado-jurisdictional healthcare AI cohort: workload falls
    under both HIPAA (vertical) and Colorado AI Act (state). Customer
    wraps with both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Colorado Hospital",
        minimum_necessary_purpose="Clinical decision support",
    )
    co = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    merged = {**hipaa.to_metadata(), **co.to_metadata()}
    # Colorado AI Act wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "CO_SB24_205"
    # Both frameworks' substantive fields present.
    assert merged["covered_entity"] == "Acme Colorado Hospital"
    assert merged["organization"] == "Acme Colorado Hospital"
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "CO"
    # Load-bearing booleans preserved.
    assert merged["high_risk_ai_system"] == "true"
    assert merged["impact_assessment_completed"] == "true"


def test_colorado_ai_act_paired_with_nist_ai_rmf_merge_works():
    """Colorado AI Act § 6-1-1703(2) risk-management-program
    requirement commonly aligns with NIST AI RMF's MAP/MEASURE/MANAGE
    functions. Customers commonly emit both context tags."""
    from nanorix.compliance import NISTAIRMFContext

    co = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Colorado Hospital",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure", "manage"],
    )
    merged = {**co.to_metadata(), **nist.to_metadata()}
    # NIST AI RMF wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "NIST_AI_RMF_1.0"
    # Both frameworks' substantive fields present.
    assert merged["organization"] == "Acme Colorado Hospital"
    assert merged["ai_system_owner"] == "Acme Colorado Hospital"
    # State-jurisdiction tag preserved (CO from Colorado AI Act).
    assert merged["jurisdiction_state"] == "CO"
