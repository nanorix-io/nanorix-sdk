# RCM claims batch showcase

> **Pattern 4 (high-volume per-record) × RCM cohort.** X12 EDI 837 claims batch processing — 100-claim batch → bulk capsule create → per-claim receipt → adjudication → 835 response.

## Cohort context

Named cohort customers — Banjo Health, Adonis, Candid Health, Olive AI, Edifecs, Availity, Cohere Health (RCM side) — operate revenue-cycle workloads where:

- A single batch contains hundreds or thousands of claims.
- Each claim has independent input/output state; one claim's adjudication doesn't depend on another's.
- HIPAA scope minimization requires per-claim audit grain (not batch-level).
- Auditor needs to follow disputes claim-by-claim, not in aggregate.

The Pattern 4 primitive — per-record receipts bound into the AuditProof Step 8 amended chain hash via Merkle root — gives the customer **one signed envelope per batch with N independently verifiable per-claim receipts**. Audit grain matches dispute grain.

## What this showcase demonstrates

1. **Ingest** a synthetic 100-claim X12 EDI 837 batch (synthetic; no real claim data).
2. **Create** one capsule via the bulk-create endpoint `/v1/capsules/batch` (hard cap 100).
3. **Emit** a per-claim receipt for each claim's input/output hashes via `POST /v1/capsules/:id/record`.
4. **Adjudicate** each claim (rules engine + parser) inside the sealed boundary.
5. **Destroy** the capsule + collect the AuditProof with the Merkle root binding all per-claim receipts into Step 8.
6. **Return** the X12 EDI 835 response with per-claim adjudication results.

The customer's auditor can follow each claim independently: the Merkle root in Step 8 + the per-claim receipts let any auditor verify a single claim's evidence without touching the other 99.

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | End-to-end runnable script |
| `requirements.txt` | Python deps |
| `capsulefile.yaml` | RCM batch Capsulefile |
| `input/sample_batch_100.jsonl` | Synthetic 100-claim batch (one JSON object per line) |
| `expected_audit_proof.json` | Anchor for diff-comparison |
| `LICENSE` | PROPRIETARY marker |

## Customer reproduction recipe

```bash
export NANORIX_API_KEY="nrx_live_..."
pip install -r requirements.txt
nanorix build -f capsulefile.yaml
python pipeline.py --batch input/sample_batch_100.jsonl --output audit_proof.json
nanorix verify audit_proof.json
```

## Verifying per-claim receipts

```bash
# Extract a single claim's receipt + Merkle inclusion proof.
python -c "
import json
ap = json.load(open('audit_proof.json'))
receipts = ap['record_receipts']
print(json.dumps([r for r in receipts if r['record_id'] == 'CLAIM-001'][0], indent=2))
"

# Verify the receipt's Merkle inclusion proof rolls up to Step 8's Merkle root.
nanorix verify-receipt audit_proof.json --record-id CLAIM-001
```

The Merkle root in Step 8 is the signed anchor; per-claim Merkle inclusion proofs let auditors verify a single claim without exposing the others. Receipt narrowness preserved — the receipt carries `pattern_tag`, `record_id`, hashes; the mapping to canonical control IDs happens at adapter time via `nanorix_control_map`.

## Discipline

- **Receipt narrowness:** receipts carry signed primitives only. No COMPLIANT / SATISFIED / PASSED / MEETS verdicts in the receipt.
- **Three-layer separation:** the receipt's `pattern_tag` is signed; the mapping is applied freshly at adapter time against the customer's pinned `framework_version`.
- **Customer-sole-declarer:** `data_classification = "PHI"` + `jurisdiction = "US"` ride into capsule metadata; Nanorix does not infer either.

## License

Proprietary — Nanorix Inc.
