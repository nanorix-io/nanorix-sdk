"""
LangChain callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (Wave 14, item 1 of 5): bridges
LangChain's existing callback system into Nanorix's in-capsule
ActivityEvent emission helpers. Customer adds one line to their
existing LangChain chain or agent_executor:

    from nanorix.integrations.langchain import NanorixCallback

    callback = NanorixCallback(framework="HIPAA", capsulefile="agent")
    chain.invoke({"input": "..."}, config={"callbacks": [callback]})

The callback runs INSIDE a Nanorix capsule (customer wraps the agent
run via `client.capsules.create()` or the future `@nanorix.workload`
decorator from item 3 of the ship sequence). LangChain's hook surface
maps onto the 4 existing ActivityEvent variants shipped by ADR-021
(`ToolCallAttempted` / `ToolCallCompleted` / `ToolCallFailed` /
`ReasoningStepCompleted`). No new variants — ADR-006 I0
Forever-Standard discipline preserved.

## Three-tier integration model

| Tier | Effort | Claim                                            |
|------|--------|--------------------------------------------------|
| A    | 5 min  | Per-call receipts (this callback)                |
| B    | 30 min | Full workflow destruction proof (decorator)      |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)    |

This callback is Tier A. The claim is partial: per-LLM-call and
per-tool-call receipts streaming into the capsule's activity_trail
buffer; the buffer drains into the AuditProof at capsule destroy. For
full workflow destruction proof (entire customer function wrapped in
capsule lifecycle), upgrade to Tier B (`@nanorix.workload` decorator)
or Tier C (framework middleware). Customer-facing docs at
`docs/integrations/langchain.md` make this distinction explicit.

## Hook → ActivityEvent mapping

| LangChain hook        | ActivityEvent emitted          | Notes                                           |
|-----------------------|--------------------------------|-------------------------------------------------|
| on_chain_start        | (none — record start_ts)       | Open chain context                              |
| on_llm_start          | (none — record llm_call_id)    | Prepare metadata                                |
| on_chat_model_start   | (none — record llm_call_id)    | Same as on_llm_start, chat-model variant        |
| on_llm_end            | ReasoningStepCompleted         | Hash response; record token counts; emit        |
| on_tool_start         | ToolCallAttempted              | Record tool_name + args_hash; emit              |
| on_tool_end           | ToolCallCompleted              | Record result_hash + duration; emit             |
| on_tool_error         | ToolCallFailed                 | Record failure_kind + error message hash; emit  |
| on_chain_end          | (none — flush buffer)          | Buffer drain handled by daemon at destroy       |
| on_chain_error        | (none — mark capsule incomplete) | Capsule destruction_state goes to incomplete  |

The "(none)" hooks track wall-clock + run-id so the corresponding
emission hooks can compute `duration_ms` and pair attempt/complete
events via `tool_call_id`. They write nothing to the buffer.

## Design constraints

- Framework arg REQUIRED — no silent default (ADR-013 customer-sole-declarer).
- No PHI scanning (ADR-013 Alt B). Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- LangChain dep is optional — `pip install nanorix[langchain]`.
- Importing this module without `langchain_core` installed succeeds;
  constructing `NanorixCallback` then raises `ImportError` with a
  remediation hint (error on use, never at import).
- Inherits `langchain_core.callbacks.BaseCallbackHandler`.
- No surface-area dependence on LangChain's heavyweight `langchain`
  package — only `langchain_core` is required.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Mapping, Optional
from uuid import UUID

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
    from langchain_core.callbacks import BaseCallbackHandler

    _LANGCHAIN_AVAILABLE = True
    _LANGCHAIN_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    BaseCallbackHandler = object
    _LANGCHAIN_AVAILABLE = False
    _LANGCHAIN_IMPORT_ERROR = _exc

_LANGCHAIN_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.langchain requires the 'langchain' optional "
    "extra. Install with:  pip install 'nanorix[langchain]'  "
    "(or pip install langchain-core>=0.3 alongside nanorix)."
)

__all__ = [
    "NanorixCallback",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
]


# Framework names match `nanorix.compliance.*Context` framework_id
# values. Customer-supplied free-form strings are also accepted (so
# new compliance contexts can be wired without an SDK release), but
# the canonical labels below are recommended.
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

Customer may pass any non-empty string for `framework=`; the canonical
labels above are convenience constants matching the
`nanorix.compliance.*Context.framework_id` values. The framework arg
is recorded on the callback instance for downstream logging /
debugging; it does NOT enter the AuditProof's hash chain (the binding
into AuditProof.metadata happens via `client.capsules.create(metadata=)`
or `nanorix.compliance.HipaaContext().to_metadata()`, NOT through the
callback).
"""


# Map LangChain exception class names → closed-enum ToolCallFailureKind
# strings (mirrors `services/api/src/cdp_document.rs:97`). Customer can
# override per-tool by subclassing and overriding `_classify_tool_error`.
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


class NanorixCallback(BaseCallbackHandler):
    """LangChain callback handler that emits Nanorix ActivityEvents.

    Attaches to a LangChain chain, agent_executor, or LCEL Runnable
    via the `callbacks=[...]` config slot. Maps each invoked hook
    into the existing 4 ActivityEvent variants without adding any
    new variant (ADR-006 I0 Forever-Standard).

    Args:
        framework: REQUIRED. Compliance framework label (e.g. ``"HIPAA"``,
            ``"GDPR"``, ``"SOC2"``). Customer-declared per ADR-013;
            no silent default. Used for per-callback logging context;
            does NOT enter the AuditProof's hash chain (the
            framework_id field on AuditProof.metadata is established
            via `client.capsules.create(metadata=)` separately).
        capsulefile: REQUIRED. Capsulefile name the agent is running
            under (e.g. ``"pa-decision-agent"``). Used for callback
            logging context. Does NOT replace the actual `capsulefile`
            field on the underlying AuditProof — that field is bound
            at capsule creation by the customer's outer code.
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`. The callback itself does NOT call the
            Nanorix API directly (it writes to the in-capsule activity
            buffer); the api_key is recorded for future out-of-capsule
            mode (V2) and accepted now to keep the public API stable
            across V1/V2.
        metadata: Optional extra metadata dict for logging context only.
            NOT bound into AuditProof. AuditProof metadata is set via
            `client.capsules.create(metadata=)` separately.
        framework_version: Optional framework version string (e.g.
            ``"2026-02"``). Recorded for callback logging context only.
        on_event: Optional callback invoked for every emitted
            ActivityEvent (debugging hook). Receives the raw event
            dict that was written to the buffer.
        buffer_path: Optional override for the activity-trail buffer
            location. Defaults to `nanorix.helpers.activity_events.
            DEFAULT_BUFFER_PATH` (the in-capsule tmpfs path).
        agent_id: Optional agent identifier propagated to every
            emitted event's `agent_id` slot. Useful when multiple
            sub-agents share one capsule (each gets a distinct id).

    Public methods inherited from `BaseCallbackHandler`:
        on_chain_start, on_chain_end, on_chain_error, on_llm_start,
        on_chat_model_start, on_llm_end, on_llm_error,
        on_tool_start, on_tool_end, on_tool_error.

    The handler maintains an in-memory dictionary of in-flight
    `run_id`s (UUID per LangChain run), tracking the wall-clock start
    timestamp + LangChain hook arguments needed to compute
    `duration_ms` and to pair `ToolCallAttempted` /
    `ToolCallCompleted` events via `tool_call_id`.

    Raises:
        ValueError: if `framework` is missing or empty, or if
            `capsulefile` is missing or empty.
    """

    # Marker so LangChain's runtime knows this is a synchronous
    # callback (avoids spawning an async-bridge task per event).
    raise_error = False
    run_inline = True

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
        if not _LANGCHAIN_AVAILABLE:
            raise ImportError(
                _LANGCHAIN_IMPORT_ERROR_MESSAGE
            ) from _LANGCHAIN_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixCallback requires an explicit `framework=` argument "
                "(e.g., framework='HIPAA'). No silent default per "
                "ADR-013 customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixCallback requires an explicit `capsulefile=` "
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

        # In-flight run state. Keys are LangChain UUIDs (run_id /
        # tool_run_id), values are dicts with start timestamp + any
        # context needed at the matching end-hook.
        self._in_flight_chains: Dict[UUID, Dict[str, Any]] = {}
        self._in_flight_llms: Dict[UUID, Dict[str, Any]] = {}
        self._in_flight_tools: Dict[UUID, Dict[str, Any]] = {}

        # Counter for `step_idx` on `ReasoningStepCompleted` events.
        # LangChain doesn't surface a step number, so we maintain one
        # per callback instance (monotonic across all chain runs).
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
            except Exception:  # noqa: BLE001 - debug hook must not crash callback
                pass

    def _classify_tool_error(self, error: BaseException) -> Optional[str]:
        """Map LangChain exception class to closed-enum failure_kind.

        Returns one of `TOOL_CALL_FAILURE_KINDS` or `None` if the
        exception type does not map cleanly. The customer's free-form
        narrative (`failure_reason`) always populates regardless.

        Subclasses can override to inject domain-specific mapping.
        """
        cls_name = type(error).__name__
        kind = _DEFAULT_FAILURE_KIND_MAP.get(cls_name)
        if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
            return kind
        # Walk the MRO for a parent-class match (catches subclasses).
        for parent in type(error).__mro__[1:]:
            kind = _DEFAULT_FAILURE_KIND_MAP.get(parent.__name__)
            if kind is not None and kind in TOOL_CALL_FAILURE_KINDS:
                return kind
        return None

    # ── chain hooks (no events emitted; track lifecycle) ───────────

    def on_chain_start(
        self,
        serialized: Dict[str, Any],
        inputs: Dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Open a chain run context. No event written.

        The chain run is the customer's overall workflow (e.g.,
        agent_executor.invoke). Sub-LLM-calls and sub-tool-calls
        emit their own events; the chain itself is bound into the
        AuditProof via the capsule lifecycle (the customer's outer
        code wraps the chain in `client.capsules.create()` or
        `@nanorix.workload`).
        """
        self._in_flight_chains[run_id] = {
            "start_ts_ms": self._now_ms(),
            "parent_run_id": parent_run_id,
            "tags": tags,
        }

    def on_chain_end(
        self,
        outputs: Dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Close a chain run context. No event written.

        The capsule's daemon drains the activity-trail buffer at
        capsule destroy regardless of whether on_chain_end fires.
        """
        self._in_flight_chains.pop(run_id, None)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Mark chain context as errored. No event written.

        The customer's outer code is responsible for translating
        chain-level failure into the capsule's `destruction_state`
        field (e.g., calling `client.capsules.destroy(...,
        force_incomplete=True)` or letting an exception propagate
        out of the capsule context manager).
        """
        self._in_flight_chains.pop(run_id, None)

    # ── LLM hooks (emit ReasoningStepCompleted at end) ─────────────

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Record LLM call start. No event written.

        We capture the prompt list + model identifier (when present
        in `serialized`) so that on_llm_end can emit a
        `ReasoningStepCompleted` with the corresponding
        `prompt_hash` and `duration_ms`.
        """
        # `serialized` shape varies across LangChain versions; we
        # extract the model identifier defensively. The most common
        # locations are `kwargs["invocation_params"]["model"]` (LCEL
        # invocations) and `serialized["kwargs"]["model_name"]`
        # (legacy chains).
        model = self._extract_model_name(serialized, kwargs)
        self._in_flight_llms[run_id] = {
            "start_ts_ms": self._now_ms(),
            "prompts": list(prompts),
            "model": model,
            "parent_run_id": parent_run_id,
        }

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[Any]],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Record chat-model call start. Same shape as on_llm_start."""
        # Render messages into a JSON-serializable shape: each
        # BaseMessage becomes ``{"role": "...", "content": "..."}``.
        # We hash the rendered list at on_llm_end via _canonical_hash;
        # plaintext messages NEVER enter the AuditProof.
        rendered: List[Dict[str, Any]] = []
        for batch in messages:
            for msg in batch:
                rendered.append(self._render_message(msg))
        model = self._extract_model_name(serialized, kwargs)
        self._in_flight_llms[run_id] = {
            "start_ts_ms": self._now_ms(),
            "prompts": rendered,
            "model": model,
            "parent_run_id": parent_run_id,
        }

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ReasoningStepCompleted` for the just-finished LLM call.

        Hashes the prompt + response via `_canonical_hash`; plaintext
        does NOT enter the AuditProof. `duration_ms` is computed from
        the matching on_llm_start. `step_idx` is a monotonic per-callback
        counter (LangChain doesn't expose a step number; the AuditProof
        consumer can re-derive ordering from `at` timestamps).
        """
        ctx = self._in_flight_llms.pop(run_id, None)
        if ctx is None:
            # on_llm_end without a matching on_llm_start — defensive
            # path. We still emit so the customer sees the event in
            # their AuditProof, but with `duration_ms=0` and no model.
            duration_ms = 0
            prompts: Any = []
            model = None
        else:
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
            prompts = ctx["prompts"]
            model = ctx["model"]

        rendered_response = self._render_llm_result(response)

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

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Pop the in-flight LLM context. No ActivityEvent emitted.

        LLM-level errors typically surface to the customer's outer
        code via raised exceptions; the chain-level retry logic in
        LangChain may invoke `on_llm_start` again with a new run_id.
        Customer's outer code translates fatal errors into the
        capsule's `destruction_state` per ADR-019. We deliberately
        do NOT emit a `ToolCallFailed` here because LLM calls and
        tool calls are distinct concepts in the AuditProof's
        activity trail.
        """
        self._in_flight_llms.pop(run_id, None)

    # ── tool hooks (emit ToolCallAttempted + Completed/Failed) ─────

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ToolCallAttempted` and record start_ts.

        `tool_call_id` is the stringified LangChain `run_id` (UUID
        per tool invocation). The `tool_name` comes from `serialized`
        (LangChain renders `name` here for both Tool and StructuredTool
        invocations); falls back to `"unknown"` defensively.
        """
        tool_name = self._extract_tool_name(serialized)
        # Prefer the `inputs` dict (StructuredTool); fall back to
        # the raw `input_str` (Tool's positional argument).
        tool_args: Any = inputs if inputs is not None else input_str

        tool_call_id = str(run_id)
        event = emit_tool_call_attempted(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._in_flight_tools[run_id] = {
            "start_ts_ms": self._now_ms(),
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "parent_run_id": parent_run_id,
        }
        self._emit(event)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ToolCallCompleted` for the just-finished tool call."""
        ctx = self._in_flight_tools.pop(run_id, None)
        if ctx is None:
            # Defensive: on_tool_end without on_tool_start. Emit with
            # synthetic minimal context so the AuditProof reflects the
            # observation; auditor sees the gap.
            tool_call_id = str(run_id)
            tool_name = "unknown"
            duration_ms = 0
        else:
            tool_call_id = ctx["tool_call_id"]
            tool_name = ctx["tool_name"]
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))

        # `output` is typically a string (Tool) or dict
        # (StructuredTool). Either is JSON-serializable. We pass to
        # `_canonical_hash` which handles both.
        rendered_output = self._render_tool_output(output)

        event = emit_tool_call_completed(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_result=rendered_output,
            duration_ms=duration_ms,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._emit(event)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ToolCallFailed` for the just-failed tool call."""
        ctx = self._in_flight_tools.pop(run_id, None)
        if ctx is None:
            tool_call_id = str(run_id)
            tool_name = "unknown"
            duration_ms = 0
        else:
            tool_call_id = ctx["tool_call_id"]
            tool_name = ctx["tool_name"]
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))

        # Free-form narrative is the exception's str() representation.
        # Closed-enum kind is mapped via _classify_tool_error (returns
        # None when the exception class doesn't map; the helper writes
        # only the free-form narrative in that case, per ADR-021's
        # "sometimes-populated" reserved-slot discipline).
        failure_reason = f"{type(error).__name__}: {error}"
        failure_kind = self._classify_tool_error(error)

        event = emit_tool_call_failed(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            failure_kind=failure_kind,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._emit(event)

    # ── private rendering helpers ──────────────────────────────────

    @staticmethod
    def _extract_tool_name(serialized: Optional[Dict[str, Any]]) -> str:
        """Pull tool name from LangChain's serialized payload.

        Stable across Tool, StructuredTool, and LCEL Runnable kinds:
        ``serialized["name"]`` (most common), then
        ``serialized["id"][-1]`` (fallback for some chain variants),
        then ``"unknown"``.
        """
        if not serialized:
            return "unknown"
        name = serialized.get("name")
        if isinstance(name, str):
            return name
        sid = serialized.get("id")
        if isinstance(sid, list) and sid and isinstance(sid[-1], str):
            return sid[-1]
        return "unknown"

    @staticmethod
    def _extract_model_name(
        serialized: Optional[Dict[str, Any]], kwargs: Mapping[str, Any]
    ) -> Optional[str]:
        """Pull model identifier from LangChain's serialized + kwargs.

        Stable across legacy LLM chains and LCEL Runnable invocations.
        """
        # LCEL invocations carry `invocation_params` in kwargs.
        ip = kwargs.get("invocation_params")
        if isinstance(ip, dict):
            for k in ("model", "model_name", "model_id"):
                v = ip.get(k)
                if isinstance(v, str):
                    return v
        if not serialized:
            return None
        # Legacy chains carry the model in `serialized["kwargs"]`.
        kw = serialized.get("kwargs")
        if isinstance(kw, dict):
            for k in ("model", "model_name", "model_id"):
                v = kw.get(k)
                if isinstance(v, str):
                    return v
        # Some integrations ship the model name at the top of `serialized`.
        for k in ("model", "model_name", "model_id"):
            v = serialized.get(k)
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def _render_message(msg: Any) -> Dict[str, Any]:
        """Render a LangChain BaseMessage into a JSON-serializable dict.

        Stable shape: ``{"role": <type-name>, "content": <str>}``. The
        canonical_hash discipline relies on stable serialization across
        LangChain versions; we capture only the fields that are
        guaranteed to exist on every BaseMessage subclass (``type`` and
        ``content``).
        """
        msg_type = getattr(msg, "type", None) or type(msg).__name__
        content = getattr(msg, "content", "")
        # `content` may be a list (multi-modal); coerce to JSON-safe form.
        if isinstance(content, (list, dict, str, int, float, bool)) or content is None:
            return {"role": msg_type, "content": content}
        return {"role": msg_type, "content": str(content)}

    def _render_llm_result(self, response: Any) -> Dict[str, Any]:
        """Render a LangChain LLMResult into a JSON-serializable dict.

        Captures generation text + token usage (when present), avoiding
        any LangChain object dependencies that can change shape across
        versions.
        """
        rendered: Dict[str, Any] = {"generations": []}
        gens = getattr(response, "generations", None)
        if isinstance(gens, list):
            for batch in gens:
                if not isinstance(batch, list):
                    continue
                batch_out: List[Dict[str, Any]] = []
                for g in batch:
                    text = getattr(g, "text", None)
                    msg = getattr(g, "message", None)
                    item: Dict[str, Any] = {}
                    if isinstance(text, str):
                        item["text"] = text
                    if msg is not None:
                        item["message"] = self._render_message(msg)
                    if item:
                        batch_out.append(item)
                rendered["generations"].append(batch_out)
        # Token usage / model output metadata, when present.
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
            if isinstance(usage, dict):
                rendered["token_usage"] = {
                    k: v
                    for k, v in usage.items()
                    if isinstance(v, (int, float, str, bool))
                }
            model = llm_output.get("model_name") or llm_output.get("model")
            if isinstance(model, str):
                rendered["model"] = model
        return rendered

    @staticmethod
    def _render_tool_output(output: Any) -> Any:
        """Render a tool output for canonical-JSON hashing.

        Strings + JSON-safe types pass through. ``BaseModel``-shaped
        outputs (Pydantic) are rendered via ``model_dump()`` when
        available; otherwise we coerce via ``str()``.
        """
        if output is None or isinstance(output, (str, int, float, bool, list, dict)):
            return output
        # Pydantic v2 model
        if hasattr(output, "model_dump"):
            try:
                return output.model_dump()
            except Exception:  # noqa: BLE001
                pass
        # Pydantic v1 / dataclass-style
        if hasattr(output, "dict"):
            try:
                rendered: Any = output.dict()
                return rendered
            except Exception:  # noqa: BLE001
                pass
        return str(output)


# Re-exported for caller convenience (tests + customer-debugging scripts).
# `_canonical_hash` is the same JCS-flavor used by Rust + verifier; do
# NOT reimplement it.
_canonical_hash_for_test = _canonical_hash
