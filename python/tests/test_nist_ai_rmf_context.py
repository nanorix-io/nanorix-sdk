"""Tests for nanorix.compliance.NISTAIRMFContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import NISTAIRMFContext


# ── Required fields + framework identity ───────────────────────


def test_nist_ai_rmf_minimal_context_emits_required_keys():
    ctx = NISTAIRMFContext(
        ai_system_owner="Acme Health AI",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["govern", "measure"],
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "NIST_AI_RMF_1.0"
    assert meta["nanorix:framework_version"] == "1.0"
    assert meta["ai_system_owner"] == "Acme Health AI"
    assert meta["use_case_category"] == "clinical-decision-support"
    assert meta["core_functions_in_scope"] == "govern,measure"


def test_nist_ai_rmf_framework_id_property_returns_canonical_identifier():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["map"],
    )
    assert ctx.framework_id == "NIST_AI_RMF_1.0"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_nist_ai_rmf_core_functions_property_returns_four_canonical():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["govern"],
    )
    funcs = ctx.core_functions
    assert funcs == ["govern", "map", "measure", "manage"]


def test_nist_ai_rmf_trustworthy_ai_characteristics_property_returns_seven():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["measure"],
    )
    characteristics = ctx.trustworthy_ai_characteristics
    assert len(characteristics) == 7
    # Key trustworthy AI characteristics present.
    assert "fairness_bias" in characteristics
    assert "validity_reliability" in characteristics
    assert "privacy" in characteristics
    assert "safety" in characteristics


# ── Core functions validation ───────────────────────────────────


@pytest.mark.parametrize(
    "func", ["govern", "map", "measure", "manage"]
)
def test_nist_ai_rmf_all_four_core_functions_accepted(func):
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=[func],
    )
    assert ctx.to_metadata()["core_functions_in_scope"] == func


def test_nist_ai_rmf_all_four_core_functions_in_combination():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["govern", "map", "measure", "manage"],
    )
    assert (
        ctx.to_metadata()["core_functions_in_scope"]
        == "govern,map,measure,manage"
    )


def test_nist_ai_rmf_empty_core_functions_rejected():
    with pytest.raises(ValueError, match="core_functions_in_scope"):
        NISTAIRMFContext(
            ai_system_owner="X",
            use_case_category="X",
            core_functions_in_scope=[],
        )


def test_nist_ai_rmf_invalid_core_function_rejected():
    with pytest.raises(ValueError, match="core_functions_in_scope"):
        NISTAIRMFContext(
            ai_system_owner="X",
            use_case_category="X",
            core_functions_in_scope=["govern", "orchestrate"],  # invalid
        )


# ── Risk categories validation ──────────────────────────────────


def test_nist_ai_rmf_risk_categories_optional():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["govern"],
    )
    assert "risk_categories" not in ctx.to_metadata()


def test_nist_ai_rmf_risk_categories_when_set_appear_comma_joined():
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["measure"],
        risk_categories=["fairness_bias", "privacy"],
    )
    meta = ctx.to_metadata()
    assert meta["risk_categories"] == "fairness_bias,privacy"


def test_nist_ai_rmf_invalid_risk_category_rejected():
    with pytest.raises(ValueError, match="risk_categories"):
        NISTAIRMFContext(
            ai_system_owner="X",
            use_case_category="X",
            core_functions_in_scope=["map"],
            risk_categories=["fairness_bias", "vibes_based_safety"],
        )


# ── Lifecycle stage validation ──────────────────────────────────


@pytest.mark.parametrize(
    "stage",
    [
        "plan_design",
        "data_collection",
        "build_model",
        "verify_validate",
        "deploy_use",
        "operate_monitor",
    ],
)
def test_nist_ai_rmf_all_lifecycle_stages_accepted(stage):
    ctx = NISTAIRMFContext(
        ai_system_owner="X",
        use_case_category="X",
        core_functions_in_scope=["map"],
        lifecycle_stage=stage,
    )
    assert ctx.to_metadata()["lifecycle_stage"] == stage


def test_nist_ai_rmf_invalid_lifecycle_stage_rejected():
    with pytest.raises(ValueError, match="lifecycle_stage"):
        NISTAIRMFContext(
            ai_system_owner="X",
            use_case_category="X",
            core_functions_in_scope=["map"],
            lifecycle_stage="ship_it",  # not in canonical set
        )


# ── Empty required-field rejection ──────────────────────────────


def test_nist_ai_rmf_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="ai_system_owner"):
        NISTAIRMFContext(
            ai_system_owner="",
            use_case_category="X",
            core_functions_in_scope=["govern"],
        )
    with pytest.raises(ValueError, match="use_case_category"):
        NISTAIRMFContext(
            ai_system_owner="X",
            use_case_category="",
            core_functions_in_scope=["govern"],
        )


# ── Optional fields ─────────────────────────────────────────────


def test_nist_ai_rmf_optional_fields_when_set_appear_in_metadata():
    ctx = NISTAIRMFContext(
        ai_system_owner="Acme Health AI",
        use_case_category="medical-imaging",
        core_functions_in_scope=["map", "measure", "manage"],
        risk_categories=["fairness_bias"],
        lifecycle_stage="deploy_use",
        risk_assessment_reference="AIRA-2026-Q2-001",
        model_card_reference="model-card-radiology-v3",
        subgroup_evaluation_reference="fairness-eval-2026-Q1",
    )
    meta = ctx.to_metadata()
    assert meta["risk_assessment_reference"] == "AIRA-2026-Q2-001"
    assert meta["model_card_reference"] == "model-card-radiology-v3"
    assert (
        meta["subgroup_evaluation_reference"]
        == "fairness-eval-2026-Q1"
    )


# ── Wire-constraint enforcement ─────────────────────────────────


def test_nist_ai_rmf_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        NISTAIRMFContext(
            ai_system_owner="A" * 257,
            use_case_category="X",
            core_functions_in_scope=["govern"],
        )


def test_nist_ai_rmf_frozen_dataclass_mutation_raises():
    ctx = NISTAIRMFContext(
        ai_system_owner="A",
        use_case_category="X",
        core_functions_in_scope=["govern"],
    )
    with pytest.raises(Exception):
        ctx.ai_system_owner = "B"  # type: ignore[misc]


def test_nist_ai_rmf_metadata_json_round_trip_preserves_dict():
    ctx = NISTAIRMFContext(
        ai_system_owner="Acme",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure"],
        risk_categories=["fairness_bias"],
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Three-role + factual-language discipline ────────────────────


def test_nist_ai_rmf_module_docstring_contains_disclaimer_language():
    from nanorix.compliance import nist_ai_rmf as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # Reframe — emerging FDA expectation, NOT mandate.
    assert "voluntary" in doc.lower()


def test_nist_ai_rmf_module_avoids_fda_mandate_claim():
    """Per industry-benchmark verification 2026-05-10:
    NIST AI RMF + emerging FDA pre-market subgroup-performance
    expectation — NOT 'FDA mandate' until draft finalizes."""
    from nanorix.compliance import nist_ai_rmf as mod

    doc = mod.__doc__ or ""
    # Must not assert FDA mandate.
    assert "FDA mandate" not in doc
    assert "FDA-mandated" not in doc


def test_nist_ai_rmf_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules: no assertive
    COMPLIANT / SATISFIED / PASSED / MEETS / CERTIFIED in helper
    source."""
    from nanorix.compliance import nist_ai_rmf as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
