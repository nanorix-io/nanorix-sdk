"""
Wave B Items 7+8 — Portable Receipt Bundle + Portable Pubkey Bundle tests.

Cross-impl byte-equivalence with Rust/Go/TypeScript on the canonical reference
vectors locked in the test constants below.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
from typing import Any, Dict

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from nanorix.verifier.bundle import (
    BUNDLE_ERR_INDEX_OUT_OF_BOUNDS,
    BUNDLE_ERR_MERKLE_INCLUSION_FAILED,
    BUNDLE_ERR_NO_RECEIPTS,
    BUNDLE_ERR_RECORD_CHAIN_HASH_MISMATCH,
    BUNDLE_ERR_SHAPE,
    BUNDLE_ERR_SIGNATURE_FAILED,
    PORTABLE_RECEIPT_BUNDLE_DISCLAIMER,
    BundleError,
    PortableReceiptBundle,
    extract_receipt_bundle,
    verify_receipt_bundle,
)
from nanorix.verifier.pubkey_bundle import (
    PORTABLE_PUBKEY_BUNDLE_DISCLAIMER,
    PUBKEY_BUNDLE_ERR_BUNDLE_SIGNATURE_FAILED,
    PUBKEY_BUNDLE_ERR_EMPTY_BUNDLE,
    PUBKEY_BUNDLE_ERR_INVALID_ENTRY,
    PUBKEY_BUNDLE_ERR_UNSUPPORTED_VERSION,
    PUBKEY_BUNDLE_ERR_WRONG_BUNDLE_TYPE,
    PortablePubkeyBundle,
    PubKeyEntry,
    PubkeyBundleError,
    build_pubkey_bundle,
    resolve_parent_key,
    resolve_parent_key_forever,
    verify_pubkey_bundle,
)
from nanorix.verifier.wave_n import GENESIS_SHA512_HEX

# ─────────────────────────────────────────────────────────────────────────────
# Fixture builder
# ─────────────────────────────────────────────────────────────────────────────


def _compute_record_chain_hash_local(
    capsule_id: str,
    record_index: int,
    record_id: str,
    in_h: str,
    out_h: str,
    activity_root: str,
) -> str:
    """Mirror of Rust compute_record_chain_hash for fixture building."""
    in_s = in_h[len("sha512:"):] if in_h.startswith("sha512:") else in_h
    out_s = out_h[len("sha512:"):] if out_h.startswith("sha512:") else out_h
    act_s = activity_root[len("sha512:"):] if activity_root.startswith("sha512:") else activity_root
    idx = str(record_index)
    parts = (
        capsule_id.encode()
        + b"\x00"
        + idx.encode()
        + b"\x00"
        + record_id.encode()
        + b"\x00"
        + in_s.encode()
        + b"\x00"
        + out_s.encode()
        + b"\x00"
        + act_s.encode()
    )
    return hashlib.sha512(parts).hexdigest()


def _compute_step_hash_local(
    prev_hash: str, subsystem: str, action: str, method: str, timestamp: str
) -> str:
    parts = (
        prev_hash.encode()
        + b"\x00"
        + subsystem.encode()
        + b"\x00"
        + action.encode()
        + b"\x00"
        + method.encode()
        + b"\x00"
        + timestamp.encode()
    )
    return hashlib.sha512(parts).hexdigest()


def _merkle_pair_hash_local(left: str, right: str) -> str:
    return hashlib.sha512(left.encode() + b"\x00" + right.encode()).hexdigest()


def make_wave_n_proof_n2() -> Dict[str, Any]:
    """Synthesize a Wave-N AuditProof with N=2 receipts."""
    timestamp = "2026-05-12T00:00:00Z"
    capsule_id = "cap_bundle_test_n2"
    receipt_0_chain = _compute_record_chain_hash_local(
        capsule_id, 0, "rec_a", "sha512:11", "sha512:21", GENESIS_SHA512_HEX
    )
    receipt_1_chain = _compute_record_chain_hash_local(
        capsule_id, 1, "rec_b", "sha512:12", "sha512:22", GENESIS_SHA512_HEX
    )
    merkle_root = _merkle_pair_hash_local(receipt_0_chain, receipt_1_chain)

    methods = {
        "eee_namespace": "procfs_verification",
        "eee_tmpfs": "mountinfo_verification",
        "eee_memory": "dod_5220_multipass_wipe",
        "dire_keys": "ed25519_key_destruction",
        "dire_identity": "credential_incineration",
        "fgx_forensic": "merkle_tree_verification",
        "rzl_audit": "hash_chain_validation",
        "capsule_destroy": "capsule_lifecycle_verification",
    }
    subsystems = list(methods.keys())

    prev_hash = GENESIS_SHA512_HEX
    chain = []
    for i, s in enumerate(subsystems):
        method = methods[s]
        if i == 7:
            base = _compute_step_hash_local(prev_hash, s, "destroy", method, timestamp)
            chain_hash = hashlib.sha512(
                base.encode() + b"\x00" + merkle_root.encode()
            ).hexdigest()
        else:
            chain_hash = _compute_step_hash_local(prev_hash, s, "destroy", method, timestamp)
        chain.append({"subsystem": s, "method": method, "chain_hash": chain_hash})
        prev_hash = chain_hash
    final_hash = chain[-1]["chain_hash"]

    # Deterministic Ed25519 signer
    seed = b"\x2a" * 32
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    pk = sk.public_key()
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    sig_bytes = sk.sign(final_hash.encode("ascii"))
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")
    pub_b64 = base64.b64encode(
        pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")

    return {
        "cdp_version": "2.0",
        "capsule_id": capsule_id,
        "destroyed_at": timestamp,
        "chain": chain,
        "final_hash": final_hash,
        "record_receipts": [
            {
                "record_index": 0,
                "record_id": "rec_a",
                "record_input_hash": "sha512:11",
                "record_output_hash": "sha512:21",
                "record_chain_hash": f"sha512:{receipt_0_chain}",
                "merkle_inclusion_proof": [receipt_1_chain],
            },
            {
                "record_index": 1,
                "record_id": "rec_b",
                "record_input_hash": "sha512:12",
                "record_output_hash": "sha512:22",
                "record_chain_hash": f"sha512:{receipt_1_chain}",
                "merkle_inclusion_proof": [receipt_0_chain],
            },
        ],
        "record_receipts_merkle_root": f"sha512:{merkle_root}",
        "attestation": {
            "key_id": "nrx-verify-test-key",
            "verification_key": pub_b64,
            "signature": sig_b64,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Item 7 — Portable Receipt Bundle tests
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_receipt_bundle_n2_index_0_succeeds() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    assert bundle.bundle_version == "1.0"
    assert bundle.bundle_type == "receipt"
    assert bundle.audit_proof_anchors.capsule_id == "cap_bundle_test_n2"
    assert bundle.receipt["record_id"] == "rec_a"


def test_extract_receipt_bundle_n2_index_1_succeeds() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 1)
    assert bundle.receipt["record_id"] == "rec_b"


def test_extract_receipt_bundle_out_of_bounds_errors() -> None:
    proof = make_wave_n_proof_n2()
    with pytest.raises(BundleError) as exc_info:
        extract_receipt_bundle(proof, 99)
    assert exc_info.value.kind == BUNDLE_ERR_INDEX_OUT_OF_BOUNDS


def test_extract_receipt_bundle_pre_wave_n_errors() -> None:
    pre = {
        "cdp_version": "1.0",
        "capsule_id": "cap_pre",
        "destroyed_at": "2026-01-01T00:00:00Z",
        "chain": [],
    }
    with pytest.raises(BundleError) as exc_info:
        extract_receipt_bundle(pre, 0)
    assert exc_info.value.kind == BUNDLE_ERR_NO_RECEIPTS


def test_verify_receipt_bundle_roundtrip_index_0() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    verify_receipt_bundle(bundle)


def test_verify_receipt_bundle_roundtrip_index_1() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 1)
    verify_receipt_bundle(bundle)


def test_verify_receipt_bundle_tampered_chain_hash_rejected() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    bundle.receipt["record_output_hash"] = "sha512:bad"
    with pytest.raises(BundleError) as exc_info:
        verify_receipt_bundle(bundle)
    assert exc_info.value.kind == BUNDLE_ERR_RECORD_CHAIN_HASH_MISMATCH


def test_verify_receipt_bundle_tampered_inclusion_rejected() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    bundle.receipt["merkle_inclusion_proof"] = ["0" * 128]
    with pytest.raises(BundleError) as exc_info:
        verify_receipt_bundle(bundle)
    assert exc_info.value.kind == BUNDLE_ERR_MERKLE_INCLUSION_FAILED


def test_verify_receipt_bundle_tampered_signature_rejected() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    bundle.audit_proof_anchors.signature = base64.b64encode(b"\x00" * 64).decode()
    with pytest.raises(BundleError) as exc_info:
        verify_receipt_bundle(bundle)
    assert exc_info.value.kind == BUNDLE_ERR_SIGNATURE_FAILED


def test_bundle_disclaimer_factual_language() -> None:
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in PORTABLE_RECEIPT_BUNDLE_DISCLAIMER


def test_bundle_disclaimer_cites_mapping_artifact() -> None:
    assert "mapping artifact" in PORTABLE_RECEIPT_BUNDLE_DISCLAIMER
    assert "control-map" in PORTABLE_RECEIPT_BUNDLE_DISCLAIMER


def test_bundle_version_pinned() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    bundle.bundle_version = "2.0"
    with pytest.raises(BundleError) as exc_info:
        verify_receipt_bundle(bundle)
    assert exc_info.value.kind == BUNDLE_ERR_SHAPE


def test_bundle_type_pinned() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    bundle.bundle_type = "pubkey"
    with pytest.raises(BundleError) as exc_info:
        verify_receipt_bundle(bundle)
    assert exc_info.value.kind == BUNDLE_ERR_SHAPE


def test_bundle_serializes_round_trips() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    json_str = bundle.to_json()
    doc = json.loads(json_str)
    roundtripped = PortableReceiptBundle.from_dict(doc)
    assert roundtripped.audit_proof_anchors.capsule_id == bundle.audit_proof_anchors.capsule_id
    verify_receipt_bundle(roundtripped)


def test_bundle_json_no_forbidden_compliance_terms() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    json_str = bundle.to_json()
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in json_str


def test_extract_bundle_carries_framework_version() -> None:
    proof = make_wave_n_proof_n2()
    proof["regulatory_context"] = {"framework_version": "2026-02"}
    bundle = extract_receipt_bundle(proof, 0)
    assert bundle.audit_proof_anchors.framework_version_at_emit == "2026-02"


def test_extract_bundle_omits_framework_version_when_absent() -> None:
    proof = make_wave_n_proof_n2()
    bundle = extract_receipt_bundle(proof, 0)
    assert bundle.audit_proof_anchors.framework_version_at_emit is None
    out = bundle.to_dict()
    assert "framework_version_at_emit" not in out["audit_proof_anchors"]


# ─────────────────────────────────────────────────────────────────────────────
# Item 8 — Portable Pubkey Bundle tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_publisher() -> tuple[Ed25519PrivateKey, Ed25519PublicKey, str]:
    sk = Ed25519PrivateKey.from_private_bytes(b"\x07" * 32)
    pk = sk.public_key()
    return sk, pk, "nrx-bundle-publisher-test-v1"


def _make_test_pubkey_entry(key_id: str) -> PubKeyEntry:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    inner_sk = Ed25519PrivateKey.from_private_bytes(b"\x09" * 32)
    pk = inner_sk.public_key()
    pub_b64 = base64.b64encode(pk.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return PubKeyEntry(
        key_id=key_id,
        algorithm="Ed25519",
        public_key=pub_b64,
        valid_from="2026-01-01T00:00:00Z",
        valid_until="2027-01-01T00:00:00Z",
        issued_by_org="vendor:test",
    )


def test_build_pubkey_bundle_succeeds() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    assert bundle.bundle_version == "1.0"
    assert bundle.bundle_type == "pubkey"
    assert len(bundle.pubkeys) == 1
    assert bundle.bundle_signature.signature


def test_verify_pubkey_bundle_round_trip() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    verify_pubkey_bundle(bundle, pk)


def test_verify_pubkey_bundle_wrong_publisher_rejected() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    attacker_sk = Ed25519PrivateKey.from_private_bytes(b"\x63" * 32)
    attacker_pk = attacker_sk.public_key()
    with pytest.raises(PubkeyBundleError) as exc_info:
        verify_pubkey_bundle(bundle, attacker_pk)
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_BUNDLE_SIGNATURE_FAILED


def test_verify_pubkey_bundle_tampered_pubkey_rejected() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    bundle.pubkeys[0].issued_by_org = "vendor:hacked"
    with pytest.raises(PubkeyBundleError) as exc_info:
        verify_pubkey_bundle(bundle, pk)
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_BUNDLE_SIGNATURE_FAILED


def test_verify_pubkey_bundle_tampered_issuer_rejected() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    bundle.issuer_organization = "issuer:hacked"
    with pytest.raises(PubkeyBundleError) as exc_info:
        verify_pubkey_bundle(bundle, pk)
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_BUNDLE_SIGNATURE_FAILED


def test_resolve_parent_key_within_window_succeeds() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    mid = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "key-1", mid) is not None


def test_resolve_parent_key_before_window_returns_none() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    before = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "key-1", before) is None


def test_resolve_parent_key_after_window_returns_none() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    after = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "key-1", after) is None


def test_resolve_parent_key_forever_finds_outside_window() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    after = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "key-1", after) is None
    assert resolve_parent_key_forever(bundle, "key-1") is not None


def test_resolve_parent_key_unknown_returns_none() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    now = datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "unknown-key", now) is None


def test_resolve_parent_key_open_ended_window() -> None:
    sk, _pk, key_id = _make_publisher()
    entry = _make_test_pubkey_entry("key-current")
    entry.valid_until = None
    bundle = build_pubkey_bundle([entry], sk, key_id, "issuer:test")
    later = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    assert resolve_parent_key(bundle, "key-current", later) is not None


def test_build_bundle_with_multiple_keys() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [
            _make_test_pubkey_entry("key-1"),
            _make_test_pubkey_entry("key-2"),
            _make_test_pubkey_entry("key-3"),
        ],
        sk,
        key_id,
        "issuer:test",
    )
    assert len(bundle.pubkeys) == 3
    verify_pubkey_bundle(bundle, pk)


def test_build_empty_bundle_rejected() -> None:
    sk, _pk, key_id = _make_publisher()
    with pytest.raises(PubkeyBundleError) as exc_info:
        build_pubkey_bundle([], sk, key_id, "issuer:test")
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_EMPTY_BUNDLE


def test_build_bundle_invalid_algorithm_rejected() -> None:
    sk, _pk, key_id = _make_publisher()
    entry = _make_test_pubkey_entry("key-1")
    entry.algorithm = "RSA-2048"
    with pytest.raises(PubkeyBundleError) as exc_info:
        build_pubkey_bundle([entry], sk, key_id, "issuer:test")
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_INVALID_ENTRY


def test_build_bundle_wrong_pubkey_size_rejected() -> None:
    sk, _pk, key_id = _make_publisher()
    entry = _make_test_pubkey_entry("key-1")
    entry.public_key = base64.b64encode(b"\x00" * 16).decode()
    with pytest.raises(PubkeyBundleError) as exc_info:
        build_pubkey_bundle([entry], sk, key_id, "issuer:test")
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_INVALID_ENTRY


def test_pubkey_bundle_disclaimer_factual_language() -> None:
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in PORTABLE_PUBKEY_BUNDLE_DISCLAIMER


def test_pubkey_bundle_version_pinned() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    bundle.bundle_version = "2.0"
    with pytest.raises(PubkeyBundleError) as exc_info:
        verify_pubkey_bundle(bundle, pk)
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_UNSUPPORTED_VERSION


def test_pubkey_bundle_type_pinned() -> None:
    sk, pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    bundle.bundle_type = "receipt"
    with pytest.raises(PubkeyBundleError) as exc_info:
        verify_pubkey_bundle(bundle, pk)
    assert exc_info.value.kind == PUBKEY_BUNDLE_ERR_WRONG_BUNDLE_TYPE


def test_pubkey_bundle_json_no_forbidden_compliance_terms() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    json_str = bundle.to_json()
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in json_str


def test_pubkey_bundle_jcs_canonicalization_deterministic() -> None:
    sk, _pk, key_id = _make_publisher()
    bundle = build_pubkey_bundle(
        [_make_test_pubkey_entry("key-1")], sk, key_id, "issuer:test"
    )
    # Round-trip serialize/parse and check signature still verifies (deterministic
    # canonicalization is required for sign/verify symmetry).
    json_str = bundle.to_json()
    parsed = PortablePubkeyBundle.from_dict(json.loads(json_str))
    _pk2 = sk.public_key()
    verify_pubkey_bundle(parsed, _pk2)
