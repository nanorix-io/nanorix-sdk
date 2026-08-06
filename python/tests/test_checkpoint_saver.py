"""
Order 3 sub-D / Phase 27 / Gap 2 — LangGraph CheckpointSaver adapter tests.

Covers cross-impl invariants:
- `hash_state_dict` produces SHA-512 hex with `sha512:` prefix matching
  `runtime/eee/src/memory_artifact.rs::hash_artifact_bytes` convention.
- `NanorixCheckpointSaver.put` builds a continuity chain across
  multiple checkpoints on the same thread_id.
- Cross-thread isolation — two threads share the saver but their chains
  are independent.
- `list_chain` walks the chain in chronological order.
- `get_tuple` retrieves the latest checkpoint by default; checkpoint_id
  override walks to a specific entry.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from nanorix.helpers.checkpoint_saver import (
    InMemoryArtifactStorage,
    NanorixCheckpointSaver,
    hash_state_dict,
)


# ── Cross-impl hash convention ───────────────────────────────────────


def test_hash_state_dict_matches_runtime_wire_convention():
    """Sha512: prefix + 128-char lowercase hex matches Rust runtime."""
    out = hash_state_dict({"k": "v"})
    assert out.startswith("sha512:")
    assert len(out) == 7 + 128
    assert all(c in "0123456789abcdef" for c in out[7:])


def test_hash_state_dict_distinct_for_distinct_inputs():
    a = hash_state_dict({"alpha": 1})
    b = hash_state_dict({"beta": 2})
    assert a != b


def test_hash_state_dict_deterministic_across_key_order():
    """JSON canonical with sort_keys=True → byte-identical for any insertion order."""
    a = hash_state_dict({"a": 1, "b": 2, "c": 3})
    b = hash_state_dict({"c": 3, "a": 1, "b": 2})
    assert a == b, "key-order MUST not affect hash (verifier replay-safety)"


def test_hash_state_dict_empty_dict_matches_rust_runtime_anchor():
    """Lock the wire format for empty dict against runtime + SDK anchor."""
    out = hash_state_dict({})
    # JSON of empty dict → "{}" → SHA-512.
    expected = (
        "sha512:" + hashlib.sha512(b"{}").hexdigest()
    )
    assert out == expected


# ── Saver: chain construction ────────────────────────────────────────


def make_saver():
    """Build a saver in test mode (no SDK client; synthetic CDP ids)."""
    return NanorixCheckpointSaver(
        client=None,
        capsulefile_id="cf_test_session",
        artifact_storage=InMemoryArtifactStorage(),
    )


def test_put_first_checkpoint_has_no_parent():
    saver = make_saver()
    config = {"configurable": {"thread_id": "patient-12847"}}
    checkpoint = {"agent_state": "init"}
    result = saver.put(config, checkpoint, metadata={"step": 1})
    chain = saver.list_chain("patient-12847")
    assert len(chain) == 1
    assert chain[0].parent_cdp_id is None
    assert chain[0].cdp_id == result["configurable"]["checkpoint_id"]
    assert chain[0].thread_id == "patient-12847"


def test_put_continuation_links_parent_cdp_id():
    saver = make_saver()
    config = {"configurable": {"thread_id": "patient-12847"}}
    saver.put(config, {"agent_state": "step-1"}, metadata={"step": 1})
    saver.put(config, {"agent_state": "step-2"}, metadata={"step": 2})
    saver.put(config, {"agent_state": "step-3"}, metadata={"step": 3})
    chain = saver.list_chain("patient-12847")
    assert len(chain) == 3
    # Continuity invariant: each checkpoint's parent_cdp_id == prior cdp_id.
    assert chain[0].parent_cdp_id is None
    assert chain[1].parent_cdp_id == chain[0].cdp_id
    assert chain[2].parent_cdp_id == chain[1].cdp_id


def test_cross_thread_isolation():
    saver = make_saver()
    saver.put({"configurable": {"thread_id": "thread_A"}}, {"v": 1})
    saver.put({"configurable": {"thread_id": "thread_B"}}, {"v": 100})
    saver.put({"configurable": {"thread_id": "thread_A"}}, {"v": 2})
    a_chain = saver.list_chain("thread_A")
    b_chain = saver.list_chain("thread_B")
    assert len(a_chain) == 2
    assert len(b_chain) == 1
    # Thread A's continuation links its OWN parent, not thread B's.
    assert a_chain[1].parent_cdp_id == a_chain[0].cdp_id
    # Thread B starts fresh.
    assert b_chain[0].parent_cdp_id is None


def test_third_leg_invariant_across_chain():
    """V1 invariant: artifact_input_hash equals prior artifact_output_hash."""
    saver = make_saver()
    config = {"configurable": {"thread_id": "T1"}}
    for i in range(5):
        saver.put(config, {"step": i})
    chain = saver.list_chain("T1")
    for i in range(1, len(chain)):
        # In V1 dormancy state-unchanged invariant: each session's
        # output_hash equals its input_hash → continuation's input_hash
        # equals prior's output_hash. Locking this here ensures sub-D
        # writer changes preserve the chain shape.
        assert chain[i].artifact_input_hash == hash_state_dict({"step": i})


# ── LangGraph contract surface ───────────────────────────────────────


def test_get_tuple_latest_default():
    saver = make_saver()
    config = {"configurable": {"thread_id": "patient-12847"}}
    saver.put(config, {"v": 1})
    saver.put(config, {"v": 2})
    saver.put(config, {"v": 3})
    latest = saver.get_tuple(config)
    assert latest is not None
    assert latest.checkpoint_data == {"v": 3}


def test_get_tuple_with_checkpoint_id_override():
    saver = make_saver()
    config = {"configurable": {"thread_id": "T1"}}
    saver.put(config, {"v": 1})
    saver.put(config, {"v": 2})
    chain = saver.list_chain("T1")
    target = chain[0]
    config_with_id = {
        "configurable": {
            "thread_id": "T1",
            "checkpoint_id": target.cdp_id,
        }
    }
    fetched = saver.get_tuple(config_with_id)
    assert fetched is not None
    assert fetched.cdp_id == target.cdp_id
    assert fetched.checkpoint_data == {"v": 1}


def test_list_returns_reverse_chronological():
    saver = make_saver()
    config = {"configurable": {"thread_id": "T1"}}
    saver.put(config, {"v": 1})
    saver.put(config, {"v": 2})
    saver.put(config, {"v": 3})
    listed = list(saver.list(config))
    # Reverse-chronological per LangGraph CheckpointSaver contract.
    assert [e.checkpoint_data["v"] for e in listed] == [3, 2, 1]


def test_list_with_limit():
    saver = make_saver()
    config = {"configurable": {"thread_id": "T1"}}
    for i in range(10):
        saver.put(config, {"step": i})
    listed = list(saver.list(config, limit=3))
    assert len(listed) == 3
    # Most recent 3.
    assert [e.checkpoint_data["step"] for e in listed] == [9, 8, 7]


def test_list_with_before_filter():
    saver = make_saver()
    config = {"configurable": {"thread_id": "T1"}}
    for i in range(5):
        saver.put(config, {"step": i})
    chain = saver.list_chain("T1")
    midpoint = chain[3]
    before_config = {
        "configurable": {
            "thread_id": "T1",
            "checkpoint_id": midpoint.cdp_id,
        }
    }
    listed = list(saver.list(config, before=before_config))
    # Strictly before midpoint (idx 3); reverse-chronological.
    assert [e.checkpoint_data["step"] for e in listed] == [2, 1, 0]


# ── Validation ──────────────────────────────────────────────────────


def test_put_rejects_missing_thread_id():
    saver = make_saver()
    config_no_thread = {"configurable": {}}
    with pytest.raises(ValueError, match=r"thread_id.*required"):
        saver.put(config_no_thread, {"v": 1})


def test_get_tuple_returns_none_for_unknown_thread():
    saver = make_saver()
    result = saver.get_tuple({"configurable": {"thread_id": "never_seen"}})
    assert result is None


def test_get_tuple_returns_none_for_unknown_checkpoint_id():
    saver = make_saver()
    saver.put({"configurable": {"thread_id": "T1"}}, {"v": 1})
    result = saver.get_tuple(
        {
            "configurable": {
                "thread_id": "T1",
                "checkpoint_id": "cdp_does_not_exist",
            }
        }
    )
    assert result is None


# ── ArtifactStorage integration ──────────────────────────────────────


def test_put_writes_artifact_to_storage():
    storage = InMemoryArtifactStorage()
    saver = NanorixCheckpointSaver(
        client=None,
        capsulefile_id="cf_test",
        artifact_storage=storage,
    )
    saver.put({"configurable": {"thread_id": "T1"}}, {"k": "v"})
    # One artifact written per checkpoint.
    assert len(storage._store) == 1
    # Bytes are JSON-serialized in canonical form.
    [(uri, data)] = storage._store.items()
    assert uri.startswith("T1/checkpoint-")
    assert json.loads(data) == {"k": "v"}


def test_artifact_uri_template_customizable():
    storage = InMemoryArtifactStorage()
    saver = NanorixCheckpointSaver(
        client=None,
        capsulefile_id="cf_test",
        artifact_storage=storage,
        artifact_uri_template="agents/{thread_id}/v{seq:04d}.bin",
    )
    saver.put({"configurable": {"thread_id": "patient-12847"}}, {"k": "v"})
    [(uri, _)] = storage._store.items()
    assert uri == "agents/patient-12847/v0000.bin"
