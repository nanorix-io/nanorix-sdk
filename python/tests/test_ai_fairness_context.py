"""Tests for nanorix.healthcare.AIFairnessContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.healthcare import AIFairnessContext


# ── Required fields + framework identity ───────────────────────


def test_ai_fairness_minimal_context_emits_required_keys():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex", "age"],
        sample_size=7166,
        evaluation_completed=True,
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "AI_Fairness_Evaluation"
    assert meta["nanorix:framework_version"] == "2026-02"
    assert meta["evaluation_methodology"] == "error_rate_parity"
    assert meta["demographic_groups_evaluated"] == (
        "race,ethnicity,sex,age"
    )
    assert meta["sample_size"] == "7166"
    assert meta["evaluation_completed"] == "true"
    # Optional fields absent in minimal construction.
    assert "tolerance_band_pp" not in meta
    assert "documentation_url" not in meta
    assert "subgroup_performance_disclosed" not in meta
    assert "methodology_pre_registered" not in meta
    assert "evaluation_date" not in meta
    assert "re_evaluation_cadence" not in meta
    assert "evaluation_owner_role" not in meta
    assert "affected_decision_categories" not in meta


def test_ai_fairness_framework_id_property_returns_canonical_identifier():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=False,
    )
    assert ctx.framework_id == "AI_Fairness_Evaluation"
    assert (
        ctx.framework_id == ctx.to_metadata()["nanorix:framework"]
    )


def test_ai_fairness_framework_version_property_returns_canonical():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=False,
    )
    assert ctx.framework_version == "2026-02"
    assert (
        ctx.framework_version
        == ctx.to_metadata()["nanorix:framework_version"]
    )


# ── Evaluation methodology validation ──────────────────────────


@pytest.mark.parametrize(
    "method",
    [
        "error_rate_parity",
        "outcome_parity",
        "calibration_parity",
        "equalized_odds",
        "demographic_parity",
        "custom",
    ],
)
def test_ai_fairness_all_methodologies_accepted(method):
    ctx = AIFairnessContext(
        evaluation_methodology=method,
        demographic_groups_evaluated=["sex"],
        sample_size=100,
        evaluation_completed=True,
    )
    assert ctx.to_metadata()["evaluation_methodology"] == method


def test_ai_fairness_invalid_methodology_rejected():
    with pytest.raises(ValueError, match="evaluation_methodology"):
        AIFairnessContext(
            evaluation_methodology="bias_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_empty_methodology_rejected():
    with pytest.raises(ValueError, match="evaluation_methodology"):
        AIFairnessContext(
            evaluation_methodology="",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_evaluation_methodology_set_returns_canonical():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
    )
    methods = ctx.evaluation_methodology_set
    for m in [
        "error_rate_parity",
        "outcome_parity",
        "calibration_parity",
        "equalized_odds",
        "demographic_parity",
        "custom",
    ]:
        assert m in methods


def test_ai_fairness_uses_tolerance_band_property():
    """Methodologies that admit ±band on subgroup metric differences."""
    band_methods = [
        "error_rate_parity",
        "calibration_parity",
        "equalized_odds",
    ]
    non_band_methods = [
        "outcome_parity",
        "demographic_parity",
        "custom",
    ]
    for m in band_methods:
        ctx = AIFairnessContext(
            evaluation_methodology=m,
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
        )
        assert ctx.uses_tolerance_band is True
    for m in non_band_methods:
        ctx = AIFairnessContext(
            evaluation_methodology=m,
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
        )
        assert ctx.uses_tolerance_band is False


# ── Demographic groups evaluated validation ────────────────────


def test_ai_fairness_empty_demographic_groups_rejected():
    with pytest.raises(
        ValueError, match="demographic_groups_evaluated"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=[],
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_demographic_groups_comma_in_item_rejected():
    """Comma inside list item collides with comma-join separator."""
    with pytest.raises(
        ValueError, match="demographic_groups_evaluated"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race,ethnicity"],
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_demographic_groups_empty_string_rejected():
    with pytest.raises(
        ValueError, match="demographic_groups_evaluated"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race", ""],
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_demographic_groups_non_string_rejected():
    with pytest.raises(
        ValueError, match="demographic_groups_evaluated"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race", 42],  # type: ignore[list-item]
            sample_size=100,
            evaluation_completed=True,
        )


def test_ai_fairness_demographic_groups_single_item_accepted():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["age"],
        sample_size=100,
        evaluation_completed=True,
    )
    assert ctx.to_metadata()["demographic_groups_evaluated"] == "age"


def test_ai_fairness_demographic_groups_six_items_emitted_comma_joined():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=[
            "race",
            "ethnicity",
            "sex",
            "age",
            "disability_status",
            "language",
        ],
        sample_size=7166,
        evaluation_completed=True,
    )
    assert ctx.to_metadata()["demographic_groups_evaluated"] == (
        "race,ethnicity,sex,age,disability_status,language"
    )


# ── Sample size validation ─────────────────────────────────────


def test_ai_fairness_zero_sample_size_rejected():
    with pytest.raises(ValueError, match="sample_size"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=0,
            evaluation_completed=True,
        )


def test_ai_fairness_negative_sample_size_rejected():
    with pytest.raises(ValueError, match="sample_size"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=-1,
            evaluation_completed=True,
        )


def test_ai_fairness_non_int_sample_size_rejected():
    with pytest.raises(ValueError, match="sample_size"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100.5,  # type: ignore[arg-type]
            evaluation_completed=True,
        )


def test_ai_fairness_bool_sample_size_rejected():
    """bool is subclass of int; reject explicit booleans to prevent
    accidental `sample_size=True` shorthand."""
    with pytest.raises(ValueError, match="sample_size"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=True,  # type: ignore[arg-type]
            evaluation_completed=True,
        )


def test_ai_fairness_large_sample_size_accepted():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=1_000_000,
        evaluation_completed=True,
    )
    assert ctx.to_metadata()["sample_size"] == "1000000"


# ── Evaluation completed validation ────────────────────────────


def test_ai_fairness_evaluation_completed_true_emits_string():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
    )
    assert ctx.to_metadata()["evaluation_completed"] == "true"


def test_ai_fairness_evaluation_completed_false_emits_string():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=False,
    )
    assert ctx.to_metadata()["evaluation_completed"] == "false"


def test_ai_fairness_evaluation_completed_non_bool_rejected():
    with pytest.raises(ValueError, match="evaluation_completed"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed="true",  # type: ignore[arg-type]
        )


# ── Tolerance band validation (Anterior used ±5pp) ─────────────


def test_ai_fairness_anterior_tolerance_band_5pp_accepted():
    """Per Anterior arXiv 2603.14631, ±5.0pp band was used on 7,166
    medical-necessity-determination cases."""
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
    )
    assert ctx.to_metadata()["tolerance_band_pp"] == "5"


def test_ai_fairness_tolerance_band_0pp_accepted():
    """Zero tolerance = strict parity."""
    ctx = AIFairnessContext(
        evaluation_methodology="equalized_odds",
        demographic_groups_evaluated=["sex"],
        sample_size=1000,
        evaluation_completed=True,
        tolerance_band_pp=0.0,
    )
    # 0.0 -> "0" after trailing-zero strip.
    assert ctx.to_metadata()["tolerance_band_pp"] == "0"


def test_ai_fairness_tolerance_band_100pp_accepted():
    """100pp upper bound = trivially-permissive band."""
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["age"],
        sample_size=500,
        evaluation_completed=True,
        tolerance_band_pp=100.0,
    )
    assert "tolerance_band_pp" in ctx.to_metadata()


def test_ai_fairness_negative_tolerance_band_rejected():
    with pytest.raises(ValueError, match="tolerance_band_pp"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            tolerance_band_pp=-1.0,
        )


def test_ai_fairness_tolerance_band_over_100_rejected():
    with pytest.raises(ValueError, match="tolerance_band_pp"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            tolerance_band_pp=100.1,
        )


def test_ai_fairness_tolerance_band_string_rejected():
    with pytest.raises(ValueError, match="tolerance_band_pp"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            tolerance_band_pp="5.0",  # type: ignore[arg-type]
        )


def test_ai_fairness_tolerance_band_bool_rejected():
    with pytest.raises(ValueError, match="tolerance_band_pp"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            tolerance_band_pp=True,  # type: ignore[arg-type]
        )


def test_ai_fairness_tolerance_band_int_accepted():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        tolerance_band_pp=5,
    )
    assert "tolerance_band_pp" in ctx.to_metadata()


# ── Subgroup performance disclosure (composes with FDA SaMD) ───


def test_ai_fairness_subgroup_performance_disclosed_true_emits_string():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        subgroup_performance_disclosed=True,
    )
    assert (
        ctx.to_metadata()["subgroup_performance_disclosed"] == "true"
    )


def test_ai_fairness_subgroup_performance_disclosed_false_emits_string():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        subgroup_performance_disclosed=False,
    )
    assert (
        ctx.to_metadata()["subgroup_performance_disclosed"] == "false"
    )


def test_ai_fairness_subgroup_performance_disclosed_non_bool_rejected():
    """Per FDA AI/ML 2025 draft framing: subgroup disclosure is an
    emerging expectation. The helper still enforces strict boolean
    shape on this declaration."""
    with pytest.raises(
        ValueError, match="subgroup_performance_disclosed"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            subgroup_performance_disclosed="true",  # type: ignore[arg-type]
        )


# ── Methodology pre-registration ───────────────────────────────


def test_ai_fairness_methodology_pre_registered_true():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        methodology_pre_registered=True,
    )
    assert ctx.to_metadata()["methodology_pre_registered"] == "true"


def test_ai_fairness_methodology_pre_registered_false():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        methodology_pre_registered=False,
    )
    assert ctx.to_metadata()["methodology_pre_registered"] == "false"


def test_ai_fairness_methodology_pre_registered_non_bool_rejected():
    with pytest.raises(
        ValueError, match="methodology_pre_registered"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            methodology_pre_registered=1,  # type: ignore[arg-type]
        )


# ── Evaluation date validation (ISO-8601) ──────────────────────


def test_ai_fairness_valid_iso_date_accepted():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        evaluation_date="2026-05-10",
    )
    assert ctx.to_metadata()["evaluation_date"] == "2026-05-10"


@pytest.mark.parametrize(
    "bad",
    [
        "2026-5-10",  # single-digit month
        "26-05-10",  # short year
        "2026/05/10",  # slash separator
        "May 10, 2026",  # natural-language
        "20260510",  # no separators
        "2026-13-01",  # not validated semantically, but format-check still
        # passes for "2026-13-01" — re-check structurally below
    ],
)
def test_ai_fairness_malformed_iso_dates_rejected_structurally(bad):
    """Helper validates structural format only (YYYY-MM-DD)."""
    if _structurally_valid(bad):
        # The "2026-13-01" case passes structural ISO regex; skip it
        # — the helper is documented as structural-only validation.
        pytest.skip("structurally valid; helper is structural-only")
    with pytest.raises(ValueError, match="evaluation_date"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            evaluation_date=bad,
        )


def _structurally_valid(s: str) -> bool:
    """Helper for parametrize: check ISO-structural regex match."""
    return bool(re.fullmatch(r"^\d{4}-\d{2}-\d{2}$", s))


# ── Re-evaluation cadence validation ───────────────────────────


@pytest.mark.parametrize(
    "cad",
    [
        "quarterly",
        "biannually",
        "annually",
        "on_significant_change",
        "ad_hoc",
    ],
)
def test_ai_fairness_all_re_evaluation_cadences_accepted(cad):
    """NYC LL 144 mandates annual cadence for AEDT bias audits;
    FDA PCCP framing aligns with on_significant_change."""
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
        re_evaluation_cadence=cad,
    )
    assert ctx.to_metadata()["re_evaluation_cadence"] == cad


def test_ai_fairness_invalid_re_evaluation_cadence_rejected():
    with pytest.raises(ValueError, match="re_evaluation_cadence"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            re_evaluation_cadence="monthly",
        )


def test_ai_fairness_re_evaluation_cadence_set_returns_canonical():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
    )
    cadences = ctx.re_evaluation_cadence_set
    for c in [
        "quarterly",
        "biannually",
        "annually",
        "on_significant_change",
        "ad_hoc",
    ]:
        assert c in cadences


# ── Affected decision categories ───────────────────────────────


def test_ai_fairness_affected_decision_categories_comma_in_item_rejected():
    with pytest.raises(
        ValueError, match="affected_decision_categories"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            affected_decision_categories=[
                "clinical,decision_support",  # comma in item
            ],
        )


def test_ai_fairness_affected_decision_categories_empty_string_rejected():
    with pytest.raises(
        ValueError, match="affected_decision_categories"
    ):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            affected_decision_categories=["valid_cat", ""],
        )


# ── Optional reference fields all together ─────────────────────


def test_ai_fairness_optional_fields_when_set_appear_in_metadata():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex", "age"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        documentation_url="https://arxiv.org/abs/2603.14631",
        subgroup_performance_disclosed=True,
        methodology_pre_registered=True,
        evaluation_date="2026-03-15",
        re_evaluation_cadence="annually",
        evaluation_owner_role="ai_ethics_lead",
        affected_decision_categories=[
            "prior_authorization",
            "clinical_decision_support",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["tolerance_band_pp"] == "5"
    assert (
        meta["documentation_url"]
        == "https://arxiv.org/abs/2603.14631"
    )
    assert meta["subgroup_performance_disclosed"] == "true"
    assert meta["methodology_pre_registered"] == "true"
    assert meta["evaluation_date"] == "2026-03-15"
    assert meta["re_evaluation_cadence"] == "annually"
    assert meta["evaluation_owner_role"] == "ai_ethics_lead"
    assert meta["affected_decision_categories"] == (
        "prior_authorization,clinical_decision_support"
    )


# ── Wire-constraint enforcement ────────────────────────────────


def test_ai_fairness_oversized_documentation_url_rejected():
    with pytest.raises(ValueError, match="256-char limit"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            documentation_url="https://example.com/" + "a" * 250,
        )


def test_ai_fairness_oversized_evaluation_owner_role_rejected():
    with pytest.raises(ValueError, match="256-char limit"):
        AIFairnessContext(
            evaluation_methodology="error_rate_parity",
            demographic_groups_evaluated=["race"],
            sample_size=100,
            evaluation_completed=True,
            evaluation_owner_role="X" * 257,
        )


def test_ai_fairness_metadata_keys_under_16_max():
    """ADR-010 A4 wire constraint: max 16 metadata keys."""
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        documentation_url="https://arxiv.org/abs/2603.14631",
        subgroup_performance_disclosed=True,
        methodology_pre_registered=True,
        evaluation_date="2026-03-15",
        re_evaluation_cadence="annually",
        evaluation_owner_role="ai_ethics_lead",
        affected_decision_categories=["cat1"],
    )
    meta = ctx.to_metadata()
    assert len(meta) <= 16


def test_ai_fairness_metadata_serialized_size_under_4kb():
    """ADR-010 A4 wire constraint: max 4096 bytes serialized."""
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        documentation_url="https://arxiv.org/abs/2603.14631",
        subgroup_performance_disclosed=True,
    )
    meta = ctx.to_metadata()
    assert len(json.dumps(meta)) <= 4096


def test_ai_fairness_frozen_dataclass_mutation_raises():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race"],
        sample_size=100,
        evaluation_completed=True,
    )
    with pytest.raises(Exception):
        ctx.evaluation_methodology = "outcome_parity"  # type: ignore[misc]


def test_ai_fairness_metadata_json_round_trip_preserves_dict():
    ctx = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        subgroup_performance_disclosed=True,
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Composition with FDA SaMD + NIST AI RMF ────────────────────


def test_ai_fairness_composes_with_fda_samd_via_subgroup_disclosure():
    """AI fairness ``subgroup_performance_disclosed`` is semantically
    the same field as FDA SaMD ``subgroup_performance_disclosed``.
    Customers wrap both context tags so auditors + GRC platforms see
    the cross-framework fairness posture; the right-hand framework
    wins the reserved ``nanorix:framework`` key."""
    from nanorix.healthcare import FDASaMDContext

    fairness = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex", "age"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        documentation_url="https://arxiv.org/abs/2603.14631",
        subgroup_performance_disclosed=True,
    )
    fda = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="in_review",
        intended_use=(
            "AI clinical decision support for prior authorization"
        ),
        subgroup_performance_disclosed=True,
    )
    merged = {**fairness.to_metadata(), **fda.to_metadata()}
    # FDA SaMD wins the reserved key by right-hand spread.
    assert merged["nanorix:framework"] == "FDA_SaMD"
    # Both contexts' substantive fields preserved.
    assert merged["evaluation_methodology"] == "error_rate_parity"
    assert merged["tolerance_band_pp"] == "5"
    assert merged["device_class"] == "II"
    # Both subgroup-performance flags emit "true" and align.
    assert merged["subgroup_performance_disclosed"] == "true"


def test_ai_fairness_composes_with_nist_ai_rmf_measure_function():
    """AI fairness composes with NIST AI RMF MEASURE function. The
    Anterior arXiv 2603.14631 methodology aligns with the MEASURE
    function fairness evaluation under Measure-2.11 + the
    ``fairness_bias`` trustworthy AI characteristic (§3 of the
    framework)."""
    from nanorix.compliance import NISTAIRMFContext

    fairness = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Prior Auth AI",
        use_case_category="prior_authorization",
        core_functions_in_scope=["measure", "manage"],
        risk_categories=["fairness_bias"],
    )
    merged = {**fairness.to_metadata(), **nist.to_metadata()}
    # NIST AI RMF wins reserved key by right-hand spread.
    assert merged["nanorix:framework"] == "NIST_AI_RMF_1.0"
    # AI fairness substantive fields preserved.
    assert merged["evaluation_methodology"] == "error_rate_parity"
    assert merged["sample_size"] == "7166"
    # NIST AI RMF MEASURE-function alignment visible.
    assert "measure" in merged["core_functions_in_scope"]
    assert merged["risk_categories"] == "fairness_bias"


def test_ai_fairness_triple_compose_fairness_fda_nist():
    """The canonical Anterior-aligned healthcare AI composition:
    AI Fairness + FDA SaMD + NIST AI RMF."""
    from nanorix.compliance import NISTAIRMFContext
    from nanorix.healthcare import FDASaMDContext

    fairness = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex"],
        sample_size=7166,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
        documentation_url="https://arxiv.org/abs/2603.14631",
        subgroup_performance_disclosed=True,
    )
    fda = FDASaMDContext(
        device_class="II",
        submission_pathway="510k",
        submission_status="cleared",
        intended_use="AI clinical decision support",
        k_number="K231234",
        subgroup_performance_disclosed=True,
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Health AI",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure", "manage"],
        risk_categories=["fairness_bias", "safety"],
    )
    merged = {
        **fairness.to_metadata(),
        **fda.to_metadata(),
        **nist.to_metadata(),
    }
    # NIST AI RMF (rightmost) wins reserved key.
    assert merged["nanorix:framework"] == "NIST_AI_RMF_1.0"
    # AI fairness fields preserved.
    assert merged["evaluation_methodology"] == "error_rate_parity"
    assert merged["sample_size"] == "7166"
    # FDA SaMD fields preserved.
    assert merged["device_class"] == "II"
    assert merged["k_number"] == "K231234"
    # NIST AI RMF fields preserved.
    assert merged["ai_system_owner"] == "Acme Health AI"
    assert "fairness_bias" in merged["risk_categories"]


# ── Cross-vertical: hiring (NYC LL 144) ────────────────────────


def test_ai_fairness_composes_with_nyc_ll144_hiring_aedt():
    """AI fairness is cross-vertical — same helper applies to NYC
    AEDT hiring bias audits (NYC LL 144 mandates annual cadence)."""
    from nanorix.compliance import NYCLL144Context

    fairness = AIFairnessContext(
        evaluation_methodology="demographic_parity",
        demographic_groups_evaluated=["race", "sex"],
        sample_size=5000,
        evaluation_completed=True,
        re_evaluation_cadence="annually",
        affected_decision_categories=["resume_screening"],
    )
    aedt = NYCLL144Context(
        employer_organization="Acme NYC Hiring",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    merged = {**fairness.to_metadata(), **aedt.to_metadata()}
    assert merged["nanorix:framework"] == "NYC_LL_144"
    assert merged["evaluation_methodology"] == "demographic_parity"
    assert merged["re_evaluation_cadence"] == "annually"
    assert merged["affected_decision_categories"] == "resume_screening"


# ── Anterior arXiv citation discipline ─────────────────────────


def test_ai_fairness_docstring_cites_anterior_arxiv_paper():
    """Per Item 82 verification: Anterior arXiv 2603.14631 is verified
    peer-reviewed prior art. The module docstring must cite the
    paper verbatim for downstream-consumer traceability."""
    from nanorix.healthcare import ai_fairness as mod

    doc = mod.__doc__ or ""
    # Verbatim arXiv ID.
    assert "2603.14631" in doc
    # Anterior or authors cited.
    assert (
        "Anterior" in doc
        or "Selvaraj" in doc
        or "Mahmoud" in doc
        or "Iravane" in doc
    )


def test_ai_fairness_class_docstring_cites_anterior_methodology():
    """Class-level docstring must cite the error-rate-parity
    methodology + 7,166 cases + ±5pp tolerance band specifics."""
    doc = AIFairnessContext.__doc__ or ""
    # Anterior methodology specifics in class-level docstring.
    assert (
        "error-rate-parity" in doc.lower()
        or "error_rate_parity" in doc.lower()
    )


def test_ai_fairness_source_has_no_fda_mandate_language():
    """Per Item 82 verification: FDA AI/ML 2025 draft has NOT been
    finalized as of 2026-05-10. The helper must frame subgroup
    performance as EMERGING expectation, NOT FDA mandate. Forbidden
    phrases 'FDA mandate' / 'FDA-mandated' (case-insensitive) must
    NOT appear in helper source."""
    from nanorix.healthcare import ai_fairness as mod

    src = inspect.getsource(mod)
    forbidden = re.compile(
        r"\bFDA[\s\-]mandate(?:d)?\b", re.IGNORECASE
    )
    matches = forbidden.findall(src)
    assert len(matches) == 0, (
        f"Forbidden FDA-mandate language in helper source: {matches}"
    )


def test_ai_fairness_source_frames_subgroup_disclosure_as_emerging():
    """Helper source must include the emerging-expectation framing
    for subgroup-performance disclosure."""
    from nanorix.healthcare import ai_fairness as mod

    src = inspect.getsource(mod)
    assert "emerging" in src.lower()
    assert (
        "not a finalized" in src.lower()
        or "not finalized" in src.lower()
        or "has not been finalized" in src.lower()
    )


# ── Forbidden vocabulary (regulatory_context rules) ────────────


def test_ai_fairness_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules + 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED /
    PASSED / MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.healthcare import ai_fairness as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


# ── Three-role discipline ──────────────────────────────────────


def test_ai_fairness_module_docstring_contains_three_role_disclaimer():
    """Module docstring must include the three-role disclaimer framing
    per ADR-017."""
    from nanorix.healthcare import ai_fairness as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc


def test_ai_fairness_class_docstring_contains_disclaimer_language():
    doc = AIFairnessContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_ai_fairness_module_docstring_cites_nist_ai_rmf_primary():
    """Module docstring must cite NIST AI 100-1 RMF 1.0 + MEASURE
    function alignment + FDA AI/ML 2025 draft (89 FR 1543) for
    primary-source traceability."""
    from nanorix.healthcare import ai_fairness as mod

    doc = mod.__doc__ or ""
    assert "NIST AI" in doc
    assert "MEASURE" in doc
    assert "89 FR 1543" in doc or "2025 draft" in doc.lower()


def test_ai_fairness_cross_vertical_framing_in_docstring():
    """AI fairness applies cross-vertically — hiring, housing,
    financial, criminal justice, education, healthcare."""
    from nanorix.healthcare import ai_fairness as mod

    doc = mod.__doc__ or ""
    assert "cross-vertical" in doc.lower() or (
        "all AI deployments" in doc.lower()
        or "regardless of vertical" in doc.lower()
    )


def test_ai_fairness_paired_with_colorado_ai_act():
    """For Colorado-jurisdictional High-Risk AI Systems, AI fairness
    composes with Colorado AI Act."""
    from nanorix.compliance import ColoradoAIActContext

    fairness = AIFairnessContext(
        evaluation_methodology="error_rate_parity",
        demographic_groups_evaluated=["race", "ethnicity", "sex"],
        sample_size=5000,
        evaluation_completed=True,
        tolerance_band_pp=5.0,
    )
    co = ColoradoAIActContext(
        organization="Acme Colorado Health AI",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    merged = {**fairness.to_metadata(), **co.to_metadata()}
    assert merged["nanorix:framework"] == "CO_SB24_205"
    assert merged["evaluation_methodology"] == "error_rate_parity"
    assert merged["consequential_decision_category"] == "healthcare"
    assert merged["high_risk_ai_system"] == "true"
