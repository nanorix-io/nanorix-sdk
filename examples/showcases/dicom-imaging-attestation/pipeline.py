"""
DICOM imaging attestation showcase — Pattern 6 × Imaging cohort.

Mocks the DICOMweb / C-STORE ingestion path (full DICOMweb integration
is a Wave-N extension not in V1). Demonstrates the AuditProof shape
for an imaging-inference workload.

Per `feedback_narrow_signed_claim_auditor_certifies.md`: no verdict
vocabulary. Per `feedback_ip_protection.md`: external-brand language only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import nanorix


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DICOM imaging attestation — Imaging cohort showcase",
    )
    parser.add_argument(
        "--study",
        type=Path,
        default=Path(__file__).parent / "input" / "synthetic_study.json",
        help="Synthetic study manifest.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "audit_proof.json",
        help="Path to write AuditProof.",
    )
    parser.add_argument(
        "--capsulefile-id",
        type=str,
        default=os.environ.get("CAPSULEFILE_ID"),
    )
    return parser.parse_args()


def sha512_hex(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("NANORIX_API_KEY")
    if not api_key:
        sys.exit("NANORIX_API_KEY not set.")
    if not args.capsulefile_id:
        sys.exit("CAPSULEFILE_ID not set. Run `nanorix build` first.")

    client = nanorix.Client(api_key=api_key)
    study_manifest = json.loads(args.study.read_text())
    study_id = study_manifest["study_instance_uid"]

    # Per ADR-013 customer-sole-declarer: PHI classification +
    # jurisdiction are customer-declared.
    capsule = client.capsules.create(
        capsulefile=args.capsulefile_id,
        jurisdiction="US",
        data_classification="PHI",
        metadata={
            "pattern": "imaging_inference",
            "study_instance_uid": study_id,
            "modality": study_manifest.get("modality"),
        },
    )
    print(f"Capsule created for study {study_id}: {capsule.id}")

    # Upload the synthetic study bytes. In production, this is the
    # DICOMweb proxy pulling C-STORE'd study into the capsule's
    # input directory; for the showcase we upload the manifest.
    study_bytes = json.dumps(study_manifest).encode("utf-8")
    client.capsules.upload(
        capsule_id=capsule.id,
        filename="study.json",
        content=study_bytes,
    )
    study_hash = sha512_hex(study_bytes)
    print(f"Study uploaded. SHA-512: {study_hash[:32]}...")

    # Execute the vision-model entrypoint inside the capsule.
    result = client.capsules.exec(
        capsule_id=capsule.id,
        command="python /capsule/infer.py",
    )
    print(f"Inference finished. Exit: {result.exit_code}")

    # Pull the finding output + compute its hash for the receipt.
    output_files = client.capsules.list_output_files(capsule.id)
    finding_hash = None
    if output_files:
        finding_content = client.capsules.download_output(
            capsule_id=capsule.id,
            filename=output_files[0].filename,
        )
        finding_hash = sha512_hex(finding_content)

    # Emit per-study receipt — chain-of-custody primitive.
    client.capsules.emit_record(
        capsule_id=capsule.id,
        record_id=study_id,
        pattern_tag="imaging_inference",
        input_hash=study_hash,
        output_hash=finding_hash,
    )
    print("Per-study chain-of-custody receipt emitted.")

    audit_proof = client.capsules.destroy(capsule.id)
    args.output.write_text(json.dumps(audit_proof, indent=2))
    print(f"AuditProof written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
