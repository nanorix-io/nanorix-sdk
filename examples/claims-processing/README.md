# claims-processing

**Production-pattern reference** for insurance prior-authorization review inside a Nanorix capsule. Demonstrates the pipeline shape that CMS-0057-F-impacted payers (Medicare Advantage, Medicaid, CHIP, QHPs on FFEs) must operate as of Jan 2026: X12 278/275 EDI ingestion → InterQual or MCG medical-necessity criteria binding → LLM clinical-policy reasoning → FHIR R4 Prior Authorization response (PAS profile) → appeal-loop case-state for the customer's case-management system. Per-event customer KMS attestation binds each ActivityEvent into the AuditProof's reserved `per_event_attestations` slot.

> Reframed from "claim payload → LLM decision" (one-shot LLM-only render) to "X12 → criteria → LLM advisory → FHIR PAS → appeal-loop" (full prior-auth pipeline). One-shot LLM rendering is for triage / drafting only; **CMS-0057-F-impacted payer determinations require licensed medical-necessity criteria as the primary authority and FHIR R4 PAS conformance for the response API.**

## Pipeline shape

```
X12 278 EDI request from clearinghouse
(Availity / Change Healthcare / Waystar)
  │
  ▼
[Layer 1] X12 278/275 parse
          ├─ member_id + subscriber_id
          ├─ requesting_provider_npi + rendering_provider_npi
          ├─ diagnosis_codes (ICD-10-CM from HI segment)
          ├─ service_lines (CPT/HCPCS + units from SV1/SV2/SV3)
          └─ X12 275 attached clinical documentation references
  │
  ▼
[Layer 2] InterQual / MCG medical-necessity criteria binding
          ├─ engine = interqual | mcg | custom
          ├─ criteria_set_id + criteria_version
          └─ outcome = criteria_met | criteria_not_met | criteria_pending_information
  │
  ▼
[Layer 3] LLM clinical-policy reasoning (advisory over criteria evaluation)
          ├─ recommended_action = approve | deny | pend_for_additional_information
          │                       | pend_for_peer_to_peer_review
          ├─ rationale + confidence
          └─ cited_criteria_questions
  │
  ▼
[Layer 4] FHIR R4 PAS-conformant response mapping
          ├─ Claim resource (use=preauthorization)
          ├─ ClaimResponse resource (review_outcome per service line)
          └─ Coverage resource (subscriber → payor)
  │
  ▼
[Layer 5] Appeal-loop case-state attestation
          ├─ case_state = intake | criteria_review | rendered | denied
          │             | appeal_filed | peer_to_peer_pending
          │             | peer_to_peer_complete | external_review_filed
          │             | external_review_complete | final
          └─ next_action (state-machine transition for customer's CMS)
  │
  ▼
FHIR PAS response bundle (JSONL) + summary.json → /data/output
```

## What's implemented vs stubbed

| Layer | Implementation | Production substitute |
|-------|----------------|----------------------|
| X12 278/275 parse | **Stub minimal segment walker** (recognizes ST, HCR, NM1*IL, NM1*71, HI, SV1) + JSON-passthrough mode for pre-parsed input | pyx12, bots-edi, Edifecs, vendor SDK from clearinghouse (Availity, Change Healthcare, Waystar) |
| InterQual / MCG criteria binding | Stub returning `criteria_pending_information` with engine + version metadata | InterQual (Change Healthcare / Optum) Connect API, MCG Cite AutoAuth, custom rules library — all per-seat licensed, not redistributable |
| LLM clinical-policy reasoning | **Implemented** — Claude reasoning over criteria evaluation, advisory-mode flag preserved | Customer's clinical-safety-board-approved prompt + post-processing rules; advisory-mode disclosure preserved in determination text |
| FHIR R4 PAS mapping | **Implemented** — Claim + ClaimResponse + Coverage resources flattened to JSON | HAPI FHIR (Java) / Smile CDR / custom FHIR server with PAS profiles; HL7 PAS Implementation Guide conformance verification |
| Appeal-loop case-state | **Implemented** — derives from LLM decisions; customer-declared `case_state` in metadata wins | Customer's case-management system (Salesforce Health Cloud, Pega, custom) consumes case_state vocabulary; state transitions across separate capsules chained via `parent_cdp_id` |
| Per-event customer KMS attestation | Framing implemented; signing_config concrete shape documented in capsulefile.yaml (commented-out example) | Customer hosts mTLS signing endpoint backed by AWS KMS / GCP Cloud KMS / Azure Key Vault / HashiCorp Vault; rotation-aware via public_key_id |

## Industry signal

Companies running adjacent UM / prior-auth AI patterns: **Cohere Health** (utilization-management LLM-augmented review at scale across MA / Medicaid; ~14M lives covered via payer partnerships), **Notable** (provider-side prior-auth automation), **Anthem / Elevance Health** internal AI teams (in-house ML for prior-auth + claims), **EvolentHealth** (specialty benefits AI; oncology + cardiology prior-auth), and payer-side teams using frontier LLMs for clinical-policy reasoning over InterQual / MCG evaluations. Adjacent-but-distinct: **Availity** (clearinghouse for X12 transactions including 278/275), **Change Healthcare** (clearinghouse + InterQual licensee, now Optum), **Waystar** (clearinghouse + revenue cycle).

> **Defunct prior generation:** Olive AI (shut down 2023). Newer generation operates under CMS-0057-F era constraints — FHIR PAS API conformance is no longer optional for impacted payers as of Jan 2026.

## CMS-0057-F Final Rule (RIN 0938-AU87)

The CMS Interoperability and Prior Authorization Final Rule (effective Jan 2026 for Medicare Advantage, Medicaid managed care, CHIP managed care, QHPs on FFEs) mandates three FHIR-based PA APIs:

- **PAS** — Prior Authorization Support API (this bundle's output shape: Claim + ClaimResponse + Coverage)
- **CRD** — Coverage Requirements Discovery API (provider-facing, returns coverage requirements at point-of-care)
- **DTR** — Documentation Templates and Rules API (provider-facing, returns templates the provider needs to satisfy)

Plus PA decision metrics reporting (number of standard / expedited PAs, denial rates, average decision time) starting Jan 2026.

The customer determines applicability and conformance verification with regulatory counsel. This bundle demonstrates the FHIR resource shape only — production deployments wire to their FHIR PAS server and validate against the HL7 PAS Implementation Guide.

## Why per-event customer attestation

Without per-event attestation, the customer's verifier sees: "Nanorix says this capsule called `interqual.payer-internal.example.com`." With per-event attestation, the verifier sees: "Nanorix says this capsule called `interqual.payer-internal.example.com`, AND the customer's KMS-backed signing service signed each event with a key the customer controls." Two-party attestation closes the cooperative-attacker scenario where Nanorix-the-attestor and the customer-as-verifier are separate trust domains.

For prior-auth specifically: **state insurance commissioners** (under state-specific PA reporting rules), **CMS** (under the CMS-0057-F PA decision metrics reporting requirement), **ERISA fiduciary auditors** (under ERISA §503 claim procedure rules), and **NCQA accreditation auditors** (Utilization Management standards UM 2 + UM 5 + UM 7) will eventually want signed evidence the criteria-engine call happened with these inputs, returned this evaluation, the LLM reasoning carried this rationale, and the FHIR response was emitted with this content. Per-event customer attestation produces that evidence cryptographically without trusting Nanorix as the sole signer.

## Customer signing endpoint contract

Customer hosts an HTTPS+mTLS service at `signing_config.endpoint`. Each ActivityEvent triggers one POST:

```
POST /v1/sign
Content-Type: application/json
mTLS client cert: <pinned to customer's signer client cert>
mTLS server CA pin: sha256:<hex of customer's signer CA root>

{
  "event_id": "<uuid>",
  "event_canonical_hash": "sha512:<128 hex>",
  "event_type": "x12_parse|criteria_evaluation|llm_decision|fhir_emit|case_state_transition"
}

→ 200 OK
{
  "event_id": "<uuid>",
  "signature": "base64:<Ed25519 or RSA-PSS sig>",
  "public_key_id": "<customer's KMS key version id>",
  "signed_at": "<RFC 3339>"
}
```

KMS key rotation: when the customer rotates the KMS key version, the new `public_key_id` rides on subsequent events. AuditProof's `per_event_attestations` carries the `public_key_id` used for each event, so verifiers can reconstruct which key version signed what even after rotation. The customer's signing service is responsible for KMS key access, audit logging, and rate limiting. Nanorix fires-and-buffers — signing failures don't block capsule execution; failed events are recorded in AuditProof with `signature_failure_reason` so verifier can refuse incomplete proofs.

## Inputs

```
/data/input/uploads/
  ├── request.x12       # raw X12 278 transaction (clearinghouse-delivered)
  └── request.json      # OR pre-parsed JSON shape (when X12 parse runs upstream)
  └── requests.jsonl    # OR batch mode (one pre-parsed JSON per line)
```

Pre-parsed JSON shape for `request.json` / `requests.jsonl` line:

```json
{
  "transaction_control_number": "0001",
  "request_category": "AR",
  "member_id": "M123456789",
  "subscriber_id": "M123456789",
  "requesting_provider_npi": "1234567890",
  "rendering_provider_npi": "9876543210",
  "diagnosis_codes": ["M17.11", "M25.561"],
  "service_lines": [
    {
      "procedure_code": "27447",
      "procedure_code_qualifier": "HC",
      "requested_units": 1,
      "unit_qualifier": "UN"
    }
  ],
  "attachments": [
    {
      "attachment_control_number": "ATT001",
      "attachment_type": "OZ",
      "transmission_method": "EL",
      "attachment_uri": "https://attachments.payer-internal.example.com/clinical/ATT001"
    }
  ],
  "place_of_service": "21"
}
```

## Outputs

```
/data/output/
  ├── fhir_pa_responses.jsonl   # per-transaction FHIR PAS bundles
  └── summary.json              # batch-level summary (decision + case_state distributions)
```

`fhir_pa_responses.jsonl` line shape (FHIR PAS bundle, flattened):

```json
{
  "schema_version": "fhir-r4-pas-bundle-flattened-v1",
  "generated_at": "2026-...",
  "transaction_control_number": "0001",
  "claim": {
    "resource_type": "Claim",
    "id": "claim-0001",
    "status": "active",
    "use": "preauthorization",
    "patient": {"reference": "Patient/M123456789"},
    "insurer": {"reference": "Organization/payer"},
    "provider": {"reference": "Practitioner/npi-1234567890"},
    "diagnosis": [...],
    "item": [...],
    "created": "2026-..."
  },
  "claim_response": {
    "resource_type": "ClaimResponse",
    "id": "claimresponse-0001",
    "status": "active",
    "use": "preauthorization",
    "outcome": "complete",
    "pre_auth_ref": "PA-0001",
    "item": [
      {
        "item_sequence": 1,
        "review_outcome_decision": "approved",
        "review_outcome_reason": "..."
      }
    ]
  },
  "coverage": {...},
  "criteria_evaluations": [...],
  "llm_decisions": [...],
  "case_state_attestation": {
    "case_state": "rendered",
    "case_management_uri": "https://cms.payer-internal.example.com/cases/...",
    "appeal_eligible": true,
    "next_action": null
  },
  "pipeline_provenance": {
    "criteria_engine": "interqual",
    "criteria_engine_endpoint_configured": true,
    "llm_model": "claude-3-5-sonnet-latest",
    "fhir_pas_endpoint_configured": true,
    "case_state_vocabulary": "intake|criteria_review|peer_to_peer|external_review|final",
    "x12_payload_sha512": "sha512:..."
  }
}
```

## Appeal-loop state composition

Prior-auth determinations don't end at first-pass render. Production pipelines orchestrate:

```
intake
  │
  ▼
criteria_review
  │
  ├─→ rendered (approved + finalized) ──→ final
  │
  └─→ denied
          │
          ▼
       appeal_filed
          │
          ▼
       peer_to_peer_pending  (clinician-to-medical-director call)
          │
          ▼
       peer_to_peer_complete
          │
          ├─→ rendered (overturned on peer-to-peer) ──→ final
          │
          └─→ external_review_filed  (state-DOI external review)
                  │
                  ▼
               external_review_complete
                  │
                  └─→ final
```

Each state transition runs as a separate capsule chained via `parent_cdp_id`. The customer's case-management system holds the lifecycle state-of-truth; capsules consume the state from metadata and emit transitions into the FHIR bundle's `case_state_attestation`.

## Memory artifact composition (multi-state-transition pipelines)

For workflows where the appeal-loop spans multiple capsules (e.g. determination → denial → appeal → peer-to-peer review → external review across days/weeks), use the **memory artifact pattern** to checkpoint case-state + accumulated criteria evaluations + LLM decisions: each transition's capsule produces a memory artifact (encrypted with customer's KMS key, never plaintext on Nanorix infrastructure). The next capsule loads the checkpoint and continues from the next state. The AuditProofs link via `parent_cdp_id`. See [`legal-ediscovery/`](../legal-ediscovery/) for the same pattern applied to TAR 2.0 / CAL iterations.

## What the AuditProof records

For each capsule:

- **`FileUploaded`** — one per input file (`request.x12` / `request.json` / `requests.jsonl`). Carries SHA-512 of bytes.
- **`MetadataSet`** — capsule metadata (`case_state`, `case_management_uri`, customer-defined keys consumed via the open-vocabulary metadata slot).
- **`SealedHttpRequest`** (criteria engine) — one per service line. Body hash includes the criteria-engine request payload; response is the criteria evaluation. Signed by customer's KMS via `signing_config.endpoint` if declared.
- **`SealedHttpRequest`** (LLM clinical-policy reasoning) — one per service line. Body hash includes the rendered prompt with criteria evaluation; response is the advisory decision JSON. Customer signature.
- **`per_event_attestations`** — array of `EventAttestation` entries when `signing_config` is declared on the Capsulefile. Each entry: `event_id`, `signature` (Ed25519 or RSA-PSS), `public_key_id` (customer's KMS key version), `signed_at`. Reserved slot is populated when signing service round-trips successfully.
- **`BatchRecordProcessed`** + **`BatchCompleted`** — per-transaction batch variants (when `requests.jsonl` is the input). The `BatchCompleted` event's payload carries the Merkle root over per-record FHIR PAS bundles — verifier can prove inclusion of any specific `transaction_control_number` in O(log N) without re-reading the entire batch.
- **`FileDownloaded`** — `fhir_pa_responses.jsonl` + `summary.json` outputs.
- **`ExecutionCompleted`** — exit + duration.

The capsule's metadata slot is open-vocabulary; customer-defined keys like `case_state`, `case_management_uri`, `payer_org_id`, `prior_auth_program_id` flow through unchanged into AuditProof's `capsule_context`.

## Compliance binding

```python
from nanorix.compliance import HIPAAContext, SOC2Context

# US prior-auth: HIPAA (PHI in claims data)
HIPAAContext().bind(proof)   # §164.312 access/audit/integrity

# Payer SOC 2: CC6 + CC7
SOC2Context().bind(proof)    # CC6 access controls + CC7 system operations
```

`SOC2Context` maps the AuditProof activity_trail + per_event_attestations to SOC 2 Trust Services Criteria — the cryptographic chain becomes the auditor's evidence for CC7.4 (logging) + CC6.6 (logical access).

The `regulatory_context` block on the AuditProof emits `related_to` mappings to relevant HIPAA / SOC 2 provisions. Nanorix attests to the data-handling boundary; **CMS-0057-F PAS API conformance verification, ERISA §503 procedure conformance, and NCQA UM standards alignment are the customer's procurement compliance team's determination.**

## Why a Nanorix capsule

Prior-authorization data carries PHI (member health information), PII (member ID, NPI), and the full clinical picture (diagnosis codes + procedure codes + attached clinical documentation). Multiple sealed HTTPS calls per transaction: criteria engine, LLM provider, FHIR PAS server, case-management system. Each carries claim data into a third-party service. With Nanorix:

- The X12 transaction + parsed request live in volatile memory only — destroyed with the capsule.
- All four egress calls are sealed-daemon-routed: TLS validated against system trust roots + body hash + response hash + duration recorded.
- The criteria evaluation + LLM decision are bound into the activity_trail.
- The FHIR PAS bundle output is hashed; downstream emission to the customer's PAS server is one additional `SealedHttpRequest` (when configured).
- Per-event customer KMS attestation provides two-party evidence that the run happened with these inputs.
- The capsule destroys all PHI bytes after execution; AuditProof attests destruction.

## Production-deployment compose disclosure

This bundle is a **production-pattern reference**, not a production prior-auth product. Customer composes the substrate:

| Substrate layer | Customer composes |
|----------------|-------------------|
| X12 278/275 parsing | pyx12 / bots-edi / Edifecs / clearinghouse vendor SDK |
| Medical-necessity criteria | Licensed InterQual or MCG deployment (per-seat licensed; not redistributable) |
| FHIR PAS server | Epic Hyperdrive PAS / Cerner Millennium PAS / Availity PAS Gateway / Smile CDR / HAPI FHIR + PAS profiles |
| Case-management system | Salesforce Health Cloud / Pega / custom case-mgr; case_state vocabulary customer-declared |
| KMS signing endpoint | mTLS service backed by AWS KMS / GCP Cloud KMS / Azure Key Vault / HashiCorp Vault |
| Clearinghouse interconnect | Availity / Change Healthcare (Optum) / Waystar X12 channels |

Nanorix injects nothing. The customer's procurement compliance team determines:
- **CMS-0057-F PAS API conformance** verification (against HL7 PAS Implementation Guide)
- **HHS BAA chain confirmation** (Nanorix BAA + LLM provider BAA + criteria-engine BAA + FHIR-server BAA)
- **ERISA §503 claim procedure** conformance (denial notice content + appeal procedures + timeframes)
- **NCQA UM accreditation** alignment (UM 2 + UM 5 + UM 7 standards)
- **State-DOI prior-auth reporting** rules (state-specific transparency reporting)

## Running this bundle

```bash
nanorix build -f capsulefile.yaml

# Capsule create with case-management metadata
CAPSULE=$(nanorix capsules create \
  --capsulefile cf_<uuid> \
  --jurisdiction US \
  --data-classification PHI \
  --metadata '{"case_state":"intake","case_management_uri":"https://cms.payer-internal.example.com/cases/abc","payer_org_id":"payer-001","prior_auth_program_id":"pa-prog-2026"}')

# Upload an X12 278 transaction OR pre-parsed JSON
nanorix capsules upload $CAPSULE -f input/request.x12

# Execute with environment configured for the customer's deployment
ANTHROPIC_API_KEY=sk-ant-... \
  CRITERIA_ENGINE=interqual \
  CRITERIA_ENGINE_BASE=https://criteria-engine.payer-internal.example.com \
  PAYER_FHIR_PAS_BASE=https://fhir-pas.payer-internal.example.com \
  nanorix capsules exec $CAPSULE "python main.py"

nanorix capsules download $CAPSULE -f fhir_pa_responses.jsonl
nanorix capsules download $CAPSULE -f summary.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE > auditproof.json
nanorix verify auditproof.json

# For the appeal loop (denied → appeal_filed transition):
PARENT_CDP=$(jq -r .capsule_id auditproof.json)
APPEAL_CAPSULE=$(nanorix capsules create \
  --capsulefile cf_<uuid> \
  --jurisdiction US \
  --data-classification PHI \
  --parent-cdp $PARENT_CDP \
  --metadata '{"case_state":"appeal_filed","case_management_uri":"https://cms.payer-internal.example.com/cases/abc"}')
# ... repeat upload + exec + destroy for each state transition ...
```

## What this bundle does NOT cover

- **Production X12 EDI parser body** — Layer 1 ships a minimal segment walker. Customer fills with pyx12 / bots-edi / Edifecs / clearinghouse vendor SDK.
- **Production InterQual / MCG criteria-engine integration** — Layer 2 ships a stub. Customer wires their licensed criteria-engine deployment (the engines themselves are per-seat licensed, not redistributable).
- **HL7 PAS Implementation Guide conformance verification** — FHIR resource shape is demonstrated; Customer's FHIR-server team validates against the HL7 PAS IG.
- **CMS-0057-F PA decision metrics reporting** — capsule emits per-determination AuditProof; the customer's reporting layer aggregates across capsules to produce CMS-required metrics (number of standard / expedited PAs, denial rates, average decision time).
- **Production case-management state machine** — `case_state` vocabulary is customer-declared; the customer's case-management system (Salesforce Health Cloud / Pega / custom) holds the lifecycle state-of-truth.
- **SaMD class determination** — advisory-mode LLM reasoning over criteria evaluation is one classification fulcrum (informs vs directs care). Customer's regulatory counsel determines SaMD class for the specific clinical decision-support deployment.
- **ERISA §503 claim procedure** conformance — denial notice content + appeal procedures + timeframes are the customer's compliance team's determination.
