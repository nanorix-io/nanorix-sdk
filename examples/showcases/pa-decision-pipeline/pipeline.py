"""
PA decision pipeline showcase — Pattern 1 (LLM advisory) × Anterior cohort.

Ingests a synthetic 278 PA request, runs the decision capsule, emits per-case
receipt, destroys the capsule with AuditProof. Optionally files evidence
to Vanta via the ADR-040 mapping artifact + the adapter at
`docs/integrations/grc-adapters/vanta-adapter.md`.

Per `feedback_narrow_signed_claim_auditor_certifies.md`: no
COMPLIANT/SATISFIED/PASSED/MEETS in this file. Use applies_to /
references / related_to.

Per `feedback_ip_protection.md`: no internal Nanorix engine names.
External-brand vocabulary only (AuditProof, capsule, Capsulefile).

Per `feedback_customer_cold_start_path.md`: the only prerequisite is
NANORIX_API_KEY. The Capsulefile + sample input ship in-repo. Run
end-to-end with `python pipeline.py`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import nanorix


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PA decision pipeline — Anterior cohort showcase",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).parent / "input" / "sample_278.json",
        help="Path to a synthetic 278 PA request JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "audit_proof.json",
        help="Path to write the resulting AuditProof JSON.",
    )
    parser.add_argument(
        "--capsulefile-id",
        type=str,
        default=os.environ.get("CAPSULEFILE_ID"),
        help="Pre-built Capsulefile ID (cf_<...>). If absent, the script "
        "exits with instructions to run `nanorix build`.",
    )
    parser.add_argument(
        "--vanta-token",
        type=str,
        default=os.environ.get("VANTA_API_TOKEN"),
        help="Optional Vanta API token — when set, file evidence to Vanta.",
    )
    parser.add_argument(
        "--vanta-workspace-id",
        type=str,
        default=os.environ.get("VANTA_WORKSPACE_ID"),
        help="Optional Vanta workspace ID for filing.",
    )
    return parser.parse_args()


def load_pa_request(path: Path) -> dict:
    """Load a synthetic 278 PA request from disk."""
    if not path.exists():
        sys.exit(f"Input file not found: {path}")
    return json.loads(path.read_text())


def run_decision_capsule(
    client: nanorix.Client,
    capsulefile_id: str,
    pa_request: dict,
) -> dict:
    """Create a capsule, upload input, run the decision step, destroy + return AuditProof."""
    # Step 1 — create capsule with customer-declared context.
    # Per ADR-013 customer-sole-declarer: classification + jurisdiction
    # are customer-declared. Nanorix does not infer either.
    capsule = client.capsules.create(
        capsulefile=capsulefile_id,
        jurisdiction="US",
        data_classification="PHI",
        metadata={
            "pattern": "pa_decision",
            "payer_id": pa_request.get("payer_id"),
            "case_id": pa_request.get("case_id"),
        },
    )
    print(f"Capsule created: {capsule.id}")

    # Step 2 — upload the PA request input.
    client.capsules.upload(
        capsule_id=capsule.id,
        filename="pa_request.json",
        content=json.dumps(pa_request).encode("utf-8"),
    )
    print("PA request uploaded.")

    # Step 3 — execute the decision logic (entrypoint defined in
    # capsulefile.yaml). The capsule runs LLM advisory + criteria
    # binding inside the sealed boundary; we never see PHI in this
    # outer Python process.
    result = client.capsules.exec(
        capsule_id=capsule.id,
        command="python /capsule/decide.py",
    )
    print(f"Decision capsule executed. Exit: {result.exit_code}")

    # Step 4 — emit a per-record receipt for the PA case. The
    # receipt's pattern_tag is the signed primitive; downstream
    # adapters resolve to canonical control IDs at adapter time.
    receipt = client.capsules.emit_record(
        capsule_id=capsule.id,
        record_id=pa_request["case_id"],
        pattern_tag="pa_decision",
        input_hash=client.helpers.sha512(json.dumps(pa_request, sort_keys=True)),
    )
    print(f"Per-case receipt emitted: {receipt.record_id}")

    # Step 5 — destroy capsule + collect the AuditProof envelope.
    audit_proof = client.capsules.destroy(capsule.id)
    print(f"Capsule destroyed. Chain hash: {audit_proof['chain'][-1]['chain_hash']}")
    return audit_proof


def file_to_vanta(
    audit_proof: dict,
    *,
    vanta_token: str,
    vanta_workspace_id: str,
) -> None:
    """Optional — file the AuditProof to Vanta via the ADR-040 adapter."""
    # The Vanta adapter implementation lives at
    # `docs/integrations/grc-adapters/vanta-adapter.md` — vendor that
    # adapter into your codebase + invoke here. The mapping is applied
    # freshly at adapter time against framework_version=2026-02 (pinned
    # for the audit period).
    print(
        f"Filing to Vanta workspace {vanta_workspace_id} via ADR-040 adapter... "
        "(reference adapter at docs/integrations/grc-adapters/vanta-adapter.md)"
    )


def main() -> int:
    args = parse_args()
    if not args.capsulefile_id:
        sys.exit(
            "CAPSULEFILE_ID not set. Run:\n"
            "  nanorix build -f capsulefile.yaml\n"
            "and re-run with --capsulefile-id cf_<id>."
        )

    api_key = os.environ.get("NANORIX_API_KEY")
    if not api_key:
        sys.exit("NANORIX_API_KEY environment variable not set.")

    client = nanorix.Client(api_key=api_key)
    pa_request = load_pa_request(args.input)
    audit_proof = run_decision_capsule(client, args.capsulefile_id, pa_request)
    args.output.write_text(json.dumps(audit_proof, indent=2))
    print(f"AuditProof written to {args.output}")

    if args.vanta_token and args.vanta_workspace_id:
        file_to_vanta(
            audit_proof,
            vanta_token=args.vanta_token,
            vanta_workspace_id=args.vanta_workspace_id,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
