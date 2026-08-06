"""
AutoGen callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (item 6c): bridges AutoGen's
agent + tool lifecycle into Nanorix's in-capsule ActivityEvent emission
helpers. Customer wraps their existing AutoGen agent / GroupChat with a
small attachment helper:

    from nanorix.integrations.autogen import (
        NanorixAutoGenCallback,
        attach_to_conversable_agent,
    )

    callback = NanorixAutoGenCallback(framework="HIPAA", capsulefile="agent")
    attach_to_conversable_agent(agent, callback)
    # ... agent.initiate_chat(...) / agent.run(...) now emits sub-receipts

AutoGen ships two surfaces in active use as of mid-2026:

- **Legacy (pyautogen 0.2.x / autogen 0.2.x)** — `ConversableAgent` exposes
  `register_reply(trigger, reply_func)` for intercepting message generation
  and `register_hook(hookable_method, hook)` for `process_message_before_send`
  / `process_last_received_message` / `update_agent_state` hooks.
- **v0.4+ (autogen-agentchat ≥0.4)** — event-driven async surface where
  `Agent.on_messages()` emits ChatMessage events; `register_event_handler`
  on `AgentRuntime` subscribes to `ToolCallEvent` / `ToolCallResultEvent`
  / `ModelClientEvent` / `ErrorEvent`.

This callback is **framework-version-agnostic** — its public method
surface is 4 stable lifecycle methods (`on_agent_message_received`,
`on_agent_message_sent`, `on_tool_call_completed`, `on_agent_error`) that
map directly to the existing 4 ActivityEvent variants shipped by.
Attachment helpers (`attach_to_conversable_agent`,
`attach_to_runtime`) wire the callback into the underlying framework's
native dispatch surface, so the callback API stays stable across AutoGen
version upgrades.

## Architectural distinction from LangChain / LangGraph

AutoGen's notion of a "step" is a single agent turn in a multi-agent
conversation (the `GroupChat` orchestrator routes messages between
`ConversableAgent`s). A turn maps onto the AuditProof's activity_trail
in two ways:

- The model call that produces the agent's reply → `ReasoningStepCompleted`
- A tool/function invocation surfaced in the reply's `tool_calls`
  field → `ToolCallAttempted` (and matching Completed/Failed at execution).

Multi-agent topology (supervisor + N workers) is captured via `agent_id`
— customer instantiates one callback per agent_id, OR attaches a shared
callback with per-call `agent_id=` override.

## Hook → ActivityEvent mapping

| AutoGen lifecycle hook              | ActivityEvent emitted        | When                          |
|-------------------------------------|------------------------------|-------------------------------|
| on_agent_message_received           | ToolCallAttempted (per tool) | Agent receives a message with `tool_calls` (one ToolCallAttempted per tool_call entry) |
| on_agent_message_sent               | ReasoningStepCompleted       | Agent emits a reply (model output) |
| on_tool_call_completed              | ToolCallCompleted            | Tool execution returns        |
| on_tool_call_completed (with error) | ToolCallFailed               | Tool execution raises         |
| on_agent_error                      | ToolCallFailed (or none)     | Agent loop raises             |

Messages without `tool_calls` go ONLY through `on_agent_message_sent` →
`ReasoningStepCompleted`. Messages with `tool_calls` emit one
`ToolCallAttempted` per tool_call AND a `ReasoningStepCompleted` for the
underlying model call (the model's text + tool_calls are part of the same
reply). When the tool returns, `on_tool_call_completed` emits the matching
`ToolCallCompleted` (success) or `ToolCallFailed` (error).

## Three-tier integration model

This callback is Tier A — per-message + per-tool-call receipts. Full
workflow AuditProof requires Tier B (`@nanorix.workload`) or
Tier C (framework middleware).

## Design constraints

- `framework=` REQUIRED — no silent default (customer-sole-declarer).
- No PHI scanning (Alt B). Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- AutoGen dep is optional — `pip install nanorix[autogen]`.
- Importing without an AutoGen package succeeds; constructing
  `NanorixAutoGenCallback` (or calling an attach helper) then raises
  `ImportError` with a remediation hint (error on use, never at import).
- Callback does NOT inherit any AutoGen base class — AutoGen's dispatch
  is a per-method registration pattern, not a subclassed handler.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional

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
# first use (constructing the callback or calling an attach helper),
# never at module import.
# AutoGen ships under two distinct package names in active use:
#   - autogen-agentchat (v0.4+) — the modern event-driven surface.
#   - autogen / pyautogen (0.2.x) — the legacy ConversableAgent surface.
# We accept either; `_AUTOGEN_FLAVOR is None` means none is present.
_AUTOGEN_IMPORT_ERROR: Optional[Exception] = None
try:
    import autogen_agentchat  # noqa: F401

    _AUTOGEN_FLAVOR: Optional[str] = "agentchat_v04"
except ImportError:
    try:
        import autogen  # noqa: F401

        _AUTOGEN_FLAVOR = "legacy_v02"
    except ImportError:
        try:
            import pyautogen  # noqa: F401

            _AUTOGEN_FLAVOR = "legacy_v02"
        except ImportError as _exc:  # pragma: no cover - exercised when extra missing
            _AUTOGEN_FLAVOR = None
            _AUTOGEN_IMPORT_ERROR = _exc

_AUTOGEN_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.autogen requires an AutoGen package. "
    "Install with:  pip install 'nanorix[autogen]'  (or pip install "
    "autogen-agentchat>=0.4 alongside nanorix; legacy pyautogen>=0.2 "
    "is also supported)."
)

__all__ = [
    "NanorixAutoGenCallback",
    "attach_to_conversable_agent",
    "attach_to_runtime",
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
# strings (same map as the LangChain / LangGraph / LlamaIndex callbacks).
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


class NanorixAutoGenCallback:
    """AutoGen callback handler that emits Nanorix ActivityEvents.

    Stable adapter API across AutoGen versions: customer instantiates one
    callback (per agent OR one shared with per-call `agent_id=` overrides)
    and attaches it to a `ConversableAgent` / `AgentRuntime` via the
    `attach_to_conversable_agent()` / `attach_to_runtime()` helpers.

    Public lifecycle methods (4 — matches the 4 ActivityEvent variants):

    - `on_agent_message_received(message, sender, recipient)` — agent
      receives a message; if the message includes `tool_calls`, one
      `ToolCallAttempted` is emitted per tool call.
    - `on_agent_message_sent(message, sender, recipient, model=None,
      duration_ms=0, agent_id=None)` — agent emits a reply (model output);
      emits `ReasoningStepCompleted`.
    - `on_tool_call_completed(tool_call_id, tool_name, tool_result,
      duration_ms, error=None, agent_id=None)` — tool execution returns;
      emits `ToolCallCompleted` (success) OR `ToolCallFailed` (error).
    - `on_agent_error(error, agent_id=None, in_flight_tool_call_id=None)`
      — agent loop raises; if `in_flight_tool_call_id` matches an open
      tool call, emits `ToolCallFailed`; otherwise lifecycle no-op.

    Args:
        framework: REQUIRED. Compliance framework label (e.g. ``"HIPAA"``).
            Customer-declared; no silent default.
        capsulefile: REQUIRED. Capsulefile name the agent is running
            under (e.g. ``"pa-decision-agent"``).
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`.
        metadata: Optional extra metadata dict for logging context only.
            NOT bound into AuditProof.
        framework_version: Optional framework version string.
        on_event: Optional callback invoked for every emitted
            ActivityEvent (debugging hook).
        buffer_path: Optional override for the activity-trail buffer
            location. Defaults to the in-capsule tmpfs path.
        agent_id: Optional default agent identifier propagated to every
            emitted event's `agent_id` slot (per-call overrides win).

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
    ) -> None:
        if _AUTOGEN_FLAVOR is None:
            raise ImportError(
                _AUTOGEN_IMPORT_ERROR_MESSAGE
            ) from _AUTOGEN_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixAutoGenCallback requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixAutoGenCallback requires an explicit `capsulefile=` "
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

        # In-flight tool-call state. Keys are tool_call_id (customer-
        # provided OR derived from AutoGen's tool_call dict).
        self._in_flight_tools: Dict[str, Dict[str, Any]] = {}

        # Counter for `step_idx` on `ReasoningStepCompleted` events
        # (monotonic per callback instance; matches the LangChain
        # integration's discipline).
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
        """Map exception class to closed-enum failure_kind.

        Returns one of `TOOL_CALL_FAILURE_KINDS` or `None` if the
        exception type does not map cleanly. The customer's free-form
        narrative (`failure_reason`) always populates regardless.

        Subclasses can override to inject domain-specific mapping.
        """
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
    def _extract_tool_calls(message: Any) -> List[Dict[str, Any]]:
        """Pull tool_calls list from an AutoGen message.

        Stable across v0.2 and v0.4: AutoGen messages adopt the OpenAI
        chat-completion schema for tool_calls — each entry has `id`,
        `type` (== "function"), and `function: {name, arguments}`.
        Returns an empty list when the message has no tool_calls.

        AutoGen v0.2 messages are dicts; v0.4 messages are pydantic
        models with `.tool_calls` attribute. We try both shapes.
        """
        if message is None:
            return []
        # Dict-shape (v0.2 + OpenAI raw)
        if isinstance(message, dict):
            tc = message.get("tool_calls")
            if isinstance(tc, list):
                return list(tc)
            return []
        # Attribute-shape (v0.4)
        tc = getattr(message, "tool_calls", None)
        if isinstance(tc, list):
            return list(tc)
        # Pydantic-model-with-content (some v0.4 ChatMessage variants
        # carry the tool_calls inside a `content` list of dicts).
        content = getattr(message, "content", None)
        if isinstance(content, list):
            calls: List[Dict[str, Any]] = []
            for entry in content:
                if isinstance(entry, dict) and entry.get("type") == "tool_use":
                    calls.append(entry)
            return calls
        return []

    @staticmethod
    def _extract_tool_call_id(tool_call: Any) -> str:
        """Pull a stable tool_call_id from a tool_call entry.

        OpenAI-shape: `tool_call["id"]` (e.g., `"call_abc123"`).
        Anthropic-shape: `tool_call["id"]` (e.g., `"toolu_..."`).
        Fallback: derive from name+monotonic-ms if no id present.
        """
        if isinstance(tool_call, dict):
            tcid = tool_call.get("id") or tool_call.get("tool_call_id")
            if isinstance(tcid, str) and tcid:
                return tcid
        else:
            tcid = getattr(tool_call, "id", None) or getattr(
                tool_call, "tool_call_id", None
            )
            if isinstance(tcid, str) and tcid:
                return tcid
        # Fallback: stable per-message + name + counter (auditor sees
        # the gap if AutoGen omits ids; we still pair start/end via
        # this synthetic id within the callback's in-flight dict).
        return f"autogen_tc:{int(time.monotonic() * 1_000_000)}"

    @staticmethod
    def _extract_tool_name(tool_call: Any) -> str:
        """Pull tool name from a tool_call entry."""
        if isinstance(tool_call, dict):
            fn = tool_call.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                if isinstance(name, str) and name:
                    return name
            # Anthropic-shape: tool_call["name"] directly
            name = tool_call.get("name")
            if isinstance(name, str) and name:
                return name
            return "unknown"
        # Attribute shape
        fn = getattr(tool_call, "function", None)
        if fn is not None:
            name = getattr(fn, "name", None)
            if isinstance(name, str) and name:
                return name
        name = getattr(tool_call, "name", None)
        if isinstance(name, str) and name:
            return name
        return "unknown"

    @staticmethod
    def _extract_tool_args(tool_call: Any) -> Any:
        """Pull tool arguments from a tool_call entry.

        OpenAI-shape: `tool_call["function"]["arguments"]` is a JSON
        STRING; we leave it as-is (the canonical hash will hash the
        string). Anthropic-shape: `tool_call["input"]` is a dict.
        """
        if isinstance(tool_call, dict):
            fn = tool_call.get("function")
            if isinstance(fn, dict):
                args = fn.get("arguments")
                if args is not None:
                    return args
            # Anthropic-shape
            inp = tool_call.get("input")
            if inp is not None:
                return inp
            return {}
        fn = getattr(tool_call, "function", None)
        if fn is not None:
            args = getattr(fn, "arguments", None)
            if args is not None:
                return args
        inp = getattr(tool_call, "input", None)
        if inp is not None:
            return inp
        return {}

    @staticmethod
    def _render_message_for_reasoning(message: Any) -> Any:
        """Render an AutoGen message for ReasoningStepCompleted hashing.

        Drops `tool_calls` (those are hashed separately in their own
        ToolCallAttempted events) and keeps `role` + `content` +
        optional `name` so the auditor can reconstruct the
        conversation turn boundary.
        """
        if message is None:
            return ""
        if isinstance(message, dict):
            rendered: Dict[str, Any] = {}
            for k in ("role", "content", "name"):
                v = message.get(k)
                if v is not None:
                    rendered[k] = v
            return rendered
        # Attribute-shape (v0.4 ChatMessage)
        rendered_attr: Dict[str, Any] = {}
        role = getattr(message, "role", None) or getattr(message, "source", None)
        if role is not None:
            rendered_attr["role"] = role if isinstance(role, str) else str(role)
        content = getattr(message, "content", None)
        if content is not None:
            if isinstance(content, (str, list, dict, int, float, bool)):
                rendered_attr["content"] = content
            else:
                rendered_attr["content"] = str(content)
        name = getattr(message, "name", None)
        if isinstance(name, str):
            rendered_attr["name"] = name
        return rendered_attr or str(message)

    @staticmethod
    def _render_tool_result(result: Any) -> Any:
        """Render a tool result for canonical hashing."""
        if result is None or isinstance(result, (str, int, float, bool, list, dict)):
            return result
        if hasattr(result, "model_dump"):
            try:
                return result.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(result, "dict"):
            try:
                rendered: Any = result.dict()
                return rendered
            except Exception:  # noqa: BLE001
                pass
        return str(result)

    # ── public lifecycle methods (4 — one per ActivityEvent variant) ──

    def on_agent_message_received(
        self,
        message: Any,
        *,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        agent_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> List[str]:
        """Process an inbound message; emit ToolCallAttempted per tool_call.

        When the message includes `tool_calls`, one
        `ToolCallAttempted` event is emitted per tool_call entry. The
        callback records each in-flight tool's `(tool_call_id,
        start_ts_ms, tool_name)` so `on_tool_call_completed` can pair
        attempt + completion events via tool_call_id and compute
        `duration_ms`.

        Messages without `tool_calls` produce NO event here — they
        flow through `on_agent_message_sent` instead.

        Args:
            message: AutoGen message dict OR pydantic model. Inspected
                for `tool_calls`.
            sender: Optional sender-agent name (debug context only).
            recipient: Optional recipient-agent name (debug context only).
            agent_id: Per-call override for the callback's default.
            parent_event_id: Optional customer-declared causal-parent
                reference — points to the prior agent
                turn or reasoning step.

        Returns:
            List of emitted tool_call_ids (empty when no tool_calls).
        """
        tool_calls = self._extract_tool_calls(message)
        emitted_ids: List[str] = []
        if not tool_calls:
            return emitted_ids

        for tc in tool_calls:
            tool_call_id = self._extract_tool_call_id(tc)
            tool_name = self._extract_tool_name(tc)
            tool_args = self._extract_tool_args(tc)

            event = emit_tool_call_attempted(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                agent_id=self._resolve_agent_id(agent_id),
                parent_event_id=parent_event_id,
                buffer_path=self.buffer_path,
            )
            self._in_flight_tools[tool_call_id] = {
                "start_ts_ms": self._now_ms(),
                "tool_name": tool_name,
                "agent_id": self._resolve_agent_id(agent_id),
                "parent_event_id": parent_event_id,
                "sender": sender,
                "recipient": recipient,
            }
            self._emit(event)
            emitted_ids.append(tool_call_id)

        return emitted_ids

    def on_agent_message_sent(
        self,
        message: Any,
        *,
        sender: Optional[str] = None,
        recipient: Optional[str] = None,
        prompt: Any = None,
        model: Optional[str] = None,
        duration_ms: int = 0,
        agent_id: Optional[str] = None,
        step_kind: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit ReasoningStepCompleted for an outbound model-produced reply.

        Each agent turn (the LLM's response to the conversation history)
        is one reasoning step in the AuditProof's activity_trail.

        Args:
            message: AutoGen message dict / model with the agent's
                response. Hashed into `response_hash`.
            sender: Sender-agent name (debug context).
            recipient: Recipient-agent name (debug context).
            prompt: Optional prompt that produced the reply. When
                omitted, the activity_trail records `prompt_hash` of
                empty list (`sha512:[])`). Customer's outer code can
                pass the full conversation history if needed.
            model: Optional model identifier (e.g., `"gpt-4o"`).
            duration_ms: Wall-clock time the LLM call took. Defaults to 0.
            agent_id: Per-call override.
            step_kind: Optional closed-enum reasoning step kind (
                D1). One of `nanorix.helpers.activity_events.
                REASONING_STEP_KINDS`.
            parent_event_id: Optional customer-declared causal-parent
                reference.

        Returns:
            The emitted event dict.
        """
        rendered_response = self._render_message_for_reasoning(message)
        prompt_payload: Any = prompt if prompt is not None else []
        event = emit_reasoning_step_completed(
            step_idx=self._reasoning_step_counter,
            prompt=prompt_payload,
            response=rendered_response,
            duration_ms=duration_ms,
            model=model,
            agent_id=self._resolve_agent_id(agent_id),
            step_kind=step_kind,
            parent_event_id=parent_event_id,
            buffer_path=self.buffer_path,
        )
        self._reasoning_step_counter += 1
        self._emit(event)
        return event

    def on_tool_call_completed(
        self,
        tool_call_id: str,
        *,
        tool_name: Optional[str] = None,
        tool_result: Any = None,
        duration_ms: Optional[int] = None,
        error: Optional[BaseException] = None,
        agent_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Emit ToolCallCompleted (success) OR ToolCallFailed (error).

        Pairs with a prior `on_agent_message_received` via
        `tool_call_id`. When `error` is supplied, emits
        `ToolCallFailed`; otherwise emits `ToolCallCompleted` with
        `tool_result` hashed into `tool_result_hash`.

        Args:
            tool_call_id: Stable id from the matching ToolCallAttempted.
            tool_name: Tool name override; defaults to the recorded
                in-flight name (set during ToolCallAttempted).
            tool_result: Tool's return value. JSON-serializable
                preferred; the helper hashes via `_canonical_hash`.
                Plaintext NEVER enters AuditProof.
            duration_ms: Wall-clock time the tool ran. Defaults to the
                monotonic delta from the matching ToolCallAttempted.
            error: When supplied, emits ToolCallFailed with this
                exception's class mapped to closed-enum failure_kind.
            agent_id: Per-call override.
            parent_event_id: Override for the recorded parent_event_id;
                falls back to the value recorded at attempt time.

        Returns:
            The emitted event dict.
        """
        ctx = self._in_flight_tools.pop(tool_call_id, None)
        if ctx is None:
            # Defensive: completion without start. Auditor sees the gap.
            recorded_tool_name = tool_name or "unknown"
            elapsed_ms = duration_ms if duration_ms is not None else 0
            recorded_agent_id = self._resolve_agent_id(agent_id)
            recorded_parent = parent_event_id
        else:
            recorded_tool_name = tool_name or ctx["tool_name"]
            elapsed_ms = (
                duration_ms
                if duration_ms is not None
                else max(0, self._now_ms() - int(ctx["start_ts_ms"]))
            )
            recorded_agent_id = (
                self._resolve_agent_id(agent_id)
                if agent_id is not None
                else ctx.get("agent_id")
            )
            recorded_parent = (
                parent_event_id
                if parent_event_id is not None
                else ctx.get("parent_event_id")
            )

        if error is not None:
            failure_reason = f"{type(error).__name__}: {error}"
            failure_kind = self._classify_tool_error(error)
            event = emit_tool_call_failed(
                tool_call_id=tool_call_id,
                tool_name=recorded_tool_name,
                failure_reason=failure_reason,
                duration_ms=elapsed_ms,
                failure_kind=failure_kind,
                agent_id=recorded_agent_id,
                parent_event_id=recorded_parent,
                buffer_path=self.buffer_path,
            )
        else:
            rendered = self._render_tool_result(tool_result)
            event = emit_tool_call_completed(
                tool_call_id=tool_call_id,
                tool_name=recorded_tool_name,
                tool_result=rendered,
                duration_ms=elapsed_ms,
                agent_id=recorded_agent_id,
                parent_event_id=recorded_parent,
                buffer_path=self.buffer_path,
            )
        self._emit(event)
        return event

    def on_agent_error(
        self,
        error: BaseException,
        *,
        in_flight_tool_call_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Surface an agent-loop error.

        If `in_flight_tool_call_id` matches an open tool call, emits
        `ToolCallFailed` for that call (and pops the in-flight ctx).
        Otherwise emits NO event — agent-level errors that aren't
        attributable to a specific tool surface to the customer's
        outer code, which translates them into the capsule's
        `destruction_state`.

        Args:
            error: The exception that surfaced.
            in_flight_tool_call_id: Optional pointer to a specific
                in-flight tool call this error attributes to.
            agent_id: Per-call override.
            parent_event_id: Optional causal-parent reference.

        Returns:
            The emitted ToolCallFailed event dict, or None.
        """
        if in_flight_tool_call_id is None:
            return None
        return self.on_tool_call_completed(
            in_flight_tool_call_id,
            error=error,
            agent_id=agent_id,
            parent_event_id=parent_event_id,
        )


# ── Attachment helpers — bridge the callback into AutoGen's surfaces ──


def attach_to_conversable_agent(agent: Any, callback: NanorixAutoGenCallback) -> None:
    """Wire a `NanorixAutoGenCallback` into a legacy ConversableAgent.

    Used with AutoGen 0.2.x (pyautogen / autogen). Hooks into:

    - `register_hook("process_message_before_send", ...)` → fires
      `on_agent_message_sent` when the agent emits a message.
    - `register_hook("process_last_received_message", ...)` → fires
      `on_agent_message_received` when the agent receives a message
      (tool_calls dispatched).
    - `register_reply` is NOT used here — its trigger semantics make
      it ambiguous for cross-agent message routing.

    For tool-call completion + failure events, the customer wraps
    their registered tool functions with `wrap_tool(callback,
    tool_call_id_extractor)` (separate helper not shipped in this
    cohort — customers add a thin try/except wrapper themselves and
    call `callback.on_tool_call_completed(...)` directly).

    AutoGen's per-hook signature contract (verified empirically
    against 0.2.x):

        def process_message_before_send_hook(
            sender, message, recipient, silent
        ) -> str | None  # returns the (possibly modified) message

        def process_last_received_message_hook(message) -> str | None

    Both return the (possibly modified) message; our hooks return the
    message unchanged (we observe only).

    Args:
        agent: A `ConversableAgent` instance (legacy AutoGen).
        callback: The `NanorixAutoGenCallback` to wire in.

    Raises:
        TypeError: if `agent` is not a ConversableAgent-shape object
            (missing `register_hook` method).
    """
    if not hasattr(agent, "register_hook"):
        raise TypeError(
            "attach_to_conversable_agent requires an AutoGen ConversableAgent "
            "instance (must expose `register_hook` method). For AutoGen v0.4+ "
            "use `attach_to_runtime(runtime, callback)` instead."
        )

    def _on_send(
        sender: Any, message: Any, recipient: Any, silent: bool
    ) -> Any:
        # AutoGen 0.2 hooks return the (possibly modified) message; we
        # observe only and return the input unchanged.
        try:
            sender_name = getattr(sender, "name", None) or str(sender)
            recipient_name = getattr(recipient, "name", None) or str(recipient)
            callback.on_agent_message_sent(
                message,
                sender=sender_name,
                recipient=recipient_name,
            )
        except Exception:  # noqa: BLE001 - observation must not break agent
            pass
        return message

    def _on_received(message: Any) -> Any:
        try:
            callback.on_agent_message_received(message)
        except Exception:  # noqa: BLE001
            pass
        return message

    agent.register_hook("process_message_before_send", _on_send)
    agent.register_hook("process_last_received_message", _on_received)


def attach_to_runtime(runtime: Any, callback: NanorixAutoGenCallback) -> None:
    """Wire a `NanorixAutoGenCallback` into an AutoGen v0.4+ AgentRuntime.

    Used with autogen-agentchat ≥0.4. Subscribes to the runtime's
    event stream:

    - `MessageEvent` → `on_agent_message_sent` (sender's outbound)
      OR `on_agent_message_received` (recipient's inbound; tool_calls
      dispatched here).
    - `ToolCallExecutionEvent` (success) → `on_tool_call_completed`
      with `error=None`.
    - `ToolCallExecutionEvent` (error) → `on_tool_call_completed`
      with `error=exc`.
    - `ErrorEvent` → `on_agent_error`.

    AutoGen v0.4's API has churned across 0.4.0/0.4.5/0.5.x; we
    introspect the runtime's available hook surface dynamically:

    - If `runtime.subscribe(event_type, handler)` exists, use it.
    - If `runtime.register_event_handler(event_type, handler)` exists,
      use it.
    - Otherwise raise `RuntimeError` with a diagnostic.

    Customers who need stable behavior across AutoGen v0.4 versions
    should pin their `autogen-agentchat` dependency.

    Args:
        runtime: An `AgentRuntime` instance (autogen-agentchat v0.4+).
        callback: The `NanorixAutoGenCallback` to wire in.

    Raises:
        RuntimeError: if `runtime` does not expose a recognized event
            subscription surface.
    """
    # Best-effort dynamic introspection — AutoGen v0.4 API has not
    # stabilized as of mid-2026; we accept either `subscribe` or
    # `register_event_handler` patterns.
    subscribe = getattr(runtime, "subscribe", None)
    register = getattr(runtime, "register_event_handler", None)
    # Introspected off the runtime, so the signature is only known at call time.
    bind: Optional[Callable[..., Any]] = subscribe if subscribe is not None else register
    if bind is None:
        raise RuntimeError(
            "attach_to_runtime: runtime does not expose `subscribe` or "
            "`register_event_handler` methods. The AutoGen v0.4+ event-runtime "
            "API has not stabilized; pin your `autogen-agentchat` version, "
            "OR wire the callback manually via "
            "`callback.on_agent_message_*`. Diagnostic: type="
            f"{type(runtime).__name__}."
        )

    def _on_message(event: Any) -> None:
        try:
            message = getattr(event, "message", None) or event
            sender = (
                getattr(event, "source", None)
                or getattr(event, "sender", None)
                or getattr(message, "source", None)
            )
            recipient = getattr(event, "recipient", None)
            # If the message carries tool_calls, treat as inbound
            # tool-dispatching event; otherwise treat as outbound
            # reasoning step.
            tool_calls = NanorixAutoGenCallback._extract_tool_calls(message)
            if tool_calls:
                callback.on_agent_message_received(
                    message,
                    sender=sender,
                    recipient=recipient,
                )
            else:
                callback.on_agent_message_sent(
                    message,
                    sender=sender,
                    recipient=recipient,
                )
        except Exception:  # noqa: BLE001
            pass

    def _on_tool_result(event: Any) -> None:
        try:
            tool_call_id = (
                getattr(event, "tool_call_id", None)
                or getattr(event, "id", None)
                or ""
            )
            tool_name = getattr(event, "name", None) or getattr(event, "tool_name", None)
            result = getattr(event, "result", None) or getattr(event, "output", None)
            error = getattr(event, "error", None) or getattr(event, "exception", None)
            duration_ms = getattr(event, "duration_ms", None)
            callback.on_tool_call_completed(
                tool_call_id,
                tool_name=tool_name,
                tool_result=result,
                duration_ms=duration_ms,
                error=error if isinstance(error, BaseException) else None,
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_error(event: Any) -> None:
        try:
            exc = getattr(event, "error", None) or getattr(event, "exception", None)
            in_flight = getattr(event, "tool_call_id", None)
            if isinstance(exc, BaseException):
                callback.on_agent_error(
                    exc, in_flight_tool_call_id=in_flight if isinstance(in_flight, str) else None
                )
        except Exception:  # noqa: BLE001
            pass

    # Wire by event-type-name string (AutoGen v0.4 event types live in
    # `autogen_core.event_types` / `autogen_agentchat.events`; their
    # names changed between 0.4.0 and 0.5.x). We subscribe by string
    # to dodge the version drift; if a name isn't recognized at
    # registration time, the bind call may raise — we swallow and
    # continue (graceful degradation).
    for event_name in ("MessageEvent", "ChatMessage", "AgentMessage"):
        try:
            bind(event_name, _on_message)
        except Exception:  # noqa: BLE001
            pass
    for event_name in (
        "ToolCallExecutionEvent",
        "ToolCallResultEvent",
        "FunctionExecutionEvent",
    ):
        try:
            bind(event_name, _on_tool_result)
        except Exception:  # noqa: BLE001
            pass
    for event_name in ("ErrorEvent", "AgentErrorEvent"):
        try:
            bind(event_name, _on_error)
        except Exception:  # noqa: BLE001
            pass


# Re-exported for caller convenience (tests + customer-debugging scripts).
_canonical_hash_for_test = _canonical_hash
