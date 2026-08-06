"""Tests for nanorix.compliance.NYCLL144Context."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import NYCLL144Context


# ── Required fields + framework identity ───────────────────────


def test_nyc_ll144_minimal_context_emits_required_keys():
    ctx = NYCLL144Context(
        employer_organization="Acme NYC Hospital",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "NYC_LL_144"
    assert meta["nanorix:framework_version"] == "2026-02"
    assert meta["employer_organization"] == "Acme NYC Hospital"
    assert meta["employer_role"] == "employer"
    assert meta["aedt_in_use"] == "true"
    assert meta["bias_audit_completed"] == "true"
    assert meta["candidate_notice_provided"] == "true"
    assert meta["decision_type"] == "screening_for_employment"
    # Always-emitted municipal-jurisdiction tags.
    assert meta["jurisdiction_state"] == "NY"
    assert meta["jurisdiction_city"] == "NYC"
    # Optional fields absent in minimal construction.
    assert "affected_candidate_categories" not in meta
    assert "bias_audit_reference" not in meta
    assert "bias_audit_public_url" not in meta
    assert "candidate_notice_reference" not in meta
    assert "aedt_distribution_date" not in meta
    assert "workforce_member_role" not in meta


def test_nyc_ll144_framework_id_property_returns_canonical_identifier():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    assert ctx.framework_id == "NYC_LL_144"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_nyc_ll144_jurisdiction_state_property_returns_NY():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    assert ctx.jurisdiction_state == "NY"
    assert ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]


def test_nyc_ll144_jurisdiction_city_property_returns_NYC():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    assert ctx.jurisdiction_city == "NYC"
    assert (
        ctx.jurisdiction_city == ctx.to_metadata()["jurisdiction_city"]
    )


def test_nyc_ll144_jurisdiction_tags_always_in_metadata():
    """NYC LL 144 is by construction a New York City municipal helper.
    Both structural jurisdiction tags (state=NY + city=NYC) must always
    emit."""
    for decision in [
        "screening_for_employment",
        "promotion_decision",
        "screening_and_promotion",
        "not_in_scope",
    ]:
        ctx = NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=decision != "not_in_scope",
            bias_audit_completed=True,
            candidate_notice_provided=True,
            decision_type=decision,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "NY"
        assert ctx.to_metadata()["jurisdiction_city"] == "NYC"


def test_nyc_ll144_decision_type_set_property_returns_taxonomy():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    decisions = ctx.decision_type_set
    assert "screening_for_employment" in decisions
    assert "promotion_decision" in decisions
    assert "screening_and_promotion" in decisions
    assert "not_in_scope" in decisions
    assert "other" in decisions


def test_nyc_ll144_employer_role_set_property_returns_taxonomy():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    roles = ctx.employer_role_set
    assert "employer" in roles
    assert "employment_agency" in roles
    assert "vendor_to_employer" in roles


# ── Employer role validation ───────────────────────────────────


@pytest.mark.parametrize(
    "role", ["employer", "employment_agency", "vendor_to_employer"]
)
def test_nyc_ll144_all_employer_roles_accepted(role):
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role=role,
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    assert ctx.to_metadata()["employer_role"] == role


def test_nyc_ll144_invalid_employer_role_rejected():
    with pytest.raises(ValueError, match="employer_role"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="recruiter",  # not LL 144-aligned
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="not_in_scope",
        )


# ── Decision type validation ───────────────────────────────────


@pytest.mark.parametrize(
    "decision",
    [
        "screening_for_employment",
        "promotion_decision",
        "screening_and_promotion",
        "not_in_scope",
        "other",
    ],
)
def test_nyc_ll144_all_decision_types_accepted(decision):
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=decision != "not_in_scope",
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type=decision,
    )
    assert ctx.to_metadata()["decision_type"] == decision


def test_nyc_ll144_invalid_decision_type_rejected():
    with pytest.raises(ValueError, match="decision_type"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="background_check",  # not in taxonomy
        )


# ── Required-boolean enforcement ───────────────────────────────


def test_nyc_ll144_required_booleans_emit_string_true_false():
    """NYC LL 144 has three load-bearing booleans tied directly to
    statutory obligations: AEDT scope (§ 20-870), bias audit
    (§ 20-871(b)), candidate notice (§ 20-871(b)(2)). All must emit
    as lowercase strings in the metadata wire format."""
    ctx_all_true = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    meta_true = ctx_all_true.to_metadata()
    assert meta_true["aedt_in_use"] == "true"
    assert meta_true["bias_audit_completed"] == "true"
    assert meta_true["candidate_notice_provided"] == "true"

    ctx_all_false = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    meta_false = ctx_all_false.to_metadata()
    assert meta_false["aedt_in_use"] == "false"
    assert meta_false["bias_audit_completed"] == "false"
    assert meta_false["candidate_notice_provided"] == "false"


def test_nyc_ll144_aedt_in_use_non_bool_rejected():
    with pytest.raises(ValueError, match="aedt_in_use"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use="true",  # type: ignore[arg-type]
            bias_audit_completed=True,
            candidate_notice_provided=True,
            decision_type="screening_for_employment",
        )


def test_nyc_ll144_bias_audit_completed_non_bool_rejected():
    with pytest.raises(ValueError, match="bias_audit_completed"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=True,
            bias_audit_completed="true",  # type: ignore[arg-type]
            candidate_notice_provided=True,
            decision_type="screening_for_employment",
        )


def test_nyc_ll144_candidate_notice_provided_non_bool_rejected():
    with pytest.raises(ValueError, match="candidate_notice_provided"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=True,
            bias_audit_completed=True,
            candidate_notice_provided="true",  # type: ignore[arg-type]
            decision_type="screening_for_employment",
        )


def test_nyc_ll144_required_booleans_enforce_bool_shape_not_truthy():
    """NYC LL 144-specific test: AEDT-scope + bias-audit + candidate-
    notice fields enforce required boolean shape per § 20-871
    discipline. Customers must NOT pass truthy non-bool values."""
    with pytest.raises(ValueError, match="aedt_in_use"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=1,  # type: ignore[arg-type]
            bias_audit_completed=True,
            candidate_notice_provided=True,
            decision_type="screening_for_employment",
        )
    with pytest.raises(ValueError, match="bias_audit_completed"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=True,
            bias_audit_completed=1,  # type: ignore[arg-type]
            candidate_notice_provided=True,
            decision_type="screening_for_employment",
        )


# ── Empty required-field rejection ─────────────────────────────


def test_nyc_ll144_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="employer_organization"):
        NYCLL144Context(
            employer_organization="",
            employer_role="employer",
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="not_in_scope",
        )
    with pytest.raises(ValueError, match="employer_role"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="",
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="not_in_scope",
        )
    with pytest.raises(ValueError, match="decision_type"):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="",
        )


# ── Affected candidate categories ──────────────────────────────


def test_nyc_ll144_affected_candidate_categories_optional():
    ctx = NYCLL144Context(
        employer_organization="X",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    assert "affected_candidate_categories" not in ctx.to_metadata()


def test_nyc_ll144_affected_candidate_categories_comma_joined_when_present():
    ctx = NYCLL144Context(
        employer_organization="Acme NYC Hospital",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
        affected_candidate_categories=[
            "nyc_resident_candidates",
            "healthcare_clinical_staff_candidates",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["affected_candidate_categories"] == (
        "nyc_resident_candidates,"
        "healthcare_clinical_staff_candidates"
    )


def test_nyc_ll144_affected_candidate_categories_comma_in_item_rejected():
    with pytest.raises(
        ValueError, match="affected_candidate_categories"
    ):
        NYCLL144Context(
            employer_organization="X",
            employer_role="employer",
            aedt_in_use=True,
            bias_audit_completed=True,
            candidate_notice_provided=True,
            decision_type="screening_for_employment",
            affected_candidate_categories=[
                "nyc_resident_candidates,senior",  # comma in item
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_nyc_ll144_optional_fields_when_set_appear_in_metadata():
    ctx = NYCLL144Context(
        employer_organization="Acme NYC Hospital",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
        affected_candidate_categories=["nyc_resident_candidates"],
        bias_audit_reference="audit-2026-Q1-aedt",
        bias_audit_public_url="https://example.com/aedt-bias-audit",
        candidate_notice_reference="notice-2026-Q1",
        aedt_distribution_date="2026-01-15",
        workforce_member_role="hiring_manager",
    )
    meta = ctx.to_metadata()
    assert meta["bias_audit_reference"] == "audit-2026-Q1-aedt"
    assert (
        meta["bias_audit_public_url"]
        == "https://example.com/aedt-bias-audit"
    )
    assert meta["candidate_notice_reference"] == "notice-2026-Q1"
    assert meta["aedt_distribution_date"] == "2026-01-15"
    assert meta["workforce_member_role"] == "hiring_manager"


# ── Wire-constraint enforcement ────────────────────────────────


def test_nyc_ll144_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        NYCLL144Context(
            employer_organization="A" * 257,
            employer_role="employer",
            aedt_in_use=False,
            bias_audit_completed=False,
            candidate_notice_provided=False,
            decision_type="not_in_scope",
        )


def test_nyc_ll144_frozen_dataclass_mutation_raises():
    ctx = NYCLL144Context(
        employer_organization="A",
        employer_role="employer",
        aedt_in_use=False,
        bias_audit_completed=False,
        candidate_notice_provided=False,
        decision_type="not_in_scope",
    )
    with pytest.raises(Exception):
        ctx.employer_organization = "B"  # type: ignore[misc]


def test_nyc_ll144_metadata_json_round_trip_preserves_dict():
    ctx = NYCLL144Context(
        employer_organization="Acme",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
        affected_candidate_categories=["nyc_resident_candidates"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Three-role + factual-language discipline ───────────────────


def test_nyc_ll144_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer framing
    per ADR-017."""
    from nanorix.compliance import nyc_ll144 as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    assert (
        "regulatory counsel" in doc.lower()
        or "Department of Consumer and Worker Protection" in doc
        or "DCWP" in doc
    )


def test_nyc_ll144_class_docstring_contains_disclaimer_language():
    doc = NYCLL144Context.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_nyc_ll144_module_docstring_surfaces_bias_audit_obligation():
    """NYC LL 144-specific: the bias-audit obligation at § 20-871(b)
    is materially distinct from other state AI laws (independent-
    auditor requirement; selection-rate + impact-ratio calculation;
    public posting). The module docstring must surface this."""
    from nanorix.compliance import nyc_ll144 as mod

    doc = mod.__doc__ or ""
    assert "bias audit" in doc.lower()
    assert "independent" in doc.lower()
    assert "10 business days" in doc or "candidate notice" in doc.lower()


def test_nyc_ll144_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import nyc_ll144 as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


# ── NYC LL 144 + HIPAA paired workflow (healthcare staffing AEDT) ──


def test_nyc_ll144_paired_with_hipaa_context_merge_works():
    """NYC-jurisdictional healthcare-staffing AEDT cohort: a clinical-
    staff candidate-screening AEDT processing PHI would fall under
    both HIPAA (vertical) and NYC LL 144 (municipal-jurisdictional).
    Customer wraps with both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme NYC Hospital",
        minimum_necessary_purpose="Clinical staff screening",
    )
    aedt = NYCLL144Context(
        employer_organization="Acme NYC Hospital",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    merged = {**hipaa.to_metadata(), **aedt.to_metadata()}
    # NYC LL 144 wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "NYC_LL_144"
    # Both frameworks' substantive fields present.
    assert merged["covered_entity"] == "Acme NYC Hospital"
    assert merged["employer_organization"] == "Acme NYC Hospital"
    # Municipal-jurisdiction tags preserved.
    assert merged["jurisdiction_state"] == "NY"
    assert merged["jurisdiction_city"] == "NYC"
    # AEDT-specific load-bearing booleans preserved.
    assert merged["bias_audit_completed"] == "true"
    assert merged["candidate_notice_provided"] == "true"
