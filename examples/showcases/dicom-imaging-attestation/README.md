# DICOM imaging attestation showcase

> **Pattern 6 (imaging AI) × Imaging cohort.** DICOM C-STORE ingest → vision model inside capsule → finding output → AuditProof for chain-of-custody.

## Cohort context

Named cohort customers — Overjet (dental AI), Cleerly (cardiac imaging), PathAI (pathology), Aidoc (radiology) — operate clinical imaging AI workloads where:

- Inputs are DICOM studies (potentially multi-GB).
- The model output is a finding / risk score / annotation that influences downstream clinical decisions.
- FDA SaMD class workflows require chain-of-custody from study ingestion → model inference → output emission.
- The AuditProof at study-grain provides the chain-of-custody record without retaining PHI bytes.

## What this showcase demonstrates

1. **Receive** a DICOM C-STORE notification (mock — DICOMweb integration is a Wave-N extension not in V1).
2. **Pull** the DICOM study into the capsule (synthetic; no real imaging data).
3. **Run** the vision model inside the sealed capsule (model artifacts inside Capsulefile; runtime egress allow-listed for model storage if needed).
4. **Emit** a finding output + per-study receipt with `pattern_tag = "imaging_inference"`.
5. **Destroy** the capsule + collect the AuditProof binding the study-DICOM hash + finding-output hash.
6. **Return** the finding to the calling system (PACS, RIS, EHR) referencing the AuditProof for chain-of-custody.

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | End-to-end script |
| `requirements.txt` | Python deps (pydicom for DICOM handling; mock implementation here) |
| `capsulefile.yaml` | DICOM-attestation Capsulefile |
| `input/synthetic_study.json` | Synthetic study manifest (no real imaging) |
| `expected_audit_proof.json` | Anchor for diff-comparison |
| `LICENSE` | PROPRIETARY |

## Customer reproduction recipe

```bash
export NANORIX_API_KEY="nrx_live_..."
pip install -r requirements.txt
nanorix build -f capsulefile.yaml
python pipeline.py --study input/synthetic_study.json --output audit_proof.json
nanorix verify audit_proof.json
```

## Why this matters

The chain-of-custody record is the receipt that an auditor traces back to. Imaging AI vendors who own the inference path but not the audit primitive struggle with FDA SaMD audit prep — the AuditProof provides the cryptographically signed boundary attestation that "this specific study was processed by this specific Capsulefile at this specific time with these specific input/output hashes." The mapping to canonical FDA SaMD control IDs happens at adapter time via `nanorix_control_map`.

## Future Wave-N extension

DICOMweb (DICOM-over-HTTPS) ingestion is a tracked Wave-N extension. Today's showcase mocks the C-STORE notification + study pull; production deployment composes a DICOMweb proxy that ingests study bytes directly into the capsule's input directory.

## Discipline

- **Customer-sole-declarer:** `data_classification = "PHI"` rides into metadata.
- **No verdict language:** the AuditProof references chain-of-custody; the FDA audit adjudicates whether the chain is acceptable.
- **Receipt narrowness:** the receipt binds DICOM-study hash + finding-output hash. Customer's adapter resolves to FDA SaMD control IDs at adapter time.

## License

Proprietary — Nanorix Inc.
