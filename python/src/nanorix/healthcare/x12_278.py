"""X12 EDI 278 services review / prior authorization adapter for healthcare workloads.

X12 EDI 278 (Health Care Services Review - Request for Review and
Response) is the HIPAA-mandated transaction set for prior authorization
(PA) and utilization management (UM) review across the US healthcare
payer ecosystem. Every prior-auth workflow uses 278; the explosive
adoption of automated PA decisioning by Banjo, Itiliti, Humata, and the
broader PA-automation cohort makes 278 the highest-strategic-value HIPAA
transaction pair as of 2026.

This adapter parses incoming 278 review-request and review-response
payloads in their X12 v5010 ANSI X12 wire form and produces structural
metadata + per-record receipt-emission helper suitable for AuditProof
binding. The adapter does NOT interpret medical-necessity criteria,
validate against a specific payer companion guide, or determine
authorization-decision substantive acceptability.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied X12 278 transaction bytes and emits
    structural metadata fields. Medical-necessity adjudication, payer
    companion-guide conformance, peer-review interpretation, and
    downstream PA decisioning remain the customer's responsibility.

Customer-sole-declarer:
    The customer supplies the X12 wire payload. This adapter parses
    structural fields only; Nanorix injects nothing beyond the
    published base-image spec.

Forever-Standard discipline:
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field. No new
    API surface; no changes to AuditProof / Capsulefile shape. The
    per-record emission uses the existing ``pattern_tag="pa"``
    closed-enum variant.

Pure-Python implementation; no native dependencies.

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Patient IDs, NPIs,
    provider IDs, and certification IDs are obviously fictitious.
    Never real PHI in any code path even in fixtures.

Companion posture document: ``docs/integrations/healthcare-x12-278.md``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "x12-005010X217"
"""X12 EDI v5010 baseline. 005010X217 is the HIPAA-mandated TR3
Implementation Guide version for 278 services-review transactions
through 2026."""

# 278 is one transaction set with two functional variants distinguished
# by the BHT06 reference qualifier:
#   13 = Health Care Services Review Information (request)
#   11 = Response to a Health Care Services Review (response)
_VALID_TRANSACTION_SETS: FrozenSet[str] = frozenset({"278"})

# Closed-enum review-request types (BHT06).
_REQUEST_TYPES: Dict[str, str] = {
    "13": "Health Care Services Review Information (Request)",
    "11": "Response to Health Care Services Review",
}

# Closed-enum certification action codes (HCR01 in 278 response).
# Per X12 278 IG, HCR01 carries the certification action result.
_CERT_ACTIONS: Dict[str, str] = {
    "A1": "Certified in Total",
    "A2": "Certified Partial",
    "A3": "Not Certified",
    "A4": "Pended",
    "A5": "Cancelled",
    "A6": "Modified",
    "C": "Cancel",
    "CT": "Contact Payer",
    "F": "Final",
    "M": "Modified",
    "NA": "No Action Required",
    "P": "Pended",
}

# Closed-enum service-review types (UM01 in 2000E loop).
_REVIEW_TYPES: Dict[str, str] = {
    "AR": "Admission Review",
    "HS": "Health Services Review",
    "IR": "Initial Review",
    "SC": "Specialty Care Review",
}

# Common UM02 certification-request categories per the X12 278 IG.
_CERT_REQUEST_CATEGORIES: Dict[str, str] = {
    "AR": "Admission Review",
    "CH": "Chiropractic",
    "CO": "Consultation",
    "DM": "Durable Medical Equipment",
    "DS": "Diagnostic Services",
    "HC": "Health Care Provider",
    "HH": "Home Health Care",
    "MS": "Medical Services",
    "NS": "Nursing Services",
    "PT": "Physical Therapy",
    "RN": "Renal Care",
    "RT": "Radiation Therapy",
    "SC": "Specialty Care",
    "SK": "Skilled Nursing Facility",
}


@dataclass(frozen=True)
class X12ServicesReviewTransaction:
    """Structural parse result for an X12 278 transaction.

    Produced by :meth:`X12ServicesReviewAdapter.parse_transaction`.
    Captures transaction-routing identifiers (transaction set, control
    numbers, sender/receiver IDs, payer ID, certification action) plus
    a SHA-256 hash of the wire payload for integrity-anchoring.

    The result does NOT include medical-necessity rationale text,
    clinical-content blob hashes, or peer-review note bodies. Customer
    application code processes clinical content inside the capsule
    workspace.

    Three-role discipline: Nanorix structurally parses;
    customer interprets medical necessity.
    """

    transaction_set: str
    """The X12 transaction set identifier (always ``"278"``)."""

    direction: str
    """Either ``"REQUEST"`` (BHT06=13) or ``"RESPONSE"`` (BHT06=11)."""

    isa_control_number: str
    """The ISA13 Interchange Control Number."""

    gs_control_number: str
    """The GS06 Group Control Number."""

    st_control_number: str
    """The ST02 Transaction Set Control Number."""

    submitter_id: str
    """The ISA06 Interchange Sender ID."""

    receiver_id: str
    """The ISA08 Interchange Receiver ID."""

    payer_id: str
    """The 2010A NM1 payer identifier."""

    payer_name: str
    """The 2010A NM103 payer name."""

    requesting_provider_npi: str
    """The 2010B NM109 requesting-provider NPI (entity ID = 1P). Empty
    string when not present."""

    service_provider_npi: str
    """The 2010F NM109 service-provider NPI (entity ID = SJ). Empty
    string when not present."""

    member_id_hash: str
    """SHA-256 hex hash of the 2010C NM109 member identifier. The raw
    member ID is NEVER emitted as metadata. Empty string if member ID
    is not present."""

    certification_request_category: str = ""
    """The UM02 certification-request category code (e.g., ``"AR"`` for
    admission review, ``"DS"`` for diagnostic services). Empty string
    if not present."""

    review_type: str = ""
    """The UM01 service-review type code. Empty string if not present."""

    certification_action: str = ""
    """The HCR01 certification action result (e.g., ``"A1"`` certified
    in total, ``"A3"`` not certified). Present only on RESPONSE direction;
    empty string on REQUEST direction or when absent."""

    certification_number: str = ""
    """The REF certification-issue number (PA reference number). Used
    for cross-correlation between request and response transactions."""

    transaction_datetime: str = ""
    """The ISA09 interchange date + ISA10 time concatenated as
    YYYYMMDDHHMM."""

    payload_hash: str = ""
    """SHA-256 hex hash of the full wire payload."""

    segment_types_observed: str = ""
    """Comma-joined list of segment types observed."""

    @property
    def is_request(self) -> bool:
        """Whether the transaction is a 278 REQUEST (BHT06=13)."""
        return self.direction == "REQUEST"

    @property
    def is_response(self) -> bool:
        """Whether the transaction is a 278 RESPONSE (BHT06=11)."""
        return self.direction == "RESPONSE"

    @property
    def is_approved(self) -> bool:
        """Convenience: whether the response indicates a positive
        certification result (A1 certified in total, A2 certified
        partial). Customer is responsible for interpreting any partial-
        approval rationale at role 3."""
        return self.certification_action in ("A1", "A2")

    @property
    def is_denied(self) -> bool:
        """Convenience: whether the response indicates a negative
        certification result (A3 not certified). Customer is responsible
        for medical-necessity appeal logic at role 3."""
        return self.certification_action == "A3"

    @property
    def is_pended(self) -> bool:
        """Convenience: whether the response is pended (A4 pended)."""
        return self.certification_action in ("A4", "P")

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by reserved framework keys plus structural
        fields from the X12 wire payload. Suitable for binding into
        the AuditProof's ``metadata_hash``.

        Field selection prioritizes role-3 (auditor) routing-evidence
        needs while staying under the 16-key MAX_METADATA_KEYS limit.
        Submitter/receiver/GS control numbers are NOT in the metadata
        dict (they are available on the dataclass for application code
        but are duplicated by ISA control number for AuditProof routing
        evidence).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "X12-278",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "transaction_set": self.transaction_set,
            "direction": self.direction,
            "isa_control_number": self.isa_control_number,
            "st_control_number": self.st_control_number,
            "payload_hash": self.payload_hash,
        }
        if self.payer_id:
            meta["payer_id"] = self.payer_id
        if self.payer_name:
            meta["payer_name"] = self.payer_name
        if self.requesting_provider_npi:
            meta["requesting_provider_npi"] = self.requesting_provider_npi
        if self.service_provider_npi:
            meta["service_provider_npi"] = self.service_provider_npi
        if self.member_id_hash:
            meta["member_id_hash"] = self.member_id_hash
        if self.certification_request_category:
            meta["certification_request_category"] = (
                self.certification_request_category
            )
        if self.review_type:
            meta["review_type"] = self.review_type
        if self.certification_action:
            meta["certification_action"] = self.certification_action
        if self.certification_number:
            meta["certification_number"] = self.certification_number
        if self.transaction_datetime:
            meta["transaction_datetime"] = self.transaction_datetime

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


class X12ServicesReviewAdapter:
    """X12 EDI 278 services-review transaction structural adapter.

    Parses incoming X12 EDI 278 (Health Care Services Review - Request
    for Review and Response) v5010 wire payloads and produces a
    :class:`X12ServicesReviewTransaction` with structural identifiers.

    The adapter auto-detects ISA segment separators (element /
    sub-element / terminator) and walks segments to extract:

    - Direction (REQUEST vs RESPONSE per BHT06=13/11)
    - Payer info (2010A NM1 PR loop)
    - Requesting + service provider NPIs (2010B / 2010F)
    - Member ID (hashed for PHI discipline)
    - Certification request category (UM02)
    - Review type (UM01)
    - Certification action result (HCR01 — RESPONSE only)
    - Certification number (REF — for request/response correlation)

    Three-role discipline: Adapter is structural; medical-
    necessity adjudication, peer-review interpretation, and PA
    decisioning remain the customer's responsibility.

    Pure-Python implementation. No third-party dependencies.

    Example::

        adapter = X12ServicesReviewAdapter()
        wire_bytes = open("incoming_278.edi", "rb").read()
        parsed = adapter.parse_transaction(wire_bytes)
        with client.capsules.create(capsulefile="cf_pa") as capsule:
            for tx in batch:
                with capsule.record(
                    record_id=tx.certification_number or tx.isa_control_number,
                    pattern_tag="pa",
                ) as r:
                    r.bind_input(tx.payload_hash)
                    decision = process_pa_review(tx)
                    r.bind_output(decision)
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on structural
            anomalies. If False (default), best-effort parse with
            empty-string fallbacks.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        return self._strict

    @property
    def supported_transaction_sets(self) -> List[str]:
        return sorted(_VALID_TRANSACTION_SETS)

    @property
    def certification_actions(self) -> Dict[str, str]:
        """Mapping of HCR01 certification-action codes to descriptions."""
        return dict(_CERT_ACTIONS)

    @property
    def review_types(self) -> Dict[str, str]:
        """Mapping of UM01 review-type codes to descriptions."""
        return dict(_REVIEW_TYPES)

    @property
    def certification_request_categories(self) -> Dict[str, str]:
        """Mapping of UM02 certification-request category codes."""
        return dict(_CERT_REQUEST_CATEGORIES)

    def parse_transaction(
        self, raw: bytes
    ) -> X12ServicesReviewTransaction:
        """Parse a single X12 278 transaction.

        :param raw: The wire payload bytes. Must begin with an ISA
            segment per X12 ASC standard.
        :returns: :class:`X12ServicesReviewTransaction` with structural
            fields.
        :raises ValueError: If the payload is missing ISA, malformed,
            or carries a non-278 transaction set.

        Structural-only parse. Clinical-content blobs (peer-review
        text, medical-necessity rationale, supporting-documentation
        attachments) are NOT extracted — only the routing identifiers
        + certification action result required for AuditProof binding.
        """
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError(
                "parse_transaction expects bytes (got "
                f"{type(raw).__name__}); decode upstream if needed"
            )
        if not raw:
            raise ValueError("parse_transaction: empty payload")

        text = raw.decode("ascii", errors="replace")
        if not text.startswith("ISA"):
            raise ValueError(
                "parse_transaction: payload does not begin with ISA "
                "segment per X12 ASC standard"
            )
        if len(text) < 106:
            raise ValueError(
                f"parse_transaction: ISA segment too short "
                f"(got {len(text)} bytes, expected >=106)"
            )

        element_sep = text[3]
        subelement_sep = text[104]
        segment_terminator = text[105]

        # Normalize segments.
        normalized = text.replace(
            segment_terminator + "\n", segment_terminator
        )
        normalized = normalized.replace(
            segment_terminator + "\r\n", segment_terminator
        )
        normalized = normalized.replace(
            segment_terminator + "\r", segment_terminator
        )
        segments = [
            seg for seg in normalized.split(segment_terminator)
            if seg.strip()
        ]

        return self._parse_segments(
            segments, element_sep, subelement_sep, raw
        )

    def _parse_segments(
        self,
        segments: List[str],
        element_sep: str,
        subelement_sep: str,
        raw: bytes,
    ) -> X12ServicesReviewTransaction:
        """Walk segments and extract structural fields."""
        isa_segment = segments[0]
        isa_fields = isa_segment.split(element_sep)

        def _isa(idx: int) -> str:
            return isa_fields[idx].strip() if idx < len(isa_fields) else ""

        submitter_id = _isa(6)
        receiver_id = _isa(8)
        isa_date = _isa(9)
        isa_time = _isa(10)
        isa_control_number = _isa(13)

        gs_control_number = ""
        for seg in segments[1:]:
            if seg.startswith("GS"):
                gs_fields = seg.split(element_sep)
                if len(gs_fields) >= 7:
                    gs_control_number = gs_fields[6].strip()
                break

        transaction_set = ""
        st_control_number = ""
        direction = ""
        for seg in segments:
            if seg.startswith("ST"):
                st_fields = seg.split(element_sep)
                if len(st_fields) >= 3:
                    transaction_set = st_fields[1].strip()
                    st_control_number = st_fields[2].strip()
                break

        if transaction_set not in _VALID_TRANSACTION_SETS:
            raise ValueError(
                f"parse_transaction: transaction_set="
                f"{transaction_set!r} not in {sorted(_VALID_TRANSACTION_SETS)}"
                "; expected 278"
            )

        # BHT06 determines direction (13 = request, 11 = response).
        for seg in segments:
            if seg.startswith("BHT"):
                bht_fields = seg.split(element_sep)
                if len(bht_fields) >= 3:
                    bht02 = bht_fields[2].strip()
                    if bht02 == "13":
                        direction = "REQUEST"
                    elif bht02 == "11":
                        direction = "RESPONSE"
                break

        if not direction and self._strict:
            raise ValueError(
                "parse_transaction [strict]: could not determine "
                "direction from BHT segment"
            )
        if not direction:
            # Default REQUEST if absent — conservative fallback.
            direction = "REQUEST"

        payer_id = ""
        payer_name = ""
        requesting_provider_npi = ""
        service_provider_npi = ""
        member_id_hash = ""
        certification_request_category = ""
        review_type = ""
        certification_action = ""
        certification_number = ""
        segment_types_seen: List[str] = []

        for seg in segments:
            seg_id = seg.split(element_sep, 1)[0]
            if seg_id and seg_id not in segment_types_seen:
                segment_types_seen.append(seg_id)

            if seg.startswith("NM1"):
                nm1_fields = seg.split(element_sep)
                if len(nm1_fields) >= 2:
                    entity_id = nm1_fields[1].strip()
                    # PR = Payer; X3 = Utilization Management Org per
                    # X12 005010X217 IG (payer-side reviewer for 278).
                    if entity_id in ("PR", "X3"):
                        if len(nm1_fields) >= 4:
                            payer_name = nm1_fields[3].strip()
                        if len(nm1_fields) >= 10:
                            payer_id = nm1_fields[9].strip()
                    # 1P = Requesting Provider
                    elif entity_id == "1P":
                        if len(nm1_fields) >= 10:
                            requesting_provider_npi = nm1_fields[9].strip()
                    # SJ = Service Provider
                    elif entity_id == "SJ":
                        if len(nm1_fields) >= 10:
                            service_provider_npi = nm1_fields[9].strip()
                    # IL = Subscriber / Member
                    elif entity_id == "IL":
                        if len(nm1_fields) >= 10:
                            member_id_raw = nm1_fields[9].strip()
                            if member_id_raw:
                                member_id_hash = hashlib.sha256(
                                    member_id_raw.encode("utf-8")
                                ).hexdigest()
            elif seg.startswith("UM"):
                um_fields = seg.split(element_sep)
                if len(um_fields) >= 2:
                    review_type = um_fields[1].strip()
                if len(um_fields) >= 3:
                    certification_request_category = um_fields[2].strip()
            elif seg.startswith("HCR"):
                hcr_fields = seg.split(element_sep)
                if len(hcr_fields) >= 2:
                    certification_action = hcr_fields[1].strip()
                if len(hcr_fields) >= 3:
                    # HCR02 carries the certification-issue number.
                    if not certification_number:
                        certification_number = hcr_fields[2].strip()
            elif seg.startswith("REF"):
                ref_fields = seg.split(element_sep)
                if len(ref_fields) >= 3:
                    qualifier = ref_fields[1].strip()
                    # REF*BB = Authorization Number; REF*G1 = Prior
                    # Authorization Number; REF*1G = Provider UPIN.
                    if qualifier in ("BB", "G1"):
                        certification_number = ref_fields[2].strip()

        transaction_datetime = ""
        if isa_date and isa_time:
            transaction_datetime = (isa_date + isa_time).strip()

        payload_hash = hashlib.sha256(raw).hexdigest()

        # Validate wire-length constraints.
        validate_value_length("isa_control_number", isa_control_number)
        validate_value_length("gs_control_number", gs_control_number)
        validate_value_length("st_control_number", st_control_number)
        validate_value_length("submitter_id", submitter_id)
        validate_value_length("receiver_id", receiver_id)
        validate_value_length("payer_id", payer_id)
        validate_value_length("payer_name", payer_name)
        validate_value_length(
            "requesting_provider_npi", requesting_provider_npi
        )
        validate_value_length(
            "service_provider_npi", service_provider_npi
        )
        validate_value_length(
            "certification_number", certification_number
        )

        segment_types_csv = ",".join(segment_types_seen)

        return X12ServicesReviewTransaction(
            transaction_set=transaction_set,
            direction=direction,
            isa_control_number=isa_control_number,
            gs_control_number=gs_control_number,
            st_control_number=st_control_number,
            submitter_id=submitter_id,
            receiver_id=receiver_id,
            payer_id=payer_id,
            payer_name=payer_name,
            requesting_provider_npi=requesting_provider_npi,
            service_provider_npi=service_provider_npi,
            member_id_hash=member_id_hash,
            certification_request_category=certification_request_category,
            review_type=review_type,
            certification_action=certification_action,
            certification_number=certification_number,
            transaction_datetime=transaction_datetime,
            payload_hash=payload_hash,
            segment_types_observed=segment_types_csv,
        )

    def parse_request(
        self, raw: bytes
    ) -> X12ServicesReviewTransaction:
        """Parse a 278 REQUEST specifically (BHT06=13).

        :raises ValueError: If the parsed direction is not REQUEST.
        """
        parsed = self.parse_transaction(raw)
        if not parsed.is_request:
            raise ValueError(
                f"parse_request: direction={parsed.direction!r}, "
                "expected REQUEST"
            )
        return parsed

    def parse_response(
        self, raw: bytes
    ) -> X12ServicesReviewTransaction:
        """Parse a 278 RESPONSE specifically (BHT06=11).

        :raises ValueError: If the parsed direction is not RESPONSE.
        """
        parsed = self.parse_transaction(raw)
        if not parsed.is_response:
            raise ValueError(
                f"parse_response: direction={parsed.direction!r}, "
                "expected RESPONSE"
            )
        return parsed

    @staticmethod
    def receipt_pattern_tag() -> str:
        """Return the canonical per-record receipt pattern_tag for PA.

        Closed enum, X12 278 PA reviews
        emit per-record receipts with ``pattern_tag="pa"``. This is the
        same pattern_tag used by other PA workflows (e.g., Anterior's
        decision-agent pipeline).
        """
        return "pa"

    @staticmethod
    def derive_metadata(
        parsed: X12ServicesReviewTransaction,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed 278 transaction."""
        meta = parsed.to_metadata()
        if customer_workflow_id is not None:
            validate_value_length(
                "customer_workflow_id", customer_workflow_id
            )
            if len(meta) >= MAX_METADATA_KEYS:
                # Prune optional fields to make room.
                # Keep core routing fields; drop transaction_datetime.
                meta.pop("transaction_datetime", None)
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


__all__ = [
    "X12ServicesReviewAdapter",
    "X12ServicesReviewTransaction",
]
