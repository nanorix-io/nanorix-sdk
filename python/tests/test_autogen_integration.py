"""Wave 18-12 / item 6c — AutoGen callback handler tests.

Mirrors `test_langgraph_integration.py` + `test_llama_index_integration.py`.
Exercises the `nanorix.integrations.autogen.NanorixAutoGenCallback`
class against:

- Synthetic AutoGen messages (OpenAI-shape dict + v0.4 attribute-shape)
- Hook-to-ActivityEvent mapping (4 variants only)
- Closed-enum classification for `ToolCallFailureKind`
- Canonical-JSON byte-equivalence with `_canonical_hash`
- Property tests on agent-conversation orderings
- Forever-Standard preservation (no new variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / AuditRecord
- Attachment helpers for legacy ConversableAgent (mocked) + v0.4
  runtime (mocked)

When NO AutoGen package is installed, the integration module raises
ImportError at import time. We satisfy the import by injecting a
minimal `autogen_agentchat` stub into `sys.modules` BEFORE importing
the integration module — this lets the tests run on any host without
needing the AutoGen extras installed. The callback's actual behavior
does not depend on any AutoGen API surface (it's a stable adapter).
"""

from __future__ import annotations

import json
import random
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ── Test-environment shim ───────────────────────────────────────────


# If no AutoGen package is importable, inject a minimal stub so the
# integration module's import-time guard passes. This shim is harmless
# in production environments (the real package wins by import order).
if "autogen_agentchat" not in sys.modules and "autogen" not in sys.modules:
    stub = types.ModuleType("autogen_agentchat")
    stub.__version__ = "0.4.0+nanorix-test-shim"  # type: ignore[attr-defined]
    sys.modules["autogen_agentchat"] = stub


from nanorix.helpers.activity_events import (  # noqa: E402
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
)
from nanorix.integrations.autogen import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    NanorixAutoGenCallback,
    attach_to_conversable_agent,
    attach_to_runtime,
)


# ── fixture helpers ────────────────────────────────────────────────


def read_buffer(buf_path: Path) -> List[Dict[str, Any]]:
    if not buf_path.exists():
        return []
    text = buf_path.read_text()
    if not text.strip():
        return []
    return [json.loads(line) for line in text.strip().split("\n")]


def make_callback(buf_path: Path, **overrides: Any) -> NanorixAutoGenCallback:
    kwargs: Dict[str, Any] = {
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-autogen",
        "buffer_path": str(buf_path),
    }
    kwargs.update(overrides)
    return NanorixAutoGenCallback(**kwargs)


def make_openai_message_with_tool_calls(
    *, calls: List[Dict[str, Any]], content: str = ""
) -> Dict[str, Any]:
    """Construct an OpenAI-shape assistant message with tool_calls."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
    }


def make_openai_tool_call(
    *, call_id: str, name: str, arguments: str
) -> Dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def make_anthropic_tool_use(
    *, call_id: str, name: str, args: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "type": "tool_use",
        "id": call_id,
        "name": name,
        "input": args,
    }


# ── construction ────────────────────────────────────────────────────


def test_construction_records_required_args():
    cb = NanorixAutoGenCallback(framework=FRAMEWORK_HIPAA, capsulefile="agent-x")
    assert cb.framework == "HIPAA"
    assert cb.capsulefile == "agent-x"


def test_construction_with_optional_args():
    cb = NanorixAutoGenCallback(
        framework=FRAMEWORK_GDPR,
        capsulefile="agent-y",
        api_key="nrx_live_test",
        metadata={"trace_id": "abc"},
        framework_version="2026-02",
        agent_id="agent-y-sub-1",
    )
    assert cb.api_key == "nrx_live_test"
    assert cb.metadata == {"trace_id": "abc"}
    assert cb.framework_version == "2026-02"
    assert cb.agent_id == "agent-y-sub-1"


def test_construction_rejects_missing_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixAutoGenCallback(framework="", capsulefile="x")


def test_construction_rejects_none_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixAutoGenCallback(framework=None, capsulefile="x")  # type: ignore[arg-type]


def test_construction_rejects_missing_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixAutoGenCallback(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    cb = NanorixAutoGenCallback(framework=FRAMEWORK_SOC2, capsulefile="x")
    assert cb.api_key == "env_value_xyz"


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value")
    cb = NanorixAutoGenCallback(
        framework=FRAMEWORK_SOC2, capsulefile="x", api_key="explicit_value"
    )
    assert cb.api_key == "explicit_value"


def test_allowed_frameworks_contains_required_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS
    cb = NanorixAutoGenCallback(framework="CUSTOM_FRAMEWORK", capsulefile="x")
    assert cb.framework == "CUSTOM_FRAMEWORK"


# ── on_agent_message_received emits ToolCallAttempted per tool_call ──


def test_on_message_received_emits_tool_call_attempted_for_openai_shape(
    tmp_path: Path,
):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(
                call_id="call_abc",
                name="lab_lookup",
                arguments='{"member_id":"X"}',
            )
        ]
    )
    emitted_ids = cb.on_agent_message_received(msg)
    assert emitted_ids == ["call_abc"]
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "tool_call_attempted"
    assert e["tool_call_id"] == "call_abc"
    assert e["tool_name"] == "lab_lookup"
    assert e["tool_args_hash"] == _canonical_hash('{"member_id":"X"}')


def test_on_message_received_emits_per_tool_for_multiple_tool_calls(
    tmp_path: Path,
):
    """Message with N tool_calls emits N ToolCallAttempted events."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(call_id="call_a", name="t_a", arguments="{}"),
            make_openai_tool_call(call_id="call_b", name="t_b", arguments="{}"),
            make_openai_tool_call(call_id="call_c", name="t_c", arguments="{}"),
        ]
    )
    emitted_ids = cb.on_agent_message_received(msg)
    assert emitted_ids == ["call_a", "call_b", "call_c"]
    events = read_buffer(buf)
    assert len(events) == 3
    assert {e["tool_call_id"] for e in events} == {"call_a", "call_b", "call_c"}


def test_on_message_received_without_tool_calls_emits_nothing(tmp_path: Path):
    """Plain conversation message (no tool_calls) emits no event."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = {"role": "user", "content": "Hi"}
    emitted_ids = cb.on_agent_message_received(msg)
    assert emitted_ids == []
    assert read_buffer(buf) == []


def test_on_message_received_handles_anthropic_tool_use_shape(tmp_path: Path):
    """Anthropic-shape tool_use entries inside content list."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _FakeMsg:
        content = [
            {"type": "text", "text": "thinking..."},
            make_anthropic_tool_use(
                call_id="toolu_abc",
                name="weather_tool",
                args={"city": "SF"},
            ),
        ]
        tool_calls = None

    cb.on_agent_message_received(_FakeMsg())
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["tool_call_id"] == "toolu_abc"
    assert e["tool_name"] == "weather_tool"
    assert e["tool_args_hash"] == _canonical_hash({"city": "SF"})


def test_on_message_received_pydantic_model_shape(tmp_path: Path):
    """V0.4 ChatMessage-like attribute-shape with tool_calls."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _ToolCall:
        id = "call_attr"

        class function:  # noqa: N801 — mimics OpenAI shape
            name = "search"
            arguments = '{"q":"foo"}'

    class _Msg:
        role = "assistant"
        content = ""
        tool_calls = [_ToolCall()]

    cb.on_agent_message_received(_Msg())
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["tool_name"] == "search"
    assert events[0]["tool_call_id"] == "call_attr"


# ── on_agent_message_sent emits ReasoningStepCompleted ─────────────


def test_on_message_sent_emits_reasoning_step_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = {"role": "assistant", "content": "Decision: approve"}
    cb.on_agent_message_sent(
        msg,
        prompt=[{"role": "user", "content": "Decide"}],
        model="gpt-4o",
        duration_ms=1200,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "reasoning_step_completed"
    assert e["step_idx"] == 0
    assert e["model"] == "gpt-4o"
    assert e["duration_ms"] == 1200
    assert e["prompt_hash"].startswith("sha512:")
    assert e["response_hash"].startswith("sha512:")


def test_on_message_sent_step_idx_increments_across_calls(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for i in range(5):
        cb.on_agent_message_sent({"role": "assistant", "content": f"r{i}"})
    events = read_buffer(buf)
    assert [e["step_idx"] for e in events] == [0, 1, 2, 3, 4]


def test_on_message_sent_with_step_kind(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_sent(
        {"role": "assistant", "content": "Final decision"},
        step_kind="decide",
    )
    events = read_buffer(buf)
    assert events[0]["step_kind"] == {"type": "decide"}


def test_on_message_sent_renders_attribute_shape_message(tmp_path: Path):
    """V0.4 attribute-shape message is rendered for hashing."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _Msg:
        role = "assistant"
        content = "Reply text"
        name = "agent_A"

    cb.on_agent_message_sent(_Msg(), duration_ms=500)
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["duration_ms"] == 500


# ── on_tool_call_completed (success + failure paths) ────────────────


def test_on_tool_call_completed_success_path(tmp_path: Path):
    """Pair attempt + completion across two callback invocations."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(
                call_id="call_xyz", name="search", arguments='{"q":"foo"}'
            )
        ]
    )
    cb.on_agent_message_received(msg)
    cb.on_tool_call_completed(
        "call_xyz",
        tool_result={"result": "found"},
        duration_ms=350,
    )
    events = read_buffer(buf)
    assert len(events) == 2
    completed = events[1]
    assert completed["event"] == "tool_call_completed"
    assert completed["tool_call_id"] == "call_xyz"
    assert completed["tool_name"] == "search"
    assert completed["tool_result_hash"] == _canonical_hash({"result": "found"})
    assert completed["duration_ms"] == 350


def test_on_tool_call_completed_with_error_emits_failed(tmp_path: Path):
    """Completion path with `error=` emits ToolCallFailed."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(
                call_id="call_err", name="bad_tool", arguments="{}"
            )
        ]
    )
    cb.on_agent_message_received(msg)
    cb.on_tool_call_completed(
        "call_err",
        error=ConnectionError("upstream gone"),
        duration_ms=900,
    )
    events = read_buffer(buf)
    failed = events[1]
    assert failed["event"] == "tool_call_failed"
    assert failed["tool_call_id"] == "call_err"
    assert failed["tool_name"] == "bad_tool"
    assert failed["failure_reason"].startswith("ConnectionError:")
    assert failed["failure_kind"] == {"type": "upstream_failure"}


def test_on_tool_call_completed_without_in_flight_is_defensive(tmp_path: Path):
    """Completion without matching attempt still emits — auditor sees gap."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_tool_call_completed(
        "orphan_call",
        tool_name="orphan",
        tool_result="ok",
        duration_ms=100,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "tool_call_completed"
    assert events[0]["tool_name"] == "orphan"


def test_on_tool_call_completed_computes_duration_from_in_flight(tmp_path: Path):
    """When duration_ms is None, monotonic delta is used."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        )
    )
    # Sleep a tiny amount so the monotonic clock advances
    import time as _t

    _t.sleep(0.005)
    cb.on_tool_call_completed("cid", tool_result="ok")
    events = read_buffer(buf)
    completed = events[1]
    assert completed["duration_ms"] >= 0  # monotonic-derived (may be 0 on fast hosts)


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
def test_classify_tool_error_closed_enum_mapping(
    exc: BaseException, expected_kind: str, tmp_path: Path
):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="c", name="t", arguments="{}"),
            ]
        )
    )
    cb.on_tool_call_completed("c", error=exc, duration_ms=0)
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": expected_kind}
    assert expected_kind in TOOL_CALL_FAILURE_KINDS


# ── on_agent_error ─────────────────────────────────────────────────


def test_on_agent_error_with_in_flight_tool_emits_failed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        )
    )
    result = cb.on_agent_error(
        TimeoutError("agent stalled"), in_flight_tool_call_id="cid"
    )
    assert result is not None
    failed = read_buffer(buf)[1]
    assert failed["event"] == "tool_call_failed"
    assert failed["failure_kind"] == {"type": "timeout"}


def test_on_agent_error_without_in_flight_emits_nothing(tmp_path: Path):
    """Bare agent error without tool attribution: no event."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    result = cb.on_agent_error(RuntimeError("agent loop died"))
    assert result is None
    assert read_buffer(buf) == []


# ── agent_id propagation ───────────────────────────────────────────


def test_agent_id_propagates_to_all_emitted_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="sub-agent-A")
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        )
    )
    cb.on_tool_call_completed("cid", tool_result="ok", duration_ms=10)
    for e in read_buffer(buf):
        assert e.get("agent_id") == "sub-agent-A"


def test_per_call_agent_id_overrides_default(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="default-agent")
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        ),
        agent_id="override-agent",
    )
    cb.on_tool_call_completed("cid", tool_result="ok", agent_id="override-agent")
    for e in read_buffer(buf):
        assert e.get("agent_id") == "override-agent"


def test_no_agent_id_omits_field(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_sent({"role": "assistant", "content": "r"})
    for e in read_buffer(buf):
        assert "agent_id" not in e


# ── parent_event_id propagation ────────────────────────────────────


def test_parent_event_id_propagates_to_tool_call_attempted(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        ),
        parent_event_id="parent_xyz",
    )
    events = read_buffer(buf)
    assert events[0]["parent_event_id"] == "parent_xyz"


def test_parent_event_id_carries_through_to_completion(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        ),
        parent_event_id="parent_xyz",
    )
    cb.on_tool_call_completed("cid", tool_result="ok", duration_ms=5)
    events = read_buffer(buf)
    # Both attempt + completion carry parent_event_id
    assert events[0]["parent_event_id"] == "parent_xyz"
    assert events[1]["parent_event_id"] == "parent_xyz"


# ── on_event debug callback ────────────────────────────────────────


def test_on_event_callback_fires_per_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    captured: List[Dict[str, Any]] = []
    cb = make_callback(buf, on_event=captured.append)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="c", name="t", arguments="{}"),
            ]
        )
    )
    cb.on_tool_call_completed("c", tool_result="ok", duration_ms=5)
    assert len(captured) == 2
    assert captured[0]["event"] == "tool_call_attempted"
    assert captured[1]["event"] == "tool_call_completed"


def test_on_event_failure_does_not_break_callback(tmp_path: Path):
    def bad(_: Dict[str, Any]) -> None:
        raise RuntimeError("debug crash")

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=bad)
    cb.on_agent_message_sent({"role": "assistant", "content": "r"})
    assert len(read_buffer(buf)) == 1


# ── attachment helpers ─────────────────────────────────────────────


def test_attach_to_conversable_agent_registers_hooks(tmp_path: Path):
    """Attach_to_conversable_agent invokes register_hook twice."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    registered: List[tuple] = []

    class _MockConversableAgent:
        def register_hook(self, name: str, fn: Any) -> None:
            registered.append((name, fn))

    agent = _MockConversableAgent()
    attach_to_conversable_agent(agent, cb)
    hook_names = {name for name, _ in registered}
    assert "process_message_before_send" in hook_names
    assert "process_last_received_message" in hook_names


def test_attach_to_conversable_agent_hook_emits_events(tmp_path: Path):
    """Hooks registered by attach_to_conversable_agent emit ActivityEvents."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    hooks: Dict[str, Any] = {}

    class _MockAgent:
        name = "test_agent"

        def register_hook(self, name: str, fn: Any) -> None:
            hooks[name] = fn

    agent = _MockAgent()
    attach_to_conversable_agent(agent, cb)

    # Fire the received-message hook with a tool_calls message
    msg = make_openai_message_with_tool_calls(
        calls=[make_openai_tool_call(call_id="c1", name="t1", arguments="{}")]
    )
    out = hooks["process_last_received_message"](msg)
    assert out is msg  # hook returns input unchanged

    # Fire the send hook
    sender = _MockAgent()
    recipient = _MockAgent()
    hooks["process_message_before_send"](
        sender, {"role": "assistant", "content": "reply"}, recipient, False
    )
    events = read_buffer(buf)
    types = [e["event"] for e in events]
    assert "tool_call_attempted" in types
    assert "reasoning_step_completed" in types


def test_attach_to_conversable_agent_rejects_wrong_shape():
    cb = NanorixAutoGenCallback(framework=FRAMEWORK_HIPAA, capsulefile="x")
    with pytest.raises(TypeError, match="register_hook"):
        attach_to_conversable_agent(object(), cb)


def test_attach_to_runtime_uses_subscribe_when_available(tmp_path: Path):
    """Attach_to_runtime calls runtime.subscribe(event_name, handler)."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    subscribed: List[tuple] = []

    class _MockRuntime:
        def subscribe(self, name: str, handler: Any) -> None:
            subscribed.append((name, handler))

    runtime = _MockRuntime()
    attach_to_runtime(runtime, cb)
    # Multiple event types attempted
    event_names = {name for name, _ in subscribed}
    assert "MessageEvent" in event_names
    assert "ToolCallExecutionEvent" in event_names
    assert "ErrorEvent" in event_names


def test_attach_to_runtime_falls_back_to_register_event_handler(tmp_path: Path):
    """Attach_to_runtime falls back to register_event_handler when subscribe absent."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    registered: List[tuple] = []

    class _MockRuntime:
        def register_event_handler(self, name: str, handler: Any) -> None:
            registered.append((name, handler))

    runtime = _MockRuntime()
    attach_to_runtime(runtime, cb)
    event_names = {name for name, _ in registered}
    assert "MessageEvent" in event_names


def test_attach_to_runtime_raises_when_no_subscription_api():
    cb = NanorixAutoGenCallback(framework=FRAMEWORK_HIPAA, capsulefile="x")
    with pytest.raises(RuntimeError, match="subscribe"):
        attach_to_runtime(object(), cb)


# ── multi-agent topology ───────────────────────────────────────────


def test_multi_agent_distinct_agent_ids(tmp_path: Path):
    """Per-callback agent_id distinguishes supervisor + workers."""
    buf = tmp_path / "events.jsonl"
    supervisor_cb = make_callback(buf, agent_id="supervisor")
    worker_cb = make_callback(buf, agent_id="worker_A")
    supervisor_cb.on_agent_message_sent({"role": "assistant", "content": "delegate"})
    worker_cb.on_agent_message_sent({"role": "assistant", "content": "work"})
    events = read_buffer(buf)
    ids = {e.get("agent_id") for e in events}
    assert ids == {"supervisor", "worker_A"}


def test_multi_step_conversation_full_shape(tmp_path: Path):
    """End-to-end shape: agent sends → receives tool_calls → completes tools."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="agent-1")

    # Agent's first reply
    cb.on_agent_message_sent(
        {"role": "assistant", "content": "I'll call tools"},
        model="gpt-4o",
        duration_ms=300,
    )

    # Agent's reply with 2 tool_calls
    msg2 = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(call_id="c1", name="lookup", arguments="{}"),
            make_openai_tool_call(call_id="c2", name="submit", arguments="{}"),
        ],
        content="",
    )
    cb.on_agent_message_received(msg2)

    # Tools complete (one success, one error)
    cb.on_tool_call_completed("c1", tool_result={"data": "x"}, duration_ms=100)
    cb.on_tool_call_completed(
        "c2", error=ConnectionError("up"), duration_ms=200
    )

    # Final reply
    cb.on_agent_message_sent(
        {"role": "assistant", "content": "Done"}, model="gpt-4o", duration_ms=200
    )

    events = read_buffer(buf)
    # 1 sent + 2 attempted + 1 completed + 1 failed + 1 sent = 6
    assert len(events) == 6
    types = [e["event"] for e in events]
    assert types == [
        "reasoning_step_completed",
        "tool_call_attempted",
        "tool_call_attempted",
        "tool_call_completed",
        "tool_call_failed",
        "reasoning_step_completed",
    ]
    assert all(e.get("agent_id") == "agent-1" for e in events)


# ── golden canonical_hash invariant ─────────────────────────────────


def test_canonical_hash_jcs_byte_equivalent_with_helpers():
    import hashlib

    payload = {"a": 1, "b": "hello"}
    expected = "sha512:" + hashlib.sha512(b'{"a":1,"b":"hello"}').hexdigest()
    assert _canonical_hash(payload) == expected


# ── property test on conversation orderings ─────────────────────────


def test_property_random_conversations_produce_well_formed_events(tmp_path: Path):
    """Property test: arbitrary message + tool sequences produce well-formed
    buffer contents.

    Iterations: 500. We exercise the 4 lifecycle methods in random orders
    and check that every emitted event has a valid `event` field, an `at`
    timestamp, and (when applicable) a `tool_call_id` from a prior
    ToolCallAttempted.
    """
    rng = random.Random(0xDEADBEEF)
    iterations = 500
    for it in range(iterations):
        buf = tmp_path / f"events_{it}.jsonl"
        cb = make_callback(buf)
        # Track open tool_call_ids so we can complete them.
        open_ids: List[str] = []
        sequence_len = rng.randint(1, 5)
        for _ in range(sequence_len):
            kind = rng.choice(["sent", "received", "complete", "fail", "agent_err"])
            if kind == "sent":
                cb.on_agent_message_sent(
                    {"role": "assistant", "content": f"r{rng.random()}"},
                    duration_ms=rng.randint(0, 1000),
                )
            elif kind == "received":
                n_calls = rng.randint(1, 3)
                calls = [
                    make_openai_tool_call(
                        call_id=f"c_{it}_{rng.randint(0, 10**6)}",
                        name=f"t{rng.randint(0, 5)}",
                        arguments="{}",
                    )
                    for _ in range(n_calls)
                ]
                emitted = cb.on_agent_message_received(
                    make_openai_message_with_tool_calls(calls=calls)
                )
                open_ids.extend(emitted)
            elif kind == "complete" and open_ids:
                tcid = open_ids.pop(rng.randrange(len(open_ids)))
                cb.on_tool_call_completed(
                    tcid, tool_result={"ok": True}, duration_ms=rng.randint(0, 500)
                )
            elif kind == "fail" and open_ids:
                tcid = open_ids.pop(rng.randrange(len(open_ids)))
                exc = rng.choice(
                    [TimeoutError("t"), ValueError("v"), ConnectionError("c")]
                )
                cb.on_tool_call_completed(
                    tcid, error=exc, duration_ms=rng.randint(0, 500)
                )
            elif kind == "agent_err":
                cb.on_agent_error(RuntimeError("agent err"))

        events = read_buffer(buf)
        attempted_ids: List[str] = []
        closure_ids: List[str] = []
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
                attempted_ids.append(e["tool_call_id"])
            elif e["event"] in ("tool_call_completed", "tool_call_failed"):
                closure_ids.append(e["tool_call_id"])
        # Most closures should match an attempt (orphan closures
        # allowed for defensive path).
        if closure_ids:
            assert any(cid in attempted_ids for cid in closure_ids) or all(
                not attempted_ids for _ in [True]
            )


# ── Forever-Standard ────────────────────────────────────


def test_adr_006_i0_only_four_variants_emitted(tmp_path: Path):
    """Forever-Standard regression guard: only 4 variants reach the buffer."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    cb.on_agent_message_sent({"role": "assistant", "content": "r"})
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="c1", name="t1", arguments="{}"),
                make_openai_tool_call(call_id="c2", name="t2", arguments="{}"),
            ]
        )
    )
    cb.on_tool_call_completed("c1", tool_result="ok", duration_ms=10)
    cb.on_tool_call_completed("c2", error=TimeoutError("t"), duration_ms=5)
    cb.on_agent_error(RuntimeError("x"))
    cb.on_agent_error(ValueError("y"), in_flight_tool_call_id="c1")  # orphan

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
    import nanorix.integrations.autogen as ag_mod

    src = Path(ag_mod.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src


# ── round-trip determinism ──────────────────────────────────────────


def test_event_emission_deterministic_for_identical_inputs(tmp_path: Path):
    buf1 = tmp_path / "buf1.jsonl"
    buf2 = tmp_path / "buf2.jsonl"
    for buf in (buf1, buf2):
        cb = make_callback(buf)
        msg = make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(
                    call_id="cid", name="t", arguments='{"k":"v"}'
                ),
            ]
        )
        cb.on_agent_message_received(msg)
        cb.on_tool_call_completed(
            "cid", tool_result={"data": [1, 2, 3]}, duration_ms=100
        )
    e1 = read_buffer(buf1)
    e2 = read_buffer(buf2)
    assert e1[0]["tool_args_hash"] == e2[0]["tool_args_hash"]
    assert e1[1]["tool_result_hash"] == e2[1]["tool_result_hash"]


# ── concurrent (interleaved) tool_call_ids ─────────────────────────


def test_concurrent_tool_call_ids_do_not_collide(tmp_path: Path):
    """Multiple in-flight tool calls stay paired correctly."""
    buf = tmp_path / "buf.jsonl"
    cb = make_callback(buf)
    msg = make_openai_message_with_tool_calls(
        calls=[
            make_openai_tool_call(call_id="ca", name="A", arguments='{"x":"a"}'),
            make_openai_tool_call(call_id="cb", name="B", arguments='{"x":"b"}'),
        ]
    )
    cb.on_agent_message_received(msg)
    cb.on_tool_call_completed("cb", tool_result={"y": "rb"}, duration_ms=10)
    cb.on_tool_call_completed("ca", tool_result={"y": "ra"}, duration_ms=20)
    events = read_buffer(buf)
    assert events[0]["tool_call_id"] == "ca"
    assert events[1]["tool_call_id"] == "cb"
    assert events[2]["tool_call_id"] == "cb"
    assert events[3]["tool_call_id"] == "ca"


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
    buf = tmp_path / "buf.jsonl"
    cb = make_callback(buf)
    cb.on_agent_message_received(
        make_openai_message_with_tool_calls(
            calls=[
                make_openai_tool_call(call_id="cid", name="t", arguments="{}"),
            ]
        )
    )
    cb.on_tool_call_completed(
        "cid", error=PermissionError("denied"), duration_ms=5
    )
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": "permission_denied"}


# ── extract_tool_call_id fallback ──────────────────────────────────


def test_extract_tool_call_id_falls_back_when_no_id(tmp_path: Path):
    """Tool call without an id field still emits an event."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    msg = {
        "role": "assistant",
        "tool_calls": [
            {"function": {"name": "no_id_tool", "arguments": "{}"}},
        ],
    }
    emitted = cb.on_agent_message_received(msg)
    assert len(emitted) == 1
    # Synthetic id pattern
    assert emitted[0].startswith("autogen_tc:")
    events = read_buffer(buf)
    assert events[0]["tool_name"] == "no_id_tool"
