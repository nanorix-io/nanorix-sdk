"""Tests for nanorix.healthcare.DICOMSRBinding.

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient identifiers are obviously fictitious; algorithm names are
obviously fictitious; SOP Instance UIDs are illustrative (in the OID
"1.2.3.x" range — not real institution OIDs). NEVER real PHI per
Wave 18-5 / Item 37 directive.

Wave 18-5 / Item 37 / ADR-038 Proposed.
"""
from __future__ import annotations

import hashlib

import pytest

from nanorix.healthcare import DICOMSRBinding
from nanorix.compliance._constraints import (
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
)


# ── Synthetic fixtures — NEVER real PHI. ────────────────────────────

SR_DOC_BYTES = b"SYNTHETIC DICOM SR BYTES FOR TESTING ONLY"

# Minimal PS3.10 file fixture: 128-byte preamble + DICM magic + minimal
# File Meta Information group (group 0x0002 with explicit VR little
# endian encoding). This is a synthetic fixture that exercises the
# bind_from_bytes parser without external pydicom dependency.
def _build_ps310_fixture(sop_instance_uid: str, sop_class_uid: str) -> bytes:
    """Build a minimal synthetic PS3.10 DICOM file with File Meta SOP IDs.

    NOT a real DICOM file; just enough byte structure for the minimal
    File Meta parser in DICOMSRBinding.bind_from_bytes() to extract
    the UIDs. NEVER real PHI.
    """
    preamble = b"\x00" * 128
    magic = b"DICM"

    def _encode_uid_record(group: int, element: int, uid: str) -> bytes:
        # Pad UID to even length per DICOM rule.
        if len(uid) % 2 == 1:
            uid = uid + "\x00"
        data = uid.encode("ascii")
        # Group (2 LE) + Element (2 LE) + VR "UI" + Length (2 LE) + Data.
        return (
            group.to_bytes(2, "little")
            + element.to_bytes(2, "little")
            + b"UI"
            + len(data).to_bytes(2, "little")
            + data
        )

    # MediaStorageSOPClassUID (0002,0002) — mirrors SOP Class UID.
    sop_class_record = _encode_uid_record(0x0002, 0x0002, sop_class_uid)
    # MediaStorageSOPInstanceUID (0002,0003) — mirrors SOP Instance UID.
    sop_instance_record = _encode_uid_record(0x0002, 0x0003, sop_instance_uid)

    return preamble + magic + sop_class_record + sop_instance_record


SYNTHETIC_SR_PS310_BYTES = _build_ps310_fixture(
    sop_instance_uid="1.2.3.4.5.1.1",
    sop_class_uid="1.2.840.10008.5.1.4.1.1.88.22",  # Enhanced SR
)


# ── bind() basic accept paths. ──────────────────────────────────────


def test_bind_human_authored_minimal() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    assert binding.sop_instance_uid == "1.2.3.4.5.1.1"
    assert binding.creator == "HUMAN"
    assert binding.content_template_id == "TID 1500"
    assert binding.document_sha256 == hashlib.sha256(SR_DOC_BYTES).hexdigest()
    assert binding.document_size_bytes == len(SR_DOC_BYTES)
    assert binding.algorithm_identification == ""


def test_bind_ai_authored_requires_algorithm_identification() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.2",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        algorithm_identification="synthetic-fake-ai-detector@v1.0.0",
    )
    assert binding.creator == "AI"
    assert binding.algorithm_identification == "synthetic-fake-ai-detector@v1.0.0"


def test_bind_with_all_optional_fields() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.3",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.88.22",
        algorithm_identification="synthetic-detector@v2.1",
        study_instance_uid="1.2.3.4.1",
        series_instance_uid="1.2.3.4.2",
    )
    assert binding.sop_class_uid == "1.2.840.10008.5.1.4.1.1.88.22"
    assert binding.study_instance_uid == "1.2.3.4.1"
    assert binding.series_instance_uid == "1.2.3.4.2"


# ── bind() validation rejections. ───────────────────────────────────


def test_bind_rejects_empty_sop_instance_uid() -> None:
    with pytest.raises(ValueError, match="sop_instance_uid required"):
        DICOMSRBinding.bind(
            sop_instance_uid="",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_rejects_malformed_sop_instance_uid() -> None:
    with pytest.raises(ValueError, match="structural format"):
        DICOMSRBinding.bind(
            sop_instance_uid="not-a-uid",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_rejects_non_bytes_document() -> None:
    with pytest.raises(ValueError, match="document_bytes expects bytes"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes="not bytes",  # type: ignore[arg-type]
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_rejects_empty_document_bytes() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=b"",
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_rejects_invalid_creator() -> None:
    with pytest.raises(ValueError, match="creator"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="ROBOT",  # not in closed enum
            content_template_id="TID 1500",
        )


def test_bind_rejects_empty_template_id() -> None:
    with pytest.raises(ValueError, match="content_template_id required"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="",
        )


def test_bind_rejects_malformed_template_id() -> None:
    for bad_tid in ["TID", "TIDxxxx", "1500", "tid 1500", "TID 12345678"]:
        with pytest.raises(ValueError, match="content_template_id"):
            DICOMSRBinding.bind(
                sop_instance_uid="1.2.3.4.5.1.1",
                document_bytes=SR_DOC_BYTES,
                creator="HUMAN",
                content_template_id=bad_tid,
            )


def test_bind_accepts_standard_template_ids() -> None:
    for tid in ["TID 1500", "TID 4019", "TID 4030", "TID 2000", "TID 1"]:
        binding = DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id=tid,
        )
        assert binding.content_template_id == tid


# ── AI-output discipline: algorithm_identification REQUIRED for AI. ─


def test_ai_creator_without_algorithm_id_rejected() -> None:
    with pytest.raises(ValueError, match="algorithm_identification required"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="AI",
            content_template_id="TID 4019",
            algorithm_identification="",
        )


def test_ai_creator_with_too_long_algorithm_id_rejected() -> None:
    with pytest.raises(ValueError, match="algorithm_identification length"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="AI",
            content_template_id="TID 4019",
            algorithm_identification="x" * 513,
        )


def test_ai_creator_with_512_char_algorithm_id_accepted() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        algorithm_identification="x" * 512,
    )
    assert len(binding.algorithm_identification) == 512


def test_human_creator_no_algorithm_id_accepted() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
        algorithm_identification="",
    )
    assert binding.algorithm_identification == ""


# ── Optional UID validations. ───────────────────────────────────────


def test_bind_rejects_malformed_sop_class_uid() -> None:
    with pytest.raises(ValueError, match="sop_class_uid"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="TID 1500",
            sop_class_uid="not-a-uid",
        )


def test_bind_rejects_malformed_study_instance_uid() -> None:
    with pytest.raises(ValueError, match="study_instance_uid"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="TID 1500",
            study_instance_uid="not-a-uid",
        )


def test_bind_rejects_malformed_series_instance_uid() -> None:
    with pytest.raises(ValueError, match="series_instance_uid"):
        DICOMSRBinding.bind(
            sop_instance_uid="1.2.3.4.5.1.1",
            document_bytes=SR_DOC_BYTES,
            creator="HUMAN",
            content_template_id="TID 1500",
            series_instance_uid="not-a-uid",
        )


# ── to_metadata + AuditProof binding. ───────────────────────────────


def test_to_metadata_includes_framework_keys() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    meta = binding.to_metadata()
    assert meta[RESERVED_FRAMEWORK_KEY] == "DICOM_SR"
    assert meta[RESERVED_FRAMEWORK_VERSION_KEY] == "dicom-sr-ps3.3-2024"
    assert meta["sop_instance_uid"] == "1.2.3.4.5.1.1"
    assert meta["creator"] == "HUMAN"
    assert meta["content_template_id"] == "TID 1500"
    assert meta["document_sha256"] == hashlib.sha256(SR_DOC_BYTES).hexdigest()


def test_to_metadata_omits_empty_optional_fields_for_human() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    meta = binding.to_metadata()
    assert "algorithm_identification" not in meta
    assert "study_instance_uid" not in meta
    assert "series_instance_uid" not in meta
    assert "sop_class_uid" not in meta


def test_to_metadata_includes_algorithm_id_for_ai() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        algorithm_identification="synthetic-detector@v1.0",
    )
    meta = binding.to_metadata()
    assert meta["algorithm_identification"] == "synthetic-detector@v1.0"
    assert meta["creator"] == "AI"


def test_to_metadata_includes_all_optional_when_populated() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.88.22",
        algorithm_identification="synthetic-detector@v1.0",
        study_instance_uid="1.2.3.4.1",
        series_instance_uid="1.2.3.4.2",
    )
    meta = binding.to_metadata()
    assert meta["study_instance_uid"] == "1.2.3.4.1"
    assert meta["series_instance_uid"] == "1.2.3.4.2"
    assert meta["sop_class_uid"] == "1.2.840.10008.5.1.4.1.1.88.22"
    assert meta["algorithm_identification"] == "synthetic-detector@v1.0"


def test_to_metadata_size_under_4kb_limit() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        sop_class_uid="1.2.840.10008.5.1.4.1.1.88.22",
        algorithm_identification="x" * 256,
        study_instance_uid="1.2.3.4.1",
        series_instance_uid="1.2.3.4.2",
    )
    import json as _json

    assert len(_json.dumps(binding.to_metadata())) < 4096


# ── bind_from_bytes — PS3.10 file format parser. ────────────────────


def test_bind_from_bytes_rejects_non_bytes() -> None:
    with pytest.raises(ValueError, match="document_bytes expects bytes"):
        DICOMSRBinding.bind_from_bytes(
            document_bytes="not bytes",  # type: ignore[arg-type]
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_from_bytes_rejects_too_short() -> None:
    with pytest.raises(ValueError, match="128-byte preamble"):
        DICOMSRBinding.bind_from_bytes(
            document_bytes=b"too short",
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_from_bytes_rejects_missing_dicm_magic() -> None:
    bad_bytes = b"\x00" * 128 + b"XXXX" + b"\x00" * 200
    with pytest.raises(ValueError, match="DICM"):
        DICOMSRBinding.bind_from_bytes(
            document_bytes=bad_bytes,
            creator="HUMAN",
            content_template_id="TID 1500",
        )


def test_bind_from_bytes_extracts_sop_instance_uid_from_synthetic_fixture() -> None:
    binding = DICOMSRBinding.bind_from_bytes(
        document_bytes=SYNTHETIC_SR_PS310_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    assert binding.sop_instance_uid == "1.2.3.4.5.1.1"
    assert binding.sop_class_uid == "1.2.840.10008.5.1.4.1.1.88.22"
    assert binding.document_sha256 == hashlib.sha256(
        SYNTHETIC_SR_PS310_BYTES
    ).hexdigest()


def test_bind_from_bytes_ai_requires_algorithm_id() -> None:
    with pytest.raises(ValueError, match="algorithm_identification"):
        DICOMSRBinding.bind_from_bytes(
            document_bytes=SYNTHETIC_SR_PS310_BYTES,
            creator="AI",
            content_template_id="TID 4019",
        )


def test_bind_from_bytes_ai_accepts_with_algorithm_id() -> None:
    binding = DICOMSRBinding.bind_from_bytes(
        document_bytes=SYNTHETIC_SR_PS310_BYTES,
        creator="AI",
        content_template_id="TID 4019",
        algorithm_identification="synthetic-ai@v1.0",
    )
    assert binding.creator == "AI"
    assert binding.algorithm_identification == "synthetic-ai@v1.0"


# ── Dataclass frozenness. ───────────────────────────────────────────


def test_binding_is_frozen() -> None:
    binding = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    with pytest.raises(Exception):
        binding.creator = "AI"  # type: ignore[misc]


# ── Sanity: hash is deterministic over identical inputs. ────────────


def test_document_sha256_deterministic_for_same_bytes() -> None:
    b1 = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    b2 = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=SR_DOC_BYTES,
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    assert b1.document_sha256 == b2.document_sha256


def test_document_sha256_changes_with_byte_drift() -> None:
    b1 = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=b"PAYLOAD A",
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    b2 = DICOMSRBinding.bind(
        sop_instance_uid="1.2.3.4.5.1.1",
        document_bytes=b"PAYLOAD B",
        creator="HUMAN",
        content_template_id="TID 1500",
    )
    assert b1.document_sha256 != b2.document_sha256
