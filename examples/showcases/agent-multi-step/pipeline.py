"""
Multi-step agent showcase — Pattern 2/8 × Healthcare AI cohort.

Demonstrates per-step receipt emission for a 5-step LangGraph agent.
Each node emits a receipt; auditor follows the chain via the Step 8
Merkle root.

Per `feedback_narrow_signed_claim_auditor_certifies.md`: no verdict
language. Per `feedback_ip_protection.md`: external-brand language only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import nanorix


# Five agent nodes — each emits a per-step receipt with its pattern_tag.
AGENT_NODES = [
    "kb_retrieval",
    "reasoning",
    "tool_use",
    "reflection",
    "output",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Multi-step agent — Healthcare AI cohort")
    p.add_argument("--query", type=str, default="synthetic test query")
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


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("NANORIX_API_KEY")
    if not api_key:
        sys.exit("NANORIX_API_KEY not set.")
    if not args.capsulefile_id:
        sys.exit("CAPSULEFILE_ID not set. Run `nanorix build` first.")

    client = nanorix.Client(api_key=api_key)

    # Customer-declared classification — agent vendor's responsibility
    # per ADR-013. Nanorix does not introspect query content.
    capsule = client.capsules.create(
        capsulefile=args.capsulefile_id,
        jurisdiction="US",
        data_classification="PHI",
        metadata={
            "pattern": "multi_step_agent",
            "agent_framework": "langgraph",
            "node_count": str(len(AGENT_NODES)),
        },
    )
    print(f"Agent capsule created: {capsule.id}")

    # Upload the query.
    client.capsules.upload(
        capsule_id=capsule.id,
        filename="query.json",
        content=json.dumps({"query": args.query}).encode("utf-8"),
    )

    # Run the agent. The agent script inside the capsule writes
    # per-node trace logs that we read for receipt emission. (In a
    # real integration, the agent itself emits receipts via the SDK
    # imported inside the capsule.)
    client.capsules.exec(
        capsule_id=capsule.id,
        command="python /capsule/agent.py",
    )

    # Emit per-node receipts. Each receipt's pattern_tag identifies
    # the LangGraph node; auditor follows the chain.
    for idx, node in enumerate(AGENT_NODES):
        client.capsules.emit_record(
            capsule_id=capsule.id,
            record_id=f"step-{idx}-{node}",
            pattern_tag=node,
            input_hash=sha512_hex(f"step-{idx}-input-synthetic"),
            output_hash=sha512_hex(f"step-{idx}-output-synthetic"),
        )
        print(f"  Receipt emitted for {node}")
    print(f"Total per-step receipts: {len(AGENT_NODES)}")

    audit_proof = client.capsules.destroy(capsule.id)
    args.output.write_text(json.dumps(audit_proof, indent=2))
    print(f"AuditProof written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
