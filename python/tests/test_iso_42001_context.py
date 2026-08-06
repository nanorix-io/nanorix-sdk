"""Tests for nanorix.compliance.ISO42001Context."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import ISO42001Context


# ── Required fields + framework identity ───────────────────────


def test_iso_42001_minimal_context_emits_required_keys():
    ctx = ISO42001Context(
        organization="Acme Health AI",
        ai_system_role="ai_provider",
        ai_system_purpose="clinical-decision-support",
        pdca_phases_in_scope=["plan", "do"],
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "ISO_IEC_42001_2023"
    assert meta["nanorix:framework_version"] == "2023"
    assert meta["organization"] == "Acme Health AI"
    assert meta["ai_system_role"] == "ai_provider"
    assert meta["ai_system_purpose"] == "clinical-decision-support"
    assert meta["pdca_phases_in_scope"] == "plan,do"
    # Optional fields absent in minimal construction.
    assert "annex_a_controls_in_scope" not in meta
    assert "certification_body" not in meta
    assert "certification_status" not in meta
    assert "cert_reference" not in meta
    assert "aims_scope_statement_reference" not in meta
    assert "workforce_member_role" not in meta


def test_iso_42001_framework_id_property_returns_canonical_identifier():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_developer",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    assert ctx.framework_id == "ISO_IEC_42001_2023"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_iso_42001_pdca_phases_property_returns_four_canonical():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    phases = ctx.pdca_phases
    assert phases == ["plan", "do", "check", "act"]


def test_iso_42001_annex_a_controls_property_returns_23_canonical():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    controls = ctx.annex_a_controls
    assert len(controls) == 23
    # Specific known controls present.
    assert "A_2_2_ai_policy" in controls
    assert "A_5_2_ai_system_impact_assessment_process" in controls
    assert "A_7_4_communication_of_incidents" in controls


def test_iso_42001_annex_a_controls_returns_copy_not_module_state():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    controls_a = ctx.annex_a_controls
    controls_a.append("A_99_corrupted")
    controls_b = ctx.annex_a_controls
    assert "A_99_corrupted" not in controls_b
    assert len(controls_b) == 23


# ── AI system role validation ──────────────────────────────────


@pytest.mark.parametrize(
    "role",
    ["ai_provider", "ai_developer", "ai_user", "ai_deployer"],
)
def test_iso_42001_all_four_ai_system_roles_accepted(role):
    ctx = ISO42001Context(
        organization="X",
        ai_system_role=role,
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    assert ctx.to_metadata()["ai_system_role"] == role


def test_iso_42001_invalid_ai_system_role_rejected():
    with pytest.raises(ValueError, match="ai_system_role"):
        ISO42001Context(
            organization="X",
            ai_system_role="ai_observer",  # not in §3 taxonomy
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan"],
        )


# ── PDCA phases validation ─────────────────────────────────────


@pytest.mark.parametrize("phase", ["plan", "do", "check", "act"])
def test_iso_42001_all_four_pdca_phases_accepted(phase):
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=[phase],
    )
    assert ctx.to_metadata()["pdca_phases_in_scope"] == phase


def test_iso_42001_all_four_pdca_phases_in_combination():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan", "do", "check", "act"],
    )
    assert (
        ctx.to_metadata()["pdca_phases_in_scope"]
        == "plan,do,check,act"
    )


def test_iso_42001_empty_pdca_phases_rejected():
    with pytest.raises(ValueError, match="pdca_phases_in_scope"):
        ISO42001Context(
            organization="X",
            ai_system_role="ai_provider",
            ai_system_purpose="X",
            pdca_phases_in_scope=[],
        )


def test_iso_42001_invalid_pdca_phase_rejected():
    with pytest.raises(ValueError, match="pdca_phases_in_scope"):
        ISO42001Context(
            organization="X",
            ai_system_role="ai_provider",
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan", "ship"],  # invalid
        )


# ── Annex A controls validation ────────────────────────────────


def test_iso_42001_annex_a_controls_optional():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    assert "annex_a_controls_in_scope" not in ctx.to_metadata()


def test_iso_42001_annex_a_controls_when_set_appear_comma_joined():
    ctx = ISO42001Context(
        organization="X",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan", "do"],
        annex_a_controls_in_scope=[
            "A_2_2_ai_policy",
            "A_5_2_ai_system_impact_assessment_process",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["annex_a_controls_in_scope"] == (
        "A_2_2_ai_policy,A_5_2_ai_system_impact_assessment_process"
    )


def test_iso_42001_invalid_annex_a_control_rejected():
    with pytest.raises(ValueError, match="annex_a_controls_in_scope"):
        ISO42001Context(
            organization="X",
            ai_system_role="ai_provider",
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan"],
            annex_a_controls_in_scope=[
                "A_2_2_ai_policy",
                "A_99_invented",  # not canonical
            ],
        )


# ── Empty required-field rejection ─────────────────────────────


def test_iso_42001_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="organization"):
        ISO42001Context(
            organization="",
            ai_system_role="ai_provider",
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan"],
        )
    with pytest.raises(ValueError, match="ai_system_role"):
        ISO42001Context(
            organization="X",
            ai_system_role="",
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan"],
        )
    with pytest.raises(ValueError, match="ai_system_purpose"):
        ISO42001Context(
            organization="X",
            ai_system_role="ai_provider",
            ai_system_purpose="",
            pdca_phases_in_scope=["plan"],
        )


# ── Optional fields ────────────────────────────────────────────


def test_iso_42001_optional_fields_when_set_appear_in_metadata():
    ctx = ISO42001Context(
        organization="Acme Health AI",
        ai_system_role="ai_provider",
        ai_system_purpose="medical-imaging",
        pdca_phases_in_scope=["plan", "do", "check", "act"],
        annex_a_controls_in_scope=["A_5_2_ai_system_impact_assessment_process"],
        certification_body="A-LIGN",
        certification_status="certified",
        cert_reference="ISO-42001-2026-001",
        aims_scope_statement_reference="aims-scope-2026",
        workforce_member_role="ai_engineer",
    )
    meta = ctx.to_metadata()
    assert meta["certification_body"] == "A-LIGN"
    assert meta["certification_status"] == "certified"
    assert meta["cert_reference"] == "ISO-42001-2026-001"
    assert meta["aims_scope_statement_reference"] == "aims-scope-2026"
    assert meta["workforce_member_role"] == "ai_engineer"


# ── Wire-constraint enforcement ────────────────────────────────


def test_iso_42001_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        ISO42001Context(
            organization="A" * 257,
            ai_system_role="ai_provider",
            ai_system_purpose="X",
            pdca_phases_in_scope=["plan"],
        )


def test_iso_42001_frozen_dataclass_mutation_raises():
    ctx = ISO42001Context(
        organization="A",
        ai_system_role="ai_provider",
        ai_system_purpose="X",
        pdca_phases_in_scope=["plan"],
    )
    with pytest.raises(Exception):
        ctx.organization = "B"  # type: ignore[misc]


def test_iso_42001_metadata_json_round_trip_preserves_dict():
    ctx = ISO42001Context(
        organization="Acme",
        ai_system_role="ai_provider",
        ai_system_purpose="clinical-decision-support",
        pdca_phases_in_scope=["plan", "do"],
        annex_a_controls_in_scope=["A_2_2_ai_policy"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── ISO 42001 + NIST AI RMF paired workflow ────────────────────


def test_iso_42001_paired_with_nist_ai_rmf_context_merge_works():
    """Healthcare-AI cohort pursuing both ISO/IEC 42001 certification
    AND NIST AI RMF voluntary alignment. Customer wraps with both and
    merges."""
    from nanorix.compliance import NISTAIRMFContext

    iso = ISO42001Context(
        organization="Acme Health AI",
        ai_system_role="ai_provider",
        ai_system_purpose="clinical-decision-support",
        pdca_phases_in_scope=["plan", "do", "check", "act"],
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Health AI",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure", "manage"],
    )
    merged = {**iso.to_metadata(), **nist.to_metadata()}
    # NIST wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "NIST_AI_RMF_1.0"
    # Both frameworks' substantive fields present.
    assert merged["organization"] == "Acme Health AI"
    assert merged["ai_system_owner"] == "Acme Health AI"


# ── Three-role + factual-language discipline ───────────────────


def test_iso_42001_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import iso_42001 as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # ISO-specific certification body disclaimer language.
    assert "certification body" in doc.lower()


def test_iso_42001_class_docstring_contains_disclaimer_language():
    doc = ISO42001Context.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_iso_42001_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts.

    The string 'certification' is intentionally allowed (it appears in
    disclaimer language describing what the customer's certification
    body does); the test is case-sensitive on uppercase assertive
    forms only.
    """
    from nanorix.compliance import iso_42001 as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
