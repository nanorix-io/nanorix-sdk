"""
Sub-C tests — offline AuditProof verifier.

Builds synthetic-but-valid CDPs from scratch using the same chain hash
formula the production code uses, then verifies them. Failure modes
exercised: bad signature, broken chain at each step, missing subsystem,
wrong algorithm, malformed input.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nanorix.verifier import verify
from nanorix.verifier._verify import (
    CANONICAL_SUBSYSTEMS,
    GENESIS_HASH,
    _compute_step_hash,
)


def _make_synthetic_proof() -> tuple[dict, Ed25519PrivateKey]:
    """Build a valid synthetic CDP signed by a fresh Ed25519 key.

    Returns the proof dict + the private key (for negative tests).
    """
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()

    methods = [
        "procfs_verification",
        "mountinfo_verification",
        "dod_5220_multipass_wipe",
        "ed25519_key_destruction",
        "credential_incineration",
        "merkle_tree_verification",
        "hash_chain_validation",
        "capsule_lifecycle_verification",
    ]
    timestamp = "2026-05-05T12:00:00.000000000Z"
    chain = []
    prev = GENESIS_HASH
    for idx, (subsys, method) in enumerate(zip(CANONICAL_SUBSYSTEMS, methods), start=1):
        h = _compute_step_hash(prev, subsys, method, timestamp)
        chain.append(
            {
                "step": idx,
                "subsystem": subsys,
                "operation": "destroy",
                "method": method,
                "timestamp": timestamp,
                "evidence_hash": "sha512:" + hashlib.sha512(f"step{idx}".encode()).hexdigest(),
                "chain_hash": "sha512:" + h,
            }
        )
        prev = h

    final_chain_hash = prev
    # Sign chain_hash AS ASCII HEX BYTES (not raw)
    signature = priv.sign(final_chain_hash.encode("ascii"))

    proof = {
        "cdp_version": "1.0",
        "chain": chain,
        "attestation": {
            "algorithm": "Ed25519",
            "key_id": "test-key-1",
            "public_key": "base64:" + base64.b64encode(pub_bytes).decode("ascii"),
            "signature": "base64:" + base64.b64encode(signature).decode("ascii"),
        },
    }
    return proof, priv


def test_synthetic_proof_verifies_ok():
    proof, _ = _make_synthetic_proof()
    result = verify(proof)
    assert result.ok is True
    assert result.chain_valid is True
    assert result.signature_valid is True
    assert result.subsystems_attested == CANONICAL_SUBSYSTEMS
    assert result.failure_reason == ""
    assert result.failed_step is None


def test_verify_accepts_dict_input():
    proof, _ = _make_synthetic_proof()
    assert verify(proof).ok is True


def test_verify_accepts_json_string_input():
    proof, _ = _make_synthetic_proof()
    assert verify(json.dumps(proof)).ok is True


def test_verify_accepts_bytes_input():
    proof, _ = _make_synthetic_proof()
    assert verify(json.dumps(proof).encode()).ok is True


def test_verify_accepts_path_input(tmp_path: Path):
    proof, _ = _make_synthetic_proof()
    p = tmp_path / "proof.json"
    p.write_text(json.dumps(proof))
    assert verify(p).ok is True
    assert verify(str(p)).ok is True


def test_chain_break_at_step_5_fails():
    proof, _ = _make_synthetic_proof()
    # Corrupt step 5's chain_hash → recomputation will mismatch at step 5.
    proof["chain"][4]["chain_hash"] = "sha512:" + "0" * 128
    result = verify(proof)
    assert result.ok is False
    assert result.chain_valid is False
    assert result.failed_step == 5
    assert "step 5" in result.failure_reason


def test_signature_mismatch_fails():
    proof, _ = _make_synthetic_proof()
    # Replace signature with a different valid signature over different data.
    other_priv = Ed25519PrivateKey.generate()
    bogus = other_priv.sign(b"different message")
    proof["attestation"]["signature"] = "base64:" + base64.b64encode(bogus).decode()
    result = verify(proof)
    assert result.ok is False
    assert result.chain_valid is True  # chain still re-computes correctly
    assert result.signature_valid is False
    assert "signature did not verify" in result.failure_reason


def test_missing_subsystem_fails():
    proof, _ = _make_synthetic_proof()
    # Remove step 7 → subsystem coverage check fails before chain check.
    proof["chain"].pop(6)
    result = verify(proof)
    assert result.ok is False
    assert "Subsystem mismatch" in result.failure_reason


def test_subsystem_reorder_fails():
    proof, _ = _make_synthetic_proof()
    # Swap step 3 and step 4 subsystems → order check fails.
    proof["chain"][2]["subsystem"], proof["chain"][3]["subsystem"] = (
        proof["chain"][3]["subsystem"],
        proof["chain"][2]["subsystem"],
    )
    result = verify(proof)
    assert result.ok is False
    assert "Subsystem mismatch" in result.failure_reason


def test_wrong_algorithm_fails():
    proof, _ = _make_synthetic_proof()
    proof["attestation"]["algorithm"] = "RSA-PSS-SHA256"
    result = verify(proof)
    assert result.ok is False
    assert result.signature_valid is False
    assert "Unsupported signature algorithm" in result.failure_reason


def test_malformed_input_returns_failure():
    result = verify("not valid json {")
    assert result.ok is False
    assert "parse" in result.failure_reason.lower()


def test_missing_chain_field_fails():
    result = verify({"attestation": {}, "cdp_version": "1.0"})
    assert result.ok is False
    assert "chain" in result.failure_reason.lower()


def test_step_hash_formula_matches_genesis():
    """Pin the chain hash formula against the canonical SHA-512("") genesis.

    Test vector from CLAUDE.md / the canonical verifier:
    GENESIS_HASH = SHA-512("") =
        cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce
        47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e
    """
    expected = (
        "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
    )
    assert hashlib.sha512(b"").hexdigest() == expected
    assert GENESIS_HASH == expected


def test_step_hash_includes_action_destroy():
    """Pin: action is hardcoded to "destroy" — operation field doesn't matter."""
    h = _compute_step_hash(
        GENESIS_HASH, "eee_namespace", "procfs_verification", "2026-05-05T12:00:00Z"
    )
    # Recompute manually to assert formula
    parts = (
        GENESIS_HASH.encode()
        + b"\x00"
        + b"eee_namespace"
        + b"\x00"
        + b"destroy"
        + b"\x00"
        + b"procfs_verification"
        + b"\x00"
        + b"2026-05-05T12:00:00Z"
    )
    assert h == hashlib.sha512(parts).hexdigest()
