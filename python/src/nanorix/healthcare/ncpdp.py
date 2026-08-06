"""NCPDP SCRIPT + Telecom Standard adapter for PBM e-prescription workloads.

NCPDP (National Council for Prescription Drug Programs) standards govern
electronic prescription and pharmacy claim transactions in the US PBM
ecosystem. Two complementary families are covered:

- **SCRIPT v2017071** — electronic prescription (e-Rx) routing between
  prescribers, pharmacies, and PBMs. Wire formats include NEWRX (new
  prescription), REFREQ / REFRES (refill request + response), RXCHG /
  CHGRES (prescription change request + response), CANRX / CANRES
  (cancel prescription + response), RXFILL (fill status notification),
  RXHREQ / RXHRES (medication history request + response), and
  VERIFY / VERRES (verify a prescriber + response).
- **Telecom Standard v5.1 / vD.0** — pharmacy claim billing (B1 / B2 /
  B3) and eligibility/reversal transactions over the PBM switch network.

This adapter parses incoming NCPDP transaction payloads in their canonical
delimited wire form and produces structural metadata + a per-record
receipt-emission helper suitable for AuditProof binding. The adapter
does NOT interpret clinical content, validate against a specific PBM
implementation guide (e.g., Surescripts Routing Service, RelayHealth
Pharmacy, ExpressScripts Direct), interpret drug-therapy intent, or
classify Schedule II-V status authoritatively. Per-record receipt
emission integrates with the ADR-039 per-record-receipt SDK helper
(``Capsule.record(pattern_tag="ncpdp_script")``).

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied NCPDP transaction bytes and emits
    structural metadata fields. Schedule II-V controlled-substance
    status, DEA registration verification, prescriber authority,
    formulary compliance, and downstream PBM adjudication remain the
    customer's responsibility.

Customer-sole-declarer (ADR-013):
    The customer supplies the NCPDP wire payload. This adapter parses
    structural fields only; Nanorix injects nothing beyond the
    published base-image spec.

Forever-Standard discipline (ADR-006 I0):
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field (ADR-010 A4). No new
    API surface; no changes to AuditProof / Capsulefile shape. The
    per-record emission uses the existing ``pattern_tag="ncpdp_script"``
    closed-enum variant (ADR-039 §"pattern_tag rule").

Pure-Python implementation; no native dependencies. The wire-format
parser handles NCPDP's segment + field + group separator hierarchy
without requiring third-party libraries.

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Prescription numbers,
    NPIs, NDCs, patient identifiers are obviously fictitious. Never
    real PHI in any code path even in fixtures.

Companion posture document: ``docs/integrations/healthcare-ncpdp.md``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "ncpdp-script-2017071"
"""NCPDP SCRIPT baseline version identifier. SCRIPT v2017071 is the
production version for e-prescription transactions across the
Surescripts network through 2026. The version string is a non-calendar
adapter-baseline identifier."""

_TELECOM_FRAMEWORK_VERSION = "ncpdp-telecom-D.0"
"""NCPDP Telecom Standard vD.0 is the production version for pharmacy
claim billing transactions through 2026."""

# Closed-enum SCRIPT transaction codes per NCPDP SCRIPT v2017071.
# Append-only per ADR-006 I0 (closed-enum extension follows ADR amendment).
_VALID_SCRIPT_TRANSACTIONS: FrozenSet[str] = frozenset(
    {
        "NEWRX",
        "REFREQ",
        "REFRES",
        "RXCHG",
        "CHGRES",
        "CANRX",
        "CANRES",
        "RXFILL",
        "RXHREQ",
        "RXHRES",
        "VERIFY",
        "VERRES",
        "ERROR",
        "STATUS",
        "PASSWORD",
    }
)

# Closed-enum Telecom transaction codes per vD.0.
_VALID_TELECOM_TRANSACTIONS: FrozenSet[str] = frozenset(
    {
        "B1",  # Billing
        "B2",  # Reversal
        "B3",  # Rebill
        "E1",  # Eligibility verification
        "N1",  # Information reporting
        "N2",  # Information reporting reversal
        "N3",  # Information reporting rebill
        "P1",  # Prior authorization request
        "P2",  # Prior authorization reversal
        "P3",  # Prior authorization inquiry
        "P4",  # Prior authorization request/inquiry
        "C1",  # Controlled substance reporting
        "C2",  # Controlled substance reversal
        "C3",  # Controlled substance rebill
    }
)

# DEA controlled-substance Schedule classifications per 21 CFR 1308.
# Adapter detects the customer-declared schedule value verbatim; does
# NOT authoritatively classify a drug. Customer is responsible for
# DEA-21 CFR 1308 + state-level scheduling determinations.
_VALID_DEA_SCHEDULES: FrozenSet[str] = frozenset(
    {"CII", "CIII", "CIV", "CV"}
)

# NCPDP SCRIPT wire-format field separators.
# SCRIPT (the e-prescription family) is XML-based per v2017071+ but
# carries delimited values inside specific element bodies; the prior
# v8.1/v10.6 wire form used pipe-segment-delimited records similar to
# X12. This adapter accepts both representations and normalizes the
# transaction code + structural identifiers in the same shape.
_FIELD_SEP = "|"
_SEGMENT_SEP = "\x1e"  # ASCII record separator; some NCPDP gateways use
_GROUP_SEP = "\x1d"  # ASCII group separator


@dataclass(frozen=True)
class NCPDPParsedTransaction:
    """Structural parse result for an NCPDP transaction.

    Produced by :meth:`NCPDPAdapter.parse_transaction`. Captures
    transaction-routing identifiers (transaction code, prescriber NPI,
    pharmacy NCPDP ID, message-control identifier) plus a SHA-256 hash
    of the wire payload for integrity-anchoring.

    The result does NOT include drug-therapy details, dose strings,
    sig text, or other clinical-content fields. Customer application
    code processes clinical content inside the capsule workspace.

    Three-role discipline (ADR-017): Nanorix structurally parses;
    customer interprets clinical content.
    """

    transaction_code: str
    """The NCPDP transaction code (e.g., ``"NEWRX"``, ``"REFREQ"``,
    ``"B1"``). Required non-empty string. Validated against the
    SCRIPT + Telecom closed enums."""

    transaction_family: str
    """Either ``"SCRIPT"`` (e-prescription routing) or ``"TELECOM"``
    (pharmacy claim billing). Derived from the transaction code."""

    message_control_id: str
    """The NCPDP message control / prescription reference number.
    Required for routing deduplication and AuditProof integrity-
    anchoring. Empty string allowed only in non-strict mode."""

    sending_application: str
    """The sender application identifier (e.g., ``"Surescripts"``,
    ``"ExpressScripts"``, ``"CVSCaremark"``). Routing-side
    accountability evidence."""

    receiving_application: str
    """The receiver application identifier (e.g., the customer's PBM
    workflow system or pharmacy management system)."""

    prescriber_id: str
    """The prescriber identifier (NPI for SCRIPT; provider ID for
    Telecom). Empty string when not present in the transaction. The
    adapter does NOT verify NPI registration or DEA authority."""

    pharmacy_ncpdp_id: str
    """The pharmacy NCPDP ID (also known as NCPDP Provider ID
    / NABP number). Empty string when not present."""

    transaction_datetime: str
    """The transaction timestamp in YYYYMMDDHHMMSS format. Empty
    string when not present. The adapter does NOT validate timezone
    handling — customer-side processing is responsible."""

    payload_hash: str
    """SHA-256 hex hash of the wire payload, used for integrity
    anchoring without exposing transaction content."""

    dea_schedule: str = ""
    """Customer-declared DEA Schedule classification (``"CII"`` -
    ``"CV"``), or empty string if not declared. The adapter does NOT
    independently classify a drug; the customer's pharmacy management
    system supplies this value (typically derived from the NDC code
    via DEA Form 510 mapping). Reduced-citation discipline: customer
    references 21 CFR 1308 schedules in their own attestation."""

    field_count: int = 0
    """Number of distinct fields observed in the wire payload.
    Structural-only metric; does NOT count clinical content depth."""

    @property
    def is_controlled_substance(self) -> bool:
        """Whether the customer declared a DEA-controlled substance."""
        return self.dea_schedule in _VALID_DEA_SCHEDULES

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by reserved framework keys plus structural
        fields from the NCPDP wire payload. Suitable for binding into
        the AuditProof's ``metadata_hash`` per ADR-010 A4.

        Reserved keys use the NCPDP framework version derived from the
        transaction family. Customer-supplied clinical content (NDC,
        sig, dose, refills authorized, etc.) is NOT emitted — customer
        application code processes those inside the capsule workspace.
        """
        framework_version = (
            _FRAMEWORK_VERSION
            if self.transaction_family == "SCRIPT"
            else _TELECOM_FRAMEWORK_VERSION
        )
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "NCPDP",
            RESERVED_FRAMEWORK_VERSION_KEY: framework_version,
            "transaction_code": self.transaction_code,
            "transaction_family": self.transaction_family,
            "message_control_id": self.message_control_id,
            "sending_application": self.sending_application,
            "receiving_application": self.receiving_application,
            "prescriber_id": self.prescriber_id,
            "pharmacy_ncpdp_id": self.pharmacy_ncpdp_id,
            "transaction_datetime": self.transaction_datetime,
            "payload_hash": self.payload_hash,
            "field_count": str(self.field_count),
        }
        if self.dea_schedule:
            meta["dea_schedule"] = self.dea_schedule

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


class NCPDPAdapter:
    """NCPDP SCRIPT + Telecom Standard transaction structural adapter.

    Parses incoming NCPDP wire payloads (SCRIPT XML-based v2017071+ or
    delimited legacy form; Telecom delimited vD.0) and produces a
    :class:`NCPDPParsedTransaction` with structural identifiers and
    payload hash.

    Covered transaction codes (closed enum per ADR-006 I0):

    - SCRIPT: NEWRX, REFREQ, REFRES, RXCHG, CHGRES, CANRX, CANRES,
      RXFILL, RXHREQ, RXHRES, VERIFY, VERRES, ERROR, STATUS, PASSWORD
    - Telecom: B1, B2, B3 (billing/reversal/rebill); E1 (eligibility);
      N1-N3 (information reporting); P1-P4 (prior authorization);
      C1-C3 (controlled substance reporting)

    Unsupported transaction codes raise :class:`ValueError`.

    Three-role discipline (ADR-017): Adapter is structural; clinical
    interpretation, DEA scheduling, prescriber authority, and PBM
    adjudication remain the customer's responsibility.

    Customer-sole-declarer (ADR-013): Customer supplies the wire
    payload; adapter produces structural metadata only.

    Pure-Python implementation. No third-party dependencies; no native
    extensions.

    Example::

        adapter = NCPDPAdapter()
        wire_bytes = open("incoming.ncpdp", "rb").read()
        parsed = adapter.parse_transaction(wire_bytes)
        with client.capsules.create(capsulefile="cf_pbm") as capsule:
            for tx in batch:
                with capsule.record(
                    record_id=tx.message_control_id,
                    pattern_tag="ncpdp_script",
                ) as r:
                    r.bind_input(tx.payload_hash)
                    result = process_prescription(tx)
                    r.bind_output(result)
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on any structural
            anomaly (missing transaction code, empty control ID,
            unknown DEA schedule string). If False (default),
            best-effort parsing with empty-string fallbacks for
            missing optional fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the adapter is operating in strict mode."""
        return self._strict

    @property
    def supported_script_transactions(self) -> List[str]:
        """Sorted list of supported SCRIPT transaction codes."""
        return sorted(_VALID_SCRIPT_TRANSACTIONS)

    @property
    def supported_telecom_transactions(self) -> List[str]:
        """Sorted list of supported Telecom transaction codes."""
        return sorted(_VALID_TELECOM_TRANSACTIONS)

    @property
    def supported_dea_schedules(self) -> List[str]:
        """Sorted list of accepted DEA Schedule classifications."""
        return sorted(_VALID_DEA_SCHEDULES)

    def parse_transaction(self, raw: bytes) -> NCPDPParsedTransaction:
        """Parse a single NCPDP transaction.

        :param raw: The wire payload bytes. Supports both XML-based
            SCRIPT v2017071+ payloads and legacy delimited payloads.
        :returns: :class:`NCPDPParsedTransaction` with structural fields.
        :raises ValueError: If the payload is empty, malformed, or
            carries an unsupported transaction code.

        The adapter detects the wire format by inspecting the first
        non-whitespace byte: ``<`` indicates XML, anything else is
        treated as delimited.

        Structural-only parse. Clinical content fields (NDC, sig,
        dose, refills authorized, days supply, AWP, dispensed
        quantity, etc.) are NOT extracted — only the routing
        identifiers required for AuditProof binding.
        """
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError(
                "parse_transaction expects bytes (got "
                f"{type(raw).__name__}); decode upstream if needed"
            )
        if not raw:
            raise ValueError("parse_transaction: empty payload")

        text = raw.decode("ascii", errors="replace").strip()
        if not text:
            raise ValueError(
                "parse_transaction: payload is empty after whitespace strip"
            )

        if text.startswith("<"):
            parsed_fields = self._parse_xml(text)
        else:
            parsed_fields = self._parse_delimited(text)

        return self._build_parsed_transaction(parsed_fields, raw)

    def _parse_xml(self, text: str) -> Dict[str, str]:
        """Parse XML-form NCPDP SCRIPT v2017071+ payload.

        Pure structural extraction — does NOT validate against the
        NCPDP SCRIPT XSD. Customer-side conformance validation is
        recommended for production pipelines. The adapter reads the
        outer-most ``<Message>`` element's structural fields without
        third-party XML libraries (the inputs are short, well-formed
        NCPDP SCRIPT messages — a tag-walker is sufficient and avoids
        any XML-parser CVE surface area).
        """
        fields: Dict[str, str] = {}

        # Detect transaction code from the Header / TransactionDomain block.
        # NCPDP SCRIPT v2017071 places the transaction code in
        # /Message/Header/PrescriberOrderNumber/... or as the message-element
        # root. Common transaction-element forms: <NewRx>, <RefillRequest>,
        # <RefillResponse>, <CancelRx>, etc.
        # We map the outer-message element to the canonical code.
        element_to_code = {
            "NewRx": "NEWRX",
            "RefillRequest": "REFREQ",
            "RefillResponse": "REFRES",
            "RxChangeRequest": "RXCHG",
            "RxChangeResponse": "CHGRES",
            "CancelRx": "CANRX",
            "CancelRxResponse": "CANRES",
            "RxFill": "RXFILL",
            "RxHistoryRequest": "RXHREQ",
            "RxHistoryResponse": "RXHRES",
            "Verify": "VERIFY",
            "VerifyResponse": "VERRES",
            "Error": "ERROR",
            "Status": "STATUS",
            "Password": "PASSWORD",
        }
        transaction_code = ""
        for element_name, code in element_to_code.items():
            if f"<{element_name}" in text or f"<{element_name}>" in text:
                transaction_code = code
                break
        fields["transaction_code"] = transaction_code
        fields["transaction_family"] = "SCRIPT"

        # Tag-walker: pull simple text content between named tags.
        # This covers PrescriberOrderNumber, Identification (sender/receiver),
        # Identifier (NPI), PharmacyID/NCPDPID, transaction timestamp.
        fields["message_control_id"] = self._extract_tag_text(
            text, "PrescriberOrderNumber"
        )
        fields["sending_application"] = self._extract_tag_text(
            text, "From"
        ) or self._extract_tag_text(text, "SenderID")
        fields["receiving_application"] = self._extract_tag_text(
            text, "To"
        ) or self._extract_tag_text(text, "ReceiverID")
        fields["prescriber_id"] = self._extract_tag_text(
            text, "Identifier"
        ) or self._extract_tag_text(text, "NPI")
        fields["pharmacy_ncpdp_id"] = self._extract_tag_text(
            text, "NCPDPID"
        ) or self._extract_tag_text(text, "PharmacyID")
        fields["transaction_datetime"] = (
            self._extract_tag_text(text, "TimeStamp")
            or self._extract_tag_text(text, "SentTime")
        )
        fields["dea_schedule"] = self._extract_tag_text(text, "DEASchedule")
        # Count meaningful XML elements (open-tags) for field_count metric.
        fields["field_count_raw"] = str(text.count("<"))
        return fields

    def _parse_delimited(self, text: str) -> Dict[str, str]:
        """Parse legacy delimited NCPDP payload (SCRIPT v8.1/v10.6 or
        Telecom vD.0).

        Delimited NCPDP payloads use segments separated by either ASCII
        record-separator (``\\x1e``), group-separator (``\\x1d``), or
        newline. Within a segment, fields are separated by either pipe
        (``|``) or NCPDP field-separator (``\\x1f``).

        Production NCPDP Telecom payloads typically begin with a header
        block containing the transaction code (``B1``, ``B2``, etc.) as
        the second field of the header segment.
        """
        # Normalize segment separators.
        normalized = (
            text.replace(_SEGMENT_SEP, "\n")
            .replace(_GROUP_SEP, "\n")
        )
        # Some test fixtures use CR (\r) as segment separator.
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        segments = [
            seg for seg in normalized.split("\n") if seg.strip()
        ]
        if not segments:
            raise ValueError(
                "parse_transaction: no segments after normalization"
            )

        header = segments[0]
        fields = header.split(_FIELD_SEP)
        if not fields:
            raise ValueError(
                "parse_transaction: header segment is empty after split"
            )

        # Map header[0]/header[1] to transaction code per family.
        # Telecom convention: header[0]="HEADER", header[1]=transaction code
        # (e.g., "B1"). SCRIPT legacy convention: header[0]="UIH" or similar.
        transaction_code = ""
        transaction_family = ""
        if len(fields) >= 2:
            candidate = fields[1].strip().upper()
            if candidate in _VALID_TELECOM_TRANSACTIONS:
                transaction_code = candidate
                transaction_family = "TELECOM"
            elif candidate in _VALID_SCRIPT_TRANSACTIONS:
                transaction_code = candidate
                transaction_family = "SCRIPT"
        if not transaction_code and len(fields) >= 1:
            candidate = fields[0].strip().upper()
            if candidate in _VALID_TELECOM_TRANSACTIONS:
                transaction_code = candidate
                transaction_family = "TELECOM"
            elif candidate in _VALID_SCRIPT_TRANSACTIONS:
                transaction_code = candidate
                transaction_family = "SCRIPT"

        # Walk segments for known field names. NCPDP legacy uses fixed
        # field positions but production senders frequently key fields.
        # The adapter accepts ``KEY=VALUE`` form within segments.
        def _kv_lookup(key: str) -> str:
            key_lower = key.lower()
            for seg in segments:
                for fld in seg.split(_FIELD_SEP):
                    if "=" in fld:
                        k, _, v = fld.partition("=")
                        if k.strip().lower() == key_lower:
                            return v.strip()
            return ""

        parsed: Dict[str, str] = {
            "transaction_code": transaction_code,
            "transaction_family": transaction_family,
            "message_control_id": _kv_lookup("messagecontrolid")
            or _kv_lookup("prescribernumber")
            or _kv_lookup("prescriptionnumber")
            or _kv_lookup("rxnumber"),
            "sending_application": _kv_lookup("sendingapplication")
            or _kv_lookup("senderid")
            or _kv_lookup("from"),
            "receiving_application": _kv_lookup("receivingapplication")
            or _kv_lookup("receiverid")
            or _kv_lookup("to"),
            "prescriber_id": _kv_lookup("prescriberid")
            or _kv_lookup("npi"),
            "pharmacy_ncpdp_id": _kv_lookup("pharmacyncpdpid")
            or _kv_lookup("ncpdpid")
            or _kv_lookup("nabpnumber"),
            "transaction_datetime": _kv_lookup("transactiondatetime")
            or _kv_lookup("timestamp")
            or _kv_lookup("senttime"),
            "dea_schedule": _kv_lookup("deaschedule")
            or _kv_lookup("schedule"),
            "field_count_raw": str(
                sum(len(seg.split(_FIELD_SEP)) for seg in segments)
            ),
        }
        return parsed

    @staticmethod
    def _extract_tag_text(text: str, tag: str) -> str:
        """Extract the text content of the first occurrence of ``<tag>``.

        Returns empty string if the tag is absent. Pure string scanner;
        does NOT handle XML namespaces, CDATA, or entity escaping.
        Customer-side schema validation is recommended for production
        deployments where these forms appear.
        """
        open_tag = f"<{tag}"
        close_tag = f"</{tag}>"
        start = text.find(open_tag)
        if start == -1:
            return ""
        # Find the closing ``>`` of the open tag (handles attributes).
        gt = text.find(">", start)
        if gt == -1:
            return ""
        # Self-closing tag.
        if text[gt - 1] == "/":
            return ""
        end = text.find(close_tag, gt + 1)
        if end == -1:
            return ""
        return text[gt + 1 : end].strip()

    def _build_parsed_transaction(
        self, fields: Mapping[str, str], raw: bytes
    ) -> NCPDPParsedTransaction:
        """Assemble :class:`NCPDPParsedTransaction` from parsed fields."""
        transaction_code = fields.get("transaction_code", "")
        if not transaction_code:
            raise ValueError(
                "parse_transaction: could not determine transaction_code; "
                f"expected one of {sorted(_VALID_SCRIPT_TRANSACTIONS | _VALID_TELECOM_TRANSACTIONS)}"
            )
        transaction_family = fields.get("transaction_family", "")
        if transaction_code in _VALID_SCRIPT_TRANSACTIONS:
            transaction_family = transaction_family or "SCRIPT"
        elif transaction_code in _VALID_TELECOM_TRANSACTIONS:
            transaction_family = transaction_family or "TELECOM"
        else:
            raise ValueError(
                f"parse_transaction: transaction_code={transaction_code!r} "
                f"not in NCPDP SCRIPT or Telecom closed enum. "
                f"Customer-side pre-processing or adapter extension required."
            )

        message_control_id = fields.get("message_control_id", "")
        if self._strict and not message_control_id:
            raise ValueError(
                "parse_transaction [strict]: message_control_id is empty"
            )

        dea_schedule = fields.get("dea_schedule", "").upper()
        if dea_schedule and dea_schedule not in _VALID_DEA_SCHEDULES:
            if self._strict:
                raise ValueError(
                    f"parse_transaction [strict]: dea_schedule="
                    f"{dea_schedule!r} not in {sorted(_VALID_DEA_SCHEDULES)}"
                )
            # Non-strict: clear out unknown schedule rather than emit it.
            dea_schedule = ""

        payload_hash = hashlib.sha256(raw).hexdigest()

        try:
            field_count = int(fields.get("field_count_raw", "0"))
        except ValueError:
            field_count = 0

        # Validate wire-length constraints.
        validate_value_length("transaction_code", transaction_code)
        validate_value_length(
            "message_control_id", message_control_id
        )
        validate_value_length(
            "sending_application", fields.get("sending_application", "")
        )
        validate_value_length(
            "receiving_application",
            fields.get("receiving_application", ""),
        )
        validate_value_length(
            "prescriber_id", fields.get("prescriber_id", "")
        )
        validate_value_length(
            "pharmacy_ncpdp_id", fields.get("pharmacy_ncpdp_id", "")
        )
        validate_value_length(
            "transaction_datetime",
            fields.get("transaction_datetime", ""),
        )

        return NCPDPParsedTransaction(
            transaction_code=transaction_code,
            transaction_family=transaction_family,
            message_control_id=message_control_id,
            sending_application=fields.get("sending_application", ""),
            receiving_application=fields.get(
                "receiving_application", ""
            ),
            prescriber_id=fields.get("prescriber_id", ""),
            pharmacy_ncpdp_id=fields.get("pharmacy_ncpdp_id", ""),
            transaction_datetime=fields.get(
                "transaction_datetime", ""
            ),
            payload_hash=payload_hash,
            dea_schedule=dea_schedule,
            field_count=field_count,
        )

    def parse_script(self, raw: bytes) -> NCPDPParsedTransaction:
        """Parse a SCRIPT-family transaction specifically.

        Equivalent to :meth:`parse_transaction` but rejects Telecom
        transaction codes. Useful when the customer's pipeline branches
        by family before parsing.
        """
        parsed = self.parse_transaction(raw)
        if parsed.transaction_family != "SCRIPT":
            raise ValueError(
                f"parse_script: transaction_family="
                f"{parsed.transaction_family!r}, expected SCRIPT"
            )
        return parsed

    def parse_telecom(self, raw: bytes) -> NCPDPParsedTransaction:
        """Parse a Telecom-family transaction specifically.

        Equivalent to :meth:`parse_transaction` but rejects SCRIPT
        transaction codes.
        """
        parsed = self.parse_transaction(raw)
        if parsed.transaction_family != "TELECOM":
            raise ValueError(
                f"parse_telecom: transaction_family="
                f"{parsed.transaction_family!r}, expected TELECOM"
            )
        return parsed

    @staticmethod
    def receipt_pattern_tag() -> str:
        """Return the canonical per-record receipt pattern_tag for NCPDP.

        Per ADR-039 §"pattern_tag rule" closed enum, NCPDP transactions
        emit per-record receipts with ``pattern_tag="ncpdp_script"``.
        Customers using ``Capsule.record(pattern_tag=...)`` should call
        this helper to avoid hardcoded string drift.
        """
        return "ncpdp_script"

    @staticmethod
    def derive_metadata(
        parsed: NCPDPParsedTransaction,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed transaction.

        Convenience wrapper that optionally adds a customer-supplied
        workflow identifier (e.g., a PBM-side routing-engine job ID).

        :param parsed: A parsed NCPDP transaction.
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
    "NCPDPAdapter",
    "NCPDPParsedTransaction",
]
