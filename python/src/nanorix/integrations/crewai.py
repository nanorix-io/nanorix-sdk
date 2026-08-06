"""
CrewAI callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (Wave 18-12, item 6d): bridges CrewAI's
task + crew + agent-step lifecycle into Nanorix's in-capsule
ActivityEvent emission helpers. Customer wires the callback into their
existing CrewAI agent / task / crew via thin attachment helpers OR by
passing the callback's bound methods directly into CrewAI's
`task_callback=` / `step_callback=` slots:

    from nanorix.integrations.crewai import (
        NanorixCrewAICallback,
        attach_to_crew,
    )

    callback = NanorixCrewAICallback(framework="HIPAA", capsulefile="agent")
    attach_to_crew(crew, callback)
    # ... crew.kickoff() now emits sub-receipts

Or per-task / per-agent:

    Task(
        ...,
        callback=callback.task_callback,
    )
    Agent(
        ...,
        step_callback=callback.step_callback,
    )

CrewAI's callback surface as of 0.30+ exposes three callback slots:

- **`Crew(task_callback=...)`** — invoked after each task completion with
  the resulting `TaskOutput`. Single-arg signature.
- **`Crew(step_callback=...)`** — invoked at each agent reasoning step
  with an `AgentAction` (tool invocation) or `AgentFinish` (final answer).
- **`Task(callback=...)`** — per-task variant of `task_callback`.
- **`Agent(step_callback=...)`** — per-agent variant of `step_callback`.

This callback adapts those signatures into the existing 4 ActivityEvent
variants — no new variants — Forever-Standard preserved (ADR-006 I0).

## Architectural distinction from LangChain / LangGraph / AutoGen / LlamaIndex

CrewAI's notion of a "task" is the unit of work for a crew member
(agent). A task maps onto the AuditProof's activity_trail as:

- **task start** → `ToolCallAttempted` (the task is the structural unit
  the agent invoked; conceptually a tool in the AuditProof sense).
- **task completed** → `ToolCallCompleted` (success).
- **task failed** → `ToolCallFailed` (failure).

A "step" inside a task (the agent's reasoning / tool-action loop)
maps onto the AuditProof as:

- **AgentAction step** → `ReasoningStepCompleted` (the agent decided
  to invoke a tool; the model's chain-of-thought + tool args + observation
  go into the reasoning step).
- **AgentFinish step** → `ReasoningStepCompleted` (the agent emitted
  its final answer for this task).

A crew kickoff is the customer's outer-most workflow; it does NOT emit
its own event (the AuditProof's overall destruction-state captures it).

## Hook → ActivityEvent mapping

| CrewAI callback path                 | ActivityEvent emitted                     | When                                |
|--------------------------------------|-------------------------------------------|-------------------------------------|
| Task.callback (success)              | ToolCallCompleted (per task)              | Task returns successfully           |
| Task.callback (with raised err)      | ToolCallFailed (per task)                 | Task raised (see note below)        |
| Crew.task_callback                   | ToolCallCompleted (per task)              | Same — fires per crew task          |
| Agent.step_callback (AgentAction)    | ReasoningStepCompleted                    | Agent decided to invoke a tool      |
| Agent.step_callback (AgentFinish)    | ReasoningStepCompleted                    | Agent emitted final task answer     |
| Crew.step_callback                   | ReasoningStepCompleted                    | Same — fires per crew step          |

The callback pre-emits `ToolCallAttempted` for each task at the moment
the task_callback fires, then immediately emits the matching
`ToolCallCompleted` — CrewAI does not expose a task-start hook
separately from task-completion, so the attempt + completion pair is
synthesized within a single callback invocation. The `duration_ms`
field is populated from the `TaskOutput.execution_time_ms` attribute
when available (CrewAI 0.51+); falls back to 0.

For task failures — CrewAI's task_callback only fires on success. To
capture failures, the customer wraps `crew.kickoff()` in try/except
and calls `callback.on_task_failed(task, exc)` directly. The
`attach_to_crew()` helper does this wrapping automatically when given
a `wrap_kickoff=True` arg.

## Three-tier integration model

This callback is Tier A — per-task + per-step receipts. Full workflow
destruction proof requires Tier B (`@nanorix.workload`) or Tier C
(framework middleware).

## Design constraints

- `framework=` REQUIRED — no silent default (ADR-013 customer-sole-declarer).
- No PHI scanning (ADR-013 Alt B). Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- CrewAI dep is optional — `pip install nanorix[crewai]`.
- Importing without `crewai` succeeds; constructing `NanorixCrewAICallback`
  then raises `ImportError` with a remediation hint (error on use, never
  at import).
- Callback does NOT inherit any CrewAI base class — CrewAI's dispatch
  is a per-callable-slot registration pattern.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Mapping, Optional

from nanorix.helpers.activity_events import (
    DEFAULT_BUFFER_PATH,
    TOOL_CALL_FAILURE_KINDS,
    _canonical_hash,
    emit_reasoning_step_completed,
    emit_tool_call_attempted,
    emit_tool_call_completed,
    emit_tool_call_failed,
)

# Optional dep — imported lazily; the remediation error is raised on
# first use (constructing the callback), never at module import.
try:
    import crewai  # noqa: F401

    _CREWAI_AVAILABLE = True
    _CREWAI_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    _CREWAI_AVAILABLE = False
    _CREWAI_IMPORT_ERROR = _exc

_CREWAI_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.crewai requires the 'crewai' optional extra. "
    "Install with:  pip install 'nanorix[crewai]'  (or pip install "
    "crewai>=0.30 alongside nanorix)."
)

__all__ = [
    "NanorixCrewAICallback",
    "attach_to_crew",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
]


# Framework names match `nanorix.compliance.*Context.framework_id` values.
FRAMEWORK_HIPAA = "HIPAA"
FRAMEWORK_GDPR = "GDPR"
FRAMEWORK_SOC2 = "SOC2"
FRAMEWORK_PCI_DSS = "PCI_DSS"
FRAMEWORK_HITRUST = "HITRUST"
FRAMEWORK_EU_AI_ACT = "EU_AI_ACT"

ALLOWED_FRAMEWORKS = frozenset(
    {
        FRAMEWORK_HIPAA,
        FRAMEWORK_GDPR,
        FRAMEWORK_SOC2,
        FRAMEWORK_PCI_DSS,
        FRAMEWORK_HITRUST,
        FRAMEWORK_EU_AI_ACT,
    }
)
"""Recommended canonical framework labels.

Customer may pass any non-empty string for `framework=`; canonical labels
above match the `nanorix.compliance.*Context.framework_id` values. The
framework arg is recorded on the callback instance for downstream logging
/ debugging; it does NOT enter the AuditProof's hash chain (binding into
AuditProof.metadata happens via `client.capsules.create(metadata=)` or
`nanorix.compliance.HipaaContext().to_metadata()`, NOT through the
callback).
"""


# Map common exception class names → closed-enum ToolCallFailureKind
# strings (same map as the LangChain / LangGraph / LlamaIndex / AutoGen
# callbacks).
_DEFAULT_FAILURE_KIND_MAP: Mapping[str, str] = {
    "TimeoutError": "timeout",
    "AsyncTimeoutError": "timeout",
    "FuturesTimeoutError": "timeout",
    "PermissionError": "permission_denied",
    "PermissionDenied": "permission_denied",
    "ValueError": "invalid_args",
    "ValidationError": "invalid_args",
    "TypeError": "invalid_args",
    "HTTPError": "upstream_failure",
    "ConnectionError": "upstream_failure",
    "RequestException": "upstream_failure",
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
}


class NanorixCrewAICallback:
    """CrewAI callback handler that emits Nanorix ActivityEvents.

    Stable adapter API across CrewAI versions. Customer instantiates one
    callback (per crew OR one shared with per-call `agent_id=` overrides)
    and wires it into CrewAI's callback slots via the bound methods:

    - `callback.task_callback` → pass into `Task(callback=...)` OR
      `Crew(task_callback=...)`. Single-arg accepting `TaskOutput`.
    - `callback.step_callback` → pass into `Agent(step_callback=...)`
      OR `Crew(step_callback=...)`. Single-arg accepting `AgentAction`
      or `AgentFinish`.
    - `callback.on_task_failed(task_or_id, error)` → call directly
      when `crew.kickoff()` raises and you want a `ToolCallFailed`
      bound to a specific task.

    OR use the `attach_to_crew(crew, callback)` helper to wire all
    three at once.

    Public lifecycle methods (4 — matches the 4 ActivityEvent variants):

    - `task_callback(task_output)` — task completion; emits
      `ToolCallAttempted` + `ToolCallCompleted`.
    - `step_callback(step)` — agent step (AgentAction or AgentFinish);
      emits `ReasoningStepCompleted`.
    - `on_task_failed(task_or_id, error)` — task-level failure; emits
      `ToolCallFailed`.
    - `on_step_error(step_or_id, error)` — step-level failure when
      surfaced by the customer's outer code; emits `ToolCallFailed`.

    Args:
        framework: REQUIRED. Compliance framework label.
        capsulefile: REQUIRED. Capsulefile name.
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`.
        metadata: Optional extra metadata dict for logging context only.
        framework_version: Optional framework version string.
        on_event: Optional callback invoked for every emitted
            ActivityEvent (debugging hook).
        buffer_path: Override for the activity-trail buffer location.
        agent_id: Optional default agent identifier propagated to every
            emitted event's `agent_id` slot.
        emit_task_events: Whether to emit `ToolCallAttempted/Completed/
            Failed` for each task. Defaults to ``True``. Set to
            ``False`` if the customer prefers only per-step reasoning
            events.
        emit_step_events: Whether to emit `ReasoningStepCompleted` per
            agent step. Defaults to ``True``. Set to ``False`` if the
            customer prefers only per-task receipts (e.g., for crews
            with very long reasoning loops where per-step granularity
            inflates the activity_trail).

    Raises:
        ValueError: if `framework` or `capsulefile` is missing/empty.
    """

    def __init__(
        self,
        *,
        framework: str,
        capsulefile: str,
        api_key: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        framework_version: Optional[str] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        buffer_path: str = DEFAULT_BUFFER_PATH,
        agent_id: Optional[str] = None,
        emit_task_events: bool = True,
        emit_step_events: bool = True,
    ) -> None:
        if not _CREWAI_AVAILABLE:
            raise ImportError(
                _CREWAI_IMPORT_ERROR_MESSAGE
            ) from _CREWAI_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixCrewAICallback requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                "ADR-013 customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixCrewAICallback requires an explicit `capsulefile=` "
                "argument (e.g., capsulefile='pa-decision-agent')."
            )

        self.framework = framework
        self.capsulefile = capsulefile
        self.api_key = (
            api_key if api_key is not None else os.environ.get("NANORIX_API_KEY")
        )
        self.metadata: Dict[str, Any] = dict(metadata) if metadata else {}
        self.framework_version = framework_version
        self.on_event = on_event
        self.buffer_path = buffer_path
        self.agent_id = agent_id
        self.emit_task_events = emit_task_events
        self.emit_step_events = emit_step_events

        # In-flight task state. Keys are task_id (extracted from
        # TaskOutput/Task) — used for on_task_failed pairing when the
        # customer's outer code surfaces a failure after a task started.
        self._in_flight_tasks: Dict[str, Dict[str, Any]] = {}

        # Counter for `step_idx` on `ReasoningStepCompleted` events.
        self._reasoning_step_counter = 0

    # ── helpers ────────────────────────────────────────────────────

    def _now_ms(self) -> int:
        """Monotonic millisecond clock for duration computation."""
        return int(time.monotonic() * 1000)

    def _emit(self, event: Dict[str, Any]) -> None:
        """Invoke `on_event` callback if configured (debugging hook)."""
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - debug hook must not crash
                pass

    def _classify_tool_error(self, error: BaseException) -> Optional[str]:
        """Map exception class to closed-enum failure_kind."""
        cls_name = type(error).__name__
        kind = _DEFAULT_FAILURE_KIND_MAP.get(cls_name)
        if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
            return kind
        for parent in type(error).__mro__[1:]:
            kind = _DEFAULT_FAILURE_KIND_MAP.get(parent.__name__)
            if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
                return kind
        return None

    def _resolve_agent_id(self, override: Optional[str]) -> Optional[str]:
        """Per-call `agent_id=` wins over the callback's default."""
        if override is not None:
            return override
        return self.agent_id

    @staticmethod
    def _extract_task_id(task_or_output: Any) -> str:
        """Pull a stable task_id from a TaskOutput or Task.

        CrewAI's TaskOutput shape (verified against 0.51+):
        - `task_output.task_id: str` — UUID-shaped per task.
        - `task_output.task.id: str` — alternate path.
        - Some versions expose only `.description` (str); we hash it to
          a synthetic id as a fallback.

        Returns a stable string identifier.
        """
        for attr in ("task_id", "id"):
            v = getattr(task_or_output, attr, None)
            if isinstance(v, str) and v:
                return v
        # Some versions wrap the task as a sub-attribute.
        task = getattr(task_or_output, "task", None)
        if task is not None:
            for attr in ("id", "task_id"):
                v = getattr(task, attr, None)
                if isinstance(v, str) and v:
                    return v
        # Dict-shape fallback (some test fixtures).
        if isinstance(task_or_output, dict):
            for k in ("task_id", "id"):
                v = task_or_output.get(k)
                if isinstance(v, str) and v:
                    return v
        # Hash description as last resort.
        desc = getattr(task_or_output, "description", None)
        if isinstance(desc, str) and desc:
            return f"crewai_task:{_canonical_hash(desc)[7:23]}"
        return f"crewai_task:{int(time.monotonic() * 1_000_000)}"

    @staticmethod
    def _extract_task_name(task_or_output: Any) -> str:
        """Pull a human-readable task name from a TaskOutput or Task."""
        # Task may have .name (rare) or .description (always).
        for attr in ("name", "description"):
            v = getattr(task_or_output, attr, None)
            if isinstance(v, str) and v:
                # Truncate description for tool_name (full text hashed).
                return v[:80]
        task = getattr(task_or_output, "task", None)
        if task is not None:
            for attr in ("name", "description"):
                v = getattr(task, attr, None)
                if isinstance(v, str) and v:
                    return v[:80]
        return "crewai_task"

    @staticmethod
    def _extract_task_duration_ms(task_output: Any) -> int:
        """Pull execution duration from a TaskOutput.

        CrewAI 0.51+ exposes `task_output.execution_time` (seconds float).
        Some older versions expose `task_output.duration` (ms int).
        Returns 0 when neither is available.
        """
        exec_time = getattr(task_output, "execution_time", None)
        if isinstance(exec_time, (int, float)) and exec_time >= 0:
            return int(exec_time * 1000) if isinstance(exec_time, float) else int(exec_time)
        dur = getattr(task_output, "duration", None)
        if isinstance(dur, (int, float)) and dur >= 0:
            return int(dur)
        return 0

    @staticmethod
    def _render_task_args(task_or_output: Any) -> Any:
        """Render task inputs for canonical hashing as tool_args.

        Pulls the task's description + expected_output + inputs (if
        present) — these are the "arguments" the agent received to
        produce its result.
        """
        task = getattr(task_or_output, "task", task_or_output)
        rendered: Dict[str, Any] = {}
        for attr in ("description", "expected_output"):
            v = getattr(task, attr, None)
            if isinstance(v, str):
                rendered[attr] = v
        inputs = getattr(task, "inputs", None) or getattr(task, "context", None)
        if inputs is not None:
            rendered["inputs"] = NanorixCrewAICallback._sanitize_value(inputs)
        return rendered or {}

    @staticmethod
    def _render_task_result(task_output: Any) -> Any:
        """Render a TaskOutput for canonical hashing as tool_result.

        Captures the `.raw` output text (CrewAI's primary result field)
        plus structured `.pydantic` or `.json_dict` outputs when present.
        """
        if task_output is None:
            return None
        # JSON-safe types pass through.
        if isinstance(task_output, (str, int, float, bool, list, dict)):
            return task_output
        rendered: Dict[str, Any] = {}
        raw = getattr(task_output, "raw", None)
        if isinstance(raw, str):
            rendered["raw"] = raw
        pyd = getattr(task_output, "pydantic", None)
        if pyd is not None:
            if hasattr(pyd, "model_dump"):
                try:
                    rendered["pydantic"] = pyd.model_dump()
                except Exception:  # noqa: BLE001
                    rendered["pydantic"] = str(pyd)
            else:
                rendered["pydantic"] = str(pyd)
        json_dict = getattr(task_output, "json_dict", None)
        if isinstance(json_dict, dict):
            rendered["json_dict"] = json_dict
        agent = getattr(task_output, "agent", None)
        if isinstance(agent, str):
            rendered["agent"] = agent
        return rendered or str(task_output)

    @staticmethod
    def _render_step(step: Any) -> Dict[str, Any]:
        """Render an AgentAction or AgentFinish for canonical hashing.

        CrewAI surfaces two shapes via the step_callback:

        - **AgentAction**: agent decided to invoke a tool. Fields:
          `tool` (str), `tool_input` (str|dict), `log` (str, the
          chain-of-thought), `text` (optional).
        - **AgentFinish**: agent emitted final answer. Fields:
          `return_values` (dict, typically `{"output": "..."}`),
          `log` (str).
        """
        # Detect AgentFinish (has return_values)
        return_values = getattr(step, "return_values", None)
        if return_values is not None:
            rendered: Dict[str, Any] = {"kind": "agent_finish"}
            if isinstance(return_values, dict):
                rendered["return_values"] = return_values
            log = getattr(step, "log", None)
            if isinstance(log, str):
                rendered["log"] = log
            return rendered

        # AgentAction
        rendered_action: Dict[str, Any] = {"kind": "agent_action"}
        for attr in ("tool", "tool_input", "log", "text"):
            v = getattr(step, attr, None)
            if isinstance(v, (str, dict, list, int, float, bool)):
                rendered_action[attr] = v
        # Dict-shape (some adapters)
        if isinstance(step, dict):
            for k in ("tool", "tool_input", "log", "text"):
                v = step.get(k)
                if isinstance(v, (str, dict, list, int, float, bool)):
                    rendered_action.setdefault(k, v)
            return_v = step.get("return_values")
            if isinstance(return_v, dict):
                return {"kind": "agent_finish", "return_values": return_v}
        return rendered_action

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        """Coerce arbitrary values into JSON-safe form for `_canonical_hash`."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(x) for x in value]
        if isinstance(value, set):
            return [cls._sanitize_value(x) for x in sorted(value, key=repr)]
        if isinstance(value, dict):
            return {str(k): cls._sanitize_value(v) for k, v in value.items()}
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(value, "dict"):
            try:
                rendered: Any = value.dict()
                return rendered
            except Exception:  # noqa: BLE001
                pass
        return str(value)

    # ── CrewAI-shape callback methods ──────────────────────────────

    def task_callback(self, task_output: Any) -> Any:
        """Emit ToolCallAttempted + ToolCallCompleted for a finished task.

        Bound method suitable for passing into `Task(callback=...)` or
        `Crew(task_callback=...)`. CrewAI invokes this with a single
        positional `TaskOutput` argument.

        Returns the input unchanged (some CrewAI versions ignore the
        return value; others propagate it as the final task output).
        """
        if not self.emit_task_events:
            return task_output

        task_id = self._extract_task_id(task_output)
        task_name = self._extract_task_name(task_output)
        task_args = self._render_task_args(task_output)
        task_result = self._render_task_result(task_output)
        duration_ms = self._extract_task_duration_ms(task_output)

        agent_field = getattr(task_output, "agent", None)
        agent_override = agent_field if isinstance(agent_field, str) else None

        # Synthesize the attempt + completion pair within one
        # callback invocation (CrewAI does not expose a task-start
        # hook).
        attempt = emit_tool_call_attempted(
            tool_call_id=task_id,
            tool_name=task_name,
            tool_args=task_args,
            agent_id=self._resolve_agent_id(agent_override),
            buffer_path=self.buffer_path,
        )
        self._emit(attempt)

        completion = emit_tool_call_completed(
            tool_call_id=task_id,
            tool_name=task_name,
            tool_result=task_result,
            duration_ms=duration_ms,
            agent_id=self._resolve_agent_id(agent_override),
            buffer_path=self.buffer_path,
        )
        self._emit(completion)

        return task_output

    def step_callback(self, step: Any) -> Any:
        """Emit ReasoningStepCompleted for an agent step.

        Bound method suitable for passing into `Agent(step_callback=...)`
        or `Crew(step_callback=...)`. CrewAI invokes this with a single
        positional argument — an AgentAction (tool decision) or
        AgentFinish (final answer).

        Returns the input unchanged.
        """
        if not self.emit_step_events:
            return step

        rendered = self._render_step(step)
        # The "prompt" is the agent's chain-of-thought log; the
        # "response" is the action/finish itself. We hash both.
        log = rendered.get("log") if isinstance(rendered, dict) else None
        prompt: Any = log if isinstance(log, str) else []
        response = rendered

        step_kind: Optional[str] = None
        if isinstance(rendered, dict):
            if rendered.get("kind") == "agent_finish":
                # Final answer = decide step in the closed enum.
                step_kind = "decide"
            elif rendered.get("kind") == "agent_action":
                # Tool decision = reason step.
                step_kind = "reason"

        event = emit_reasoning_step_completed(
            step_idx=self._reasoning_step_counter,
            prompt=prompt,
            response=response,
            duration_ms=0,
            agent_id=self._resolve_agent_id(None),
            step_kind=step_kind,
            buffer_path=self.buffer_path,
        )
        self._reasoning_step_counter += 1
        self._emit(event)
        return step

    def on_task_failed(
        self,
        task_or_id: Any,
        error: BaseException,
        *,
        duration_ms: int = 0,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit ToolCallFailed for a task that raised during execution.

        CrewAI's task_callback fires only on success. Customer's outer
        code (or `attach_to_crew(crew, callback, wrap_kickoff=True)`)
        wraps `crew.kickoff()` in try/except and calls this method
        when an exception escapes a task.

        Args:
            task_or_id: A CrewAI `Task` instance OR a string task_id.
            error: The exception that surfaced.
            duration_ms: Optional wall-clock time the task ran before
                failing. Defaults to 0.
            agent_id: Per-call agent_id override.

        Returns:
            The emitted ToolCallFailed event dict.
        """
        if not self.emit_task_events:
            # Even with emit_task_events=False, we surface failures
            # — silent failure is a bug-class we want to avoid.
            pass

        if isinstance(task_or_id, str):
            task_id = task_or_id
            task_name = "crewai_task"
        else:
            task_id = self._extract_task_id(task_or_id)
            task_name = self._extract_task_name(task_or_id)

        failure_reason = f"{type(error).__name__}: {error}"
        failure_kind = self._classify_tool_error(error)
        event = emit_tool_call_failed(
            tool_call_id=task_id,
            tool_name=task_name,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            failure_kind=failure_kind,
            agent_id=self._resolve_agent_id(agent_id),
            buffer_path=self.buffer_path,
        )
        self._emit(event)
        return event

    def on_step_error(
        self,
        step_or_id: Any,
        error: BaseException,
        *,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit ToolCallFailed for a step-level error.

        Used when the customer's outer code captures an exception
        raised inside an AgentAction (e.g., a tool returned an
        exception trace rather than raising). CrewAI's normal
        step_callback path does NOT receive errors.

        Args:
            step_or_id: An AgentAction-shape object OR a string step_id.
            error: The exception that surfaced.
            agent_id: Per-call override.

        Returns:
            The emitted ToolCallFailed event dict.
        """
        if isinstance(step_or_id, str):
            step_id = step_or_id
            tool_name = "crewai_step"
        else:
            tool_name = (
                getattr(step_or_id, "tool", None) or "crewai_step"
            )
            step_id = (
                getattr(step_or_id, "id", None)
                or f"crewai_step:{int(time.monotonic() * 1_000_000)}"
            )
            if not isinstance(step_id, str):
                step_id = str(step_id)
            if not isinstance(tool_name, str):
                tool_name = str(tool_name)

        failure_reason = f"{type(error).__name__}: {error}"
        failure_kind = self._classify_tool_error(error)
        event = emit_tool_call_failed(
            tool_call_id=step_id,
            tool_name=tool_name,
            failure_reason=failure_reason,
            duration_ms=0,
            failure_kind=failure_kind,
            agent_id=self._resolve_agent_id(agent_id),
            buffer_path=self.buffer_path,
        )
        self._emit(event)
        return event


# ── Attachment helper ──────────────────────────────────────────────


def attach_to_crew(
    crew: Any,
    callback: NanorixCrewAICallback,
    *,
    wrap_kickoff: bool = False,
) -> None:
    """Wire a `NanorixCrewAICallback` into a CrewAI Crew.

    Sets `crew.task_callback` and `crew.step_callback` to the
    callback's bound methods. Optionally wraps `crew.kickoff` in a
    try/except that calls `callback.on_task_failed(...)` on the
    currently-executing task when an exception escapes (CrewAI does
    not surface task failures through `task_callback`).

    Args:
        crew: A CrewAI `Crew` instance.
        callback: The `NanorixCrewAICallback` to wire in.
        wrap_kickoff: When ``True`` (default ``False``), monkey-patch
            `crew.kickoff` to catch + report exceptions. Off by
            default to avoid surprising the customer's outer code.

    Raises:
        TypeError: if `crew` is not a Crew-shape object (missing
            `task_callback` / `step_callback` attributes).
    """
    if not hasattr(crew, "task_callback") and not hasattr(crew, "step_callback"):
        raise TypeError(
            "attach_to_crew requires a CrewAI Crew instance (must expose "
            "`task_callback` and/or `step_callback` attributes). Diagnostic: "
            f"type={type(crew).__name__}."
        )

    try:
        crew.task_callback = callback.task_callback
    except Exception:  # noqa: BLE001
        # Some Crew variants make these slots read-only via pydantic; the
        # customer must wire per-Task/per-Agent in that case.
        pass
    try:
        crew.step_callback = callback.step_callback
    except Exception:  # noqa: BLE001
        pass

    if wrap_kickoff:
        original_kickoff = getattr(crew, "kickoff", None)
        if not callable(original_kickoff):
            return

        def _wrapped_kickoff(*args: Any, **kwargs: Any) -> Any:
            try:
                return original_kickoff(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                # Attribute the failure to the currently-executing task
                # if discoverable; otherwise emit with a synthetic id.
                current_task = getattr(crew, "_current_task", None)
                target = current_task if current_task is not None else "crew_kickoff"
                callback.on_task_failed(target, exc)
                raise

        try:
            crew.kickoff = _wrapped_kickoff
        except Exception:  # noqa: BLE001
            # Crew may be pydantic-frozen; customer should call
            # callback.on_task_failed manually in that case.
            pass


# Re-exported for caller convenience (tests + customer-debugging scripts).
_canonical_hash_for_test = _canonical_hash
