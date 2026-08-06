"""Tests for nanorix.healthcare.BulkFhirAdapter (Batch 2 sub-item 5).

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient IDs, MRNs, resource IDs are obviously fictitious. No real PHI
even in fixtures.

Coverage:
- Kickoff URL parsing (system / patient / group endpoints)
- Status response parsing (in-progress / completed / failed)
- NDJSON stream iteration
- Resource type closed-enum validation
- Patient ID hashing (never raw)
- Patient crosswalk for HCC coding workflows
- Multi-resource batch + per-record receipt emission
- Vocabulary discipline scan
- Engine-name leak guard
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re

import pytest

from nanorix.capsule_record import PATTERN_TAGS
from nanorix.healthcare import (
    BulkFhirAdapter,
)


# ── Synthetic fixtures ─────────────────────────────────────────────


PATIENT_RESOURCE_FIXTURE = {
    "resourceType": "Patient",
    "id": "patient-test-001",
    "meta": {
        "lastUpdated": "2026-05-24T12:00:00Z",
        "versionId": "1",
        "profile": [
            "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"
        ],
    },
    "name": [{"family": "DOE", "given": ["JOHN"]}],
    "gender": "male",
    "birthDate": "1980-01-01",
}


OBSERVATION_RESOURCE_FIXTURE = {
    "resourceType": "Observation",
    "id": "obs-test-001",
    "meta": {
        "lastUpdated": "2026-05-24T12:30:00Z",
        "versionId": "1",
    },
    "status": "final",
    "subject": {"reference": "Patient/patient-test-001"},
    "code": {
        "coding": [
            {"system": "http://loinc.org", "code": "8302-2", "display": "Body height"}
        ]
    },
    "valueQuantity": {
        "value": 180,
        "unit": "cm",
        "system": "http://unitsofmeasure.org",
        "code": "cm",
    },
}


CONDITION_RESOURCE_FIXTURE = {
    "resourceType": "Condition",
    "id": "cond-test-001",
    "meta": {"lastUpdated": "2026-05-24T13:00:00Z"},
    "subject": {"reference": "Patient/patient-test-001"},
    "code": {
        "coding": [
            {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I10"}
        ]
    },
}


def _ndjson_stream(*resources) -> bytes:
    """Build an NDJSON byte string from a sequence of dict resources."""
    return b"\n".join(
        json.dumps(r, separators=(",", ":")).encode("utf-8")
        for r in resources
    )


# ── 1. Kickoff URL parsing ─────────────────────────────────────────


def test_bulk_fhir_kickoff_system_endpoint():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/$export"
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.endpoint_type == "system"
    assert kickoff.base_url == "https://fhir.example.com/r4"


def test_bulk_fhir_kickoff_patient_endpoint():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/Patient/$export"
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.endpoint_type == "patient"
    assert kickoff.base_url == "https://fhir.example.com/r4"


def test_bulk_fhir_kickoff_group_endpoint():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/Group/group-test-001/$export"
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.endpoint_type == "group"
    assert kickoff.group_id == "group-test-001"
    assert kickoff.base_url == "https://fhir.example.com/r4"


def test_bulk_fhir_kickoff_type_parameter_extracted():
    adapter = BulkFhirAdapter()
    url = (
        "https://fhir.example.com/r4/Patient/$export"
        "?_type=Patient,Observation,Condition"
    )
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.types_requested == "Patient,Observation,Condition"


def test_bulk_fhir_kickoff_since_parameter_extracted():
    adapter = BulkFhirAdapter()
    url = (
        "https://fhir.example.com/r4/Patient/$export"
        "?_since=2026-01-01T00:00:00Z"
    )
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.since == "2026-01-01T00:00:00Z"


def test_bulk_fhir_kickoff_type_filter_extracted():
    adapter = BulkFhirAdapter()
    url = (
        "https://fhir.example.com/r4/Patient/$export"
        "?_typeFilter=Patient?_lastUpdated=gt2026-01-01"
    )
    kickoff = adapter.parse_kickoff(url)
    assert "lastUpdated" in kickoff.type_filter


def test_bulk_fhir_kickoff_output_format_default():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/Patient/$export"
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.output_format == "application/fhir+ndjson"


def test_bulk_fhir_kickoff_output_format_override():
    adapter = BulkFhirAdapter()
    url = (
        "https://fhir.example.com/r4/Patient/$export"
        "?_outputFormat=application/ndjson"
    )
    kickoff = adapter.parse_kickoff(url)
    assert kickoff.output_format == "application/ndjson"


def test_bulk_fhir_kickoff_rejects_non_export_url():
    adapter = BulkFhirAdapter()
    with pytest.raises(ValueError, match=r"\$export"):
        adapter.parse_kickoff("https://fhir.example.com/r4/Patient")


def test_bulk_fhir_kickoff_rejects_empty_url():
    adapter = BulkFhirAdapter()
    with pytest.raises(ValueError, match="non-empty"):
        adapter.parse_kickoff("")


def test_bulk_fhir_kickoff_request_hash_deterministic():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/Patient/$export"
    h1 = adapter.parse_kickoff(url).request_hash
    h2 = adapter.parse_kickoff(url).request_hash
    assert h1 == h2
    assert len(h1) == 64


def test_bulk_fhir_kickoff_metadata_reserved_keys():
    adapter = BulkFhirAdapter()
    url = "https://fhir.example.com/r4/Patient/$export"
    meta = adapter.parse_kickoff(url).to_metadata()
    assert meta["nanorix:framework"] == "FHIR-Bulk"
    assert meta["nanorix:framework_version"] == "fhir-bulk-data-2.0.0"


# ── 2. Status response parsing ─────────────────────────────────────


def test_bulk_fhir_status_in_progress_empty_body():
    adapter = BulkFhirAdapter()
    status = adapter.parse_status(b"")
    assert status.state == "in-progress"
    assert status.is_in_progress is True
    assert status.is_completed is False


def test_bulk_fhir_status_completed():
    adapter = BulkFhirAdapter()
    body = json.dumps({
        "transactionTime": "2026-05-24T12:00:00Z",
        "request": "https://fhir.example.com/r4/Patient/$export",
        "requiresAccessToken": True,
        "output": [
            {"type": "Patient", "url": "https://example.com/p.ndjson", "count": 100},
            {"type": "Observation", "url": "https://example.com/o.ndjson", "count": 500},
        ],
        "error": [],
    }).encode("utf-8")
    status = adapter.parse_status(body)
    assert status.state == "completed"
    assert status.is_completed is True
    assert status.output_count == 2
    assert status.error_count == 0


def test_bulk_fhir_status_completed_with_errors():
    adapter = BulkFhirAdapter()
    body = json.dumps({
        "transactionTime": "2026-05-24T12:00:00Z",
        "request": "https://fhir.example.com/r4/Patient/$export",
        "output": [
            {"type": "Patient", "url": "https://example.com/p.ndjson", "count": 100},
        ],
        "error": [
            {
                "type": "OperationOutcome",
                "url": "https://example.com/err.ndjson",
                "count": 3,
            },
        ],
    }).encode("utf-8")
    status = adapter.parse_status(body)
    assert status.state == "completed"
    assert status.output_count == 1
    assert status.error_count == 1


def test_bulk_fhir_status_failed_operation_outcome():
    adapter = BulkFhirAdapter()
    body = json.dumps({
        "resourceType": "OperationOutcome",
        "issue": [
            {"severity": "error", "code": "invalid", "details": {"text": "fail"}}
        ],
    }).encode("utf-8")
    status = adapter.parse_status(body)
    assert status.state == "failed"
    assert status.is_failed is True


def test_bulk_fhir_status_with_deleted():
    """R4B+ status responses may include a `deleted` array."""
    adapter = BulkFhirAdapter()
    body = json.dumps({
        "transactionTime": "2026-05-24T12:00:00Z",
        "request": "https://fhir.example.com/r4/Patient/$export",
        "output": [],
        "error": [],
        "deleted": [
            {"type": "Patient", "url": "https://example.com/d.ndjson", "count": 5},
        ],
    }).encode("utf-8")
    status = adapter.parse_status(body)
    assert status.deleted_count == 1


def test_bulk_fhir_status_rejects_non_json():
    adapter = BulkFhirAdapter()
    with pytest.raises(ValueError, match="JSON decode"):
        adapter.parse_status(b"not json{")


# ── 3. NDJSON stream parsing ───────────────────────────────────────


def test_bulk_fhir_ndjson_single_patient():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(PATIENT_RESOURCE_FIXTURE)
    resources = list(adapter.parse_ndjson_stream(stream))
    assert len(resources) == 1
    assert resources[0].resource_type == "Patient"
    assert resources[0].resource_id == "patient-test-001"


def test_bulk_fhir_ndjson_observation_has_patient_ref():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(OBSERVATION_RESOURCE_FIXTURE)
    resources = list(adapter.parse_ndjson_stream(stream))
    assert resources[0].resource_type == "Observation"
    # Hash of "patient-test-001" should be present.
    expected = hashlib.sha256(b"patient-test-001").hexdigest()
    assert resources[0].patient_id_hash == expected


def test_bulk_fhir_ndjson_patient_id_hashed_not_raw():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(PATIENT_RESOURCE_FIXTURE)
    resources = list(adapter.parse_ndjson_stream(stream))
    expected = hashlib.sha256(b"patient-test-001").hexdigest()
    assert resources[0].patient_id_hash == expected
    # Hash is 64 hex chars.
    assert len(resources[0].patient_id_hash) == 64


def test_bulk_fhir_ndjson_multi_resource_stream():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(
        PATIENT_RESOURCE_FIXTURE,
        OBSERVATION_RESOURCE_FIXTURE,
        CONDITION_RESOURCE_FIXTURE,
    )
    resources = list(adapter.parse_ndjson_stream(stream))
    assert len(resources) == 3
    types = [r.resource_type for r in resources]
    assert types == ["Patient", "Observation", "Condition"]


def test_bulk_fhir_ndjson_meta_lastupdated_extracted():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(PATIENT_RESOURCE_FIXTURE)
    resource = next(adapter.parse_ndjson_stream(stream))
    assert resource.last_updated == "2026-05-24T12:00:00Z"
    assert resource.version_id == "1"


def test_bulk_fhir_ndjson_meta_profile_extracted():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(PATIENT_RESOURCE_FIXTURE)
    resource = next(adapter.parse_ndjson_stream(stream))
    assert "us-core-patient" in resource.profile


def test_bulk_fhir_ndjson_resource_hash_deterministic():
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(PATIENT_RESOURCE_FIXTURE)
    r1 = next(adapter.parse_ndjson_stream(stream))
    r2 = next(adapter.parse_ndjson_stream(stream))
    assert r1.resource_hash == r2.resource_hash
    assert len(r1.resource_hash) == 64


def test_bulk_fhir_ndjson_skips_empty_lines():
    adapter = BulkFhirAdapter()
    stream = (
        b"\n"
        + json.dumps(PATIENT_RESOURCE_FIXTURE, separators=(",", ":")).encode("utf-8")
        + b"\n\n\n"
    )
    resources = list(adapter.parse_ndjson_stream(stream))
    assert len(resources) == 1


def test_bulk_fhir_ndjson_strict_rejects_malformed_json():
    adapter = BulkFhirAdapter(strict=True)
    stream = b'{"resourceType": "Patient", "id": "broken\n'
    with pytest.raises(ValueError, match="JSON decode"):
        list(adapter.parse_ndjson_stream(stream))


def test_bulk_fhir_ndjson_non_strict_skips_malformed():
    adapter = BulkFhirAdapter(strict=False)
    stream = (
        b'{"broken json\n'
        + json.dumps(PATIENT_RESOURCE_FIXTURE, separators=(",", ":")).encode("utf-8")
    )
    resources = list(adapter.parse_ndjson_stream(stream))
    assert len(resources) == 1


def test_bulk_fhir_ndjson_strict_rejects_unknown_resource_type():
    adapter = BulkFhirAdapter(strict=True)
    bad = {"resourceType": "Spaceship", "id": "scifi-001"}
    stream = _ndjson_stream(bad)
    with pytest.raises(ValueError, match="resourceType"):
        list(adapter.parse_ndjson_stream(stream))


def test_bulk_fhir_ndjson_empty_input_returns_empty():
    adapter = BulkFhirAdapter()
    assert list(adapter.parse_ndjson_stream(b"")) == []


# ── 4. Closed-enum + supported types ───────────────────────────────


def test_bulk_fhir_supported_endpoint_types():
    adapter = BulkFhirAdapter()
    assert adapter.supported_endpoint_types == ["group", "patient", "system"]


def test_bulk_fhir_supported_resource_types_includes_us_core():
    adapter = BulkFhirAdapter()
    types = set(adapter.supported_resource_types)
    # US Core common resources
    for t in ("Patient", "Observation", "Condition", "Procedure",
              "MedicationRequest", "AllergyIntolerance", "Encounter"):
        assert t in types


def test_bulk_fhir_supported_status_states():
    adapter = BulkFhirAdapter()
    states = adapter.supported_status_states
    for s in ("in-progress", "completed", "failed", "queued"):
        assert s in states


# ── 5. Patient crosswalk (HCC coding pattern) ──────────────────────


def test_bulk_fhir_patient_crosswalk_groups_resources():
    """Atria HCC coding pattern: group Observation/Condition by Patient."""
    adapter = BulkFhirAdapter()
    stream = _ndjson_stream(
        PATIENT_RESOURCE_FIXTURE,
        OBSERVATION_RESOURCE_FIXTURE,
        CONDITION_RESOURCE_FIXTURE,
    )
    resources = list(adapter.parse_ndjson_stream(stream))
    crosswalk = adapter.derive_patient_crosswalk(resources)
    expected_hash = hashlib.sha256(b"patient-test-001").hexdigest()
    assert expected_hash in crosswalk
    # Patient + Observation + Condition all map to same patient hash.
    assert set(crosswalk[expected_hash]) == {
        "Patient", "Observation", "Condition"
    }


def test_bulk_fhir_patient_crosswalk_multi_patient():
    adapter = BulkFhirAdapter()
    patient2 = {**PATIENT_RESOURCE_FIXTURE, "id": "patient-test-002"}
    obs2 = {
        **OBSERVATION_RESOURCE_FIXTURE,
        "id": "obs-test-002",
        "subject": {"reference": "Patient/patient-test-002"},
    }
    stream = _ndjson_stream(
        PATIENT_RESOURCE_FIXTURE,
        OBSERVATION_RESOURCE_FIXTURE,
        patient2,
        obs2,
    )
    resources = list(adapter.parse_ndjson_stream(stream))
    crosswalk = adapter.derive_patient_crosswalk(resources)
    assert len(crosswalk) == 2  # Two distinct patient hashes


# ── 6. Per-record receipt integration ──────────────────────────────


def test_bulk_fhir_receipt_pattern_tag_is_fhir_record():
    pt = BulkFhirAdapter.receipt_pattern_tag()
    assert pt == "fhir_record"
    assert pt in PATTERN_TAGS


def test_bulk_fhir_receipt_pattern_tag_static_method():
    assert BulkFhirAdapter.receipt_pattern_tag() == "fhir_record"


# ── 7. Multi-resource batch ────────────────────────────────────────


def _build_large_ndjson(n: int) -> bytes:
    """Build an NDJSON stream of N synthetic Patient resources."""
    resources = []
    for i in range(n):
        r = {
            "resourceType": "Patient",
            "id": f"patient-batch-{i:06d}",
            "meta": {"lastUpdated": "2026-05-24T12:00:00Z"},
            "gender": "unknown",
        }
        resources.append(r)
    return _ndjson_stream(*resources)


def test_bulk_fhir_batch_n10():
    adapter = BulkFhirAdapter()
    resources = list(adapter.parse_ndjson_stream(_build_large_ndjson(10)))
    assert len(resources) == 10
    assert len({r.patient_id_hash for r in resources}) == 10


def test_bulk_fhir_batch_n100():
    adapter = BulkFhirAdapter()
    resources = list(adapter.parse_ndjson_stream(_build_large_ndjson(100)))
    assert len(resources) == 100


def test_bulk_fhir_batch_n1000():
    adapter = BulkFhirAdapter()
    resources = list(adapter.parse_ndjson_stream(_build_large_ndjson(1000)))
    assert len(resources) == 1000


# ── 8. Vocabulary discipline ───────────────────────────────────────


def test_bulk_fhir_module_has_no_banned_verdict_vocabulary():
    import nanorix.healthcare.bulk_fhir as bf_mod
    source = inspect.getsource(bf_mod)
    banned = ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]
    for word in banned:
        matches = re.findall(rf"\b{word}\b", source)
        assert not matches, (
            f"banned verdict vocabulary {word!r} found: {matches[:3]}"
        )


def test_bulk_fhir_module_avoids_engine_name_leaks(engine_names):
    import nanorix.healthcare.bulk_fhir as bf_mod
    source = inspect.getsource(bf_mod)
    for engine in engine_names:
        matches = re.findall(rf"\b{engine}\b", source, re.IGNORECASE)
        assert not matches, f"possible engine name leak: {engine}"
