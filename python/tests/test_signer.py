"""
Order 6 sub-B tests — customer-side per-event signing helpers.

Covers Ed25519FileKey load + generate, sign_activity_event roundtrip,
verify_event_attestation positive + negative paths, canonical JSON
encoding determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

from nanorix.helpers import (
    Ed25519FileKey,
    EventAttestation,
    sign_activity_event,
)
from nanorix.helpers.signer import (
    canonicalize_event,
    verify_event_attestation,
)


# ── Canonical JSON ──


def test_canonicalize_event_sorts_keys():
    """Canonical JSON must be deterministic across input dict iteration order."""
    a = canonicalize_event({"b": 2, "a": 1, "c": 3})
    b = canonicalize_event({"c": 3, "a": 1, "b": 2})
    assert a == b
    parsed = json.loads(a)
    # Ensure ordering is stable
    assert list(parsed.keys()) == ["a", "b", "c"]


def test_canonicalize_event_no_whitespace():
    """No spaces between keys/values — minimum byte form."""
    out = canonicalize_event({"event": "test", "at": "2026-05-06"})
    assert b" " not in out
    assert b": " not in out
    assert b", " not in out


def test_canonicalize_event_pinned_cross_impl_vector():
    """Cross-impl invariant pin — this Python SDK's canonicalize_event +
    SHA-512 must produce IDENTICAL hash to the Rust server's
    canonical event hash
    AND the TypeScript SDK's `canonicalizeEvent` + SHA-512.

    If this hash drifts, EITHER the Python SDK OR the server impl OR
    the TS SDK diverged from RFC 8785 JCS — verifier replay would break
    cross-language. Pin all three implementations against this vector.

    Hardcoded expected value:
      sha512:b68d4893d287625a8545e3896f90715dea06ae156e139f118009c9c7af9795
             79bb547bf02aac94068152a69b752f387fe8b6179eed9a8782f3866cf76dadb472
    """
    import hashlib

    event = {
        "event": "execution_completed",
        "exit_code": 0,
        "duration_seconds": 5,
        "at": "2026-05-06T01:00:00Z",
    }
    canonical = canonicalize_event(event)
    expected = (
        "b68d4893d287625a8545e3896f90715dea06ae156e139f118009c9c7af979579"
        "bb547bf02aac94068152a69b752f387fe8b6179eed9a8782f3866cf76dadb472"
    )
    assert hashlib.sha512(canonical).hexdigest() == expected


# ── Ed25519FileKey ──


def test_ed25519_file_key_generate_creates_file(tmp_path: Path):
    p = tmp_path / "key.pem"
    key = Ed25519FileKey.generate(p)
    assert p.exists()
    assert p.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    assert key.algorithm_name() == "Ed25519"
    pub = key.public_key_bytes()
    assert len(pub) == 32  # raw Ed25519 public key


def test_ed25519_file_key_load_existing(tmp_path: Path):
    p = tmp_path / "key.pem"
    key1 = Ed25519FileKey.generate(p)
    key2 = Ed25519FileKey(p)
    assert key1.public_key_bytes() == key2.public_key_bytes()


def test_ed25519_file_key_sign_returns_64_bytes(tmp_path: Path):
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    sig = key.sign(b"hello")
    assert len(sig) == 64  # Ed25519 signature size


def test_ed25519_file_key_missing_file_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        Ed25519FileKey(tmp_path / "nonexistent.pem")


def test_ed25519_file_key_wrong_key_type_raises(tmp_path: Path):
    """File containing an RSA key (not Ed25519) must raise."""
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "rsa.pem"
    p.write_bytes(rsa_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    with pytest.raises(ValueError, match="not an Ed25519"):
        Ed25519FileKey(p)


# ── sign_activity_event roundtrip ──


def test_sign_activity_event_returns_wire_format(tmp_path: Path):
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    event = {
        "event": "execution_completed",
        "exit_code": 0,
        "duration_seconds": 5,
        "at": "2026-05-06T01:00:00Z",
    }
    att = sign_activity_event(key, event, event_id="evt-001")
    assert isinstance(att, EventAttestation)
    assert att.algorithm == "Ed25519"
    assert att.public_key.startswith("base64:")
    assert att.signature.startswith("base64:")
    assert att.event_id == "evt-001"
    assert att.signed_at.endswith("Z")


def test_sign_activity_event_to_wire_dict_shape(tmp_path: Path):
    """To_wire_dict matches the customer signing endpoint response shape."""
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    event = {"event": "test", "at": "2026-05-06T01:00:00Z"}
    att = sign_activity_event(key, event, event_id="e1")
    wire = att.to_wire_dict()
    assert set(wire.keys()) == {
        "algorithm",
        "public_key",
        "signature",
        "event_id",
        "signed_at",
    }


# ── verify_event_attestation ──


def test_verify_event_attestation_succeeds_with_correct_signature(tmp_path: Path):
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    event = {"event": "test", "at": "2026-05-06T01:00:00Z"}
    att = sign_activity_event(key, event, event_id="e1")
    assert verify_event_attestation(att, event) is True


def test_verify_event_attestation_fails_when_event_tampered(tmp_path: Path):
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    event = {"event": "test", "at": "2026-05-06T01:00:00Z"}
    att = sign_activity_event(key, event, event_id="e1")
    # Tamper: change a field — signature shouldn't verify anymore
    tampered = {"event": "DIFFERENT", "at": "2026-05-06T01:00:00Z"}
    assert verify_event_attestation(att, tampered) is False


def test_verify_event_attestation_fails_with_wrong_algorithm():
    """Unsupported algorithm rejected without attempting cryptographic verify."""
    bad_att = EventAttestation(
        algorithm="RSA-PSS-SHA256",
        public_key="base64:AA==",
        signature="base64:AA==",
        event_id="e1",
        signed_at="2026-05-06T01:00:00Z",
    )
    assert verify_event_attestation(bad_att, {"event": "test"}) is False


def test_verify_event_attestation_fails_with_corrupt_signature(tmp_path: Path):
    key = Ed25519FileKey.generate(tmp_path / "k.pem")
    event = {"event": "test", "at": "2026-05-06T01:00:00Z"}
    att = sign_activity_event(key, event, event_id="e1")
    # Corrupt: replace signature with garbage
    bad_att = EventAttestation(
        algorithm=att.algorithm,
        public_key=att.public_key,
        signature="base64:" + ("A" * 88),  # 64-byte garbage
        event_id=att.event_id,
        signed_at=att.signed_at,
    )
    assert verify_event_attestation(bad_att, event) is False
