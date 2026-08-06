# Customer cohort showcase repos

> Five end-to-end runnable examples, one per pattern × named cohort. Each demonstrates the **full customer flow** — ingest data, run capsule, get AuditProof, file evidence — for a specific industry signal. Reduces first-touch evaluation friction across the named cohort customers.

Each showcase is self-contained — clone the repository, open one showcase directory, and it runs on its own without the others.

## Showcase catalog

| Showcase | Pattern | Named cohort | Workload |
|---|---|---|---|
| [`pa-decision-pipeline/`](pa-decision-pipeline/) | Pattern 1 (LLM advisory) | Anterior cohort (Anterior, Cohere Health, Notable) | Prior-auth decision pipeline: ingest 278 PA request → InterQual/MCG criteria → LLM advisory → FHIR R4 PAS response → Vanta evidence filing |
| [`rcm-claims-batch/`](rcm-claims-batch/) | Pattern 4 (high-volume per-record) | RCM cohort (Banjo Health, Adonis, Candid Health) | X12 EDI 837 claims batch: 100-claim batch → bulk capsule create → per-claim receipts → adjudication → 835 response |
| [`dicom-imaging-attestation/`](dicom-imaging-attestation/) | Pattern 6 (imaging AI) | Imaging cohort (Overjet, Cleerly, PathAI) | DICOM imaging attestation: DICOM C-STORE → capsule → vision model → finding output → AuditProof for chart-of-custody |
| [`agent-multi-step/`](agent-multi-step/) | Pattern 2/8 (multi-step agent) | Healthcare AI cohort (Hippocratic AI, Sully, Tennr) | LangGraph multi-step agent: knowledge-base retrieval → reasoning steps → tool use → per-step receipts → chain-walk verification |
| [`pharma-rd-screening/`](pharma-rd-screening/) | Pattern 6 R&D | Pharma R&D cohort (Recursion, Insitro, Atomwise) | Molecule screening pipeline: 1000-molecule batch → ADMET prediction → per-molecule receipts → results for SAR analysis |

## Structure of each showcase

```
<showcase>/
├── README.md                  # What + why + how-to-run + reproduction recipe
├── pipeline.py OR pipeline.ts # Runnable end-to-end script
├── requirements.txt OR package.json
├── capsulefile.yaml           # Sample Capsulefile for the workload
├── expected_audit_proof.json  # Anchor for verification + comparison
└── LICENSE                    # PROPRIETARY (Nanorix Inc.)
```

## How to run any showcase

```bash
# 1. Set NANORIX_API_KEY env var.
export NANORIX_API_KEY="nrx_live_..."

# 2. cd into the showcase + install deps.
cd pa-decision-pipeline
pip install -r requirements.txt   # or: npm install

# 3. Run the pipeline.
python pipeline.py                # or: npx ts-node pipeline.ts

# 4. Verify the AuditProof against the expected anchor.
nanorix-verify audit_proof.json
diff <(jq -S 'del(.terminated_at, .capsule_id)' audit_proof.json) \
     <(jq -S 'del(.terminated_at, .capsule_id)' expected_audit_proof.json)
```

## Discipline anchors

- **Customer cold-start path**: every showcase enumerates `NANORIX_API_KEY` as the only prerequisite. SDK install + Capsulefile yaml + sample data ship in-repo.
- **Vocabulary**: README text + script docstrings use `applies_to` / `references` / `related_to` — never `COMPLIANT` / `SATISFIED` / `PASSED` / `MEETS`.
- **IP-protection**: showcases describe what Nanorix does (capsule + AuditProof + Merkle-rooted receipts) — never internal engine names.
- **No mock PHI / real customer data**: sample inputs are synthetic and clearly labeled.

## License

Proprietary — see Nanorix repository root LICENSE. Showcase code is made available for customer evaluation + reproduction; redistribution to non-customers is not granted.
