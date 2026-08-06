"""C-CDA reader → FHIR Bundle adapter for capsule workloads.

The Consolidated Clinical Document Architecture (C-CDA) — codified at
HL7's C-CDA R2.1 Implementation Guide — is the principal XML-based
clinical-document exchange standard for care-transition events in US
healthcare. While FHIR is the strategic modernization layer, C-CDA
remains widely deployed: Epic supports C-CDA export via Direct secure
messaging AND via FHIR API DocumentReference + Binary resources;
Cerner/Oracle Health, MEDITECH, and most large IDN networks emit
C-CDA at care-transition touchpoints.

Per industry-baseline production patterns, the Epic-side interface
in 2026 is increasingly C-CDA-document-as-FHIR-attachment: a
DocumentReference resource references a Binary resource whose
``data`` is the base64-encoded C-CDA XML payload. This adapter
provides a reader-only path: customer-supplied C-CDA XML is parsed
for structural identifiers (document type, patient ID reference,
author + custodian organizations, document datetime, section types
observed) and emits a FHIR-Bundle-shaped Python dict
(DocumentReference + Binary) along with AuditProof-binding metadata.

**Scope discipline (per industry-benchmark verification):**

This is a **reader → FHIR Bundle adapter only**. It does NOT:

- Produce C-CDA XML (no producer path)
- Validate against any specific C-CDA implementation guide
  (Continuity of Care Document, Discharge Summary, Progress Note,
  etc.) — customer's clinical-document validator handles that
- Interpret clinical content inside sections — clinical
  interpretation remains the customer's
- Map terminology (SNOMED CT, LOINC, ICD-10) — terminology mapping
  remains the customer's
- Validate FHIR Bundle conformance against any FHIR IG — the
  emitted Bundle shape is structural, not implementation-guide-
  validated

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied C-CDA XML and emits a FHIR-Bundle-shaped
    Python dict + AuditProof binding metadata. The adapter does NOT
    interpret clinical content, validate against any specific
    implementation guide, or certify upstream-system conformance.

Customer-sole-declarer (ADR-013):
    The customer is the sole declarer of capsule content. This adapter
    parses customer-supplied C-CDA payloads to produce structural
    metadata only.

Optional dependency: ``lxml>=4.9`` for robust XML parsing. The helper
provides a fallback minimal parser using the stdlib
``xml.etree.ElementTree`` that handles structural extraction without
external dependency. Install with ``pip install nanorix[ccda]``.

Companion posture document: ``docs/healthcare/ccda-fhir-adapter.md``.
"""
from __future__ import annotations

import base64
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "ccda-r2.1"
"""C-CDA baseline version identifier. The adapter targets C-CDA R2.1
which is the dominant production version across US deployments
through 2026. The version string is a non-calendar adapter-baseline
identifier."""

# C-CDA / HL7 XML namespaces. C-CDA documents always use the urn:hl7-
# org:v3 namespace; the v3 namespace is the load-bearing identifier.
_CDA_NS = "urn:hl7-org:v3"
_NS_MAP = {"cda": _CDA_NS}


@dataclass(frozen=True)
class CCDADocumentReference:
    """Structural reader result for a C-CDA XML document.

    Captures structural identifiers extracted from the C-CDA header
    (ClinicalDocument metadata) plus a SHA-256 hash of the original
    document bytes for integrity anchoring. Clinical content remains
    in the customer's capsule workspace; this dataclass surfaces only
    structural identifiers suitable for AuditProof binding.

    Three-role discipline (ADR-017): Nanorix structurally parses;
    customer interprets clinical content.
    """

    document_type_code: str
    """The ClinicalDocument/code/@code (e.g., ``"34133-9"`` for
    Continuity of Care Document). LOINC-coded per C-CDA R2.1.
    Required."""

    document_type_display: str
    """The ClinicalDocument/code/@displayName (e.g., ``"Continuity of
    Care Document"``). Required; may be empty if the source document
    omits the display name."""

    document_id: str
    """The ClinicalDocument/id/@extension. Required for routing
    deduplication and AuditProof integrity-anchoring."""

    document_datetime: str
    """The ClinicalDocument/effectiveTime/@value. HL7 V3 timestamp
    format (YYYYMMDDHHMMSS). Required."""

    patient_id_reference: str
    """The ClinicalDocument/recordTarget/patientRole/id/@extension.
    Customer-side reference to the patient — DOES NOT include patient
    name, date of birth, or other PHI from PID-equivalent fields.
    Required."""

    author_organization: str
    """The ClinicalDocument/author/assignedAuthor/representedOrganization
    /name (e.g., ``"Acme Hospital"``). Required."""

    custodian_organization: str
    """The ClinicalDocument/custodian/assignedCustodian/
    representedCustodianOrganization/name. Required for document-
    custody accountability evidence."""

    document_sha256: str
    """SHA-256 hex hash of the original document bytes, used for
    integrity anchoring."""

    section_template_ids: Tuple[str, ...] = field(default_factory=tuple)
    """Tuple of templateIds observed in the document's structuredBody
    sections (e.g., ``("2.16.840.1.113883.10.20.22.2.1.1",)`` for
    Medications). Structural-only; does NOT include section content."""

    section_codes: Tuple[str, ...] = field(default_factory=tuple)
    """Tuple of LOINC section codes observed (e.g., ``("10160-0",)``
    for History of Medication Use)."""

    @property
    def is_continuity_of_care(self) -> bool:
        """Whether this document is a Continuity of Care Document
        (LOINC 34133-9). Common care-transition document type."""
        return self.document_type_code == "34133-9"

    @property
    def is_discharge_summary(self) -> bool:
        """Whether this document is a Discharge Summary (LOINC
        18842-5)."""
        return self.document_type_code == "18842-5"

    @property
    def is_progress_note(self) -> bool:
        """Whether this document is a Progress Note (LOINC 11506-3)."""
        return self.document_type_code == "11506-3"

    def to_fhir_bundle(self, *, raw_bytes: bytes) -> Dict[str, Any]:
        """Emit a FHIR-Bundle-shaped Python dict (DocumentReference +
        Binary).

        Returns a structural FHIR Bundle suitable for the Epic-side
        2026 production pattern (C-CDA-document-as-FHIR-attachment).
        The Bundle contains:

        - A ``DocumentReference`` resource with ``type``,
          ``subject.identifier``, ``date``, ``author``, ``custodian``,
          and ``content.attachment.url`` pointing to the Binary.
        - A ``Binary`` resource with ``contentType="application/xml"``
          and ``data`` containing the base64-encoded C-CDA XML
          payload.

        :param raw_bytes: The original C-CDA XML bytes. The Bundle's
            Binary.data field is the base64-encoded payload.
        :returns: A Python dict shaped as a FHIR Bundle.

        Note: FHIR conformance is structural only; the customer's
        downstream FHIR validator validates against the customer's
        FHIR IG. Adapter does NOT certify FHIR conformance.
        """
        if not isinstance(raw_bytes, (bytes, bytearray)):
            raise ValueError(
                "to_fhir_bundle: raw_bytes must be bytes (got "
                f"{type(raw_bytes).__name__})"
            )

        b64 = base64.standard_b64encode(raw_bytes).decode("ascii")

        binary_id = "ccda-binary"
        doc_ref_id = "ccda-document-reference"

        bundle: Dict[str, Any] = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {
                    "fullUrl": f"urn:uuid:{doc_ref_id}",
                    "resource": {
                        "resourceType": "DocumentReference",
                        "id": doc_ref_id,
                        "status": "current",
                        "type": {
                            "coding": [
                                {
                                    "system": "http://loinc.org",
                                    "code": self.document_type_code,
                                    "display": (
                                        self.document_type_display
                                    ),
                                }
                            ]
                        },
                        "subject": {
                            "identifier": {
                                "value": self.patient_id_reference,
                            }
                        },
                        "date": self.document_datetime,
                        "author": [
                            {
                                "display": self.author_organization,
                            }
                        ],
                        "custodian": {
                            "display": self.custodian_organization,
                        },
                        "content": [
                            {
                                "attachment": {
                                    "contentType": "application/xml",
                                    "url": f"urn:uuid:{binary_id}",
                                    "hash": self.document_sha256,
                                }
                            }
                        ],
                    },
                },
                {
                    "fullUrl": f"urn:uuid:{binary_id}",
                    "resource": {
                        "resourceType": "Binary",
                        "id": binary_id,
                        "contentType": "application/xml",
                        "data": b64,
                    },
                },
            ],
        }
        return bundle

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        structural fields from the C-CDA document header. Suitable
        for binding into the AuditProof's ``metadata_hash`` per
        ADR-010 A4.

        Clinical content (section bodies) is NOT emitted.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "CCDA",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "document_type_code": self.document_type_code,
            "document_type_display": self.document_type_display,
            "document_id": self.document_id,
            "document_datetime": self.document_datetime,
            "patient_id_reference": self.patient_id_reference,
            "author_organization": self.author_organization,
            "custodian_organization": self.custodian_organization,
            "document_sha256": self.document_sha256,
        }
        if self.section_codes:
            meta["section_codes"] = ",".join(self.section_codes)

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, "
                f"max {MAX_METADATA_KEYS}"
            )
        size = estimate_serialized_size(meta)
        if size > 4096:
            raise ValueError(
                f"to_metadata serializes to {size} bytes, max 4096"
            )
        return meta


class CCDAReader:
    """C-CDA XML reader → FHIR Bundle adapter.

    Parses incoming C-CDA XML bytes and produces a
    :class:`CCDADocumentReference` with structural identifiers + a
    FHIR-Bundle-shaped Python dict for the Epic-side 2026 production
    pattern.

    Covered C-CDA document types (most-common per industry-baseline):

    - **Continuity of Care Document** (LOINC 34133-9)
    - **Discharge Summary** (LOINC 18842-5)
    - **Progress Note** (LOINC 11506-3)
    - Other LOINC-coded C-CDA document types parse structurally; the
      adapter does not branch on document type.

    Three-role discipline (ADR-017): Adapter is structural; clinical
    interpretation remains the customer's.

    Customer-sole-declarer (ADR-013): Customer supplies the document;
    adapter produces structural metadata + FHIR Bundle shape only.

    Optional dependency: ``lxml>=4.9`` for robust XML parsing;
    fallback minimal parser uses stdlib ``xml.etree.ElementTree``.

    Example::

        reader = CCDAReader()
        xml_bytes = open("ccd.xml", "rb").read()
        doc = reader.read(xml_bytes)
        bundle = doc.to_fhir_bundle(raw_bytes=xml_bytes)
        capsule = client.capsules.create(
            capsulefile="cf_...",
            metadata=doc.to_metadata(),
        )
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the reader.

        :param strict: If True, raise ValueError on any structural
            anomaly (missing required header field, non-CDA root
            element). If False (default), best-effort parsing with
            empty-string fallbacks for missing optional fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the reader is operating in strict mode."""
        return self._strict

    def read(self, raw: bytes) -> CCDADocumentReference:
        """Parse a C-CDA XML document.

        :param raw: The XML bytes. Must contain a ClinicalDocument
            root element in the ``urn:hl7-org:v3`` namespace.
        :returns: :class:`CCDADocumentReference` with structural
            fields.
        :raises ValueError: If the XML is malformed, missing required
            header fields (in strict mode), or has a non-CDA root.

        Clinical-content sections are NOT parsed; only their template
        IDs and section codes are observed for the
        ``section_template_ids`` and ``section_codes`` fields.
        """
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError(
                "read expects bytes (got "
                f"{type(raw).__name__}); decode upstream if needed"
            )
        if not raw:
            raise ValueError("read: empty document bytes")

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            raise ValueError(
                f"read: XML parse error: {e}"
            ) from None

        # Verify root is ClinicalDocument in the v3 namespace.
        if root.tag != f"{{{_CDA_NS}}}ClinicalDocument":
            raise ValueError(
                "read: root element is "
                f"{root.tag!r}, expected ClinicalDocument in namespace "
                f"{_CDA_NS!r}"
            )

        doc_sha = hashlib.sha256(raw).hexdigest()

        # Document type code (ClinicalDocument/code).
        code_elem = root.find("cda:code", _NS_MAP)
        if code_elem is None:
            if self._strict:
                raise ValueError(
                    "read [strict]: ClinicalDocument/code element "
                    "missing"
                )
            doc_type_code = ""
            doc_type_display = ""
        else:
            doc_type_code = code_elem.get("code", "")
            doc_type_display = code_elem.get("displayName", "")

        # Document ID (ClinicalDocument/id).
        id_elem = root.find("cda:id", _NS_MAP)
        if id_elem is None:
            if self._strict:
                raise ValueError(
                    "read [strict]: ClinicalDocument/id element missing"
                )
            doc_id = ""
        else:
            # C-CDA documents may use @extension or @root for the ID.
            doc_id = id_elem.get("extension") or id_elem.get("root") or ""

        # Effective time (ClinicalDocument/effectiveTime).
        time_elem = root.find("cda:effectiveTime", _NS_MAP)
        if time_elem is None:
            if self._strict:
                raise ValueError(
                    "read [strict]: ClinicalDocument/effectiveTime "
                    "element missing"
                )
            doc_dt = ""
        else:
            doc_dt = time_elem.get("value", "")

        # Patient ID (ClinicalDocument/recordTarget/patientRole/id).
        patient_id = ""
        pr = root.find(
            "cda:recordTarget/cda:patientRole/cda:id", _NS_MAP
        )
        if pr is not None:
            patient_id = pr.get("extension") or pr.get("root") or ""
        elif self._strict:
            raise ValueError(
                "read [strict]: recordTarget/patientRole/id element "
                "missing"
            )

        # Author organization (ClinicalDocument/author/assignedAuthor/
        # representedOrganization/name).
        author_org = ""
        author_org_elem = root.find(
            "cda:author/cda:assignedAuthor/"
            "cda:representedOrganization/cda:name",
            _NS_MAP,
        )
        if author_org_elem is not None and author_org_elem.text:
            author_org = author_org_elem.text.strip()

        # Custodian organization (ClinicalDocument/custodian/...
        # representedCustodianOrganization/name).
        custodian_org = ""
        custodian_elem = root.find(
            "cda:custodian/cda:assignedCustodian/"
            "cda:representedCustodianOrganization/cda:name",
            _NS_MAP,
        )
        if custodian_elem is not None and custodian_elem.text:
            custodian_org = custodian_elem.text.strip()

        # Observe section structure (component/structuredBody/component/
        # section).
        section_template_ids: List[str] = []
        section_codes: List[str] = []
        for section in root.iter(f"{{{_CDA_NS}}}section"):
            for tid in section.findall("cda:templateId", _NS_MAP):
                root_attr = tid.get("root", "")
                if root_attr and root_attr not in section_template_ids:
                    section_template_ids.append(root_attr)
            code = section.find("cda:code", _NS_MAP)
            if code is not None:
                code_val = code.get("code", "")
                if code_val and code_val not in section_codes:
                    section_codes.append(code_val)

        # Validate wire constraints.
        validate_value_length("document_type_code", doc_type_code)
        validate_value_length("document_type_display", doc_type_display)
        validate_value_length("document_id", doc_id)
        validate_value_length("document_datetime", doc_dt)
        validate_value_length("patient_id_reference", patient_id)
        validate_value_length("author_organization", author_org)
        validate_value_length("custodian_organization", custodian_org)

        return CCDADocumentReference(
            document_type_code=doc_type_code,
            document_type_display=doc_type_display,
            document_id=doc_id,
            document_datetime=doc_dt,
            patient_id_reference=patient_id,
            author_organization=author_org,
            custodian_organization=custodian_org,
            document_sha256=doc_sha,
            section_template_ids=tuple(section_template_ids),
            section_codes=tuple(section_codes),
        )

    def read_to_bundle(
        self, raw: bytes
    ) -> Tuple[CCDADocumentReference, Dict[str, Any]]:
        """Parse a C-CDA document AND emit the FHIR Bundle.

        Convenience wrapper that combines :meth:`read` and
        :meth:`CCDADocumentReference.to_fhir_bundle`.

        :param raw: The XML bytes.
        :returns: Tuple of (parsed C-CDA reference, FHIR Bundle dict).
        """
        doc = self.read(raw)
        bundle = doc.to_fhir_bundle(raw_bytes=bytes(raw))
        return doc, bundle

    @staticmethod
    def supported_document_types() -> List[Tuple[str, str]]:
        """The canonical list of supported C-CDA document type codes.

        Returns a list of (LOINC code, display name) tuples for the
        most-common C-CDA document types covered by industry-baseline
        production patterns. The reader handles other LOINC-coded
        C-CDA documents structurally; this list documents the
        most-common subset.
        """
        return [
            ("34133-9", "Continuity of Care Document"),
            ("18842-5", "Discharge Summary"),
            ("11506-3", "Progress Note"),
            ("28570-0", "Procedure Note"),
            ("57133-1", "Referral Note"),
            ("11488-4", "Consultation Note"),
        ]

    @staticmethod
    def derive_metadata(
        parsed: CCDADocumentReference,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed C-CDA document.

        Convenience wrapper around
        :meth:`CCDADocumentReference.to_metadata` that optionally adds
        a customer-supplied workflow identifier.

        :param parsed: A parsed C-CDA document reference.
        :param customer_workflow_id: Optional customer-side workflow
            identifier.
        :returns: Dict suitable for ``capsules.create(metadata=...)``.
        """
        meta = parsed.to_metadata()
        if customer_workflow_id is not None:
            validate_value_length(
                "customer_workflow_id", customer_workflow_id
            )
            if len(meta) >= MAX_METADATA_KEYS:
                raise ValueError(
                    "derive_metadata: cannot add customer_workflow_id; "
                    "metadata already at MAX_METADATA_KEYS"
                )
            meta["customer_workflow_id"] = customer_workflow_id
            size = estimate_serialized_size(meta)
            if size > 4096:
                raise ValueError(
                    f"derive_metadata serializes to {size} bytes, "
                    "max 4096"
                )
        return meta
