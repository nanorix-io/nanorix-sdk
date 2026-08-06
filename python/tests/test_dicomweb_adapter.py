"""Tests for nanorix.healthcare.DICOMwebAdapter.

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient identifiers are obviously fictitious; modality codes and UIDs
are illustrative; NEVER real PHI per Wave 18-5 / Item 36 directive.

Wave 18-5 / Item 36 / ADR-038 Proposed.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from nanorix.healthcare import DICOMwebAdapter
from nanorix.compliance._constraints import (
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
)


# ── Synthetic fixtures — NEVER real PHI. ────────────────────────────

WADO_RS_MULTIPART_FIXTURE = (
    b"--boundary\r\n"
    b"Content-Type: application/dicom; transfer-syntax=1.2.840.10008.1.2.1\r\n"
    b"Content-Length: 4\r\n"
    b"\r\n"
    b"DCM1"
    b"\r\n"
    b"--boundary\r\n"
    b"Content-Type: application/dicom; transfer-syntax=1.2.840.10008.1.2.1\r\n"
    b"Content-Length: 4\r\n"
    b"\r\n"
    b"DCM2"
    b"\r\n"
    b"--boundary--\r\n"
)

QIDO_RS_JSON_FIXTURE = json.dumps(
    [
        {
            "00080016": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.1.1.2"]},
            "00080018": {"vr": "UI", "Value": ["1.2.3.4.5.1"]},
            "00080060": {"vr": "CS", "Value": ["CT"]},
        },
        {
            "00080016": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.1.1.4"]},
            "00080018": {"vr": "UI", "Value": ["1.2.3.4.5.2"]},
            "00080060": {"vr": "CS", "Value": ["MR"]},
        },
    ]
).encode("utf-8")

STOW_RS_RESPONSE_FIXTURE = (
    b'<?xml version="1.0"?><NativeDicomModel></NativeDicomModel>'
)


# ── Constructor + introspection tests. ──────────────────────────────


def test_adapter_default_constructor() -> None:
    adapter = DICOMwebAdapter()
    assert adapter.strict_mode is False


def test_adapter_strict_mode_constructor() -> None:
    adapter = DICOMwebAdapter(strict=True)
    assert adapter.strict_mode is True


def test_supported_verb_families_returns_sorted_copy() -> None:
    adapter = DICOMwebAdapter()
    supported = adapter.supported_verb_families
    assert supported == ["QIDO-RS", "STOW-RS", "WADO-RS"]
    # Mutation isolation.
    supported.append("FAKE")
    assert "FAKE" not in adapter.supported_verb_families


# ── parse_response — basic verb family validation. ──────────────────


def test_parse_response_rejects_unknown_verb_family() -> None:
    adapter = DICOMwebAdapter()
    with pytest.raises(ValueError, match="unsupported verb_family"):
        adapter.parse_response(
            verb_family="UPS-RS",
            http_status=200,
            content_type="application/dicom",
            response_body=b"",
        )


def test_parse_response_rejects_invalid_http_status() -> None:
    adapter = DICOMwebAdapter()
    for bad in [0, -1, 600, 1000]:
        with pytest.raises(ValueError, match="http_status"):
            adapter.parse_response(
                verb_family="WADO-RS",
                http_status=bad,
                content_type="application/dicom",
                response_body=b"",
            )


def test_parse_response_rejects_empty_content_type() -> None:
    adapter = DICOMwebAdapter()
    with pytest.raises(ValueError, match="content_type"):
        adapter.parse_response(
            verb_family="WADO-RS",
            http_status=200,
            content_type="",
            response_body=b"",
        )


def test_parse_response_rejects_non_bytes_body() -> None:
    adapter = DICOMwebAdapter()
    with pytest.raises(ValueError, match="response_body"):
        adapter.parse_response(
            verb_family="WADO-RS",
            http_status=200,
            content_type="application/dicom",
            response_body="not bytes",  # type: ignore[arg-type]
        )


# ── WADO-RS retrieve transactions. ──────────────────────────────────


def test_wado_rs_multipart_parse_extracts_instance_count() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; type="application/dicom"; boundary=boundary',
        response_body=WADO_RS_MULTIPART_FIXTURE,
        study_instance_uid="1.2.840.113619.2.1.1.123",
    )
    assert tx.verb_family == "WADO-RS"
    assert tx.http_status == 200
    assert tx.is_retrieve is True
    assert tx.is_store is False
    assert tx.is_query is False
    assert tx.study_instance_uid == "1.2.840.113619.2.1.1.123"
    assert tx.instance_count == 2
    assert tx.response_body_sha256 == hashlib.sha256(
        WADO_RS_MULTIPART_FIXTURE
    ).hexdigest()
    assert tx.response_size_bytes == len(WADO_RS_MULTIPART_FIXTURE)


def test_wado_rs_quoted_boundary_extraction() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; type="application/dicom"; boundary="boundary"',
        response_body=WADO_RS_MULTIPART_FIXTURE,
    )
    assert tx.instance_count == 2


def test_wado_rs_with_no_boundary_returns_zero_count() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=b"DICOM",
    )
    assert tx.instance_count == 0


# ── QIDO-RS query transactions. ──────────────────────────────────────


def test_qido_rs_extracts_instance_count_from_dicom_json() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="QIDO-RS",
        http_status=200,
        content_type="application/dicom+json",
        response_body=QIDO_RS_JSON_FIXTURE,
    )
    assert tx.verb_family == "QIDO-RS"
    assert tx.is_query is True
    assert tx.instance_count == 2
    assert tx.response_body_sha256 == hashlib.sha256(
        QIDO_RS_JSON_FIXTURE
    ).hexdigest()


def test_qido_rs_malformed_json_returns_zero_count() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="QIDO-RS",
        http_status=200,
        content_type="application/dicom+json",
        response_body=b"not valid json",
    )
    assert tx.instance_count == 0


# ── STOW-RS store transactions. ─────────────────────────────────────


def test_stow_rs_store_basic() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=200,
        content_type="application/dicom+xml",
        response_body=STOW_RS_RESPONSE_FIXTURE,
        study_instance_uid="1.2.840.113619.2.1.1.456",
    )
    assert tx.verb_family == "STOW-RS"
    assert tx.is_store is True
    assert tx.is_retrieve is False
    assert tx.study_instance_uid == "1.2.840.113619.2.1.1.456"
    assert tx.response_body_sha256 == hashlib.sha256(
        STOW_RS_RESPONSE_FIXTURE
    ).hexdigest()


def test_stow_rs_202_accepted_status_accepted() -> None:
    """STOW-RS may return 202 Accepted for queued processing."""
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=202,
        content_type="application/dicom+xml",
        response_body=STOW_RS_RESPONSE_FIXTURE,
    )
    assert tx.http_status == 202


def test_stow_rs_partial_content_status_accepted() -> None:
    """STOW-RS may return 206 Partial Content for partial-store success."""
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=206,
        content_type="application/dicom+xml",
        response_body=STOW_RS_RESPONSE_FIXTURE,
    )
    assert tx.http_status == 206


# ── UID validation. ─────────────────────────────────────────────────


def test_is_valid_uid_accepts_real_dicom_sop_classes() -> None:
    # PS3.4 standard SOP Class UIDs.
    assert DICOMwebAdapter.is_valid_uid("1.2.840.10008.5.1.4.1.1.2")  # CT Image
    assert DICOMwebAdapter.is_valid_uid("1.2.840.10008.5.1.4.1.1.4")  # MR Image
    assert DICOMwebAdapter.is_valid_uid("1.2.840.10008.5.1.4.1.1.6.1")  # US Image


def test_is_valid_uid_rejects_invalid_shapes() -> None:
    assert not DICOMwebAdapter.is_valid_uid("")
    assert not DICOMwebAdapter.is_valid_uid("abc.123")
    assert not DICOMwebAdapter.is_valid_uid("1.2.840.")  # trailing dot
    assert not DICOMwebAdapter.is_valid_uid(".1.2.840")  # leading dot
    assert not DICOMwebAdapter.is_valid_uid("1." * 100)  # > 64 chars


def test_strict_mode_rejects_malformed_uid() -> None:
    adapter = DICOMwebAdapter(strict=True)
    with pytest.raises(ValueError, match="structural format"):
        adapter.parse_response(
            verb_family="WADO-RS",
            http_status=200,
            content_type="application/dicom",
            response_body=b"",
            study_instance_uid="not-a-uid",
        )


def test_non_strict_mode_preserves_malformed_uid_as_supplied() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=b"",
        study_instance_uid="customer-internal-id-123",
    )
    assert tx.study_instance_uid == "customer-internal-id-123"


# ── to_metadata + AuditProof binding. ───────────────────────────────


def test_to_metadata_includes_framework_keys() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; type="application/dicom"; boundary=boundary',
        response_body=WADO_RS_MULTIPART_FIXTURE,
        study_instance_uid="1.2.3",
        modality="CT",
    )
    meta = tx.to_metadata()
    assert meta[RESERVED_FRAMEWORK_KEY] == "DICOMWEB"
    assert meta[RESERVED_FRAMEWORK_VERSION_KEY] == "dicomweb-ps3.18-2024"
    assert meta["verb_family"] == "WADO-RS"
    assert meta["http_status"] == "200"
    assert meta["modality"] == "CT"


def test_to_metadata_omits_empty_optional_fields() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=b"",
    )
    meta = tx.to_metadata()
    # Empty optional fields are omitted.
    assert "study_instance_uid" not in meta
    assert "series_instance_uid" not in meta
    assert "sop_instance_uid" not in meta
    assert "sop_class_uid" not in meta
    assert "modality" not in meta


def test_to_metadata_includes_all_uids_when_provided() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; type="application/dicom"; boundary=b',
        response_body=b"",
        study_instance_uid="1.2.3.1",
        series_instance_uid="1.2.3.1.1",
        sop_instance_uid="1.2.3.1.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        modality="CT",
    )
    meta = tx.to_metadata()
    assert meta["study_instance_uid"] == "1.2.3.1"
    assert meta["series_instance_uid"] == "1.2.3.1.1"
    assert meta["sop_instance_uid"] == "1.2.3.1.1.1"
    assert meta["sop_class_uid"] == "1.2.840.10008.5.1.4.1.1.2"
    assert meta["modality"] == "CT"


def test_to_metadata_size_under_4kb_limit() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; type="application/dicom"; boundary=b',
        response_body=b"x" * 1000,
        study_instance_uid="1.2.3.1",
        series_instance_uid="1.2.3.1.1",
        sop_instance_uid="1.2.3.1.1.1",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.2",
        modality="CT",
    )
    meta = tx.to_metadata()
    # All values are short strings; well under the 4096 byte limit.
    import json as _json

    assert len(_json.dumps(meta)) < 4096


# ── Predicate properties. ────────────────────────────────────────────


def test_is_retrieve_is_store_is_query_mutually_exclusive() -> None:
    adapter = DICOMwebAdapter()
    for verb in ["WADO-RS", "STOW-RS", "QIDO-RS"]:
        tx = adapter.parse_response(
            verb_family=verb,
            http_status=200,
            content_type="application/dicom",
            response_body=b"",
        )
        flags = [tx.is_retrieve, tx.is_store, tx.is_query]
        assert flags.count(True) == 1


def test_dataclass_frozen() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=b"",
    )
    # Frozen dataclass — mutation should raise.
    with pytest.raises(Exception):
        tx.verb_family = "STOW-RS"  # type: ignore[misc]


# ── Content-Type variations + boundary edge cases. ──────────────────


def test_boundary_with_special_chars() -> None:
    """RFC 2387 allows boundaries with limited special chars."""
    adapter = DICOMwebAdapter()
    body = b"--boundary123_xyz\r\n--boundary123_xyz--\r\n"
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; boundary=boundary123_xyz',
        response_body=body,
    )
    # Two raw matches (one open + one close), so count = max(0, 2-1) = 1.
    assert tx.instance_count == 1


def test_empty_multipart_body_returns_zero_count() -> None:
    adapter = DICOMwebAdapter()
    tx = adapter.parse_response(
        verb_family="WADO-RS",
        http_status=200,
        content_type='multipart/related; boundary=b',
        response_body=b"",
    )
    assert tx.instance_count == 0


# ── Body size invariants. ───────────────────────────────────────────


def test_response_size_matches_body_length() -> None:
    adapter = DICOMwebAdapter()
    body = b"x" * 12345
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=body,
    )
    assert tx.response_size_bytes == 12345


def test_response_sha256_matches_manual_hash() -> None:
    adapter = DICOMwebAdapter()
    body = b"SYNTHETIC DICOM PAYLOAD FOR TESTING ONLY"
    tx = adapter.parse_response(
        verb_family="STOW-RS",
        http_status=200,
        content_type="application/dicom",
        response_body=body,
    )
    assert tx.response_body_sha256 == hashlib.sha256(body).hexdigest()
    assert len(tx.response_body_sha256) == 64  # SHA-256 hex = 64 chars
