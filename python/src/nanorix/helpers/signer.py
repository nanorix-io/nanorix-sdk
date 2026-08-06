"""
Customer-side per-event signing helpers — Order 6 / Gap 3.

Per Agent C research (2026-05-05): customer hosts a signing endpoint
that receives ActivityEvent payloads + returns Ed25519 signatures.
Nanorix daemon's signing-flow background task calls this endpoint
when the capsulefile has `signing.per_event: true` declared.

The endpoint shape is convergent across AWS KMS / GCP KMS / Azure
Key Vault:

    POST https://signer.acme.internal/v1/sign
    Headers: X-Nanorix-Capsule-Id, X-Nanorix-Event-Id, Content-Type: application/json
    Body:    {"algorithm": "Ed25519", "message_hash": "sha512:<hex>"}
    Response: {"signature": "base64:...", "public_key": "base64:...",
               "key_id": "...", "signed_at": "<rfc3339>"}

This module provides:
- `EventSigner` — Protocol customer implements (KMS-backed or HSM)
- `Ed25519FileKey` — concrete file-loaded private key for dev/testing
  (production customers wire their KMS by implementing EventSigner)
- `sign_activity_event(signer, event_dict, capsule_id, event_id)` —
  pure function that hashes event JSON, calls signer, returns
  EventAttestation matching the FullCdp.per_event_attestations schema

V1 customer integration:
1. Implement EventSigner protocol (or use Ed25519FileKey for dev)
2. Stand up an HTTPS-mTLS endpoint (FastAPI / Flask / Express)
3. On POST /v1/sign, extract message_hash, call sign(), return shape
4. Declare signing_config.customer_signing_endpoint in Capsulefile
5. Nanorix daemon POSTs to endpoint per event; populated attestations
   appear in FullCdp.per_event_attestations at destroy

Customer's KMS integration is THEIR responsibility (boto3 / google-cloud-kms /
azure-keyvault-keys); this SDK helper provides the harness + hashing
primitive + dev-mode Ed25519 file key.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Union

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)


@dataclass
class EventAttestation:
    """Per-event customer signature row.

    Mirrors `EventAttestation` struct at
    the Nanorix API service, plus the
    `capsule_event_attestations` table from Migration 045. The Nanorix
    daemon POSTs these into the queue at emission time; CDP absorption
    drains them into FullCdp.per_event_attestations at destroy.

    `signature` + `public_key` are wire-format with `base64:` prefix
    (matches existing convention for CDP attestation field).
    """

    algorithm: str  # "Ed25519" V1; "RSA-PSS-SHA256" reserved post-V1
    public_key: str  # base64-encoded; "base64:" prefix
    signature: str  # base64-encoded; "base64:" prefix
    event_id: str
    signed_at: str  # RFC 3339 UTC timestamp

    def to_wire_dict(self) -> dict[str, str]:
        """Render as dict matching the wire-format schema."""
        return {
            "algorithm": self.algorithm,
            "public_key": self.public_key,
            "signature": self.signature,
            "event_id": self.event_id,
            "signed_at": self.signed_at,
        }


class EventSigner(Protocol):
    """Protocol customer implements for KMS-backed signing.

    Methods:
        sign(message: bytes) -> bytes — sign raw bytes, return raw signature
        public_key_bytes() -> bytes — return raw 32-byte Ed25519 public key
        algorithm_name() -> str — closed enum: "Ed25519"
    """

    def sign(self, message: bytes) -> bytes: ...
    def public_key_bytes(self) -> bytes: ...
    def algorithm_name(self) -> str: ...


class Ed25519FileKey:
    """File-loaded Ed25519 private key — dev/testing helper.

    Production customers should implement `EventSigner` against their
    KMS (AWS KMS / GCP KMS / Azure Key Vault / HashiCorp Vault Transit /
    PKCS#11 HSM). This implementation loads a PEM private key from a
    file path; suitable for local dev + integration tests.

    Generate a keypair for testing:

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, NoEncryption,
        )

        key = Ed25519PrivateKey.generate()
        Path("test_key.pem").write_bytes(
            key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )

    Then: `Ed25519FileKey("test_key.pem")`
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"Ed25519 private key file not found: {self._path}")
        pem_bytes = self._path.read_bytes()
        loaded = load_pem_private_key(pem_bytes, password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise ValueError(
                f"File at {self._path} is not an Ed25519 private key (got {type(loaded).__name__})"
            )
        self._private = loaded
        self._public_bytes = self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, message: bytes) -> bytes:
        """Sign raw bytes with Ed25519. Returns 64-byte signature."""
        return self._private.sign(message)

    def public_key_bytes(self) -> bytes:
        """Return raw 32-byte Ed25519 public key."""
        return self._public_bytes

    def algorithm_name(self) -> str:
        """Return algorithm closed-enum string."""
        return "Ed25519"

    @staticmethod
    def generate(path: Union[str, Path]) -> "Ed25519FileKey":
        """Generate a fresh Ed25519 keypair + write to PEM file.

        Convenience for tests/dev. Production should use KMS.
        """
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        path = Path(path)
        path.write_bytes(pem)
        return Ed25519FileKey(path)


def canonicalize_event(event: Dict[str, Any]) -> bytes:
    """Compute canonical-JSON byte representation of an ActivityEvent.

    Deterministic JSON encoding (sorted keys, no whitespace) so the
    signature replays identically regardless of language/library.
    Matches the convention used by Rust runtime's
    `canonical_hash` (RFC 8785 JCS subset).
    """
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_activity_event(
    signer: EventSigner,
    event: Dict[str, Any],
    *,
    event_id: str,
    capsule_id: Optional[str] = None,
) -> EventAttestation:
    """Sign one ActivityEvent and return an EventAttestation.

    The customer's signing endpoint typically calls this after loading
    its KMS-backed key. Pure function — no network IO.

    Args:
        signer: EventSigner implementation (KMS-backed or Ed25519FileKey).
        event: Parsed ActivityEvent dict (has `event` discriminator +
            variant-specific fields per the reference implementation).
        event_id: Customer-assigned event ID; matches activity_trail
            event correlation. Composite (capsule_id, event_id) is the
            queue table primary key.
        capsule_id: Capsule ID (for logging / context); not part of
            signature input.

    Returns:
        EventAttestation with wire-format signature + public_key.
        Customer's HTTPS endpoint serializes via .to_wire_dict() and
        returns to the Nanorix daemon caller.
    """
    canonical_bytes = canonicalize_event(event)
    # Per the reference implementation convention: signature is over
    # SHA-512 hash of canonical JSON. The hash is what the signer
    # actually receives (so the signer doesn't need to know the JSON
    # encoding rules — it just signs bytes).
    message_hash = hashlib.sha512(canonical_bytes).digest()
    signature = signer.sign(message_hash)
    public_key = signer.public_key_bytes()

    signed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return EventAttestation(
        algorithm=signer.algorithm_name(),
        public_key="base64:" + base64.b64encode(public_key).decode("ascii"),
        signature="base64:" + base64.b64encode(signature).decode("ascii"),
        event_id=event_id,
        signed_at=signed_at,
    )


def verify_event_attestation(attestation: EventAttestation, event: Dict[str, Any]) -> bool:
    """Verify a customer-signed EventAttestation against its event.

    Used by the verifier to replay per-event customer attestations.
    Mirrors the verification logic the Nanorix verifier will run when
    a CDP has populated FullCdp.per_event_attestations.

    Returns True iff the signature verifies.
    """
    if attestation.algorithm != "Ed25519":
        return False
    pub_key_bytes = base64.b64decode(attestation.public_key.removeprefix("base64:"))
    sig_bytes = base64.b64decode(attestation.signature.removeprefix("base64:"))
    canonical_bytes = canonicalize_event(event)
    message_hash = hashlib.sha512(canonical_bytes).digest()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
        public_key.verify(sig_bytes, message_hash)
        return True
    except Exception:
        return False
