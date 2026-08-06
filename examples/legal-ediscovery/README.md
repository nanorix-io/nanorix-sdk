# legal-ediscovery

**TAR 2.0 / Continuous Active Learning reference** for court-defensible eDiscovery review inside a Nanorix capsule. Demonstrates the iteration loop production review platforms (Relativity aiR, Disco, Everlaw predictive coding, Reveal) operate: seed-set ingestion → classifier train → corpus scoring → uncertainty-band identification for human review → reviewer feedback → retrain → iterate to coverage threshold.

> Reframed from "privilege-triage reference" (one-shot batch classification) to "TAR 2.0 reference" (CAL iteration loop). One-shot batch is for triage; **court-defensible review (FRCP 26(g) good-faith defensibility, FRE 502 clawback risk reduction) requires CAL with documented seed-set, iteration history, and uncertainty-band coverage.**

## Industry signal

Companies running adjacent eDiscovery / TAR 2.0 / CAL patterns: **Relativity** (RelativityOne aiR — predictive coding + assisted review), **Disco** (review workflow + Cecilia AI), **Everlaw** (predictive coding + storybuilder), **Reveal** (acquired Logikcull 2022; AI review), **Brainspace** (now Reveal — concept search + analytics), and BigLaw inhouse AI teams running review-supplementation models.

## TAR 2.0 / CAL loop

```
iteration 0: SEED-SET INGESTION
  ┌────────────────────────────────────────┐
  │ humans label initial 200-2000 documents │
  │ with privilege_status + responsiveness  │
  └─────────────────┬──────────────────────┘
                    ▼
iteration 1+: CAL LOOP
  ┌────────────────────────────────────┐
  │ load CAL state from memory artifact│
  │ (parent_cdp_id chain → iteration N) │
  └─────────────────┬──────────────────┘
                    ▼
  ┌──────────────────────────────────────────┐
  │ train classifier on accumulated labels    │
  │ (TF-IDF + LogisticRegression placeholder; │
  │  production: legal-BERT / ContractBERT)   │
  └─────────────────┬────────────────────────┘
                    ▼
  ┌──────────────────────────────────────────┐
  │ score remaining unlabeled corpus          │
  │ → predicted_label + max_class_probability │
  └─────────────────┬────────────────────────┘
                    ▼
  ┌──────────────────────────────────────────┐
  │ uncertainty band: top UNCERTAINTY_BAND_PCT│
  │ by 1 - max_class_probability              │
  │ → emit as "next batch for human review"   │
  └─────────────────┬────────────────────────┘
                    ▼
  ┌──────────────────────────────────────────┐
  │ persist state to memory artifact (JSON)   │
  │ (classifier coeffs + iteration count + history) │
  └─────────────────┬────────────────────────┘
                    ▼
  HUMAN REVIEW of uncertainty band
  → labels added → next iteration's new_labels.jsonl
                    ▼
  iterate until coverage_recall_estimate >= COVERAGE_THRESHOLD_RECALL
```

## Per-iteration inputs

```
/data/input/uploads/
  ├── seed_set.jsonl       # iteration 0 only — initial human labels
  ├── new_labels.jsonl     # iteration N>0 — reviewer feedback from prior iter
  └── corpus.jsonl         # remaining unlabeled corpus
```

`seed_set.jsonl` line shape:

```json
{"custom_id":"matter-001-bates-0001","text":"...","matter_id":"matter-001","bates":"BATES-0001","label":"attorney_client"}
```

`new_labels.jsonl` is the same shape but represents reviewer-applied labels for documents that were in the prior iteration's uncertainty band.

`corpus.jsonl` line shape (unlabeled):

```json
{"custom_id":"matter-001-bates-0823","text":"...","matter_id":"matter-001","bates":"BATES-0823"}
```

## Per-iteration outputs

```
/data/output/
  ├── scored.jsonl              # per-document scoring this iteration
  └── iteration_summary.json    # iteration-level summary + history
```

`scored.jsonl` line shape — court-defensibility record per document:

```json
{
  "custom_id": "matter-001-bates-0823",
  "matter_id": "matter-001",
  "bates": "BATES-0823",
  "iteration_id": 3,
  "classifier_version": "sha256:8a2f...",
  "predicted_label": "non_responsive",
  "confidence": 0.92,
  "uncertainty": 0.08,
  "decision_route": "auto_classified"
}
```

`decision_route` is the load-bearing field for defensibility — every document has one of:
- `seed_set_label` — human-applied at iteration 0 (highest evidentiary weight)
- `auto_classified` — machine-classified (above confidence threshold)
- `uncertainty_band_for_review` — flagged for human review next iteration

## Memory artifact composition

CAL state crosses capsule boundaries via the memory artifact pattern. Each iteration's capsule:

1. Loads prior state from `/data/memory/cal_state.json` (decrypted in-capsule via customer's KMS roundtrip).
2. Runs one CAL iteration.
3. Persists updated state to `/data/memory/cal_state.json`.

The next iteration's capsule references this destruction's AuditProof via `parent_cdp_id`. The chain proves "iteration N's classifier derives from iteration N-1's state" cryptographically.

State carried across iterations:
- `iteration_id`
- `classifier_version` (SHA-256 of the JSON-serialized classifier)
- `classifier` (explicit JSON form: TF-IDF vocabulary + IDF weights + LR coefficients + intercepts + class labels)
- `accumulated_label_count`
- `label_distribution`
- `coverage_recall_estimate`
- `iteration_history` (full audit trail of prior iterations)

The classifier is serialized as **explicit JSON, not pickled**. Memory artifacts cross capsule boundaries and arrive as untrusted-content into the next capsule's volatile memory; pickle on untrusted content is unsafe (arbitrary code execution on `pickle.load`). Production deployments using transformer classifiers serialize via safetensors or external storage refs (e.g. pointer to a customer-KMS-encrypted blob in their object store). The principle (no pickle of customer-derived blobs) holds across substitutions.

## What the AuditProof records

For each iteration's capsule:

- **`FileUploaded`** — one per input JSONL (seed_set / new_labels / corpus). Carries SHA-512 of bytes.
- **`MemoryArtifactLoaded`** — when iteration > 0. Carries `parent_cdp_id`, `key_id`, encrypted blob hash (the prior CAL state).
- **`ExecutionStarted`** + **`ExecutionCompleted`** — capsule lifecycle bounds.
- **`BatchRecordProcessed`** + **`BatchCompleted`** — per-iteration batch variants. The `BatchCompleted` event's payload carries the Merkle root over per-record outcomes — verifier can prove inclusion of any specific `custom_id` in O(log N) without re-reading the entire corpus.
- **`MemoryArtifactPersisted`** — at capsule destroy. Carries the new memory artifact's encrypted blob hash for chaining to the next iteration.
- **`FileDownloaded`** — `scored.jsonl` + `iteration_summary.json` outputs.

For the cross-iteration chain as a whole, verifier replay walks the `parent_cdp_id` chain: iteration N's AuditProof carries the hash of iteration N-1's destroyed memory artifact, and so on back to the genesis (iteration 0, where `parent_cdp_id == null`). Court-defensibility documentation can then prove the full CAL loop was executed cryptographically — not just claimed in a proceedings affidavit.

## The Merkle root binding

The `BatchCompleted` event carries:

```json
{
  "event": "batch_completed",
  "total_records": 1000,
  "merkle_root": "sha512:<128 hex>",
  "duration_ms": 1200000,
  "at": "2026-..."
}
```

The Merkle root is computed over per-record `ScoredDocument.model_dump_json()` outputs (canonical-JSON-serialized, leaves are SHA-512 of each canonical-JSON, internal nodes are SHA-512 concatenation per RFC 6962). To prove "document with `custom_id` X was classified as Y in iteration N," the verifier provides the inclusion path (log-N hashes) — opposing counsel's verifier confirms inclusion without re-running the classifier.

## Production substitution: classifier choice

The bundle ships with TF-IDF + LogisticRegression as the placeholder classifier. Production substitution paths:

| Target | Tool | Why |
|--------|------|-----|
| Best baseline at low cost | TF-IDF + LogisticRegression (default) | No GPU, no LLM, ~5-50KB classifier |
| Stronger semantic signal | sentence-transformers + LogisticRegression | Better recall on conceptual matches; ~400MB model |
| Best in-class | legal-BERT (Casehold) / ContractBERT fine-tune | Domain knowledge baked in; requires labeled corpus + GPU |
| LLM-zero-shot fallback for novel matters | Anthropic Claude with classification prompt | No training; per-doc cost; not iteration-stable across model versions |

Substitution interface: `train_classifier(labeled_docs) → (in_memory_classifier, json_serializable_form, version_hash)` and `score_corpus(classifier, unlabeled) → list[(doc, label, max_prob)]`. Iteration loop logic, AuditProof binding, and state persistence are classifier-agnostic.

## Why a Nanorix capsule

Legal document review on attorney-client privileged material is the highest-stakes confidentiality scenario in computing. A leak can void privilege for the entire matter and trigger malpractice. With Nanorix:

- The corpus + classifier weights live in volatile memory only — destroyed with the capsule.
- The CAL state crosses iterations only via customer-KMS-encrypted memory artifacts; Nanorix never sees plaintext state.
- LLM calls (when production substitutes legal-BERT / Claude) are sealed-daemon-routed: each `SealedHttpRequest` carries SHA-512 of request body so verifier can later confirm "this corpus was processed by THIS model on THIS date."
- Per-record `custom_id` (matter-id + bates-number) is preserved through AuditProof activity_trail so privilege logs reconcile to specific document IDs.
- The capsule destroys all corpus bytes after execution; AuditProof attests destruction.

## Court-defensibility narrative

The full court-defensibility record for a TAR 2.0 production assembles:

1. **Seed-set provenance** — iteration 0's seed_set.jsonl + the AuditProof's FileUploaded SHA-512 binding, plus the customer-side reviewer attestation log (who labeled what when).
2. **Iteration history** — chained AuditProofs (parent_cdp_id chain) prove every iteration ran in sealed isolation; iteration_summary.json's iteration_history field summarizes per-iteration metrics.
3. **Final corpus scoring** — the last iteration's scored.jsonl is the production-set basis (auto_classified above threshold + reviewer-applied labels for the rest).
4. **Coverage achievement** — the last iteration's coverage_recall_estimate ≥ COVERAGE_THRESHOLD_RECALL demonstrates good-faith defensibility per FRCP 26(g).
5. **Reproducibility** — opposing counsel's verifier can reconstruct any document's classification history via the (custom_id, iteration_id, classifier_version) tuple and the Merkle inclusion paths.

## Compliance binding

```python
from nanorix.compliance import SOC2Context
SOC2Context().bind(proof)  # CC6 + CC7 — logical access + system operations
```

For matters subject to specific regulatory regimes (HIPAA-covered medical malpractice, GDPR-subject EU data discovery), use the appropriate framework helpers as well.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml

# --- Iteration 0: seed-set ---
echo '{"custom_id":"matter-001-bates-0001","text":"This memo from counsel...","matter_id":"matter-001","bates":"BATES-0001","label":"attorney_client"}' > input/seed_set.jsonl
# (in production: 200-2000 seed-set lines)
echo '{"custom_id":"matter-001-bates-0823","text":"Routine business correspondence...","matter_id":"matter-001","bates":"BATES-0823"}' > input/corpus.jsonl
# (in production: full unlabeled corpus)

CAPSULE_0=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification general --metadata '{"matter_id":"matter-001","cal_iteration":0}')
nanorix capsules upload $CAPSULE_0 -f input/seed_set.jsonl
nanorix capsules upload $CAPSULE_0 -f input/corpus.jsonl
nanorix capsules exec $CAPSULE_0 "python main.py"
nanorix capsules download $CAPSULE_0 -f scored.jsonl
nanorix capsules download $CAPSULE_0 -f iteration_summary.json
nanorix capsules destroy $CAPSULE_0
nanorix proofs get $CAPSULE_0 > auditproof-iter0.json

# --- Reviewer applies labels to uncertainty band of scored.jsonl ---
# (out-of-band human review step; reviewer outputs new_labels.jsonl)

# --- Iteration 1: chain via parent_cdp_id ---
PARENT=$(jq -r .capsule_id auditproof-iter0.json)
CAPSULE_1=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification general --parent-cdp $PARENT --metadata '{"matter_id":"matter-001","cal_iteration":1}')
nanorix capsules upload $CAPSULE_1 -f input/new_labels.jsonl
nanorix capsules upload $CAPSULE_1 -f input/corpus.jsonl
nanorix capsules exec $CAPSULE_1 "python main.py"
nanorix capsules download $CAPSULE_1 -f scored.jsonl
nanorix capsules destroy $CAPSULE_1
nanorix proofs get $CAPSULE_1 > auditproof-iter1.json

# Verify the chain
nanorix verify auditproof-iter0.json
nanorix verify auditproof-iter1.json
nanorix verify --chain auditproof-iter0.json auditproof-iter1.json

# Repeat until iteration_summary.json's coverage_threshold_met == true
```

## What this bundle does NOT cover

- **Production fine-tuned classifier** — TF-IDF + LogisticRegression is the placeholder. Customer substitutes legal-BERT / ContractBERT / matter-corpus-tuned model.
- **Reviewer workflow UI** — human review of uncertainty-band documents is the out-of-band step between iterations. Customer's review platform (Relativity / Disco / Everlaw / custom UI) handles assignment, second-pass review, conflicts.
- **Bates numbering / production-set generation** — `bates` field is preserved through the pipeline; production-set assembly (TIFF/PDF rendering, Bates-stamping, load-file generation in DAT/OPT/IPRO format) is the customer's downstream step.
- **FRCP 26(g) defensibility documentation** — the CAL loop produces the cryptographic record; the legal team produces the affidavit referencing it.
- **Matter-isolation enforcement** — `matter_id` flows through capsule metadata + per-record output. Customer's matter-isolation policy (separate AWS accounts, separate Nanorix customer accounts per matter, etc) is the boundary.
