"""High-volume per-record claims processing reference workload.

Pipeline shape: one X12 EDI claim (837P/837I) → parse + validate →
clearinghouse round-trip (eligibility verification, prior-auth
check, real-time adjudication) → decision (paid / denied /
pended) → optional FHIR-shaped supplementary output → /data/output.

Industry signal: this is the production pattern for high-volume
claims-processing workflows (HealthEdge HealthRules, Olive's
revenue-cycle automation, Edifecs's clearinghouse adapters,
payer-internal real-time-adjudication engines). Each claim is one
capsule; the per-AuditProof economics + sealed isolation discipline
matter at this volume.

Per-claim AuditProof is the load-bearing artifact:
  - the claim envelope's bytes hash (load-bearing for state-DOI
    market-conduct examination)
  - the clearinghouse round-trip's request/response hashes
    (load-bearing for payment-dispute resolution)
  - the adjudication decision (paid amount, denied reason,
    pended-with-reason) signed under customer's authority when
    BYO-HSM is enabled — load-bearing for ERISA / state-DOI /
    member-appeal scenarios

Production-shape primitives (stubbed where remote service is
required):

  1. X12 EDI parsing. Production substitutes pyx12 / bots-edi /
     Edifecs SDK / vendor parser from the customer's clearinghouse.
     The reference shape walks the segment hierarchy minimally
     (ISA / GS / ST / BHT / NM1 / CLM / SBR / etc) and extracts
     the load-bearing fields.
  2. Clearinghouse round-trip. HTTPS POST to the customer's
     licensed clearinghouse endpoint with the X12 envelope.
     The clearinghouse returns either an X12 277 (claim status
     response) or a vendor-specific JSON envelope.
  3. Adjudication decision logic. Reference shape demonstrates
     the decision-tree primitive: eligibility → coverage check →
     pre-auth requirement check → benefit-summary application →
     paid / denied / pended.
  4. FHIR supplementary output (optional, when downstream
     consumer is FHIR-native). Maps the X12 837 → FHIR Claim
     resource per the FHIR Claim profile.

Each claim is processed in a fresh capsule. The per-claim PHI
(member identifiers, diagnosis/procedure codes) lives in volatile
capsule memory only — destroyed with the capsule. The
clearinghouse HTTPS call's request/response bytes are hashed (not
retained) in the AuditProof.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
CLEARINGHOUSE_URL = os.environ.get("CLEARINGHOUSE_URL", "")
X12_TYPE = os.environ.get("X12_TRANSACTION_TYPE", "837P")
TIMEOUT = int(os.environ.get("CLAIM_TIMEOUT_SECONDS", "60"))
ENABLE_AUTO = os.environ.get("ENABLE_AUTO_ADJUDICATION", "true").lower() == "true"

ClaimDecision = Literal["paid", "denied", "pended", "rejected"]


# -- X12 parse output schema ---------------------------------------------


class X12ClaimEnvelope(BaseModel):
    """Minimal X12 837 envelope shape — load-bearing fields only.

    Production deployments populate the full envelope from pyx12 /
    bots-edi parse trees. The reference shape captures the fields
    the adjudication decision depends on.
    """

    interchange_control_number: str  # ISA13
    transaction_set_control_number: str  # ST02
    submitter_npi: str  # NM1*41 — billing provider
    payer_id: str  # NM1*PR — payer
    member_id: str  # NM1*IL — subscriber/insured
    claim_id: str  # CLM01 — claim submitter ID
    total_charge_amount: float  # CLM02
    diagnosis_codes: list[str] = Field(default_factory=list)  # HI segments (ICD-10)
    service_lines: list[dict[str, Any]] = Field(default_factory=list)  # SV1/SV2 segments
    place_of_service: str = ""  # CLM05-1
    raw_envelope_hash: str  # SHA-512 of the X12 byte stream


# -- Adjudication decision schema ---------------------------------------


class AdjudicationDecision(BaseModel):
    """Per-claim decision output."""

    claim_id: str
    decision: ClaimDecision
    decision_reason_code: str  # CARC/RARC code or internal reason
    decision_reason_text: str
    paid_amount: float = 0.0
    member_responsibility: float = 0.0
    pended_for_review: bool = False
    pended_reason: str | None = None
    clearinghouse_response_hash: str | None = None
    decision_timestamp: str  # ISO 8601
    auditproof_per_record_economics_note: str = (
        "One AuditProof per claim. High-volume per-record workloads "
        "rely on Team-tier (or above) pricing with metered AuditProof "
        "billing — see https://nanorix.io for current tier-aware pricing."
    )


# -- X12 parse stub ------------------------------------------------------


def parse_x12_envelope(envelope_bytes: bytes) -> X12ClaimEnvelope:
    """Minimal X12 837 segment-walk parser stub.

    Production deployments substitute pyx12.x12n_document() or the
    customer's clearinghouse vendor parser. The reference shape
    walks segment delimiters minimally to extract load-bearing
    fields. Real X12 envelopes use:
      - segment delimiter:    ~  (tilde)
      - element delimiter:    *  (asterisk)
      - subelement delimiter: :  (colon, typical)

    Robust production parsers handle all delimiter variations,
    HIPAA-X12 5010 errata, and edge cases (mid-segment overrides,
    vendor extensions, etc).
    """
    raw_hash = hashlib.sha512(envelope_bytes).hexdigest()
    text = envelope_bytes.decode("utf-8", errors="replace")
    segments = [s.strip() for s in text.split("~") if s.strip()]

    # Helper: find first segment by ID + return elements.
    def find_segment(segment_id: str) -> list[str] | None:
        for seg in segments:
            elements = seg.split("*")
            if elements and elements[0] == segment_id:
                return elements
        return None

    isa = find_segment("ISA")
    st = find_segment("ST")
    clm = find_segment("CLM")

    # NM1 segments: each has *role-qualifier; we extract by role.
    def find_nm1_by_role(role: str) -> list[str] | None:
        for seg in segments:
            elements = seg.split("*")
            if elements and elements[0] == "NM1" and len(elements) > 1:
                if elements[1] == role:
                    return elements
        return None

    nm1_billing = find_nm1_by_role("85")  # billing provider (or "41" for submitter)
    nm1_payer = find_nm1_by_role("PR")
    nm1_subscriber = find_nm1_by_role("IL")

    diagnosis_codes: list[str] = []
    for seg in segments:
        elements = seg.split("*")
        if elements and elements[0] == "HI":
            for elem in elements[1:]:
                if ":" in elem:
                    parts = elem.split(":")
                    if len(parts) >= 2 and parts[0].startswith("ABK"):
                        diagnosis_codes.append(parts[1])

    service_lines: list[dict[str, Any]] = []
    for seg in segments:
        elements = seg.split("*")
        if elements and elements[0] == "SV1" and len(elements) >= 4:
            service_lines.append(
                {
                    "procedure_code_qualifier_and_code": elements[1],
                    "charge_amount": elements[2],
                    "unit_or_basis_for_measurement": elements[3] if len(elements) > 3 else "",
                }
            )

    return X12ClaimEnvelope(
        interchange_control_number=(isa[13] if isa and len(isa) > 13 else "STUB"),
        transaction_set_control_number=(st[2] if st and len(st) > 2 else "STUB"),
        submitter_npi=(nm1_billing[9] if nm1_billing and len(nm1_billing) > 9 else "STUB-NPI"),
        payer_id=(nm1_payer[9] if nm1_payer and len(nm1_payer) > 9 else "STUB-PAYER"),
        member_id=(nm1_subscriber[9] if nm1_subscriber and len(nm1_subscriber) > 9 else "STUB-MEMBER"),
        claim_id=(clm[1] if clm and len(clm) > 1 else "STUB-CLAIM"),
        total_charge_amount=(
            float(clm[2]) if clm and len(clm) > 2 and clm[2].replace(".", "").isdigit() else 0.0
        ),
        diagnosis_codes=diagnosis_codes,
        service_lines=service_lines,
        place_of_service=(clm[5] if clm and len(clm) > 5 else ""),
        raw_envelope_hash=raw_hash,
    )


# -- Clearinghouse round-trip stub --------------------------------------


def submit_to_clearinghouse(
    envelope: X12ClaimEnvelope, raw_envelope_bytes: bytes
) -> tuple[dict[str, Any], str | None]:
    """Forward the X12 envelope to the customer's clearinghouse.

    Production: HTTPS POST to CLEARINGHOUSE_URL with the X12 byte
    stream (or vendor-specific JSON envelope per the clearinghouse's
    API spec). Each call is one SealedHttpRequest in AuditProof.

    Returns (response_payload, response_hash).
    """
    if not CLEARINGHOUSE_URL:
        # Stub mode — synthesize a response that exercises the
        # adjudication path.
        print(
            f"  [clearinghouse] STUB call for claim={envelope.claim_id}",
            file=sys.stderr,
        )
        synthetic = {
            "stub": True,
            "claim_id": envelope.claim_id,
            "eligibility": {"status": "eligible", "plan_id": "stub-plan"},
            "preauth": {"required": False, "auth_id": None},
            "adjudication": {
                "decision": "paid",
                "paid_amount": envelope.total_charge_amount * 0.80,
                "member_responsibility": envelope.total_charge_amount * 0.20,
                "carc_code": "1",  # processed as primary
                "rarc_code": None,
            },
        }
        canonical = json.dumps(synthetic, sort_keys=True, separators=(",", ":"))
        return synthetic, hashlib.sha512(canonical.encode()).hexdigest()

    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(
                CLEARINGHOUSE_URL,
                content=raw_envelope_bytes,
                headers={"Content-Type": "application/EDI-X12"},
            )
            response.raise_for_status()
            payload = response.json()
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return payload, hashlib.sha512(canonical.encode()).hexdigest()
    except httpx.HTTPError as e:
        print(f"  [clearinghouse] error: {e}", file=sys.stderr)
        return ({"error": str(e)}, None)


# -- Adjudication decision logic ----------------------------------------


def adjudicate_claim(
    envelope: X12ClaimEnvelope, clearinghouse_response: dict[str, Any]
) -> AdjudicationDecision:
    """Apply decision logic to clearinghouse response.

    Production decision engines layer:
      1. Eligibility verification (clearinghouse 271)
      2. Coverage / benefit-summary application
      3. Pre-auth requirement check (per CMS-0057-F)
      4. Medical-necessity criteria (InterQual / MCG / customer-curated)
      5. Pricing application (fee schedule / DRG / capitation)
      6. CARC/RARC code assignment

    Reference shape demonstrates the decision-tree primitive.
    Customer extends per their adjudication ruleset.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    err = clearinghouse_response.get("error")
    if err:
        return AdjudicationDecision(
            claim_id=envelope.claim_id,
            decision="rejected",
            decision_reason_code="CO-16",
            decision_reason_text=f"Clearinghouse error: {err}",
            decision_timestamp=timestamp,
        )

    eligibility = clearinghouse_response.get("eligibility", {})
    if eligibility.get("status") != "eligible":
        return AdjudicationDecision(
            claim_id=envelope.claim_id,
            decision="denied",
            decision_reason_code="CO-31",
            decision_reason_text="Patient cannot be identified as our insured",
            decision_timestamp=timestamp,
        )

    preauth = clearinghouse_response.get("preauth", {})
    if preauth.get("required") and not preauth.get("auth_id"):
        return AdjudicationDecision(
            claim_id=envelope.claim_id,
            decision="pended",
            decision_reason_code="CO-197",
            decision_reason_text="Precert/auth absent; pended for review",
            pended_for_review=True,
            pended_reason="missing_preauth",
            decision_timestamp=timestamp,
        )

    if not ENABLE_AUTO:
        return AdjudicationDecision(
            claim_id=envelope.claim_id,
            decision="pended",
            decision_reason_code="INTERNAL-001",
            decision_reason_text="Auto-adjudication disabled; manual review",
            pended_for_review=True,
            pended_reason="manual_review_required",
            decision_timestamp=timestamp,
        )

    adj = clearinghouse_response.get("adjudication", {})
    decision_str = adj.get("decision", "pended")
    if decision_str not in ("paid", "denied", "pended"):
        decision_str = "pended"
    return AdjudicationDecision(
        claim_id=envelope.claim_id,
        decision=decision_str,
        decision_reason_code=adj.get("carc_code", ""),
        decision_reason_text=adj.get("rarc_text", ""),
        paid_amount=float(adj.get("paid_amount", 0.0)),
        member_responsibility=float(adj.get("member_responsibility", 0.0)),
        decision_timestamp=timestamp,
    )


# -- Pipeline orchestration ----------------------------------------------


def process_claim(envelope_path: Path) -> AdjudicationDecision:
    raw_bytes = envelope_path.read_bytes()
    envelope = parse_x12_envelope(raw_bytes)
    print(
        f"  parsed claim={envelope.claim_id} "
        f"member={envelope.member_id} "
        f"charge={envelope.total_charge_amount} "
        f"dx_codes={len(envelope.diagnosis_codes)} "
        f"lines={len(envelope.service_lines)}",
        file=sys.stderr,
    )
    response_payload, response_hash = submit_to_clearinghouse(envelope, raw_bytes)
    decision = adjudicate_claim(envelope, response_payload)
    decision.clearinghouse_response_hash = response_hash
    return decision


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # X12 envelopes are typically .edi or .x12 files. Some
    # clearinghouses ship .txt with X12 contents.
    claim_files = (
        sorted(INPUT_DIR.glob("*.edi"))
        + sorted(INPUT_DIR.glob("*.x12"))
        + sorted(INPUT_DIR.glob("*.txt"))
    )
    if not claim_files:
        print("No X12 claim envelopes found in /data/input/uploads")
        return

    decisions: list[dict[str, Any]] = []
    for f in claim_files:
        print(f"Processing {f.name}")
        decision = process_claim(f)
        print(
            f"  → claim={decision.claim_id} decision={decision.decision} "
            f"paid={decision.paid_amount} reason={decision.decision_reason_code}"
        )
        decisions.append(json.loads(decision.model_dump_json()))

    output_path = OUTPUT_DIR / "adjudication.json"
    output_path.write_text(
        json.dumps(
            {
                "schema_version": "high-volume-per-record-v1",
                "x12_transaction_type": X12_TYPE,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "decisions": decisions,
            },
            indent=2,
        )
    )
    print(f"Adjudicated {len(decisions)} claims → {output_path}")


if __name__ == "__main__":
    main()
