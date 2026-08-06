#!/usr/bin/env python3
"""C2PA provenance demo — LLM output from a sealed capsule, destruction proof attached.

Offline end-to-end flow:

1. Load a real AuditProof (produced by the G10 wedge demo capsule run) and an
   AI-generated output text.
2. Sign a C2PA manifest binding the output bytes to that capsule run, using a
   customer-held Ed25519 key (generated locally by this script — in production
   the customer brings their own key; Nanorix never holds it).
3. Verify the manifest offline with `verify_c2pa_manifest` and cross-check the
   binding against the attached AuditProof.

Everything this demo signs is STRUCTURAL ("this output hash was produced in
this capsule at this time"). Whether that structural evidence answers EU AI
Act Article 50 for a specific deployment is the auditor's determination —
see the printed disclaimer.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "sdk" / "python" / "src"))

from nanorix.helpers.signer import Ed25519FileKey  # noqa: E402
from nanorix.integrations.c2pa_provenance import (  # noqa: E402
    C2PAManifestBuilder,
    verify_c2pa_manifest,
)

# The destruction evidence comes from the sibling wedge demo's real capsule
# run; its offline verifier (tools/verify_auditproof.py there) validates it
# from public inputs only. Bring your own proof with --proof.
DEFAULT_PROOF = HERE.parent / "g10-wedge-demo" / "out" / "auditproof.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, default=DEFAULT_PROOF)
    args = parser.parse_args()

    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)

    # ── 1. The capsule run's destruction evidence + the output it produced ──
    proof = json.loads(args.proof.read_text())
    capsule_id = proof["capsule_id"]
    verification_key_id = proof["attestation"]["key_id"]
    final_hash_hex = proof["final_hash"].removeprefix("sha512:")
    # Offline binding: the proof travels WITH the manifest, so we reference it
    # by content (first 40 hex of its final hash). In the live API flow you
    # would use `cdp.cdp_id` returned by `capsule.destroy()` instead — see
    # docs/integrations/c2pa-provenance.md.
    auditproof_id = f"fh-{final_hash_hex[:40]}"

    output_bytes = (HERE / "data" / "llm_output.txt").read_bytes()

    print(f"capsule run ............ {capsule_id}")
    print(f"destroyed at ........... {proof['destroyed_at']}")
    print(f"proof final hash ....... sha512:{final_hash_hex[:24]}…")

    # ── 2. Customer-held signing key (generated here for the demo) ──
    key_path = out_dir / "demo_customer_key.pem"
    if key_path.exists():
        signer = Ed25519FileKey(key_path)
    else:
        signer = Ed25519FileKey.generate(key_path)

    # Every provenance field is customer-declared (ADR-013: the SDK refuses to
    # infer any of them). The values below are the demo's declarations.
    manifest = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id=capsule_id,
            auditproof_id=auditproof_id,
            verification_key_id=verification_key_id,
        )
        .with_model(model_id="openai/gpt-4o-2024-08-06")
        .with_prompt_intent("clinical_pa_decision_narrative_v3")
        .with_generation_timestamp(proof["destroyed_at"])
        .with_output_content(output_bytes)
        .sign(signer)
    )

    manifest_bytes = manifest.to_canonical_bytes()
    (out_dir / "c2pa_manifest.json").write_bytes(manifest_bytes)
    print(f"manifest written ....... {out_dir / 'c2pa_manifest.json'}")

    # ── 3. Independent offline verification (what a downstream consumer runs) ──
    result = verify_c2pa_manifest(manifest_bytes, signer.public_key_bytes())

    checks = {
        "signature valid": result.signature_valid,
        "claims hash valid": result.claims_hash_valid,
        "bound to capsule": result.capsule_id == capsule_id,
        "bound to proof": result.auditproof_id == auditproof_id,
        "bound to proof key": result.verification_key_id == verification_key_id,
    }
    for label, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {label}")

    print()
    print(result.disclaimer)
    print()
    print("The referenced AuditProof verifies independently of this script,")
    print("from public inputs only, with the wedge demo's offline verifier:")
    print("  cd ../g10-wedge-demo && \\")
    print("  python3 tools/verify_auditproof.py --proof out/auditproof.json --pubkey out/capsule_pubkey.json")

    if not all(checks.values()):
        print("\nDEMO FAILED — one or more checks did not pass.")
        return 1
    print("\nDemo complete: output + provenance manifest + destruction proof, all offline-verifiable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
