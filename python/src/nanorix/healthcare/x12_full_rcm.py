"""X12 EDI 834/835/837 full revenue-cycle adapter for healthcare workloads.

X12 EDI 834 (Benefit Enrollment and Maintenance), 835 (Health Care
Claim Payment/Advice), and 837 (Health Care Claim) are the HIPAA-mandated
transaction sets covering the full healthcare revenue cycle:

- **834** carries enrollment / maintenance transactions between employers
  (or sponsors) and payers. Members are added, terminated, or updated.
  Per X12 v5010 005010X220A1.
- **835** carries payment / remittance advice from payers to providers
  detailing how individual claims were adjudicated. Per X12 v5010
  005010X221A1.
- **837** carries the actual healthcare claim from provider to payer.
  Three flavors covered:
  * **837P** (Professional, 005010X222A1) — physician-office claims
  * **837I** (Institutional, 005010X223A2) — hospital/facility claims
  * **837D** (Dental, 005010X224A2) — dental practice claims

Together these three transaction sets are the highest-volume HIPAA
transaction families in production, used by Adonis, Candid, Nym, Fathom,
Cair, Sohar and the broader RCM-automation cohort.

This adapter parses incoming 834/835/837 payloads in their X12 v5010
ANSI X12 wire form and produces structural metadata + per-record
receipt-emission helper suitable for AuditProof binding. The adapter
does NOT interpret medical-necessity criteria, validate against a
specific payer companion guide, adjudicate claims, or determine
payment/denial reasons substantively.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This adapter structurally
    parses customer-supplied X12 transaction bytes and emits structural
    metadata fields. Claim adjudication, payment determination, denial
    rationale interpretation, payer companion-guide conformance, and
    downstream RCM workflows remain the customer's responsibility.

Customer-sole-declarer (ADR-013):
    The customer supplies the X12 wire payload. This adapter parses
    structural fields only.

Forever-Standard discipline (ADR-006 I0):
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field (ADR-010 A4). Per-record
    receipt emission uses ``pattern_tag`` closed-enum variants
    (``rcm_claim`` for 837, ``rcm_remit`` for 835).

Pure-Python implementation; no native dependencies.

PHI/PII discipline:
    All test fixtures use SYNTHETIC FAKE DATA. Member IDs, NPIs, payer
    IDs, claim numbers, and dollar amounts are obviously fictitious.
    Never real PHI even in fixtures.

The 837 transaction is structurally the most complex healthcare X12
transaction (10+ loops; 500+ segment positions). This adapter implements
progressive depth: ST/SE envelope + claim-level loops + service-level
loops. Optional segments (NTE notes, K3 file information, CR1 ambulance,
etc.) are observed but not parsed in V1.

Companion posture document: ``docs/integrations/healthcare-x12-full-rcm.md``.
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

# Framework version identifiers per X12 005010 family.
_FRAMEWORK_VERSIONS: Dict[str, str] = {
    "834": "x12-005010X220A1",
    "835": "x12-005010X221A1",
    "837P": "x12-005010X222A1",
    "837I": "x12-005010X223A2",
    "837D": "x12-005010X224A2",
}

_VALID_TRANSACTION_SETS: FrozenSet[str] = frozenset(
    {"834", "835", "837"}
)

# 837 flavor detection via GS08 implementation-guide reference.
_837_FLAVOR_TO_GS08: Dict[str, str] = {
    "837P": "005010X222A1",
    "837I": "005010X223A2",
    "837D": "005010X224A2",
}


# 834 maintenance type codes (INS03 segment).
_834_MAINTENANCE_CODES: Dict[str, str] = {
    "001": "Change",
    "021": "Addition",
    "024": "Cancel/Termination",
    "025": "Reinstatement",
    "030": "Audit/Compare",
}

# 835 claim status codes (CLP02).
_835_CLAIM_STATUS_CODES: Dict[str, str] = {
    "1": "Processed as Primary",
    "2": "Processed as Secondary",
    "3": "Processed as Tertiary",
    "4": "Denied",
    "5": "Pended",
    "19": "Processed as Primary, Forwarded to Additional Payer",
    "20": "Processed as Secondary, Forwarded to Additional Payer",
    "21": "Processed as Tertiary, Forwarded to Additional Payer",
    "22": "Reversal of Previous Payment",
    "23": "Not Our Claim, Forwarded",
    "25": "Predetermination Pricing Only",
}


@dataclass(frozen=True)
class X12RCMTransaction:
    """Structural parse result for an X12 834/835/837 transaction.

    Produced by :meth:`X12FullRCMAdapter.parse_transaction`. Captures
    transaction-routing identifiers + transaction-type-specific
    structural fields suitable for AuditProof binding.

    Three-role discipline (ADR-017): Nanorix structurally parses;
    customer adjudicates claims and interprets payment / denial
    rationale.
    """

    transaction_set: str
    """The X12 transaction set identifier (``"834"``, ``"835"``, or
    ``"837"``)."""

    flavor: str
    """Sub-type of the transaction. For 837: ``"P"`` / ``"I"`` /
    ``"D"``. For 834 / 835: ``""`` (single flavor)."""

    framework_version: str
    """The X12 implementation-guide version identifier
    (``"x12-005010X220A1"`` for 834, etc.)."""

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
    """The payer identifier extracted from the appropriate NM1 loop.
    For 837: 2010BB NM1 PR. For 835: 1000A N1 PR. For 834: 1000A N1."""

    payer_name: str
    """The payer name."""

    provider_npi: str
    """The billing/rendering provider NPI. For 837: 2010AA NM1 85 (BL).
    For 835: 1000B N1 PE."""

    member_id_hash: str
    """SHA-256 hex hash of the patient/member identifier. Raw IDs are
    NEVER emitted into metadata."""

    claim_count: int = 0
    """Number of claims / remits / enrollment records observed in the
    payload. 837 = count of CLM segments. 835 = count of CLP segments.
    834 = count of INS segments."""

    total_charge_amount_cents: int = 0
    """Sum of CLM02 (charge amounts) on 837 in cents — to avoid
    floating-point. Empty (0) on 835 / 834. Provider-supplied
    structural metric only; the adapter does NOT verify or reprice."""

    total_payment_amount_cents: int = 0
    """Sum of CLP04 (payment amounts) on 835 in cents. Empty (0) on
    837 / 834."""

    claim_status_distribution: str = ""
    """Comma-joined list of distinct claim-status codes observed (835
    only; from CLP02). Empty string on 837 / 834."""

    maintenance_type_distribution: str = ""
    """Comma-joined list of distinct maintenance-type codes observed
    (834 only; from INS03). Empty string on 837 / 835."""

    transaction_datetime: str = ""
    """Concatenated ISA09 + ISA10 (YYMMDDHHMM)."""

    payload_hash: str = ""
    """SHA-256 hex hash of the full wire payload."""

    segment_types_observed: str = ""
    """Comma-joined list of segment types."""

    @property
    def is_claim(self) -> bool:
        """Whether the transaction is an 837 claim."""
        return self.transaction_set == "837"

    @property
    def is_remittance(self) -> bool:
        """Whether the transaction is an 835 remittance/payment advice."""
        return self.transaction_set == "835"

    @property
    def is_enrollment(self) -> bool:
        """Whether the transaction is an 834 enrollment/maintenance."""
        return self.transaction_set == "834"

    @property
    def has_denied_claims(self) -> bool:
        """Whether any 835 claim status indicates denial (CLP02=4).

        Convenience derivation; customer is responsible for substantive
        denial interpretation at role 3.
        """
        if not self.claim_status_distribution:
            return False
        return "4" in self.claim_status_distribution.split(",")

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by reserved framework keys plus structural
        fields scoped to the transaction type. Stays under the
        16-key MAX_METADATA_KEYS limit by emitting only transaction-
        type-relevant fields.
        """
        framework_label = "X12-834"
        if self.transaction_set == "835":
            framework_label = "X12-835"
        elif self.transaction_set == "837":
            framework_label = f"X12-837{self.flavor}"

        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: framework_label,
            RESERVED_FRAMEWORK_VERSION_KEY: self.framework_version,
            "transaction_set": self.transaction_set,
            "isa_control_number": self.isa_control_number,
            "st_control_number": self.st_control_number,
            "payload_hash": self.payload_hash,
            "claim_count": str(self.claim_count),
        }
        if self.flavor:
            meta["flavor"] = self.flavor
        if self.payer_id:
            meta["payer_id"] = self.payer_id
        if self.payer_name:
            meta["payer_name"] = self.payer_name
        if self.provider_npi:
            meta["provider_npi"] = self.provider_npi
        if self.member_id_hash:
            meta["member_id_hash"] = self.member_id_hash
        if self.total_charge_amount_cents:
            meta["total_charge_amount_cents"] = str(
                self.total_charge_amount_cents
            )
        if self.total_payment_amount_cents:
            meta["total_payment_amount_cents"] = str(
                self.total_payment_amount_cents
            )
        if self.claim_status_distribution:
            meta["claim_status_distribution"] = (
                self.claim_status_distribution
            )
        if self.maintenance_type_distribution:
            meta["maintenance_type_distribution"] = (
                self.maintenance_type_distribution
            )
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


class X12FullRCMAdapter:
    """X12 EDI 834/835/837 full revenue-cycle structural adapter.

    Parses incoming X12 EDI 834 (Benefit Enrollment), 835 (Health Care
    Claim Payment/Advice), or 837 (Health Care Claim — Professional /
    Institutional / Dental) v5010 wire payloads and produces a
    :class:`X12RCMTransaction` with structural identifiers + claim-
    count + amount-summary fields.

    The adapter auto-detects transaction set (ST01) and 837 flavor (via
    GS08 implementation-guide reference). It walks segments to extract:

    - Transaction routing (ISA, GS, ST control numbers)
    - Payer info (NM1 PR for 837; N1 PR for 835; N1 for 834)
    - Provider info (NM1 85/BL for 837 billing provider)
    - Member ID (hashed for PHI discipline)
    - Per-transaction-type detail:
      * 837: claim count (CLM segment count), total charges (CLM02 sum)
      * 835: remit count (CLP segment count), total payments (CLP04 sum),
        claim status distribution (CLP02 codes)
      * 834: enrollment record count (INS), maintenance type distribution

    Three-role discipline (ADR-017): Adapter is structural; claim
    adjudication, payment determination, and denial-rationale
    interpretation remain the customer's responsibility.

    Pure-Python implementation. No third-party dependencies.

    Example::

        adapter = X12FullRCMAdapter()
        wire_bytes = open("incoming_837P.edi", "rb").read()
        parsed = adapter.parse_transaction(wire_bytes)
        with client.capsules.create(capsulefile="cf_rcm") as capsule:
            for tx in batch:
                pattern_tag = X12FullRCMAdapter.receipt_pattern_tag(
                    tx.transaction_set
                )
                with capsule.record(
                    record_id=tx.isa_control_number,
                    pattern_tag=pattern_tag,
                ) as r:
                    r.bind_input(tx.payload_hash)
                    result = process_rcm_transaction(tx)
                    r.bind_output(result)
    """

    def __init__(self, strict: bool = False) -> None:
        """Initialize the adapter.

        :param strict: If True, raise ValueError on structural anomalies.
        """
        self._strict = bool(strict)

    @property
    def strict_mode(self) -> bool:
        return self._strict

    @property
    def supported_transaction_sets(self) -> List[str]:
        return sorted(_VALID_TRANSACTION_SETS)

    @property
    def supported_837_flavors(self) -> List[str]:
        return ["D", "I", "P"]

    @property
    def claim_status_codes(self) -> Dict[str, str]:
        """Mapping of 835 CLP02 claim-status codes to descriptions."""
        return dict(_835_CLAIM_STATUS_CODES)

    @property
    def maintenance_codes(self) -> Dict[str, str]:
        """Mapping of 834 INS03 maintenance-type codes to descriptions."""
        return dict(_834_MAINTENANCE_CODES)

    def parse_transaction(self, raw: bytes) -> X12RCMTransaction:
        """Parse a single X12 834, 835, or 837 transaction.

        :param raw: The wire payload bytes.
        :returns: :class:`X12RCMTransaction` with structural fields.
        :raises ValueError: On malformed or unsupported payload.

        Structural-only parse. Service-level detail (SVC segments on 835;
        SV1/SV2/SV3 service lines on 837), adjustment reason codes (CAS),
        and reference-segment metadata are NOT extracted in V1 — they
        live inside the capsule workspace for customer application code.
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
    ) -> X12RCMTransaction:
        """Walk segments and extract structural fields by transaction type."""
        isa_fields = segments[0].split(element_sep)

        def _isa(idx: int) -> str:
            return isa_fields[idx].strip() if idx < len(isa_fields) else ""

        submitter_id = _isa(6)
        receiver_id = _isa(8)
        isa_date = _isa(9)
        isa_time = _isa(10)
        isa_control_number = _isa(13)

        # GS segment carries the implementation-guide reference (GS08).
        gs_control_number = ""
        gs08 = ""
        for seg in segments[1:]:
            if seg.startswith("GS"):
                gs_fields = seg.split(element_sep)
                if len(gs_fields) >= 7:
                    gs_control_number = gs_fields[6].strip()
                if len(gs_fields) >= 9:
                    gs08 = gs_fields[8].strip()
                break

        # ST segment carries transaction set + control number.
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
                "; expected 834, 835, or 837"
            )

        # Determine 837 flavor via GS08 implementation-guide reference.
        flavor = ""
        framework_version = _FRAMEWORK_VERSIONS.get(
            transaction_set, ""
        )
        if transaction_set == "837":
            for flav, ig in _837_FLAVOR_TO_GS08.items():
                if ig in gs08:
                    flavor = flav.replace("837", "")  # "P", "I", "D"
                    framework_version = _FRAMEWORK_VERSIONS[flav]
                    break
            if not flavor and self._strict:
                raise ValueError(
                    "parse_transaction [strict]: 837 flavor could not be "
                    f"determined from GS08={gs08!r}; expected one of "
                    f"{sorted(_837_FLAVOR_TO_GS08.values())}"
                )
            if not flavor:
                # Conservative default: Professional is the most common.
                flavor = "P"
                framework_version = _FRAMEWORK_VERSIONS["837P"]

        # Walk segments for transaction-type-specific extraction.
        payer_id = ""
        payer_name = ""
        provider_npi = ""
        member_id_hash = ""
        claim_count = 0
        total_charge_cents = 0
        total_payment_cents = 0
        claim_status_codes_seen: List[str] = []
        maintenance_codes_seen: List[str] = []
        segment_types_seen: List[str] = []

        for seg in segments:
            seg_id = seg.split(element_sep, 1)[0]
            if seg_id and seg_id not in segment_types_seen:
                segment_types_seen.append(seg_id)

            if seg.startswith("NM1"):
                nm1_fields = seg.split(element_sep)
                if len(nm1_fields) >= 2:
                    entity_id = nm1_fields[1].strip()
                    # PR = Payer (837 + 835)
                    if entity_id == "PR":
                        if len(nm1_fields) >= 4:
                            payer_name = nm1_fields[3].strip()
                        if len(nm1_fields) >= 10:
                            payer_id = nm1_fields[9].strip()
                    # 85 = Billing Provider (837)
                    elif entity_id == "85":
                        if len(nm1_fields) >= 10:
                            provider_npi = nm1_fields[9].strip()
                    # BL = Billing (legacy code, 837)
                    elif entity_id == "BL" and not provider_npi:
                        if len(nm1_fields) >= 10:
                            provider_npi = nm1_fields[9].strip()
                    # IL = Insured/Subscriber/Member (834, 835, 837)
                    elif entity_id == "IL" and not member_id_hash:
                        if len(nm1_fields) >= 10:
                            member_id_raw = nm1_fields[9].strip()
                            if member_id_raw:
                                member_id_hash = hashlib.sha256(
                                    member_id_raw.encode("utf-8")
                                ).hexdigest()
                    # QC = Patient (837 — first-encounter patient)
                    elif entity_id == "QC" and not member_id_hash:
                        if len(nm1_fields) >= 10:
                            patient_raw = nm1_fields[9].strip()
                            if patient_raw:
                                member_id_hash = hashlib.sha256(
                                    patient_raw.encode("utf-8")
                                ).hexdigest()
            elif seg.startswith("N1"):
                # 834 + 835 use N1 (not NM1) for org-level entities.
                n1_fields = seg.split(element_sep)
                if len(n1_fields) >= 2:
                    entity_id = n1_fields[1].strip()
                    # PR = Payer
                    if entity_id == "PR" and not payer_id:
                        if len(n1_fields) >= 3:
                            payer_name = n1_fields[2].strip()
                        if len(n1_fields) >= 5:
                            payer_id = n1_fields[4].strip()
                    # PE = Payee (835 provider)
                    elif entity_id == "PE" and not provider_npi:
                        if len(n1_fields) >= 5:
                            provider_npi = n1_fields[4].strip()
            elif seg.startswith("CLM") and transaction_set == "837":
                # CLM = Claim header (one per claim, 837)
                claim_count += 1
                clm_fields = seg.split(element_sep)
                if len(clm_fields) >= 3:
                    # CLM02 = total charge amount (decimal). Convert to cents.
                    try:
                        amount = clm_fields[2].strip()
                        if amount:
                            # Round to cents safely. Use string conversion
                            # to avoid Python-float drift.
                            dollars, _, cents = amount.partition(".")
                            cents = (cents + "00")[:2]
                            total_charge_cents += int(dollars) * 100 + int(cents)
                    except (ValueError, IndexError):
                        pass
            elif seg.startswith("CLP") and transaction_set == "835":
                # CLP = Claim Payment (one per claim, 835)
                claim_count += 1
                clp_fields = seg.split(element_sep)
                if len(clp_fields) >= 3:
                    status_code = clp_fields[2].strip()
                    if status_code and status_code not in claim_status_codes_seen:
                        claim_status_codes_seen.append(status_code)
                if len(clp_fields) >= 5:
                    try:
                        amount = clp_fields[4].strip()
                        if amount:
                            dollars, _, cents = amount.partition(".")
                            cents = (cents + "00")[:2]
                            total_payment_cents += int(dollars) * 100 + int(cents)
                    except (ValueError, IndexError):
                        pass
            elif seg.startswith("INS") and transaction_set == "834":
                # INS = Insurance/Maintenance (one per enrollment, 834)
                claim_count += 1
                ins_fields = seg.split(element_sep)
                if len(ins_fields) >= 4:
                    maint_code = ins_fields[3].strip()
                    if maint_code and maint_code not in maintenance_codes_seen:
                        maintenance_codes_seen.append(maint_code)

        transaction_datetime = ""
        if isa_date and isa_time:
            transaction_datetime = (isa_date + isa_time).strip()

        payload_hash = hashlib.sha256(raw).hexdigest()

        validate_value_length("isa_control_number", isa_control_number)
        validate_value_length("gs_control_number", gs_control_number)
        validate_value_length("st_control_number", st_control_number)
        validate_value_length("payer_id", payer_id)
        validate_value_length("payer_name", payer_name)
        validate_value_length("provider_npi", provider_npi)

        claim_status_csv = ",".join(claim_status_codes_seen)
        maintenance_csv = ",".join(maintenance_codes_seen)
        segment_types_csv = ",".join(segment_types_seen)

        return X12RCMTransaction(
            transaction_set=transaction_set,
            flavor=flavor,
            framework_version=framework_version,
            isa_control_number=isa_control_number,
            gs_control_number=gs_control_number,
            st_control_number=st_control_number,
            submitter_id=submitter_id,
            receiver_id=receiver_id,
            payer_id=payer_id,
            payer_name=payer_name,
            provider_npi=provider_npi,
            member_id_hash=member_id_hash,
            claim_count=claim_count,
            total_charge_amount_cents=total_charge_cents,
            total_payment_amount_cents=total_payment_cents,
            claim_status_distribution=claim_status_csv,
            maintenance_type_distribution=maintenance_csv,
            transaction_datetime=transaction_datetime,
            payload_hash=payload_hash,
            segment_types_observed=segment_types_csv,
        )

    def parse_834(self, raw: bytes) -> X12RCMTransaction:
        """Parse an 834 enrollment transaction specifically."""
        parsed = self.parse_transaction(raw)
        if not parsed.is_enrollment:
            raise ValueError(
                f"parse_834: transaction_set={parsed.transaction_set!r}, "
                "expected 834"
            )
        return parsed

    def parse_835(self, raw: bytes) -> X12RCMTransaction:
        """Parse an 835 remittance transaction specifically."""
        parsed = self.parse_transaction(raw)
        if not parsed.is_remittance:
            raise ValueError(
                f"parse_835: transaction_set={parsed.transaction_set!r}, "
                "expected 835"
            )
        return parsed

    def parse_837(self, raw: bytes) -> X12RCMTransaction:
        """Parse an 837 claim transaction specifically."""
        parsed = self.parse_transaction(raw)
        if not parsed.is_claim:
            raise ValueError(
                f"parse_837: transaction_set={parsed.transaction_set!r}, "
                "expected 837"
            )
        return parsed

    @staticmethod
    def receipt_pattern_tag(transaction_set: str) -> str:
        """Return the canonical per-record receipt pattern_tag for an
        RCM transaction set.

        Per ADR-039 §"pattern_tag rule" closed enum:
        - ``"837"`` → ``"rcm_claim"``
        - ``"835"`` → ``"rcm_remit"``
        - ``"834"`` → ``"rcm_claim"`` (enrollment treated as claim-class
          for receipt purposes — 834 is membership maintenance which
          shares the rcm_claim closed-enum slot)

        :raises ValueError: If the transaction set is not 834/835/837.
        """
        if transaction_set == "837":
            return "rcm_claim"
        if transaction_set == "835":
            return "rcm_remit"
        if transaction_set == "834":
            # 834 enrollment uses rcm_claim slot for the maintenance
            # record class (closed-enum reuse — no new pattern_tag).
            return "rcm_claim"
        raise ValueError(
            f"receipt_pattern_tag: unsupported transaction_set="
            f"{transaction_set!r}; expected 834/835/837"
        )

    @staticmethod
    def derive_metadata(
        parsed: X12RCMTransaction,
        *,
        customer_workflow_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Derive AuditProof metadata from a parsed RCM transaction."""
        meta = parsed.to_metadata()
        if customer_workflow_id is not None:
            validate_value_length(
                "customer_workflow_id", customer_workflow_id
            )
            if len(meta) >= MAX_METADATA_KEYS:
                # Prune optional field to make room.
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
    "X12FullRCMAdapter",
    "X12RCMTransaction",
]
