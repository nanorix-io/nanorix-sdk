"""Tests for nanorix.healthcare.EpicLaunchContext.

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient identifiers ("pat-001"), encounter IDs ("enc-xyz"),
Practitioner IDs ("u-456") are obviously fictitious;
no real PHI directive.
"""
from __future__ import annotations

import inspect
import re

import pytest

from nanorix.healthcare import EpicLaunchContext, EpicLaunchContextResult


# ── Constructor validation ─────────────────────────────────────


def test_epic_constructor_requires_client_id():
    with pytest.raises(ValueError, match="epic_client_id"):
        EpicLaunchContext(
            epic_client_id="",
            deployment_environment="epic_production",
        )


def test_epic_constructor_requires_deployment_environment():
    with pytest.raises(ValueError, match="deployment_environment"):
        EpicLaunchContext(
            epic_client_id="acme",
            deployment_environment="",
        )


def test_epic_constructor_rejects_invalid_environment():
    with pytest.raises(ValueError, match="deployment_environment"):
        EpicLaunchContext(
            epic_client_id="acme",
            deployment_environment="aws_lambda",
        )


def test_epic_constructor_accepts_all_valid_environments():
    for env in [
        "epic_sandbox",
        "epic_vendor_test",
        "epic_production",
        "epic_local_test",
        "other",
    ]:
        binder = EpicLaunchContext(
            epic_client_id="acme",
            deployment_environment=env,
        )
        assert binder.deployment_environment == env


# ── from_token_response — happy path ──────────────────────────


def test_epic_from_token_response_extracts_all_context_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme-prod",
        deployment_environment="epic_production",
    )
    result = binder.from_token_response({
        "access_token": "token123",
        "patient": "pat-001",
        "encounter": "enc-xyz",
        "fhirUser": "Practitioner/u-456",
        "tenant": "Org-99",
        "intent": "patient-summary",
        "scope": "patient/Patient.read launch",
    })
    assert result.patient_fhir_id == "pat-001"
    assert result.encounter_fhir_id == "enc-xyz"
    assert result.fhir_user == "Practitioner/u-456"
    assert result.tenant == "Org-99"
    assert result.intent == "patient-summary"
    assert result.scopes_granted == [
        "patient/Patient.read",
        "launch",
    ]


def test_epic_from_token_response_token_material_not_exposed():
    """Critical: access_token / id_token MUST NOT appear in result."""
    binder = EpicLaunchContext(
        epic_client_id="acme-prod",
        deployment_environment="epic_production",
    )
    result = binder.from_token_response({
        "access_token": "SECRET_TOKEN_DO_NOT_LEAK",
        "id_token": "SECRET_ID_TOKEN",
        "patient": "pat-001",
        "scope": "patient/Patient.read",
    })
    meta = result.to_metadata()
    # Token material never surfaces in metadata.
    for v in meta.values():
        assert "SECRET_TOKEN" not in v
        assert "SECRET_ID_TOKEN" not in v


def test_epic_from_token_response_metadata_includes_reserved_keys():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_token_response({
        "patient": "pat-1",
        "scope": "patient/Patient.read",
    })
    meta = result.to_metadata()
    assert meta["nanorix:framework"] == "EpicSMART"
    assert meta["nanorix:framework_version"] == "smart-app-launch-v2"


def test_epic_from_token_response_standalone_launch_no_patient():
    """Standalone launches (without patient context) are valid."""
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_token_response({
        "scope": "user/Patient.read",
    })
    assert result.patient_fhir_id is None
    assert result.has_patient_context is False
    assert result.has_encounter_context is False


def test_epic_from_token_response_handles_missing_optional_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_token_response({"patient": "pat-1"})
    assert result.patient_fhir_id == "pat-1"
    assert result.encounter_fhir_id is None
    assert result.fhir_user is None
    assert result.scopes_granted == []


# ── from_token_response — error handling ──────────────────────


def test_epic_from_token_response_rejects_non_dict():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(ValueError, match="must be a dict"):
        binder.from_token_response("not a dict")  # type: ignore


def test_epic_from_token_response_rejects_non_string_patient():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(ValueError, match="patient.*must be a string"):
        binder.from_token_response({"patient": 12345})


def test_epic_from_token_response_rejects_non_string_encounter():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(
        ValueError, match=r"encounter.*must be a string"
    ):
        binder.from_token_response({"encounter": [1, 2]})


def test_epic_from_token_response_rejects_non_string_scope():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(ValueError, match="scope.*must be a string"):
        binder.from_token_response({"scope": ["scope1", "scope2"]})


# ── from_components — happy path ──────────────────────────────


def test_epic_from_components_supplies_all_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_vendor_test",
    )
    result = binder.from_components(
        patient_fhir_id="pat-001",
        encounter_fhir_id="enc-xyz",
        fhir_user="Practitioner/u-456",
        scopes_granted=["patient/Patient.read"],
    )
    assert result.patient_fhir_id == "pat-001"
    assert result.encounter_fhir_id == "enc-xyz"
    assert result.fhir_user == "Practitioner/u-456"
    assert result.scopes_granted == ["patient/Patient.read"]


def test_epic_from_components_supplies_no_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_sandbox",
    )
    result = binder.from_components()
    assert result.patient_fhir_id is None
    assert result.scopes_granted == []


def test_epic_from_components_rejects_non_string_scope():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(ValueError, match=r"scopes_granted"):
        binder.from_components(
            scopes_granted=[123]  # type: ignore
        )


def test_epic_from_components_rejects_empty_scope_item():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    with pytest.raises(ValueError, match=r"scopes_granted"):
        binder.from_components(scopes_granted=[""])


# ── Context-presence predicates ────────────────────────────────


def test_epic_has_patient_context_true_when_patient_set():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_components(patient_fhir_id="pat-1")
    assert result.has_patient_context is True


def test_epic_has_patient_context_false_when_patient_unset():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_components()
    assert result.has_patient_context is False


def test_epic_has_encounter_context_predicate():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    r_with = binder.from_components(encounter_fhir_id="enc-1")
    r_without = binder.from_components()
    assert r_with.has_encounter_context is True
    assert r_without.has_encounter_context is False


def test_epic_has_user_context_predicate():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    r_with = binder.from_components(fhir_user="Practitioner/u-1")
    r_without = binder.from_components()
    assert r_with.has_user_context is True
    assert r_without.has_user_context is False


def test_epic_is_production_predicate():
    prod = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    ).from_components()
    sbx = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_sandbox",
    ).from_components()
    assert prod.is_production is True
    assert sbx.is_production is False


# ── Metadata emission ──────────────────────────────────────────


def test_epic_metadata_includes_epic_client_id():
    binder = EpicLaunchContext(
        epic_client_id="acme-prod",
        deployment_environment="epic_production",
    )
    meta = binder.from_components(
        patient_fhir_id="pat-1"
    ).to_metadata()
    assert meta["epic_client_id"] == "acme-prod"


def test_epic_metadata_includes_deployment_environment():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_vendor_test",
    )
    meta = binder.from_components().to_metadata()
    assert meta["deployment_environment"] == "epic_vendor_test"


def test_epic_metadata_omits_unset_optional_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    meta = binder.from_components().to_metadata()
    assert "patient_fhir_id" not in meta
    assert "encounter_fhir_id" not in meta
    assert "fhir_user" not in meta
    assert "tenant" not in meta
    assert "intent" not in meta
    assert "scopes_granted" not in meta


def test_epic_metadata_scope_serialization():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    result = binder.from_components(
        scopes_granted=["patient/Patient.read", "launch"]
    )
    meta = result.to_metadata()
    assert meta["scopes_granted"] == "patient/Patient.read launch"


# ── Introspection ──────────────────────────────────────────────


def test_epic_deployment_environment_set_contains_all_envs():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    envs = binder.deployment_environment_set
    for env in [
        "epic_sandbox",
        "epic_vendor_test",
        "epic_production",
        "epic_local_test",
        "other",
    ]:
        assert env in envs


def test_epic_known_context_fields_contains_standard_smart_fields():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    fields = binder.known_context_fields
    assert "patient" in fields
    assert "encounter" in fields
    assert "fhirUser" in fields


def test_epic_deployment_environment_taxonomy_includes_descriptions():
    taxonomy = EpicLaunchContext.deployment_environment_taxonomy()
    envs = [env for env, _ in taxonomy]
    assert "epic_production" in envs
    for env, desc in taxonomy:
        assert isinstance(env, str)
        assert isinstance(desc, str)
        assert len(desc) > 0


# ── Wire-constraint enforcement ────────────────────────────────


def test_epic_metadata_keys_under_16_max():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    meta = binder.from_token_response({
        "patient": "p",
        "encounter": "e",
        "fhirUser": "Practitioner/u",
        "tenant": "t",
        "intent": "i",
        "scope": "scope1 scope2",
    }).to_metadata()
    assert len(meta) <= 16


def test_epic_metadata_serialized_size_under_4kb():
    import json
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    meta = binder.from_token_response({
        "patient": "p",
        "scope": "patient/Patient.read",
    }).to_metadata()
    assert len(json.dumps(meta)) <= 4096


def test_epic_oversize_patient_id_rejected():
    binder = EpicLaunchContext(
        epic_client_id="acme",
        deployment_environment="epic_production",
    )
    # Wire limit on values is 256 chars.
    with pytest.raises(ValueError, match=r"patient_fhir_id"):
        binder.from_token_response({"patient": "x" * 300})


# ── Three-role / vocabulary discipline ─────────────────────────


def test_epic_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.healthcare import epic_smart as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


def test_epic_class_docstring_surfaces_three_role_discipline():
    doc = EpicLaunchContext.__doc__ or ""
    assert "structural" in doc.lower()
    assert "customer" in doc.lower()


def test_epic_module_docstring_surfaces_deployment_pattern_framing():
    """Per industry-benchmark verification, this is framed as Epic
    deployment-pattern helper, NOT Epic-SMART-scope helper."""
    from nanorix.healthcare import epic_smart as mod
    doc = mod.__doc__ or ""
    assert "deployment" in doc.lower()


def test_epic_module_docstring_surfaces_oauth_not_implemented():
    """The helper does NOT implement OAuth2 flow — that's customer
    side. Doc must surface this distinction clearly."""
    from nanorix.healthcare import epic_smart as mod
    doc = mod.__doc__ or ""
    # Surface that OAuth flow is customer-side.
    assert "oauth" in doc.lower()


# ── Direct result instantiation ────────────────────────────────


def test_epic_result_dataclass_directly_instantiable():
    """The result dataclass can be constructed directly when needed."""
    result = EpicLaunchContextResult(
        epic_client_id="acme",
        deployment_environment="epic_production",
        patient_fhir_id="pat-1",
    )
    assert result.epic_client_id == "acme"
    assert result.has_patient_context is True
