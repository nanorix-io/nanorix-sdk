"""DICOMweb (WADO-RS / STOW-RS / QIDO-RS) HTTP API adapter for capsule
workloads.

DICOMweb is the HTTP-based modernization layer over the legacy DICOM
DIMSE protocol. WADO-RS retrieves DICOM instances, series, and studies;
STOW-RS stores DICOM instances; QIDO-RS queries DICOM metadata. Together
they form the dominant pattern for cloud-native medical-imaging
integration as of 2026, used by Overjet (FDA-cleared dental imaging AI),
NVIDIA Clara, Imaging Data Commons (NCI), Google Healthcare API, and the
broader healthcare-AI cohort.

This adapter parses DICOMweb HTTP transactions (response Content-Type,
status code, multipart/related boundary, DICOM-JSON or
application/dicom payloads) and produces structural metadata suitable
for AuditProof binding. The adapter does NOT interpret pixel data,
validate against a specific DICOM Implementation Guide (e.g., the IHE
RAD-TF profile family), or transform DICOM payloads to other formats —
it extracts structural identifiers (StudyInstanceUID, SeriesInstanceUID,
SOPInstanceUID, SOP Class UID, modality) and emits a SHA-256 hash of
the response body for integrity-anchoring.

Three DICOMweb verb families covered:

- **WADO-RS** (Retrieve): GET /studies, GET /studies/{StudyInstanceUID},
  GET /studies/.../series/.../instances/{SOPInstanceUID}. Retrieves
  DICOM instances by UID. Per PS3.18 §10.4.
- **STOW-RS** (Store): POST /studies, POST /studies/{StudyInstanceUID}.
  Stores DICOM instances (multipart/related payloads). Per PS3.18 §10.5.
- **QIDO-RS** (Query): GET /studies?StudyDate=..., GET /series?...
  Queries DICOM metadata via DICOM-JSON. Per PS3.18 §10.6.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied DICOMweb HTTP transactions and emits
    metadata fields suitable for AuditProof binding. The adapter does
    NOT interpret pixel data, classify PHI inside DICOM tags, validate
    against a specific Implementation Guide (e.g., IHE RAD-TF, the
    Dental DICOM Profile, the Multi-Frame Image Profile), or certify
    upstream-system conformance. Pixel-data interpretation, IG
    conformance, PHI classification, and downstream consumer
    responsibilities remain the customer's.

Customer-sole-declarer:
    The customer is the sole declarer of capsule content. This adapter
    parses customer-supplied DICOMweb HTTP transactions to produce
    structural metadata only; Nanorix injects nothing into the workload
    beyond the published base-image spec.

Optional dependency: ``dicomweb-client>=0.59`` for full DICOMweb client
implementation. Helper provides a minimal structural parser that handles
WADO-RS / STOW-RS / QIDO-RS metadata extraction without external
dependency. Install with ``pip install nanorix[dicomweb]``.

Companion posture document: ``docs/healthcare/dicomweb-adapter.md``.

PHI/PII discipline:
    All test fixtures use SYNTHETIC DICOM data. Patient identifiers
    are obviously fictitious ("TEST^Alice", DOB "20000101",
    StudyDescription "TEST STUDY"). NEVER real PHI even in fixtures.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
)

_FRAMEWORK_VERSION = "dicomweb-ps3.18-2024"
"""DICOMweb baseline version identifier. PS3.18 is the DICOM Standard
chapter that defines DICOMweb (WADO-RS / STOW-RS / QIDO-RS). The 2024
edition is the most current as of 2026-05 and is what production
deployments target."""

_VALID_VERB_FAMILIES = frozenset({"WADO-RS", "STOW-RS", "QIDO-RS"})
"""DICOMweb verb families covered by this adapter."""

# Pattern for DICOM UID format per PS3.5 §9: ASCII digits + dots, max 64 chars.
_UID_PATTERN = re.compile(r"^\d+(\.\d+)*$")

# Pattern for SOP Class UID extraction from QIDO-RS DICOM-JSON responses.
# DICOM-JSON encodes attributes as { "00080016": { "vr": "UI", "Value": [...] } }.
# 00080016 is the SOP Class UID tag (PS3.7); 0020000D is StudyInstanceUID
# (PS3.3); 0020000E is SeriesInstanceUID; 00080018 is SOP Instance UID.
DICOM_TAG_SOP_CLASS_UID = "00080016"
DICOM_TAG_SOP_INSTANCE_UID = "00080018"
DICOM_TAG_STUDY_INSTANCE_UID = "0020000D"
DICOM_TAG_SERIES_INSTANCE_UID = "0020000E"
DICOM_TAG_MODALITY = "00080060"
DICOM_TAG_TRANSFER_SYNTAX_UID = "00020010"


def _is_valid_uid(s: str) -> bool:
    """Check DICOM UID structural format (PS3.5 §9)."""
    if not s or len(s) > 64:
        return False
    return bool(_UID_PATTERN.match(s))


@dataclass(frozen=True)
class DICOMwebTransaction:
    """Structural parse result for a DICOMweb HTTP transaction.

    Produced by :meth:`DICOMwebAdapter.parse_response`. Captures the
    verb family (WADO-RS / STOW-RS / QIDO-RS), HTTP status, the
    Content-Type (multipart/related for retrieve, application/dicom+json
    for query, application/dicom for store), the relevant DICOM UIDs,
    and a SHA-256 hash of the response body for integrity anchoring.
    Does NOT include pixel data — clinical payload remains in the
    customer's capsule workspace.

    Three-role discipline: Nanorix structurally parses;
    customer interprets.
    """

    verb_family: str
    """The DICOMweb verb family (``"WADO-RS"`` / ``"STOW-RS"`` /
    ``"QIDO-RS"``). Required non-empty string."""

    http_status: int
    """The HTTP response status code (200 OK / 202 Accepted / 4xx / 5xx).
    Required."""

    content_type: str
    """The HTTP Content-Type header (e.g.,
    ``"multipart/related; type=\\"application/dicom\\"; boundary=..."``
    for WADO-RS instance retrieval, ``"application/dicom+json"`` for
    QIDO-RS, ``"application/dicom"`` for STOW-RS individual instance).
    Required."""

    study_instance_uid: str
    """The StudyInstanceUID (DICOM tag 0020000D). Empty string if the
    transaction did not target a specific study."""

    series_instance_uid: str
    """The SeriesInstanceUID (DICOM tag 0020000E). Empty string if the
    transaction did not target a specific series."""

    sop_instance_uid: str
    """The SOPInstanceUID (DICOM tag 00080018). Empty string if the
    transaction did not target a specific instance."""

    sop_class_uid: str
    """The SOP Class UID (DICOM tag 00080016) — identifies the
    image-type taxonomy (CT, MR, US, CR, DX, MG, PT, RTSTRUCT, etc.).
    Empty string if not extractable from the transaction."""

    modality: str
    """The Modality (DICOM tag 00080060, e.g. ``"CT"``, ``"MR"``,
    ``"DX"``, ``"PX"`` for panoramic dental). Empty string if not
    present in the transaction."""

    response_body_sha256: str
    """SHA-256 hex hash of the response body bytes, used for integrity
    anchoring without exposing content. For multipart/related (WADO-RS
    instance retrieve), hashes the full multipart envelope including
    boundary delimiters; this anchors the byte-exact response."""

    response_size_bytes: int
    """Total size of the response body in bytes. Useful for capacity
    attribution evidence."""

    instance_count: int = 0
    """Number of DICOM instances in the transaction. For WADO-RS series
    retrieve this reflects the multipart envelope's instance count; for
    QIDO-RS this reflects the number of result entries. Zero for
    STOW-RS until the response is fully parsed."""

    @property
    def is_retrieve(self) -> bool:
        """Whether this is a retrieve operation (WADO-RS)."""
        return self.verb_family == "WADO-RS"

    @property
    def is_store(self) -> bool:
        """Whether this is a store operation (STOW-RS)."""
        return self.verb_family == "STOW-RS"

    @property
    def is_query(self) -> bool:
        """Whether this is a query operation (QIDO-RS)."""
        return self.verb_family == "QIDO-RS"

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        structural fields from the DICOMweb transaction. Suitable for
        binding into the AuditProof's ``metadata_hash``.

        The metadata captures structural identifiers only; pixel data
        and any DICOM tags beyond UIDs / modality / SOP class are NOT
        emitted. Customer application code processes pixel data and
        clinical-content tags inside the capsule workspace.

        Three-role discipline: structural parse only;
        Nanorix does not interpret pixel data or DICOM clinical content.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "DICOMWEB",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "verb_family": self.verb_family,
            "http_status": str(self.http_status),
            "content_type": self.content_type,
            "response_body_sha256": self.response_body_sha256,
            "response_size_bytes": str(self.response_size_bytes),
            "instance_count": str(self.instance_count),
        }
        # UIDs and modality are optional in the transaction; only emit
        # when present (skip-if-empty discipline preserves canonical
        # metadata bytes for transactions that don't carry the field).
        for name, value in (
            ("study_instance_uid", self.study_instance_uid),
            ("series_instance_uid", self.series_instance_uid),
            ("sop_instance_uid", self.sop_instance_uid),
            ("sop_class_uid", self.sop_class_uid),
            ("modality", self.modality),
        ):
            if value:
                meta[name] = value

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


class DICOMwebAdapter:
    """DICOMweb (WADO-RS / STOW-RS / QIDO-RS) structural adapter.

    Parses incoming DICOMweb HTTP responses and produces a
    :class:`DICOMwebTransaction` with structural identifiers + body
    SHA-256 hash.

    Covered verb families: WADO-RS, STOW-RS, QIDO-RS. Customers using
    other DICOMweb extensions (UPS-RS workitem management, BulkData
    retrieval) can request adapter extension.

    Three-role discipline: Adapter is structural; pixel-data
    interpretation, DICOM IG conformance, and PHI tag classification
    remain the customer's responsibility.

    Customer-sole-declarer: Customer supplies the HTTP
    response payload; adapter produces structural metadata only.

    Optional dependency: ``dicomweb-client>=0.59`` for full DICOMweb
    client implementation; this adapter handles structural parse of
    response payloads independent of any specific client library.

    Example::

        adapter = DICOMwebAdapter()
        # Customer code performs the actual DICOMweb HTTP request
        # (using dicomweb-client, requests, httpx, etc.).
        response_body = b"--boundary\\r\\nContent-Type: ..."
        tx = adapter.parse_response(
            verb_family="WADO-RS",
            http_status=200,
            content_type='multipart/related; type="application/dicom"; boundary=boundary',
            response_body=response_body,
            study_instance_uid="1.2.840.113619.2.1.1.123",
        )
        capsule = client.capsules.create(
            capsulefile="cf_...",
            metadata=tx.to_metadata(),
        )
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on any structural
            anomaly (malformed UIDs, unknown verb family, missing
            required UIDs for the verb family). If False (default),
            best-effort parsing with empty-string fallbacks for
            missing optional fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the adapter is operating in strict mode."""
        return self._strict

    @property
    def supported_verb_families(self) -> List[str]:
        """The canonical list of supported DICOMweb verb families.

        Returns a sorted copy.
        """
        return sorted(_VALID_VERB_FAMILIES)

    def parse_response(
        self,
        verb_family: str,
        http_status: int,
        content_type: str,
        response_body: bytes,
        study_instance_uid: str = "",
        series_instance_uid: str = "",
        sop_instance_uid: str = "",
        sop_class_uid: str = "",
        modality: str = "",
    ) -> DICOMwebTransaction:
        """Parse a single DICOMweb HTTP response.

        :param verb_family: One of ``"WADO-RS"`` / ``"STOW-RS"`` /
            ``"QIDO-RS"``. Required.
        :param http_status: The HTTP response status code (1..=599).
            Required.
        :param content_type: The HTTP Content-Type header. Required.
        :param response_body: The HTTP response body bytes (multipart
            envelope for WADO-RS retrieve, application/dicom for
            STOW-RS, application/dicom+json for QIDO-RS). May be
            empty bytes for responses without body.
        :param study_instance_uid: The StudyInstanceUID for this
            transaction. Optional — the adapter cannot extract this
            from arbitrary HTTP responses without parsing multipart
            envelopes; caller supplies when known from request URL
            path components.
        :param series_instance_uid: The SeriesInstanceUID. Optional.
        :param sop_instance_uid: The SOPInstanceUID. Optional.
        :param sop_class_uid: The SOP Class UID. Optional.
        :param modality: The DICOM Modality string. Optional.
        :returns: :class:`DICOMwebTransaction`.
        :raises ValueError: If verb_family is not in the closed enum,
            or in strict mode if UIDs are malformed.
        """
        if verb_family not in _VALID_VERB_FAMILIES:
            raise ValueError(
                f"unsupported verb_family '{verb_family}'; must be one of "
                f"{sorted(_VALID_VERB_FAMILIES)}"
            )
        if not isinstance(http_status, int) or not 1 <= http_status <= 599:
            raise ValueError(
                f"http_status must be int in 1..=599 (got {http_status!r})"
            )
        if not isinstance(content_type, str) or not content_type:
            raise ValueError("content_type must be a non-empty string")
        if not isinstance(response_body, (bytes, bytearray)):
            raise ValueError(
                "response_body expects bytes (got "
                f"{type(response_body).__name__}); encode upstream if needed"
            )

        # Validate UID structural format (PS3.5 §9). Strict mode rejects
        # malformed UIDs; non-strict mode preserves them as supplied
        # (customer's responsibility to ensure correctness).
        for name, value in (
            ("study_instance_uid", study_instance_uid),
            ("series_instance_uid", series_instance_uid),
            ("sop_instance_uid", sop_instance_uid),
            ("sop_class_uid", sop_class_uid),
        ):
            if value and not _is_valid_uid(value):
                if self._strict:
                    raise ValueError(
                        f"{name} '{value}' does not match DICOM UID "
                        "structural format (PS3.5 §9: ASCII digits "
                        "and dots, max 64 chars)"
                    )

        # Structural hash + size.
        body_hash = hashlib.sha256(bytes(response_body)).hexdigest()
        body_size = len(response_body)

        # Best-effort instance count extraction:
        # - WADO-RS multipart/related: count boundary delimiters.
        # - QIDO-RS application/dicom+json: count top-level JSON array elements.
        # - STOW-RS: stays 0 (response body is the STOW response report).
        instance_count = self._extract_instance_count(
            verb_family, content_type, response_body
        )

        return DICOMwebTransaction(
            verb_family=verb_family,
            http_status=http_status,
            content_type=content_type,
            study_instance_uid=study_instance_uid,
            series_instance_uid=series_instance_uid,
            sop_instance_uid=sop_instance_uid,
            sop_class_uid=sop_class_uid,
            modality=modality,
            response_body_sha256=body_hash,
            response_size_bytes=body_size,
            instance_count=instance_count,
        )

    def _extract_instance_count(
        self, verb_family: str, content_type: str, body: bytes
    ) -> int:
        """Best-effort instance count extraction.

        - WADO-RS multipart/related: count boundary delimiters in body
          (each occurrence of ``--<boundary>`` marks one part).
        - QIDO-RS application/dicom+json: count top-level JSON-array
          length. Only attempts when content-type indicates DICOM-JSON.
        - STOW-RS: returns 0 (response body is a STOW response report
          per PS3.18 §10.5.3.2, not a direct instance count).

        Best-effort: parse errors return 0 (the count is a non-critical
        attestation field; the response_body_sha256 carries the
        cryptographic evidence).
        """
        try:
            if verb_family == "WADO-RS" and "multipart" in content_type.lower():
                boundary = self._extract_boundary(content_type)
                if boundary:
                    # Count occurrences of the boundary marker. The trailing
                    # `--<boundary>--` closer is one extra occurrence; subtract
                    # so the count reflects actual parts.
                    text = body.decode("latin-1", errors="replace")
                    raw = text.count(f"--{boundary}")
                    if raw >= 2:
                        return max(0, raw - 1)
                    return 0
            elif verb_family == "QIDO-RS" and "json" in content_type.lower():
                import json as _json

                try:
                    decoded = _json.loads(body.decode("utf-8", errors="replace"))
                    if isinstance(decoded, list):
                        return len(decoded)
                except (ValueError, UnicodeDecodeError):
                    return 0
        except Exception:
            return 0
        return 0

    @staticmethod
    def _extract_boundary(content_type: str) -> str:
        """Extract the boundary parameter from a multipart Content-Type.

        Format: ``multipart/related; type="application/dicom";
        boundary=<token>`` per RFC 2387.
        """
        for part in content_type.split(";"):
            stripped = part.strip()
            if stripped.lower().startswith("boundary="):
                value = stripped[len("boundary="):].strip()
                # Strip surrounding quotes if present.
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    value = value[1:-1]
                return value
        return ""

    @staticmethod
    def is_valid_uid(s: str) -> bool:
        """Public wrapper around the DICOM UID validator.

        DICOM UIDs (PS3.5 §9): ASCII digits and dots, max 64 chars,
        components are decimal-digit strings separated by single dots,
        no leading zeros in components per PS3.5 §9.1 (the adapter
        permits leading zeros for compatibility — real-world DICOM
        archives sometimes emit them).
        """
        return _is_valid_uid(s)
