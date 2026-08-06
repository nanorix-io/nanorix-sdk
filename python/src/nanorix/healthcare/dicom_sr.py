"""DICOM SR (Structured Reporting) AuditProof binding helper for
capsule workloads.

DICOM SR (Structured Reporting) is the DICOM-standard format for
encoding structured clinical reports — radiologist findings, AI
analysis output, measurement reports, key image notes, mammography
CAD output. PS3.16 (DICOM Content Mapping Resource) defines the
templates and code values; PS3.3 defines the SR information object
definitions (Basic Text SR, Enhanced SR, Comprehensive SR,
Mammography CAD SR, Chest CAD SR, Radiation Dose SR).

This helper binds DICOM SR document evidence into AuditProof metadata:
the SR document SHA-256 hash, the SOP Instance UID, the content
template ID (e.g., "TID 1500" for measurement report; "TID 4019" for
AI analysis report), the creator (radiologist / AI system), and the
algorithm identification (when SR is AI-generated, per FDA SaMD
expectations for ``algorithm_identification`` metadata).

The helper does NOT interpret structured content items (NumericMeasurement,
CodeContent, TextContent, ImageReference children of the SR document
tree). The helper extracts the structural identifiers (SOP Instance
UID, template ID, document hash, creator metadata) and emits an
AuditProof-binding metadata dict.

AI-output discipline:
    When an SR document is AI-generated, the helper requires the
    customer to declare ``creator="AI"`` plus ``algorithm_identification``
    per FDA SaMD expectations (see :class:`nanorix.healthcare.FDASaMDContext`
    for the FDA SaMD pathway context). The customer's clinical
    workflow generates the SR document; this helper produces the
    AuditProof binding.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper structurally
    parses customer-supplied DICOM SR document bytes (PS3.10 file format)
    and emits metadata fields suitable for AuditProof binding. The
    helper does NOT interpret structured content items, validate against
    a specific SR template (TID 1500 / 4019 / 4030 / etc.), or certify
    that the AI-generated content meets any specific regulatory standard
    (FDA SaMD post-market surveillance, EU MDR Annex VIII Rule 11,
    UKCA UK MDR Schedule 9). Clinical-content interpretation, template
    conformance, regulatory determination, and downstream consumer
    responsibilities remain the customer's.

Customer-sole-declarer:
    The customer is the sole declarer of capsule content. This helper
    parses customer-supplied DICOM SR bytes (or accepts customer-supplied
    structural identifiers when the customer's pydicom-based pipeline
    has already parsed the SR) to produce structural metadata only;
    Nanorix injects nothing into the workload beyond the published
    base-image spec.

Optional dependency: ``pydicom>=2.4`` for full DICOM SR parsing.
This helper accepts both pre-parsed structural identifiers (via the
:meth:`DICOMSRBinding.bind` constructor) and raw SR document bytes
(via :meth:`DICOMSRBinding.bind_from_bytes`); the bytes path performs
a minimal structural parse without external dependency. Install with
``pip install nanorix[dicom-sr]``.

Companion posture document: ``docs/healthcare/dicom-sr-binding.md``.

PHI/PII discipline:
    All test fixtures use SYNTHETIC DICOM SR data. Patient identifiers
    are obviously fictitious; AI algorithm names are obviously
    fictitious. NEVER real PHI even in fixtures.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
)

_FRAMEWORK_VERSION = "dicom-sr-ps3.3-2024"
"""DICOM SR baseline version identifier. PS3.3 is the DICOM Standard
chapter that defines SR Information Object Definitions. The 2024 edition
is the most current as of 2026-05."""

# Closed enum for SR document creator role.
_VALID_CREATOR_ROLES = frozenset({"HUMAN", "AI"})
"""Creator roles. ``"HUMAN"`` = SR created by a clinician (radiologist,
pathologist, etc.). ``"AI"`` = SR created by an AI system. When
``creator == "AI"``, ``algorithm_identification`` is REQUIRED per FDA
SaMD expectations."""

# Pattern for DICOM UID format per PS3.5 §9.
_UID_PATTERN = re.compile(r"^\d+(\.\d+)*$")

# Common SR template IDs per PS3.16:
# - TID 1500: Measurement Report
# - TID 4019: AI Analysis Report
# - TID 4030: Image Library
# - TID 2000: Basic Diagnostic Imaging Report
SR_TEMPLATE_MEASUREMENT_REPORT = "TID 1500"
SR_TEMPLATE_AI_ANALYSIS_REPORT = "TID 4019"
SR_TEMPLATE_IMAGE_LIBRARY = "TID 4030"
SR_TEMPLATE_BASIC_DIAGNOSTIC_IMAGING_REPORT = "TID 2000"

# Pattern for template ID format: "TID NNNN" where NNNN is 1-6 digits.
_TID_PATTERN = re.compile(r"^TID \d{1,6}$")


def _is_valid_uid(s: str) -> bool:
    """Check DICOM UID structural format (PS3.5 §9)."""
    if not s or len(s) > 64:
        return False
    return bool(_UID_PATTERN.match(s))


def _is_valid_template_id(s: str) -> bool:
    """Check DICOM SR Content Template ID format (PS3.16 §A.3.3.2.2)."""
    return bool(_TID_PATTERN.match(s))


@dataclass(frozen=True)
class DICOMSRBinding:
    """Structural binding for a DICOM SR document.

    Produced by :meth:`DICOMSRBinding.bind` or
    :meth:`DICOMSRBinding.bind_from_bytes`. Captures the SR document's
    SOP Instance UID, the content template ID, the SHA-256 hash of the
    document bytes (PS3.10 file format), the creator role, and (when
    AI-generated) the algorithm identification. Does NOT include
    structured content items or PHI-bearing fields.

    Three-role discipline: Nanorix structurally parses;
    customer interprets clinical content.
    """

    sop_instance_uid: str
    """The SR document's SOP Instance UID (DICOM tag 00080018). Required
    non-empty string per DICOM PS3.3."""

    sop_class_uid: str
    """The SR SOP Class UID (DICOM tag 00080016) — identifies the SR
    document family:
    - ``"1.2.840.10008.5.1.4.1.1.88.11"``: Basic Text SR
    - ``"1.2.840.10008.5.1.4.1.1.88.22"``: Enhanced SR
    - ``"1.2.840.10008.5.1.4.1.1.88.33"``: Comprehensive SR
    - ``"1.2.840.10008.5.1.4.1.1.88.59"``: Comprehensive 3D SR
    - ``"1.2.840.10008.5.1.4.1.1.88.50"``: Mammography CAD SR
    - ``"1.2.840.10008.5.1.4.1.1.88.65"``: Chest CAD SR
    - ``"1.2.840.10008.5.1.4.1.1.88.67"``: X-Ray Radiation Dose SR
    - ``"1.2.840.10008.5.1.4.1.1.88.69"``: Radiopharmaceutical Radiation Dose SR
    Empty string permitted (not required for the binding; SOP Instance
    UID + content_template_id provide sufficient evidence)."""

    content_template_id: str
    """The DICOM SR Content Template ID (PS3.16) — e.g., ``"TID 1500"``
    for Measurement Report, ``"TID 4019"`` for AI Analysis Report.
    Required when ``creator == "AI"`` (the SR template indicates the
    expected structural content; FDA SaMD post-market surveillance
    needs this for performance-tracking attribution)."""

    document_sha256: str
    """SHA-256 hex hash of the SR document bytes (PS3.10 file format).
    The hash anchors the SR document's byte-exact content into the
    AuditProof without exposing structured content. 64 hex chars."""

    document_size_bytes: int
    """Size of the SR document in bytes. Useful for capacity-attribution
    evidence."""

    creator: str
    """SR document creator role: ``"HUMAN"`` (clinician-authored) or
    ``"AI"`` (AI-system-generated). Required. When ``"AI"``,
    ``algorithm_identification`` is REQUIRED per FDA SaMD expectations."""

    algorithm_identification: str = ""
    """Algorithm identification when the SR document is AI-generated.
    Format: ``"<algorithm_name>@<version>"`` or richer customer-defined
    identifier. Required when ``creator == "AI"``; empty otherwise.
    Per FDA SaMD post-market surveillance: AI-generated output must
    carry algorithm identification so the customer can attribute
    performance characteristics to a specific algorithm version."""

    study_instance_uid: str = ""
    """The parent StudyInstanceUID (DICOM tag 0020000D) — links the SR
    document to its parent study. Optional; empty if the SR is
    decoupled from a study (uncommon but valid per PS3.3)."""

    series_instance_uid: str = ""
    """The parent SeriesInstanceUID (DICOM tag 0020000E) — links the SR
    document to its parent series. Optional."""

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        structural fields from the DICOM SR document. Suitable for
        binding into the AuditProof's ``metadata_hash``.

        The metadata captures structural identifiers + algorithm
        identification (when AI-generated) only; structured content
        items (NumericMeasurement, CodeContent, TextContent,
        ImageReference) are NOT emitted. Customer application code
        processes structured content inside the capsule workspace.

        Three-role discipline: structural parse only;
        Nanorix does not interpret SR content or certify regulatory
        conformance.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "DICOM_SR",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "sop_instance_uid": self.sop_instance_uid,
            "document_sha256": self.document_sha256,
            "document_size_bytes": str(self.document_size_bytes),
            "creator": self.creator,
            "content_template_id": self.content_template_id,
        }
        if self.sop_class_uid:
            meta["sop_class_uid"] = self.sop_class_uid
        if self.algorithm_identification:
            meta["algorithm_identification"] = self.algorithm_identification
        if self.study_instance_uid:
            meta["study_instance_uid"] = self.study_instance_uid
        if self.series_instance_uid:
            meta["series_instance_uid"] = self.series_instance_uid

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, max {MAX_METADATA_KEYS}"
            )
        size = estimate_serialized_size(meta)
        if size > 4096:
            raise ValueError(
                f"to_metadata serializes to {size} bytes, max 4096"
            )
        return meta

    @classmethod
    def bind(
        cls,
        sop_instance_uid: str,
        document_bytes: bytes,
        creator: str,
        content_template_id: str,
        sop_class_uid: str = "",
        algorithm_identification: str = "",
        study_instance_uid: str = "",
        series_instance_uid: str = "",
    ) -> "DICOMSRBinding":
        """Build a DICOMSRBinding from structural identifiers + raw
        document bytes.

        The customer's pydicom-based pipeline (or any DICOM library) is
        the canonical source of structural identifiers; pass them here
        verbatim. The SHA-256 hash is computed over ``document_bytes``.

        :param sop_instance_uid: The SR document's SOP Instance UID.
            Required.
        :param document_bytes: The PS3.10 file-format bytes of the SR
            document. Required. The SHA-256 hash is computed over these
            bytes verbatim — caller must not modify the byte sequence
            after hashing, or the AuditProof binding becomes invalid.
        :param creator: ``"HUMAN"`` or ``"AI"``. Required.
        :param content_template_id: The SR content template ID
            (e.g., ``"TID 1500"`` for Measurement Report,
            ``"TID 4019"`` for AI Analysis Report). Required.
        :param sop_class_uid: The SR SOP Class UID. Optional.
        :param algorithm_identification: Required when
            ``creator == "AI"``. Format: ``"<algorithm_name>@<version>"``.
            Per FDA SaMD post-market surveillance expectations.
        :param study_instance_uid: The parent StudyInstanceUID. Optional.
        :param series_instance_uid: The parent SeriesInstanceUID.
            Optional.
        :returns: :class:`DICOMSRBinding`.
        :raises ValueError: If validations fail (see below).
        """
        # Required validations.
        if not isinstance(sop_instance_uid, str) or not sop_instance_uid:
            raise ValueError("sop_instance_uid required (non-empty string)")
        if not _is_valid_uid(sop_instance_uid):
            raise ValueError(
                f"sop_instance_uid '{sop_instance_uid}' does not match DICOM "
                "UID structural format (PS3.5 §9: ASCII digits and dots, "
                "max 64 chars)"
            )
        if not isinstance(document_bytes, (bytes, bytearray)):
            raise ValueError(
                "document_bytes expects bytes (got "
                f"{type(document_bytes).__name__})"
            )
        if not document_bytes:
            raise ValueError("document_bytes must be non-empty")
        if creator not in _VALID_CREATOR_ROLES:
            raise ValueError(
                f"creator '{creator}' invalid; must be one of "
                f"{sorted(_VALID_CREATOR_ROLES)}"
            )
        if not content_template_id:
            raise ValueError(
                "content_template_id required (non-empty TID string, "
                "e.g., 'TID 1500' for Measurement Report or "
                "'TID 4019' for AI Analysis Report)"
            )
        if not _is_valid_template_id(content_template_id):
            raise ValueError(
                f"content_template_id '{content_template_id}' invalid; "
                "must match 'TID NNNN' pattern (e.g., 'TID 1500')"
            )

        # AI-output discipline: algorithm_identification REQUIRED when
        # creator == "AI" per FDA SaMD post-market surveillance.
        if creator == "AI":
            if not algorithm_identification:
                raise ValueError(
                    "algorithm_identification required when creator='AI' "
                    "(FDA SaMD post-market surveillance: AI-generated SR "
                    "must carry algorithm identification for "
                    "performance attribution)"
                )
            if len(algorithm_identification) > 512:
                raise ValueError(
                    f"algorithm_identification length {len(algorithm_identification)}; "
                    "max 512 chars"
                )

        # Optional UID validations (only when populated).
        if sop_class_uid and not _is_valid_uid(sop_class_uid):
            raise ValueError(
                f"sop_class_uid '{sop_class_uid}' does not match DICOM UID "
                "structural format"
            )
        if study_instance_uid and not _is_valid_uid(study_instance_uid):
            raise ValueError(
                f"study_instance_uid '{study_instance_uid}' does not match "
                "DICOM UID structural format"
            )
        if series_instance_uid and not _is_valid_uid(series_instance_uid):
            raise ValueError(
                f"series_instance_uid '{series_instance_uid}' does not match "
                "DICOM UID structural format"
            )

        document_sha256 = hashlib.sha256(bytes(document_bytes)).hexdigest()
        document_size_bytes = len(document_bytes)

        return cls(
            sop_instance_uid=sop_instance_uid,
            sop_class_uid=sop_class_uid,
            content_template_id=content_template_id,
            document_sha256=document_sha256,
            document_size_bytes=document_size_bytes,
            creator=creator,
            algorithm_identification=algorithm_identification,
            study_instance_uid=study_instance_uid,
            series_instance_uid=series_instance_uid,
        )

    @classmethod
    def bind_from_bytes(
        cls,
        document_bytes: bytes,
        creator: str,
        content_template_id: str,
        algorithm_identification: str = "",
    ) -> "DICOMSRBinding":
        """Build a DICOMSRBinding by performing a minimal structural
        parse of raw PS3.10 SR bytes.

        This is the fallback path for customers without a pydicom-based
        pipeline. The minimal parser:

        1. Verifies the PS3.10 preamble + DICM magic.
        2. Locates the SOP Instance UID (tag 00080018) in the
           File Meta Information group via the explicit-VR-little-endian
           encoding mandated for File Meta.
        3. Locates the SOP Class UID (tag 00080016) in File Meta if present.
        4. Computes the SHA-256 hash of the full document.

        Customers with pydicom-parsed SR documents should use
        :meth:`bind` instead — it accepts the SOP Instance UID + SOP
        Class UID + template ID + parent study/series UIDs directly,
        without re-parsing.

        :param document_bytes: PS3.10 file-format bytes. Required.
        :param creator: ``"HUMAN"`` or ``"AI"``. Required.
        :param content_template_id: The SR content template ID. Required.
        :param algorithm_identification: Required when
            ``creator == "AI"``.
        :returns: :class:`DICOMSRBinding`.
        :raises ValueError: If the bytes are not valid PS3.10 (missing
            DICM magic at offset 128) or the SOP Instance UID cannot
            be extracted.
        """
        if not isinstance(document_bytes, (bytes, bytearray)):
            raise ValueError(
                "document_bytes expects bytes (got "
                f"{type(document_bytes).__name__})"
            )
        if len(document_bytes) < 132:
            raise ValueError(
                f"document_bytes length {len(document_bytes)}; PS3.10 "
                "file format requires at least 128-byte preamble + 'DICM' magic"
            )
        if bytes(document_bytes[128:132]) != b"DICM":
            raise ValueError(
                "document_bytes is not a valid PS3.10 file: missing 'DICM' "
                "magic at offset 128"
            )

        sop_instance_uid = _extract_uid_from_file_meta(
            document_bytes, group=0x0008, element=0x0018
        )
        sop_class_uid = _extract_uid_from_file_meta(
            document_bytes, group=0x0008, element=0x0016
        )

        if not sop_instance_uid:
            raise ValueError(
                "Cannot extract SOP Instance UID (tag 00080018) from "
                "File Meta Information group. Use bind() and supply the "
                "UID directly from a pydicom-parsed document."
            )

        return cls.bind(
            sop_instance_uid=sop_instance_uid,
            document_bytes=document_bytes,
            creator=creator,
            content_template_id=content_template_id,
            sop_class_uid=sop_class_uid,
            algorithm_identification=algorithm_identification,
        )


def _extract_uid_from_file_meta(
    document_bytes: bytes, group: int, element: int
) -> str:
    """Extract a UID from the File Meta Information group of a PS3.10
    DICOM file.

    File Meta Information is encoded in explicit VR little endian per
    PS3.10 §7.1; this minimal parser walks the explicit-VR records in
    the meta group to locate the requested (group, element) tag and
    decode the UID value.

    Returns empty string if the tag is not present (caller decides if
    that's an error).

    Three-role discipline: structural extraction only; no
    interpretation of the UID's semantic meaning.

    :param document_bytes: PS3.10 file bytes.
    :param group: DICOM tag group (e.g., 0x0008).
    :param element: DICOM tag element (e.g., 0x0018).
    :returns: UID string (decoded), or empty string if not found.
    """
    # File Meta starts at offset 132 (after preamble + DICM).
    # Tag layout: (group:2 LE)(element:2 LE)(VR:2 ASCII)(length:2 LE for VR
    # types other than OB/OW/OF/SQ/UT/UN which use a reserved+4-byte length).
    # UIDs use VR 'UI' with 2-byte length.
    pos = 132
    end = len(document_bytes)

    # Walk explicit-VR records. The File Meta length is given by the
    # first record (group=0x0002, element=0x0000, VR='UL'); after that,
    # records are 0x0002 elements until the group switches.
    while pos + 8 <= end:
        gr = int.from_bytes(document_bytes[pos:pos + 2], "little")
        el = int.from_bytes(document_bytes[pos + 2:pos + 4], "little")
        vr = bytes(document_bytes[pos + 4:pos + 6]).decode(
            "ascii", errors="replace"
        )

        # Stop at end of File Meta group (transition to data set).
        if gr != 0x0002 and pos > 132:
            # Reset: the dataset uses implicit VR by default; but for
            # SOP Instance UID at 0x0008,0x0018 we may not get a hit
            # via this path. Continue trying — but most DICOM SR files
            # have the UID also in File Meta as MediaStorageSOPInstanceUID
            # at tag 00020003, which we may need to map.
            # Conservative: try the alternate File-Meta MediaStorage tags.
            break

        # For VR 'UI', length is 2 bytes; for VR 'UL', also 2 bytes.
        # OB, OW, OF, SQ, UT, UN use 4-byte length with a 2-byte reserved
        # prefix. For UI we only need the 2-byte length path.
        if vr in ("OB", "OW", "OF", "SQ", "UT", "UN"):
            # Reserved 2 bytes, then 4-byte length.
            if pos + 12 > end:
                break
            length = int.from_bytes(document_bytes[pos + 8:pos + 12], "little")
            data_start = pos + 12
        else:
            length = int.from_bytes(document_bytes[pos + 6:pos + 8], "little")
            data_start = pos + 8

        if data_start + length > end:
            break

        if gr == group and el == element and vr == "UI":
            value = (
                bytes(document_bytes[data_start:data_start + length])
                .decode("ascii", errors="replace")
                .rstrip("\x00 ")
            )
            return value

        # Also check File Meta MediaStorageSOPInstanceUID (00020003)
        # which mirrors the dataset SOP Instance UID (00080018) per
        # PS3.10 §7.1. If caller asked for 0x0008,0x0018 we map it
        # transparently.
        if (
            group == 0x0008
            and element == 0x0018
            and gr == 0x0002
            and el == 0x0003
            and vr == "UI"
        ):
            value = (
                bytes(document_bytes[data_start:data_start + length])
                .decode("ascii", errors="replace")
                .rstrip("\x00 ")
            )
            return value

        if (
            group == 0x0008
            and element == 0x0016
            and gr == 0x0002
            and el == 0x0002
            and vr == "UI"
        ):
            # MediaStorageSOPClassUID (00020002) mirrors SOPClassUID (00080016).
            value = (
                bytes(document_bytes[data_start:data_start + length])
                .decode("ascii", errors="replace")
                .rstrip("\x00 ")
            )
            return value

        pos = data_start + length

    return ""
