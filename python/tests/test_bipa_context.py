"""Tests for nanorix.compliance.BIPAContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import BIPAContext


# ── Required fields + framework identity ───────────────────────


def test_bipa_minimal_context_emits_required_keys():
    ctx = BIPAContext(
        collecting_entity="Acme Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "BIPA"
    assert meta["nanorix:framework_version"] == "2008-operative"
    assert meta["collecting_entity"] == "Acme Voice Biomarker Co"
    assert meta["biometric_data_in_scope"] == "true"
    assert meta["consent_obtained"] == "true"
    assert meta["retention_policy_documented"] == "true"
    assert meta["biometric_identifier_type"] == "voiceprint"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "IL"
    # Optional fields absent in minimal construction.
    assert "consent_basis" not in meta
    assert "affected_individual_categories" not in meta
    assert "consent_record_reference" not in meta
    assert "retention_policy_reference" not in meta
    assert "destruction_cadence_reference" not in meta
    assert "workforce_member_role" not in meta


def test_bipa_framework_id_property_returns_canonical_identifier():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    assert ctx.framework_id == "BIPA"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_bipa_jurisdiction_state_property_returns_IL():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    assert ctx.jurisdiction_state == "IL"
    assert (
        ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]
    )


def test_bipa_jurisdiction_state_always_in_metadata():
    """BIPA is by construction an Illinois-jurisdictional helper.
    The structural jurisdiction_state="IL" key must always emit."""
    for biometric in [
        "voiceprint",
        "fingerprint",
        "retina_iris_scan",
        "no_biometrics_processed",
    ]:
        ctx = BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=biometric != "no_biometrics_processed",
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type=biometric,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "IL"


def test_bipa_biometric_identifier_type_set_property_returns_taxonomy():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    types = ctx.biometric_identifier_type_set
    # §14/10 statutory identifiers present.
    assert "retina_iris_scan" in types
    assert "fingerprint" in types
    assert "voiceprint" in types
    assert "hand_geometry_scan" in types
    assert "face_geometry_scan" in types
    # Derived information category present.
    assert "biometric_information_derived" in types
    # Escape hatches present.
    assert "no_biometrics_processed" in types
    assert "other" in types


def test_bipa_consent_basis_set_property_returns_taxonomy():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    bases = ctx.consent_basis_set
    assert "written_consent_obtained" in bases
    assert "healthcare_exemption_claimed" in bases
    assert "court_ordered" in bases
    assert "no_consent_required" in bases
    assert "consent_pending" in bases
    assert "other" in bases


# ── Biometric-identifier-type validation ───────────────────────


@pytest.mark.parametrize(
    "biometric",
    [
        "retina_iris_scan",
        "fingerprint",
        "voiceprint",
        "hand_geometry_scan",
        "face_geometry_scan",
        "biometric_information_derived",
        "no_biometrics_processed",
        "other",
    ],
)
def test_bipa_all_biometric_identifier_types_accepted(biometric):
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=biometric != "no_biometrics_processed",
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type=biometric,
    )
    assert ctx.to_metadata()["biometric_identifier_type"] == biometric


def test_bipa_invalid_biometric_identifier_type_rejected():
    with pytest.raises(ValueError, match="biometric_identifier_type"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type="dna_sequence",  # not §14/10
        )


# ── Required-boolean enforcement ───────────────────────────────


def test_bipa_required_booleans_emit_string_true_false():
    """BIPA has three load-bearing booleans tied directly to §14/15
    statutory obligations; all must emit as lowercase strings in the
    metadata wire format."""
    ctx_all_true = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
    )
    meta_true = ctx_all_true.to_metadata()
    assert meta_true["biometric_data_in_scope"] == "true"
    assert meta_true["consent_obtained"] == "true"
    assert meta_true["retention_policy_documented"] == "true"

    ctx_all_false = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    meta_false = ctx_all_false.to_metadata()
    assert meta_false["biometric_data_in_scope"] == "false"
    assert meta_false["consent_obtained"] == "false"
    assert meta_false["retention_policy_documented"] == "false"


def test_bipa_biometric_data_in_scope_non_bool_rejected():
    with pytest.raises(ValueError, match="biometric_data_in_scope"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope="true",  # type: ignore[arg-type]
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
        )


def test_bipa_consent_obtained_non_bool_rejected():
    with pytest.raises(ValueError, match="consent_obtained"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained="true",  # type: ignore[arg-type]
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
        )


def test_bipa_retention_policy_documented_non_bool_rejected():
    with pytest.raises(
        ValueError, match="retention_policy_documented"
    ):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained=True,
            retention_policy_documented="true",  # type: ignore[arg-type]
            biometric_identifier_type="voiceprint",
        )


def test_bipa_required_booleans_enforce_bool_shape_not_truthy():
    """BIPA-specific test: written consent + retention policy fields
    enforce required boolean shape per §14/15(a) + §14/15(b)
    discipline. Customers must NOT pass truthy non-bool values
    (e.g., int 1) for these load-bearing fields."""
    with pytest.raises(ValueError, match="biometric_data_in_scope"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=1,  # type: ignore[arg-type]
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
        )
    with pytest.raises(ValueError, match="consent_obtained"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained=1,  # type: ignore[arg-type]
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
        )


# ── Empty required-field rejection ─────────────────────────────


def test_bipa_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="collecting_entity"):
        BIPAContext(
            collecting_entity="",
            biometric_data_in_scope=False,
            consent_obtained=False,
            retention_policy_documented=False,
            biometric_identifier_type="no_biometrics_processed",
        )
    with pytest.raises(ValueError, match="biometric_identifier_type"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=False,
            consent_obtained=False,
            retention_policy_documented=False,
            biometric_identifier_type="",
        )


# ── Consent basis (optional with enum validation) ──────────────


def test_bipa_consent_basis_optional():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    assert "consent_basis" not in ctx.to_metadata()


@pytest.mark.parametrize(
    "basis",
    [
        "written_consent_obtained",
        "healthcare_exemption_claimed",
        "court_ordered",
        "no_consent_required",
        "consent_pending",
        "other",
    ],
)
def test_bipa_all_consent_bases_accepted(basis):
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=True,
        consent_obtained=basis == "written_consent_obtained",
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
        consent_basis=basis,
    )
    assert ctx.to_metadata()["consent_basis"] == basis


def test_bipa_invalid_consent_basis_rejected():
    with pytest.raises(ValueError, match="consent_basis"):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
            consent_basis="oral_consent",  # not canonical
        )


# ── Affected individual categories ─────────────────────────────


def test_bipa_affected_individual_categories_optional():
    ctx = BIPAContext(
        collecting_entity="X",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    assert "affected_individual_categories" not in ctx.to_metadata()


def test_bipa_affected_individual_categories_comma_joined_when_present():
    ctx = BIPAContext(
        collecting_entity="Acme Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
        affected_individual_categories=[
            "illinois_residents",
            "illinois_voice_biomarker_subjects",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["affected_individual_categories"] == (
        "illinois_residents,illinois_voice_biomarker_subjects"
    )


def test_bipa_affected_individual_categories_comma_in_item_rejected():
    with pytest.raises(
        ValueError, match="affected_individual_categories"
    ):
        BIPAContext(
            collecting_entity="X",
            biometric_data_in_scope=True,
            consent_obtained=True,
            retention_policy_documented=True,
            biometric_identifier_type="voiceprint",
            affected_individual_categories=[
                "illinois_residents,under_18",  # comma in item
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_bipa_optional_fields_when_set_appear_in_metadata():
    ctx = BIPAContext(
        collecting_entity="Acme Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
        consent_basis="written_consent_obtained",
        affected_individual_categories=["illinois_patients"],
        consent_record_reference="consent-2026-Q1-IL",
        retention_policy_reference="retention-policy-v2-2026",
        destruction_cadence_reference="destruction-records-2026",
        workforce_member_role="voice_biomarker_engineer",
    )
    meta = ctx.to_metadata()
    assert meta["consent_basis"] == "written_consent_obtained"
    assert meta["consent_record_reference"] == "consent-2026-Q1-IL"
    assert (
        meta["retention_policy_reference"]
        == "retention-policy-v2-2026"
    )
    assert (
        meta["destruction_cadence_reference"]
        == "destruction-records-2026"
    )
    assert meta["workforce_member_role"] == "voice_biomarker_engineer"


# ── Wire-constraint enforcement ────────────────────────────────


def test_bipa_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        BIPAContext(
            collecting_entity="A" * 257,
            biometric_data_in_scope=False,
            consent_obtained=False,
            retention_policy_documented=False,
            biometric_identifier_type="no_biometrics_processed",
        )


def test_bipa_frozen_dataclass_mutation_raises():
    ctx = BIPAContext(
        collecting_entity="A",
        biometric_data_in_scope=False,
        consent_obtained=False,
        retention_policy_documented=False,
        biometric_identifier_type="no_biometrics_processed",
    )
    with pytest.raises(Exception):
        ctx.collecting_entity = "B"  # type: ignore[misc]


def test_bipa_metadata_json_round_trip_preserves_dict():
    ctx = BIPAContext(
        collecting_entity="Acme",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
        consent_basis="written_consent_obtained",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── BIPA + HIPAA paired workflow (Illinois healthcare biometrics) ──


def test_bipa_paired_with_hipaa_context_merge_works():
    """Illinois-jurisdictional healthcare biometric AI cohort:
    workload falls under both HIPAA (vertical) and BIPA (state-
    jurisdictional + biometric-specific). Customer wraps with both
    and merges. The §14/10 healthcare exemption scope is litigated;
    customer counsel determines applicability."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Illinois Voice Biomarker Co",
        minimum_necessary_purpose="Voice-based clinical screening",
    )
    bipa = BIPAContext(
        collecting_entity="Acme Illinois Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
        consent_basis="written_consent_obtained",
    )
    merged = {**hipaa.to_metadata(), **bipa.to_metadata()}
    # BIPA wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "BIPA"
    # Both frameworks' substantive fields present.
    assert (
        merged["covered_entity"] == "Acme Illinois Voice Biomarker Co"
    )
    assert (
        merged["collecting_entity"]
        == "Acme Illinois Voice Biomarker Co"
    )
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "IL"
    # BIPA-specific load-bearing booleans preserved.
    assert merged["consent_obtained"] == "true"
    assert merged["retention_policy_documented"] == "true"


# ── Three-role + factual-language discipline ───────────────────


def test_bipa_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import bipa as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # Illinois-specific enforcement / counsel disclaimer language.
    assert (
        "regulatory counsel" in doc.lower()
        or "Illinois Attorney General" in doc
        or "private litigation" in doc.lower()
    )


def test_bipa_class_docstring_contains_disclaimer_language():
    doc = BIPAContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_bipa_module_docstring_surfaces_private_right_of_action():
    """BIPA-specific: the private right of action at §14/20 is a
    material risk consideration that materially differentiates BIPA
    from other US privacy statutes. The module docstring must surface
    this differentiator."""
    from nanorix.compliance import bipa as mod

    doc = mod.__doc__ or ""
    assert "private right of action" in doc.lower()
    # Statutory damages framing surfaced.
    assert (
        "$1,000" in doc
        or "$5,000" in doc
        or "statutory damages" in doc.lower()
    )


def test_bipa_class_docstring_surfaces_private_right_of_action():
    """The class-level docstring must also surface the private-right-
    of-action risk for customers reading help() / IDE tooltips."""
    doc = BIPAContext.__doc__ or ""
    assert "private right of action" in doc.lower()


def test_bipa_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import bipa as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
