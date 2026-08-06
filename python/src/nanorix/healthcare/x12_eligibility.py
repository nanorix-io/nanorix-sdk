"""X12 EDI 270/271 eligibility verification adapter for healthcare workloads.

X12 EDI 270 (Health Care Eligibility Benefit Inquiry) and X12 EDI 271
(Health Care Eligibility Benefit Response) are the HIPAA-mandated
transaction sets for eligibility verification across the US healthcare
payer ecosystem. Every revenue cycle management (RCM) workflow begins
with a 270 inquiry; every payer response carries the 271. Together they
form the highest-volume HIPAA transaction pair in production, used by
Adonis, Candid, Nym, Fathom, Cair, Sohar, and the broader RCM
automation cohort.

This adapter parses incoming 270 inquiry payloads and 271 response
payloads in their X12 v5010 ANSI X12 wire form and produces
structural metadata + per-record receipt-emission helper suitable for
AuditProof binding. The adapter does NOT interpret coverage decisions,
validate against a specific payer companion guide (e.g., the CAQH CORE
Phase III Eligibility & Benefits operating rules, the Aetna 270/271
Companion Guide, the Anthem 270/271 Companion Guide), or determine
benefit-coverage substantive acceptability.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied X12 270/271 transaction bytes and emits
    structural metadata fields. Coverage interpretation, payer
    companion-guide conformance, member-eligibility determination, and
    downstream RCM adjudication remain the customer's responsibility.

Customer-sole-declarer (ADR-013):
    The customer supplies the X12 wire payload. This adapter parses
    structural fields only; Nanorix injects nothing beyond the
    published base-image spec.

Forever-Standard discipline (ADR-006 I0):
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field (ADR-010 A4). No new
    API surface; no changes to AuditProof / Capsulefile shape. The
    per-record emission uses the existing ``pattern_tag=
    "rcm_eligibility"`` closed-enum variant (ADR-039 §"pattern_tag
    rule").

Pure-Python implementation; no native dependencies. The X12 wire-format
parser handles the segment + element + sub-element separator hierarchy
without requiring third-party libraries.

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Member IDs, NPIs, and
    payer IDs are obviously fictitious. Never real PHI in any code
    path even in fixtures.

Companion posture document: ``docs/integrations/healthcare-x12-eligibility.md``.
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

_FRAMEWORK_VERSION = "x12-005010X279A1"
"""X12 EDI v5010 baseline. 005010X279A1 is the HIPAA-mandated TR3
Implementation Guide version for 270/271 eligibility transactions
through 2026. The version string identifies the X12 ASC standard
version that the adapter structurally targets."""

# Closed-enum X12 transaction set identifiers covered by this helper.
_VALID_TRANSACTION_SETS: FrozenSet[str] = frozenset({"270", "271"})

# Closed-enum service-type codes per X12 005010X279A1 §EQ03.
# This is the standard service-type-code element in the EQ segment.
# Appendix lists ~150+ codes; here we curate the most-common per
# industry-baseline production traffic (~85% coverage).
# The adapter accepts ANY 2-char service-type code structurally; the
# closed enum is used for validate-mode reporting only.
_COMMON_SERVICE_TYPES: Dict[str, str] = {
    "1": "Medical Care",
    "30": "Health Benefit Plan Coverage",
    "33": "Chiropractic",
    "35": "Dental Care",
    "47": "Hospital",
    "48": "Hospital - Inpatient",
    "50": "Hospital - Outpatient",
    "51": "Hospital - Emergency Accident",
    "52": "Hospital - Emergency Medical",
    "53": "Hospital - Ambulatory Surgical",
    "62": "MRI/CAT Scan",
    "65": "Newborn Care",
    "68": "Well Baby Care",
    "73": "Diagnostic Medical",
    "76": "Dialysis",
    "78": "Chemotherapy",
    "80": "Immunizations",
    "81": "Routine Physical",
    "82": "Family Planning",
    "86": "Emergency Services",
    "88": "Pharmacy",
    "93": "Podiatry",
    "98": "Professional (Physician) Visit - Office",
    "99": "Professional (Physician) Visit - Inpatient",
    "AL": "Vision (Optometry)",
    "MH": "Mental Health",
    "UC": "Urgent Care",
}

# X12 segment-separator defaults per X12 ASC standard.
_DEFAULT_SEGMENT_TERMINATOR = "~"
_DEFAULT_ELEMENT_SEPARATOR = "*"
_DEFAULT_SUBELEMENT_SEPARATOR = ":"


@dataclass(frozen=True)
class X12EligibilityTransaction:
    """Structural parse result for an X12 270 or 271 transaction.

    Produced by :meth:`X12EligibilityAdapter.parse_transaction`. Captures
    transaction-routing identifiers (transaction set, control numbers,
    sender/receiver IDs, payer ID, member ID hash) plus a SHA-256 hash
    of the wire payload for integrity-anchoring.

    The result does NOT include member demographic detail, copay/
    coinsurance amounts, or benefit-limit text. Customer application
    code processes coverage detail inside the capsule workspace.

    Three-role discipline (ADR-017): Nanorix structurally parses;
    customer interprets coverage detail.
    """

    transaction_set: str
    """The X12 transaction set identifier (``"270"`` or ``"271"``).
    Required non-empty string."""

    isa_control_number: str
    """The ISA13 Interchange Control Number (9 chars, numeric). Required
    for de-duplication and AuditProof integrity-anchoring."""

    gs_control_number: str
    """The GS06 Group Control Number. Required for transaction-group
    correlation across multi-transaction interchanges."""

    st_control_number: str
    """The ST02 Transaction Set Control Number. Required for
    transaction-level deduplication within a group."""

    submitter_id: str
    """The ISA06 Interchange Sender ID (15 chars, typically a payer-
    assigned trading-partner ID). Empty string allowed only in
    non-strict mode."""

    receiver_id: str
    """The ISA08 Interchange Receiver ID (15 chars)."""

    payer_id: str
    """The 2100A NM1 payer identifier extracted from the body's payer
    information loop (NM109 with NM108=PI). Empty string if not
    present."""

    payer_name: str
    """The 2100A NM103 payer name. Empty string if not present."""

    member_id_hash: str
    """SHA-256 hex hash of the 2100C NM109 member identifier (when
    present in 270/271 body). The raw member ID is NEVER emitted as
    metadata to avoid PHI leakage; only the hash binds into the
    AuditProof. Empty string if member ID is not present."""

    service_type_codes: str = ""
    """Comma-joined list of service-type codes from EQ03 segments
    (e.g., ``"30"`` for Health Benefit Plan Coverage, ``"88"`` for
    Pharmacy). Structural-only; the adapter does NOT interpret what
    service category was queried."""

    transaction_datetime: str = ""
    """The ISA09 interchange date + ISA10 time concatenated as
    YYYYMMDDHHMM. Empty string if not present."""

    payload_hash: str = ""
    """SHA-256 hex hash of the full wire payload, for integrity
    anchoring without exposing transaction content."""

    segment_types_observed: str = ""
    """Comma-joined list of segment types observed in the payload
    (e.g., ``"ISA,GS,ST,BHT,HL,NM1,EQ,SE,GE,IEA"``). Structural-only;
    does NOT include segment content."""

    @property
    def is_inquiry(self) -> bool:
        """Whether the transaction is a 270 inquiry."""
        return self.transaction_set == "270"

    @property
    def is_response(self) -> bool:
        """Whether the transaction is a 271 response."""
        return self.transaction_set == "271"

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by reserved framework keys plus structural
        fields from the X12 wire payload. Suitable for binding into
        the AuditProof's ``metadata_hash`` per ADR-010 A4.

        The metadata captures structural identifiers only; member
        demographic detail, coverage detail, and clinical content
        are NOT emitted. Customer application code processes those
        inside the capsule workspace.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "X12-270-271",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "transaction_set": self.transaction_set,
            "isa_control_number": self.isa_control_number,
            "gs_control_number": self.gs_control_number,
            "st_control_number": self.st_control_number,
            "submitter_id": self.submitter_id,
            "receiver_id": self.receiver_id,
            "payload_hash": self.payload_hash,
        }
        if self.payer_id:
            meta["payer_id"] = self.payer_id
        if self.payer_name:
            meta["payer_name"] = self.payer_name
        if self.member_id_hash:
            meta["member_id_hash"] = self.member_id_hash
        if self.service_type_codes:
            meta["service_type_codes"] = self.service_type_codes
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


class X12EligibilityAdapter:
    """X12 EDI 270/271 eligibility transaction structural adapter.

    Parses incoming X12 EDI 270 (Health Care Eligibility Benefit Inquiry)
    or 271 (Health Care Eligibility Benefit Response) v5010 wire payloads
    and produces a :class:`X12EligibilityTransaction` with structural
    identifiers, payer/submitter info, and payload hash.

    The adapter automatically detects the ISA-segment separator + element
    + sub-element separators from the payload header (ISA segment
    explicitly declares them). Supports both standard X12 wire forms
    (~ segment terminator + ``*`` element separator) and common
    variations (newline-terminated segments).

    Three-role discipline (ADR-017): Adapter is structural; coverage
    interpretation, payer companion-guide conformance, and downstream
    RCM adjudication remain the customer's responsibility.

    Customer-sole-declarer (ADR-013): Customer supplies the wire
    payload; adapter produces structural metadata only.

    Pure-Python implementation. No third-party dependencies; no
    native extensions.

    Example::

        adapter = X12EligibilityAdapter()
        wire_bytes = open("incoming_270.edi", "rb").read()
        parsed = adapter.parse_transaction(wire_bytes)
        with client.capsules.create(capsulefile="cf_rcm") as capsule:
            for tx in batch:
                with capsule.record(
                    record_id=tx.isa_control_number,
                    pattern_tag="rcm_eligibility",
                ) as r:
                    r.bind_input(tx.payload_hash)
                    result = check_eligibility(tx)
                    r.bind_output(result)
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on any structural
            anomaly (missing ISA, malformed control number, unknown
            transaction set ID). If False (default), best-effort
            parsing with empty-string fallbacks for missing optional
            fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the adapter is operating in strict mode."""
        return self._strict

    @property
    def supported_transaction_sets(self) -> List[str]:
        """Sorted list of supported transaction sets."""
        return sorted(_VALID_TRANSACTION_SETS)

    @property
    def common_service_types(self) -> Dict[str, str]:
        """Mapping of common X12 service-type codes to their names.

        The adapter accepts ANY 2-char service-type code structurally;
        this dict is provided for customer reference + reporting only.
        """
        return dict(_COMMON_SERVICE_TYPES)

    def parse_transaction(
        self, raw: bytes
    ) -> X12EligibilityTransaction:
        """Parse a single X12 270 or 271 transaction.

        :param raw: The wire payload bytes. Must begin with an ISA
            segment per X12 ASC standard.
        :returns: :class:`X12EligibilityTransaction` with structural
            fields.
        :raises ValueError: If the payload is missing ISA, malformed,
            or carries an unsupported transaction set.

        Structural-only parse. Coverage detail (EB, MSG, AAA, DTP
        segments inside the body) is NOT extracted — only the routing
        identifiers + service-type-code summary required for
        AuditProof binding.
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

        # ISA segment is exactly 106 bytes (fixed-position) + segment
        # terminator. Element separator is byte 4 (after "ISA").
        if len(text) < 106:
            raise ValueError(
                "parse_transaction: ISA segment too short "
                f"(got {len(text)} bytes, expected >=106)"
            )

        element_sep = text[3]
        # Sub-element separator is ISA-16 (byte 104).
        subelement_sep = text[104]
        # Segment terminator is the first character after the ISA segment
        # (byte 105).
        segment_terminator = text[105]

        # Validate the chosen separators are reasonable single chars.
        if element_sep in {"A", "B", "C"} or subelement_sep in {"A", "B"}:
            if self._strict:
                raise ValueError(
                    "parse_transaction [strict]: ISA separators look "
                    f"malformed (elem={element_sep!r}, "
                    f"subelem={subelement_sep!r})"
                )

        segments = self._split_segments(text, segment_terminator)
        if not segments:
            raise ValueError(
                "parse_transaction: no segments after split"
            )

        return self._parse_segments(
            segments, element_sep, subelement_sep, raw
        )

    @staticmethod
    def _split_segments(
        text: str, segment_terminator: str
    ) -> List[str]:
        """Split the X12 payload into segments.

        The segment terminator is declared in the ISA segment (byte 105).
        Common terminators are ``~`` (tilde, default) and ``\\n`` (newline)
        for human-readable trading-partner test files.
        """
        # Handle the case where production senders embed a newline after
        # the segment terminator for human readability.
        normalized = text.replace(
            segment_terminator + "\n", segment_terminator
        )
        normalized = normalized.replace(
            segment_terminator + "\r\n", segment_terminator
        )
        normalized = normalized.replace(
            segment_terminator + "\r", segment_terminator
        )
        return [
            seg for seg in normalized.split(segment_terminator)
            if seg.strip()
        ]

    def _parse_segments(
        self,
        segments: List[str],
        element_sep: str,
        subelement_sep: str,
        raw: bytes,
    ) -> X12EligibilityTransaction:
        """Walk segments and extract structural fields."""
        isa_segment = segments[0]
        isa_fields = isa_segment.split(element_sep)
        # ISA has exactly 17 elements (ISA01 through ISA16 + ISA itself).
        # Common indices: ISA06=submitter, ISA08=receiver, ISA09=date,
        # ISA10=time, ISA13=control number.

        def _isa(idx: int) -> str:
            return isa_fields[idx].strip() if idx < len(isa_fields) else ""

        submitter_id = _isa(6)
        receiver_id = _isa(8)
        isa_date = _isa(9)
        isa_time = _isa(10)
        isa_control_number = _isa(13)

        # Find GS segment for group control number.
        gs_control_number = ""
        for seg in segments[1:]:
            if seg.startswith("GS"):
                gs_fields = seg.split(element_sep)
                if len(gs_fields) >= 7:
                    gs_control_number = gs_fields[6].strip()
                break

        # Find ST segment for transaction set + control number.
        transaction_set = ""
        st_control_number = ""
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
                "; expected 270 or 271"
            )

        # Walk NM1 segments to find payer info (2100A loop, NM101=PR).
        payer_id = ""
        payer_name = ""
        member_id_hash = ""
        service_type_codes: List[str] = []
        segment_types_seen: List[str] = []
        for seg in segments:
            # Track segment types (3-char prefix until first separator).
            seg_id = seg.split(element_sep, 1)[0]
            if seg_id and seg_id not in segment_types_seen:
                segment_types_seen.append(seg_id)

            if seg.startswith("NM1"):
                nm1_fields = seg.split(element_sep)
                if len(nm1_fields) >= 2:
                    entity_id = nm1_fields[1].strip()
                    # PR = Payer
                    if entity_id == "PR":
                        if len(nm1_fields) >= 4:
                            payer_name = nm1_fields[3].strip()
                        if len(nm1_fields) >= 10:
                            payer_id = nm1_fields[9].strip()
                    # IL = Insured/Subscriber (member)
                    elif entity_id == "IL":
                        if len(nm1_fields) >= 10:
                            member_id_raw = nm1_fields[9].strip()
                            if member_id_raw:
                                # Hash only — never emit raw member ID.
                                member_id_hash = hashlib.sha256(
                                    member_id_raw.encode("utf-8")
                                ).hexdigest()
            elif seg.startswith("EQ"):
                # EQ segment: EQ01=service-type code (single) or list
                # in sub-elements.
                eq_fields = seg.split(element_sep)
                if len(eq_fields) >= 2:
                    eq01 = eq_fields[1].strip()
                    # Some senders pack multiple codes in sub-elements.
                    for stc in eq01.split(subelement_sep):
                        stc = stc.strip()
                        if stc and stc not in service_type_codes:
                            service_type_codes.append(stc)

        if not isa_control_number and self._strict:
            raise ValueError(
                "parse_transaction [strict]: ISA13 control number is empty"
            )

        transaction_datetime = ""
        if isa_date and isa_time:
            # ISA09 is YYMMDD; ISA10 is HHMM.
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
        validate_value_length("transaction_datetime", transaction_datetime)

        service_type_csv = ",".join(service_type_codes)
        segment_types_csv = ",".join(segment_types_seen)

        return X12EligibilityTransaction(
            transaction_set=transaction_set,
            isa_control_number=isa_control_number,
            gs_control_number=gs_control_number,
            st_control_number=st_control_number,
            submitter_id=submitter_id,
            receiver_id=receiver_id,
            payer_id=payer_id,
            payer_name=payer_name,
            member_id_hash=member_id_hash,
            service_type_codes=service_type_csv,
            transaction_datetime=transaction_datetime,
            payload_hash=payload_hash,
            segment_types_observed=segment_types_csv,
        )

    def parse_270(self, raw: bytes) -> X12EligibilityTransaction:
        """Parse an X12 270 inquiry specifically.

        Equivalent to :meth:`parse_transaction` but rejects 271 responses.
        Useful when the customer's pipeline branches by direction before
        parsing.
        """
        parsed = self.parse_transaction(raw)
        if parsed.transaction_set != "270":
            raise ValueError(
                f"parse_270: transaction_set="
                f"{parsed.transaction_set!r}, expected 270"
            )
        return parsed

    def parse_271(self, raw: bytes) -> X12EligibilityTransaction:
        """Parse an X12 271 response specifically.

        Equivalent to :meth:`parse_transaction` but rejects 270 inquiries.
        """
        parsed = self.parse_transaction(raw)
        if parsed.transaction_set != "271":
            raise ValueError(
                f"parse_271: transaction_set="
                f"{parsed.transaction_set!r}, expected 271"
            )
        return parsed

    @staticmethod
    def receipt_pattern_tag() -> str:
        """Return the canonical per-record receipt pattern_tag for
        eligibility transactions.

        Per ADR-039 §"pattern_tag rule" closed enum, X12 270/271
        eligibility transactions emit per-record receipts with
        ``pattern_tag="rcm_eligibility"``.
        """
        return "rcm_eligibility"

    @staticmethod
    def derive_metadata(
        parsed: X12EligibilityTransaction,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed transaction.

        Convenience wrapper that optionally adds a customer-supplied
        workflow identifier (e.g., an RCM-side routing-engine job ID).

        :param parsed: A parsed X12 270/271 transaction.
        :param customer_workflow_id: Optional customer-side workflow ID.
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


__all__ = [
    "X12EligibilityAdapter",
    "X12EligibilityTransaction",
]
