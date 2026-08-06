"""
Session capsule helpers — `SessionCapsule` returned by `Capsule.session()`.

Per Agent A research (2026-05-05): adopted shape mirrors LangGraph
CheckpointSaver (`thread_id` semantics, `parent_config` chain) +
AWS Bedrock AgentCore CMK-encrypted artifact posture. Healthcare AI
customers building agents (Hippocratic, Notable PA, Memora, Triomics)
universally use LangGraph + Redis today; our `Capsule.session()`
ships as a drop-in replacement that adds cryptographic chain
attestation via `parent_cdp_id` + `session_id` bindings on the CDP.

V1 SDK-driven flow (server-side runtime auto-mount in subsequent
batches):
- Customer pre-fetches state.bin from their artifact_uri + decrypts
  via their KMS BEFORE passing bytes to SDK.
- SDK uploads state.bin to capsule, executes customer entrypoint,
  downloads new state.bin, destroys → ONE CdpAccess.
- Customer re-encrypts the new state with their KMS + persists to
  their next-session artifact_uri.

Continuity invariant (verifier-replayable):
- session_id is a metadata binding (matches LangGraph thread_id).
- parent_cdp_id links the chain. Verifier replays the chain by
  fetching each parent CDP and confirming continuity.
- artifact_input_hash + artifact_output_hash bind state bytes to
  the CDP via metadata. Verifier checks
  `current.artifact_input_hash == parent.artifact_output_hash`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from nanorix._models import CdpAccess


@dataclass
class SessionResult:
    """Outcome of `Capsule.session().run()` — one CDP for the session.

    Attributes:
        state_out: Customer's new state bytes (downloaded from
            /data/output/state.bin). Customer is responsible for
            re-encrypting + persisting to their artifact_uri before
            the next session in the chain.
        artifact_input_hash: SHA-512 hex (`sha512:...` prefix) of the
            initial_state bytes the SDK uploaded. Verifier uses this
            for continuity-chain validation.
        artifact_output_hash: SHA-512 hex of `state_out`. Customer
            must persist this for the next session's
            parent_artifact_output_hash binding.
        audit_proof: AuditProof for this session capsule. CDP includes
            session_id + parent_cdp_id + artifact_input_hash in
            metadata.
        session_id: The customer-supplied session_id (echo for
            convenience).
    """

    state_out: bytes
    artifact_input_hash: str
    artifact_output_hash: str
    audit_proof: CdpAccess
    session_id: str


def hash_state_bytes(data: bytes) -> str:
    """Compute SHA-512 wire-format hash of state bytes.

    Mirrors `runtime/eee/src/batch.rs::hash_record_bytes` convention
    + Python `_merkle.hash_record_bytes`. Verifier reproduces from
    the customer's persisted state bytes.
    """
    return "sha512:" + hashlib.sha512(data).hexdigest()


def build_session_metadata(
    *,
    session_id: str,
    parent_cdp_id: Optional[str],
    artifact_input_hash: str,
    extra: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Assemble the metadata bindings for a session capsule.

    Reserved `nanorix:` keys carry the continuity-chain bindings:
    - `nanorix:session_id` — customer-supplied session ID
      (LangGraph thread_id convention)
    - `nanorix:parent_cdp_id` — previous session's CDP ID
      (None for first session in chain)
    - `nanorix:artifact_input_hash` — SHA-512 of state bytes loaded
      at session start (verifier reproduces)

    Customer-supplied keys merge underneath these reserved bindings.
    The API enforces the `nanorix:*` reserved-prefix rule;
    customer-supplied `nanorix:*` keys would be rejected, but our
    SDK helper-emitted ones are allowed-listed at server.
    """
    md: dict[str, str] = {}
    if extra is not None:
        md.update(extra)
    md["nanorix:session_id"] = session_id
    if parent_cdp_id is not None:
        md["nanorix:parent_cdp_id"] = parent_cdp_id
    md["nanorix:artifact_input_hash"] = artifact_input_hash
    return md
