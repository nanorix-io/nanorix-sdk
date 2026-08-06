"""Wave 18-12 / item 6d — CrewAI callback handler tests.

Mirrors `test_langgraph_integration.py` + `test_llama_index_integration.py`
+ `test_autogen_integration.py`. Exercises the
`nanorix.integrations.crewai.NanorixCrewAICallback` class against:

- Synthetic CrewAI TaskOutput / AgentAction / AgentFinish shapes
- Hook-to-ActivityEvent mapping per ADR-021 (4 variants only)
- Closed-enum classification for `ToolCallFailureKind`
- Canonical-JSON byte-equivalence with `_canonical_hash`
- Property tests on crew-execution orderings
- ADR-006 I0 Forever-Standard preservation (no new variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / AuditRecord
- Attachment helper for Crew (mocked)
- Hierarchical + sequential crew patterns

When `crewai` isn't installed, we inject a stub into `sys.modules` so
the integration module's import-time guard passes — the callback's
behavior is framework-version-agnostic.
"""

from __future__ import annotations

import json
import random
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# ── Test-environment shim ───────────────────────────────────────────


if "crewai" not in sys.modules:
    stub = types.ModuleType("crewai")
    stub.__version__ = "0.30.0+nanorix-test-shim"  # type: ignore[attr-defined]
    sys.modules["crewai"] = stub


from nanorix.helpers.activity_events import (  # noqa: E402
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
)
from nanorix.integrations.crewai import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    NanorixCrewAICallback,
    attach_to_crew,
)


# ── fixture helpers ────────────────────────────────────────────────


def read_buffer(buf_path: Path) -> List[Dict[str, Any]]:
    if not buf_path.exists():
        return []
    text = buf_path.read_text()
    if not text.strip():
        return []
    return [json.loads(line) for line in text.strip().split("\n")]


def make_callback(buf_path: Path, **overrides: Any) -> NanorixCrewAICallback:
    kwargs: Dict[str, Any] = {
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-crewai",
        "buffer_path": str(buf_path),
    }
    kwargs.update(overrides)
    return NanorixCrewAICallback(**kwargs)


class _FakeTaskOutput:
    """Synthetic CrewAI TaskOutput shape (0.51+)."""

    def __init__(
        self,
        *,
        task_id: str = "task_abc",
        raw: str = "result text",
        description: str = "Analyze patient X",
        expected_output: Optional[str] = None,
        agent: Optional[str] = None,
        execution_time: Optional[float] = None,
        inputs: Optional[Dict[str, Any]] = None,
        json_dict: Optional[Dict[str, Any]] = None,
    ):
        self.task_id = task_id
        self.raw = raw
        self.description = description
        self.expected_output = expected_output
        self.agent = agent
        self.execution_time = execution_time
        self.inputs = inputs
        self.json_dict = json_dict


class _FakeAgentAction:
    """Synthetic CrewAI AgentAction (tool decision step)."""

    def __init__(
        self,
        *,
        tool: str = "search",
        tool_input: Any = None,
        log: str = "I should search for X",
        text: Optional[str] = None,
    ):
        self.tool = tool
        self.tool_input = tool_input or {"q": "X"}
        self.log = log
        self.text = text


class _FakeAgentFinish:
    """Synthetic CrewAI AgentFinish (final answer step)."""

    def __init__(
        self,
        *,
        return_values: Optional[Dict[str, Any]] = None,
        log: str = "Final answer ready",
    ):
        self.return_values = return_values or {"output": "Final answer"}
        self.log = log


# ── construction ────────────────────────────────────────────────────


def test_construction_records_required_args():
    cb = NanorixCrewAICallback(framework=FRAMEWORK_HIPAA, capsulefile="crew-x")
    assert cb.framework == "HIPAA"
    assert cb.capsulefile == "crew-x"


def test_construction_with_optional_args():
    cb = NanorixCrewAICallback(
        framework=FRAMEWORK_GDPR,
        capsulefile="crew-y",
        api_key="nrx_live_test",
        metadata={"trace_id": "abc"},
        framework_version="2026-02",
        agent_id="crew-y-sub-1",
        emit_task_events=False,
        emit_step_events=False,
    )
    assert cb.api_key == "nrx_live_test"
    assert cb.metadata == {"trace_id": "abc"}
    assert cb.framework_version == "2026-02"
    assert cb.agent_id == "crew-y-sub-1"
    assert cb.emit_task_events is False
    assert cb.emit_step_events is False


def test_construction_rejects_missing_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixCrewAICallback(framework="", capsulefile="x")


def test_construction_rejects_none_framework():
    with pytest.raises(ValueError, match="framework"):
        NanorixCrewAICallback(framework=None, capsulefile="x")  # type: ignore[arg-type]


def test_construction_rejects_missing_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixCrewAICallback(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    cb = NanorixCrewAICallback(framework=FRAMEWORK_SOC2, capsulefile="x")
    assert cb.api_key == "env_value_xyz"


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value")
    cb = NanorixCrewAICallback(
        framework=FRAMEWORK_SOC2, capsulefile="x", api_key="explicit_value"
    )
    assert cb.api_key == "explicit_value"


def test_allowed_frameworks_contains_required_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS
    cb = NanorixCrewAICallback(framework="CUSTOM_FRAMEWORK", capsulefile="x")
    assert cb.framework == "CUSTOM_FRAMEWORK"


# ── task_callback emits ToolCallAttempted + ToolCallCompleted ──────


def test_task_callback_emits_attempted_and_completed_pair(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    output = _FakeTaskOutput(
        task_id="t_abc",
        raw="Patient X eligible",
        description="Eligibility check",
        agent="eligibility_agent",
        execution_time=1.234,  # 1234 ms
    )
    cb.task_callback(output)
    events = read_buffer(buf)
    assert len(events) == 2
    attempt = events[0]
    completion = events[1]
    assert attempt["event"] == "tool_call_attempted"
    assert attempt["tool_call_id"] == "t_abc"
    assert attempt["tool_name"] == "Eligibility check"
    assert completion["event"] == "tool_call_completed"
    assert completion["tool_call_id"] == "t_abc"
    assert completion["duration_ms"] == 1234


def test_task_callback_hashes_raw_output(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    output = _FakeTaskOutput(task_id="t1", raw="output text")
    cb.task_callback(output)
    completion = read_buffer(buf)[1]
    # tool_result_hash = canonical_hash of the rendered output dict.
    # Verify the hash is present + has the sha512 prefix.
    assert completion["tool_result_hash"].startswith("sha512:")


def test_task_callback_returns_input_unchanged(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    output = _FakeTaskOutput(task_id="t1")
    result = cb.task_callback(output)
    assert result is output


def test_task_callback_with_emit_task_events_false_emits_nothing(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, emit_task_events=False)
    cb.task_callback(_FakeTaskOutput(task_id="t1"))
    assert read_buffer(buf) == []


def test_task_callback_uses_agent_field_as_agent_id(tmp_path: Path):
    """The TaskOutput.agent field becomes the agent_id when callback default absent."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(_FakeTaskOutput(task_id="t1", agent="senior_researcher"))
    events = read_buffer(buf)
    assert all(e.get("agent_id") == "senior_researcher" for e in events)


def test_task_callback_callback_agent_id_overridden_by_output_agent(tmp_path: Path):
    """When both callback.agent_id and TaskOutput.agent set, TaskOutput.agent wins."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="default_agent")
    cb.task_callback(_FakeTaskOutput(task_id="t1", agent="task_agent"))
    events = read_buffer(buf)
    assert all(e.get("agent_id") == "task_agent" for e in events)


def test_task_callback_falls_back_to_default_agent_id(tmp_path: Path):
    """When TaskOutput has no .agent, callback.agent_id is used."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="default_agent")
    cb.task_callback(_FakeTaskOutput(task_id="t1", agent=None))
    events = read_buffer(buf)
    assert all(e.get("agent_id") == "default_agent" for e in events)


# ── extract_task_duration_ms variants ──────────────────────────────


def test_extract_task_duration_seconds_float(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(_FakeTaskOutput(task_id="t1", execution_time=2.5))
    events = read_buffer(buf)
    assert events[1]["duration_ms"] == 2500


def test_extract_task_duration_zero_default(tmp_path: Path):
    """Missing execution_time/duration falls back to 0."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(_FakeTaskOutput(task_id="t1", execution_time=None))
    events = read_buffer(buf)
    assert events[1]["duration_ms"] == 0


def test_extract_task_duration_dict_style(tmp_path: Path):
    """Older shape with `.duration` (ms int) is honored."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _OldStyleOutput:
        task_id = "t_old"
        raw = "r"
        description = "Old style"
        agent = None
        duration = 500

    cb.task_callback(_OldStyleOutput())
    events = read_buffer(buf)
    assert events[1]["duration_ms"] == 500


# ── extract_task_id fallbacks ──────────────────────────────────────


def test_task_id_extraction_from_id_attribute(tmp_path: Path):
    """TaskOutput exposing .id instead of .task_id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _AltOutput:
        id = "task_alt_id"
        raw = "r"
        description = "alt"
        agent = None
        execution_time = None

    cb.task_callback(_AltOutput())
    events = read_buffer(buf)
    assert events[0]["tool_call_id"] == "task_alt_id"


def test_task_id_extraction_falls_back_to_description_hash(tmp_path: Path):
    """When neither task_id nor id is present, description hash is used."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _NoIdOutput:
        raw = "r"
        description = "Some unique description"
        agent = None
        execution_time = None

    cb.task_callback(_NoIdOutput())
    events = read_buffer(buf)
    # Synthetic id pattern
    assert events[0]["tool_call_id"].startswith("crewai_task:")


# ── step_callback emits ReasoningStepCompleted ─────────────────────


def test_step_callback_agent_action_emits_reasoning_step(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    action = _FakeAgentAction(
        tool="search",
        tool_input={"q": "patient X"},
        log="I should search for X",
    )
    cb.step_callback(action)
    events = read_buffer(buf)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "reasoning_step_completed"
    assert e["step_idx"] == 0
    assert e["step_kind"] == {"type": "reason"}


def test_step_callback_agent_finish_emits_decide_step(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    finish = _FakeAgentFinish(return_values={"output": "Final"})
    cb.step_callback(finish)
    events = read_buffer(buf)
    assert events[0]["step_kind"] == {"type": "decide"}


def test_step_callback_step_idx_increments(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for _ in range(5):
        cb.step_callback(_FakeAgentAction())
    events = read_buffer(buf)
    assert [e["step_idx"] for e in events] == [0, 1, 2, 3, 4]


def test_step_callback_with_emit_step_events_false_emits_nothing(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, emit_step_events=False)
    cb.step_callback(_FakeAgentAction())
    cb.step_callback(_FakeAgentFinish())
    assert read_buffer(buf) == []


def test_step_callback_returns_input_unchanged(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    action = _FakeAgentAction()
    result = cb.step_callback(action)
    assert result is action


def test_step_callback_handles_dict_shape(tmp_path: Path):
    """Some adapters pass plain dicts instead of AgentAction objects."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.step_callback({"tool": "x", "tool_input": "y", "log": "thinking"})
    events = read_buffer(buf)
    assert events[0]["event"] == "reasoning_step_completed"


def test_step_callback_dict_with_return_values_is_finish(tmp_path: Path):
    """Dict-shape AgentFinish — has return_values key."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.step_callback({"return_values": {"output": "done"}, "log": "thinking"})
    events = read_buffer(buf)
    assert events[0]["step_kind"] == {"type": "decide"}


# ── on_task_failed emits ToolCallFailed ────────────────────────────


def test_on_task_failed_emits_tool_call_failed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_task_failed("task_xyz", TimeoutError("upstream slow"))
    events = read_buffer(buf)
    assert len(events) == 1
    failed = events[0]
    assert failed["event"] == "tool_call_failed"
    assert failed["tool_call_id"] == "task_xyz"
    assert failed["failure_reason"].startswith("TimeoutError:")
    assert failed["failure_kind"] == {"type": "timeout"}


def test_on_task_failed_with_task_object(tmp_path: Path):
    """Task object instead of string id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _Task:
        id = "task_obj"
        description = "Some task"
        name = None

    cb.on_task_failed(_Task(), ConnectionError("dropped"), duration_ms=300)
    failed = read_buffer(buf)[0]
    assert failed["tool_call_id"] == "task_obj"
    assert failed["failure_kind"] == {"type": "upstream_failure"}
    assert failed["duration_ms"] == 300


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
def test_classify_task_error_closed_enum_mapping(
    exc: BaseException, expected_kind: str, tmp_path: Path
):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_task_failed("tcid", exc)
    failed = read_buffer(buf)[0]
    assert failed["failure_kind"] == {"type": expected_kind}
    assert expected_kind in TOOL_CALL_FAILURE_KINDS


# ── on_step_error emits ToolCallFailed ─────────────────────────────


def test_on_step_error_emits_tool_call_failed(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.on_step_error("step_xyz", RuntimeError("step crashed"))
    events = read_buffer(buf)
    assert len(events) == 1
    failed = events[0]
    assert failed["event"] == "tool_call_failed"
    assert failed["tool_call_id"] == "step_xyz"


def test_on_step_error_with_agent_action_object(tmp_path: Path):
    """AgentAction object instead of string id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    action = _FakeAgentAction(tool="bad_tool")
    cb.on_step_error(action, ValueError("invalid"))
    failed = read_buffer(buf)[0]
    assert failed["tool_name"] == "bad_tool"
    assert failed["failure_kind"] == {"type": "invalid_args"}


# ── agent_id propagation ───────────────────────────────────────────


def test_agent_id_default_propagates_to_step_events(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, agent_id="sub-agent-A")
    cb.step_callback(_FakeAgentAction())
    cb.step_callback(_FakeAgentFinish())
    for e in read_buffer(buf):
        assert e.get("agent_id") == "sub-agent-A"


def test_no_agent_id_omits_field(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.step_callback(_FakeAgentAction())
    for e in read_buffer(buf):
        assert "agent_id" not in e


# ── on_event debug callback ────────────────────────────────────────


def test_on_event_callback_fires_per_emission(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    captured: List[Dict[str, Any]] = []
    cb = make_callback(buf, on_event=captured.append)
    cb.task_callback(_FakeTaskOutput(task_id="t1"))
    cb.step_callback(_FakeAgentAction())
    assert len(captured) == 3  # 2 from task + 1 from step
    assert captured[0]["event"] == "tool_call_attempted"
    assert captured[1]["event"] == "tool_call_completed"
    assert captured[2]["event"] == "reasoning_step_completed"


def test_on_event_failure_does_not_break_callback(tmp_path: Path):
    def bad(_: Dict[str, Any]) -> None:
        raise RuntimeError("debug crash")

    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf, on_event=bad)
    cb.task_callback(_FakeTaskOutput(task_id="t1"))
    assert len(read_buffer(buf)) == 2


# ── attachment helper ──────────────────────────────────────────────


def test_attach_to_crew_wires_both_callbacks(tmp_path: Path):
    """attach_to_crew sets both task_callback and step_callback."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    class _MockCrew:
        task_callback = None
        step_callback = None
        kickoff = lambda self: None  # noqa: E731

    crew = _MockCrew()
    attach_to_crew(crew, cb)
    # The wired callables are bound methods; verify they were assigned
    # (not the original None defaults) and that they fire correctly.
    assert callable(crew.task_callback)
    assert callable(crew.step_callback)
    assert crew.task_callback is not None
    assert crew.step_callback is not None
    # Verify they actually emit events through the callback's buffer
    crew.task_callback(_FakeTaskOutput(task_id="wire_test"))
    crew.step_callback(_FakeAgentAction())
    events = read_buffer(buf)
    assert len(events) == 3  # 2 from task + 1 from step


def test_attach_to_crew_wraps_kickoff_when_requested(tmp_path: Path):
    """wrap_kickoff=True monkey-patches kickoff to capture exceptions."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    original_called = {"n": 0}

    class _Task:
        id = "task_in_progress"
        description = "x"
        name = None

    class _MockCrew:
        task_callback = None
        step_callback = None
        _current_task = _Task()

        def kickoff(self) -> str:
            original_called["n"] += 1
            raise ValueError("boom")

    crew = _MockCrew()
    attach_to_crew(crew, cb, wrap_kickoff=True)
    with pytest.raises(ValueError):
        crew.kickoff()
    # The wrapper should have called the original AND emitted a failed event
    assert original_called["n"] == 1
    events = read_buffer(buf)
    failed = [e for e in events if e["event"] == "tool_call_failed"]
    assert len(failed) == 1
    assert failed[0]["tool_call_id"] == "task_in_progress"


def test_attach_to_crew_rejects_wrong_shape():
    cb = NanorixCrewAICallback(framework=FRAMEWORK_HIPAA, capsulefile="x")
    with pytest.raises(TypeError, match="task_callback"):
        attach_to_crew(object(), cb)


def test_attach_to_crew_handles_readonly_slots():
    """Some Crew variants make task_callback read-only; attachment shouldn't raise."""
    cb = NanorixCrewAICallback(framework=FRAMEWORK_HIPAA, capsulefile="x")

    class _ReadOnlyCrew:
        task_callback = None
        step_callback = None

        def __setattr__(self, k: str, v: Any) -> None:  # noqa: D401
            raise AttributeError("frozen")

    crew = _ReadOnlyCrew()
    # Should not raise (we swallow setattr failures gracefully).
    attach_to_crew(crew, cb)


# ── sequential vs hierarchical crew patterns ────────────────────────


def test_sequential_crew_emits_events_in_order(tmp_path: Path):
    """Sequential crew = N tasks fired in sequence."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    for i in range(3):
        cb.task_callback(
            _FakeTaskOutput(
                task_id=f"t{i}",
                raw=f"result{i}",
                description=f"Task {i}",
                agent=f"agent{i}",
                execution_time=0.1 * (i + 1),
            )
        )
    events = read_buffer(buf)
    # 3 tasks × 2 events each = 6
    assert len(events) == 6
    # Ordered: t0 attempted, t0 completed, t1 attempted, t1 completed, t2 ...
    types = [e["event"] for e in events]
    assert types == [
        "tool_call_attempted",
        "tool_call_completed",
        "tool_call_attempted",
        "tool_call_completed",
        "tool_call_attempted",
        "tool_call_completed",
    ]


def test_hierarchical_crew_supervisor_then_workers(tmp_path: Path):
    """Hierarchical crew: supervisor task → worker tasks → all flow."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    # Supervisor task
    cb.task_callback(
        _FakeTaskOutput(
            task_id="supervisor_task",
            description="Coordinate",
            agent="manager",
            execution_time=2.0,
        )
    )

    # Supervisor's reasoning steps
    cb.step_callback(_FakeAgentAction(tool="delegate", log="Assigning to worker A"))
    cb.step_callback(_FakeAgentFinish(return_values={"output": "delegation done"}))

    # Worker task 1
    cb.task_callback(
        _FakeTaskOutput(
            task_id="worker_a_task",
            description="Sub-task A",
            agent="worker_A",
            execution_time=0.5,
        )
    )

    events = read_buffer(buf)
    # 2 supervisor task events + 2 steps + 2 worker A task events = 6
    assert len(events) == 6
    types = [e["event"] for e in events]
    assert types == [
        "tool_call_attempted",
        "tool_call_completed",
        "reasoning_step_completed",
        "reasoning_step_completed",
        "tool_call_attempted",
        "tool_call_completed",
    ]


def test_hierarchical_crew_distinct_agent_ids(tmp_path: Path):
    """Different crew members surface with distinct agent_id."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(
        _FakeTaskOutput(task_id="t1", description="x", agent="manager")
    )
    cb.task_callback(
        _FakeTaskOutput(task_id="t2", description="y", agent="worker_A")
    )
    events = read_buffer(buf)
    agent_ids = {e.get("agent_id") for e in events}
    assert agent_ids == {"manager", "worker_A"}


# ── golden canonical_hash invariant ─────────────────────────────────


def test_canonical_hash_jcs_byte_equivalent_with_helpers():
    import hashlib

    payload = {"a": 1, "b": "hello"}
    expected = "sha512:" + hashlib.sha512(b'{"a":1,"b":"hello"}').hexdigest()
    assert _canonical_hash(payload) == expected


# ── property test on crew execution orderings ──────────────────────


def test_property_random_crew_orderings_produce_well_formed_events(
    tmp_path: Path,
):
    """Property test: arbitrary task + step sequences produce well-formed
    buffer contents.

    Iterations: 500.
    """
    rng = random.Random(0xFACECAFE)
    iterations = 500
    for it in range(iterations):
        buf = tmp_path / f"events_{it}.jsonl"
        cb = make_callback(buf)
        sequence_len = rng.randint(1, 5)
        for s in range(sequence_len):
            kind = rng.choice(["task", "task_fail", "action", "finish"])
            if kind == "task":
                cb.task_callback(
                    _FakeTaskOutput(
                        task_id=f"t_{it}_{s}",
                        raw=f"r_{rng.random()}",
                        description=f"Task {s}",
                        agent=f"agent_{rng.randint(0, 3)}",
                        execution_time=rng.random(),
                    )
                )
            elif kind == "task_fail":
                exc = rng.choice(
                    [TimeoutError("t"), ValueError("v"), ConnectionError("c")]
                )
                cb.on_task_failed(f"t_{it}_{s}", exc, duration_ms=rng.randint(0, 500))
            elif kind == "action":
                cb.step_callback(
                    _FakeAgentAction(
                        tool=f"tool_{rng.randint(0, 3)}",
                        tool_input={"x": rng.random()},
                        log=f"thought_{rng.random()}",
                    )
                )
            elif kind == "finish":
                cb.step_callback(
                    _FakeAgentFinish(
                        return_values={"output": f"answer_{rng.random()}"}
                    )
                )

        events = read_buffer(buf)
        for e in events:
            assert isinstance(e, dict)
            assert e.get("event") in {
                "tool_call_attempted",
                "tool_call_completed",
                "tool_call_failed",
                "reasoning_step_completed",
            }
            assert "at" in e


# ── ADR-006 I0 Forever-Standard ────────────────────────────────────


def test_adr_006_i0_only_four_variants_emitted(tmp_path: Path):
    """Forever-Standard regression guard: only 4 variants reach the buffer."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)

    cb.task_callback(_FakeTaskOutput(task_id="t1"))
    cb.step_callback(_FakeAgentAction())
    cb.step_callback(_FakeAgentFinish())
    cb.on_task_failed("t2", TimeoutError("t"))
    cb.on_step_error("s1", ValueError("v"))

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
    import nanorix.integrations.crewai as crewai_mod

    src = Path(crewai_mod.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src


# ── round-trip determinism ──────────────────────────────────────────


def test_event_emission_deterministic_for_identical_inputs(tmp_path: Path):
    buf1 = tmp_path / "buf1.jsonl"
    buf2 = tmp_path / "buf2.jsonl"
    for buf in (buf1, buf2):
        cb = make_callback(buf)
        cb.task_callback(
            _FakeTaskOutput(
                task_id="t1",
                raw="result",
                description="Test task",
                agent="a",
                execution_time=0.5,
            )
        )
    e1 = read_buffer(buf1)
    e2 = read_buffer(buf2)
    assert e1[0]["tool_args_hash"] == e2[0]["tool_args_hash"]
    assert e1[1]["tool_result_hash"] == e2[1]["tool_result_hash"]
    assert e1[1]["duration_ms"] == e2[1]["duration_ms"]


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
    cb.on_task_failed("t1", PermissionError("denied"))
    failed = read_buffer(buf)[0]
    assert failed["failure_kind"] == {"type": "permission_denied"}


# ── task_args + task_result rendering edge cases ───────────────────


def test_task_args_includes_description_and_expected_output(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(
        _FakeTaskOutput(
            task_id="t1",
            description="Analyze X",
            expected_output="A clear decision",
            inputs={"context": "..."},
        )
    )
    events = read_buffer(buf)
    # tool_args_hash should be canonical_hash of a dict including both
    # description, expected_output, and inputs.
    expected_args = {
        "description": "Analyze X",
        "expected_output": "A clear decision",
        "inputs": {"context": "..."},
    }
    assert events[0]["tool_args_hash"] == _canonical_hash(expected_args)


def test_task_result_includes_json_dict(tmp_path: Path):
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    cb.task_callback(
        _FakeTaskOutput(
            task_id="t1",
            raw="text",
            json_dict={"decision": "approve", "confidence": 0.9},
            agent="a",
        )
    )
    events = read_buffer(buf)
    expected_result = {
        "raw": "text",
        "json_dict": {"decision": "approve", "confidence": 0.9},
        "agent": "a",
    }
    assert events[1]["tool_result_hash"] == _canonical_hash(expected_result)


# ── task_name truncation ───────────────────────────────────────────


def test_task_name_truncates_long_description(tmp_path: Path):
    """Long descriptions truncated to 80 chars for tool_name."""
    buf = tmp_path / "events.jsonl"
    cb = make_callback(buf)
    long_desc = "A" * 200
    cb.task_callback(
        _FakeTaskOutput(task_id="t1", description=long_desc, agent="a")
    )
    events = read_buffer(buf)
    assert len(events[0]["tool_name"]) == 80
