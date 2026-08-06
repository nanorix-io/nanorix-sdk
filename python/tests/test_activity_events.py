"""Cap-3 (Phase 1) — ActivityEvent emission helpers tests.

Validates wire-shape invariants, hash determinism + JCS-flavor
canonical-JSON, closed-enum validation, sometimes-populated reserved
slots, and buffer-write atomicity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from nanorix.helpers.activity_events import (
    DEFAULT_BUFFER_PATH,
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
    emit_reasoning_step_completed,
    emit_tool_call_attempted,
    emit_tool_call_completed,
    emit_tool_call_failed,
)


# ── Canonical hash determinism (cross-impl invariant) ───────────────


def test_canonical_hash_is_deterministic_across_dict_key_orders():
    """JCS-flavor canonical JSON: sort_keys=True must produce identical
    bytes regardless of input dict's iteration order."""
    a = {"z": 1, "a": 2, "m": [3, 4]}
    b = {"a": 2, "m": [3, 4], "z": 1}
    assert _canonical_hash(a) == _canonical_hash(b)


def test_canonical_hash_starts_with_sha512_prefix():
    h = _canonical_hash({"k": "v"})
    assert h.startswith("sha512:")
    # 128 hex chars after prefix (SHA-512 digest size)
    assert len(h) == len("sha512:") + 128


def test_canonical_hash_matches_known_jcs_flavor():
    """Cross-impl invariant: this exact byte format is what the Rust
    runtime + verifier hashes against. Any drift breaks byte-equivalence
    with the AuditProof's canonical_hash."""
    payload = {"a": 1, "b": "hello"}
    expected_bytes = b'{"a":1,"b":"hello"}'
    expected = "sha512:" + hashlib.sha512(expected_bytes).hexdigest()
    assert _canonical_hash(payload) == expected


# ── tool_call_attempted ─────────────────────────────────────────────


def test_emit_tool_call_attempted_writes_minimal_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_tool_call_attempted(
        tool_call_id="call_1",
        tool_name="submit_pa_request",
        tool_args={"patient_id": "X"},
        buffer_path=str(buf),
    )
    assert event["event"] == "tool_call_attempted"
    assert event["tool_call_id"] == "call_1"
    assert event["tool_name"] == "submit_pa_request"
    assert event["tool_args_hash"].startswith("sha512:")
    assert "at" in event
    # Sometimes-populated reserved slots omitted by default.
    assert "agent_id" not in event
    assert "parent_event_id" not in event
    # Buffer wrote exactly one line.
    lines = buf.read_text().strip().split("\n")
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed == event


def test_emit_tool_call_attempted_with_reserved_slots(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_tool_call_attempted(
        tool_call_id="call_2",
        tool_name="search_patients",
        tool_args={"query": "active"},
        agent_id="planner_subagent",
        parent_event_id="evt_root",
        buffer_path=str(buf),
    )
    assert event["agent_id"] == "planner_subagent"
    assert event["parent_event_id"] == "evt_root"


def test_emit_tool_call_attempted_hash_zero_retention(tmp_path: Path):
    """Plaintext args MUST NOT appear in the emitted event — only the
    SHA-512 hash. Zero-retention discipline (ADR-006 I0)."""
    buf = tmp_path / "events.jsonl"
    secret = {"ssn": "123-45-6789", "patient_name": "Jane Doe"}
    event = emit_tool_call_attempted(
        tool_call_id="call_x",
        tool_name="lookup",
        tool_args=secret,
        buffer_path=str(buf),
    )
    serialized = json.dumps(event)
    assert "123-45-6789" not in serialized
    assert "Jane Doe" not in serialized
    assert event["tool_args_hash"] == _canonical_hash(secret)


# ── tool_call_completed ─────────────────────────────────────────────


def test_emit_tool_call_completed_writes_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_tool_call_completed(
        tool_call_id="call_1",
        tool_name="submit_pa_request",
        tool_result={"submission_id": "PA-001"},
        duration_ms=1_200,
        buffer_path=str(buf),
    )
    assert event["event"] == "tool_call_completed"
    assert event["tool_result_hash"].startswith("sha512:")
    assert event["duration_ms"] == 1_200


def test_emit_tool_call_completed_rejects_negative_duration(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="duration_ms"):
        emit_tool_call_completed(
            tool_call_id="x",
            tool_name="t",
            tool_result={},
            duration_ms=-1,
            buffer_path=str(buf),
        )


# ── tool_call_failed ────────────────────────────────────────────────


def test_emit_tool_call_failed_writes_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_tool_call_failed(
        tool_call_id="call_1",
        tool_name="upstream_api",
        failure_reason="upstream returned 503",
        duration_ms=500,
        buffer_path=str(buf),
    )
    assert event["event"] == "tool_call_failed"
    assert event["failure_reason"] == "upstream returned 503"
    # No closed-enum kind unless explicitly supplied.
    assert "failure_kind" not in event


def test_emit_tool_call_failed_with_closed_enum_kind(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_tool_call_failed(
        tool_call_id="call_1",
        tool_name="upstream_api",
        failure_reason="rate limited",
        duration_ms=10,
        failure_kind="upstream_failure",
        buffer_path=str(buf),
    )
    # Closed-enum-tagged JSON shape mirrors Rust serde output.
    assert event["failure_kind"] == {"type": "upstream_failure"}


def test_emit_tool_call_failed_rejects_unknown_failure_kind(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="closed-enum"):
        emit_tool_call_failed(
            tool_call_id="x",
            tool_name="t",
            failure_reason="?",
            duration_ms=1,
            failure_kind="not_a_real_kind",
            buffer_path=str(buf),
        )


def test_emit_tool_call_failed_accepts_all_closed_enum_kinds(tmp_path: Path):
    """Every closed-enum kind from the Rust definition must accept."""
    for kind in TOOL_CALL_FAILURE_KINDS:
        buf = tmp_path / f"events_{kind}.jsonl"
        event = emit_tool_call_failed(
            tool_call_id="x",
            tool_name="t",
            failure_reason="?",
            duration_ms=1,
            failure_kind=kind,
            buffer_path=str(buf),
        )
        assert event["failure_kind"] == {"type": kind}


# ── reasoning_step_completed ───────────────────────────────────────


def test_emit_reasoning_step_completed_writes_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_reasoning_step_completed(
        step_idx=0,
        prompt={"user": "What's PA criteria?"},
        response={"text": "Here is..."},
        duration_ms=2_400,
        buffer_path=str(buf),
    )
    assert event["event"] == "reasoning_step_completed"
    assert event["step_idx"] == 0
    assert event["prompt_hash"].startswith("sha512:")
    assert event["response_hash"].startswith("sha512:")
    # No model / step_kind / summary unless explicitly populated.
    assert "model" not in event
    assert "step_kind" not in event
    assert "summary_hash" not in event


def test_emit_reasoning_step_completed_with_full_reserved_slots(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    event = emit_reasoning_step_completed(
        step_idx=3,
        prompt={"q": "Decide"},
        response={"a": "Approve"},
        duration_ms=1_800,
        model="claude-opus-4-7",
        agent_id="decider",
        step_kind="decide",
        summary={"decision": "approve", "rationale": "criteria met"},
        parent_event_id="evt_synthesize_2",
        buffer_path=str(buf),
    )
    assert event["model"] == "claude-opus-4-7"
    assert event["agent_id"] == "decider"
    assert event["step_kind"] == {"type": "decide"}
    assert event["summary_hash"].startswith("sha512:")
    assert event["parent_event_id"] == "evt_synthesize_2"


def test_emit_reasoning_step_completed_rejects_invalid_step_kind(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="closed-enum"):
        emit_reasoning_step_completed(
            step_idx=0,
            prompt={"q": "?"},
            response={"a": "?"},
            duration_ms=1,
            step_kind="not_a_real_kind",
            buffer_path=str(buf),
        )


def test_emit_reasoning_step_completed_accepts_all_closed_enum_kinds(tmp_path: Path):
    for kind in REASONING_STEP_KINDS:
        buf = tmp_path / f"reasoning_{kind}.jsonl"
        event = emit_reasoning_step_completed(
            step_idx=0,
            prompt={"q": "?"},
            response={"a": "?"},
            duration_ms=1,
            step_kind=kind,
            buffer_path=str(buf),
        )
        assert event["step_kind"] == {"type": kind}


def test_emit_reasoning_step_completed_rejects_negative_step_idx(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    with pytest.raises(ValueError, match="step_idx"):
        emit_reasoning_step_completed(
            step_idx=-1,
            prompt={},
            response={},
            duration_ms=1,
            buffer_path=str(buf),
        )


# ── Multi-event sequence (chronological trail) ──────────────────────


def test_multi_event_sequence_appends_in_order(tmp_path: Path):
    buf = tmp_path / "events.jsonl"

    emit_reasoning_step_completed(
        step_idx=0,
        prompt={"q": "Plan"},
        response={"a": "Use tool X"},
        duration_ms=100,
        step_kind="plan",
        buffer_path=str(buf),
    )
    emit_tool_call_attempted(
        tool_call_id="t1",
        tool_name="X",
        tool_args={"k": "v"},
        buffer_path=str(buf),
    )
    emit_tool_call_completed(
        tool_call_id="t1",
        tool_name="X",
        tool_result={"ok": True},
        duration_ms=50,
        buffer_path=str(buf),
    )
    emit_reasoning_step_completed(
        step_idx=1,
        prompt={"q": "Decide"},
        response={"a": "Done"},
        duration_ms=80,
        step_kind="decide",
        buffer_path=str(buf),
    )

    lines = buf.read_text().strip().split("\n")
    assert len(lines) == 4
    parsed = [json.loads(line) for line in lines]
    event_names = [p["event"] for p in parsed]
    assert event_names == [
        "reasoning_step_completed",
        "tool_call_attempted",
        "tool_call_completed",
        "reasoning_step_completed",
    ]


def test_emit_creates_parent_directory_when_missing(tmp_path: Path):
    """The helper must create the buffer's parent directory if missing
    (matches capsule tmpfs auto-create discipline)."""
    buf = tmp_path / "subdir" / "deep" / "events.jsonl"
    assert not buf.parent.exists()
    emit_tool_call_attempted(
        tool_call_id="x",
        tool_name="t",
        tool_args={},
        buffer_path=str(buf),
    )
    assert buf.exists()


# ── Default buffer path constant ────────────────────────────────────


def test_default_buffer_path_matches_documented_capsule_mount():
    """The default buffer path is `/data/activity_events.jsonl`. The
    Nanorix daemon reads this exact path at destroy. Drift here =
    silent event loss."""
    assert DEFAULT_BUFFER_PATH == "/data/activity_events.jsonl"


# ── Cross-impl: hash matches Rust serde canonical-JSON shape ────────


def test_hash_matches_rust_serde_canonical_json_for_simple_value():
    """The Rust runtime hashes payloads with serde_json's
    canonical-JSON output (sort_keys + no whitespace). Python helper's
    JCS-flavor (sort_keys=True, separators=(",", ":")) produces
    byte-identical output for JSON-compatible primitives.
    """
    # Simple object, sorted-key output: `{"a":1,"b":2}`
    payload: dict[str, Any] = {"b": 2, "a": 1}
    expected_canonical = b'{"a":1,"b":2}'
    expected_hash = "sha512:" + hashlib.sha512(expected_canonical).hexdigest()
    assert _canonical_hash(payload) == expected_hash
