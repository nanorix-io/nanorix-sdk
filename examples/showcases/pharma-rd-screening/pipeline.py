"""
Pharma R&D screening showcase — Pattern 6 R&D × Pharma R&D cohort.

1000-molecule ADMET screening with per-molecule receipts. Each
molecule's prediction is independently auditable for IP-trace +
federated-learning attestation.

No verdict
language. External-brand vocabulary
only.
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
    p = argparse.ArgumentParser(description="Pharma R&D screening — Recursion/Insitro/Atomwise cohort")
    p.add_argument(
        "--library",
        type=Path,
        default=Path(__file__).parent / "input" / "sample_library_synthetic.smi",
        help="SMILES library file (one SMILES per line).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "audit_proof.json",
    )
    p.add_argument(
        "--capsulefile-id",
        type=str,
        default=os.environ.get("CAPSULEFILE_ID"),
    )
    return p.parse_args()


def sha512_hex(s: str) -> str:
    return hashlib.sha512(s.encode("utf-8")).hexdigest()


def load_library(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"Library file not found: {path}")
    smiles = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            smiles.append(line.split()[0])
    return smiles


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("NANORIX_API_KEY")
    if not api_key:
        sys.exit("NANORIX_API_KEY not set.")
    if not args.capsulefile_id:
        sys.exit("CAPSULEFILE_ID not set. Run `nanorix build` first.")

    client = nanorix.Client(api_key=api_key)
    library = load_library(args.library)
    print(f"Loaded {len(library)} synthetic molecules from {args.library}")

    # Data_classification + jurisdiction customer-declared.
    capsule = client.capsules.create(
        capsulefile=args.capsulefile_id,
        jurisdiction="US",
        data_classification="general",
        metadata={
            "pattern": "molecule_admet_screening",
            "library_size": str(len(library)),
        },
    )
    print(f"Screening capsule created: {capsule.id}")

    # Upload the library.
    library_bytes = "\n".join(library).encode("utf-8")
    client.capsules.upload(
        capsule_id=capsule.id,
        filename="library.smi",
        content=library_bytes,
    )

    # Execute the ADMET prediction inside the capsule.
    client.capsules.exec(
        capsule_id=capsule.id,
        command="python /capsule/screen.py",
    )
    print("Screening pipeline finished.")

    # Emit per-molecule receipts. Each receipt binds the molecule's
    # SMILES hash; downstream IP-traceability adapters use the
    # receipt as the auditable primitive.
    for idx, smiles in enumerate(library):
        client.capsules.emit_record(
            capsule_id=capsule.id,
            record_id=f"molecule-{idx:04d}",
            pattern_tag="molecule_admet_screening",
            input_hash=sha512_hex(smiles),
            output_hash=sha512_hex(f"synthetic-prediction-{idx}"),
        )
    print(f"Emitted {len(library)} per-molecule receipts.")

    audit_proof = client.capsules.destroy(capsule.id)
    args.output.write_text(json.dumps(audit_proof, indent=2))
    print(f"AuditProof written to {args.output}")
    print(f"Merkle root binding {len(library)} per-molecule receipts: "
          f"{audit_proof.get('record_receipts_merkle_root')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
