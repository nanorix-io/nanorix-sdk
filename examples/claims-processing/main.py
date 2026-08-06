"""Insurance prior-auth review reference workload — production pattern.

Pipeline shape (CMS-0057-F era, effective Jan 2026):

  X12 278 EDI request from clearinghouse (Availity / Change Healthcare /
  Waystar) → X12 parse (member + procedure + diagnoses + units +
  attached clinical documentation references in X12 275) → InterQual /
  MCG medical-necessity criteria binding → LLM clinical-policy
  reasoning over the criteria evaluation → FHIR R4 Prior Authorization
  response (Claim + ClaimResponse + Coverage resources per the PAS
  Implementation Guide) → appeal-loop case-state metadata for
  customer's case-management system → /data/output.

This bundle demonstrates the pipeline *shape* + integration contracts.
Stubs sit at the four customer-substituted boundaries:

  1. X12 278/275 parser. Production deployments substitute pyx12,
     bots-edi, Edifecs, or vendor SDK from their clearinghouse.
  2. InterQual (Change Healthcare) or MCG (Hearst Health) criteria
     engine. Production deployments compose with their licensed
     criteria-engine — the engines are not redistributable.
  3. CMS-0057-F FHIR R4 Prior Authorization API conformance. Output
     mapping demonstrates the resource shape; production deployments
     wire to their FHIR PAS server (HL7 PAS IG conformance).
  4. Appeal-loop case-state. Customer's case-management system
     (denial → reconsideration → peer-to-peer → external-review state
     machine per ERISA / state-DOI rules) consumes the case_state
     vocabulary the customer declares in capsule metadata.

CMS-0057-F (Interoperability and Prior Authorization Final Rule, RIN
0938-AU87) compliance fulcrum
-----------------------------------------------------------------
For impacted payers (Medicare Advantage, Medicaid, CHIP, QHPs on FFEs)
the rule mandates FHIR-based PA APIs by Jan 2026/2027:
  - PAS  — Prior Authorization Support (this bundle's output shape)
  - CRD  — Coverage Requirements Discovery
  - DTR  — Documentation Templates and Rules
The customer determines applicability + conformance verification with
regulatory counsel; this bundle demonstrates the resource shape only.

Per-event customer attestation
------------------------------
Every X12 parse, criteria-engine call, LLM call, and FHIR response
emission produces one ActivityEvent. When `signing_config` is declared
on the Capsulefile, each event is fire-and-buffered to the customer's
KMS-backed signing endpoint (mTLS), the signature is absorbed at
capsule destroy, and bound into the AuditProof's per_event_attestations
reserved slot. Two-party attestation: Nanorix attests to capsule
boundary; customer's KMS attests to event-level evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
CRITERIA_ENGINE = os.environ.get("CRITERIA_ENGINE", "interqual")  # "interqual" | "mcg"
CRITERIA_ENGINE_BASE = os.environ.get("CRITERIA_ENGINE_BASE", "")  # customer-provided endpoint
PAYER_FHIR_PAS_BASE = os.environ.get("PAYER_FHIR_PAS_BASE", "")
CASE_STATE_VOCABULARY = os.environ.get(
    "CASE_STATE_VOCABULARY", "intake|criteria_review|peer_to_peer|external_review|final"
)


# -- X12 278 / 275 EDI request shape -------------------------------------
#
# X12 278 (Health Care Services Review — Request for Review and
# Response) is the production EDI transaction set for prior-auth
# requests, defined in ASC X12N v005010X217. X12 275 (Patient
# Information) carries attached clinical documentation. Real
# clearinghouses (Availity, Change Healthcare, Waystar) deliver
# this as raw X12 segments separated by `~` with element separator
# `*` and component separator `:`. Production parsers substitute
# pyx12 / bots-edi / Edifecs / vendor SDK.


CaseState = Literal[
    "intake",
    "criteria_review",
    "rendered",
    "denied",
    "appeal_filed",
    "peer_to_peer_pending",
    "peer_to_peer_complete",
    "external_review_filed",
    "external_review_complete",
    "final",
]


class X12ServiceLine(BaseModel):
    """One service line from the X12 278 SV1/SV2/SV3 segment.

    HCPCS / CPT procedure code plus requested units. Multiple service
    lines per transaction are common (e.g. surgical procedure +
    pre-op imaging + facility services).
    """

    procedure_code: str  # HCPCS/CPT code from SV1*HC segment
    procedure_code_qualifier: str = "HC"  # HC=HCPCS, ER=Jurisdiction
    requested_units: int  # SV1 quantity element
    unit_qualifier: str = "UN"  # UN=Units, DA=Days, MJ=Minutes
    service_date_from: str | None = None  # DTP*472 segment
    service_date_to: str | None = None  # DTP*472 segment


class X12ClinicalAttachment(BaseModel):
    """X12 275 reference — attached clinical documentation.

    Production X12 275 transactions carry the documentation inline as
    binary segments (BIN/BDS) or by URL reference. This bundle
    surfaces the reference; the actual documents live in the
    customer's clinical attachment system.
    """

    attachment_control_number: str  # PWK*02 ID
    attachment_type: str  # PWK*01 type code (e.g. "OZ"=support data, "CT"=certification)
    transmission_method: Literal["BM", "EL", "EM", "FX", "FT", "OZ"] = "EL"
    attachment_uri: str | None = None  # customer's clinical-doc URL


class X12PriorAuthRequest(BaseModel):
    """Parsed X12 278 prior-auth request.

    Mirrors the salient ASC X12N v005010X217 segments:
      - 2010A NM1 (Utilization Management Organization)
      - 2010C NM1 (Subscriber) → member_id
      - 2010D NM1 (Patient) — when distinct from subscriber
      - 2000E HL → request loop with HCR (Health Care Services Review Information)
      - 2010EA NM1 (Requester / ordering provider)
      - 2010EB NM1 (Service Provider)
      - 2400 SV1/SV2/SV3 (Service line) → procedure codes + units
      - 2010F HI (Health Care Information / diagnoses) → ICD-10 codes
      - 2400 PWK references → X12 275 attachments
    """

    transaction_control_number: str  # ST*278 control number
    request_category: str  # AR, HS, IN, RU, SC (request type qualifier)
    member_id: str
    subscriber_id: str | None = None  # if patient != subscriber
    requesting_provider_npi: str
    rendering_provider_npi: str | None = None
    diagnosis_codes: list[str]  # ICD-10-CM codes from HI segment
    service_lines: list[X12ServiceLine]
    attachments: list[X12ClinicalAttachment] = Field(default_factory=list)
    place_of_service: str | None = None  # POS code, e.g. "11"=office, "21"=inpatient hospital


def parse_x12_278(raw: str) -> X12PriorAuthRequest:
    """Parse X12 278 transaction.

    STUB — production deployments substitute their X12 parser library
    (pyx12 / bots-edi / Edifecs / vendor SDK from their clearinghouse).
    The integration shape is identical: raw X12 segments in,
    structured `X12PriorAuthRequest` out.

    For demonstration this stub recognizes a JSON-shaped representation
    (i.e. customer pre-parses upstream and sends JSON into the capsule)
    OR a degenerate single-line X12 with `~` segment separators that we
    walk deterministically for the smoke-test input.
    """
    raw = raw.strip()
    if raw.startswith("{"):
        # Pre-parsed JSON path (most production deployments parse X12 at
        # the clearinghouse boundary and ship JSON downstream).
        return X12PriorAuthRequest.model_validate_json(raw)

    # Minimal X12-segment walker for stub. Production: pyx12 / bots-edi.
    print("  [x12] STUB parser — production substitutes pyx12 / bots-edi / vendor SDK", file=sys.stderr)
    segments = [s.strip() for s in raw.split("~") if s.strip()]
    fields: dict[str, Any] = {
        "transaction_control_number": "0001",
        "request_category": "AR",
        "member_id": "",
        "requesting_provider_npi": "",
        "diagnosis_codes": [],
        "service_lines": [],
    }
    for seg in segments:
        parts = seg.split("*")
        tag = parts[0]
        if tag == "ST" and len(parts) >= 3:
            fields["transaction_control_number"] = parts[2]
        elif tag == "HCR" and len(parts) >= 2:
            fields["request_category"] = parts[1]
        elif tag == "NM1" and len(parts) >= 10 and parts[1] == "IL":
            # Subscriber/insured
            fields["member_id"] = parts[9]
        elif tag == "NM1" and len(parts) >= 10 and parts[1] == "71":
            # Requesting provider
            fields["requesting_provider_npi"] = parts[9]
        elif tag == "HI":
            for code_part in parts[1:]:
                if ":" in code_part:
                    qual, code = code_part.split(":", 1)
                    if qual.startswith("ABK") or qual.startswith("ABF"):
                        fields["diagnosis_codes"].append(code)
        elif tag == "SV1" and len(parts) >= 4:
            proc_part = parts[1].split(":") if ":" in parts[1] else ["HC", parts[1]]
            fields["service_lines"].append({
                "procedure_code": proc_part[1] if len(proc_part) > 1 else parts[1],
                "procedure_code_qualifier": proc_part[0] if len(proc_part) > 1 else "HC",
                "requested_units": int(parts[3]) if parts[3].isdigit() else 1,
            })
    return X12PriorAuthRequest.model_validate(fields)


# -- InterQual / MCG criteria binding stub --------------------------------
#
# Production prior-auth pipelines apply the payer's licensed
# medical-necessity criteria via InterQual (Change Healthcare /
# Optum) or MCG Health (Hearst Health) before LLM reasoning. Both
# are per-seat-licensed proprietary content libraries; neither is
# redistributable. The integration shape is consistent: criteria-set
# selection by procedure-code → patient-data binding → criteria
# evaluation → recommendation (Met / Not Met / Pending).


CriteriaOutcome = Literal["criteria_met", "criteria_not_met", "criteria_pending_information"]


class CriteriaEvaluation(BaseModel):
    """Result of binding clinical data against medical-necessity criteria.

    The `engine` field discriminates InterQual vs MCG vs custom; the
    `criteria_set_id` identifies the specific criteria book + version
    (e.g. InterQual "ISC Procedures 2026 Q1" or MCG "ORG:M-200").
    """

    engine: Literal["interqual", "mcg", "custom"]
    criteria_set_id: str
    criteria_version: str
    procedure_code: str
    outcome: CriteriaOutcome
    rationale_summary: str
    criteria_questions_evaluated: int
    criteria_questions_met: int
    additional_information_requested: list[str] = Field(default_factory=list)


def evaluate_criteria(request: X12PriorAuthRequest, service_line: X12ServiceLine) -> CriteriaEvaluation:
    """Evaluate medical-necessity criteria against the request.

    STUB — production deployments compose with their licensed
    criteria-engine (InterQual API / MCG Cite AutoAuth / custom rules
    library). The integration shape:

      criteria_engine.evaluate(
          procedure_code=service_line.procedure_code,
          diagnoses=request.diagnosis_codes,
          place_of_service=request.place_of_service,
          clinical_data=fetch_clinical_data(request.attachments),
      ) -> CriteriaEvaluation

    Common production wiring: the criteria engine runs as a sealed-egress
    HTTPS call to the customer's licensed deployment (e.g.
    `interqual.payer-internal.example.com` or `mcg.payer-internal.example.com`).
    Each evaluation emits one SealedHttpRequest in the AuditProof so
    auditors can reconstruct "this criteria-set + this version was
    applied to this request on this date."
    """
    print(
        f"  [criteria] STUB {CRITERIA_ENGINE} for procedure {service_line.procedure_code} — "
        f"production substitutes licensed criteria-engine API",
        file=sys.stderr,
    )
    return CriteriaEvaluation(
        engine=CRITERIA_ENGINE,  # type: ignore[arg-type]
        criteria_set_id=f"{CRITERIA_ENGINE.upper()}-STUB-{service_line.procedure_code}",
        criteria_version="2026-Q1",
        procedure_code=service_line.procedure_code,
        outcome="criteria_pending_information",
        rationale_summary=(
            f"STUB: {CRITERIA_ENGINE} engine returns pending; "
            "production substitutes licensed engine evaluation."
        ),
        criteria_questions_evaluated=0,
        criteria_questions_met=0,
        additional_information_requested=[
            "production_engine_substitution_required",
        ],
    )


# -- LLM clinical-policy reasoning over criteria evaluation ---------------


class LlmDecisionInput(BaseModel):
    """Input bundle for LLM reasoning."""

    request: X12PriorAuthRequest
    service_line: X12ServiceLine
    criteria_evaluation: CriteriaEvaluation


class LlmDecision(BaseModel):
    """LLM decision over the criteria evaluation.

    The LLM does NOT replace the criteria engine. The criteria engine
    is the licensed content authority; the LLM provides clinical-
    policy reasoning over the evaluation (e.g. surfacing peer-reviewed
    exceptions, recognizing contraindications the criteria don't
    enumerate, drafting determination rationale text).

    Production deployments may run the LLM in advisory mode only —
    the rendered determination cites the criteria evaluation as the
    primary authority and the LLM as supplementary.
    """

    recommended_action: Literal[
        "approve",
        "deny",
        "pend_for_additional_information",
        "pend_for_peer_to_peer_review",
    ]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    cited_criteria_questions: list[str] = Field(default_factory=list)
    advisory_only: bool = True


def render_llm_decision(payload: LlmDecisionInput) -> LlmDecision:
    """LLM reasoning call. Returns advisory decision over criteria evaluation."""
    client = anthropic.Anthropic()
    prompt = (
        "You are an advisory clinical-policy reasoning model for prior-auth "
        "determination. The licensed medical-necessity criteria engine "
        f"({payload.criteria_evaluation.engine}) has already returned: "
        f"outcome={payload.criteria_evaluation.outcome}. Your role is "
        "supplementary reasoning — surface peer-reviewed exceptions, "
        "contraindications the criteria don't enumerate, and draft "
        "determination rationale text. The criteria engine remains the "
        "primary authority.\n\n"
        "Return JSON: {recommended_action: 'approve'|'deny'|"
        "'pend_for_additional_information'|'pend_for_peer_to_peer_review', "
        "rationale: str, confidence: float in [0,1], "
        "cited_criteria_questions: list[str], advisory_only: true}\n\n"
        f"Request: {payload.request.model_dump_json()}\n"
        f"Service line: {payload.service_line.model_dump_json()}\n"
        f"Criteria evaluation: {payload.criteria_evaluation.model_dump_json()}"
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return LlmDecision.model_validate_json(raw.strip())


# -- CMS-0057-F FHIR R4 Prior Authorization response shape ----------------
#
# Output JSON conforms to FHIR R4 PAS Implementation Guide patterns.
# For impacted payers (MA / Medicaid / CHIP / QHPs on FFEs) the
# CMS-0057-F Final Rule (effective Jan 2026 / Jan 2027 by API) mandates
# FHIR-based PA APIs. The response shape:
#   - Claim          : the prior-auth submission resource
#   - ClaimResponse  : the determination resource
#   - Coverage       : member benefit + plan reference
#   - (optional) Communication : when pending additional info


class FHIRReference(BaseModel):
    """FHIR Reference type — points to another resource by id."""

    reference: str  # e.g. "Patient/123" or "Practitioner/npi-9876543210"
    display: str | None = None


class FHIRCodeableConcept(BaseModel):
    """FHIR CodeableConcept — coded value with optional text."""

    system: str  # e.g. "http://www.ama-assn.org/go/cpt" or "http://hl7.org/fhir/sid/icd-10-cm"
    code: str
    display: str | None = None


class FHIRDiagnosis(BaseModel):
    """FHIR Claim.diagnosis backbone element."""

    sequence: int
    diagnosis_codeable_concept: FHIRCodeableConcept


class FHIRItem(BaseModel):
    """FHIR Claim.item backbone — one service line."""

    sequence: int
    product_or_service: FHIRCodeableConcept  # CPT/HCPCS code
    quantity_value: int
    diagnosis_sequence: list[int] = Field(default_factory=list)


class FHIRClaim(BaseModel):
    """FHIR R4 Claim resource (PAS profile).

    Per HL7 PAS IG: `use=preauthorization`. ResourceType MUST be "Claim"
    so the impacted-payer FHIR PAS endpoint can route it.
    """

    resource_type: Literal["Claim"] = "Claim"
    id: str
    status: Literal["active", "cancelled", "draft", "entered-in-error"] = "active"
    use: Literal["preauthorization"] = "preauthorization"
    patient: FHIRReference
    insurer: FHIRReference
    provider: FHIRReference
    diagnosis: list[FHIRDiagnosis] = Field(default_factory=list)
    item: list[FHIRItem] = Field(default_factory=list)
    created: str  # ISO 8601


class FHIRClaimResponseItem(BaseModel):
    """FHIR ClaimResponse.item backbone — per-service determination."""

    item_sequence: int
    adjudication: list[dict[str, Any]] = Field(default_factory=list)
    review_outcome_decision: Literal["approved", "denied", "pended"] = "pended"
    review_outcome_reason: str | None = None


class FHIRClaimResponse(BaseModel):
    """FHIR R4 ClaimResponse resource (PAS profile)."""

    resource_type: Literal["ClaimResponse"] = "ClaimResponse"
    id: str
    status: Literal["active", "cancelled", "draft", "entered-in-error"] = "active"
    type_codeable_concept: FHIRCodeableConcept
    use: Literal["preauthorization"] = "preauthorization"
    patient: FHIRReference
    created: str  # ISO 8601
    insurer: FHIRReference
    request: FHIRReference  # Reference to the source Claim
    outcome: Literal["queued", "complete", "error", "partial"] = "complete"
    pre_auth_ref: str | None = None  # the assigned auth number
    item: list[FHIRClaimResponseItem] = Field(default_factory=list)


class FHIRCoverage(BaseModel):
    """FHIR R4 Coverage resource — minimal subset linking subscriber to plan."""

    resource_type: Literal["Coverage"] = "Coverage"
    id: str
    status: Literal["active", "cancelled", "draft", "entered-in-error"] = "active"
    subscriber_id: str
    beneficiary: FHIRReference
    payor: list[FHIRReference]


class PriorAuthCaseStateAttestation(BaseModel):
    """Customer's appeal-loop state binding.

    The customer's case-management system declares the case-state
    vocabulary in capsule metadata via `case_state` and an optional
    `case_management_uri`. The pipeline emits the current state into
    the output so the customer's downstream case-management system
    can bind the FHIR ClaimResponse to its case lifecycle.

    Production deployments compose with their case-management system
    (Salesforce Health Cloud, Pega, custom case-mgr). The state
    machine is customer-defined; this bundle preserves whatever
    vocabulary the customer ships in metadata.
    """

    case_state: CaseState
    case_management_uri: str | None = None
    appeal_eligible: bool = True
    next_action: str | None = None


class FhirPriorAuthBundle(BaseModel):
    """Top-level FHIR PAS-shaped output.

    Conforms to FHIR R4 transaction-bundle pattern: one Claim + one
    ClaimResponse + one Coverage. The customer's downstream FHIR PAS
    server (their PAS endpoint per CMS-0057-F) ingests this shape
    directly — Epic Hyperdrive PAS / Cerner Millennium PAS / Availity
    PAS Gateway / custom FHIR server.
    """

    schema_version: Literal["fhir-r4-pas-bundle-flattened-v1"] = "fhir-r4-pas-bundle-flattened-v1"
    generated_at: str  # ISO 8601
    transaction_control_number: str  # ties back to source X12 278

    claim: FHIRClaim
    claim_response: FHIRClaimResponse
    coverage: FHIRCoverage

    criteria_evaluations: list[CriteriaEvaluation]
    llm_decisions: list[LlmDecision]
    case_state_attestation: PriorAuthCaseStateAttestation

    # Provenance binding for downstream auditor inspection
    pipeline_provenance: dict[str, Any]


# -- FHIR mapping (X12 278 + criteria + LLM → FHIR Claim + ClaimResponse) --


def map_to_fhir(
    request: X12PriorAuthRequest,
    criteria: list[CriteriaEvaluation],
    decisions: list[LlmDecision],
    case_state: CaseState,
    case_management_uri: str | None,
) -> tuple[FHIRClaim, FHIRClaimResponse, FHIRCoverage]:
    """Map the parsed X12 + criteria + LLM decisions into FHIR PAS resources.

    The mapping rules below are simplified vs the full HL7 PAS IG; they
    demonstrate the shape. Production deployments substitute their
    FHIR-mapping layer (HAPI FHIR server with PAS profiles, Smile CDR,
    custom mappers).
    """
    now = datetime.now(timezone.utc).isoformat()
    claim_id = f"claim-{request.transaction_control_number}"
    response_id = f"claimresponse-{request.transaction_control_number}"
    coverage_id = f"coverage-{request.member_id}"

    diagnoses = [
        FHIRDiagnosis(
            sequence=idx + 1,
            diagnosis_codeable_concept=FHIRCodeableConcept(
                system="http://hl7.org/fhir/sid/icd-10-cm",
                code=code,
            ),
        )
        for idx, code in enumerate(request.diagnosis_codes)
    ]

    items = [
        FHIRItem(
            sequence=idx + 1,
            product_or_service=FHIRCodeableConcept(
                system="http://www.ama-assn.org/go/cpt",
                code=line.procedure_code,
            ),
            quantity_value=line.requested_units,
            diagnosis_sequence=list(range(1, len(request.diagnosis_codes) + 1)),
        )
        for idx, line in enumerate(request.service_lines)
    ]

    claim = FHIRClaim(
        id=claim_id,
        patient=FHIRReference(reference=f"Patient/{request.member_id}"),
        insurer=FHIRReference(reference="Organization/payer"),
        provider=FHIRReference(reference=f"Practitioner/npi-{request.requesting_provider_npi}"),
        diagnosis=diagnoses,
        item=items,
        created=now,
    )

    # Map the LLM decision per service line to ClaimResponse.item.review_outcome
    response_items: list[FHIRClaimResponseItem] = []
    for idx, decision in enumerate(decisions):
        action_to_decision = {
            "approve": "approved",
            "deny": "denied",
            "pend_for_additional_information": "pended",
            "pend_for_peer_to_peer_review": "pended",
        }
        response_items.append(FHIRClaimResponseItem(
            item_sequence=idx + 1,
            review_outcome_decision=action_to_decision.get(  # type: ignore[arg-type]
                decision.recommended_action, "pended"
            ),
            review_outcome_reason=decision.rationale[:240],
        ))

    # Aggregate outcome: complete if all items rendered, partial if mixed
    has_pending = any(i.review_outcome_decision == "pended" for i in response_items)
    aggregate_outcome: Literal["queued", "complete", "error", "partial"] = (
        "partial" if has_pending else "complete"
    )

    claim_response = FHIRClaimResponse(
        id=response_id,
        type_codeable_concept=FHIRCodeableConcept(
            system="http://terminology.hl7.org/CodeSystem/claim-type",
            code="professional",
            display="Professional",
        ),
        patient=FHIRReference(reference=f"Patient/{request.member_id}"),
        created=now,
        insurer=FHIRReference(reference="Organization/payer"),
        request=FHIRReference(reference=f"Claim/{claim_id}"),
        outcome=aggregate_outcome,
        pre_auth_ref=(
            f"PA-{request.transaction_control_number}"
            if not has_pending else None
        ),
        item=response_items,
    )

    coverage = FHIRCoverage(
        id=coverage_id,
        subscriber_id=request.subscriber_id or request.member_id,
        beneficiary=FHIRReference(reference=f"Patient/{request.member_id}"),
        payor=[FHIRReference(reference="Organization/payer")],
    )

    return claim, claim_response, coverage


# -- Pipeline orchestration ----------------------------------------------


def derive_case_state(decisions: list[LlmDecision]) -> CaseState:
    """Derive case_state from the aggregate LLM decisions.

    Customer's case-management vocabulary is declared on capsule create
    via metadata (`case_state` field). This function provides the
    initial state derivation; subsequent state transitions (denied →
    appeal_filed → peer_to_peer_pending → external_review_filed →
    final) happen in the customer's case-management system across
    multiple capsule lifecycles. Each state transition can run as a
    separate capsule chained via `parent_cdp_id`.
    """
    if not decisions:
        return "intake"
    if all(d.recommended_action == "approve" for d in decisions):
        return "rendered"
    if all(d.recommended_action == "deny" for d in decisions):
        return "denied"
    if any(d.recommended_action == "pend_for_peer_to_peer_review" for d in decisions):
        return "peer_to_peer_pending"
    return "criteria_review"


def process_request(raw_x12: str, capsule_metadata: dict[str, Any]) -> FhirPriorAuthBundle:
    request = parse_x12_278(raw_x12)

    criteria_evaluations: list[CriteriaEvaluation] = []
    llm_decisions: list[LlmDecision] = []

    for line in request.service_lines:
        criteria = evaluate_criteria(request, line)
        criteria_evaluations.append(criteria)

        decision = render_llm_decision(LlmDecisionInput(
            request=request,
            service_line=line,
            criteria_evaluation=criteria,
        ))
        llm_decisions.append(decision)

    derived_case_state = derive_case_state(llm_decisions)

    # Customer-declared case_state in metadata wins over derivation.
    # Customer's case-management system tells us "this request entered
    # the workflow at state X" — derivation is the fallback for fresh
    # intake.
    customer_case_state_raw = capsule_metadata.get("case_state")
    case_state: CaseState
    if customer_case_state_raw and customer_case_state_raw in (
        "intake", "criteria_review", "rendered", "denied",
        "appeal_filed", "peer_to_peer_pending", "peer_to_peer_complete",
        "external_review_filed", "external_review_complete", "final",
    ):
        case_state = customer_case_state_raw  # type: ignore[assignment]
    else:
        case_state = derived_case_state

    case_management_uri = capsule_metadata.get("case_management_uri")
    case_state_attestation = PriorAuthCaseStateAttestation(
        case_state=case_state,
        case_management_uri=case_management_uri,
        appeal_eligible=case_state in (
            "denied", "appeal_filed", "peer_to_peer_pending", "peer_to_peer_complete"
        ),
        next_action={
            "denied": "appeal_filed",
            "appeal_filed": "peer_to_peer_pending",
            "peer_to_peer_pending": "peer_to_peer_complete",
            "peer_to_peer_complete": "external_review_filed",
            "external_review_filed": "external_review_complete",
            "external_review_complete": "final",
        }.get(case_state),
    )

    claim, claim_response, coverage = map_to_fhir(
        request, criteria_evaluations, llm_decisions,
        case_state, case_management_uri,
    )

    pipeline_provenance = {
        "criteria_engine": CRITERIA_ENGINE,
        "criteria_engine_endpoint_configured": bool(CRITERIA_ENGINE_BASE),
        "llm_model": MODEL,
        "fhir_pas_endpoint_configured": bool(PAYER_FHIR_PAS_BASE),
        "case_state_vocabulary": CASE_STATE_VOCABULARY,
        "x12_payload_sha512": "sha512:" + hashlib.sha512(raw_x12.encode()).hexdigest(),
    }

    return FhirPriorAuthBundle(
        generated_at=datetime.now(timezone.utc).isoformat(),
        transaction_control_number=request.transaction_control_number,
        claim=claim,
        claim_response=claim_response,
        coverage=coverage,
        criteria_evaluations=criteria_evaluations,
        llm_decisions=llm_decisions,
        case_state_attestation=case_state_attestation,
        pipeline_provenance=pipeline_provenance,
    )


def load_capsule_metadata() -> dict[str, Any]:
    """Read capsule metadata from the runtime mount.

    Customer-declared metadata (`case_state`, `case_management_uri`,
    payer-specific keys) lives at `/data/metadata.json` per the
    capsule runtime contract.
    """
    metadata_path = Path("/data/metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    capsule_metadata = load_capsule_metadata()

    # Accept either:
    #   /data/input/uploads/request.x12   — raw X12 278 transaction
    #   /data/input/uploads/request.json  — pre-parsed JSON shape
    x12_path = INPUT_DIR / "request.x12"
    json_path = INPUT_DIR / "request.json"
    bundle_in_path = INPUT_DIR / "requests.jsonl"

    bundles: list[FhirPriorAuthBundle] = []

    if bundle_in_path.exists():
        # Batch mode: one transaction per JSONL line (pre-parsed JSON shape).
        for line in bundle_in_path.read_text().splitlines():
            if not line.strip():
                continue
            print(f"Processing batch entry transaction_control_number=...")
            bundles.append(process_request(line, capsule_metadata))
    elif x12_path.exists():
        print(f"Processing X12 278 transaction from {x12_path.name}")
        bundles.append(process_request(x12_path.read_text(), capsule_metadata))
    elif json_path.exists():
        print(f"Processing pre-parsed JSON request from {json_path.name}")
        bundles.append(process_request(json_path.read_text(), capsule_metadata))
    else:
        print(
            "No input found. Provide one of:\n"
            "  /data/input/uploads/request.x12    — raw X12 278 transaction\n"
            "  /data/input/uploads/request.json   — pre-parsed JSON shape\n"
            "  /data/input/uploads/requests.jsonl — batch mode (one JSON per line)\n",
            file=sys.stderr,
        )
        return

    output_path = OUTPUT_DIR / "fhir_pa_responses.jsonl"
    output_path.write_text(
        "\n".join(b.model_dump_json() for b in bundles)
    )

    summary_path = OUTPUT_DIR / "summary.json"
    decision_counts: dict[str, int] = {}
    case_state_counts: dict[str, int] = {}
    for b in bundles:
        for d in b.llm_decisions:
            decision_counts[d.recommended_action] = decision_counts.get(d.recommended_action, 0) + 1
        case_state_counts[b.case_state_attestation.case_state] = (
            case_state_counts.get(b.case_state_attestation.case_state, 0) + 1
        )
    summary_path.write_text(json.dumps({
        "schema_version": "fhir-r4-pas-bundle-flattened-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "transactions_processed": len(bundles),
        "decision_distribution": decision_counts,
        "case_state_distribution": case_state_counts,
        "criteria_engine": CRITERIA_ENGINE,
        "fhir_pas_endpoint_configured": bool(PAYER_FHIR_PAS_BASE),
    }, indent=2))

    print(
        f"Processed {len(bundles)} transactions → {output_path}\n"
        f"Summary → {summary_path}"
    )


if __name__ == "__main__":
    main()
