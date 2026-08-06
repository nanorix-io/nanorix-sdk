# PA decision pipeline showcase

> **Pattern 1 (LLM advisory) × Anterior cohort.** Prior-auth decision pipeline: ingest a 278 PA request → InterQual/MCG criteria binding → LLM advisory → FHIR R4 PAS response → file evidence to Vanta.

## Cohort context

Named cohort customers — Anterior, Cohere Health (post-acquisition), Notable, EvolentHealth — operate prior-auth review workloads where:

- Each PA case has a structured decision chain (criteria checks → guideline lookup → advisory generation).
- The decision must be auditable for CMS-0057-F PAS conformance.
- Health plans receive a FHIR R4 PAS response, with the chain of attestations following.
- A signed receipt of "this specific case ran through this specific Capsulefile at this specific time" satisfies audit-of-decision-chain at the boundary.

## What this showcase demonstrates

1. **Ingest** a sample 278 PA request (synthetic; no real PHI).
2. **Bind** InterQual/MCG criteria references via the Nanorix SDK's `nanorix.compliance` helpers (declarative; doesn't infer satisfaction).
3. **Run** the decision capsule (LLM advisory + criteria binding).
4. **Emit** an AuditProof binding the entire decision chain into a single signed envelope.
5. **File** the AuditProof to Vanta as evidence under the canonical control IDs resolved at adapter time via the `nanorix_control_map` mapping (three-layer separation — receipt carries `pattern_tag`; mapping applies at adapter time).
6. **Return** the FHIR R4 PAS response to the calling plan.

## Files in this showcase

| File | Purpose |
|---|---|
| `pipeline.py` | Runnable end-to-end script |
| `requirements.txt` | Python deps (`nanorix`, `requests`) |
| `capsulefile.yaml` | Capsulefile for the decision-pipeline workload |
| `input/sample_278.json` | Synthetic X12 EDI 278 PA request |
| `expected_audit_proof.json` | Anchor AuditProof for diff-comparison |

## Customer reproduction recipe

```bash
# 1. Set the API key.
export NANORIX_API_KEY="nrx_live_..."

# 2. Install Nanorix Python SDK.
pip install -r requirements.txt

# 3. Build + finalize the Capsulefile.
nanorix build -f capsulefile.yaml

# 4. Run the pipeline.
python pipeline.py --input input/sample_278.json --output audit_proof.json

# 5. Verify offline.
nanorix verify audit_proof.json
```

## Verifying the AuditProof

The AuditProof emitted at end-of-run captures:

- Capsule lifecycle (8-step SHA-512 chain + Ed25519 signature).
- `pattern_tag = "pa_decision"` per-record receipt for the PA case.
- `data_classification = "PHI"` + `jurisdiction = "US"` capsule context.
- Decision-chain steps as `activity_trail` entries.

Verify via any of:

```bash
# Local CLI.
nanorix verify audit_proof.json

# Browser.
# https://verify.nanorix.io → drag-drop audit_proof.json

# 4-impl byte-equivalent verifier (Rust / Go / Python / TypeScript).
# All produce identical Pass/Fail verdict Forever-Standard.
```

## Discipline

- **Customer-sole-declarer:** `jurisdiction` + `data_classification` come from the capsule's metadata. Nanorix does not infer either.
- **No compliance verdicts:** the AuditProof references control IDs at adapter time via the `nanorix_control_map` mapping. References, not assertions. Auditor adjudicates.
- **Receipt narrowness:** the per-PA-case receipt carries the signed primitive (`pattern_tag`, record hashes). Vanta evidence enrichment happens in adapter code, not in the receipt.

## License

Proprietary — Nanorix Inc.
