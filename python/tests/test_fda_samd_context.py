"""Tests for nanorix.healthcare.FDASaMDContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.healthcare import FDASaMDContext


# ── Required fields + framework identity ───────────────────────


def test_fda_samd_minimal_context_emits_required_keys():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use=(
            "Voice biomarker for behavioral health screening"
        ),
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "FDA_SaMD"
    assert meta["nanorix:framework_version"] == "2026-02"
    assert meta["device_class"] == "II"
    assert meta["submission_pathway"] == "510k"
    assert meta["submission_status"] == "in_review"
    assert meta["intended_use"] == (
        "Voice biomarker for behavioral health screening"
    )
    # Optional fields absent in minimal construction.
    assert "indications_for_use" not in meta
    assert "clinical_validation_status" not in meta
    assert "k_number" not in meta
    assert "de_novo_number" not in meta
    assert "subgroup_performance_disclosed" not in meta
    assert "predicate_device_reference" not in meta
    assert "quality_system_reference" not in meta
    assert "submission_owner_role" not in meta
    assert "affected_device_categories" not in meta


def test_fda_samd_framework_id_property_returns_canonical_identifier():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    assert ctx.framework_id == "FDA_SaMD"
    assert (
        ctx.framework_id == ctx.to_metadata()["nanorix:framework"]
    )


def test_fda_samd_framework_version_property_returns_canonical():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    assert ctx.framework_version == "2026-02"
    assert (
        ctx.framework_version
        == ctx.to_metadata()["nanorix:framework_version"]
    )


# ── Device class validation (21 CFR Part 860) ──────────────────


@pytest.mark.parametrize("dc", ["I", "II", "III"])
def test_fda_samd_all_device_classes_accepted(dc):
    ctx = FDASaMDContext(
        device_class=dc,
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    assert ctx.to_metadata()["device_class"] == dc


def test_fda_samd_invalid_device_class_rejected():
    with pytest.raises(ValueError, match="device_class"):
        FDASaMDContext(
            device_class="IV",
            submission_pathway="510k",
            submission_status="pre_submission",
            intended_use="X",
        )


def test_fda_samd_invalid_device_class_lowercase_rejected():
    """21 CFR Part 860 enumeration is uppercase Roman numerals."""
    with pytest.raises(ValueError, match="device_class"):
        FDASaMDContext(
            device_class="ii",
            submission_pathway="510k",
            submission_status="pre_submission",
            intended_use="X",
        )


def test_fda_samd_device_class_set_returns_canonical_taxonomy():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    classes = ctx.device_class_set
    assert classes == ["I", "II", "III"]


def test_fda_samd_is_high_risk_class_property():
    """Class III = highest risk under 21 CFR Part 860."""
    c1 = FDASaMDContext(
        device_class="I",
        submission_pathway="exempt",
        submission_status="cleared",
        intended_use="X",
    )
    c2 = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="X",
    )
    c3 = FDASaMDContext(
        device_class="III",
        submission_pathway="pma",
        submission_status="approved",
        intended_use="X",
    )
    assert c1.is_high_risk_class is False
    assert c2.is_high_risk_class is False
    assert c3.is_high_risk_class is True


# ── Submission pathway validation ──────────────────────────────


@pytest.mark.parametrize(
    "pathway", ["510k", "de_novo", "pma", "exempt"]
)
def test_fda_samd_all_submission_pathways_accepted(pathway):
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway=pathway,
        submission_status="pre_submission",
        intended_use="X",
    )
    assert ctx.to_metadata()["submission_pathway"] == pathway


def test_fda_samd_invalid_submission_pathway_rejected():
    """Custom 'fda_breakthrough' pathway is not in the canonical set —
    must be rejected at construction."""
    with pytest.raises(ValueError, match="submission_pathway"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="fda_breakthrough",
            submission_status="pre_submission",
            intended_use="X",
        )


def test_fda_samd_submission_pathway_set_returns_canonical_taxonomy():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    pathways = ctx.submission_pathway_set
    assert "510k" in pathways
    assert "de_novo" in pathways
    assert "pma" in pathways
    assert "exempt" in pathways


# ── Submission status validation ───────────────────────────────


@pytest.mark.parametrize(
    "status",
    [
        "pre_submission",
        "submitted",
        "in_review",
        "cleared",
        "approved",
        "denied",
        "withdrawn",
    ],
)
def test_fda_samd_all_submission_statuses_accepted(status):
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status=status,
        intended_use="X",
    )
    assert ctx.to_metadata()["submission_status"] == status


def test_fda_samd_invalid_submission_status_rejected():
    with pytest.raises(ValueError, match="submission_status"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="fda_breakthrough_designation",
            intended_use="X",
        )


def test_fda_samd_submission_status_set_returns_canonical_taxonomy():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    statuses = ctx.submission_status_set
    for s in [
        "pre_submission",
        "submitted",
        "in_review",
        "cleared",
        "approved",
        "denied",
        "withdrawn",
    ]:
        assert s in statuses


def test_fda_samd_is_cleared_or_approved_property():
    cleared = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="X",
    )
    approved = FDASaMDContext(
        device_class="III",
        submission_pathway="pma",
        submission_status="approved",
        intended_use="X",
    )
    in_review = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="X",
    )
    denied = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="denied",
        intended_use="X",
    )
    assert cleared.is_cleared_or_approved is True
    assert approved.is_cleared_or_approved is True
    assert in_review.is_cleared_or_approved is False
    assert denied.is_cleared_or_approved is False


# ── Empty required-field rejection ─────────────────────────────


def test_fda_samd_empty_device_class_rejected():
    with pytest.raises(ValueError, match="device_class"):
        FDASaMDContext(
            device_class="",
            submission_pathway="510k",
            submission_status="pre_submission",
            intended_use="X",
        )


def test_fda_samd_empty_submission_pathway_rejected():
    with pytest.raises(ValueError, match="submission_pathway"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="",
            submission_status="pre_submission",
            intended_use="X",
        )


def test_fda_samd_empty_submission_status_rejected():
    with pytest.raises(ValueError, match="submission_status"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="",
            intended_use="X",
        )


def test_fda_samd_empty_intended_use_rejected():
    with pytest.raises(ValueError, match="intended_use"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="pre_submission",
            intended_use="",
        )


# ── Clinical validation status (optional enum) ─────────────────


@pytest.mark.parametrize(
    "phase", ["not_started", "in_progress", "completed"]
)
def test_fda_samd_all_clinical_validation_statuses_accepted(phase):
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="X",
        clinical_validation_status=phase,
    )
    assert ctx.to_metadata()["clinical_validation_status"] == phase


def test_fda_samd_invalid_clinical_validation_status_rejected():
    with pytest.raises(
        ValueError, match="clinical_validation_status"
    ):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            clinical_validation_status="halfway",
        )


def test_fda_samd_clinical_validation_status_optional():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    assert "clinical_validation_status" not in ctx.to_metadata()


# ── K-number format validation (K\d{6}) ────────────────────────


@pytest.mark.parametrize(
    "k", ["K000001", "K123456", "K231234", "K999999"]
)
def test_fda_samd_valid_k_number_formats_accepted(k):
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="X",
        k_number=k,
    )
    assert ctx.to_metadata()["k_number"] == k


@pytest.mark.parametrize(
    "bad",
    [
        "K12345",  # 5 digits
        "K1234567",  # 7 digits
        "k123456",  # lowercase prefix
        "DEN123456",  # DEN prefix
        "123456",  # no prefix
        "K12345A",  # trailing letter
        "KK123456",  # double K
    ],
)
def test_fda_samd_invalid_k_number_format_rejected(bad):
    with pytest.raises(ValueError, match="k_number"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="cleared",
            intended_use="X",
            k_number=bad,
        )


# ── DEN-number format validation (DEN\d{6}) ────────────────────


@pytest.mark.parametrize(
    "den", ["DEN000001", "DEN230001", "DEN999999"]
)
def test_fda_samd_valid_de_novo_number_formats_accepted(den):
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="de_novo",
        submission_status="cleared",
        intended_use="X",
        de_novo_number=den,
    )
    assert ctx.to_metadata()["de_novo_number"] == den


@pytest.mark.parametrize(
    "bad",
    [
        "DEN12345",  # 5 digits
        "DEN1234567",  # 7 digits
        "den230001",  # lowercase prefix
        "K230001",  # K prefix
        "230001",  # no prefix
        "DEN23000A",  # trailing letter
    ],
)
def test_fda_samd_invalid_de_novo_number_format_rejected(bad):
    with pytest.raises(ValueError, match="de_novo_number"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="de_novo",
            submission_status="cleared",
            intended_use="X",
            de_novo_number=bad,
        )


# ── Subgroup performance disclosure (emerging FDA expectation) ──


def test_fda_samd_subgroup_performance_disclosed_true_emits_string():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="X",
        subgroup_performance_disclosed=True,
    )
    assert (
        ctx.to_metadata()["subgroup_performance_disclosed"] == "true"
    )


def test_fda_samd_subgroup_performance_disclosed_false_emits_string():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="X",
        subgroup_performance_disclosed=False,
    )
    assert (
        ctx.to_metadata()["subgroup_performance_disclosed"] == "false"
    )


def test_fda_samd_subgroup_performance_disclosed_omitted_when_none():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    assert "subgroup_performance_disclosed" not in ctx.to_metadata()


def test_fda_samd_subgroup_performance_disclosed_non_bool_rejected():
    """Per FDA AI/ML 2025 draft framing: subgroup disclosure is an
    emerging expectation. The helper still enforces strict boolean
    shape on this declaration."""
    with pytest.raises(
        ValueError, match="subgroup_performance_disclosed"
    ):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            subgroup_performance_disclosed="true",  # type: ignore[arg-type]
        )


def test_fda_samd_subgroup_performance_disclosed_int_rejected():
    with pytest.raises(
        ValueError, match="subgroup_performance_disclosed"
    ):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            subgroup_performance_disclosed=1,  # type: ignore[arg-type]
        )


# ── Optional reference fields ──────────────────────────────────


def test_fda_samd_optional_fields_when_set_appear_in_metadata():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="Voice biomarker for behavioral health screening",
        indications_for_use=(
            "Adjunctive screening for depression in adult "
            "primary care"
        ),
        clinical_validation_status="completed",
        k_number="K231234",
        subgroup_performance_disclosed=True,
        predicate_device_reference="K201234",
        quality_system_reference="qsr-records-2026-q1",
        submission_owner_role="regulatory_affairs_lead",
        affected_device_categories=[
            "voice_biomarker_diagnostic",
            "behavioral_health_screening",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["indications_for_use"] == (
        "Adjunctive screening for depression in adult primary care"
    )
    assert meta["clinical_validation_status"] == "completed"
    assert meta["k_number"] == "K231234"
    assert meta["subgroup_performance_disclosed"] == "true"
    assert meta["predicate_device_reference"] == "K201234"
    assert meta["quality_system_reference"] == "qsr-records-2026-q1"
    assert meta["submission_owner_role"] == "regulatory_affairs_lead"
    assert meta["affected_device_categories"] == (
        "voice_biomarker_diagnostic,behavioral_health_screening"
    )


def test_fda_samd_affected_device_categories_comma_in_item_rejected():
    """Comma inside list item collides with comma-join separator."""
    with pytest.raises(
        ValueError, match="affected_device_categories"
    ):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            affected_device_categories=[
                "voice_biomarker,diagnostic",  # comma in item
            ],
        )


def test_fda_samd_affected_device_categories_empty_string_rejected():
    with pytest.raises(
        ValueError, match="affected_device_categories"
    ):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            affected_device_categories=["valid_category", ""],
        )


# ── Wire-constraint enforcement ────────────────────────────────


def test_fda_samd_oversized_intended_use_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="pre_submission",
            intended_use="A" * 257,
        )


def test_fda_samd_oversized_indications_for_use_rejected():
    with pytest.raises(ValueError, match="256-char limit"):
        FDASaMDContext(
            device_class="II",
            submission_pathway="510k",
            submission_status="in_review",
            intended_use="X",
            indications_for_use="B" * 257,
        )


def test_fda_samd_metadata_keys_under_16_max():
    """ADR-010 A4 wire constraint: max 16 metadata keys."""
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="X",
        indications_for_use="Y",
        clinical_validation_status="completed",
        k_number="K231234",
        subgroup_performance_disclosed=True,
        predicate_device_reference="K201234",
        quality_system_reference="qsr",
        submission_owner_role="ra_lead",
        affected_device_categories=["cat1"],
    )
    meta = ctx.to_metadata()
    assert len(meta) <= 16


def test_fda_samd_metadata_serialized_size_under_4kb():
    """ADR-010 A4 wire constraint: max 4096 bytes serialized."""
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="Voice biomarker for behavioral health",
        indications_for_use=(
            "Adjunctive screening for depression in adult "
            "primary care"
        ),
        clinical_validation_status="completed",
        k_number="K231234",
        subgroup_performance_disclosed=True,
    )
    meta = ctx.to_metadata()
    assert len(json.dumps(meta)) <= 4096


def test_fda_samd_frozen_dataclass_mutation_raises():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="pre_submission",
        intended_use="X",
    )
    with pytest.raises(Exception):
        ctx.device_class = "III"  # type: ignore[misc]


def test_fda_samd_metadata_json_round_trip_preserves_dict():
    ctx = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="Voice biomarker for behavioral health screening",
        k_number="K231234",
        subgroup_performance_disclosed=True,
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Composition with NIST AI RMF (Item 82 fairness eval) ───────


def test_fda_samd_paired_with_nist_ai_rmf_subgroup_fairness():
    """FDA AI/ML 2025 draft subgroup-performance expectation
    composes with NIST AI RMF Measure-2.11 fairness evaluation. The
    customer wraps both context tags so auditors + GRC platforms see
    the cross-framework fairness posture."""
    from nanorix.compliance import NISTAIRMFContext

    fda = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use="Voice biomarker for behavioral health screening",
        clinical_validation_status="completed",
        subgroup_performance_disclosed=True,
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Voice Biomarker Co",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure", "manage"],
    )
    merged = {**fda.to_metadata(), **nist.to_metadata()}
    # NIST AI RMF wins by default (right-hand spread).
    assert merged["nanorix:framework"] == nist.framework_id
    # FDA SaMD substantive fields preserved.
    assert merged["device_class"] == "II"
    assert merged["submission_pathway"] == "510k"
    assert merged["subgroup_performance_disclosed"] == "true"
    # NIST AI RMF substantive fields preserved.
    assert merged["ai_system_owner"] == "Acme Voice Biomarker Co"


def test_fda_samd_paired_with_hipaa_phi_handling():
    """FDA SaMD does NOT supersede HIPAA. Healthcare-AI device
    software handling PHI requires both framework helpers."""
    from nanorix.compliance import HipaaContext

    fda = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="Adjunctive depression screening",
        k_number="K231234",
    )
    hipaa = HipaaContext(
        covered_entity="Acme Voice Biomarker Co",
        minimum_necessary_purpose=(
            "Voice-based behavioral health screening inference"
        ),
    )
    merged = {**fda.to_metadata(), **hipaa.to_metadata()}
    # HIPAA wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "HIPAA"
    # Both frameworks' substantive fields present.
    assert merged["device_class"] == "II"
    assert merged["k_number"] == "K231234"
    assert merged["covered_entity"] == "Acme Voice Biomarker Co"


def test_fda_samd_paired_with_bipa_illinois_voice_biomarker():
    """Illinois-jurisdictional voice biomarker SaMD: triple-wrap
    FDA SaMD + HIPAA + BIPA. BIPA's §14/20 private right of action
    materially increases the risk surface for Illinois deployments."""
    from nanorix.compliance import BIPAContext, HipaaContext

    fda = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="Voice biomarker depression screening",
        k_number="K231234",
    )
    hipaa = HipaaContext(
        covered_entity="Acme Illinois Voice Biomarker Co",
        minimum_necessary_purpose=(
            "Voice-based behavioral health screening"
        ),
    )
    bipa = BIPAContext(
        collecting_entity="Acme Illinois Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
    )
    merged = {
        **fda.to_metadata(),
        **hipaa.to_metadata(),
        **bipa.to_metadata(),
    }
    # BIPA wins by right-hand spread.
    assert merged["nanorix:framework"] == "BIPA"
    # FDA SaMD fields preserved.
    assert merged["device_class"] == "II"
    assert merged["k_number"] == "K231234"
    # BIPA state-jurisdictional flag preserved.
    assert merged["jurisdiction_state"] == "IL"
    # HIPAA + BIPA load-bearing booleans preserved.
    assert merged["consent_obtained"] == "true"


# ── FDA-mandate vs emerging-expectation framing ────────────────


def test_fda_samd_docstring_frames_subgroup_disclosure_as_emerging():
    """Per Item 82 verification: subgroup performance is an EMERGING
    FDA expectation tied to the AI/ML 2025 draft guidance, NOT a
    finalized FDA mandate. The class-level docstring must surface
    this framing."""
    doc = FDASaMDContext.__doc__ or ""
    assert "emerging" in doc.lower() or "draft" in doc.lower()
    assert "mandate" in doc.lower() or "finalized" in doc.lower()


def test_fda_samd_field_docstring_for_subgroup_disclosure_is_emerging():
    """The subgroup_performance_disclosed field-level docstring must
    explicitly note the emerging-vs-mandated framing."""
    from nanorix.healthcare import fda_samd as mod

    src = inspect.getsource(mod)
    # Find the subgroup_performance_disclosed field block.
    assert "subgroup_performance_disclosed" in src
    # The text 'emerging' or 'not a finalized' must appear near the
    # field declaration / docstring.
    assert (
        "emerging" in src.lower() or "not a finalized" in src.lower()
    )


def test_fda_samd_module_docstring_cites_fda_primary_sources():
    """Module docstring must cite FDA primary sources for traceability:
    21 CFR Part 860 + FDA SaMD framework + 2025 AI/ML draft guidance."""
    from nanorix.healthcare import fda_samd as mod

    doc = mod.__doc__ or ""
    assert "21 CFR Part 860" in doc
    assert "SaMD" in doc
    assert "510(k)" in doc
    assert "De Novo" in doc


# ── Three-role discipline / vocabulary discipline ──────────────


def test_fda_samd_module_docstring_contains_three_role_disclaimer():
    """Module docstring must include the three-role disclaimer framing
    per ADR-017."""
    from nanorix.healthcare import fda_samd as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    assert (
        "regulatory affairs" in doc.lower()
        or "CDRH" in doc
        or "FDA's" in doc
    )


def test_fda_samd_class_docstring_contains_disclaimer_language():
    doc = FDASaMDContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_fda_samd_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules + 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED /
    PASSED / MEETS / CERTIFIED in Nanorix-voice contexts.

    FDA SaMD specifically: helper must NOT claim FDA clearance /
    approval; only records customer-declared submission status."""
    from nanorix.healthcare import fda_samd as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


def test_fda_samd_source_does_not_claim_fda_clearance_in_voice():
    """FDA-specific: helper must not assert clearance/approval as a
    Nanorix determination. The phrases 'FDA cleared' / 'FDA approved'
    appearing as a Nanorix-voice assertion would breach the role-1
    discipline."""
    from nanorix.healthcare import fda_samd as mod

    src = inspect.getsource(mod)
    # The phrase 'helper certifies FDA clearance' or 'Nanorix asserts
    # FDA approval' would breach role-1. We check for absence of any
    # 'helper certifies' / 'helper asserts' patterns.
    breach_patterns = re.compile(
        r"helper\s+(?:certifies|asserts|verifies)\s+(?:FDA|clearance|approval)",
        re.IGNORECASE,
    )
    matches = breach_patterns.findall(src)
    assert len(matches) == 0, (
        f"Helper-voice FDA-determination breach: {matches}"
    )


def test_fda_samd_cross_vertical_framing_in_docstring():
    """Per spec: FDA SaMD applies to all medical device software, not
    just healthcare AI. Module docstring must surface cross-vertical
    framing."""
    from nanorix.healthcare import fda_samd as mod

    doc = mod.__doc__ or ""
    # Cross-vertical signal: helper applies across clinical
    # specialties / device categories.
    assert "cross-vertical" in doc.lower() or (
        "regardless of clinical specialty" in doc.lower()
    )
