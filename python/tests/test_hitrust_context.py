"""Tests for nanorix.compliance.HITRUSTContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import HITRUSTContext


# ── Required fields + framework identity ───────────────────────


def test_hitrust_minimal_context_emits_required_keys():
    ctx = HITRUSTContext(
        covered_entity="Acme Hospital",
        assessment_type="r2",
        data_classification="PHI",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "HITRUST_CSF_v11"
    assert meta["nanorix:framework_version"] == "v11"
    assert meta["covered_entity"] == "Acme Hospital"
    assert meta["assessment_type"] == "r2"
    assert meta["data_classification"] == "PHI"
    # Optional fields absent in minimal construction.
    assert "in_scope_domains" not in meta
    assert "assessor" not in meta
    assert "assessment_period" not in meta
    assert "cert_reference" not in meta
    assert "workforce_member_role" not in meta


def test_hitrust_framework_id_property_returns_canonical_identifier():
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type="i1",
        data_classification="PHI",
    )
    assert ctx.framework_id == "HITRUST_CSF_v11"
    # framework_id matches what to_metadata emits.
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_hitrust_domains_property_returns_19_canonical_domains():
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type="r2",
        data_classification="X",
    )
    domains = ctx.domains
    assert len(domains) == 19
    assert domains[0].startswith("01_")
    assert domains[-1].startswith("19_")
    # Specific known domains present.
    assert "10_access_control" in domains
    assert "11_audit_logging_and_monitoring" in domains
    assert "18_data_protection_and_privacy" in domains


def test_hitrust_domains_property_returns_copy_not_module_state():
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type="r2",
        data_classification="X",
    )
    domains_a = ctx.domains
    domains_a.append("999_corrupted")
    domains_b = ctx.domains
    # Mutation of the first copy must not leak into the second copy.
    assert "999_corrupted" not in domains_b
    assert len(domains_b) == 19


# ── Assessment-type validation ──────────────────────────────────


@pytest.mark.parametrize("tier", ["e1", "i1", "r2"])
def test_hitrust_all_three_assessment_tiers_accepted(tier):
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type=tier,
        data_classification="X",
    )
    assert ctx.to_metadata()["assessment_type"] == tier


def test_hitrust_invalid_assessment_type_rejected():
    with pytest.raises(ValueError, match="assessment_type"):
        HITRUSTContext(
            covered_entity="X",
            assessment_type="r3",  # not in {e1, i1, r2}
            data_classification="X",
        )


def test_hitrust_empty_assessment_type_rejected():
    with pytest.raises(ValueError, match="assessment_type"):
        HITRUSTContext(
            covered_entity="X",
            assessment_type="",
            data_classification="X",
        )


# ── Empty required-field rejection ──────────────────────────────


def test_hitrust_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="covered_entity"):
        HITRUSTContext(
            covered_entity="",
            assessment_type="r2",
            data_classification="X",
        )
    with pytest.raises(ValueError, match="data_classification"):
        HITRUSTContext(
            covered_entity="X",
            assessment_type="r2",
            data_classification="",
        )


# ── In-scope domains ────────────────────────────────────────────


def test_hitrust_in_scope_domains_optional_when_empty():
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type="r2",
        data_classification="X",
        in_scope_domains=[],
    )
    # Empty list omits the in_scope_domains key entirely (workload may
    # not yet be evidence-mapped).
    assert "in_scope_domains" not in ctx.to_metadata()


def test_hitrust_in_scope_domains_comma_joined_when_present():
    ctx = HITRUSTContext(
        covered_entity="X",
        assessment_type="r2",
        data_classification="X",
        in_scope_domains=[
            "10_access_control",
            "11_audit_logging_and_monitoring",
            "18_data_protection_and_privacy",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["in_scope_domains"] == (
        "10_access_control,"
        "11_audit_logging_and_monitoring,"
        "18_data_protection_and_privacy"
    )


def test_hitrust_invalid_in_scope_domain_rejected():
    with pytest.raises(ValueError, match="in_scope_domains"):
        HITRUSTContext(
            covered_entity="X",
            assessment_type="r2",
            data_classification="X",
            in_scope_domains=["10_access_control", "99_invented_domain"],
        )


# ── Optional fields ─────────────────────────────────────────────


def test_hitrust_optional_fields_when_set_appear_in_metadata():
    ctx = HITRUSTContext(
        covered_entity="Acme Hospital",
        assessment_type="r2",
        data_classification="PHI-radiology",
        assessor="Coalfire",
        assessment_period="2026-Q2",
        cert_reference="HITRUST-2026-12345",
        workforce_member_role="attending_radiologist",
    )
    meta = ctx.to_metadata()
    assert meta["assessor"] == "Coalfire"
    assert meta["assessment_period"] == "2026-Q2"
    assert meta["cert_reference"] == "HITRUST-2026-12345"
    assert meta["workforce_member_role"] == "attending_radiologist"


# ── Wire-constraint enforcement ─────────────────────────────────


def test_hitrust_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        HITRUSTContext(
            covered_entity="A" * 257,
            assessment_type="r2",
            data_classification="X",
        )


def test_hitrust_frozen_dataclass_mutation_raises():
    ctx = HITRUSTContext(
        covered_entity="A",
        assessment_type="r2",
        data_classification="X",
    )
    with pytest.raises(Exception):
        ctx.covered_entity = "B"  # type: ignore[misc]


def test_hitrust_metadata_json_round_trip_preserves_dict():
    ctx = HITRUSTContext(
        covered_entity="Acme",
        assessment_type="i1",
        data_classification="PHI",
        in_scope_domains=["11_audit_logging_and_monitoring"],
        assessor="BDO",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Three-role + factual-language discipline ────────────────────


def test_hitrust_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import hitrust as mod

    doc = mod.__doc__ or ""
    # Must explicitly say does NOT certify / attest / claim.
    assert "does NOT" in doc
    # Must reference three-role discipline.
    assert "evidence vendor" in doc or "role 1" in doc


def test_hitrust_class_docstring_contains_disclaimer_language():
    doc = HITRUSTContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_hitrust_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts.

    Vocabulary-discipline framings ("does NOT certify", "compliance
    determination remains the customer's") are explicit
    non-assertions and are exempt.
    """
    from nanorix.compliance import hitrust as mod

    src = inspect.getsource(mod)
    # The strings should not appear as assertions about the workload's
    # state. The disclaimer language ("certify", "compliance with",
    # "certification") is intentional. Forbidden assertive forms are
    # the past-participle adjectives.
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    # Allow uppercase only inside docstrings explicitly framing
    # non-assertions; this regex is case-sensitive on uppercase forms.
    # Helper must not USE these as positive claims.
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
