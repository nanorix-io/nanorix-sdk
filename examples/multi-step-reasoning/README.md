# multi-step-reasoning

**Production-pattern reference** for clinical-reasoning agent workloads inside a Nanorix capsule. Demonstrates the multi-step reasoning loop pattern: longitudinal patient context (PHI) → iterative LLM reasoning steps with knowledge-base retrieval → final structured clinical recommendation. Each reasoning step is hashed into the AuditProof; auditors can reconstruct the agent's full decision path step-by-step.

> **Important: this is a *pattern reference*, not a SaMD-classified clinical decision-support product.** The reasoning loop, knowledge-base retrieval, and recommendation-shape are illustrative. Production deployments substitute customer-licensed knowledge bases (UpToDate / DynaMed / BMJ Best Practice) and assess SaMD classification with regulatory counsel before any clinical use. Nanorix is the attestation substrate; the clinical product is the customer's.

## Industry signal

Pattern 1 in the [9-pattern atlas](../../docs-site/docs/recipes/multi-step-reasoning.md) — 40 companies tracked at the substrate-fit horizon. Production reasoning-agent workflows in this space:

- **Sully** — referral-letter triage + clinical reasoning
- **Notable** — provider-side reasoning agents (intake, discharge, follow-up planning)
- **Hippocratic AI** — clinician agent stack (Polaris, ADK)
- **Tennr** — claims + referral reasoning
- **Augmedix** — ambient documentation reasoning chains

These workflows uniformly require: (a) the agent's reasoning trace as audit-grade evidence (defensibility for clinician acceptance + payer review + regulatory inquiry), (b) cryptographic binding from input PHI through reasoning steps to final recommendation, (c) each LLM call + KB retrieval recorded with bytes-level hashes, (d) zero PHI persistence outside the capsule.

## Pipeline shape

```
Patient summary (PHI)
  │
  ▼
[Loop iteration 1..N]
  │  ┌─────────────────────────────────────────┐
  │  │ LLM reasoning step (Claude / GPT-4)     │
  │  │  → action: continue | KB lookup | final │
  │  │  → observation: reasoning text          │
  │  │  → confidence: 0.0-1.0 self-reported    │
  │  └─────────────────────────────────────────┘
  │       │
  │       ▼ (if KB lookup)
  │  ┌─────────────────────────────────────────┐
  │  │ Knowledge-base retrieval (UpToDate /    │
  │  │ DynaMed / customer's licensed deployment)│
  │  └─────────────────────────────────────────┘
  │       │
  │       ▼
  │  Append to reasoning trace
  │       │
  │       ▼ Termination check (confidence ≥ threshold OR step cap)
  ▼
Final structured recommendation
  (template-shaped: differential | drug-interaction | follow-up)
```

Loop control parameters are env-tunable (`MAX_REASONING_STEPS`, `REASONING_CONFIDENCE_THRESHOLD`). Customer tunes per workflow shape — differential-diagnosis loops typically converge in 3-5 steps; complex M&M case reviews iterate longer (memory artifact + parent_cdp_id chain composes for >60min runs; see [`long-running-agent`](../long-running-agent/) bundle).

## Reasoning template

The bundle ships three reference templates selected via `REASONING_TEMPLATE` env var:

| Template | Workflow | Termination signal |
|----------|----------|--------------------|
| `differential_diagnosis` | Generate ranked differential from longitudinal record | LLM emits final differential with supporting evidence |
| `drug_interaction_check` | Screen medication list for clinically actionable interactions | LLM emits severity-graded findings |
| `follow_up_recommendation` | Plan timing/modality of next encounter | LLM emits timing + modality + rationale |

Customer extends or substitutes per their workflow. Each template renders a class-specific system prompt; the loop control is identical.

## Knowledge-base composition

The agent calls the customer's licensed clinical knowledge base when the LLM requests a lookup (typically: "I need the latest guideline on X" or "what's the prevalence of Y in this demographic"). The retrieval result is folded back into the next reasoning step's context. Each retrieval is one `SealedHttpRequest` event in the AuditProof.

Production-substitute KBs:

- **UpToDate (Wolters Kluwer)** — most common in U.S. clinical settings; per-seat licensed
- **DynaMed (EBSCO)** — evidence-based clinical reference
- **BMJ Best Practice** — common in UK / Commonwealth settings
- **PubMed / E-utilities** — public NIH NLM endpoint (no license required, lower-precision)
- **Customer-curated rules engine** — internal CDS rules (e.g. customer's clinical-pathways library)
- **Vector-DB-backed semantic search** — over customer's licensed corpus (Pinecone / Weaviate / pgvector inside customer's environment)

## Confidence calibration

Self-reported confidence at each step is the loop's termination signal. The threshold (`REASONING_CONFIDENCE_THRESHOLD`) is customer-tunable. Lower thresholds (0.70) reach termination faster but with more residual diagnostic uncertainty; higher thresholds (0.95) drive deeper reasoning but risk loop exhaustion (`step_cap_reached`).

The reasoning trace records confidence per step, so auditors can see whether the agent terminated on a high-confidence final answer or hit the step cap with residual uncertainty. The `termination_reason` field distinguishes the cases:

- `llm_emitted_final` — agent self-determined the final answer
- `confidence_threshold` — confidence ≥ threshold triggered termination
- `step_cap_reached` — loop exhausted without final answer (synthesized minimal recommendation; flagged `clinician_review_required_loop_did_not_terminate`)

## Why a Nanorix capsule

PHI in the patient summary triggers HIPAA's "minimum necessary" + business-associate clauses. Calling an LLM iteratively over PHI without sealed isolation makes the calling host (your AI service, your CDS gateway, your dev laptop) part of HIPAA scope. With Nanorix:

- The patient summary lives in volatile memory only, destroyed with the capsule.
- Each LLM reasoning step's HTTPS call to `api.anthropic.com` flows through the sealed egress layer: TLS validated against system trust roots + per-request hash + duration recorded.
- Each knowledge-base retrieval to the customer's licensed deployment is similarly attested.
- The reasoning trace (each step's observation + confidence + KB-result hash) is hashed into the AuditProof.
- The structured recommendation output is hashed into the activity trail.
- AuditProof is signed Ed25519; verifiers reconstruct the agent's reasoning path step-by-step.

## What the AuditProof records

Activity-trail events for one patient summary processed through a 5-step reasoning loop:

1. **`FileUploaded`** — patient summary. Carries `filename` + `size_bytes` + SHA-512.
2. **`SealedHttpRequest`** (one per reasoning step, 5 events) — `host: api.anthropic.com`, `body_hash` (SHA-512 of rendered prompt), `response_body_hash` (SHA-512 of LLM response), TLS metadata.
3. **`SealedHttpRequest`** (one per KB lookup when reasoning step requests it) — `host: <customer's KB host>`, `body_hash` of query, `response_body_hash` of excerpt.
4. **`ReasoningStepCompleted`** (one per loop iteration) — `step_number`, `action`, `confidence`, `observation_hash` (SHA-512 of the step's observation text). Reasoning trace integrity bound into AuditProof's hash chain.
5. **`ExecutionCompleted`** — exit + duration.
6. **`FileDownloaded`** — `recommendation.json` output bytes hashed.

When customer enables BYO-HSM signing (per `signing_config` block in capsulefile), each `ReasoningStepCompleted` event also produces a `per_event_attestations` entry signed by the customer's HSM — enabling step-level non-repudiation under the customer's signing authority.

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HIPAAContext, AIActContext, NISTAIRMFContext

client = Client()
proof = client.proofs.get(capsule_id)

HIPAAContext().bind(proof)        # related_to §164.312(a-e)
AIActContext().bind(proof)        # EU AI Act helper when in scope
NISTAIRMFContext().bind(proof)    # NIST AI Risk Management Framework
```

The `regulatory_context` block enumerates provisions this AuditProof's activity trail is `related_to`:

- **HIPAA Security Rule §164.312(a-e)** (framework_version: 2026-02) — access control, audit controls, integrity, transmission security
- **EU AI Act Article 13** (framework_version: 2026-02) — transparency obligations for high-risk AI; reasoning trace + per-step model attestation supports the technical-documentation requirement
- **NIST AI RMF MEASURE-2.13** (framework_version: 2026-02) — model versioning + reasoning trace traceability
- **21 CFR Part 11 §11.10(b,c,e)** (framework_version: 2026-02) — when SaMD-classified, audit trail discipline + record integrity

Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination. For SaMD classification, customer's regulatory counsel determines FDA Class (I / II / III) per the AI/ML Action Plan and applicable 510(k) / De Novo submission strategy.

> **Disclaimer:** Nanorix is an evidence vendor (role 1 of three-role liability split). Customer remains responsible for their organizational GRC program. The reasoning loop, knowledge-base, and recommendation shapes are illustrative reference implementations — production composes customer's licensed KB, customer's regulatory-counsel-determined SaMD classification, and customer's clinical-workflow validation.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# Drop a representative patient summary into input/
# (no real PHI — synthetic / consented research data only)
echo "$(cat <<'EOF'
PATIENT SUMMARY (synthetic):
  Age 67 F, T2DM 12y, HTN, CKD-3a (eGFR 47), HFpEF.
  Med list: metformin 1000mg BID, lisinopril 20mg, atorvastatin 40mg, empagliflozin 10mg.
  Recent labs: HbA1c 8.2 (was 7.4 6mo prior), K 4.8, creatinine 1.4.
  Recent visit: increasing edema + DOE on minimal exertion.
QUESTION: differential for worsening volume status; medication adjustment recommendations.
EOF
)" > input/case-001.txt

# Create capsule + execute
CAPSULE=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification PHI)
nanorix capsules upload $CAPSULE -f input/case-001.txt
ANTHROPIC_API_KEY=sk-ant-... nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f recommendation.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

## Cross-links

- Recipe: [`docs-site/docs/recipes/multi-step-reasoning.md`](../../docs-site/docs/recipes/multi-step-reasoning.md) — recipe-level walkthrough with model-snapshot binding + replay attestation
- CLI reference: [`docs-site/docs/cli-reference.md`](../../docs-site/docs/cli-reference.md) — `nanorix build` / `capsules` / `proofs` / `verify`
- Compliance frameworks:
  - [`docs/compliance/hipaa-controls-mapping.md`](../../docs/compliance/hipaa-controls-mapping.md) — §164.312 control walk
  - [`docs/compliance/eu-ai-act.md`](../../docs/compliance/eu-ai-act.md) — Article 13 transparency binding
  - [`docs/compliance/21-cfr-part-11.md`](../../docs/compliance/21-cfr-part-11.md) — when SaMD-classified
- Adjacent bundles:
  - [`tool-using-agent/`](../tool-using-agent/) — Pattern 2; reasoning loops that invoke external tools
  - [`long-running-agent/`](../long-running-agent/) — Pattern 8; reasoning chains that exceed 60min ceiling

## What this bundle does NOT cover

- **Tool-using agents** (calling MCP tools / structured APIs from inside the loop) — see [`tool-using-agent/`](../tool-using-agent/).
- **Multi-session reasoning** (>60min total runtime via memory artifact + parent_cdp_id chain) — see [`long-running-agent/`](../long-running-agent/).
- **Production knowledge-base bodies** — KB retrieval ships as stub when `KNOWLEDGE_BASE_URL` is unset; production fills with customer's licensed deployment.
- **Production-grade prompt templates** — the three shipped templates are illustrative; customer's clinical informaticist + regulatory counsel author production templates.
- **SaMD classification** — Nanorix attests to data-handling boundary; SaMD class for the clinical workflow is customer + regulatory counsel.
- **Single-shot extraction** — see [`med-ai-extraction/`](../med-ai-extraction/) (Pattern 3 — document-AI pipeline).
