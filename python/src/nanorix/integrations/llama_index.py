"""
LlamaIndex callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (Wave 14, item 6b of second-wave): bridges
LlamaIndex's callback surface into Nanorix's in-capsule ActivityEvent
emission helpers. Customer adds a few lines to their existing LlamaIndex
runtime configuration:

    from llama_index.core.callbacks import CallbackManager
    from llama_index.core import Settings
    from nanorix.integrations.llama_index import NanorixLlamaIndexCallback

    callback = NanorixLlamaIndexCallback(framework="HIPAA", capsulefile="agent")
    Settings.callback_manager = CallbackManager([callback])

The callback runs INSIDE a Nanorix capsule (customer wraps the agent run
via `client.capsules.create()` or the future `@nanorix.workload`
decorator). LlamaIndex's hook surface maps onto the 4 existing
ActivityEvent variants shipped by — `ToolCallAttempted` /
`ToolCallCompleted` / `ToolCallFailed` / `ReasoningStepCompleted`. No
new variants — Forever-Standard preserved.

## Architectural distinction from LangChain

LlamaIndex's `BaseCallbackHandler` (at
`llama_index.core.callbacks.base_handler`) is structurally different:

- A single `on_event_start(event_type, payload, event_id, parent_id)`
  method receives EVERY event kind (LLM, FUNCTION_CALL, EMBEDDING,
  RETRIEVE, TREE, SUB_QUESTION, etc.) — discriminated by the
  `event_type: CBEventType` enum.
- A single `on_event_end(event_type, payload, event_id)` closes the
  event.
- `start_trace(trace_id)` and `end_trace(trace_id, trace_map)`
  bracket the overall run.
- Payload values are keyed by `EventPayload` enum members
  (e.g., `EventPayload.PROMPT.value == "formatted_prompt"`,
  `EventPayload.FUNCTION_CALL.value == "function_call"`).

We dispatch internally by `event_type`:

| LlamaIndex event_type           | ActivityEvent emitted          | Pair semantics |
|---------------------------------|--------------------------------|----------------|
| LLM (start)                     | (none — record llm_call_id)    | open ctx       |
| LLM (end)                       | ReasoningStepCompleted         | per LLM end    |
| FUNCTION_CALL (start)           | ToolCallAttempted              | per tool start |
| FUNCTION_CALL (end, no excpt)   | ToolCallCompleted              | per tool end   |
| EXCEPTION (any)                 | ToolCallFailed (if paired)     | failure path   |
| AGENT_STEP (start)              | ToolCallAttempted (agent step) | per step       |
| AGENT_STEP (end)                | ToolCallCompleted (agent step) | per step end   |
| EMBEDDING / RETRIEVE / TREE     | (none — structural noise)      | —              |
| SUB_QUESTION / QUERY / others   | (none — outer flow)            | —              |
| start_trace / end_trace         | (none — overall run brackets)  | —              |

The EXCEPTION event_type fires when an event-in-progress raises. We
look for an in-flight FUNCTION_CALL or AGENT_STEP with the same
`event_id` and emit `ToolCallFailed` for it. Bare EXCEPTION events
(no matching in-flight) are dropped (auditor sees the gap; this is
the same defensive discipline as the LangChain `on_chain_error`
without `on_chain_start`).

## Three-tier integration model

This callback is Tier A — per-LLM-call + per-FUNCTION_CALL +
per-AGENT_STEP receipts. Full workflow AuditProof requires
Tier B (`@nanorix.workload` decorator) or Tier C (framework
middleware).

## Design constraints

- `framework=` REQUIRED — no silent default.
- No PHI scanning. Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- LlamaIndex dep is optional — `pip install nanorix[llama-index]`.
- Importing without `llama_index.core` succeeds; constructing
  `NanorixLlamaIndexCallback` then raises `ImportError` with a
  remediation hint (error on use, never at import).
- Inherits `llama_index.core.callbacks.base_handler.BaseCallbackHandler`.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    from llama_index.core.callbacks.base_handler import BaseCallbackHandler
    from llama_index.core.callbacks.schema import CBEventType, EventPayload

    _LLAMA_INDEX_AVAILABLE = True
    _LLAMA_INDEX_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    BaseCallbackHandler = object
    _LLAMA_INDEX_AVAILABLE = False
    _LLAMA_INDEX_IMPORT_ERROR = _exc

    # Must stay a class, not `= None`: the module-scope frozensets below read
    # `CBEventType.FUNCTION_CALL` etc. at import time, before any guard runs.
    class CBEventType:  # type: ignore[no-redef]
        """Stand-in exposing the members referenced at module scope."""

        FUNCTION_CALL = "function_call"
        AGENT_STEP = "agent_step"
        LLM = "llm"
        EXCEPTION = "exception"

    EventPayload = None  # only reachable after the __init__ guard

_LLAMA_INDEX_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.llama_index requires the 'llama-index' "
    "optional extra. Install with:  pip install 'nanorix[llama-index]'  "
    "(or pip install llama-index-core>=0.11 alongside nanorix)."
)

__all__ = [
    "NanorixLlamaIndexCallback",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
]


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


# Map exception class names → closed-enum ToolCallFailureKind strings.
_DEFAULT_FAILURE_KIND_MAP: Dict[str, str] = {
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


# Event kinds we treat as structural tool-shape (emit Tool* events).
_TOOL_SHAPE_EVENT_TYPES = frozenset(
    {
        CBEventType.FUNCTION_CALL,
        CBEventType.AGENT_STEP,
    }
)

# Event kinds we treat as reasoning-shape (emit ReasoningStepCompleted on end).
_REASONING_SHAPE_EVENT_TYPES = frozenset(
    {
        CBEventType.LLM,
    }
)

# Event kinds we silently track lifecycle (no events emitted) for
# eventual EXCEPTION pairing — these fire `on_event_start` /
# `on_event_end` but we record only the start ts; if EXCEPTION fires
# with the same event_id we map it back. Note: structurally-noisy
# event kinds (EMBEDDING, RETRIEVE, etc.) are NOT recorded — they
# can fire at high frequency and an auditor doesn't need them at the
# AuditProof's activity_trail tier.


class NanorixLlamaIndexCallback(BaseCallbackHandler):
    """LlamaIndex callback handler that emits Nanorix ActivityEvents.

    Attaches to LlamaIndex's `Settings.callback_manager` (or per-query
    callback manager). Discriminates incoming events by
    `CBEventType` and routes:

    - `LLM` end → `ReasoningStepCompleted`
    - `FUNCTION_CALL` start → `ToolCallAttempted`
    - `FUNCTION_CALL` end → `ToolCallCompleted`
    - `AGENT_STEP` start/end → tool-shape pair (each agent step
      surfaces as a structural unit in the activity_trail)
    - `EXCEPTION` (matching in-flight FUNCTION_CALL / AGENT_STEP) →
      `ToolCallFailed`
    - All other event types → silently dropped (structural noise that
      doesn't belong in the AuditProof's activity_trail tier)

    Args:
        framework: REQUIRED. Compliance framework label.
        capsulefile: REQUIRED. Capsulefile name.
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`.
        metadata: Optional logging-context dict (NOT bound into AuditProof).
        framework_version: Optional framework version string.
        on_event: Optional debugging hook invoked for every emitted
            ActivityEvent.
        buffer_path: Override for the activity-trail buffer location.
        agent_id: Optional agent identifier propagated to all events.
        emit_agent_step_events: Whether AGENT_STEP events emit tool-
            shape receipts. Defaults to ``True``. Set ``False`` if the
            customer prefers only LLM + FUNCTION_CALL receipts (and
            wants AGENT_STEP suppressed as too granular).
        event_starts_to_ignore: Passed to
            `BaseCallbackHandler.__init__`. List of `CBEventType`
            members for which `on_event_start` is silently skipped.
            Defaults to `[]`.
        event_ends_to_ignore: Passed to `BaseCallbackHandler.__init__`.
            Defaults to `[]`.

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
        emit_agent_step_events: bool = True,
        event_starts_to_ignore: Optional[List[CBEventType]] = None,
        event_ends_to_ignore: Optional[List[CBEventType]] = None,
    ) -> None:
        if not _LLAMA_INDEX_AVAILABLE:
            raise ImportError(
                _LLAMA_INDEX_IMPORT_ERROR_MESSAGE
            ) from _LLAMA_INDEX_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixLlamaIndexCallback requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixLlamaIndexCallback requires an explicit `capsulefile=` "
                "argument (e.g., capsulefile='pa-decision-agent')."
            )

        super().__init__(
            event_starts_to_ignore=event_starts_to_ignore or [],
            event_ends_to_ignore=event_ends_to_ignore or [],
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
        self.emit_agent_step_events = emit_agent_step_events

        # In-flight event state. Keys are LlamaIndex `event_id` (str).
        # Values track event_type + tool_call_id + start_ts_ms +
        # parent_id, so EXCEPTION pairing + duration computation work.
        self._in_flight_tools: Dict[str, Dict[str, Any]] = {}
        self._in_flight_llms: Dict[str, Dict[str, Any]] = {}

        # Counter for `step_idx` on `ReasoningStepCompleted` events.
        self._reasoning_step_counter = 0

    # ── helpers ────────────────────────────────────────────────────

    def _now_ms(self) -> int:
        return int(time.monotonic() * 1000)

    def _emit(self, event: Dict[str, Any]) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001 - debug hook must not crash
                pass

    def _classify_tool_error(self, error: BaseException) -> Optional[str]:
        cls_name = type(error).__name__
        kind = _DEFAULT_FAILURE_KIND_MAP.get(cls_name)
        if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
            return kind
        for parent in type(error).__mro__[1:]:
            kind = _DEFAULT_FAILURE_KIND_MAP.get(parent.__name__)
            if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
                return kind
        return None

    @staticmethod
    def _extract_tool_name_and_args(payload: Optional[Dict[str, Any]]) -> Tuple[str, Any]:
        """Pull tool_name + tool_args from a FUNCTION_CALL payload.

        LlamaIndex's payload shape for FUNCTION_CALL events:
        - `payload[EventPayload.TOOL.value]` → ToolMetadata or dict
          (carries tool name + description).
        - `payload[EventPayload.FUNCTION_CALL.value]` → dict with
          `arguments` and possibly `name` keys, OR a string with
          serialized arguments.

        Returns ``(tool_name, tool_args)`` — `tool_name` defaults to
        ``"unknown"`` and `tool_args` to ``{}`` on missing keys.
        """
        tool_name: str = "unknown"
        tool_args: Any = {}
        if not payload:
            return tool_name, tool_args

        tool = payload.get(EventPayload.TOOL.value)
        if tool is not None:
            # ToolMetadata-like object
            name_attr = getattr(tool, "name", None)
            if isinstance(name_attr, str) and name_attr:
                tool_name = name_attr
            elif isinstance(tool, dict):
                n = tool.get("name")
                if isinstance(n, str) and n:
                    tool_name = n

        fn_call = payload.get(EventPayload.FUNCTION_CALL.value)
        if fn_call is not None:
            if isinstance(fn_call, dict):
                # Some integrations carry `name` in the function_call
                # dict; prefer it if not yet known.
                if tool_name == "unknown":
                    n = fn_call.get("name")
                    if isinstance(n, str) and n:
                        tool_name = n
                # Use the entire function_call dict as args (auditor
                # gets full tool-call shape hashed).
                tool_args = fn_call
            else:
                # Could be a JSON string or other primitive — pass through.
                tool_args = fn_call

        return tool_name, tool_args

    @staticmethod
    def _extract_function_output(payload: Optional[Dict[str, Any]]) -> Any:
        """Pull the function-call response payload."""
        if not payload:
            return None
        out = payload.get(EventPayload.FUNCTION_OUTPUT.value)
        if out is not None:
            return out
        # Fallback: some integrations put the output in RESPONSE.
        return payload.get(EventPayload.RESPONSE.value)

    @staticmethod
    def _extract_llm_inputs(payload: Optional[Dict[str, Any]]) -> Any:
        """Render LLM input (prompt or messages) for hashing."""
        if not payload:
            return []
        # Chat-model path: messages list
        messages = payload.get(EventPayload.MESSAGES.value)
        if messages is not None:
            return NanorixLlamaIndexCallback._render_messages(messages)
        prompt = payload.get(EventPayload.PROMPT.value)
        if prompt is not None:
            return prompt
        return []

    @staticmethod
    def _render_messages(messages: Any) -> List[Dict[str, Any]]:
        """Render LlamaIndex ChatMessage list → JSON-serializable dicts.

        LlamaIndex ChatMessage has `role` (an enum) and `content`. We
        coerce to ``{"role": <str>, "content": <str-or-json-safe>}``.
        """
        rendered: List[Dict[str, Any]] = []
        if not isinstance(messages, list):
            return rendered
        for m in messages:
            role = getattr(m, "role", None)
            if role is not None and not isinstance(role, str):
                # MessageRole enum: use .value
                role = getattr(role, "value", str(role))
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                role = m.get("role", role)
                content = m.get("content", "")
            if isinstance(content, (str, list, dict, int, float, bool)) or content is None:
                rendered.append({"role": role or "?", "content": content})
            else:
                rendered.append({"role": role or "?", "content": str(content)})
        return rendered

    @staticmethod
    def _extract_llm_response(payload: Optional[Dict[str, Any]]) -> Any:
        """Render LLM response payload for hashing."""
        if not payload:
            return None
        resp = payload.get(EventPayload.RESPONSE.value)
        if resp is None:
            resp = payload.get(EventPayload.COMPLETION.value)
        if resp is None:
            return None
        # ChatResponse-like: has .message
        msg = getattr(resp, "message", None)
        if msg is not None:
            return NanorixLlamaIndexCallback._render_messages([msg])[0]
        # Has .text or .raw
        text = getattr(resp, "text", None)
        if isinstance(text, str):
            return {"text": text}
        # Fallback: stringify
        if isinstance(resp, (str, list, dict, int, float, bool)):
            return resp
        return str(resp)

    @staticmethod
    def _extract_model_name(payload: Optional[Dict[str, Any]]) -> Optional[str]:
        """Pull model identifier from an LLM payload."""
        if not payload:
            return None
        m = payload.get(EventPayload.MODEL_NAME.value)
        if isinstance(m, str):
            return m
        return None

    # ── BaseCallbackHandler abstract surface ───────────────────────

    def on_event_start(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        parent_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Dispatch on event_type and emit the appropriate event.

        Returns the event_id (LlamaIndex contract — must echo back).
        """
        # Tool-shape events: emit ToolCallAttempted
        if event_type == CBEventType.FUNCTION_CALL:
            tool_name, tool_args = self._extract_tool_name_and_args(payload)
            tool_call_id = event_id or f"function_call:{self._now_ms()}"
            self._in_flight_tools[event_id or tool_call_id] = {
                "start_ts_ms": self._now_ms(),
                "event_type": event_type,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "parent_id": parent_id,
            }
            event = emit_tool_call_attempted(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                agent_id=self.agent_id,
                parent_event_id=parent_id or None,
                buffer_path=self.buffer_path,
            )
            self._emit(event)
            return event_id

        if event_type == CBEventType.AGENT_STEP and self.emit_agent_step_events:
            # Treat each agent step as a structural tool-shape unit.
            # Payload may carry the step's prompt/messages; we capture
            # the ENTIRE payload (sanitized to JSON-safe shapes via
            # canonical_hash's serialization path) as tool_args.
            tool_call_id = event_id or f"agent_step:{self._now_ms()}"
            tool_name = "agent_step"
            self._in_flight_tools[event_id or tool_call_id] = {
                "start_ts_ms": self._now_ms(),
                "event_type": event_type,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "parent_id": parent_id,
            }
            event = emit_tool_call_attempted(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=self._sanitize_payload(payload),
                agent_id=self.agent_id,
                parent_event_id=parent_id or None,
                buffer_path=self.buffer_path,
            )
            self._emit(event)
            return event_id

        if event_type == CBEventType.LLM:
            # Open an LLM context — emission happens at on_event_end.
            self._in_flight_llms[event_id] = {
                "start_ts_ms": self._now_ms(),
                "prompts": self._extract_llm_inputs(payload),
                "model": self._extract_model_name(payload),
                "parent_id": parent_id,
            }
            return event_id

        # Other event types: structural noise (EMBEDDING, RETRIEVE,
        # TREE, SUB_QUESTION, QUERY, etc.) — silently drop.
        return event_id

    def on_event_end(
        self,
        event_type: CBEventType,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Close the matching in-flight event and emit the end event."""
        if event_type == CBEventType.FUNCTION_CALL:
            ctx = self._in_flight_tools.pop(event_id, None)
            if ctx is None:
                # Defensive: end without start
                return
            output = self._extract_function_output(payload)
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
            event = emit_tool_call_completed(
                tool_call_id=ctx["tool_call_id"],
                tool_name=ctx["tool_name"],
                tool_result=self._sanitize_value(output),
                duration_ms=duration_ms,
                agent_id=self.agent_id,
                parent_event_id=ctx.get("parent_id") or None,
                buffer_path=self.buffer_path,
            )
            self._emit(event)
            return

        if event_type == CBEventType.AGENT_STEP and self.emit_agent_step_events:
            ctx = self._in_flight_tools.pop(event_id, None)
            if ctx is None:
                return
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
            event = emit_tool_call_completed(
                tool_call_id=ctx["tool_call_id"],
                tool_name=ctx["tool_name"],
                tool_result=self._sanitize_payload(payload),
                duration_ms=duration_ms,
                agent_id=self.agent_id,
                parent_event_id=ctx.get("parent_id") or None,
                buffer_path=self.buffer_path,
            )
            self._emit(event)
            return

        if event_type == CBEventType.LLM:
            ctx = self._in_flight_llms.pop(event_id, None)
            if ctx is None:
                duration_ms = 0
                prompts: Any = []
                model = None
            else:
                duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
                prompts = ctx["prompts"]
                model = ctx["model"]
            response = self._extract_llm_response(payload)
            event = emit_reasoning_step_completed(
                step_idx=self._reasoning_step_counter,
                prompt=prompts,
                response=response if response is not None else "",
                duration_ms=duration_ms,
                model=model,
                agent_id=self.agent_id,
                buffer_path=self.buffer_path,
            )
            self._reasoning_step_counter += 1
            self._emit(event)
            return

        if event_type == CBEventType.EXCEPTION:
            # EXCEPTION fires when an event-in-progress raises. The
            # payload carries the exception under EventPayload.EXCEPTION.
            self._handle_exception_event(event_id, payload)
            return

        # Other event_type: structural noise.

    def _handle_exception_event(
        self, event_id: str, payload: Optional[Dict[str, Any]]
    ) -> None:
        """Emit `ToolCallFailed` if the EXCEPTION pairs with an
        in-flight tool/agent_step. Bare exceptions (no matching
        in-flight) are dropped — auditor sees the gap, same defensive
        discipline as LangChain's `on_chain_error` without `on_chain_start`.
        """
        ctx = self._in_flight_tools.pop(event_id, None)
        if ctx is None:
            # Could be an LLM-level exception — pop the LLM context.
            # We do NOT emit ReasoningStepCompleted for a failed LLM
            # (same discipline as LangChain `on_llm_error`); the
            # customer's outer code translates fatal LLM errors into
            # capsule destruction_state.
            self._in_flight_llms.pop(event_id, None)
            return

        exc: BaseException
        raw_exc = (payload or {}).get(EventPayload.EXCEPTION.value)
        if isinstance(raw_exc, BaseException):
            exc = raw_exc
        else:
            # Some integrations stringify the exception in the payload.
            exc = Exception(str(raw_exc) if raw_exc is not None else "unknown error")

        duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
        failure_reason = f"{type(exc).__name__}: {exc}"
        failure_kind = self._classify_tool_error(exc)
        event = emit_tool_call_failed(
            tool_call_id=ctx["tool_call_id"],
            tool_name=ctx["tool_name"],
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            failure_kind=failure_kind,
            agent_id=self.agent_id,
            parent_event_id=ctx.get("parent_id") or None,
            buffer_path=self.buffer_path,
        )
        self._emit(event)

    def start_trace(self, trace_id: Optional[str] = None) -> None:
        """Trace bracket — no event emitted. The capsule lifecycle is
        the AuditProof's overall trace; LlamaIndex's notion of
        `start_trace` is internal observability."""
        # Reset the reasoning step counter at trace boundary so each
        # trace starts at step_idx=0 (matches LangChain integration's
        # per-callback-instance counter).
        # NOTE: we do NOT reset the in-flight tool/llm dicts — those
        # belong to the in-flight events, which span trace boundaries
        # in some pathological cases. End-events clean them up.
        return None

    def end_trace(
        self,
        trace_id: Optional[str] = None,
        trace_map: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        """Trace bracket — no event emitted."""
        return None

    # ── value sanitization (JSON-safe for canonical hashing) ───────

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        """Coerce arbitrary values into JSON-safe form for `_canonical_hash`.

        - Primitives + None pass through.
        - Lists / tuples / sets recursed (sets sorted for stability).
        - Dicts recursed (keys coerced to str).
        - ``model_dump()`` / ``dict()`` for Pydantic-like objects.
        - Fallback: ``str(value)``.
        """
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(x) for x in value]
        if isinstance(value, set):
            return [cls._sanitize_value(x) for x in sorted(value, key=repr)]
        if isinstance(value, dict):
            return {str(k): cls._sanitize_value(v) for k, v in value.items()}
        # Pydantic v2
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump()
            except Exception:  # noqa: BLE001
                pass
        # Pydantic v1 / dataclass-style
        if hasattr(value, "dict"):
            try:
                return value.dict()
            except Exception:  # noqa: BLE001
                pass
        return str(value)

    @classmethod
    def _sanitize_payload(cls, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Coerce a LlamaIndex payload dict into JSON-safe form."""
        if not payload:
            return {}
        return {str(k): cls._sanitize_value(v) for k, v in payload.items()}


# Re-exported for caller convenience (tests + customer-debugging scripts).
_canonical_hash_for_test = _canonical_hash
