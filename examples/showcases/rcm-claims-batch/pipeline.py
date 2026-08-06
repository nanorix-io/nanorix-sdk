"""
RCM claims batch showcase — Pattern 4 × RCM cohort.

100-claim X12 EDI 837 batch with per-claim receipts. The AuditProof's
Step 8 Merkle root binds all per-claim receipts; auditor can verify
any single claim without seeing the others.

Per `feedback_narrow_signed_claim_auditor_certifies.md`: no verdict
language. Per `feedback_ip_protection.md`: external-brand vocabulary
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
    parser = argparse.ArgumentParser(
        description="RCM claims batch — RCM cohort showcase",
    )
    parser.add_argument(
        "--batch",
        type=Path,
        default=Path(__file__).parent / "input" / "sample_batch_100.jsonl",
        help="JSONL file with one claim per line.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "audit_proof.json",
        help="Path to write the resulting AuditProof.",
    )
    parser.add_argument(
        "--capsulefile-id",
        type=str,
        default=os.environ.get("CAPSULEFILE_ID"),
        help="Capsulefile ID (cf_<...>). Build first via `nanorix build`.",
    )
    return parser.parse_args()


def load_batch(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"Batch file not found: {path}")
    claims = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            claims.append(json.loads(line))
    return claims


def sha512_hex(data: bytes) -> str:
    return hashlib.sha512(data).hexdigest()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("NANORIX_API_KEY")
    if not api_key:
        sys.exit("NANORIX_API_KEY not set.")
    if not args.capsulefile_id:
        sys.exit("CAPSULEFILE_ID not set. Run `nanorix build -f capsulefile.yaml` first.")

    client = nanorix.Client(api_key=api_key)
    claims = load_batch(args.batch)
    print(f"Loaded {len(claims)} claims from {args.batch}")

    # Create one capsule for the whole batch (HIPAA scope-minimization
    # at audit grain rather than batch grain — per-claim receipts give
    # individual auditability without per-claim capsules).
    capsule = client.capsules.create(
        capsulefile=args.capsulefile_id,
        jurisdiction="US",
        data_classification="PHI",
        metadata={
            "pattern": "rcm_claim_adjudication",
            "batch_size": str(len(claims)),
        },
    )
    print(f"Capsule created: {capsule.id}")

    # Upload the batch as input.
    batch_bytes = "\n".join(json.dumps(c) for c in claims).encode("utf-8")
    client.capsules.upload(
        capsule_id=capsule.id,
        filename="batch.jsonl",
        content=batch_bytes,
    )
    print(f"Batch uploaded ({len(batch_bytes):,} bytes).")

    # Execute the adjudication entrypoint inside the sealed capsule.
    result = client.capsules.exec(
        capsule_id=capsule.id,
        command="python /capsule/adjudicate.py",
    )
    print(f"Adjudication finished. Exit: {result.exit_code}")

    # Emit one per-claim receipt. Auditor follows each claim
    # independently via the Merkle inclusion proof + Step 8's Merkle
    # root (ADR-039 + ADR-041 binding).
    for claim in claims:
        client.capsules.emit_record(
            capsule_id=capsule.id,
            record_id=claim["claim_id"],
            pattern_tag="rcm_claim",
            input_hash=sha512_hex(json.dumps(claim, sort_keys=True).encode("utf-8")),
        )
    print(f"Emitted {len(claims)} per-claim receipts.")

    # Destroy + collect AuditProof.
    audit_proof = client.capsules.destroy(capsule.id)
    args.output.write_text(json.dumps(audit_proof, indent=2))
    print(f"AuditProof written to {args.output}")
    print(f"Step 8 chain hash: {audit_proof['chain'][-1]['chain_hash']}")
    print(f"Record-receipts Merkle root: {audit_proof.get('record_receipts_merkle_root')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
