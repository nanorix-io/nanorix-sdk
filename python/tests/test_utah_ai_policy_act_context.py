"""Tests for nanorix.compliance.UtahAIPolicyActContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import UtahAIPolicyActContext


# ── Required fields + framework identity ───────────────────────


def test_utah_ai_policy_act_minimal_context_emits_required_keys():
    ctx = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "UT_SB_149"
    assert meta["nanorix:framework_version"] == "2024-operative"
    assert meta["organization"] == "Acme Utah Health AI"
    assert meta["actor_role"] == "regulated_occupation_provider"
    assert meta["generative_ai_in_use"] == "true"
    assert meta["consumer_disclosure_provided"] == "true"
    assert meta["regulated_occupation_disclosure_provided"] == "true"
    assert meta["generative_ai_use_case"] == "patient_engagement"
    # Always-emitted state-jurisdiction tag.
    assert meta["jurisdiction_state"] == "UT"
    # Optional fields absent in minimal construction.
    assert "regulated_occupation" not in meta
    assert "affected_consumer_categories" not in meta
    assert "consumer_disclosure_reference" not in meta
    assert "regulated_occupation_disclosure_reference" not in meta
    assert "ai_learning_lab_reference" not in meta
    assert "workforce_member_role" not in meta


def test_utah_ai_policy_act_framework_id_property_returns_canonical_identifier():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    assert ctx.framework_id == "UT_SB_149"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_utah_ai_policy_act_jurisdiction_state_property_returns_UT():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    assert ctx.jurisdiction_state == "UT"
    assert ctx.jurisdiction_state == ctx.to_metadata()["jurisdiction_state"]


def test_utah_ai_policy_act_jurisdiction_state_always_in_metadata():
    """Utah AI Policy Act is by construction a Utah-jurisdictional
    helper. The structural jurisdiction_state="UT" key must always
    emit."""
    for use_case in [
        "consumer_facing_chatbot",
        "clinical_decision_support",
        "patient_engagement",
        "not_generative_ai",
    ]:
        ctx = UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=use_case != "not_generative_ai",
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case=use_case,
        )
        assert ctx.to_metadata()["jurisdiction_state"] == "UT"


def test_utah_ai_policy_act_generative_ai_use_case_set_property_returns_taxonomy():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    use_cases = ctx.generative_ai_use_case_set
    # Consumer-facing categories.
    assert "consumer_facing_chatbot" in use_cases
    assert "customer_service" in use_cases
    # Healthcare-adjacent categories.
    assert "clinical_decision_support" in use_cases
    assert "patient_engagement" in use_cases
    assert "healthcare_administrative" in use_cases
    # Content / code categories.
    assert "content_generation" in use_cases
    assert "code_generation" in use_cases
    assert "internal_analytics" in use_cases
    # Escape hatches.
    assert "not_generative_ai" in use_cases
    assert "other" in use_cases


def test_utah_ai_policy_act_actor_role_set_property_returns_taxonomy():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    roles = ctx.actor_role_set
    assert "generative_ai_provider" in roles
    assert "generative_ai_user" in roles
    assert "regulated_occupation_provider" in roles
    assert "ai_learning_lab_participant" in roles


# ── Actor role validation ──────────────────────────────────────


@pytest.mark.parametrize(
    "role",
    [
        "generative_ai_provider",
        "generative_ai_user",
        "regulated_occupation_provider",
        "ai_learning_lab_participant",
    ],
)
def test_utah_ai_policy_act_all_actor_roles_accepted(role):
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role=role,
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="consumer_facing_chatbot",
    )
    assert ctx.to_metadata()["actor_role"] == role


def test_utah_ai_policy_act_invalid_actor_role_rejected():
    with pytest.raises(ValueError, match="actor_role"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="model_trainer",  # not Utah-AI-Policy-Act-aligned
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="not_generative_ai",
        )


# ── Generative AI use-case validation ──────────────────────────


@pytest.mark.parametrize(
    "use_case",
    [
        "consumer_facing_chatbot",
        "clinical_decision_support",
        "patient_engagement",
        "healthcare_administrative",
        "customer_service",
        "content_generation",
        "code_generation",
        "internal_analytics",
        "not_generative_ai",
        "other",
    ],
)
def test_utah_ai_policy_act_all_use_cases_accepted(use_case):
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=use_case != "not_generative_ai",
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case=use_case,
    )
    assert ctx.to_metadata()["generative_ai_use_case"] == use_case


def test_utah_ai_policy_act_invalid_use_case_rejected():
    with pytest.raises(ValueError, match="generative_ai_use_case"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="autonomous_driving",  # not in taxonomy
        )


# ── Required-boolean enforcement ───────────────────────────────


def test_utah_ai_policy_act_required_booleans_emit_string_true_false():
    """Utah AI Policy Act has three load-bearing booleans tied
    directly to §§ 13-72-202 + 13-72-203 statutory obligations:
    generative-AI scope, consumer-disclosure provision, regulated-
    occupation-disclosure provision."""
    ctx_all_true = UtahAIPolicyActContext(
        organization="X",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
    )
    meta_true = ctx_all_true.to_metadata()
    assert meta_true["generative_ai_in_use"] == "true"
    assert meta_true["consumer_disclosure_provided"] == "true"
    assert meta_true["regulated_occupation_disclosure_provided"] == "true"

    ctx_all_false = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    meta_false = ctx_all_false.to_metadata()
    assert meta_false["generative_ai_in_use"] == "false"
    assert meta_false["consumer_disclosure_provided"] == "false"
    assert (
        meta_false["regulated_occupation_disclosure_provided"] == "false"
    )


def test_utah_ai_policy_act_generative_ai_in_use_non_bool_rejected():
    with pytest.raises(ValueError, match="generative_ai_in_use"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use="true",  # type: ignore[arg-type]
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="consumer_facing_chatbot",
        )


def test_utah_ai_policy_act_consumer_disclosure_provided_non_bool_rejected():
    with pytest.raises(
        ValueError, match="consumer_disclosure_provided"
    ):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=True,
            consumer_disclosure_provided="true",  # type: ignore[arg-type]
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="consumer_facing_chatbot",
        )


def test_utah_ai_policy_act_regulated_occupation_disclosure_provided_non_bool_rejected():
    with pytest.raises(
        ValueError, match="regulated_occupation_disclosure_provided"
    ):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="regulated_occupation_provider",
            generative_ai_in_use=True,
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided="true",  # type: ignore[arg-type]
            generative_ai_use_case="clinical_decision_support",
        )


def test_utah_ai_policy_act_required_booleans_enforce_bool_shape_not_truthy():
    """Utah-AI-Policy-Act-specific test: §§ 13-72-202 + 13-72-203
    discipline. Customers must NOT pass truthy non-bool values."""
    with pytest.raises(ValueError, match="generative_ai_in_use"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=1,  # type: ignore[arg-type]
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="consumer_facing_chatbot",
        )
    with pytest.raises(
        ValueError, match="consumer_disclosure_provided"
    ):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=True,
            consumer_disclosure_provided=1,  # type: ignore[arg-type]
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="consumer_facing_chatbot",
        )


# ── Empty required-field rejection ─────────────────────────────


def test_utah_ai_policy_act_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="organization"):
        UtahAIPolicyActContext(
            organization="",
            actor_role="generative_ai_user",
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="not_generative_ai",
        )
    with pytest.raises(ValueError, match="actor_role"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="",
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="not_generative_ai",
        )
    with pytest.raises(ValueError, match="generative_ai_use_case"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="generative_ai_user",
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="",
        )


# ── Regulated occupation (optional) ────────────────────────────


def test_utah_ai_policy_act_regulated_occupation_optional():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="consumer_facing_chatbot",
    )
    assert "regulated_occupation" not in ctx.to_metadata()


def test_utah_ai_policy_act_regulated_occupation_when_set_appears():
    ctx = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="clinical_decision_support",
        regulated_occupation="healthcare_physician",
    )
    assert ctx.to_metadata()["regulated_occupation"] == (
        "healthcare_physician"
    )


def test_utah_ai_policy_act_regulated_occupation_empty_rejected():
    with pytest.raises(ValueError, match="regulated_occupation"):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="regulated_occupation_provider",
            generative_ai_in_use=True,
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="clinical_decision_support",
            regulated_occupation="",  # empty string
        )


# ── Affected consumer categories ───────────────────────────────


def test_utah_ai_policy_act_affected_consumer_categories_optional():
    ctx = UtahAIPolicyActContext(
        organization="X",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    assert "affected_consumer_categories" not in ctx.to_metadata()


def test_utah_ai_policy_act_affected_consumer_categories_comma_joined_when_present():
    ctx = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
        affected_consumer_categories=[
            "utah_residents",
            "utah_patients",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["affected_consumer_categories"] == (
        "utah_residents,utah_patients"
    )


def test_utah_ai_policy_act_affected_consumer_categories_comma_in_item_rejected():
    with pytest.raises(
        ValueError, match="affected_consumer_categories"
    ):
        UtahAIPolicyActContext(
            organization="X",
            actor_role="regulated_occupation_provider",
            generative_ai_in_use=True,
            consumer_disclosure_provided=True,
            regulated_occupation_disclosure_provided=True,
            generative_ai_use_case="patient_engagement",
            affected_consumer_categories=[
                "utah_residents,senior",  # comma in item
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_utah_ai_policy_act_optional_fields_when_set_appear_in_metadata():
    ctx = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
        regulated_occupation="healthcare_physician",
        affected_consumer_categories=["utah_residents"],
        consumer_disclosure_reference="disclosure-2026",
        regulated_occupation_disclosure_reference="reg-disclosure-2026",
        ai_learning_lab_reference="ai-lab-program-2026-001",
        workforce_member_role="patient_engagement_engineer",
    )
    meta = ctx.to_metadata()
    assert meta["regulated_occupation"] == "healthcare_physician"
    assert meta["consumer_disclosure_reference"] == "disclosure-2026"
    assert (
        meta["regulated_occupation_disclosure_reference"]
        == "reg-disclosure-2026"
    )
    assert (
        meta["ai_learning_lab_reference"] == "ai-lab-program-2026-001"
    )
    assert (
        meta["workforce_member_role"] == "patient_engagement_engineer"
    )


# ── Wire-constraint enforcement ────────────────────────────────


def test_utah_ai_policy_act_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        UtahAIPolicyActContext(
            organization="A" * 257,
            actor_role="generative_ai_user",
            generative_ai_in_use=False,
            consumer_disclosure_provided=False,
            regulated_occupation_disclosure_provided=False,
            generative_ai_use_case="not_generative_ai",
        )


def test_utah_ai_policy_act_frozen_dataclass_mutation_raises():
    ctx = UtahAIPolicyActContext(
        organization="A",
        actor_role="generative_ai_user",
        generative_ai_in_use=False,
        consumer_disclosure_provided=False,
        regulated_occupation_disclosure_provided=False,
        generative_ai_use_case="not_generative_ai",
    )
    with pytest.raises(Exception):
        ctx.organization = "B"  # type: ignore[misc]


def test_utah_ai_policy_act_metadata_json_round_trip_preserves_dict():
    ctx = UtahAIPolicyActContext(
        organization="Acme",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
        regulated_occupation="healthcare_physician",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── Three-role + factual-language discipline ───────────────────


def test_utah_ai_policy_act_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer framing
    per ADR-017."""
    from nanorix.compliance import utah_ai_policy_act as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    assert (
        "regulatory counsel" in doc.lower()
        or "Division of Consumer Protection" in doc
        or "Utah Attorney General" in doc
    )


def test_utah_ai_policy_act_class_docstring_contains_disclaimer_language():
    doc = UtahAIPolicyActContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_utah_ai_policy_act_module_docstring_surfaces_disclosure_obligations():
    """Utah-AI-Policy-Act-specific: §§ 13-72-202 + 13-72-203 disclosure
    obligations are materially distinct from other state AI laws
    (consumer-inquiry-triggered + regulated-occupation-start-of-
    exchange). The module docstring must surface this."""
    from nanorix.compliance import utah_ai_policy_act as mod

    doc = mod.__doc__ or ""
    assert "generative" in doc.lower()
    assert "disclosure" in doc.lower()
    assert "regulated occupation" in doc.lower()


def test_utah_ai_policy_act_module_docstring_surfaces_ai_civil_rights_act_substitution():
    """Wave 17 batch 5 specific: Utah AI Policy Act was shipped in
    lieu of the federal AI Civil Rights Act because the federal Act
    was not enacted at the time of helper publication. The module
    docstring must surface this substitution rationale for
    transparency to the customer."""
    from nanorix.compliance import utah_ai_policy_act as mod

    doc = mod.__doc__ or ""
    assert "AI Civil Rights Act" in doc
    assert "not been enacted" in doc.lower() or "enacted-law" in doc


def test_utah_ai_policy_act_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import utah_ai_policy_act as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


# ── Paired-framework workflows ─────────────────────────────────


def test_utah_ai_policy_act_paired_with_hipaa_context_merge_works():
    """Utah-jurisdictional healthcare generative-AI cohort: workload
    falls under both HIPAA (vertical) and Utah AI Policy Act
    (§ 13-72-203 regulated-occupation disclosure). Customer wraps with
    both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Utah Health AI",
        minimum_necessary_purpose="Patient-facing generative AI",
    )
    ut = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
        regulated_occupation="healthcare_physician",
    )
    merged = {**hipaa.to_metadata(), **ut.to_metadata()}
    # Utah AI Policy Act wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "UT_SB_149"
    # Both frameworks' substantive fields present.
    assert merged["covered_entity"] == "Acme Utah Health AI"
    assert merged["organization"] == "Acme Utah Health AI"
    # State-jurisdiction tag preserved.
    assert merged["jurisdiction_state"] == "UT"
    # Load-bearing booleans preserved.
    assert merged["generative_ai_in_use"] == "true"
    assert (
        merged["regulated_occupation_disclosure_provided"] == "true"
    )
