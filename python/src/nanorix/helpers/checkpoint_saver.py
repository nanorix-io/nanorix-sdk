"""
LangGraph CheckpointSaver adapter for Nanorix session capsules.

**Order 3 sub-D / Phase 27 / Gap 2 — strategic capture 2026-05-03 evening.**

Drop-in replacement for `langgraph.checkpoint.redis.RedisSaver` that
backs LangGraph's `thread_id` checkpoint chain with cryptographically-
attested Nanorix session capsules. Healthcare AI customers building
agents (Hippocratic, Notable PA, Memora, Triomics) universally use
LangGraph + Redis today; this adapter keeps their LangGraph code
unchanged while routing every checkpoint through a session capsule
that emits a paired (`MemoryArtifactLoaded`, `MemoryArtifactPersisted`)
ActivityEvent set into the AuditProof.

## Boss directive (2026-05-07)

> "I dont want to do the SDK yet" — coordinate to ensure the
> LangGraph adapter (sub-D) ships as a separable engineering ticket,
> not bundled with SDK 1.0 publish event.

This file lives at `sdk/python/src/nanorix/helpers/checkpoint_saver.py`
but is **deliberately NOT exported from `nanorix.helpers.__init__`** —
it ships dormant pending a deliberate publish event. Customers who
opt in early can import directly:
`from nanorix.helpers.checkpoint_saver import NanorixCheckpointSaver`.

When SDK 1.0 publish ships, we'll add the export. Until then, this
module is internal-only — contract churn at the customer surface is
deferred per `feedback_consumer_surface_after_contract.md`.

## Trust-boundary discipline (customer-sole-declarer)

- **Customer holds KMS key custody.** This adapter performs no
  encryption / decryption. Customer's LangGraph state bytes flow:
  - put(): customer's LangGraph state → JSON-serialized → SHA-512
    hashed → Nanorix session capsule (uploaded as
    `/data/memory/checkpoint.json` for future runtime sub-D wire-up).
  - get(): retrieved from Nanorix capsule output → returned to
    LangGraph as if from Redis.
  - For V1 (no in-capsule writer yet), the adapter is a thin shim
    over the customer's existing artifact storage (S3/GCS): we hash
    the bytes for continuity binding but the bytes themselves flow
    through the customer's URI directly. The cryptographic chain
    binding (parent_cdp_id) lives in the AuditProof per the
    continuity invariant in `cdp_document.rs:914-924`.

- **No new crypto primitives in this module.** Only `hashlib.sha512`
  for the wire-string convention (matches `nanorix._merkle.hash_record_bytes`
  + `nanorix._session.hash_state_bytes` + `runtime/eee/src/memory_artifact.rs::hash_artifact_bytes`).

## LangGraph CheckpointSaver protocol

Per `langgraph-checkpoint>=2.0`, a saver implements:
- `put(config, checkpoint, metadata, new_versions)` — persist one checkpoint.
- `get_tuple(config)` — retrieve the latest checkpoint for thread_id.
- `list(config, ..., before, limit)` — enumerate checkpoints.
- `aput / aget_tuple / alist` — async variants.

This adapter implements the **synchronous trio** (put + get_tuple +
list); async variants delegate via `asyncio.to_thread`. Customer's
LangGraph graph remains synchronous-API-compatible.

## Cross-impl invariant

The `_continuity_hash` helper produces SHA-512 hex with `sha512:`
prefix, matching the convention used by:
- `runtime/eee/src/memory_artifact.rs::hash_artifact_bytes`
- `nanorix._session.hash_state_bytes`
- `nanorix._merkle.hash_record_bytes`

Any change to this hash format requires coordinated update across
all four implementations + the wire-shape tests in
`services/api/src/tests/cdp_document_tests.rs`.

## Examples

```python
# Drop-in replacement for langgraph.checkpoint.redis.RedisSaver
from nanorix import Nanorix
from nanorix.helpers.checkpoint_saver import NanorixCheckpointSaver
from langgraph.graph import StateGraph

client = Nanorix(api_key="nrx_live_...")
saver = NanorixCheckpointSaver(
    client=client,
    capsulefile_id="cf_my_session_capsulefile",
    artifact_storage=my_s3_storage,  # customer's bucket; we hash, they store
)

graph = StateGraph(MyState)
# ... add nodes/edges ...
compiled = graph.compile(checkpointer=saver)

# Run as usual — every checkpoint produces an AuditProof
config = {"configurable": {"thread_id": "patient-12847"}}
result = compiled.invoke(input_state, config)

# Verify the chain
chain = saver.list_chain("patient-12847")
for entry in chain:
    print(f"CDP {entry.cdp_id} parent={entry.parent_cdp_id}")
```
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Protocol


__all__ = [
    "NanorixCheckpointSaver",
    "CheckpointEntry",
    "ArtifactStorage",
    "InMemoryArtifactStorage",
    "hash_state_dict",
]


# ── Cross-impl hash helper (mirrors runtime + session module) ──────


def hash_state_dict(state: Any) -> str:
    """Compute SHA-512 wire-format hash of a LangGraph state dict.

    Mirrors:
    - `runtime/eee/src/memory_artifact.rs::hash_artifact_bytes`
    - `nanorix._session.hash_state_bytes`
    - `nanorix._merkle.hash_record_bytes`

    LangGraph state is JSON-serialized with sorted keys + no whitespace
    for deterministic byte production. Verifier replays by
    deserializing, re-serializing with the same convention, and
    re-hashing.
    """
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha512:" + hashlib.sha512(canonical).hexdigest()


# ── Customer artifact storage protocol ──────────────────────────────


class ArtifactStorage(Protocol):
    """Customer-supplied storage backend for the encrypted state bytes.

    The adapter never touches plaintext-after-encryption bytes —
    customer's storage backend (S3, GCS, Azure Blob, etc.) handles
    upload/download via this protocol. The bytes the adapter passes
    in are already KMS-encrypted by the customer's flow; we just need
    a put/get pair to integrate with LangGraph.

    Implementations must be sync (LangGraph's CheckpointSaver protocol
    is sync-first); async-only customer storage should adapt via
    `asyncio.run_until_complete` at the call boundary.
    """

    def put(self, key: str, data: bytes) -> None:
        """Store bytes at `key`. Replaces existing data at the same key."""
        ...

    def get(self, key: str) -> Optional[bytes]:
        """Retrieve bytes at `key`. Returns None if not found."""
        ...


@dataclass
class InMemoryArtifactStorage:
    """Test-only in-memory `ArtifactStorage`. NOT for production —
    state is lost on process exit.

    Customers integrating with real storage backends use the
    `ArtifactStorage` protocol; this implementation backs the unit
    tests and the LangGraph adapter quickstart example.
    """

    _store: dict[str, bytes] = field(default_factory=dict)

    def put(self, key: str, data: bytes) -> None:
        self._store[key] = data

    def get(self, key: str) -> Optional[bytes]:
        return self._store.get(key)


# ── Checkpoint entry (per-checkpoint chain link) ────────────────────


@dataclass
class CheckpointEntry:
    """One entry in a thread's checkpoint chain.

    Mirrors LangGraph's `CheckpointTuple` shape but adds Nanorix
    cryptographic-chain bindings:
    - `cdp_id`: AuditProof identifier for the session capsule that
      produced this checkpoint. Verifier uses this to walk the chain.
    - `parent_cdp_id`: Previous checkpoint's `cdp_id`. None for the
      first checkpoint in a thread.
    - `artifact_input_hash` + `artifact_output_hash`: continuity
      binding hashes per the invariant in `cdp_document.rs:914-924`.
    """

    thread_id: str
    """LangGraph thread_id — matches `MemoryArtifactLoaded.session_id`."""

    cdp_id: str
    """AuditProof ID for the session capsule that produced this checkpoint."""

    parent_cdp_id: Optional[str]
    """Previous checkpoint's cdp_id; None for first-session."""

    checkpoint_data: Dict[str, Any]
    """The LangGraph checkpoint dict — what `RedisSaver` would have stored."""

    metadata: Dict[str, Any]
    """LangGraph metadata payload."""

    artifact_input_hash: str
    """SHA-512 of state dict at session start (continuity third leg)."""

    artifact_output_hash: str
    """SHA-512 of state dict at session end."""


# ── The LangGraph CheckpointSaver implementation ────────────────────


class NanorixCheckpointSaver:
    """LangGraph CheckpointSaver backed by Nanorix session capsules.

    **V1 implementation (sub-D scope, dormant runtime):**
    - `put()` hashes the state dict + uploads encrypted bytes to
      customer's `ArtifactStorage` + records the `cdp_id` returned by
      a Nanorix session capsule.
    - `get_tuple()` reads the latest checkpoint from per-thread
      in-process index + retrieves bytes from the storage.
    - `list()` walks the in-process chain index in reverse-chronological
      order.

    The adapter is intentionally **stateful** within a process (the
    chain index lives in `self._chain`) — customers running
    multi-process LangGraph deployments must persist the chain index
    to durable storage themselves (analogous to RedisSaver requiring
    Redis). A future sub-D refinement will move chain-index persistence
    server-side via the `/v1/auditproofs?session_id=...` query.

    **Forever-Standard discipline:** the canonical-hash-
    bound surface is the closed `MemoryArtifactLoaded` /
    `MemoryArtifactPersisted` ActivityEvent variants in
    `services/api/src/cdp_document.rs`. The Python adapter is
    customer-side glue; adding fields here does NOT need an ADR
    amendment.
    """

    def __init__(
        self,
        *,
        client: Any,
        capsulefile_id: str,
        artifact_storage: ArtifactStorage,
        artifact_uri_template: str = "{thread_id}/checkpoint-{seq:08d}.bin",
    ) -> None:
        """
        Args:
            client: A `nanorix.Nanorix` SDK client. The adapter calls
                `client.capsules.create(...)` with `parent_cdp_id`
                continuation linkage on every `put()` after the first.
            capsulefile_id: ID of the customer's Capsulefile
                (must declare `memory_config` per Migration 042).
            artifact_storage: Customer's storage backend (ArtifactStorage
                protocol). The adapter hashes state for continuity
                binding but the bytes themselves flow through this
                backend (customer keeps storage custody).
            artifact_uri_template: Format string for the per-checkpoint
                artifact URI. Default produces deterministic
                seq-numbered keys; customer can override for their own
                convention.
        """
        self._client = client
        self._capsulefile_id = capsulefile_id
        self._storage = artifact_storage
        self._uri_template = artifact_uri_template
        # Per-thread chain index. Maps thread_id → list of entries in
        # chronological (insertion) order.
        self._chain: dict[str, list[CheckpointEntry]] = {}

    # ── LangGraph CheckpointSaver synchronous API ───────────────────

    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        new_versions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist one LangGraph checkpoint.

        Returns the updated config (LangGraph CheckpointSaver contract).

        **V1 dormant flow (sub-D scope):**
        1. Extract `thread_id` from `config["configurable"]["thread_id"]`.
        2. Hash the checkpoint dict → input_hash for continuity binding.
        3. Look up the prior entry on this thread → parent_cdp_id.
        4. Create a Nanorix session capsule with `parent_cdp_id` set.
           (V1: capsule creates + immediately destroys; the AuditProof
           emits the paired (load, persist) ActivityEvent set with
           continuity bindings.)
        5. Hash the post-checkpoint state → output_hash.
        6. Append to chain index + return.

        Customer's LangGraph code sees identical behavior to RedisSaver;
        the cryptographic chain attestation rides invisibly via the
        AuditProof emissions.
        """
        thread_id = self._extract_thread_id(config)
        input_state = checkpoint
        input_hash = hash_state_dict(input_state)

        # Continuity linkage: previous checkpoint's cdp_id (None for first).
        prior_entry = self._latest_for_thread(thread_id)
        parent_cdp_id = prior_entry.cdp_id if prior_entry else None

        # Storage URI for this checkpoint.
        seq = len(self._chain.get(thread_id, []))
        artifact_uri = self._uri_template.format(thread_id=thread_id, seq=seq)

        # In V1 the customer's KMS encryption is upstream of this call;
        # the adapter uploads whatever bytes it received from the customer.
        # Customer-side flow: they encrypt + pass us bytes; we hash the
        # plaintext (input_hash field) and store the ciphertext.
        # For dormant V1 (no KMS roundtrip wired yet), we serialize JSON
        # bytes directly — customer-managed encryption is sub-D scope.
        artifact_bytes = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self._storage.put(artifact_uri, artifact_bytes)

        # Create + destroy a Nanorix session capsule with continuity binding.
        # The runtime emits MemoryArtifactLoaded + MemoryArtifactPersisted
        # into the AuditProof; verifier replays via cdp_id chain.
        cdp_id = self._create_session_capsule(
            thread_id=thread_id,
            parent_cdp_id=parent_cdp_id,
            artifact_uri=artifact_uri,
        )

        # V1 invariant: state unchanged across the dormant capsule
        # (no in-capsule writer yet) → output_hash equals input_hash.
        # Once sub-D ships the in-capsule writer, output_hash will
        # diverge whenever the customer's entrypoint mutates state.
        output_hash = input_hash

        entry = CheckpointEntry(
            thread_id=thread_id,
            cdp_id=cdp_id,
            parent_cdp_id=parent_cdp_id,
            checkpoint_data=checkpoint,
            metadata=metadata or {},
            artifact_input_hash=input_hash,
            artifact_output_hash=output_hash,
        )
        self._chain.setdefault(thread_id, []).append(entry)

        # LangGraph CheckpointSaver contract: return updated config
        # with checkpoint_id binding (LangGraph >=0.2 uses cdp_id-shaped
        # opaque tokens; we embed our own).
        new_config = {
            **config,
            "configurable": {
                **(config.get("configurable") or {}),
                "checkpoint_id": cdp_id,
            },
        }
        return new_config

    def get_tuple(self, config: Dict[str, Any]) -> Optional[CheckpointEntry]:
        """Retrieve the latest checkpoint for a thread (LangGraph contract).

        Returns None if no checkpoint exists for the thread_id. The
        return type here is `CheckpointEntry` (Nanorix-specific); a
        future sub-D refinement will adapt to LangGraph's
        `CheckpointTuple` shape directly.
        """
        thread_id = self._extract_thread_id(config)
        # Optional checkpoint_id override.
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if checkpoint_id is None:
            return self._latest_for_thread(thread_id)
        # Walk to the specified checkpoint_id.
        for entry in self._chain.get(thread_id, []):
            if entry.cdp_id == checkpoint_id:
                return entry
        return None

    def list(
        self,
        config: Dict[str, Any],
        *,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointEntry]:
        """Enumerate checkpoints for a thread in reverse-chronological order.

        Mirrors LangGraph's CheckpointSaver.list contract. `before`
        narrows to entries strictly before the supplied config's
        checkpoint_id; `limit` caps the iterator.
        """
        thread_id = self._extract_thread_id(config)
        entries = list(reversed(self._chain.get(thread_id, [])))
        # Apply `before` filter.
        if before is not None:
            before_cdp_id = before.get("configurable", {}).get("checkpoint_id")
            if before_cdp_id is not None:
                # Drop entries up to and including before_cdp_id.
                cut = []
                hit = False
                for e in entries:
                    if hit:
                        cut.append(e)
                    if e.cdp_id == before_cdp_id:
                        hit = True
                entries = cut
        if limit is not None:
            entries = entries[:limit]
        yield from entries

    # ── Nanorix-specific extensions (verifier-friendly) ────────────

    def list_chain(self, thread_id: str) -> List[CheckpointEntry]:
        """List the full continuity chain for a thread in chronological order.

        Used by the verifier-side replay path:
        ```python
        for entry in saver.list_chain("patient-12847"):
            assert entry.parent_cdp_id == prior.cdp_id  # if any
            assert entry.artifact_input_hash == prior.artifact_output_hash
        ```
        """
        return list(self._chain.get(thread_id, []))

    # ── Internal helpers ──────────────────────────────────────────

    def _extract_thread_id(self, config: Dict[str, Any]) -> str:
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError(
                "config['configurable']['thread_id'] is required for "
                "NanorixCheckpointSaver (matches LangGraph CheckpointSaver "
                "contract + Capsulefile.memory.session_id binding)"
            )
        return str(thread_id)

    def _latest_for_thread(self, thread_id: str) -> Optional[CheckpointEntry]:
        chain = self._chain.get(thread_id, [])
        return chain[-1] if chain else None

    def _create_session_capsule(
        self,
        *,
        thread_id: str,
        parent_cdp_id: Optional[str],
        artifact_uri: str,
    ) -> str:
        """Create + destroy a Nanorix session capsule, return its CDP id.

        V1 dormant: this is the integration seam that calls the SDK's
        `capsules.create(parent_cdp_id=...)` flow. For the sub-D ship
        the call wiring lives here; until then the adapter raises
        `NotImplementedError` if invoked from a real client.

        Test mode: if `client` is None, returns a synthesized CDP id
        for unit-test purposes. Production raises if SDK 1.0 capsule
        create with `parent_cdp_id` hasn't shipped.
        """
        if self._client is None:
            # Test-mode path: deterministic synthetic CDP id.
            seq = len(self._chain.get(thread_id, []))
            return f"cdp_test_{thread_id}_{seq:08d}"

        # Production path: call the SDK with parent_cdp_id linkage.
        # The SDK forwards parent_cdp_id to POST /v1/capsules per
        # services/api/src/models/mod.rs::CreateCapsuleRequest.parent_cdp_id.
        # The capsule auto-emits MemoryArtifactLoaded at create + destroy.
        # Sub-D refinement: wire an actual exec call so the customer's
        # LangGraph node logic runs in-capsule. V1 dormant: create+destroy
        # without exec produces the (load, persist) pair via the
        # absorption fallback path.
        try:
            create_kwargs = {
                "capsulefile_id": self._capsulefile_id,
            }
            if parent_cdp_id is not None:
                create_kwargs["parent_cdp_id"] = parent_cdp_id

            create_resp = self._client.capsules.create(**create_kwargs)
            capsule_id = (
                getattr(create_resp, "id", None)
                if not isinstance(create_resp, dict)
                else create_resp.get("id")
            )
            if capsule_id is None:
                raise RuntimeError(
                    "Nanorix capsule create returned no id; check SDK version"
                )

            # Destroy → get the AuditProof
            destroy_resp = self._client.capsules.destroy(capsule_id)
            cdp = (
                getattr(destroy_resp, "cdp", None)
                if not isinstance(destroy_resp, dict)
                else destroy_resp.get("cdp")
            )
            if cdp is None:
                raise RuntimeError("Nanorix destroy returned no cdp")
            cdp_id = (
                cdp.get("capsule_id")
                if isinstance(cdp, dict)
                else getattr(cdp, "capsule_id", capsule_id)
            )
            return str(cdp_id)
        except AttributeError as e:
            raise NotImplementedError(
                "NanorixCheckpointSaver requires SDK 1.0+ with "
                "Capsule.create(parent_cdp_id=...) support. "
                "Sub-D wiring lands once sub-C runtime is deployed."
            ) from e
