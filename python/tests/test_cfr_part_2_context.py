"""Tests for nanorix.compliance.CFRPart2Context."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import CFRPart2Context


# ── Required fields + framework identity ───────────────────────


def test_cfr_part_2_minimal_context_emits_required_keys():
    ctx = CFRPart2Context(
        part_2_program="Acme SUD Program",
        minimum_necessary_purpose="Clinical workflow for counselor",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "42_CFR_Part_2"
    assert meta["nanorix:framework_version"] == "2024-Revision"
    assert meta["part_2_program"] == "Acme SUD Program"
    assert meta["minimum_necessary_purpose"] == (
        "Clinical workflow for counselor"
    )
    assert meta["data_classification"] == "PHI-SUD"  # default
    assert meta["disclosure_basis"] == "not_disclosed"  # default
    # Structural redisclosure-restriction flag always emitted.
    assert meta["treatment_record_redisclosure_restriction"] == "true"


def test_cfr_part_2_framework_id_property_returns_canonical_identifier():
    ctx = CFRPart2Context(
        part_2_program="X",
        minimum_necessary_purpose="X",
    )
    assert ctx.framework_id == "42_CFR_Part_2"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_cfr_part_2_redisclosure_restriction_applies_property():
    """§2.32 redisclosure restriction always applies to any workload
    wrapped in CFRPart2Context."""
    ctx = CFRPart2Context(
        part_2_program="X",
        minimum_necessary_purpose="X",
    )
    assert ctx.redisclosure_restriction_applies is True


def test_cfr_part_2_redisclosure_flag_always_in_metadata():
    """The treatment_record_redisclosure_restriction metadata key is
    structurally emitted for every workload, regardless of disclosure
    basis. The §2.32 restriction is on records, not on the disclosure
    decision."""
    for basis in [
        "not_disclosed",
        "patient_consent",
        "qso_agreement",
    ]:
        kwargs = {
            "part_2_program": "X",
            "minimum_necessary_purpose": "X",
            "disclosure_basis": basis,
        }
        if basis == "patient_consent":
            kwargs["consent_reference"] = "consent_001"
        ctx = CFRPart2Context(**kwargs)  # type: ignore[arg-type]
        meta = ctx.to_metadata()
        assert (
            meta["treatment_record_redisclosure_restriction"] == "true"
        )


# ── Disclosure-basis validation ─────────────────────────────────


@pytest.mark.parametrize(
    "basis,extras",
    [
        ("not_disclosed", {}),
        ("medical_emergency", {}),
        ("audit_evaluation", {}),
        ("court_order", {}),
        ("qso_agreement", {}),
        ("patient_consent", {"consent_reference": "consent_001"}),
        (
            "research",
            {"research_authorization_reference": "research_001"},
        ),
    ],
)
def test_cfr_part_2_all_disclosure_bases_accepted(basis, extras):
    ctx = CFRPart2Context(
        part_2_program="X",
        minimum_necessary_purpose="X",
        disclosure_basis=basis,
        **extras,
    )
    assert ctx.to_metadata()["disclosure_basis"] == basis


def test_cfr_part_2_invalid_disclosure_basis_rejected():
    with pytest.raises(ValueError, match="disclosure_basis"):
        CFRPart2Context(
            part_2_program="X",
            minimum_necessary_purpose="X",
            disclosure_basis="hand_wave",  # not in enum
        )


def test_cfr_part_2_consent_basis_requires_consent_reference():
    with pytest.raises(ValueError, match="consent_reference"):
        CFRPart2Context(
            part_2_program="X",
            minimum_necessary_purpose="X",
            disclosure_basis="patient_consent",
            # consent_reference omitted → must raise
        )


def test_cfr_part_2_research_basis_requires_research_reference():
    with pytest.raises(
        ValueError, match="research_authorization_reference"
    ):
        CFRPart2Context(
            part_2_program="X",
            minimum_necessary_purpose="X",
            disclosure_basis="research",
            # research_authorization_reference omitted → must raise
        )


# ── Empty required-field rejection ──────────────────────────────


def test_cfr_part_2_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="part_2_program"):
        CFRPart2Context(
            part_2_program="",
            minimum_necessary_purpose="X",
        )
    with pytest.raises(ValueError, match="minimum_necessary_purpose"):
        CFRPart2Context(
            part_2_program="X",
            minimum_necessary_purpose="",
        )


# ── Optional fields ─────────────────────────────────────────────


def test_cfr_part_2_optional_fields_when_set_appear_in_metadata():
    ctx = CFRPart2Context(
        part_2_program="Acme SUD",
        minimum_necessary_purpose="Clinical workflow",
        data_classification="PHI-SUD-counseling-notes",
        disclosure_basis="qso_agreement",
        qso_agreement_reference="QSOA-2026-001",
        workforce_member_role="sud_counselor",
    )
    meta = ctx.to_metadata()
    assert meta["data_classification"] == "PHI-SUD-counseling-notes"
    assert meta["qso_agreement_reference"] == "QSOA-2026-001"
    assert meta["workforce_member_role"] == "sud_counselor"


# ── Wire-constraint enforcement ─────────────────────────────────


def test_cfr_part_2_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        CFRPart2Context(
            part_2_program="A" * 257,
            minimum_necessary_purpose="X",
        )


def test_cfr_part_2_frozen_dataclass_mutation_raises():
    ctx = CFRPart2Context(
        part_2_program="A",
        minimum_necessary_purpose="X",
    )
    with pytest.raises(Exception):
        ctx.part_2_program = "B"  # type: ignore[misc]


def test_cfr_part_2_metadata_json_round_trip_preserves_dict():
    ctx = CFRPart2Context(
        part_2_program="Acme SUD",
        minimum_necessary_purpose="X",
        qso_agreement_reference="QSOA-2026-Q2",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── HIPAA + Part 2 paired workflow ─────────────────────────────


def test_cfr_part_2_paired_with_hipaa_context_merge_works():
    """Behavioral-health cohort: SUD records fall under BOTH HIPAA and
    42 CFR Part 2. Customer wraps with both and merges. Sanity-check
    that merging produces a single dict whose framework key reflects
    whichever is last (customer choice)."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Treatment Center",
        minimum_necessary_purpose="Clinical workflow",
    )
    part2 = CFRPart2Context(
        part_2_program="Acme SUD Program",
        minimum_necessary_purpose="Clinical workflow",
    )
    # Part 2 wins by default (right-hand spread).
    merged = {**hipaa.to_metadata(), **part2.to_metadata()}
    assert merged["nanorix:framework"] == "42_CFR_Part_2"
    # Both frameworks' substantive fields are present.
    assert merged["covered_entity"] == "Acme Treatment Center"
    assert merged["part_2_program"] == "Acme SUD Program"


# ── Three-role + factual-language discipline ────────────────────


def test_cfr_part_2_module_docstring_contains_disclaimer_language():
    from nanorix.compliance import cfr_part_2 as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # Redisclosure prohibition + consent are customer responsibility.
    assert "§2.32" in doc or "redisclosure" in doc.lower()


def test_cfr_part_2_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules: no assertive
    COMPLIANT / SATISFIED / PASSED / MEETS / CERTIFIED in helper
    source."""
    from nanorix.compliance import cfr_part_2 as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
