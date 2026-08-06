# Pharma R&D screening showcase

> **Pattern 6 R&D × Pharma R&D cohort.** Molecule screening pipeline — 1000-molecule batch → ADMET prediction → per-molecule receipts → results for SAR analysis.

## Cohort context

Named cohort customers — Recursion (phenomics + ML), Insitro (ML-driven drug discovery), Atomwise (structure-based drug design), Owkin (federated learning for pharma) — operate compute-intensive screening workloads where:

- A single batch tests thousands of molecules against ADMET (absorption / distribution / metabolism / excretion / toxicity) predictors.
- Each molecule's prediction is independently auditable — for IP-trace ("which molecules did this discovery sweep evaluate?") and federated-learning attestation.
- Per-molecule audit grain lets multi-party collaborations (federated drug discovery) attest individual contributions without revealing the molecule library.
- HIPAA does not apply (no patient data), but the customer's compliance posture includes FDA SaMD (for downstream therapeutic-AI workflows) + IP-traceability for patent prep.

## What this showcase demonstrates

1. **Ingest** a synthetic 1000-molecule screening library (SMILES strings; no real proprietary structures).
2. **Run** the ADMET prediction pipeline inside the capsule — per-molecule prediction batched for GPU efficiency.
3. **Emit** a per-molecule receipt with `pattern_tag = "molecule_admet_screening"`, binding the molecule's SMILES hash + prediction output hash.
4. **Aggregate** the per-molecule receipts into the AuditProof's Step 8 Merkle root.
5. **Return** SAR-ready results to the calling system (typically a chemoinformatics database or active-learning loop).

## Federated-learning composition

For multi-party federated drug discovery (Pattern 9), each participating organization runs their slice inside their own Nanorix install + signs under their HSM-rooted authority (BYO-HSM). The coordinator aggregates the per-org AuditProofs into a multi-party trust chain — each organization can verify the others' contributions without ever seeing each other's molecule libraries.

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | End-to-end screening pipeline |
| `requirements.txt` | Python deps (rdkit + sklearn + nanorix) |
| `capsulefile.yaml` | ADMET-screening Capsulefile |
| `input/sample_library_synthetic.smi` | Synthetic 1000-molecule SMILES library |
| `expected_audit_proof.json` | Anchor |
| `LICENSE` | PROPRIETARY |

## Reproduction recipe

```bash
export NANORIX_API_KEY="nrx_live_..."
pip install -r requirements.txt
nanorix build -f capsulefile.yaml
python pipeline.py --library input/sample_library_synthetic.smi --output audit_proof.json
nanorix verify audit_proof.json
```

## Discipline

- **Customer-sole-declarer:** `data_classification = "general"` (no PHI / PII in molecule libraries); jurisdiction varies per org.
- **Receipt narrowness:** per-molecule receipts carry signed primitives only. Mapping to canonical IP-traceability control IDs happens at adapter time via `nanorix_control_map`.
- **Federated composition:** each org signs under their HSM; coordinator chains via parent_proof_hashes; no organization sees another's library.

## License

Proprietary — Nanorix Inc.
