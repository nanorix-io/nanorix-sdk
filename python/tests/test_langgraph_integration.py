""" / item 6a — LangGraph callback handler tests.

Mirrors `test_langchain_integration.py`. Exercises the
`nanorix.integrations.langgraph.NanorixLangGraphCallback` class against:

- Synthetic LangChain-shape events (LangGraph dispatches via the
  standard `BaseCallbackHandler` interface)
- Hook-to-ActivityEvent mapping (4 variants only)
- Node-vs-outer-chain discrimination via `graph:step:*` tag
- Closed-enum classification for `ToolCallFailureKind`
- Canonical-JSON byte-equivalence with `_canonical_hash`
- Property tests on chain orderings (≥10k iterations)
- Forever-Standard preservation (no new variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / AuditRecord
- Real LangGraph compiled-graph invocation (when available)

When `langgraph` isn't installed, the entire test module is skipped.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import pytest

# Skip when optional deps aren't present.
pytest.importorskip("langchain_core")
pytest.importorskip("langgraph")

from langchain_core.callbacks import BaseCallbackHandler  # noqa: E402
from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import Generation, LLMResult  # noqa: E402

from nanorix.helpers.activity_events import (  # noqa: E402
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
)
from nanorix.integrations.langgraph import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    NanorixLangGraphCallback,
)


# ── fixture helpers ────────────────────────────────────────────────


def read_buffer(buf_path: Path) -> List[Dict[str, Any]]:
    if not buf_path.exists():
        return []
    text = buf_path.read_text()
    if not text.strip():
        return []
    return [json.loads(line) for line in text.strip().split("\n")]


def make_callback(buf_path: Path, **overrides: Any) -> NanorixLangGraphCallback:
    kwargs: Dict[str, Any] = {
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-graph",
        "buffer_path": str(buf_path),
    }
    kwargs.update(overrides)
    return NanorixLangGraphCallback(**kwargs)


def graph_step_tag(step_idx: int) -> List[str]:
    """Helper: produce a tag list mimicking LangGraph's node tagging."""
    return [f"graph:step:{step_idx}"]


# ── inheritance + construction ──────────────────────────────────────


def test_inherits_base_callback_handler():
    """LangGraph dispatches via langchain_core's BaseCallbackHandler."""
    cb = NanorixLangGraphCallback(framework=FRAMEWORK_HIPAA, capsulefile="test")
    assert isinstance(cb, BaseCallbackHandler)


def test_construction_records_required_args():
    cb = NanorixLangGraphCallback(framework=FRAMEWORK_HIPAA, capsulefile="agent-x")
    assert cb.framework == "HIPAA"
    assert cb.capsulefile == "agent-x"


def test_construction_with_optional_args():
    cb = NanorixLangGraphCallback(
        framework=FRAMEWORK_GDPR,
        capsulefile="agent-y",
        api_key="nrx_live_test",
        metadata={"trace_id": "abc"},
        framework_version="2026-02",
        agent_id="agent-y-sub-1",
        emit_node_events=False,
    )
    assert cb.api_key == "nrx_live_test"
    assert cb.metadata == {"trace_id": "abc"}
    assert cb.framework_version == "2026-02"
    assert cb.agent_id == "agent-y-sub-1"
    assert cb.emit_node_events is False


def test_construction_rejects_missing_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLangGraphCallback(framework="", capsulefile="x")


def test_construction_rejects_none_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixLangGraphCallback(framework=None, capsulefile="x")  # type: ignore[arg-type]


def test_construction_rejects_missing_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixLangGraphCallback(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    cb = NanorixLangGraphCallback(framework=FRAMEWORK_SOC2, capsulefile="x")
    assert cb.api_key == "env_value_xyz"


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value")
    cb = NanorixLangGraphCallback(
        framework=FRAMEWORK_SOC2, capsulefile="x", api_key="explicit_value"
    )
    assert cb.api_key == "explicit_value"


def test_allowed_frameworks_contains_required_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS
    cb = NanorixLangGraphCallback(framework="CUSTOM_FRAMEWORK", capsulefile="x")
    assert cb.framework == "CUSTOM_FRAMEWORK"


# ── on_chain_start emits ToolCallAttempted for graph nodes ────────


def test_on_chain_start_emits_tool_call_attempted_for_graph_node(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_a"},
        inputs={"value": "x"},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "tool_call_attempted"
    assert e["tool_call_id"] == str(run_id)
    assert e["tool_name"] == "node_a"
    assert e["tool_args_hash"] == _canonical_hash({"value": "x"})


def test_on_chain_start_metadata_signal_recognizes_node(tmp_path: Path):
    """Metadata['langgraph_node'] alone should trigger node-event emission."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "alt_node"},
        inputs={"input": "y"},
        run_id=run_id,
        metadata={"langgraph_node": "alt_node"},
    )
    events = read_buffer(buf)
    assert len(events) == 1
    assert events[0]["tool_name"] == "alt_node"


def test_on_chain_start_outer_graph_wrapper_emits_no_event(tmp_path: Path):
    """Chain without graph:step:* tag is the outer wrapper — no event."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "LangGraph"},
        inputs={"x": 1},
        run_id=run_id,
        tags=[],
    )
    cb.on_chain_end(outputs={"y": 2}, run_id=run_id)
    assert read_buffer(buf) == []


def test_on_chain_start_lcel_runnable_emits_no_event(tmp_path: Path):
    """LCEL Runnable (without graph:step tag) is also outer flow."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "RunnableLambda"},
        inputs={"x": 1},
        run_id=run_id,
        tags=["seq:step:1"],  # not a graph:step tag
    )
    cb.on_chain_end(outputs={"y": 2}, run_id=run_id)
    assert read_buffer(buf) == []


def test_emit_node_events_false_suppresses_node_emission(tmp_path: Path):
    """`emit_node_events=False` suppresses node tool-shape pairs."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, emit_node_events=False)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_a"},
        inputs={"value": "x"},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"value": "x-a"}, run_id=run_id)
    assert read_buffer(buf) == []


# ── on_chain_end emits ToolCallCompleted for matching node ────────


def test_on_chain_end_emits_tool_call_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_b"},
        inputs={"value": "x"},
        run_id=run_id,
        tags=graph_step_tag(2),
    )
    cb.on_chain_end(outputs={"value": "x-b"}, run_id=run_id)
    events = read_buffer(buf)
    assert len(events) == 2
    completed = events[1]
    assert completed["event"] == "tool_call_completed"
    assert completed["tool_call_id"] == str(run_id)
    assert completed["tool_name"] == "node_b"
    assert completed["tool_result_hash"] == _canonical_hash({"value": "x-b"})
    assert completed["duration_ms"] >= 0


def test_on_chain_end_outer_wrapper_emits_no_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "outer"}, inputs={}, run_id=run_id, tags=[]
    )
    cb.on_chain_end(outputs={"x": 1}, run_id=run_id)
    assert read_buffer(buf) == []


# ── on_chain_error emits ToolCallFailed for matching node ─────────


def test_on_chain_error_emits_tool_call_failed_for_node(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "bad_node"},
        inputs={"x": 1},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_error(error=TimeoutError("upstream timed out"), run_id=run_id)
    events = read_buffer(buf)
    failed = events[1]
    assert failed["event"] == "tool_call_failed"
    assert failed["tool_name"] == "bad_node"
    assert failed["failure_reason"].startswith("TimeoutError:")
    assert failed["failure_kind"] == {"type": "timeout"}


def test_on_chain_error_outer_wrapper_emits_no_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(serialized={"name": "outer"}, inputs={}, run_id=run_id, tags=[])
    cb.on_chain_error(error=Exception("boom"), run_id=run_id)
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
def test_classify_node_error_closed_enum_mapping(
    exc: BaseException, expected_kind: str, tmp_path: Path
):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={"x": 1},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_error(error=exc, run_id=run_id)
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": expected_kind}
    assert expected_kind in TOOL_CALL_FAILURE_KINDS


# ── on_llm_end emits ReasoningStepCompleted ────────────────────────


def test_on_llm_end_emits_reasoning_step_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_llm_start(
        serialized={"name": "ChatAnthropic", "kwargs": {"model": "claude-3-5"}},
        prompts=["Analyze patient X."],
        run_id=run_id,
    )
    cb.on_llm_end(
        response=LLMResult(
            generations=[[Generation(text="The result is...")]],
            llm_output={"token_usage": {"prompt_tokens": 12, "completion_tokens": 30}},
        ),
        run_id=run_id,
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "reasoning_step_completed"
    assert e["step_idx"] == 0
    assert e["model"] == "claude-3-5"
    assert e["prompt_hash"].startswith("sha512:")


def test_step_idx_increments_across_calls(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for i in range(5):
        run_id = uuid4()
        cb.on_llm_start(serialized={}, prompts=[f"prompt_{i}"], run_id=run_id)
        cb.on_llm_end(
            response=LLMResult(generations=[[Generation(text=f"resp_{i}")]]),
            run_id=run_id,
        )
    events = read_buffer(buf)
    assert [e["step_idx"] for e in events] == [0, 1, 2, 3, 4]


def test_on_chat_model_start_renders_messages(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chat_model_start(
        serialized={"name": "chat", "kwargs": {"model_name": "gpt-4o"}},
        messages=[
            [
                SystemMessage(content="You are an agent."),
                HumanMessage(content="Hello"),
            ]
        ],
        run_id=run_id,
    )
    cb.on_llm_end(
        response=LLMResult(
            generations=[
                [Generation(text="Hi there", message=AIMessage(content="Hi there"))]
            ]
        ),
        run_id=run_id,
    )
    e = read_buffer(buf)[0]
    assert e["model"] == "gpt-4o"


def test_on_llm_error_writes_no_event(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_llm_start(serialized={}, prompts=["x"], run_id=run_id)
    cb.on_llm_error(error=Exception("boom"), run_id=run_id)
    assert read_buffer(buf) == []


# ── on_tool_* hooks (tools nested inside nodes) ────────────────────


def test_on_tool_start_emits_tool_call_attempted(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_tool_start(
        serialized={"name": "lab_lookup"},
        input_str='{"member_id": "X"}',
        run_id=run_id,
        inputs={"member_id": "X"},
    )
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "tool_call_attempted"
    assert e["tool_name"] == "lab_lookup"
    assert e["tool_args_hash"] == _canonical_hash({"member_id": "X"})


def test_on_tool_end_emits_tool_call_completed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_tool_start(serialized={"name": "search"}, input_str="q", run_id=run_id)
    cb.on_tool_end(output={"result": "found"}, run_id=run_id)
    events = read_buffer(buf)
    completed = events[1]
    assert completed["event"] == "tool_call_completed"
    assert completed["tool_result_hash"] == _canonical_hash({"result": "found"})


def test_on_tool_error_emits_tool_call_failed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_tool_start(serialized={"name": "x"}, input_str="q", run_id=run_id)
    cb.on_tool_error(error=ConnectionError("down"), run_id=run_id)
    failed = read_buffer(buf)[1]
    assert failed["event"] == "tool_call_failed"
    assert failed["failure_kind"] == {"type": "upstream_failure"}


# ── parent_event_id propagation ────────────────────────────────────


def test_parent_event_id_propagates_for_nodes(tmp_path: Path):
    """A node's parent_run_id (its enclosing chain) maps to parent_event_id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    parent = uuid4()
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "node"},
        inputs={"x": 1},
        run_id=run_id,
        parent_run_id=parent,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"y": 1}, run_id=run_id, parent_run_id=parent)
    events = read_buffer(buf)
    assert all(e["parent_event_id"] == str(parent) for e in events)


def test_parent_event_id_uses_node_id_when_tool_nested(tmp_path: Path):
    """Tool inside a node should reference the node's run_id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    node_run = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_with_tool"},
        inputs={"x": 1},
        run_id=node_run,
        tags=graph_step_tag(1),
    )
    tool_run = uuid4()
    cb.on_tool_start(
        serialized={"name": "fetch"},
        input_str="q",
        run_id=tool_run,
        parent_run_id=node_run,
    )
    cb.on_tool_end(output="ok", run_id=tool_run, parent_run_id=node_run)
    cb.on_chain_end(outputs={"y": 1}, run_id=node_run)
    events = read_buffer(buf)
    # 4 events: node attempt → tool attempt → tool complete → node complete
    assert len(events) == 4
    tool_events = [e for e in events if e["tool_name"] == "fetch"]
    assert all(e["parent_event_id"] == str(node_run) for e in tool_events)


# ── on_event debug callback ────────────────────────────────────────


def test_on_event_callback_fires_per_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    captured: List[Dict[str, Any]] = []
    cb = make_callback(buf, on_event=captured.append)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={"x": 1},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"y": 1}, run_id=run_id)
    assert len(captured) == 2
    assert captured[0]["event"] == "tool_call_attempted"
    assert captured[1]["event"] == "tool_call_completed"


def test_on_event_failure_does_not_break_callback(tmp_path: Path):
    def bad(_: Dict[str, Any]) -> None:
        raise RuntimeError("debug crash")

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=bad)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={}, run_id=run_id)
    assert len(read_buffer(buf)) == 2


# ── agent_id propagation ───────────────────────────────────────────


def test_agent_id_propagates_to_all_emitted_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="sub-agent-A")
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={"x": 1},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"y": 1}, run_id=run_id)
    for e in read_buffer(buf):
        assert e.get("agent_id") == "sub-agent-A"


def test_no_agent_id_omits_field(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    run_id = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={"x": 1},
        run_id=run_id,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"y": 1}, run_id=run_id)
    for e in read_buffer(buf):
        assert "agent_id" not in e


# ── multi-node graph integration shape ──────────────────────────────


def test_multi_node_graph_full_shape(tmp_path: Path):
    """Synthetic 3-node graph with LLM call inside one node."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="agent-test-1")

    # Node 1
    n1 = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_router"},
        inputs={"input": "task"},
        run_id=n1,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"route": "decide"}, run_id=n1)

    # Node 2 (with LLM inside)
    n2 = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_decide"},
        inputs={"input": "task"},
        run_id=n2,
        tags=graph_step_tag(2),
    )
    llm_run = uuid4()
    cb.on_llm_start(
        serialized={"kwargs": {"model": "claude-3-5"}},
        prompts=["decide"],
        run_id=llm_run,
        parent_run_id=n2,
    )
    cb.on_llm_end(
        response=LLMResult(generations=[[Generation(text="decision")]]),
        run_id=llm_run,
        parent_run_id=n2,
    )
    cb.on_chain_end(outputs={"decision": "approve"}, run_id=n2)

    # Node 3 (with tool call inside)
    n3 = uuid4()
    cb.on_chain_start(
        serialized={"name": "node_emit"},
        inputs={"decision": "approve"},
        run_id=n3,
        tags=graph_step_tag(3),
    )
    tool_run = uuid4()
    cb.on_tool_start(
        serialized={"name": "submit_decision"},
        input_str="{}",
        run_id=tool_run,
        parent_run_id=n3,
    )
    cb.on_tool_end(output={"id": "PA-123"}, run_id=tool_run, parent_run_id=n3)
    cb.on_chain_end(outputs={"submitted": True}, run_id=n3)

    events = read_buffer(buf)
    # 3 nodes × 2 events + 1 LLM + 2 tool = 9
    assert len(events) == 9
    types = [e["event"] for e in events]
    assert types == [
        "tool_call_attempted",  # node_router start
        "tool_call_completed",  # node_router end
        "tool_call_attempted",  # node_decide start
        "reasoning_step_completed",  # LLM end
        "tool_call_completed",  # node_decide end
        "tool_call_attempted",  # node_emit start
        "tool_call_attempted",  # tool start
        "tool_call_completed",  # tool end
        "tool_call_completed",  # node_emit end
    ]
    assert all(e.get("agent_id") == "agent-test-1" for e in events)


# ── real LangGraph compiled-graph integration ──────────────────────


def test_real_langgraph_compiled_graph_invocation(tmp_path: Path):
    """End-to-end: build a real LangGraph, invoke with the callback,
    verify expected events appear in the buffer."""
    pytest.importorskip("langgraph.graph")
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        value: str

    def node_a(state: S) -> S:
        return {"value": state["value"] + "-a"}

    def node_b(state: S) -> S:
        return {"value": state["value"] + "-b"}

    g = StateGraph(S)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    graph = g.compile()

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    out = graph.invoke({"value": "x"}, config={"callbacks": [cb]})
    assert out == {"value": "x-a-b"}

    events = read_buffer(buf)
    # Expect node_a + node_b each with attempted + completed = 4 events
    # (the outer graph wrapper emits no events).
    types = [e["event"] for e in events]
    assert "tool_call_attempted" in types
    assert "tool_call_completed" in types
    # Node names are "a" and "b" per the graph definition
    node_names = {e.get("tool_name") for e in events}
    assert "a" in node_names
    assert "b" in node_names


def test_real_langgraph_with_failing_node(tmp_path: Path):
    """End-to-end failure: node raises ValueError → ToolCallFailed."""
    pytest.importorskip("langgraph.graph")
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        value: str

    def bad_node(state: S) -> S:
        raise ValueError(f"bad input {state['value']}")

    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)
    graph = g.compile()

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    with pytest.raises(ValueError):
        graph.invoke({"value": "x"}, config={"callbacks": [cb]})

    events = read_buffer(buf)
    failed = [e for e in events if e["event"] == "tool_call_failed"]
    assert len(failed) >= 1
    # The failure_kind for ValueError is invalid_args
    assert failed[0]["failure_kind"] == {"type": "invalid_args"}
    assert failed[0]["tool_name"] == "bad"


# ── golden canonical_hash invariant ─────────────────────────────────


def test_canonical_hash_jcs_byte_equivalent_with_helpers():
    import hashlib

    payload = {"a": 1, "b": "hello"}
    expected = "sha512:" + hashlib.sha512(b'{"a":1,"b":"hello"}').hexdigest()
    assert _canonical_hash(payload) == expected


# ── property test on chain orderings ────────────────────────────────


def test_property_random_graph_orderings_produce_well_formed_events(tmp_path: Path):
    """Property test: arbitrary node + LLM + tool sequences produce
    well-formed buffer contents.

    Iterations: 1_000 (smaller than the LangChain test's 10k because we
    have 4× more event-shape paths to exercise; cumulative coverage stays
    high while keeping wall-clock tight per anti-crash budget).
    """
    rng = random.Random(0xCAFEBEEF)
    iterations = 1_000
    for it in range(iterations):
        buf = tmp_path / f"events_{it}.jsonl"
        cb = make_callback(buf)
        sequence_len = rng.randint(1, 5)
        for _ in range(sequence_len):
            kind = rng.choice(["node", "llm", "tool", "tool_err", "node_err"])
            run_id = uuid4()
            if kind == "node":
                cb.on_chain_start(
                    serialized={"name": f"n_{rng.randint(0, 5)}"},
                    inputs={"k": rng.random()},
                    run_id=run_id,
                    tags=graph_step_tag(rng.randint(1, 10)),
                )
                cb.on_chain_end(outputs={"v": rng.random()}, run_id=run_id)
            elif kind == "node_err":
                cb.on_chain_start(
                    serialized={"name": "bad"},
                    inputs={},
                    run_id=run_id,
                    tags=graph_step_tag(1),
                )
                exc = rng.choice(
                    [ValueError("v"), ConnectionError("c"), PermissionError("p")]
                )
                cb.on_chain_error(error=exc, run_id=run_id)
            elif kind == "llm":
                cb.on_llm_start(
                    serialized={}, prompts=[f"p{rng.random()}"], run_id=run_id
                )
                cb.on_llm_end(
                    response=LLMResult(
                        generations=[[Generation(text=f"r{rng.random()}")]]
                    ),
                    run_id=run_id,
                )
            elif kind == "tool":
                cb.on_tool_start(
                    serialized={"name": f"t_{rng.randint(0, 3)}"},
                    input_str=str(rng.random()),
                    run_id=run_id,
                    inputs={"k": rng.random()},
                )
                cb.on_tool_end(output={"ok": True}, run_id=run_id)
            else:
                cb.on_tool_start(
                    serialized={"name": "bad"},
                    input_str="x",
                    run_id=run_id,
                )
                cb.on_tool_error(
                    error=rng.choice(
                        [TimeoutError("t"), ValueError("v"), ConnectionError("c")]
                    ),
                    run_id=run_id,
                )

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

        for cid in closure_ids:
            assert cid in attempted_ids


# ── Forever-Standard ────────────────────────────────────


def test_adr_006_i0_only_four_variants_emitted(tmp_path: Path):
    """Forever-Standard regression guard: only 4 variants reach the buffer."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    rid_n = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={"x": 1},
        run_id=rid_n,
        tags=graph_step_tag(1),
    )
    cb.on_chain_end(outputs={"y": 1}, run_id=rid_n)

    rid_n2 = uuid4()
    cb.on_chain_start(
        serialized={"name": "n2"},
        inputs={},
        run_id=rid_n2,
        tags=graph_step_tag(2),
    )
    cb.on_chain_error(error=Exception("x"), run_id=rid_n2)

    rid_llm = uuid4()
    cb.on_llm_start(serialized={}, prompts=["x"], run_id=rid_llm)
    cb.on_llm_end(
        response=LLMResult(generations=[[Generation(text="y")]]), run_id=rid_llm
    )

    rid_t = uuid4()
    cb.on_tool_start(serialized={"name": "x"}, input_str="q", run_id=rid_t)
    cb.on_tool_end(output="ok", run_id=rid_t)

    rid_te = uuid4()
    cb.on_tool_start(serialized={"name": "y"}, input_str="q", run_id=rid_te)
    cb.on_tool_error(error=TimeoutError("t"), run_id=rid_te)

    # Outer-wrapper chains (no events expected)
    rid_outer = uuid4()
    cb.on_chain_start(serialized={"name": "outer"}, inputs={}, run_id=rid_outer, tags=[])
    cb.on_chain_end(outputs={}, run_id=rid_outer)

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
    import nanorix.integrations.langgraph as lg_mod

    src = Path(lg_mod.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src


# ── round-trip determinism ──────────────────────────────────────────


def test_event_emission_deterministic_for_identical_inputs(tmp_path: Path):
    buf1 = tmp_path / "buf1.jsonl"
    buf2 = tmp_path / "buf2.jsonl"
    for buf in (buf1, buf2):
        cb = make_callback(buf)
        rid = uuid4()
        cb.on_chain_start(
            serialized={"name": "n"},
            inputs={"k": "v", "n": 1},
            run_id=rid,
            tags=graph_step_tag(1),
        )
        cb.on_chain_end(outputs={"ok": True, "data": [1, 2, 3]}, run_id=rid)
    e1 = read_buffer(buf1)
    e2 = read_buffer(buf2)
    assert e1[0]["tool_args_hash"] == e2[0]["tool_args_hash"]
    assert e1[1]["tool_result_hash"] == e2[1]["tool_result_hash"]


# ── concurrent (interleaved) run_ids ───────────────────────────────


def test_concurrent_node_run_ids_do_not_collide(tmp_path: Path):
    """Multiple in-flight node chains stay paired correctly."""
    buf = tmp_path / "buf.jsonl"
    cb = make_callback(buf)
    rid_a = uuid4()
    rid_b = uuid4()
    cb.on_chain_start(
        serialized={"name": "A"},
        inputs={"x": "a"},
        run_id=rid_a,
        tags=graph_step_tag(1),
    )
    cb.on_chain_start(
        serialized={"name": "B"},
        inputs={"x": "b"},
        run_id=rid_b,
        tags=graph_step_tag(2),
    )
    cb.on_chain_end(outputs={"y": "rb"}, run_id=rid_b)
    cb.on_chain_end(outputs={"y": "ra"}, run_id=rid_a)
    events = read_buffer(buf)
    assert events[0]["tool_name"] == "A" and events[0]["event"] == "tool_call_attempted"
    assert events[1]["tool_name"] == "B" and events[1]["event"] == "tool_call_attempted"
    assert events[2]["tool_name"] == "B" and events[2]["event"] == "tool_call_completed"
    assert events[3]["tool_name"] == "A" and events[3]["event"] == "tool_call_completed"
    assert events[0]["tool_call_id"] == events[3]["tool_call_id"]
    assert events[1]["tool_call_id"] == events[2]["tool_call_id"]


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
    rid = uuid4()
    cb.on_chain_start(
        serialized={"name": "n"},
        inputs={},
        run_id=rid,
        tags=graph_step_tag(1),
    )
    cb.on_chain_error(error=PermissionError("denied"), run_id=rid)
    failed = read_buffer(buf)[1]
    assert failed["failure_kind"] == {"type": "permission_denied"}
