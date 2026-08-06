"""Pydantic model tests."""
from nanorix._models import (
    Capsule,
    CdpDocument,
    Capsulefile,
    VerificationCdp,
)


def test_capsulefile_model_allows_extra_fields():
    data = {
        "id": "cf_x",
        "name": "test",
        "runtime": "python3.11",
        "entrypoint": "python x.py",
        "unknown_future_field": "should not crash",
        "another_new_field": 42,
    }
    cf = Capsulefile.model_validate(data)
    assert cf.id == "cf_x"
    # Extra fields should be accessible
    assert cf.model_extra is not None
    assert cf.model_extra.get("unknown_future_field") == "should not crash"


def test_capsule_model_minimal_fields():
    data = {"id": "cap_min", "status": "active"}
    capsule = Capsule.model_validate(data)
    assert capsule.id == "cap_min"
    assert capsule.status == "active"
    assert capsule.capsulefile_id is None
    assert capsule.max_lifetime_seconds is None
    assert capsule.created_at is None


def test_cdp_document_parses_activity_events():
    data = {
        "capsule_id": "cap_act",
        "activity": [
            {"event": "capsule_started", "at": "2026-03-25T00:00:00Z", "runtime": "python3.11"},
            {"event": "file_uploaded", "at": "2026-03-25T00:00:01Z", "filename": "a.csv", "size_bytes": 100},
            {"event": "execution_completed", "at": "2026-03-25T00:00:05Z", "exit_code": 0},
        ],
    }
    cdp = CdpDocument.model_validate(data)
    assert len(cdp.activity) == 3
    assert cdp.activity[0].event == "capsule_started"
    assert cdp.activity[1].event == "file_uploaded"
    assert cdp.activity[2].event == "execution_completed"
    # Extra fields captured
    assert cdp.activity[0].model_extra.get("runtime") == "python3.11"


def test_verification_cdp_all_fields_optional():
    data = {"capsule_id": "cap_verify"}
    v = VerificationCdp.model_validate(data)
    assert v.capsule_id == "cap_verify"
    assert v.version is None
    assert v.environment is None
    assert v.data_ingested is None
    assert v.destruction is None
    assert v.attestation is None
    assert v.document_hash is None
    assert v.verify is None
