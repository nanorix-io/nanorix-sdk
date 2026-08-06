"""
LiteLLM callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (Wave 14, item 7 of second-wave): bridges
litellm's `CustomLogger` callback class into Nanorix's in-capsule
ActivityEvent emission helpers. Customer adds two lines to their existing
litellm-routed agent:

    from nanorix.integrations.litellm import NanorixLiteLLMCallback
    import litellm

    callback = NanorixLiteLLMCallback(framework="HIPAA", capsulefile="agent")
    litellm.callbacks = [callback]
    # then any litellm.completion(...) / acompletion(...) call dispatches
    # through the callback's success/failure paths.

litellm is the universal LLM-provider proxy — one call surface that routes
across OpenAI / Anthropic / Bedrock / Vertex / Azure / Gemini / 100+
backends. A large share of healthcare AI teams already use litellm;
bridging it covers those customers regardless of which underlying LLM
they pick.

## Architectural distinction from other framework callbacks

litellm is NOT an agent framework — it's a provider abstraction. There is
no `chain` or `node` concept; the callback interface is purely
LLM-call-centric (success/failure of a single completion request). The
mapping is therefore the simplest of the integration set:

- Each `litellm.completion(...)` (sync) or `litellm.acompletion(...)`
  (async) call surfaces as a `log_success_event` or `log_failure_event`
  invocation on the callback.
- Function-calling completions populate `response_obj.choices[*].message.
  tool_calls` (OpenAI shape, faithfully passed through litellm regardless
  of underlying provider). Each tool-call entry in the response produces
  a `ToolCallAttempted` event on the SDK side (the LLM REQUESTED a tool
  call; whether the customer's outer code actually invoked it is outside
  litellm's view — that's the customer's downstream work). The accompanying
  `ToolCallCompleted` / `ToolCallFailed` for the tool's actual execution
  must be emitted by the customer's own agent loop or via a separate
  framework callback (LangChain, LangGraph, LlamaIndex).

This shape is empirically true for litellm 1.40+ (verified against the
`CustomLogger` source at https://github.com/BerriAI/litellm/blob/main/
litellm/integrations/custom_logger.py — the public hooks have been
stable across minor versions). The 4 ActivityEvent variants shipped by
 cover the entire surface — no new variants — Forever-Standard
preserved.

## Hook → ActivityEvent mapping

| litellm hook                       | Nanorix emission              | When         |
|------------------------------------|-------------------------------|--------------|
| log_pre_api_call                   | (none — record start_ts ctx)  | Open call    |
| log_success_event                  | ReasoningStepCompleted        | Per LLM end  |
| log_success_event (with tool_calls)| + ToolCallAttempted (per call)| LLM requests tool |
| log_failure_event                  | ReasoningStepCompleted (failed)| Per LLM err |
| async_log_pre_api_call             | (none — same ctx)             | Async open   |
| async_log_success_event            | ReasoningStepCompleted        | Async LLM end|
| async_log_failure_event            | ReasoningStepCompleted (failed)| Async LLM err |
| log_post_api_call                  | (none — lifecycle only)       | —            |
| log_stream_event                   | (none — partial chunk)        | —            |

Note: failure events emit `ReasoningStepCompleted` (NOT `ToolCallFailed`).
Per the LangChain integration's discipline, LLM-level errors are reasoning
faults, not tool faults — distinguishing them in the activity_trail
preserves auditor semantics.

When the customer wants to record the EXECUTION outcome of a tool the
LLM requested (the customer's downstream `submit_pa_request` invocation
returning success/failure), they should pair this callback with an agent-
framework callback (LangChain, LangGraph) OR call the helpers directly:

    from nanorix.helpers.activity_events import (
        emit_tool_call_completed, emit_tool_call_failed,
    )

The litellm callback emits the LLM's REQUEST for a tool; the agent's
downstream code emits the tool's RESPONSE.

## Three-tier integration model

| Tier | Effort | Claim                                                       |
|------|--------|-------------------------------------------------------------|
| A    | 5 min  | Per-LLM-call + per-tool-request receipts (this)             |
| B    | 30 min | Full workflow destruction proof (decorator)                 |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)               |

This callback is Tier A. The claim is partial: per-litellm-completion
receipts streaming into the capsule's activity_trail; the buffer drains
into the AuditProof at capsule destroy.

## Design constraints

- `framework=` REQUIRED — no silent default (customer-sole-declarer).
- No PHI scanning (Alt B). Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- litellm dep is optional — `pip install nanorix[litellm]`.
- Importing without `litellm` installed succeeds; constructing
  `NanorixLiteLLMCallback` then raises `ImportError` with a remediation
  hint (error on use, never at import).
- Inherits `litellm.integrations.custom_logger.CustomLogger` (litellm's
  dispatch contract).
- Both sync and async hook paths populate the SAME activity_trail buffer.
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
# first use (constructing the callback), never at module import.
try:
    from litellm.integrations.custom_logger import CustomLogger

    _LITELLM_AVAILABLE = True
    _LITELLM_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    CustomLogger = object
    _LITELLM_AVAILABLE = False
    _LITELLM_IMPORT_ERROR = _exc

_LITELLM_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.litellm requires the 'litellm' optional "
    "extra. Install with:  pip install 'nanorix[litellm]'  "
    "(or pip install litellm>=1.40 alongside nanorix)."
)

__all__ = [
    "NanorixLiteLLMCallback",
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

Same convention as the LangChain / LangGraph integrations: customer may
pass any non-empty string for `framework=`; canonical labels above match
the `nanorix.compliance.*Context.framework_id` values. The framework arg
is recorded on the callback instance for downstream logging / debugging;
it does NOT enter the AuditProof's hash chain (the binding into
AuditProof.metadata happens via `client.capsules.create(metadata=)` or
`nanorix.compliance.HipaaContext().to_metadata()`, NOT through the
callback).
"""


# Map common Python exception class names → closed-enum
# ToolCallFailureKind strings. Same map as the LangChain integration.
# Used for the tool-call surface only; LLM-level failures go through the
# free-form `failure_reason` on `ReasoningStepCompleted`.
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
    "APIConnectionError": "upstream_failure",
    "APIError": "upstream_failure",
    "RateLimitError": "upstream_failure",
    "ServiceUnavailableError": "upstream_failure",
    "CancelledError": "cancelled",
    "KeyboardInterrupt": "cancelled",
}


class NanorixLiteLLMCallback(CustomLogger):
    """LiteLLM callback handler that emits Nanorix ActivityEvents.

    Attaches to litellm via the global `litellm.callbacks = [...]` slot
    (or `litellm.success_callback` / `litellm.failure_callback` lists).
    Each `litellm.completion(...)` / `litellm.acompletion(...)` call
    dispatches `log_pre_api_call` (open) → `log_success_event` (close)
    OR `log_failure_event` (close). The callback emits one
    `ReasoningStepCompleted` per LLM call. If the response carries
    function-calling `tool_calls`, the callback ALSO emits one
    `ToolCallAttempted` per requested tool — the customer's downstream
    agent code is responsible for emitting the matching `Completed` /
    `Failed` events when the tool actually executes.

    Args:
        framework: REQUIRED. Compliance framework label (e.g. ``"HIPAA"``,
            ``"GDPR"``). Customer-declared; no silent default.
        capsulefile: REQUIRED. Capsulefile name the agent is running
            under. Used for callback logging context only.
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`.
        metadata: Optional extra metadata dict for logging context only.
            NOT bound into AuditProof.
        framework_version: Optional framework version string.
        on_event: Optional callback invoked for every emitted
            ActivityEvent (debugging hook).
        buffer_path: Optional override for the activity-trail buffer
            location. Defaults to the in-capsule tmpfs path.
        agent_id: Optional agent identifier propagated to every emitted
            event's `agent_id` slot.
        emit_tool_request_events: Whether to emit `ToolCallAttempted`
            for each `tool_calls` entry in the LLM's response. Defaults
            to ``True``. Set to ``False`` when the customer is also
            running a LangChain / LangGraph callback that already emits
            tool events (avoids double-counting).

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
        emit_tool_request_events: bool = True,
    ) -> None:
        if not _LITELLM_AVAILABLE:
            raise ImportError(
                _LITELLM_IMPORT_ERROR_MESSAGE
            ) from _LITELLM_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixLiteLLMCallback requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixLiteLLMCallback requires an explicit `capsulefile=` "
                "argument (e.g., capsulefile='pa-decision-agent')."
            )

        super().__init__()
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
        self.emit_tool_request_events = emit_tool_request_events

        # In-flight call state (per litellm_call_id). litellm guarantees
        # the same litellm_call_id reaches log_pre_api_call AND the
        # matching log_success_event / log_failure_event.
        self._in_flight_calls: Dict[str, Dict[str, Any]] = {}

        # Counter for `step_idx` on `ReasoningStepCompleted` events
        # (monotonic per callback instance).
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
        """Map exception class to closed-enum failure_kind.

        Used for tool-call-shape failures (when the LLM's tool-call
        request itself surfaces a structured error). LLM-level errors
        do NOT use this; they emit `ReasoningStepCompleted` with
        free-form `failure_reason` only.
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

    @staticmethod
    def _extract_call_id(kwargs: Mapping[str, Any]) -> str:
        """Pull the litellm_call_id from kwargs.

        Stable across litellm 1.x: `kwargs["litellm_call_id"]` is a UUID
        string assigned at completion entry. Falls back to a synthetic
        identifier derived from `id(kwargs)` if absent (defensive — the
        field may be missing in some test harnesses).
        """
        call_id = kwargs.get("litellm_call_id")
        if isinstance(call_id, str) and call_id:
            return call_id
        # Defensive synthetic identifier
        return f"litellm-call-{id(kwargs)}"

    @staticmethod
    def _extract_model(
        kwargs: Mapping[str, Any], model_arg: Optional[str] = None
    ) -> Optional[str]:
        """Pull the model name from litellm kwargs.

        litellm.completion(model="gpt-4o", ...) → kwargs["model"] = "gpt-4o".
        For provider-prefixed models (e.g. "anthropic/claude-3-5-sonnet"),
        the full prefix is recorded as-is.
        """
        if isinstance(model_arg, str) and model_arg:
            return model_arg
        m = kwargs.get("model")
        if isinstance(m, str) and m:
            return m
        return None

    @staticmethod
    def _extract_messages(kwargs: Mapping[str, Any]) -> Any:
        """Pull the messages list from litellm kwargs.

        Stable: `kwargs["messages"]` is the OpenAI-shape list of
        `{"role": "...", "content": "..."}` dicts (litellm normalizes
        across providers to this shape).
        """
        return kwargs.get("messages", [])

    def _render_response(self, response_obj: Any) -> Dict[str, Any]:
        """Render a litellm ModelResponse into a JSON-serializable dict.

        Captures the choices' message content + tool_calls + token usage,
        avoiding any litellm object dependencies that can change shape
        across versions. Plaintext NEVER enters the AuditProof — this
        rendered dict is hashed via _canonical_hash on the consumer side.
        """
        rendered: Dict[str, Any] = {"choices": []}
        choices = self._safe_attr(response_obj, "choices", default=None)
        if isinstance(choices, list):
            for choice in choices:
                item: Dict[str, Any] = {}
                msg = self._safe_attr(choice, "message", default=None)
                if msg is not None:
                    item["message"] = self._render_message(msg)
                idx = self._safe_attr(choice, "index", default=None)
                if isinstance(idx, int):
                    item["index"] = idx
                finish = self._safe_attr(choice, "finish_reason", default=None)
                if isinstance(finish, str):
                    item["finish_reason"] = finish
                rendered["choices"].append(item)
        # Token usage (top-level on ModelResponse)
        usage = self._safe_attr(response_obj, "usage", default=None)
        if usage is not None:
            usage_dict = self._coerce_usage(usage)
            if usage_dict:
                rendered["usage"] = usage_dict
        # Model identifier (litellm sometimes overrides the requested model
        # with the actual served model id; record both in `model`).
        model = self._safe_attr(response_obj, "model", default=None)
        if isinstance(model, str):
            rendered["model"] = model
        return rendered

    @staticmethod
    def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
        """Get an attribute via getattr; fall back to dict-key lookup.

        litellm ModelResponse is a Pydantic model (litellm 1.40+) which
        supports both attribute and dict-key access; older versions used
        plain dicts. This helper handles both.
        """
        try:
            v = getattr(obj, name, default)
            if v is not default:
                return v
        except Exception:  # noqa: BLE001
            pass
        if isinstance(obj, Mapping):
            return obj.get(name, default)
        return default

    def _render_message(self, msg: Any) -> Dict[str, Any]:
        """Render a litellm Message (or dict) into a JSON-safe dict.

        Captures role + content + tool_calls (if present, function-calling).
        """
        role = self._safe_attr(msg, "role", default=None)
        content = self._safe_attr(msg, "content", default=None)
        result: Dict[str, Any] = {}
        if isinstance(role, str):
            result["role"] = role
        if isinstance(content, (str, int, float, bool, list, dict)) or content is None:
            result["content"] = content
        else:
            result["content"] = str(content)
        # tool_calls is the OpenAI function-calling shape:
        # [{"id": "call_X", "type": "function",
        #   "function": {"name": "...", "arguments": "{...}"}}]
        tool_calls = self._safe_attr(msg, "tool_calls", default=None)
        if isinstance(tool_calls, list) and tool_calls:
            rendered_tcs: List[Dict[str, Any]] = []
            for tc in tool_calls:
                rendered_tcs.append(self._render_tool_call(tc))
            result["tool_calls"] = rendered_tcs
        return result

    def _render_tool_call(self, tc: Any) -> Dict[str, Any]:
        """Render a single tool_call entry."""
        result: Dict[str, Any] = {}
        tc_id = self._safe_attr(tc, "id", default=None)
        if isinstance(tc_id, str):
            result["id"] = tc_id
        tc_type = self._safe_attr(tc, "type", default=None)
        if isinstance(tc_type, str):
            result["type"] = tc_type
        fn = self._safe_attr(tc, "function", default=None)
        if fn is not None:
            fn_name = self._safe_attr(fn, "name", default=None)
            fn_args = self._safe_attr(fn, "arguments", default=None)
            fn_dict: Dict[str, Any] = {}
            if isinstance(fn_name, str):
                fn_dict["name"] = fn_name
            if fn_args is not None:
                fn_dict["arguments"] = fn_args if isinstance(fn_args, str) else str(fn_args)
            if fn_dict:
                result["function"] = fn_dict
        return result

    def _coerce_usage(self, usage: Any) -> Dict[str, Any]:
        """Coerce a Usage object/dict into a JSON-safe dict."""
        if isinstance(usage, Mapping):
            return {
                k: v
                for k, v in usage.items()
                if isinstance(v, (int, float, str, bool))
            }
        result: Dict[str, Any] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = self._safe_attr(usage, key, default=None)
            if isinstance(v, (int, float)):
                result[key] = v
        return result

    def _extract_tool_calls_from_response(
        self, response_obj: Any
    ) -> List[Dict[str, Any]]:
        """Pull tool_calls list from response_obj.choices[0].message.tool_calls.

        Returns a list of rendered tool_call dicts (id, type, function).
        Empty list if no function-calling response.
        """
        choices = self._safe_attr(response_obj, "choices", default=None)
        if not isinstance(choices, list) or not choices:
            return []
        first_choice = choices[0]
        msg = self._safe_attr(first_choice, "message", default=None)
        if msg is None:
            return []
        tool_calls = self._safe_attr(msg, "tool_calls", default=None)
        if not isinstance(tool_calls, list):
            return []
        return [self._render_tool_call(tc) for tc in tool_calls]

    # ── unified emission paths (sync + async share these) ──────────

    def _emit_reasoning_step_for_call(
        self,
        *,
        call_id: str,
        kwargs: Mapping[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
        is_failure: bool,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Emit a `ReasoningStepCompleted` event for a litellm completion.

        Single emission path used by both sync and async hooks; failure
        path passes a populated `failure_reason` (LLM-level errors).
        """
        # Compute duration_ms from the in-flight context (monotonic clock)
        # if present; otherwise fall back to end_time - start_time.
        ctx = self._in_flight_calls.pop(call_id, None)
        if ctx is not None:
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
            prompts = ctx.get("messages", [])
            model = ctx.get("model")
        else:
            duration_ms = self._duration_ms_from_datetimes(start_time, end_time)
            prompts = self._extract_messages(kwargs)
            model = self._extract_model(kwargs)

        if is_failure:
            # On failure, response_obj may be None or partial. We hash an
            # explicit failure-shape payload so the activity_trail records
            # a deterministic shape distinct from success.
            response_payload: Dict[str, Any] = {
                "_failure": True,
            }
            if failure_reason is not None:
                response_payload["failure_reason"] = failure_reason
            # litellm sometimes attaches the raw exception in kwargs["exception"]
            exc = kwargs.get("exception")
            if exc is not None and failure_reason is None:
                response_payload["failure_reason"] = f"{type(exc).__name__}: {exc}"
            rendered_response: Any = response_payload
        else:
            rendered_response = self._render_response(response_obj)

        event = emit_reasoning_step_completed(
            step_idx=self._reasoning_step_counter,
            prompt=prompts,
            response=rendered_response,
            duration_ms=duration_ms,
            model=model,
            agent_id=self.agent_id,
            buffer_path=self.buffer_path,
        )
        self._reasoning_step_counter += 1
        self._emit(event)

    def _emit_tool_request_events(
        self, *, call_id: str, response_obj: Any
    ) -> None:
        """Emit `ToolCallAttempted` per `tool_calls` in the response.

        The LLM REQUESTED a tool (function-calling); the customer's
        downstream code is responsible for emitting the matching
        `Completed` / `Failed` event when the tool actually runs.
        """
        if not self.emit_tool_request_events:
            return
        tcs = self._extract_tool_calls_from_response(response_obj)
        for tc in tcs:
            tc_id = tc.get("id") or f"{call_id}-tc-{id(tc)}"
            fn_obj = tc.get("function") or {}
            tool_name = fn_obj.get("name") or "unknown"
            # The LLM-supplied arguments string is treated as the tool_args.
            # litellm passes through the OpenAI shape: arguments is a JSON
            # string the customer's code parses + executes.
            raw_args = fn_obj.get("arguments")
            tool_args: Any = raw_args if raw_args is not None else {}
            event = emit_tool_call_attempted(
                tool_call_id=tc_id,
                tool_name=tool_name,
                tool_args=tool_args,
                agent_id=self.agent_id,
                parent_event_id=call_id,
                buffer_path=self.buffer_path,
            )
            self._emit(event)

    @staticmethod
    def _duration_ms_from_datetimes(start_time: Any, end_time: Any) -> int:
        """Compute duration in milliseconds from start/end datetimes.

        litellm passes Python `datetime.datetime` objects; some versions
        pass floats (Unix timestamps). This helper handles both.
        """
        try:
            if hasattr(start_time, "timestamp") and hasattr(end_time, "timestamp"):
                delta = end_time.timestamp() - start_time.timestamp()
                return max(0, int(delta * 1000))
            if isinstance(start_time, (int, float)) and isinstance(end_time, (int, float)):
                return max(0, int((end_time - start_time) * 1000))
        except Exception:  # noqa: BLE001 - defensive
            pass
        return 0

    # ── sync hooks ─────────────────────────────────────────────────

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: Any) -> None:
        """Open the in-flight context for a litellm completion.

        Captures monotonic start time + model + messages so the matching
        success/failure hook can compute duration and recover prompts
        without depending on the success-event's kwargs payload.
        """
        try:
            call_id = self._extract_call_id(kwargs or {})
            self._in_flight_calls[call_id] = {
                "start_ts_ms": self._now_ms(),
                "model": self._extract_model(kwargs or {}, model),
                "messages": messages if messages is not None else [],
            }
        except Exception:  # noqa: BLE001 - never crash the customer's call path
            pass

    def log_success_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Emit `ReasoningStepCompleted` (success) + tool-request events."""
        try:
            kw = kwargs or {}
            call_id = self._extract_call_id(kw)
            self._emit_reasoning_step_for_call(
                call_id=call_id,
                kwargs=kw,
                response_obj=response_obj,
                start_time=start_time,
                end_time=end_time,
                is_failure=False,
            )
            self._emit_tool_request_events(call_id=call_id, response_obj=response_obj)
        except Exception:  # noqa: BLE001 - never crash the customer's call path
            pass

    def log_failure_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Emit `ReasoningStepCompleted` with a failure-shape payload."""
        try:
            kw = kwargs or {}
            call_id = self._extract_call_id(kw)
            self._emit_reasoning_step_for_call(
                call_id=call_id,
                kwargs=kw,
                response_obj=response_obj,
                start_time=start_time,
                end_time=end_time,
                is_failure=True,
            )
        except Exception:  # noqa: BLE001 - never crash the customer's call path
            pass

    # ── async hooks ────────────────────────────────────────────────

    async def async_log_pre_api_call(
        self, model: Any, messages: Any, kwargs: Any
    ) -> None:
        """Async equivalent of log_pre_api_call.

        Same in-flight context capture path; litellm dispatches this for
        `litellm.acompletion(...)` (async clients).
        """
        self.log_pre_api_call(model, messages, kwargs)

    async def async_log_success_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Async equivalent of log_success_event."""
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        """Async equivalent of log_failure_event."""
        self.log_failure_event(kwargs, response_obj, start_time, end_time)


# Re-exported for caller convenience (tests + customer-debugging scripts).
_canonical_hash_for_test = _canonical_hash
_DEFAULT_FAILURE_KIND_MAP_for_test = _DEFAULT_FAILURE_KIND_MAP
# Re-export tool helpers for the customer's downstream agent code (the
# matching Completed / Failed events when the LLM-requested tool actually
# executes — outside the litellm callback's view).
__all__ += [
    "emit_tool_call_completed",
    "emit_tool_call_failed",
]
