"""
Offline AuditProof (CDP) verifier — pure Python, no network.

Re-implements the canonical 8-step SHA-512 chain + Ed25519 attestation
defined in `governance/rzl/src/cdp.rs` and `governance/rzl/src/proofs/mod.rs`.
Customers, auditors, and air-gapped systems can verify any AuditProof
independently without trusting the Nanorix API.

Usage:
    from nanorix.verifier import verify

    result = verify(proof_dict)         # accepts dict OR str path OR file-like
    if result.ok:
        print("VERIFIED:", result.chain_hash)
    else:
        print("FAIL:", result.failure_reason)

The verifier checks three layers + the canonical chain spec:

1. **Chain integrity** — re-compute every step's `chain_hash` via
   `SHA-512(prev \\x00 subsystem \\x00 "destroy" \\x00 method \\x00 timestamp)`,
   confirm each stored chain_hash matches.

2. **Ed25519 attestation** — verify `signature` over step 8's
   chain_hash (as ASCII hex bytes — NOT raw binary) using the
   embedded `public_key`.

3. **Subsystem coverage** — confirm all 8 canonical subsystems are
   present and in order (eee_namespace → eee_tmpfs → eee_memory →
   dire_keys → dire_identity → fgx_forensic → rzl_audit →
   capsule_destroy).

`failure_reason` carries a precise diagnostic for any failure (which
step, what mismatched). `failed_step` is the 1-indexed step number
where chain integrity broke (None if signature failed instead).
"""

from __future__ import annotations

from nanorix.verifier._verify import VerifyResult, verify
from nanorix.verifier.bundle import (
    PORTABLE_RECEIPT_BUNDLE_DISCLAIMER,
    AuditProofAnchors,
    BundleError,
    PortableReceiptBundle,
    extract_receipt_bundle,
    verify_receipt_bundle,
)
from nanorix.verifier.pubkey_bundle import (
    PORTABLE_PUBKEY_BUNDLE_DISCLAIMER,
    BundleSignature,
    PortablePubkeyBundle,
    PubKeyEntry,
    PubkeyBundleError,
    build_pubkey_bundle,
    resolve_parent_key,
    resolve_parent_key_forever,
    verify_pubkey_bundle,
)
from nanorix.verifier.wave_n import (
    GENESIS_SHA512_HEX,
    PARENT_PROOF_MAX_DEPTH,
    PATTERN_TAGS_WIRE,
    ParentProofLink,
    RecordReceipt,
    WaveNVerifyError,
    WaveNVerifyResult,
    build_merkle_inclusion_proof,
    compute_activity_root,
    compute_parent_proofs_merkle_root,
    compute_record_chain_hash,
    compute_record_receipts_merkle_root,
    compute_step_8_amended,
    compute_step_8_base,
    detect_parent_proof_cycle,
    enforce_depth_cap,
    merkle_pair_hash,
    merkle_root_sha512_null_separated,
    verify_full_audit_proof,
    verify_merkle_inclusion_proof,
    verify_record_receipt,
)

__all__ = [
    # Original V1 surface.
    "verify",
    "VerifyResult",
    # Wave-N surface.
    "GENESIS_SHA512_HEX",
    "PARENT_PROOF_MAX_DEPTH",
    "PATTERN_TAGS_WIRE",
    "RecordReceipt",
    "ParentProofLink",
    "WaveNVerifyError",
    "WaveNVerifyResult",
    "merkle_pair_hash",
    "merkle_root_sha512_null_separated",
    "compute_record_receipts_merkle_root",
    "compute_parent_proofs_merkle_root",
    "build_merkle_inclusion_proof",
    "verify_merkle_inclusion_proof",
    "compute_activity_root",
    "compute_record_chain_hash",
    "compute_step_8_base",
    "compute_step_8_amended",
    "detect_parent_proof_cycle",
    "enforce_depth_cap",
    "verify_record_receipt",
    "verify_full_audit_proof",
    # Wave B Item 7 — Portable Receipt Bundle surface.
    "PORTABLE_RECEIPT_BUNDLE_DISCLAIMER",
    "AuditProofAnchors",
    "BundleError",
    "PortableReceiptBundle",
    "extract_receipt_bundle",
    "verify_receipt_bundle",
    # Wave B Item 8 — Portable Pubkey Bundle surface.
    "PORTABLE_PUBKEY_BUNDLE_DISCLAIMER",
    "BundleSignature",
    "PortablePubkeyBundle",
    "PubKeyEntry",
    "PubkeyBundleError",
    "build_pubkey_bundle",
    "resolve_parent_key",
    "resolve_parent_key_forever",
    "verify_pubkey_bundle",
]
