"""
Core verification logic — chain recomputation + Ed25519 signature check.

The chain hash formula is FROZEN (per `governance/rzl/src/cdp.rs` CDP_VERSION="1.0"):

    chain_hash = SHA-512(prev_hash \\x00 subsystem \\x00 "destroy" \\x00 method \\x00 timestamp)

Genesis (prev for step 1) = SHA-512("") =
    cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce
    47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e

Note `action` is always literal "destroy" — `operation` field in JSON is
descriptive only and does NOT participate in the hash.

Ed25519 signature is over step 8's chain_hash AS ASCII HEX BYTES (128 chars).
NOT raw binary digest. This is load-bearing — getting it wrong is the most
common verifier bug.

JSON prefixes (per `cdp_document.rs:1100+`):
- `chain_hash`, `evidence_hash`, `final_hash` → "sha512:{hex}" — strip before compare
- `attestation.public_key`, `attestation.signature` → "base64:{b64}" — strip before decode
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Genesis hash (SHA-512 of empty input). Must match `GENESIS_HASH` in
# governance/rzl/src/proofs/mod.rs.
GENESIS_HASH = (
    "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
)

# Canonical 8-step subsystem order (must match CHAIN_DEFS in cdp.rs).
CANONICAL_SUBSYSTEMS = [
    "eee_namespace",
    "eee_tmpfs",
    "eee_memory",
    "dire_keys",
    "dire_identity",
    "fgx_forensic",
    "rzl_audit",
    "capsule_destroy",
]


@dataclass
class VerifyResult:
    """Outcome of verifying an AuditProof.

    Attributes:
        ok: True iff chain + signature + subsystem coverage all valid.
        chain_valid: True iff every step's stored chain_hash matches
            the recomputed value.
        signature_valid: True iff Ed25519 signature verifies over step
            8's chain_hash.
        subsystems_attested: List of subsystem names found in the chain
            (in order). For a valid CDP this equals CANONICAL_SUBSYSTEMS.
        failed_step: 1-indexed step number where chain integrity broke,
            None if no chain failure (or signature failed instead).
        failure_reason: Human-readable diagnostic. Empty string if ok.
        chain_hash: The final (step 8) chain_hash if extracted, else "".
    """

    ok: bool
    chain_valid: bool
    signature_valid: bool
    subsystems_attested: List[str] = field(default_factory=list)
    failed_step: Optional[int] = None
    failure_reason: str = ""
    chain_hash: str = ""


def _strip_sha512_prefix(value: str) -> str:
    """Strip "sha512:" prefix (added at JSON-emit time) before comparison."""
    if value.startswith("sha512:"):
        return value[len("sha512:") :]
    return value


def _strip_base64_prefix(value: str) -> str:
    """Strip "base64:" prefix (added at JSON-emit time) before decode."""
    if value.startswith("base64:"):
        return value[len("base64:") :]
    return value


def _compute_step_hash(
    prev_hash: str,
    subsystem: str,
    method: str,
    timestamp: str,
) -> str:
    """Re-implement compute_step_hash from governance/rzl/src/proofs/mod.rs.

    `action` is always "destroy" — hardcoded per CDP spec.
    """
    parts = [
        prev_hash.encode(),
        b"\x00",
        subsystem.encode(),
        b"\x00",
        b"destroy",
        b"\x00",
        method.encode(),
        b"\x00",
        timestamp.encode(),
    ]
    return hashlib.sha512(b"".join(parts)).hexdigest()


def _load_proof(input_data: Union[Dict[str, Any], str, Path, bytes]) -> Dict[str, Any]:
    """Accept dict, JSON string, file path, or bytes and return parsed dict.

    Heuristic: if `Path` instance OR string with no `{`/`[` prefix, treat
    as path; if string starts with `{`/`[` or `"`, parse as JSON. Avoids
    `Path(<long json string>).exists()` raising ENAMETOOLONG on long
    strings.
    """
    if isinstance(input_data, dict):
        return input_data
    if isinstance(input_data, bytes):
        return cast(Dict[str, Any], json.loads(input_data))
    if isinstance(input_data, Path):
        return cast(Dict[str, Any], json.loads(input_data.read_text()))
    if isinstance(input_data, str):
        stripped = input_data.lstrip()
        if stripped.startswith(("{", "[", '"')):
            return cast(Dict[str, Any], json.loads(input_data))
        # Treat as path
        p = Path(input_data)
        if p.exists() and p.is_file():
            return cast(Dict[str, Any], json.loads(p.read_text()))
        # Last-resort: try JSON anyway (caller may have passed an
        # unprefixed JSON-encoded number/literal)
        return cast(Dict[str, Any], json.loads(input_data))
    raise TypeError(
        f"verify() expected dict / path / JSON string / bytes; got {type(input_data).__name__}"
    )


def verify(proof: Union[Dict[str, Any], str, Path, bytes]) -> VerifyResult:
    """Verify an AuditProof (CDP) offline.

    Three checks, each independent:
    1. Chain integrity — recompute every chain_hash from inputs.
    2. Ed25519 signature over step 8's chain_hash (as ASCII hex bytes).
    3. Subsystem coverage — all 8 canonical subsystems present in order.

    Args:
        proof: AuditProof as dict, JSON string, file path, or bytes.

    Returns:
        VerifyResult with `.ok=True` iff all three checks pass.
    """
    try:
        proof_dict = _load_proof(proof)
    except (TypeError, json.JSONDecodeError, OSError) as e:
        return VerifyResult(
            ok=False,
            chain_valid=False,
            signature_valid=False,
            failure_reason=f"Could not parse proof input: {e}",
        )

    chain = proof_dict.get("chain")
    if not isinstance(chain, list):
        return VerifyResult(
            ok=False,
            chain_valid=False,
            signature_valid=False,
            failure_reason="Proof missing 'chain' array",
        )

    # ── Subsystem coverage ─────────────────────────────────────────────
    subsystems = [step.get("subsystem", "") for step in chain]
    if subsystems != CANONICAL_SUBSYSTEMS:
        return VerifyResult(
            ok=False,
            chain_valid=False,
            signature_valid=False,
            subsystems_attested=subsystems,
            failure_reason=(
                f"Subsystem mismatch: expected {CANONICAL_SUBSYSTEMS}, got {subsystems}"
            ),
        )

    # ── Chain integrity ────────────────────────────────────────────────
    prev_hash = GENESIS_HASH
    for idx, step in enumerate(chain, start=1):
        method = step.get("method", "")
        timestamp = step.get("timestamp", "")
        subsystem = step.get("subsystem", "")
        stored = _strip_sha512_prefix(step.get("chain_hash", ""))
        computed = _compute_step_hash(prev_hash, subsystem, method, timestamp)
        if stored != computed:
            return VerifyResult(
                ok=False,
                chain_valid=False,
                signature_valid=False,
                subsystems_attested=subsystems,
                failed_step=idx,
                failure_reason=(
                    f"Chain hash mismatch at step {idx} ({subsystem}): "
                    f"stored={stored[:16]}... computed={computed[:16]}..."
                ),
                chain_hash=stored,
            )
        prev_hash = computed

    final_chain_hash = prev_hash  # step 8's chain_hash

    # ── Ed25519 signature ──────────────────────────────────────────────
    attestation = proof_dict.get("attestation", {})
    public_key_b64 = _strip_base64_prefix(attestation.get("public_key", ""))
    signature_b64 = _strip_base64_prefix(attestation.get("signature", ""))
    algorithm = attestation.get("algorithm", "")

    if algorithm != "Ed25519":
        return VerifyResult(
            ok=False,
            chain_valid=True,
            signature_valid=False,
            subsystems_attested=subsystems,
            failure_reason=f"Unsupported signature algorithm: {algorithm!r} (expected Ed25519)",
            chain_hash=final_chain_hash,
        )

    try:
        pub_key_bytes = base64.b64decode(public_key_b64)
        signature_bytes = base64.b64decode(signature_b64)
    except (ValueError, TypeError) as e:
        return VerifyResult(
            ok=False,
            chain_valid=True,
            signature_valid=False,
            subsystems_attested=subsystems,
            failure_reason=f"Could not decode base64 attestation: {e}",
            chain_hash=final_chain_hash,
        )

    try:
        public_key = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
    except (ValueError, TypeError) as e:
        return VerifyResult(
            ok=False,
            chain_valid=True,
            signature_valid=False,
            subsystems_attested=subsystems,
            failure_reason=f"Invalid Ed25519 public key: {e}",
            chain_hash=final_chain_hash,
        )

    # CRITICAL: signature is over chain_hash AS ASCII HEX BYTES, not raw bytes.
    # Per cdp_document.rs:1100 + cdp.rs sign_and_destroy.
    message = final_chain_hash.encode("ascii")

    try:
        public_key.verify(signature_bytes, message)
    except InvalidSignature:
        return VerifyResult(
            ok=False,
            chain_valid=True,
            signature_valid=False,
            subsystems_attested=subsystems,
            failure_reason="Ed25519 signature did not verify against final chain_hash",
            chain_hash=final_chain_hash,
        )

    return VerifyResult(
        ok=True,
        chain_valid=True,
        signature_valid=True,
        subsystems_attested=subsystems,
        chain_hash=final_chain_hash,
    )
