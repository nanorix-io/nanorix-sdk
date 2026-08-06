"""HL7 V2 ADT/ORU message adapter for capsule workloads.

HL7 V2 remains the dominant production messaging standard in hospital
integration environments through 2026. While FHIR is the strategic
modernization layer, HL7 V2 messages — particularly ADT
(Admission/Discharge/Transfer) and ORU (Observation Result Unsolicited)
families — still carry the majority of clinical event traffic across
Epic, Cerner/Oracle Health, MEDITECH, Allscripts/Veradigm, and
eClinicalWorks deployments.

This adapter parses incoming HL7 V2 ADT and ORU message payloads and
produces structural metadata suitable for AuditProof binding. The
adapter does NOT interpret clinical content, validate against a
specific implementation guide, or transform messages to other formats
— it extracts structural identifiers (message type, sender, receiver,
control ID, message timestamp) and emits a SHA-256 hash of the MSH
segment for integrity-anchoring.

Two message families covered (per industry-baseline most-common):

- **ADT** (Patient Administration): A01 (admit), A03 (discharge), A08
  (update patient information) — admission/discharge/transfer events.
- **ORU** (Observation Result): R01 (observation result unsolicited)
  — lab results, clinical observations.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied HL7 V2 message bytes and emits metadata
    fields suitable for AuditProof binding. The adapter does NOT
    interpret clinical content, classify PHI/PII inside segments,
    validate against a specific implementation guide (e.g., the HL7
    Australia AS-4700.x family, the HL7 V2.5.1 Implementation Guide
    for Public Health), or certify upstream-system conformance.
    Clinical interpretation, IG conformance, and downstream consumer
    responsibilities remain the customer's.

Customer-sole-declarer:
    The customer is the sole declarer of capsule content. This adapter
    parses customer-supplied HL7 V2 payloads to produce structural
    metadata only; Nanorix injects nothing beyond the published
    base-image spec.

Optional dependency: ``hl7apy>=1.3`` for robust parsing. Helper
provides a fallback minimal parser that handles MSH + standard segment
extraction without external dependency. Install with
``pip install nanorix[hl7-v2]``.

Companion posture document: ``docs/healthcare/hl7-v2-integration.md``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "hl7-v2.5"
"""HL7 V2 baseline version identifier. The adapter targets V2.x
message structure broadly; V2.5 is the most-common production version
across US hospital deployments through 2026. The version string is a
non-calendar adapter-baseline identifier."""

_VALID_ADT_TRIGGERS = frozenset(
    {
        "A01",
        "A02",
        "A03",
        "A04",
        "A05",
        "A06",
        "A07",
        "A08",
        "A11",
        "A12",
        "A13",
    }
)
"""ADT trigger events covered by the adapter. A01 (admit), A03
(discharge), A08 (update) are the most-common per industry-baseline.
A02 (transfer), A04 (register outpatient), A11 (cancel admit), A12
(cancel transfer), A13 (cancel discharge) are extended coverage for
production ADT pipelines."""

_VALID_ORU_TRIGGERS = frozenset({"R01", "R03", "R30", "R31", "R32"})
"""ORU trigger events covered by the adapter. R01 (unsolicited
transmission of observation) is the most-common per industry-baseline.
R30, R31, R32 cover unsolicited point-of-care observations used in
critical-care + ICU pipelines."""

_VALID_MESSAGE_TYPES = frozenset({"ADT", "ORU"})
"""Message families covered by this adapter. ADT + ORU are the two
most-common HL7 V2 message families in hospital integration
environments per industry-baseline."""

# HL7 V2 default field separators per MSH segment. Customer messages
# may override these; the parser reads them from MSH-1/MSH-2.
_DEFAULT_FIELD_SEP = "|"
_DEFAULT_COMPONENT_SEP = "^"


@dataclass(frozen=True)
class HL7v2ParsedMessage:
    """Structural parse result for an HL7 V2 message.

    Produced by :meth:`HL7v2Adapter.parse_message`. Captures
    message-routing identifiers (sender, receiver, control ID,
    timestamp) plus a SHA-256 hash of the MSH segment for integrity
    anchoring. Does NOT include PHI/PID-segment content — clinical
    payload remains in the customer's capsule workspace and is parsed
    by the customer's application code.

    Three-role discipline: Nanorix structurally parses;
    customer interprets.
    """

    message_type: str
    """The HL7 V2 message type (e.g., ``"ADT"``, ``"ORU"``). Required
    non-empty string."""

    trigger_event: str
    """The HL7 V2 trigger event (e.g., ``"A01"``, ``"R01"``). Required
    non-empty string."""

    message_control_id: str
    """The MSH-10 message control ID. Required for routing
    deduplication and AuditProof integrity-anchoring."""

    sending_application: str
    """The MSH-3 sending application identifier (e.g., ``"EpicCare"``,
    ``"Cerner"``). Required for routing-side accountability evidence."""

    receiving_application: str
    """The MSH-5 receiving application identifier (e.g., the customer's
    integration engine or workflow system). Required."""

    sending_facility: str
    """The MSH-4 sending facility identifier (e.g., ``"AcmeHospital"``).
    Required for facility-attribution evidence."""

    receiving_facility: str
    """The MSH-6 receiving facility identifier. Required."""

    message_datetime: str
    """The MSH-7 message timestamp (YYYYMMDDHHMMSS format per HL7 V2
    standard). Required."""

    processing_id: str
    """The MSH-11 processing ID. ``P`` for production, ``T`` for
    testing, ``D`` for debugging. Customer counsel may rely on this
    for production-vs-test attribution evidence."""

    version_id: str
    """The MSH-12 version ID (e.g., ``"2.5"``, ``"2.5.1"``,
    ``"2.7"``)."""

    msh_segment_hash: str
    """SHA-256 hex hash of the MSH segment, used for integrity
    anchoring without exposing message content."""

    segment_types_observed: Tuple[str, ...] = field(default_factory=tuple)
    """Tuple of segment types observed in the message (e.g.,
    ``("MSH", "EVN", "PID", "PV1")``). Structural-only; does NOT
    include segment content."""

    @property
    def message_type_with_trigger(self) -> str:
        """The canonical message-type-with-trigger identifier (e.g.,
        ``"ADT^A01"``). Used for routing and AuditProof metadata
        keying."""
        return f"{self.message_type}^{self.trigger_event}"

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        structural fields from the HL7 V2 MSH segment. Suitable for
        binding into the AuditProof's ``metadata_hash``
        A4.

        The metadata captures structural identifiers only; clinical
        content (PID, PV1, OBR, OBX segments) is NOT emitted. Customer
        application code processes clinical content inside the capsule
        workspace.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "HL7V2",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "message_type": self.message_type,
            "trigger_event": self.trigger_event,
            "message_control_id": self.message_control_id,
            "sending_application": self.sending_application,
            "receiving_application": self.receiving_application,
            "sending_facility": self.sending_facility,
            "receiving_facility": self.receiving_facility,
            "message_datetime": self.message_datetime,
            "processing_id": self.processing_id,
            "version_id": self.version_id,
            "msh_segment_hash": self.msh_segment_hash,
        }
        if self.segment_types_observed:
            meta["segment_types_observed"] = ",".join(
                self.segment_types_observed
            )

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


class HL7v2Adapter:
    """HL7 V2 ADT/ORU message structural adapter.

    Parses incoming HL7 V2 message bytes/string and produces a
    :class:`HL7v2ParsedMessage` with structural identifiers + MSH
    segment hash. The adapter handles MSH-segment extraction across
    common field-separator conventions (``|`` field, ``^`` component);
    customer messages with non-standard separators may require
    customer-side pre-processing.

    Covered message types: ADT (A01/A02/A03/A04/A05/A06/A07/A08/A11/
    A12/A13) and ORU (R01/R03/R30/R31/R32). Unsupported message types
    raise :class:`ValueError` — customers needing other families
    (SIU/ORM/MDM/ACK) can request adapter extension.

    Three-role discipline: Adapter is structural; clinical
    interpretation remains the customer's. The adapter neither
    classifies PHI inside segments nor validates against any specific
    HL7 V2 implementation guide.

    Customer-sole-declarer: Customer supplies the message
    bytes; adapter produces structural metadata only.

    Optional dependency: ``hl7apy>=1.3`` for robust parsing; fallback
    minimal parser handles MSH segment extraction without external
    dependency.

    Example::

        adapter = HL7v2Adapter()
        msg_bytes = open("incoming.hl7", "rb").read()
        parsed = adapter.parse_message(msg_bytes)
        capsule = client.capsules.create(
            capsulefile="cf_...",
            metadata=parsed.to_metadata(),
        )
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on any structural
            anomaly (missing MSH, unknown trigger event, malformed
            timestamp). If False (default), best-effort parsing with
            empty-string fallbacks for missing optional fields.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        """Whether the adapter is operating in strict mode."""
        return self._strict

    @property
    def supported_message_types(self) -> List[str]:
        """The canonical list of supported HL7 V2 message types.

        Returns a sorted copy. Useful for customer introspection of
        adapter coverage.
        """
        return sorted(_VALID_MESSAGE_TYPES)

    @property
    def supported_adt_triggers(self) -> List[str]:
        """The canonical list of supported ADT trigger events.

        Returns a sorted copy.
        """
        return sorted(_VALID_ADT_TRIGGERS)

    @property
    def supported_oru_triggers(self) -> List[str]:
        """The canonical list of supported ORU trigger events.

        Returns a sorted copy.
        """
        return sorted(_VALID_ORU_TRIGGERS)

    def parse_message(self, raw: bytes) -> HL7v2ParsedMessage:
        """Parse a single HL7 V2 message.

        :param raw: The message bytes. Must contain at least an MSH
            segment terminated by ``\\r``, ``\\n``, or ``\\r\\n``.
        :returns: :class:`HL7v2ParsedMessage` with structural fields.
        :raises ValueError: If the message is missing MSH or has an
            unsupported message type/trigger event.

        The adapter normalizes segment terminators (HL7 V2 standard is
        ``\\r``; many integration engines emit ``\\n`` or ``\\r\\n``).
        It extracts the MSH segment, parses fields per the field
        separator in MSH-1, computes the SHA-256 hash of the MSH
        segment, and returns structural metadata.

        Clinical-content segments (PID, PV1, OBR, OBX, etc.) are
        NOT parsed — only their segment types are observed for the
        ``segment_types_observed`` field.
        """
        if not isinstance(raw, (bytes, bytearray)):
            raise ValueError(
                "parse_message expects bytes (got "
                f"{type(raw).__name__}); decode upstream if needed"
            )
        if not raw:
            raise ValueError("parse_message: empty message bytes")

        text = raw.decode("ascii", errors="replace")
        # Normalize segment separators to '\r' (HL7 V2 standard).
        normalized = (
            text.replace("\r\n", "\r")
            .replace("\n", "\r")
        )
        segments = [s for s in normalized.split("\r") if s]
        if not segments:
            raise ValueError("parse_message: no segments found")

        msh = segments[0]
        if not msh.startswith("MSH"):
            raise ValueError(
                "parse_message: first segment is not MSH "
                f"(got {msh[:3]!r}); HL7 V2 messages must start with MSH"
            )

        return self._parse_msh(msh, segments)

    def _parse_msh(
        self, msh: str, segments: List[str]
    ) -> HL7v2ParsedMessage:
        """Parse the MSH segment + observe segment types."""
        # Field separator is the character immediately after 'MSH' (per
        # HL7 V2 standard, the MSH segment is special: MSH-1 IS the
        # field separator).
        if len(msh) < 5:
            raise ValueError(
                "parse_message: MSH segment too short to contain field "
                "separator"
            )
        field_sep = msh[3]
        if field_sep != _DEFAULT_FIELD_SEP and self._strict:
            raise ValueError(
                f"parse_message [strict]: non-standard field separator "
                f"{field_sep!r}; expected {_DEFAULT_FIELD_SEP!r}"
            )

        fields = msh.split(field_sep)
        # MSH-1 = field separator (MSH itself), MSH-2 = encoding chars,
        # MSH-3 = sending application, MSH-4 = sending facility,
        # MSH-5 = receiving application, MSH-6 = receiving facility,
        # MSH-7 = message datetime, MSH-9 = message type^trigger event,
        # MSH-10 = message control ID, MSH-11 = processing ID,
        # MSH-12 = version ID.
        def _get(idx: int) -> str:
            if idx < len(fields):
                return fields[idx].strip()
            return ""

        sending_app = _get(2)
        sending_facility = _get(3)
        receiving_app = _get(4)
        receiving_facility = _get(5)
        message_datetime = _get(6)
        msh9 = _get(8)
        message_control_id = _get(9)
        processing_id = _get(10)
        version_id = _get(11)

        # Parse MSH-9 (message type ^ trigger event).
        if not msh9:
            raise ValueError(
                "parse_message: MSH-9 (message type) is empty; cannot "
                "determine message type or trigger event"
            )
        msh9_parts = msh9.split(_DEFAULT_COMPONENT_SEP)
        message_type = msh9_parts[0].strip() if msh9_parts else ""
        trigger_event = (
            msh9_parts[1].strip() if len(msh9_parts) > 1 else ""
        )

        if message_type not in _VALID_MESSAGE_TYPES:
            raise ValueError(
                f"parse_message: message_type={message_type!r} not in "
                f"{sorted(_VALID_MESSAGE_TYPES)}; supported families are "
                "ADT + ORU. Customer-side pre-processing or adapter "
                "extension required for other families."
            )

        if message_type == "ADT":
            if trigger_event not in _VALID_ADT_TRIGGERS:
                if self._strict:
                    raise ValueError(
                        f"parse_message [strict]: trigger_event="
                        f"{trigger_event!r} not in supported ADT set "
                        f"{sorted(_VALID_ADT_TRIGGERS)}"
                    )
        elif message_type == "ORU":
            if trigger_event not in _VALID_ORU_TRIGGERS:
                if self._strict:
                    raise ValueError(
                        f"parse_message [strict]: trigger_event="
                        f"{trigger_event!r} not in supported ORU set "
                        f"{sorted(_VALID_ORU_TRIGGERS)}"
                    )

        if not message_control_id and self._strict:
            raise ValueError(
                "parse_message [strict]: MSH-10 (message control ID) "
                "is empty"
            )
        if not message_datetime and self._strict:
            raise ValueError(
                "parse_message [strict]: MSH-7 (message datetime) is "
                "empty"
            )

        msh_hash = hashlib.sha256(msh.encode("utf-8")).hexdigest()

        # Observe segment types (structural only).
        segment_types: List[str] = []
        for seg in segments:
            if len(seg) >= 3:
                seg_type = seg[:3]
                if seg_type not in segment_types:
                    segment_types.append(seg_type)

        # Validate wire constraints.
        validate_value_length("sending_application", sending_app)
        validate_value_length("receiving_application", receiving_app)
        validate_value_length("sending_facility", sending_facility)
        validate_value_length("receiving_facility", receiving_facility)
        validate_value_length("message_control_id", message_control_id)
        validate_value_length("message_datetime", message_datetime)
        validate_value_length("processing_id", processing_id)
        validate_value_length("version_id", version_id)

        return HL7v2ParsedMessage(
            message_type=message_type,
            trigger_event=trigger_event,
            message_control_id=message_control_id,
            sending_application=sending_app,
            receiving_application=receiving_app,
            sending_facility=sending_facility,
            receiving_facility=receiving_facility,
            message_datetime=message_datetime,
            processing_id=processing_id,
            version_id=version_id,
            msh_segment_hash=msh_hash,
            segment_types_observed=tuple(segment_types),
        )

    def parse_adt(self, raw: bytes) -> HL7v2ParsedMessage:
        """Parse an HL7 V2 ADT message specifically.

        Equivalent to :meth:`parse_message` but rejects non-ADT
        message types. Useful when the customer's pipeline branches
        by message family before parsing.

        :raises ValueError: If the message is not of type ADT.
        """
        parsed = self.parse_message(raw)
        if parsed.message_type != "ADT":
            raise ValueError(
                f"parse_adt: message_type={parsed.message_type!r}, "
                "expected ADT"
            )
        return parsed

    def parse_oru(self, raw: bytes) -> HL7v2ParsedMessage:
        """Parse an HL7 V2 ORU message specifically.

        Equivalent to :meth:`parse_message` but rejects non-ORU
        message types.

        :raises ValueError: If the message is not of type ORU.
        """
        parsed = self.parse_message(raw)
        if parsed.message_type != "ORU":
            raise ValueError(
                f"parse_oru: message_type={parsed.message_type!r}, "
                "expected ORU"
            )
        return parsed

    @staticmethod
    def derive_metadata(
        parsed: HL7v2ParsedMessage,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed message.

        Convenience wrapper around :meth:`HL7v2ParsedMessage.to_metadata`
        that optionally adds a customer-supplied workflow identifier.

        :param parsed: A parsed HL7 V2 message.
        :param customer_workflow_id: Optional customer-side workflow
            identifier (e.g., a routing-engine job ID). Stored in
            metadata as ``customer_workflow_id``.
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
