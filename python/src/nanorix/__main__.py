"""
Entry point for `python -m nanorix`.

Subcommands:
    python -m nanorix                      → print version
    python -m nanorix verify <proof.json>  → offline AuditProof verification
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nanorix._constants import VERSION
from nanorix.verifier import verify


def _cmd_verify(args: list[str]) -> int:
    """Run offline AuditProof verification on a JSON file.

    Usage:
        python -m nanorix verify <path-to-proof.json> [--quiet] [--json]

    Exit code:
        0 — verification succeeded
        1 — verification failed
        2 — usage error / file not found
    """
    json_output = "--json" in args
    quiet = "--quiet" in args
    paths = [a for a in args if not a.startswith("--")]

    if not paths:
        print("usage: python -m nanorix verify <proof.json> [--quiet] [--json]", file=sys.stderr)
        return 2

    path = Path(paths[0])
    if not path.exists():
        print(f"error: proof file not found: {path}", file=sys.stderr)
        return 2

    result = verify(path)

    if json_output:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "chain_valid": result.chain_valid,
                    "signature_valid": result.signature_valid,
                    "subsystems_attested": result.subsystems_attested,
                    "failed_step": result.failed_step,
                    "failure_reason": result.failure_reason,
                    "chain_hash": result.chain_hash,
                },
                indent=2,
            )
        )
    elif not quiet:
        if result.ok:
            print(f"chain: VALID (SHA-512, {len(result.subsystems_attested)} steps)")
            print("signature: VALID (Ed25519)")
            print(f"subsystems: {len(result.subsystems_attested)}/8 attested")
            print("verdict: ok")
        else:
            print("verdict: FAIL")
            print(f"  chain_valid: {result.chain_valid}")
            print(f"  signature_valid: {result.signature_valid}")
            if result.failed_step is not None:
                print(f"  failed_step: {result.failed_step}")
            print(f"  reason: {result.failure_reason}")

    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(f"nanorix {VERSION}")
        return 0
    cmd, *rest = argv
    if cmd == "verify":
        return _cmd_verify(rest)
    if cmd in ("--version", "-v", "version"):
        print(f"nanorix {VERSION}")
        return 0
    print(f"unknown command: {cmd!r}", file=sys.stderr)
    print("usage: python -m nanorix [verify <proof.json>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
