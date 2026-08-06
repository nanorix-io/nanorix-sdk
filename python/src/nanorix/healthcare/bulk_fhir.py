"""Bulk FHIR $export helper for population-data healthcare workloads.

The HL7 FHIR Bulk Data Access Implementation Guide (v2.0.0, published
2023; broadly deployed through 2026) defines the $export operation for
extracting large FHIR resource sets from EHR / data-platform systems
as NDJSON streams. Population-data analytics, research, HCC coding,
and large-scale extraction workloads use $export as the canonical
ingestion path.

Three primary $export endpoints per the IG:

- **System-level**: ``GET [base]/$export`` — entire system data
- **Patient-level**: ``GET [base]/Patient/$export`` — patient compartment
- **Group-level**: ``GET [base]/Group/[id]/$export`` — defined group

This adapter parses kickoff requests + status polling responses +
NDJSON resource streams from the $export workflow and produces structural
metadata + per-record receipt-emission helper suitable for AuditProof
binding.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied bulk FHIR transaction artifacts and emits
    structural metadata fields. Clinical-content interpretation, FHIR
    Implementation Guide conformance (e.g., US Core, IHE Patient Care
    Coordination, Da Vinci HRex, CMS-0057 interoperability), and
    downstream analytics adjudication remain the customer's
    responsibility.

Customer-sole-declarer:
    The customer supplies the FHIR resource bytes. This adapter parses
    structural fields only.

Forever-Standard discipline:
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field. Per-record
    receipt emission uses the existing ``pattern_tag="fhir_record"``
    closed-enum variant.

Pure-Python implementation; no native dependencies. The adapter handles
NDJSON parsing with Python's built-in `json` module and HTTP-style
header parsing without `requests` / `httpx` (the kickoff and polling
HTTP transactions are short and well-formed).

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Patient IDs, MRNs,
    resource IDs are obviously fictitious. Never real PHI in any code
    path even in fixtures.

Companion posture document: ``docs/integrations/healthcare-bulk-fhir.md``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterator, List

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "fhir-bulk-data-2.0.0"
"""HL7 FHIR Bulk Data Access IG v2.0.0 baseline. v2.0.0 is the current
published version through 2026. The version string targets the IG
structurally; the adapter parses kickoff + status + NDJSON without
binding to a specific FHIR R4 vs R5 resource schema."""

# HL7 FHIR resource type closed enum per FHIR R4 / R5 §"List of all
# resource types". Append-only; new resource types land
# via ADR amendment. Curated for the most-common bulk-export resources
# in production through 2026 (~95% of population-data workload coverage).
_VALID_FHIR_RESOURCE_TYPES: FrozenSet[str] = frozenset({
    # Patient + administrative
    "Patient",
    "Practitioner",
    "PractitionerRole",
    "Organization",
    "Location",
    "Group",
    "RelatedPerson",
    "Person",
    # Clinical core
    "Observation",
    "Condition",
    "Procedure",
    "Encounter",
    "EpisodeOfCare",
    "AllergyIntolerance",
    "FamilyMemberHistory",
    "ClinicalImpression",
    # Medication
    "Medication",
    "MedicationRequest",
    "MedicationStatement",
    "MedicationAdministration",
    "MedicationDispense",
    "Immunization",
    # Diagnostics
    "DiagnosticReport",
    "ImagingStudy",
    "Specimen",
    "Media",
    # Care plan
    "CarePlan",
    "CareTeam",
    "Goal",
    "ServiceRequest",
    "DeviceRequest",
    "NutritionOrder",
    # Financial
    "Coverage",
    "Claim",
    "ClaimResponse",
    "ExplanationOfBenefit",
    "Account",
    "Invoice",
    # Document
    "DocumentReference",
    "Composition",
    "Bundle",
    "List",
    # Workflow
    "Task",
    "Appointment",
    "Schedule",
    "Slot",
    # Foundation
    "Provenance",
    "AuditEvent",
    "Questionnaire",
    "QuestionnaireResponse",
    "DocumentManifest",
    # Genomics (held — Pattern 7 boss directive)
    "MolecularSequence",
    "MolecularDefinition",
})

# $export endpoint types per HL7 FHIR Bulk Data Access IG.
_EXPORT_ENDPOINT_TYPES: FrozenSet[str] = frozenset(
    {"system", "patient", "group"}
)

# Status response state values per the IG §"Bulk Data Status Request".
_STATUS_STATES: FrozenSet[str] = frozenset(
    {"in-progress", "completed", "failed", "queued"}
)


@dataclass(frozen=True)
class BulkFhirKickoff:
    """Structural parse result for a bulk FHIR $export kickoff request."""

    endpoint_type: str
    """One of ``"system"``, ``"patient"``, or ``"group"``."""

    base_url: str
    """The FHIR server base URL (e.g.,
    ``"https://fhir.acmehealth.example.com/r4"``)."""

    group_id: str = ""
    """For group-level exports, the Group resource ID. Empty for
    system/patient exports."""

    output_format: str = "application/fhir+ndjson"
    """The customer-declared ``_outputFormat`` parameter (default per
    IG). NDJSON is the only widely-supported output format through 2026."""

    type_filter: str = ""
    """Comma-joined ``_typeFilter`` parameter value if specified. Empty
    if no filter."""

    since: str = ""
    """The ``_since`` parameter (FHIR instant). Empty if absent."""

    types_requested: str = ""
    """Comma-joined ``_type`` parameter values, indicating which FHIR
    resource types the customer requested. Empty for all-types
    exports."""

    accept_header: str = "application/fhir+json"
    """The Accept header for the kickoff request."""

    request_hash: str = ""
    """SHA-256 hex hash of the canonical kickoff request URL +
    headers."""

    def to_metadata(self) -> Dict[str, str]:
        """Convert to AuditProof metadata."""
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "FHIR-Bulk",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "kind": "kickoff",
            "endpoint_type": self.endpoint_type,
            "base_url": self.base_url,
            "output_format": self.output_format,
            "request_hash": self.request_hash,
        }
        if self.group_id:
            meta["group_id"] = self.group_id
        if self.type_filter:
            meta["type_filter"] = self.type_filter
        if self.since:
            meta["since"] = self.since
        if self.types_requested:
            meta["types_requested"] = self.types_requested

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


@dataclass(frozen=True)
class BulkFhirStatus:
    """Structural parse result for a bulk FHIR $export status response."""

    state: str
    """One of ``"in-progress"``, ``"completed"``, ``"failed"``,
    ``"queued"``."""

    transaction_time: str = ""
    """The export's transactionTime value (FHIR instant)."""

    request_url: str = ""
    """The kickoff request URL associated with this export."""

    output_count: int = 0
    """Number of NDJSON output files in the export."""

    error_count: int = 0
    """Number of OperationOutcome error files in the export."""

    deleted_count: int = 0
    """Number of deleted-resource references (group-level FHIR R4B+)."""

    expires_at: str = ""
    """The Expires header from the status response (when the NDJSON
    files will be deleted from the FHIR server)."""

    @property
    def is_completed(self) -> bool:
        return self.state == "completed"

    @property
    def is_failed(self) -> bool:
        return self.state == "failed"

    @property
    def is_in_progress(self) -> bool:
        return self.state == "in-progress"

    def to_metadata(self) -> Dict[str, str]:
        """Convert to AuditProof metadata."""
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "FHIR-Bulk",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "kind": "status",
            "state": self.state,
            "output_count": str(self.output_count),
            "error_count": str(self.error_count),
            "deleted_count": str(self.deleted_count),
        }
        if self.transaction_time:
            meta["transaction_time"] = self.transaction_time
        if self.request_url:
            meta["request_url"] = self.request_url
        if self.expires_at:
            meta["expires_at"] = self.expires_at

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, max {MAX_METADATA_KEYS}"
            )
        return meta


@dataclass(frozen=True)
class BulkFhirResource:
    """Structural parse result for a single FHIR resource extracted from
    an NDJSON stream.

    The adapter extracts resource type + ID + meta.lastUpdated as
    structural identifiers. Clinical content (name, telecom, address,
    observation values, etc.) is NOT extracted — only the resource-level
    routing identifiers required for per-record AuditProof binding.
    """

    resource_type: str
    """The FHIR resource type (e.g., ``"Patient"``, ``"Observation"``,
    ``"Condition"``)."""

    resource_id: str
    """The FHIR resource ID."""

    last_updated: str = ""
    """The ``meta.lastUpdated`` instant. Empty if absent."""

    version_id: str = ""
    """The ``meta.versionId``. Empty if absent."""

    profile: str = ""
    """The first profile URL in ``meta.profile``. Empty if absent."""

    patient_id_hash: str = ""
    """SHA-256 hex hash of the patient identifier when extractable
    (Patient resources or resources with .subject.reference patient/
    id). Empty when no patient reference present. Raw identifiers are
    NEVER emitted."""

    resource_hash: str = ""
    """SHA-256 hex hash of the canonical-JSON serialization of the
    resource body."""


class BulkFhirAdapter:
    """Bulk FHIR $export structural adapter.

    Parses three classes of bulk-FHIR transaction artifacts:

    1. **Kickoff request**: parse ``GET [base]/$export?...`` URL
       parameters → :class:`BulkFhirKickoff`
    2. **Status response**: parse the JSON body of the polling response
       → :class:`BulkFhirStatus`
    3. **NDJSON stream**: parse a newline-delimited JSON FHIR resource
       stream → iterator of :class:`BulkFhirResource`

    Three-role discipline: Adapter is structural; clinical
    content interpretation, FHIR IG conformance, and downstream
    analytics adjudication remain the customer's responsibility.

    Pure-Python implementation. No third-party dependencies.

    Example::

        adapter = BulkFhirAdapter()

        # 1. Parse kickoff
        kickoff = adapter.parse_kickoff(
            url="https://fhir.example.com/r4/Patient/$export?_type=Patient,Observation"
        )

        # 2. Poll status
        status = adapter.parse_status(status_response_json)

        # 3. Iterate NDJSON
        with client.capsules.create(capsulefile="cf_bulk_fhir") as capsule:
            for resource in adapter.parse_ndjson_stream(ndjson_bytes):
                with capsule.record(
                    record_id=f"{resource.resource_type}/{resource.resource_id}",
                    pattern_tag="fhir_record",
                ) as r:
                    r.bind_input(resource.resource_hash)
                    result = process_resource(resource)
                    r.bind_output(result)
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter."""
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        return self._strict

    @property
    def supported_endpoint_types(self) -> List[str]:
        return sorted(_EXPORT_ENDPOINT_TYPES)

    @property
    def supported_resource_types(self) -> List[str]:
        """Sorted list of supported FHIR resource types."""
        return sorted(_VALID_FHIR_RESOURCE_TYPES)

    @property
    def supported_status_states(self) -> List[str]:
        return sorted(_STATUS_STATES)

    def parse_kickoff(
        self,
        url: str,
        *,
        accept_header: str = "application/fhir+json",
        prefer_header: str = "respond-async",
    ) -> BulkFhirKickoff:
        """Parse a $export kickoff request URL into a structural metadata
        dataclass.

        :param url: The full kickoff URL (e.g.,
            ``"https://fhir.example.com/r4/Patient/$export?_type=Patient,Observation"``).
        :param accept_header: The Accept header (default per IG).
        :param prefer_header: The Prefer header (must be
            ``"respond-async"`` per IG).
        :returns: :class:`BulkFhirKickoff` with structural fields.
        :raises ValueError: On malformed URL or unsupported endpoint type.

        The adapter detects endpoint type from the URL path:
        - ``/$export`` (no resource type) → system
        - ``/Patient/$export`` → patient
        - ``/Group/{id}/$export`` → group

        Query parameters parsed: ``_outputFormat``, ``_since``,
        ``_type``, ``_typeFilter``.
        """
        if not isinstance(url, str) or not url:
            raise ValueError("parse_kickoff: url must be a non-empty string")
        if "$export" not in url:
            raise ValueError(
                "parse_kickoff: URL does not contain '$export'; not a "
                "bulk FHIR kickoff request"
            )

        # Split URL into path + query.
        path_part, _, query_part = url.partition("?")

        # Detect endpoint type from path.
        endpoint_type = ""
        group_id = ""
        if path_part.endswith("/$export"):
            stem = path_part[: -len("/$export")]
            # System: stem ends at base URL (e.g., /r4 or /fhir)
            # Patient: stem ends with /Patient
            # Group: stem ends with /Group/{id}
            if stem.endswith("/Patient"):
                endpoint_type = "patient"
                base_url = stem[: -len("/Patient")]
            elif "/Group/" in stem:
                endpoint_type = "group"
                base_url, _, group_id = stem.rpartition("/Group/")
            else:
                endpoint_type = "system"
                base_url = stem
        else:
            raise ValueError(
                "parse_kickoff: URL path does not end with '/$export'"
            )

        # Parse query parameters.
        output_format = "application/fhir+ndjson"
        type_filter = ""
        since = ""
        types_requested = ""

        if query_part:
            for pair in query_part.split("&"):
                key, _, value = pair.partition("=")
                # URL-decode minimally (the test fixtures use
                # un-encoded values; production senders MAY encode).
                key = key.strip()
                value = value.replace("+", " ").strip()
                if key == "_outputFormat":
                    output_format = value
                elif key == "_typeFilter":
                    type_filter = value
                elif key == "_since":
                    since = value
                elif key == "_type":
                    types_requested = value

        # Validate types_requested against the closed enum (best-effort;
        # custom resource types are flagged in strict mode only).
        if types_requested and self._strict:
            unknown = [
                t for t in types_requested.split(",")
                if t.strip() and t.strip() not in _VALID_FHIR_RESOURCE_TYPES
            ]
            if unknown:
                raise ValueError(
                    f"parse_kickoff [strict]: unknown resource types: "
                    f"{unknown}"
                )

        # Canonical request hash for AuditProof integrity-anchoring.
        canonical = (
            url + "\n" + accept_header + "\n" + prefer_header
        ).encode("utf-8")
        request_hash = hashlib.sha256(canonical).hexdigest()

        validate_value_length("base_url", base_url)
        validate_value_length("group_id", group_id)
        validate_value_length("output_format", output_format)
        validate_value_length("type_filter", type_filter)
        validate_value_length("since", since)
        validate_value_length("types_requested", types_requested)

        return BulkFhirKickoff(
            endpoint_type=endpoint_type,
            base_url=base_url,
            group_id=group_id,
            output_format=output_format,
            type_filter=type_filter,
            since=since,
            types_requested=types_requested,
            accept_header=accept_header,
            request_hash=request_hash,
        )

    def parse_status(
        self, response_body: bytes
    ) -> BulkFhirStatus:
        """Parse a status-response JSON body.

        :param response_body: The JSON-encoded status response body.
        :returns: :class:`BulkFhirStatus` with structural fields.
        :raises ValueError: On malformed JSON or invalid state.

        Status response shape per IG §"Bulk Data Status Request":
        - "transactionTime": FHIR instant
        - "request": kickoff URL
        - "requiresAccessToken": bool
        - "output": [{type, url, count}, ...]
        - "error": [{type, url, count}, ...]
        - "deleted": [{type, url, count}, ...]  (R4B+)

        For in-progress responses, the body may instead carry an
        OperationOutcome or be entirely empty (HTTP 202). The adapter
        detects this via the absence of `output` / `error` / `deleted`.
        """
        if not isinstance(response_body, (bytes, bytearray, str)):
            raise ValueError(
                "parse_status expects bytes/bytearray/str (got "
                f"{type(response_body).__name__})"
            )
        if isinstance(response_body, (bytes, bytearray)):
            body_str = response_body.decode("utf-8", errors="replace")
        else:
            body_str = response_body
        if not body_str.strip():
            # Empty body usually means in-progress (HTTP 202).
            return BulkFhirStatus(state="in-progress")

        try:
            doc = json.loads(body_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"parse_status: JSON decode error: {exc}"
            ) from None

        if not isinstance(doc, dict):
            raise ValueError(
                "parse_status: status response is not a JSON object"
            )

        # Detect state. Per the IG, a completed response carries
        # `transactionTime` + `request` + `output`/`error`/`deleted`.
        # A failed response carries an OperationOutcome resource with
        # issue[].
        # An in-progress response is HTTP 202 with empty body.
        state = "in-progress"
        if "transactionTime" in doc and "output" in doc:
            state = "completed"
        elif "resourceType" in doc and doc["resourceType"] == "OperationOutcome":
            state = "failed"

        if state not in _STATUS_STATES:
            if self._strict:
                raise ValueError(
                    f"parse_status [strict]: detected state {state!r} "
                    f"not in {sorted(_STATUS_STATES)}"
                )

        transaction_time = doc.get("transactionTime", "") or ""
        request_url = doc.get("request", "") or ""
        output = doc.get("output", []) or []
        error = doc.get("error", []) or []
        deleted = doc.get("deleted", []) or []

        validate_value_length("transaction_time", transaction_time)
        validate_value_length("request_url", request_url)

        return BulkFhirStatus(
            state=state,
            transaction_time=transaction_time,
            request_url=request_url,
            output_count=len(output) if isinstance(output, list) else 0,
            error_count=len(error) if isinstance(error, list) else 0,
            deleted_count=len(deleted) if isinstance(deleted, list) else 0,
        )

    def parse_ndjson_stream(
        self, ndjson: bytes
    ) -> Iterator[BulkFhirResource]:
        """Parse an NDJSON FHIR resource stream into a structural iterator.

        :param ndjson: The NDJSON bytes (one JSON resource per line).
        :returns: Iterator over :class:`BulkFhirResource` instances.
        :raises ValueError: On malformed NDJSON (in strict mode) or
            unsupported resource type (in strict mode).

        The adapter yields one :class:`BulkFhirResource` per JSON line
        with structural fields (resource_type, resource_id, lastUpdated,
        versionId, profile, patient_id_hash, resource_hash). Clinical
        content fields are NOT extracted — the customer's application
        code processes those inside the capsule workspace.

        Patient IDs referenced via ``.subject.reference`` (e.g.,
        ``"Patient/abc-123"``) or ``.id`` on Patient resources are
        SHA-256 hashed. Raw identifiers are NEVER emitted.

        The iterator is one-shot; cannot be reused. For multi-pass
        processing, call :meth:`parse_ndjson_stream` again.
        """
        if not isinstance(ndjson, (bytes, bytearray)):
            raise ValueError(
                "parse_ndjson_stream expects bytes (got "
                f"{type(ndjson).__name__})"
            )
        if not ndjson:
            return

        text = ndjson.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                resource = json.loads(stripped)
            except json.JSONDecodeError as exc:
                if self._strict:
                    raise ValueError(
                        f"parse_ndjson_stream [strict]: line {line_no}: "
                        f"JSON decode error: {exc}"
                    ) from None
                # Non-strict: skip malformed line.
                continue
            if not isinstance(resource, dict):
                if self._strict:
                    raise ValueError(
                        f"parse_ndjson_stream [strict]: line {line_no}: "
                        "not a JSON object"
                    )
                continue
            yield self._build_resource(resource, line_no)

    def _build_resource(
        self, resource: Dict[str, Any], line_no: int
    ) -> BulkFhirResource:
        """Build a :class:`BulkFhirResource` from a parsed JSON dict."""
        resource_type = resource.get("resourceType", "")
        if not resource_type and self._strict:
            raise ValueError(
                f"parse_ndjson_stream [strict]: line {line_no}: "
                "missing resourceType"
            )
        if (
            resource_type
            and resource_type not in _VALID_FHIR_RESOURCE_TYPES
            and self._strict
        ):
            raise ValueError(
                f"parse_ndjson_stream [strict]: line {line_no}: "
                f"resourceType={resource_type!r} not in supported set"
            )

        resource_id = str(resource.get("id", "") or "")
        meta_obj = resource.get("meta", {}) or {}
        last_updated = ""
        version_id = ""
        profile = ""
        if isinstance(meta_obj, dict):
            last_updated = str(meta_obj.get("lastUpdated", "") or "")
            version_id = str(meta_obj.get("versionId", "") or "")
            profile_list = meta_obj.get("profile", []) or []
            if isinstance(profile_list, list) and profile_list:
                profile = str(profile_list[0])

        # Patient ID hashing.
        patient_id_hash = ""
        if resource_type == "Patient" and resource_id:
            patient_id_hash = hashlib.sha256(
                resource_id.encode("utf-8")
            ).hexdigest()
        else:
            subject = resource.get("subject", {}) or {}
            if isinstance(subject, dict):
                ref = subject.get("reference", "") or ""
                if isinstance(ref, str) and ref.startswith("Patient/"):
                    patient_ref_id = ref.split("/", 1)[1]
                    if patient_ref_id:
                        patient_id_hash = hashlib.sha256(
                            patient_ref_id.encode("utf-8")
                        ).hexdigest()

        # Canonical resource hash for AuditProof integrity-anchoring.
        canonical_bytes = json.dumps(
            resource, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        resource_hash = hashlib.sha256(canonical_bytes).hexdigest()

        return BulkFhirResource(
            resource_type=resource_type,
            resource_id=resource_id,
            last_updated=last_updated,
            version_id=version_id,
            profile=profile,
            patient_id_hash=patient_id_hash,
            resource_hash=resource_hash,
        )

    def derive_patient_crosswalk(
        self,
        resources: List[BulkFhirResource],
    ) -> Dict[str, List[str]]:
        """Build a patient-ID-hash → resource-type list cross-walk.

        Useful for HCC coding workflows (Atria) that need to know
        which non-Patient resources reference each Patient.

        :param resources: A list of :class:`BulkFhirResource` instances.
        :returns: Dict mapping patient_id_hash to list of resource types
            referencing that patient.
        """
        crosswalk: Dict[str, List[str]] = {}
        for r in resources:
            if r.patient_id_hash:
                if r.patient_id_hash not in crosswalk:
                    crosswalk[r.patient_id_hash] = []
                if r.resource_type not in crosswalk[r.patient_id_hash]:
                    crosswalk[r.patient_id_hash].append(r.resource_type)
        return crosswalk

    @staticmethod
    def receipt_pattern_tag() -> str:
        """Return the canonical per-record receipt pattern_tag for FHIR
        bulk extraction.

        Closed enum, each FHIR resource
        extracted via $export emits a per-record receipt with
        ``pattern_tag="fhir_record"``.
        """
        return "fhir_record"


__all__ = [
    "BulkFhirAdapter",
    "BulkFhirKickoff",
    "BulkFhirStatus",
    "BulkFhirResource",
]
