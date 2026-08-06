# high-volume-per-record

**Production-pattern reference** for high-volume per-record claims processing inside Nanorix capsules. Demonstrates the one-capsule-per-record pattern: X12 EDI 837 claim → parse + validate → clearinghouse round-trip → adjudication decision (paid/denied/pended) → optional FHIR-shaped supplementary output. Each claim produces one AuditProof; the per-AuditProof economics + sealed isolation discipline are load-bearing at this volume.

> **Important: this is a *pattern reference*, not a turnkey claims-adjudication engine.** The X12 parser, clearinghouse adapter, and adjudication decision tree ship as illustrative stubs. Production deployments substitute customer's pyx12 / bots-edi / Edifecs / vendor parser, customer's licensed clearinghouse (Change Healthcare / Optum / Availity / Waystar / Trizetto), and customer's full benefit-summary + criteria + fee-schedule decision engine. Per `feedback_substrate_reframe_pattern.md`, Nanorix is the attestation substrate; the adjudication product is the customer's.

## Industry signal

Pattern 4 in the [9-pattern atlas](../../docs-site/docs/recipes/) — 25 companies tracked at the substrate-fit horizon. Production high-volume per-record workflows in this space:

- **HealthEdge** (HealthRules) — payer-side claims-processing platform; one transaction per workflow
- **Olive AI** (revenue-cycle automation) — high-volume per-claim processing for providers
- **Edifecs** (clearinghouse adapters) — X12 transformation + routing at payer/clearinghouse scale
- **Availity / Change Healthcare / Optum / Waystar / Trizetto** — clearinghouse engines themselves
- **Cohere Health / Anthem / Elevance / Evolent** — payer-side prior-auth + claims workflows
- **Plutus Health / SmartLight Health / Apero Health** — modern payer-tech with per-record AuditProof appetite

These workflows uniformly require: (a) per-claim cryptographic non-repudiation (state-DOI examination, ERISA appeals, payment-dispute resolution), (b) one-capsule-per-record economics that scale linearly with claim volume, (c) sealed isolation at single-claim grain (HIPAA scope minimization), (d) customer-controlled adjudication logic (no Nanorix-managed decision rules — per ADR-013 customer-sole-declarer).

## Pipeline shape

```
X12 EDI 837 claim envelope (PHI: member_id, NPI, dx + procedure codes)
  │
  ▼
[Parse + validate] (pyx12 / bots-edi / vendor parser)
  │   - segment walk: ISA / GS / ST / BHT / NM1 / CLM / SBR / HI / SV1
  │   - extract load-bearing fields: member, payer, claim ID, dx codes,
  │     service lines, place of service
  │   - raw envelope SHA-512 → hashed into AuditProof
  ▼
[Clearinghouse round-trip] (customer's licensed clearinghouse)
  │   - HTTPS POST X12 envelope (or vendor JSON)
  │   - eligibility verification (X12 270/271)
  │   - pre-auth check (per CMS-0057-F when applicable)
  │   - real-time adjudication (where supported)
  │   - request + response SHA-512 → hashed into AuditProof
  ▼
[Adjudication decision logic] (customer's decision engine)
  │   - eligibility → coverage check
  │   - pre-auth requirement check
  │   - benefit-summary application
  │   - medical-necessity criteria (when applicable)
  │   - pricing application (fee schedule / DRG / capitation)
  │   - CARC/RARC code assignment
  ▼
Decision: paid | denied | pended | rejected
  + paid amount, member responsibility, reason code, reason text
  + clearinghouse response hash bound into output
  ▼
AuditProof: per-claim non-repudiation (signed under customer's
  authority when BYO-HSM is enabled per ADR-031)
```

## Why one-capsule-per-record

Three reasons the per-record (vs batch) pattern dominates production claims processing:

1. **Audit grain matches dispute grain.** When a member appeals a denial, the question is "what evidence supports the denial of THIS claim." The AuditProof for that claim is the answer — bytes-level binding from claim envelope through clearinghouse response through decision rationale. Batch AuditProofs across 10,000 claims aren't dispute-grain.

2. **HIPAA scope minimization.** Each capsule sees one member's PHI only. A batch capsule processing 10,000 claims sees 10,000 members' PHI. Per-record capsules localize HIPAA scope (and cross-member data-leakage risk via processing bugs / memory-reuse / etc) to one record per capsule lifetime.

3. **Linear-scaling economics.** Modern claims volumes are 10s of millions per month per mid-large payer. The per-AuditProof economics need to scale linearly. Tier-aware pricing absorbs this — see [Nanorix pricing](https://nanorix.io) for the tier appropriate to your claim volume. Per-record AuditProof emission is the substrate's discipline; the customer's clearinghouse + decision engine drive the actual processing time.

## Per-AuditProof economics

High-volume per-record workloads are the Nanorix economics' load-bearing case. Each capsule:

- Targets ~30-90 second runtime per claim (clearinghouse round-trip dominates)
- Targets ~30-50MB memory footprint (claim envelope + response + decision in memory)
- Emits one AuditProof at destroy

The Capsulefile sets `default_ttl: 300` (5 min absorbs network jitter + clearinghouse retries) and `memory_mb: 512` to right-size the substrate footprint. Customer tunes per workflow — bulk batch mode (multiple claims in one capsule via a `requests.jsonl` envelope) extends `default_ttl` to 30min and trades audit grain for processing throughput. Audit grain is workflow-determined; the substrate's per-capsule sealed isolation is identical regardless.

## Reference adjudication decision tree

The bundle ships a minimal reference decision tree:

| Step | Check | Outcome on fail |
|------|-------|-----------------|
| 1 | Clearinghouse error (HTTP / TLS / parse) | `rejected` (CO-16) |
| 2 | Eligibility status (271 response) | `denied` (CO-31 patient not insured) |
| 3 | Pre-auth requirement + auth_id | `pended` (CO-197 missing preauth) |
| 4 | Auto-adjudication enabled | `pended` (manual review when disabled) |
| 5 | Clearinghouse adjudication outcome | `paid` / `denied` / `pended` per response |

Production deployments substitute the customer's full decision engine: HealthRules (HealthEdge), customer-curated rules in their preferred engine (Drools / RulesPower / IBM ODM / vendor-specific), or LLM-assisted decision support (when SaMD-classification + regulatory-counsel review supports it).

## CMS-0057-F (Prior Authorization API) era

The bundle's pattern composes naturally with the CMS-0057-F Prior Authorization API rule (effective Jan 2026): prior-auth requests follow the X12 278/275 transaction set; FHIR R4 PAS profile is the modern wire format. See [`claims-processing/`](../claims-processing/) for the prior-auth-specific bundle (CMS-0057-F era PAS profile + InterQual/MCG criteria binding); the present bundle covers the broader 837 claim adjudication pipeline that the prior-auth determination feeds into downstream.

## Why a Nanorix capsule

Claims processing operates on PHI under HIPAA + state-specific privacy laws. Without sealed isolation, the calling host (your adjudication engine, your batch-job runner, your provider portal backend) becomes part of HIPAA scope and inherits cross-claim data-leakage risk. With Nanorix:

- The claim envelope (with member ID, NPI, dx codes, charge amounts) lives in volatile memory only, destroyed with the capsule.
- The clearinghouse HTTPS call flows through sealed egress: TLS pin verification + per-request hash + duration recorded.
- The clearinghouse response (eligibility, preauth status, adjudication outcome) is hashed into AuditProof — not retained.
- The adjudication decision (paid amount, denied reason, pended-with-reason) is bound into the AuditProof; signed under customer's authority when BYO-HSM is enabled.
- AuditProof is signed Ed25519; customer's verifier reconstructs each claim's processing path from envelope to decision.

## What the AuditProof records

Activity-trail events for one X12 claim processed:

1. **`FileUploaded`** — claim envelope. Carries `filename` + `size_bytes` + SHA-512 (bound to `raw_envelope_hash` in output).
2. **`SealedHttpRequest`** (clearinghouse round-trip) — `host: clearinghouse.example.com`, `body_hash` (SHA-512 of X12 envelope or vendor JSON), `response_body_hash` (SHA-512 of clearinghouse response), TLS metadata.
3. **`ClaimAdjudicated`** — `claim_id`, `decision`, `decision_reason_code`, `paid_amount`, `member_responsibility`, `decision_hash` (SHA-512 of canonical decision JSON). Per-decision binding into AuditProof's hash chain.
4. **`ExecutionCompleted`** — exit + duration.
5. **`FileDownloaded`** — `adjudication.json` output bytes hashed.

When customer enables BYO-HSM signing (per `signing_config` block in capsulefile — STRONGLY RECOMMENDED for high-volume per-record workloads), each `ClaimAdjudicated` event also produces a `per_event_attestations` entry signed by the customer's HSM. Result: per-claim non-repudiation under the customer's signing authority. Load-bearing for ERISA appeals, state-DOI market-conduct examinations, member-appeal scenarios, and payment-dispute resolution.

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HIPAAContext, HITRUSTContext, NAICContext

client = Client()
proof = client.proofs.get(capsule_id)

HIPAAContext().bind(proof)         # related_to §164.312(a-e)
HITRUSTContext().bind(proof)       # HITRUST CSF v11 mappings
NAICContext().bind(proof)          # NAIC market-conduct examination support
```

The `regulatory_context` block enumerates provisions this AuditProof's activity trail is `related_to`:

- **HIPAA Security Rule §164.312(a-e)** (framework_version: 2026-02) — access control, audit controls, integrity, transmission security
- **HIPAA Privacy Rule §164.502** (framework_version: 2026-02) — minimum-necessary disclosure; one-capsule-per-record discipline narrows disclosure scope
- **CMS-0057-F Prior Authorization API** (framework_version: 2026-02) — when the workflow is PAS-bound; X12 278 / FHIR R4 PAS profile
- **HITRUST CSF v11** (framework_version: 2026-02) — HITRUST control mappings for ePHI-bearing workflows
- **NAIC Model Audit Rule** (framework_version: 2026-02) — payer-side market-conduct + financial-examination support
- **State DOI claims-handling regulations** (framework_version: 2026-02) — state-by-state; customer's compliance counsel maps per state

Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination.

> **Disclaimer:** Nanorix is an evidence vendor (role 1 of three-role liability split per ADR-017). Customer remains responsible for their organizational GRC program. The X12 parser, clearinghouse adapter, decision tree, and CARC/RARC mappings are illustrative reference implementations — production composes customer's licensed parser + clearinghouse + benefit-summary + criteria engine + fee schedule + state-by-state regulatory overlays.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# Drop a representative X12 837P claim envelope into input/
# (synthetic / test-data only — no real PHI)
# X12 envelopes commonly use .edi extension.
cp /path/to/synthetic-claim.edi input/

# Create capsule + execute (one capsule per claim)
CAPSULE=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification PHI)
nanorix capsules upload $CAPSULE -f input/synthetic-claim.edi
CLEARINGHOUSE_URL=https://clearinghouse.payer-internal.example.com/v1/submit \
  nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f adjudication.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

For high-volume processing, customer's job orchestrator drives the capsule-per-claim cycle:

```python
# Pseudo-code for high-volume orchestration
from nanorix import Client

client = Client()
for claim_envelope_path in claim_queue:  # 10K-1M claims/day
    capsule = client.capsules.create(
        capsulefile_id="cf_<uuid>",
        jurisdiction="US",
        data_classification="PHI",
        metadata={
            "claim_id": extract_claim_id(claim_envelope_path),
            "submission_batch_id": batch_id,
        },
    )
    capsule.upload(open(claim_envelope_path, "rb"))
    capsule.run("python main.py")
    decision = capsule.download("adjudication.json")
    proof = capsule.destroy()
    # decision + proof.id stored in customer's case-management system
    archive_for_audit(decision, proof.id)
```

Customer-side concurrency, retry, and queue management are workflow-specific — the substrate's per-capsule discipline is identical regardless.

## Cross-links

- CLI reference: [`docs-site/docs/cli-reference.md`](../../docs-site/docs/cli-reference.md) — `nanorix build` / `capsules` / `proofs` / `verify`
- Compliance frameworks:
  - [`docs/compliance/hipaa-controls-mapping.md`](../../docs/compliance/hipaa-controls-mapping.md) — §164.312 + §164.502 mappings
  - [`docs/compliance/hitrust-csf-v11.md`](../../docs/compliance/hitrust-csf-v11.md) — HITRUST CSF v11 control walk
- Adjacent bundles:
  - [`claims-processing/`](../claims-processing/) — Pattern 5; CMS-0057-F prior-auth specific (X12 278 + FHIR R4 PAS) with InterQual/MCG criteria binding
  - [`med-ai-extraction/`](../med-ai-extraction/) — Pattern 3; document AI for the supporting clinical documentation that often accompanies 837 envelopes

## What this bundle does NOT cover

- **Prior authorization adjudication** (CMS-0057-F era PAS profile, InterQual/MCG criteria binding) — see [`claims-processing/`](../claims-processing/).
- **Production X12 parser bodies** — pyx12 / bots-edi / vendor parsers ship as commented-out package declarations; minimal segment-walk parser is illustrative.
- **Production clearinghouse bodies** — clearinghouse round-trip ships as stub when `CLEARINGHOUSE_URL` is unset; production fills with customer's licensed deployment.
- **Production decision engine** — minimal 5-step decision tree is illustrative; customer fills with HealthRules / customer-curated rules engine / customer's full adjudication ruleset.
- **Production fee-schedule pricing application** — paid-amount calculation in stub mode is illustrative (80% of charge); production composes customer's fee schedule + DRG + capitation logic.
- **State-DOI regulatory overlays** — claim-handling regulations vary state-by-state; customer's compliance counsel authors per-state mappings.
- **FHIR R4 supplementary mapping** — when downstream consumer is FHIR-native; package import (`fhir.resources`) is wired via commented-out package declaration; customer fills the mapping.
