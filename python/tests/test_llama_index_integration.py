"""Wave 14 / item 6b — LlamaIndex callback handler tests.

Mirrors `test_langchain_integration.py`. Exercises the
`nanorix.integrations.llama_index.NanorixLlamaIndexCallback` class
against:

- Synthetic `on_event_start` / `on_event_end` events (LlamaIndex's
  callback shape — single dispatch method discriminated by
  `CBEventType`)
- Per-event-type hook routing (4 ActivityEvent variants only)
- Closed-enum classification for `ToolCallFailureKind` on EXCEPTION events
- Canonical-JSON byte-equivalence with `_canonical_hash`
- Property tests on event orderings (≥1k iterations)
- Forever-Standard preservation (no new variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / AuditRecord
- Real `CallbackManager` integration

When `llama-index-core` isn't installed, the entire test module is skipped.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Skip when optional dep isn't present.
pytest.importorskip("llama_index.core")

from llama_index.core.callbacks import (  # noqa: E402
    CallbackManager,
    CBEventType,
    EventPayload,
)
from llama_index.core.callbacks.base_handler import BaseCallbackHandler  # noqa: E402

from nanorix.helpers.activity_events import (  # noqa: E402
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
)
from nanorix.integrations.llama_index import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    NanorixLlamaIndexCallback,
)


# ── fixture helpers ────────────────────────────────────────────────


def read_buffer(buf_path: Path) -> List[Dict[str, Any]]:
    if not buf_path.exists():
        return []
    text = buf_path.read_text()
    if not text.strip():
        return []
    return [json.loads(line) for line in text.strip().split("\n")]


def make_callback(buf_path: Path, **overrides: Any) -> NanorixLlamaIndexCallback:
    kwargs: Dict[str, Any] = {
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-li",
        "buffer_path": str(buf_path),
    }
    kwargs.update(overrides)
    return NanorixLlamaIndexCallback(**kwargs)


# ── inheritance + construction ──────────────────────────────────────


def test_inherits_base_callback_handler():
    cb = NanorixLlamaIndexCallback(framework=FRAMEWORK_HIPAA, capsulefile="t")
    assert isinstance(cb, BaseCallbackHandler)


def test_construction_records_required_args():
    cb = NanorixLlamaIndexCallback(framework=FRAMEWORK_HIPAA, capsulefile="agent-x")
    assert cb.framework == "HIPAA"
    assert cb.capsulefile == "agent-x"


def test_construction_with_optional_args():
    cb = NanorixLlamaIndexCallback(
        framework=FRAMEWORK_GDPR,
        capsulefile="agent-y",
        api_key="nrx_live_test",
        metadata={"trace_id": "abc"},
        framework_version="2026-02",
        agent_id="agent-y-sub-1",
        emit_agent_step_events=False,
    )
    assert cb.api_key == "nrx_live_test"
    assert cb.metadata == {"trace_id": "abc"}
    assert cb.framework_version == "2026-02"
    assert cb.agent_id == "agent-y-sub-1"
    assert cb.emit_agent_step_events is False


def test_construction_rejects_missing_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLlamaIndexCallback(framework="", capsulefile="x")


def test_construction_rejects_none_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLlamaIndexCallback(framework=None, capsulefile="x")  # type: ignore[arg-type]


def test_construction_rejects_missing_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixLlamaIndexCallback(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    cb = NanorixLlamaIndexCallback(framework=FRAMEWORK_SOC2, capsulefile="x")
    assert cb.api_key == "env_value_xyz"


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value")
    cb = NanorixLlamaIndexCallback(
        framework=FRAMEWORK_SOC2, capsulefile="x", api_key="explicit_value"
    )
    assert cb.api_key == "explicit_value"


def test_allowed_frameworks_contains_required_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS
    cb = NanorixLlamaIndexCallback(framework="CUSTOM_FRAMEWORK", capsulefile="x")
    assert cb.framework == "CUSTOM_FRAMEWORK"


# ── FUNCTION_CALL emits ToolCallAttempted/Completed pair ───────────


def test_function_call_emits_tool_call_attempted(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: {"name": "lab_lookup"},
            EventPayload.FUNCTION_CALL.value: {"member_id": "X"},
        },
        event_id="fc-1",
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "tool_call_attempted"
    assert e["tool_call_id"] == "fc-1"
    assert e["tool_name"] == "lab_lookup"
    # tool_args = the entire function_call dict
    assert e["tool_args_hash"] == _canonical_hash({"member_id": "X"})


def test_function_call_extracts_name_from_tool_object(tmp_path: Path):
    """Tool object with .name attribute (ToolMetadata-like)."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class FakeToolMeta:
        name = "weather_tool"

    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: FakeToolMeta(),
            EventPayload.FUNCTION_CALL.value: {"city": "SF"},
        },
        event_id="fc-2",
    )
    events = read_buffer(buf)
    assert events[0]["tool_name"] == "weather_tool"


def test_function_call_falls_back_to_unknown_when_payload_empty(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={},
        event_id="fc-3",
    )
    events = read_buffer(buf)
    assert events[0]["tool_name"] == "unknown"


def test_function_call_end_emits_tool_call_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: {"name": "search"},
            EventPayload.FUNCTION_CALL.value: {"q": "foo"},
        },
        event_id="fc-4",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: {"result": "bar"}},
        event_id="fc-4",
    )
    events = read_buffer(buf)
    assert len(events) == 2
    completed = events[1]
    assert completed["event"] == "tool_call_completed"
    assert completed["tool_call_id"] == "fc-4"
    assert completed["tool_name"] == "search"
    assert completed["tool_result_hash"] == _canonical_hash({"result": "bar"})


def test_function_call_end_with_response_payload(tmp_path: Path):
    """Some integrations put the output in RESPONSE instead of FUNCTION_OUTPUT."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc-5",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.RESPONSE.value: "ok"},
        event_id="fc-5",
    )
    completed = read_buffer(buf)[1]
    assert completed["tool_result_hash"] == _canonical_hash("ok")


def test_function_call_end_without_start_is_no_op(tmp_path: Path):
    """Defensive: end without matching start drops silently."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "x"},
        event_id="orphan",
    )
    assert read_buffer(buf) == []


# ── EXCEPTION fires ToolCallFailed when paired ─────────────────────


def test_exception_paired_with_function_call_emits_tool_call_failed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "fetch"}},
        event_id="fc-x",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: TimeoutError("upstream timed out")},
        event_id="fc-x",
    )
    failed = read_buffer(buf)[1]
    assert failed["event"] == "tool_call_failed"
    assert failed["tool_name"] == "fetch"
    assert failed["failure_reason"].startswith("TimeoutError:")
    assert failed["failure_kind"] == {"type": "timeout"}


def test_exception_with_string_payload_synthesizes_exception(tmp_path: Path):
    """Exception payload may be stringified by some integrations."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc-x2",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: "stringified error"},
        event_id="fc-x2",
    )
    failed = read_buffer(buf)[1]
    assert failed["event"] == "tool_call_failed"
    assert "stringified error" in failed["failure_reason"]


def test_bare_exception_without_in_flight_drops_silently(tmp_path: Path):
    """EXCEPTION without a matching in-flight FUNCTION_CALL/AGENT_STEP drops."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: ValueError("no pair")},
        event_id="orphan",
    )
    assert read_buffer(buf) == []


@pytest.mark.parametrize(
    "exc,expected_kind",
    [
        (PermissionError("denied"), "permission_denied"),
        (ValueError("bad arg"), "invalid_args"),
        (TypeError("wrong type"), "invalid_args"),
        (TimeoutError("slow"), "timeout"),
        (ConnectionError("upstream gone"), "upstream_failure"),
    ],
)
def test_classify_exception_closed_enum_mapping(
    exc: BaseException, expected_kind: str, tmp_path: Path
):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: exc},
        event_id="fc",
    )
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": expected_kind}
    assert expected_kind in TOOL_CALL_FAILURE_KINDS


def test_unmapped_exception_omits_failure_kind(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class BizarreError(Exception):
        pass

    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: BizarreError("weird")},
        event_id="fc",
    )
    failed = read_buffer(buf)[1]
    assert "failure_kind" not in failed
    assert "BizarreError" in failed["failure_reason"]


# ── LLM event emits ReasoningStepCompleted ─────────────────────────


def test_llm_event_emits_reasoning_step_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.LLM,
        payload={
            EventPayload.PROMPT.value: "Analyze patient X",
            EventPayload.MODEL_NAME.value: "gpt-4o",
        },
        event_id="llm-1",
    )
    cb.on_event_end(
        event_type=CBEventType.LLM,
        payload={EventPayload.RESPONSE.value: "The result is..."},
        event_id="llm-1",
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "reasoning_step_completed"
    assert e["step_idx"] == 0
    assert e["model"] == "gpt-4o"
    assert e["prompt_hash"].startswith("sha512:")
    assert e["response_hash"].startswith("sha512:")


def test_llm_event_with_messages_payload(tmp_path: Path):
    """Chat-model path: messages list under EventPayload.MESSAGES."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class FakeMsg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    cb.on_event_start(
        event_type=CBEventType.LLM,
        payload={
            EventPayload.MESSAGES.value: [
                FakeMsg("system", "You are an agent"),
                FakeMsg("user", "Hello"),
            ],
            EventPayload.MODEL_NAME.value: "claude-3-5",
        },
        event_id="llm-2",
    )
    cb.on_event_end(
        event_type=CBEventType.LLM,
        payload={EventPayload.RESPONSE.value: "Hi there"},
        event_id="llm-2",
    )
    e = read_buffer(buf)[0]
    assert e["model"] == "claude-3-5"
    assert e["prompt_hash"].startswith("sha512:")


def test_llm_step_idx_increments_across_calls(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for i in range(5):
        eid = f"llm-{i}"
        cb.on_event_start(
            event_type=CBEventType.LLM,
            payload={EventPayload.PROMPT.value: f"p{i}"},
            event_id=eid,
        )
        cb.on_event_end(
            event_type=CBEventType.LLM,
            payload={EventPayload.RESPONSE.value: f"r{i}"},
            event_id=eid,
        )
    events = read_buffer(buf)
    assert [e["step_idx"] for e in events] == [0, 1, 2, 3, 4]


def test_llm_exception_drops_no_event(tmp_path: Path):
    """LLM-level EXCEPTION (matched to LLM in-flight, not tool) emits nothing.

    Same discipline as LangChain's `on_llm_error` — LLM-level errors
    are NOT mapped to ToolCallFailed.
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.LLM,
        payload={EventPayload.PROMPT.value: "x"},
        event_id="llm-fail",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: Exception("boom")},
        event_id="llm-fail",
    )
    assert read_buffer(buf) == []


# ── AGENT_STEP emits tool-shape pair ───────────────────────────────


def test_agent_step_emits_tool_call_attempted_and_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.AGENT_STEP,
        payload={"input": "decide"},
        event_id="step-1",
    )
    cb.on_event_end(
        event_type=CBEventType.AGENT_STEP,
        payload={"output": "approved"},
        event_id="step-1",
    )
    events = read_buffer(buf)
    assert len(events) == 2
    assert events[0]["event"] == "tool_call_attempted"
    assert events[0]["tool_name"] == "agent_step"
    assert events[1]["event"] == "tool_call_completed"


def test_emit_agent_step_events_false_suppresses_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, emit_agent_step_events=False)
    cb.on_event_start(
        event_type=CBEventType.AGENT_STEP,
        payload={"input": "decide"},
        event_id="step-2",
    )
    cb.on_event_end(
        event_type=CBEventType.AGENT_STEP,
        payload={"output": "approved"},
        event_id="step-2",
    )
    assert read_buffer(buf) == []


# ── structural-noise events emit nothing ───────────────────────────


@pytest.mark.parametrize(
    "evt",
    [
        CBEventType.EMBEDDING,
        CBEventType.RETRIEVE,
        CBEventType.TREE,
        CBEventType.SUB_QUESTION,
        CBEventType.QUERY,
        CBEventType.SYNTHESIZE,
        CBEventType.CHUNKING,
        CBEventType.NODE_PARSING,
        CBEventType.TEMPLATING,
        CBEventType.RERANKING,
    ],
)
def test_structural_noise_events_emit_nothing(tmp_path: Path, evt: CBEventType):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(event_type=evt, payload={}, event_id=f"e-{evt.value}")
    cb.on_event_end(event_type=evt, payload={}, event_id=f"e-{evt.value}")
    assert read_buffer(buf) == []


# ── start_trace / end_trace are no-ops ─────────────────────────────


def test_start_trace_end_trace_no_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.start_trace(trace_id="t-1")
    cb.end_trace(trace_id="t-1", trace_map={"root": ["fc-1"]})
    assert read_buffer(buf) == []


# ── on_event debug callback ────────────────────────────────────────


def test_on_event_callback_fires_per_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    captured: List[Dict[str, Any]] = []
    cb = make_callback(buf, on_event=captured.append)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc-1",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="fc-1",
    )
    assert len(captured) == 2
    assert captured[0]["event"] == "tool_call_attempted"
    assert captured[1]["event"] == "tool_call_completed"


def test_on_event_failure_does_not_break_callback(tmp_path: Path):
    def bad(_: Dict[str, Any]) -> None:
        raise RuntimeError("debug crash")

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=bad)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="fc",
    )
    assert len(read_buffer(buf)) == 2


# ── agent_id + parent_event_id propagation ─────────────────────────


def test_agent_id_propagates_to_all_emitted_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="sub-A")
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="fc",
    )
    for e in read_buffer(buf):
        assert e.get("agent_id") == "sub-A"


def test_no_agent_id_omits_field(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="fc",
    )
    for e in read_buffer(buf):
        assert "agent_id" not in e


def test_parent_event_id_propagates(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
        parent_id="parent-step-1",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="fc",
    )
    events = read_buffer(buf)
    assert all(e.get("parent_event_id") == "parent-step-1" for e in events)


# ── full agent flow integration ────────────────────────────────────


def test_full_agent_flow_shape(tmp_path: Path):
    """Synthetic agent run: AGENT_STEP wrapping LLM + FUNCTION_CALL pair."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="agent-x")

    # Outer agent step
    cb.on_event_start(
        event_type=CBEventType.AGENT_STEP,
        payload={"input": "task"},
        event_id="step-1",
    )

    # LLM call inside step
    cb.on_event_start(
        event_type=CBEventType.LLM,
        payload={
            EventPayload.PROMPT.value: "decide",
            EventPayload.MODEL_NAME.value: "claude-3-5",
        },
        event_id="llm-1",
        parent_id="step-1",
    )
    cb.on_event_end(
        event_type=CBEventType.LLM,
        payload={EventPayload.RESPONSE.value: "use tool A"},
        event_id="llm-1",
    )

    # Function call inside step
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: {"name": "fetch"},
            EventPayload.FUNCTION_CALL.value: {"id": 1},
        },
        event_id="fc-1",
        parent_id="step-1",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: {"result": "ok"}},
        event_id="fc-1",
    )

    # Close step
    cb.on_event_end(
        event_type=CBEventType.AGENT_STEP,
        payload={"output": "done"},
        event_id="step-1",
    )

    events = read_buffer(buf)
    types = [e["event"] for e in events]
    # step_attempted, llm_reasoning, fc_attempted, fc_completed, step_completed
    assert types == [
        "tool_call_attempted",  # AGENT_STEP start
        "reasoning_step_completed",  # LLM end
        "tool_call_attempted",  # FUNCTION_CALL start
        "tool_call_completed",  # FUNCTION_CALL end
        "tool_call_completed",  # AGENT_STEP end
    ]
    assert all(e.get("agent_id") == "agent-x" for e in events)
    # parent_event_id propagated for inner events
    fc_attempt = events[2]
    assert fc_attempt["parent_event_id"] == "step-1"


# ── CallbackManager wraps + dispatches correctly ───────────────────


def test_callback_manager_dispatches_to_handler(tmp_path: Path):
    """LlamaIndex's `CallbackManager` is the runtime wrapper customers
    register via `Settings.callback_manager`. Confirm it correctly
    dispatches to our handler."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    mgr = CallbackManager([cb])

    # event_with: context-manager API for an event
    with mgr.event(
        CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: {"name": "search"},
            EventPayload.FUNCTION_CALL.value: {"q": "foo"},
        },
    ) as evt:
        evt.on_end(payload={EventPayload.FUNCTION_OUTPUT.value: "found"})

    events = read_buffer(buf)
    types = [e["event"] for e in events]
    assert "tool_call_attempted" in types
    assert "tool_call_completed" in types
    attempt = next(e for e in events if e["event"] == "tool_call_attempted")
    assert attempt["tool_name"] == "search"


# ── golden canonical_hash invariant ─────────────────────────────────


def test_canonical_hash_jcs_byte_equivalent_with_helpers():
    import hashlib

    payload = {"a": 1, "b": "hello"}
    expected = "sha512:" + hashlib.sha512(b'{"a":1,"b":"hello"}').hexdigest()
    assert _canonical_hash(payload) == expected


# ── property test on event orderings ────────────────────────────────


def test_property_random_event_orderings_produce_well_formed_buffer(tmp_path: Path):
    """1k random sequences of mixed event_types — all buffer lines are
    well-formed and tool pairs are matched by event_id."""
    rng = random.Random(0xBEEFC0DE)
    iterations = 1_000
    for it in range(iterations):
        buf = tmp_path / f"events_{it}.jsonl"
        cb = make_callback(buf)
        seq_len = rng.randint(1, 5)
        for j in range(seq_len):
            eid = f"e-{it}-{j}"
            kind = rng.choice(["fc", "llm", "agent", "fc_err", "noise"])
            if kind == "fc":
                cb.on_event_start(
                    event_type=CBEventType.FUNCTION_CALL,
                    payload={
                        EventPayload.TOOL.value: {"name": f"t_{rng.randint(0, 3)}"},
                        EventPayload.FUNCTION_CALL.value: {"k": rng.random()},
                    },
                    event_id=eid,
                )
                cb.on_event_end(
                    event_type=CBEventType.FUNCTION_CALL,
                    payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
                    event_id=eid,
                )
            elif kind == "fc_err":
                cb.on_event_start(
                    event_type=CBEventType.FUNCTION_CALL,
                    payload={EventPayload.TOOL.value: {"name": "x"}},
                    event_id=eid,
                )
                exc = rng.choice(
                    [TimeoutError("t"), ValueError("v"), ConnectionError("c")]
                )
                cb.on_event_end(
                    event_type=CBEventType.EXCEPTION,
                    payload={EventPayload.EXCEPTION.value: exc},
                    event_id=eid,
                )
            elif kind == "llm":
                cb.on_event_start(
                    event_type=CBEventType.LLM,
                    payload={EventPayload.PROMPT.value: f"p{rng.random()}"},
                    event_id=eid,
                )
                cb.on_event_end(
                    event_type=CBEventType.LLM,
                    payload={EventPayload.RESPONSE.value: f"r{rng.random()}"},
                    event_id=eid,
                )
            elif kind == "agent":
                cb.on_event_start(
                    event_type=CBEventType.AGENT_STEP,
                    payload={"x": rng.random()},
                    event_id=eid,
                )
                cb.on_event_end(
                    event_type=CBEventType.AGENT_STEP,
                    payload={"y": rng.random()},
                    event_id=eid,
                )
            else:
                cb.on_event_start(
                    event_type=CBEventType.RETRIEVE,
                    payload={},
                    event_id=eid,
                )
                cb.on_event_end(
                    event_type=CBEventType.RETRIEVE,
                    payload={},
                    event_id=eid,
                )

        events = read_buffer(buf)
        attempted: List[str] = []
        closures: List[str] = []
        for e in events:
            assert isinstance(e, dict)
            assert e.get("event") in {
                "tool_call_attempted",
                "tool_call_completed",
                "tool_call_failed",
                "reasoning_step_completed",
            }
            assert "at" in e
            if e["event"] == "tool_call_attempted":
                attempted.append(e["tool_call_id"])
            elif e["event"] in ("tool_call_completed", "tool_call_failed"):
                closures.append(e["tool_call_id"])
        for cid in closures:
            assert cid in attempted


# ── Forever-Standard ────────────────────────────────────


def test_adr_006_i0_only_four_variants_emitted(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    # FUNCTION_CALL ok
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "a"}},
        event_id="1",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ok"},
        event_id="1",
    )
    # FUNCTION_CALL err
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "b"}},
        event_id="2",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: TimeoutError("t")},
        event_id="2",
    )
    # LLM
    cb.on_event_start(
        event_type=CBEventType.LLM,
        payload={EventPayload.PROMPT.value: "p"},
        event_id="3",
    )
    cb.on_event_end(
        event_type=CBEventType.LLM,
        payload={EventPayload.RESPONSE.value: "r"},
        event_id="3",
    )
    # AGENT_STEP
    cb.on_event_start(
        event_type=CBEventType.AGENT_STEP, payload={}, event_id="4"
    )
    cb.on_event_end(
        event_type=CBEventType.AGENT_STEP, payload={}, event_id="4"
    )
    # Noise (should write nothing)
    cb.on_event_start(
        event_type=CBEventType.RETRIEVE, payload={}, event_id="5"
    )
    cb.on_event_end(
        event_type=CBEventType.RETRIEVE, payload={}, event_id="5"
    )

    events = read_buffer(buf)
    seen = {e["event"] for e in events}
    allowed = {
        "tool_call_attempted",
        "tool_call_completed",
        "tool_call_failed",
        "reasoning_step_completed",
    }
    assert seen <= allowed, f"Forever-Standard violation: {seen - allowed}"


# ── vocabulary discipline ───────────────────────────────────────────


def test_module_vocabulary_uses_auditproof_terminology():
    import nanorix.integrations.llama_index as li_mod

    src = Path(li_mod.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src


# ── round-trip determinism ──────────────────────────────────────────


def test_event_emission_deterministic_for_identical_inputs(tmp_path: Path):
    buf1 = tmp_path / "buf1.jsonl"
    buf2 = tmp_path / "buf2.jsonl"
    for buf in (buf1, buf2):
        cb = make_callback(buf)
        cb.on_event_start(
            event_type=CBEventType.FUNCTION_CALL,
            payload={
                EventPayload.TOOL.value: {"name": "t"},
                EventPayload.FUNCTION_CALL.value: {"k": "v", "n": 1},
            },
            event_id="fc",
        )
        cb.on_event_end(
            event_type=CBEventType.FUNCTION_CALL,
            payload={EventPayload.FUNCTION_OUTPUT.value: {"ok": True, "data": [1, 2, 3]}},
            event_id="fc",
        )
    e1 = read_buffer(buf1)
    e2 = read_buffer(buf2)
    assert e1[0]["tool_args_hash"] == e2[0]["tool_args_hash"]
    assert e1[1]["tool_result_hash"] == e2[1]["tool_result_hash"]


# ── concurrent / interleaved event_ids ─────────────────────────────


def test_concurrent_event_ids_do_not_collide(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "A"}},
        event_id="a",
    )
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "B"}},
        event_id="b",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "rb"},
        event_id="b",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: "ra"},
        event_id="a",
    )
    events = read_buffer(buf)
    assert events[0]["tool_name"] == "A" and events[0]["event"] == "tool_call_attempted"
    assert events[1]["tool_name"] == "B" and events[1]["event"] == "tool_call_attempted"
    assert events[2]["tool_name"] == "B" and events[2]["event"] == "tool_call_completed"
    assert events[3]["tool_name"] == "A" and events[3]["event"] == "tool_call_completed"


# ── existing helpers reachable ─────────────────────────────────────


def test_existing_helpers_still_reachable_via_module():
    from nanorix.helpers import activity_events as ae

    assert hasattr(ae, "emit_tool_call_attempted")
    assert hasattr(ae, "emit_tool_call_completed")
    assert hasattr(ae, "emit_tool_call_failed")
    assert hasattr(ae, "emit_reasoning_step_completed")
    assert TOOL_CALL_FAILURE_KINDS >= {
        "timeout",
        "permission_denied",
        "invalid_args",
        "upstream_failure",
        "cancelled",
        "reserved",
    }
    assert REASONING_STEP_KINDS >= {
        "plan",
        "reason",
        "synthesize",
        "decide",
        "reserved",
    }


# ── failure_kind shape ─────────────────────────────────────────────


def test_failure_kind_uses_type_tag_dict_shape(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.TOOL.value: {"name": "x"}},
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.EXCEPTION,
        payload={EventPayload.EXCEPTION.value: PermissionError("denied")},
        event_id="fc",
    )
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": "permission_denied"}


# ── on_event hook receives full event dict ─────────────────────────


def test_on_event_receives_full_event_dict(tmp_path: Path):
    captured: List[Dict[str, Any]] = []
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=captured.append)
    cb.on_event_start(
        event_type=CBEventType.FUNCTION_CALL,
        payload={
            EventPayload.TOOL.value: {"name": "tool"},
            EventPayload.FUNCTION_CALL.value: {"k": 1},
        },
        event_id="fc",
    )
    cb.on_event_end(
        event_type=CBEventType.FUNCTION_CALL,
        payload={EventPayload.FUNCTION_OUTPUT.value: {"ok": True}},
        event_id="fc",
    )
    written = read_buffer(buf)
    assert captured == written
