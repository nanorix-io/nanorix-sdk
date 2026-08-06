"""Wave 14 / item 7 — LiteLLM callback handler tests.

Mirrors `test_langgraph_integration.py`. Exercises the
`nanorix.integrations.litellm.NanorixLiteLLMCallback` class against:

- Synthetic litellm-shape events (no real provider calls; no network)
- Real `litellm.integrations.custom_logger.CustomLogger` interface
- Hook-to-ActivityEvent mapping (4 variants only)
- Sync + async hook paths populate the SAME activity_trail buffer
- Function-calling response → ToolCallAttempted per tool_call entry
- Closed-enum classification for `ToolCallFailureKind` (when applicable)
- Canonical-JSON byte-equivalence with `_canonical_hash`
- Property tests on call orderings (≥1k iterations)
- Forever-Standard preservation (no new variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / AuditRecord

When `litellm` isn't installed, the entire test module is skipped.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from uuid import uuid4

import pytest

# Skip when optional dep isn't present.
pytest.importorskip("litellm")

from litellm.integrations.custom_logger import CustomLogger  # noqa: E402

from nanorix.helpers.activity_events import (  # noqa: E402
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
)
from nanorix.integrations.litellm import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    NanorixLiteLLMCallback,
)


# ── fixture helpers ────────────────────────────────────────────────


def read_buffer(buf_path: Path) -> List[Dict[str, Any]]:
    if not buf_path.exists():
        return []
    text = buf_path.read_text()
    if not text.strip():
        return []
    return [json.loads(line) for line in text.strip().split("\n")]


def make_callback(buf_path: Path, **overrides: Any) -> NanorixLiteLLMCallback:
    kwargs: Dict[str, Any] = {
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-litellm",
        "buffer_path": str(buf_path),
    }
    kwargs.update(overrides)
    return NanorixLiteLLMCallback(**kwargs)


def make_response(
    *,
    content: str = "OK",
    role: str = "assistant",
    tool_calls: List[Dict[str, Any]] | None = None,
    model: str = "gpt-4o",
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
) -> SimpleNamespace:
    """Build a synthetic litellm ModelResponse using SimpleNamespace.

    Mirrors the shape litellm 1.40+ returns: `.choices[*].message.{role,
    content, tool_calls}`, `.usage.{prompt_tokens, completion_tokens,
    total_tokens}`, `.model`. SimpleNamespace supports attribute access;
    NanorixLiteLLMCallback._safe_attr handles both attr + dict shapes.
    """
    msg_kwargs: Dict[str, Any] = {"role": role, "content": content}
    if tool_calls is not None:
        msg_kwargs["tool_calls"] = [
            SimpleNamespace(
                id=tc["id"],
                type=tc.get("type", "function"),
                function=SimpleNamespace(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
            for tc in tool_calls
        ]
    message = SimpleNamespace(**msg_kwargs)
    choice = SimpleNamespace(
        index=0,
        message=message,
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(
        id=f"chatcmpl-{uuid4().hex[:24]}",
        choices=[choice],
        usage=usage,
        model=model,
    )


def now_pair() -> tuple[datetime, datetime]:
    """Realistic start/end datetime pair (~50ms apart)."""
    start = datetime.now(timezone.utc)
    end = start + timedelta(milliseconds=50)
    return start, end


# ── inheritance + construction ──────────────────────────────────────


def test_inherits_litellm_custom_logger():
    """Forever-Standard: NanorixLiteLLMCallback IS a CustomLogger."""
    cb = NanorixLiteLLMCallback(framework=FRAMEWORK_HIPAA, capsulefile="test")
    assert isinstance(cb, CustomLogger)


def test_construction_records_required_args():
    cb = NanorixLiteLLMCallback(framework=FRAMEWORK_HIPAA, capsulefile="agent-x")
    assert cb.framework == "HIPAA"
    assert cb.capsulefile == "agent-x"


def test_construction_with_optional_args():
    cb = NanorixLiteLLMCallback(
        framework=FRAMEWORK_GDPR,
        capsulefile="agent-y",
        api_key="nrx_live_test",
        metadata={"trace_id": "abc"},
        framework_version="2026-02",
        agent_id="agent-y-sub-1",
        emit_tool_request_events=False,
    )
    assert cb.api_key == "nrx_live_test"
    assert cb.metadata == {"trace_id": "abc"}
    assert cb.framework_version == "2026-02"
    assert cb.agent_id == "agent-y-sub-1"
    assert cb.emit_tool_request_events is False


def test_construction_rejects_missing_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLiteLLMCallback(framework="", capsulefile="x")


def test_construction_rejects_none_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLiteLLMCallback(framework=None, capsulefile="x")  # type: ignore[arg-type]


def test_construction_rejects_missing_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixLiteLLMCallback(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    cb = NanorixLiteLLMCallback(framework=FRAMEWORK_SOC2, capsulefile="x")
    assert cb.api_key == "env_value_xyz"


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value")
    cb = NanorixLiteLLMCallback(
        framework=FRAMEWORK_SOC2, capsulefile="x", api_key="explicit_value"
    )
    assert cb.api_key == "explicit_value"


def test_allowed_frameworks_contains_required_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS
    cb = NanorixLiteLLMCallback(framework="CUSTOM_FRAMEWORK", capsulefile="x")
    assert cb.framework == "CUSTOM_FRAMEWORK"


# ── log_success_event emits ReasoningStepCompleted ──────────────────


def test_log_success_event_emits_reasoning_step_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hello"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    start, end = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(content="hi"),
        start_time=start,
        end_time=end,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "reasoning_step_completed"
    assert e["step_idx"] == 0
    assert e["model"] == "gpt-4o"
    assert e["prompt_hash"].startswith("sha512:")
    assert e["response_hash"].startswith("sha512:")
    assert e["duration_ms"] >= 0


def test_log_success_event_records_provider_prefixed_model(tmp_path: Path):
    """Anthropic / Bedrock / Vertex models keep their prefix as recorded."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "anthropic/claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "hi"}],
    }
    cb.log_pre_api_call(
        model=kwargs["model"], messages=kwargs["messages"], kwargs=kwargs
    )
    start, end = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(model=kwargs["model"]),
        start_time=start,
        end_time=end,
    )
    e = read_buffer(buf)[0]
    assert e["model"] == "anthropic/claude-3-5-sonnet-20241022"


def test_log_success_event_step_idx_increments(tmp_path: Path):
    """Step_idx is monotonic per callback instance across calls."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for i in range(5):
        call_id = uuid4().hex
        kwargs = {
            "litellm_call_id": call_id,
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": f"msg_{i}"}],
        }
        cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
        s, e = now_pair()
        cb.log_success_event(
            kwargs=kwargs,
            response_obj=make_response(content=f"r_{i}"),
            start_time=s,
            end_time=e,
        )
    events = read_buffer(buf)
    assert [e["step_idx"] for e in events] == [0, 1, 2, 3, 4]


def test_log_success_event_without_pre_api_call(tmp_path: Path):
    """Defensive: if log_pre_api_call wasn't fired (rare), still emit.

    Duration_ms falls back to end_time - start_time computation.
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    kwargs = {
        "litellm_call_id": "no-pre",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"
    assert events[0]["duration_ms"] >= 0


def test_log_success_event_handles_missing_call_id(tmp_path: Path):
    """If kwargs doesn't carry litellm_call_id, synthesize one defensively."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    kwargs = {"model": "gpt-4o", "messages": []}
    cb.log_pre_api_call(model="gpt-4o", messages=[], kwargs=kwargs)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    assert len(events) == 1


# ── log_failure_event emits ReasoningStepCompleted (failure shape) ──


def test_log_failure_event_emits_reasoning_step_completed(tmp_path: Path):
    """LLM-level failures emit ReasoningStepCompleted (NOT ToolCallFailed).

    Per the integration discipline: LLM failures are reasoning faults,
    not tool faults. Distinguishing them in the activity_trail
    preserves auditor semantics.
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "exception": TimeoutError("upstream timed out"),
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    s, e = now_pair()
    cb.log_failure_event(
        kwargs=kwargs,
        response_obj=None,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e_evt = events[0]
    assert e_evt["event"] == "reasoning_step_completed"
    # The response_hash should reflect a failure-shape payload (different
    # from a success-shape payload's hash).
    assert e_evt["response_hash"].startswith("sha512:")
    # No ToolCallFailed event was emitted (LLM-level errors are reasoning
    # faults, not tool faults).
    assert all(e["event"] != "tool_call_failed" for e in events)


def test_log_failure_event_carries_failure_reason_in_response_hash(tmp_path: Path):
    """Failure shape includes failure_reason; success shape doesn't.

    Two calls with identical prompts but different outcomes (success vs
    failure with the same underlying message) MUST produce different
    response_hash values — the failure marker discriminates.
    """
    buf_ok = tmp_path / "ok.jsonl"
    buf_fail = tmp_path / "fail.jsonl"
    cb_ok = make_callback(buf_ok)
    cb_fail = make_callback(buf_fail)

    call_id_ok = uuid4().hex
    call_id_fail = uuid4().hex
    kwargs_ok = {
        "litellm_call_id": call_id_ok,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    kwargs_fail = {
        "litellm_call_id": call_id_fail,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "exception": TimeoutError("upstream timed out"),
    }

    cb_ok.log_pre_api_call(model="gpt-4o", messages=kwargs_ok["messages"], kwargs=kwargs_ok)
    cb_fail.log_pre_api_call(
        model="gpt-4o", messages=kwargs_fail["messages"], kwargs=kwargs_fail
    )
    s, e = now_pair()
    cb_ok.log_success_event(
        kwargs=kwargs_ok,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    cb_fail.log_failure_event(
        kwargs=kwargs_fail,
        response_obj=None,
        start_time=s,
        end_time=e,
    )

    e_ok = read_buffer(buf_ok)[0]
    e_fail = read_buffer(buf_fail)[0]
    assert e_ok["response_hash"] != e_fail["response_hash"]


def test_log_failure_event_without_pre_api_call(tmp_path: Path):
    """Defensive: failure without pre_api_call still emits."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    kwargs = {
        "litellm_call_id": "x",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    s, e = now_pair()
    cb.log_failure_event(
        kwargs=kwargs,
        response_obj=None,
        start_time=s,
        end_time=e,
    )
    assert len(read_buffer(buf)) == 1


# ── function-calling: tool_calls in response → ToolCallAttempted ───


def test_tool_calls_in_response_emit_tool_call_attempted(tmp_path: Path):
    """Function-calling response → one ToolCallAttempted per tool_call."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "look up patient"}],
        "tools": [
            {"type": "function", "function": {"name": "lab_result_fetch"}},
        ],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        content="",
        tool_calls=[
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "lab_result_fetch",
                    "arguments": '{"member_id": "X"}',
                },
            }
        ],
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    # 1 ReasoningStep + 1 ToolCallAttempted = 2
    assert len(events) == 2
    types = [ev["event"] for ev in events]
    assert types == ["reasoning_step_completed", "tool_call_attempted"]
    tc_evt = events[1]
    assert tc_evt["tool_call_id"] == "call_abc123"
    assert tc_evt["tool_name"] == "lab_result_fetch"
    # The arguments are a JSON string (OpenAI shape); _canonical_hash
    # hashes the string verbatim.
    assert tc_evt["tool_args_hash"] == _canonical_hash('{"member_id": "X"}')
    # parent_event_id ties the tool request back to the LLM call.
    assert tc_evt["parent_event_id"] == call_id


def test_multiple_tool_calls_emit_one_event_each(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "do two things"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        tool_calls=[
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "tool_a", "arguments": "{}"},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "tool_b", "arguments": '{"x": 1}'},
            },
        ]
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    # 1 reasoning + 2 tool_call_attempted = 3
    assert len(events) == 3
    tool_events = [ev for ev in events if ev["event"] == "tool_call_attempted"]
    assert {ev["tool_name"] for ev in tool_events} == {"tool_a", "tool_b"}


def test_emit_tool_request_events_false_suppresses(tmp_path: Path):
    """Emit_tool_request_events=False suppresses tool_call_attempted."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, emit_tool_request_events=False)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        tool_calls=[
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }
        ]
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    # Only the ReasoningStep — no tool events
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"


def test_response_without_tool_calls_emits_only_reasoning_step(tmp_path: Path):
    """Standard text completion → 1 ReasoningStep, 0 tool events."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(content="hello back"),
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"


# ── async hooks populate the same buffer ────────────────────────────


def test_async_log_success_emits_same_shape(tmp_path: Path):
    """Async_log_success_event populates the same activity_trail buffer."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "async hi"}],
    }

    async def run():
        await cb.async_log_pre_api_call(
            model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs
        )
        s, e = now_pair()
        await cb.async_log_success_event(
            kwargs=kwargs,
            response_obj=make_response(),
            start_time=s,
            end_time=e,
        )

    asyncio.run(run())
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"


def test_async_log_failure_emits_failure_shape(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "exception": ConnectionError("provider down"),
    }

    async def run():
        await cb.async_log_pre_api_call(
            model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs
        )
        s, e = now_pair()
        await cb.async_log_failure_event(
            kwargs=kwargs,
            response_obj=None,
            start_time=s,
            end_time=e,
        )

    asyncio.run(run())
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"


def test_async_tool_calls_emit_tool_call_attempted(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "use tool"}],
    }

    async def run():
        await cb.async_log_pre_api_call(
            model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs
        )
        response = make_response(
            tool_calls=[
                {
                    "id": "tc-a",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ]
        )
        s, e = now_pair()
        await cb.async_log_success_event(
            kwargs=kwargs,
            response_obj=response,
            start_time=s,
            end_time=e,
        )

    asyncio.run(run())
    events = read_buffer(buf)
    assert [ev["event"] for ev in events] == [
        "reasoning_step_completed",
        "tool_call_attempted",
    ]


# ── on_event debug callback ────────────────────────────────────────


def test_on_event_callback_fires_per_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    captured: List[Dict[str, Any]] = []
    cb = make_callback(buf, on_event=captured.append)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    assert len(captured) == 1
    assert captured[0]["event"] == "reasoning_step_completed"


def test_on_event_failure_does_not_break_callback(tmp_path: Path):
    """Bad on_event hook crashes silently; callback continues to write."""

    def bad(_: Dict[str, Any]) -> None:
        raise RuntimeError("debug crash")

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=bad)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    assert len(read_buffer(buf)) == 1


# ── agent_id propagation ───────────────────────────────────────────


def test_agent_id_propagates_to_all_emitted_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="sub-agent-A")
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        tool_calls=[
            {
                "id": "tc1",
                "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }
        ]
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    for ev in read_buffer(buf):
        assert ev.get("agent_id") == "sub-agent-A"


def test_no_agent_id_omits_field(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=make_response(),
        start_time=s,
        end_time=e,
    )
    for ev in read_buffer(buf):
        assert "agent_id" not in ev


# ── parent_event_id chaining: tool_calls reference the LLM call ────


def test_tool_call_parent_event_id_is_llm_call_id(tmp_path: Path):
    """ToolCallAttempted's parent_event_id == litellm_call_id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        tool_calls=[
            {
                "id": "tc-1",
                "type": "function",
                "function": {"name": "t", "arguments": "{}"},
            }
        ]
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    tc_evt = next(ev for ev in events if ev["event"] == "tool_call_attempted")
    assert tc_evt["parent_event_id"] == call_id


# ── multi-call agent shape ──────────────────────────────────────────


def test_full_multi_call_agent_shape(tmp_path: Path):
    """Synthetic 2-iteration agent: LLM → tool_call → LLM (final).

    Mirrors the LangChain integration's full_agent_loop_shape test, but
    via the litellm callback surface (function-calling response on the
    first LLM call, plain-text on the second).
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="agent-litellm-1")

    # Call 1: LLM returns tool_call request
    call1_id = uuid4().hex
    kw1 = {
        "litellm_call_id": call1_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "task definition"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw1["messages"], kwargs=kw1)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kw1,
        response_obj=make_response(
            content="",
            tool_calls=[
                {
                    "id": "call_lab",
                    "type": "function",
                    "function": {
                        "name": "lab_result_fetch",
                        "arguments": '{"member_id": "M1"}',
                    },
                }
            ],
        ),
        start_time=s,
        end_time=e,
    )

    # Call 2: LLM returns final answer (no tool_calls)
    call2_id = uuid4().hex
    kw2 = {
        "litellm_call_id": call2_id,
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "task"},
            {"role": "tool", "content": '{"loinc": "2160-0"}'},
        ],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw2["messages"], kwargs=kw2)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kw2,
        response_obj=make_response(content="final answer"),
        start_time=s,
        end_time=e,
    )

    events = read_buffer(buf)
    # Call 1: ReasoningStep + ToolCallAttempted
    # Call 2: ReasoningStep
    # Total = 3
    assert len(events) == 3
    types = [ev["event"] for ev in events]
    assert types == [
        "reasoning_step_completed",
        "tool_call_attempted",
        "reasoning_step_completed",
    ]
    reasoning = [ev for ev in events if ev["event"] == "reasoning_step_completed"]
    assert [r["step_idx"] for r in reasoning] == [0, 1]
    assert all(ev.get("agent_id") == "agent-litellm-1" for ev in events)


# ── golden canonical_hash invariant ─────────────────────────────────


def test_canonical_hash_jcs_byte_equivalent_with_helpers():
    import hashlib

    payload = {"a": 1, "b": "hello"}
    expected = "sha512:" + hashlib.sha512(b'{"a":1,"b":"hello"}').hexdigest()
    assert _canonical_hash(payload) == expected




# ── property test on call orderings ─────────────────────────────────


def test_property_random_call_orderings_produce_well_formed_events(tmp_path: Path):
    """Property test: arbitrary success/failure/tool-call sequences produce
    well-formed buffer contents.

    Iterations: 1_000 (mirrors test_langgraph_integration.py iteration count
    per anti-crash budget — well-formed property holds at smaller N for
    smaller surface).
    """
    rng = random.Random(0xCAFE_BAB1)
    iterations = 1_000
    for it in range(iterations):
        buf = tmp_path / f"events_{it}.jsonl"
        cb = make_callback(buf)
        sequence_len = rng.randint(1, 5)
        for _ in range(sequence_len):
            kind = rng.choice(["success", "tool_call", "failure"])
            call_id = uuid4().hex
            kwargs = {
                "litellm_call_id": call_id,
                "model": rng.choice(["gpt-4o", "claude-3-5-sonnet-20241022"]),
                "messages": [
                    {"role": "user", "content": f"msg_{rng.random()}"}
                ],
            }
            cb.log_pre_api_call(
                model=kwargs["model"], messages=kwargs["messages"], kwargs=kwargs
            )
            s, e = now_pair()
            if kind == "success":
                cb.log_success_event(
                    kwargs=kwargs,
                    response_obj=make_response(content=f"r_{rng.random()}"),
                    start_time=s,
                    end_time=e,
                )
            elif kind == "tool_call":
                cb.log_success_event(
                    kwargs=kwargs,
                    response_obj=make_response(
                        tool_calls=[
                            {
                                "id": f"tc_{rng.randint(0, 99)}",
                                "type": "function",
                                "function": {
                                    "name": f"tool_{rng.randint(0, 5)}",
                                    "arguments": f'{{"k": {rng.random()}}}',
                                },
                            }
                        ]
                    ),
                    start_time=s,
                    end_time=e,
                )
            else:
                kwargs["exception"] = rng.choice(
                    [
                        TimeoutError("t"),
                        ConnectionError("c"),
                        ValueError("v"),
                    ]
                )
                cb.log_failure_event(
                    kwargs=kwargs,
                    response_obj=None,
                    start_time=s,
                    end_time=e,
                )

        events = read_buffer(buf)
        for ev in events:
            assert isinstance(ev, dict)
            assert ev.get("event") in {
                "tool_call_attempted",
                "tool_call_completed",
                "tool_call_failed",
                "reasoning_step_completed",
            }
            assert "at" in ev


# ── Forever-Standard ────────────────────────────────────


def test_adr_006_i0_only_four_variants_emitted(tmp_path: Path):
    """Forever-Standard regression guard: only 4 variants reach the buffer."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    # Drive every code path that writes to the buffer.
    # 1. Plain success
    call1 = uuid4().hex
    kw1 = {
        "litellm_call_id": call1,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw1["messages"], kwargs=kw1)
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kw1, response_obj=make_response(), start_time=s, end_time=e
    )
    # 2. Success with tool_calls
    call2 = uuid4().hex
    kw2 = {
        "litellm_call_id": call2,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw2["messages"], kwargs=kw2)
    cb.log_success_event(
        kwargs=kw2,
        response_obj=make_response(
            tool_calls=[
                {
                    "id": "tc-a",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ]
        ),
        start_time=s,
        end_time=e,
    )
    # 3. Failure
    call3 = uuid4().hex
    kw3 = {
        "litellm_call_id": call3,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "exception": TimeoutError("t"),
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw3["messages"], kwargs=kw3)
    cb.log_failure_event(
        kwargs=kw3, response_obj=None, start_time=s, end_time=e
    )

    events = read_buffer(buf)
    seen = {ev["event"] for ev in events}
    allowed = {
        "tool_call_attempted",
        "tool_call_completed",
        "tool_call_failed",
        "reasoning_step_completed",
    }
    assert seen <= allowed, f"Forever-Standard violation: {seen - allowed}"


# ── vocabulary discipline ───────────────────────────────────────────


def test_module_vocabulary_uses_auditproof_terminology():
    """Module source uses AuditProof + Capsulefile per CONVENTIONS.md.

    Deprecated terms (Capsule Record, Cryptographic Destruction Proof)
    must not appear in customer-facing strings.
    """
    import nanorix.integrations.litellm as ll_mod

    src = Path(ll_mod.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src


# ── round-trip determinism ──────────────────────────────────────────


def test_event_emission_deterministic_for_identical_inputs(tmp_path: Path):
    """Same prompts + responses → identical hashes (cross-impl invariant)."""
    buf1 = tmp_path / "buf1.jsonl"
    buf2 = tmp_path / "buf2.jsonl"
    for buf in (buf1, buf2):
        cb = make_callback(buf)
        call_id = uuid4().hex
        kw = {
            "litellm_call_id": call_id,
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "hello"},
            ],
        }
        cb.log_pre_api_call(model="gpt-4o", messages=kw["messages"], kwargs=kw)
        s, e = now_pair()
        cb.log_success_event(
            kwargs=kw,
            response_obj=make_response(content="resp"),
            start_time=s,
            end_time=e,
        )
    e1 = read_buffer(buf1)[0]
    e2 = read_buffer(buf2)[0]
    assert e1["prompt_hash"] == e2["prompt_hash"]
    assert e1["response_hash"] == e2["response_hash"]


def test_event_emission_distinguishes_distinct_messages(tmp_path: Path):
    """Different prompts → different prompt_hashes (sanity)."""
    buf = tmp_path / "buf.jsonl"
    cb = make_callback(buf)
    for content in ("hello", "world"):
        call_id = uuid4().hex
        kw = {
            "litellm_call_id": call_id,
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": content}],
        }
        cb.log_pre_api_call(model="gpt-4o", messages=kw["messages"], kwargs=kw)
        s, e = now_pair()
        cb.log_success_event(
            kwargs=kw,
            response_obj=make_response(),
            start_time=s,
            end_time=e,
        )
    events = read_buffer(buf)
    assert events[0]["prompt_hash"] != events[1]["prompt_hash"]


# ── concurrent (interleaved) call_ids ──────────────────────────────


def test_concurrent_call_ids_do_not_collide(tmp_path: Path):
    """Multiple in-flight calls with distinct litellm_call_ids stay paired."""
    buf = tmp_path / "buf.jsonl"
    cb = make_callback(buf)
    call_a = uuid4().hex
    call_b = uuid4().hex

    kw_a = {
        "litellm_call_id": call_a,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "A"}],
    }
    kw_b = {
        "litellm_call_id": call_b,
        "model": "claude-3-5",
        "messages": [{"role": "user", "content": "B"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kw_a["messages"], kwargs=kw_a)
    cb.log_pre_api_call(model="claude-3-5", messages=kw_b["messages"], kwargs=kw_b)

    s, e = now_pair()
    # Close B first, then A — order preserved by call_id
    cb.log_success_event(
        kwargs=kw_b, response_obj=make_response(model="claude-3-5"),
        start_time=s, end_time=e,
    )
    cb.log_success_event(
        kwargs=kw_a, response_obj=make_response(model="gpt-4o"),
        start_time=s, end_time=e,
    )

    events = read_buffer(buf)
    assert events[0]["model"] == "claude-3-5"
    assert events[1]["model"] == "gpt-4o"


# ── existing helpers reachable ─────────────────────────────────────


def test_existing_helpers_still_reachable_via_module():
    """Forever-Standard regression: existing helpers remain importable.

    The 4-variant set is unchanged; this module reuses, never shadows.
    """
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


def test_emit_tool_helpers_reexported_for_downstream_use():
    """Customer's downstream agent code needs `emit_tool_call_completed` /
    `emit_tool_call_failed` to record the tool's actual execution outcome
    (the litellm callback only records the LLM's tool REQUEST). Re-exporting
    them from the litellm module is a convenience."""
    from nanorix.integrations.litellm import (
        emit_tool_call_completed,
        emit_tool_call_failed,
    )

    assert callable(emit_tool_call_completed)
    assert callable(emit_tool_call_failed)


# ── litellm dict-shape kwargs (older versions / proxy mode) ────────


def test_kwargs_as_plain_dict_works(tmp_path: Path):
    """Litellm sometimes passes kwargs as plain dict (not a Pydantic model).

    The callback's _safe_attr handles both attribute and dict-key access.
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    # Shape kwargs as plain dict (Mapping)
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    # Build a response shape as plain dicts (some versions)
    response_dict = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "model": "gpt-4o",
    }
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response_dict,
        start_time=s,
        end_time=e,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["event"] == "reasoning_step_completed"
    assert events[0]["model"] == "gpt-4o"


# ── exception swallowing: callback must not crash customer's call path ─


def test_log_success_event_never_raises_on_malformed_response(tmp_path: Path):
    """If response_obj is wildly malformed, callback swallows the error.

    Critical discipline: a customer's `litellm.completion(...)` must NEVER
    fail because of a Nanorix callback bug. The callback wraps every
    public hook in a try/except.
    """
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    kwargs = {
        "litellm_call_id": "x",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)

    # Object that explodes on .choices access
    class BombResponse:
        @property
        def choices(self):
            raise RuntimeError("boom")

        model = "gpt-4o"

        usage = None

    s, e = now_pair()
    # Must NOT raise
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=BombResponse(),
        start_time=s,
        end_time=e,
    )
    # Buffer may have partial event; the discipline is "never crash the
    # call path", not "always emit a perfect event".


def test_log_pre_api_call_never_raises_on_bad_kwargs(tmp_path: Path):
    """Log_pre_api_call must never crash even with garbage kwargs."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    # None kwargs (unusual but possible in test harnesses)
    cb.log_pre_api_call(model="x", messages=[], kwargs=None)
    # Buffer should be empty (pre_api_call writes nothing)
    assert read_buffer(buf) == []


# ── failure_kind shape on tool-call surface (when applicable) ──────


def test_tool_call_args_hash_matches_arguments_string(tmp_path: Path):
    """The OpenAI shape: arguments is a JSON STRING. Hash matches the
    string verbatim (canonical-JSON-of-string is the string itself
    quoted + serialized — _canonical_hash handles this)."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    call_id = uuid4().hex
    kwargs = {
        "litellm_call_id": call_id,
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
    }
    cb.log_pre_api_call(model="gpt-4o", messages=kwargs["messages"], kwargs=kwargs)
    response = make_response(
        tool_calls=[
            {
                "id": "tc",
                "type": "function",
                "function": {
                    "name": "t",
                    "arguments": '{"a":1,"b":"hello"}',
                },
            }
        ]
    )
    s, e = now_pair()
    cb.log_success_event(
        kwargs=kwargs,
        response_obj=response,
        start_time=s,
        end_time=e,
    )
    tc_evt = next(
        ev for ev in read_buffer(buf) if ev["event"] == "tool_call_attempted"
    )
    expected = _canonical_hash('{"a":1,"b":"hello"}')
    assert tc_evt["tool_args_hash"] == expected
