"""
LangGraph callback handler — Tier A (proxy/library) integration.

Per the integration ship sequence (Wave 14, item 6a of second-wave): bridges
LangGraph's callback surface into Nanorix's in-capsule ActivityEvent
emission helpers. Customer adds one line to their existing LangGraph
agent invocation:

    from nanorix.integrations.langgraph import NanorixLangGraphCallback

    callback = NanorixLangGraphCallback(framework="HIPAA", capsulefile="agent")
    graph.invoke({"input": "..."}, config={"callbacks": [callback]})

LangGraph DOES NOT define its own callback base class — it dispatches into
the standard `langchain_core.callbacks.BaseCallbackHandler` interface
(verified empirically against langgraph 1.x: each node invocation surfaces
as an `on_chain_start` / `on_chain_end` cycle, where `serialized["name"]`
= the node name and `tags` includes `"graph:step:N"`; `metadata` carries
`langgraph_node`). LLM calls inside nodes route through `on_llm_*` /
`on_chat_model_start`, and tool calls route through `on_tool_*`. The same
4 ActivityEvent variants shipped by ADR-021 cover the surface — no new
variants — Forever-Standard preserved (ADR-006 I0).

## Architectural distinction from the LangChain callback

A LangGraph node IS structurally a tool from the AuditProof's perspective
(it's a unit of work the agent invoked). The LangGraph callback emits:

- `ToolCallAttempted` on `on_chain_start` *when the chain is a graph node*
  (heuristic: `tags` contains `"graph:step:*"` OR
  `metadata["langgraph_node"]` is set).
- `ToolCallCompleted` on the matching `on_chain_end`.
- `ToolCallFailed` on the matching `on_chain_error`.
- `ReasoningStepCompleted` on `on_llm_end` (any LLM call inside any node).
- Tool calls invoked from inside a node STILL emit their own
  `ToolCallAttempted/Completed/Failed` via `on_tool_*` (the node and the
  tool both get receipts; the tool's `parent_event_id` points to the
  enclosing node's `tool_call_id`).
- Lifecycle hooks for non-node chains (the outer graph wrapper) write
  nothing.

This shape mirrors how a graph topology serializes into the activity
trail: each node = a structural step, each LLM call = a reasoning step,
each tool invocation = a tool call. Auditor tooling can reconstruct the
graph traversal from `(parent_event_id, tool_name, agent_id)` triples.

## Hook → ActivityEvent mapping

| LangChain hook (LangGraph dispatches via this) | Nanorix emission           | When         |
|------------------------------------------------|----------------------------|--------------|
| on_chain_start (tagged graph:step:*)           | ToolCallAttempted (node)   | Per node     |
| on_chain_end (tagged graph:step:*)             | ToolCallCompleted (node)   | Per node end |
| on_chain_error (tagged graph:step:*)           | ToolCallFailed (node)      | Per node err |
| on_chain_start (NOT tagged graph:step:*)       | (none — outer graph wrap)  | —            |
| on_chain_end (NOT tagged)                      | (none — outer graph wrap)  | —            |
| on_chain_error (NOT tagged)                    | (none — outer graph wrap)  | —            |
| on_chat_model_start / on_llm_start             | (none — record llm_call_id)| Open ctx     |
| on_llm_end                                     | ReasoningStepCompleted     | Per LLM end  |
| on_llm_error                                   | (none — lifecycle only)    | —            |
| on_tool_start                                  | ToolCallAttempted          | Per tool     |
| on_tool_end                                    | ToolCallCompleted          | Per tool end |
| on_tool_error                                  | ToolCallFailed             | Per tool err |

## Three-tier integration model

| Tier | Effort | Claim                                                       |
|------|--------|-------------------------------------------------------------|
| A    | 5 min  | Per-node + per-LLM-call + per-tool-call receipts (this)     |
| B    | 30 min | Full workflow destruction proof (decorator)                 |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)               |

This callback is Tier A. The claim is partial: per-node/LLM/tool receipts
streaming into the capsule's activity_trail; the buffer drains into the
AuditProof at capsule destroy.

## Design constraints

- `framework=` REQUIRED — no silent default (ADR-013 customer-sole-declarer).
- No PHI scanning (ADR-013 Alt B). Callback is structural.
- No raw payload retention. SHA-512 hex digests only.
- LangGraph dep is optional — `pip install nanorix[langgraph]`.
- Importing without `langgraph` / `langchain_core` succeeds; constructing
  `NanorixLangGraphCallback` then raises `ImportError` with a remediation
  hint (error on use, never at import — both deps are guarded).
- Inherits `langchain_core.callbacks.BaseCallbackHandler` (LangGraph's
  dispatch contract).
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

# Optional deps — imported lazily; the remediation error is raised on
# first use (constructing the callback), never at module import.
try:
    from langchain_core.callbacks import BaseCallbackHandler
    import langgraph  # noqa: F401

    _LANGGRAPH_AVAILABLE = True
    _LANGGRAPH_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    BaseCallbackHandler = object
    _LANGGRAPH_AVAILABLE = False
    _LANGGRAPH_IMPORT_ERROR = _exc

_LANGGRAPH_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.langgraph requires the 'langgraph' optional "
    "extra. Install with:  pip install 'nanorix[langgraph]'  "
    "(or pip install langgraph>=0.2 alongside nanorix; LangGraph depends "
    "on langchain_core — both must be importable)."
)

__all__ = [
    "NanorixLangGraphCallback",
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

Same convention as the LangChain integration: customer may pass any
non-empty string for `framework=`; canonical labels above match the
`nanorix.compliance.*Context.framework_id` values. The framework arg is
recorded on the callback instance for downstream logging / debugging; it
does NOT enter the AuditProof's hash chain (the binding into
AuditProof.metadata happens via `client.capsules.create(metadata=)` or
`nanorix.compliance.HipaaContext().to_metadata()`, NOT through the
callback).
"""


# Map LangChain exception class names → closed-enum ToolCallFailureKind
# strings. Same map as the LangChain integration.
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


# LangGraph's dispatch tag prefix. Verified empirically against
# langgraph 1.x: each node invocation tags the chain run with
# "graph:step:N" plus other tags. The presence of this tag (or
# `metadata["langgraph_node"]`) is how we discriminate a node chain from
# the outer graph wrapper or a LCEL Runnable.
_LANGGRAPH_NODE_TAG_PREFIX = "graph:step:"


class NanorixLangGraphCallback(BaseCallbackHandler):
    """LangGraph callback handler that emits Nanorix ActivityEvents.

    Attaches to a LangGraph compiled graph via the `callbacks=[...]`
    config slot on `graph.invoke(...)` / `graph.stream(...)` /
    `graph.ainvoke(...)`. Each graph node executes as a `chain` from
    LangChain's perspective (LangGraph dispatches via the standard
    `BaseCallbackHandler` interface) — we discriminate node chains
    from the outer graph wrapper via the `"graph:step:*"` tag and
    emit `ToolCallAttempted` / `Completed` / `Failed` for nodes,
    `ReasoningStepCompleted` for LLM calls, and the standard tool
    pair for any tool invoked inside a node.

    Args:
        framework: REQUIRED. Compliance framework label (e.g. ``"HIPAA"``,
            ``"GDPR"``). Customer-declared per ADR-013; no silent default.
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
        emit_node_events: Whether to emit `ToolCallAttempted/Completed/
            Failed` for each LangGraph node. Defaults to ``True``. Set
            to ``False`` if the customer prefers only LLM + tool
            sub-receipts (and not per-node receipts) — useful for
            very-fan-out graphs where node-level granularity inflates
            the activity_trail.

    Raises:
        ValueError: if `framework` or `capsulefile` is missing/empty.
    """

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
        emit_node_events: bool = True,
    ) -> None:
        if not _LANGGRAPH_AVAILABLE:
            raise ImportError(
                _LANGGRAPH_IMPORT_ERROR_MESSAGE
            ) from _LANGGRAPH_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixLangGraphCallback requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                "ADR-013 customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixLangGraphCallback requires an explicit `capsulefile=` "
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
        self.emit_node_events = emit_node_events

        # In-flight run state (per UUID).
        # Node chains: tool-shape pair. Tools: tool-shape pair.
        # LLM calls: reasoning-shape pair.
        self._in_flight_nodes: Dict[UUID, Dict[str, Any]] = {}
        self._in_flight_tools: Dict[UUID, Dict[str, Any]] = {}
        self._in_flight_llms: Dict[UUID, Dict[str, Any]] = {}
        # Non-node chains we explicitly opened (e.g., outer graph
        # wrapper). We track ids so the matching close hook is a
        # no-op even if it arrives without the discriminator.
        self._in_flight_outer_chains: Dict[UUID, Dict[str, Any]] = {}

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
    def _is_langgraph_node(
        tags: Optional[List[str]], metadata: Optional[Mapping[str, Any]]
    ) -> bool:
        """Heuristic: is this `on_chain_start` a LangGraph node?

        Verified against langgraph 1.x:
        - `tags` contains a `"graph:step:N"` entry (most reliable).
        - `metadata["langgraph_node"]` is set (alternate signal).

        Either signal counts. If neither is present, the chain is
        either the outer graph wrapper, a LCEL Runnable, or a
        LangChain agent — we ignore it for node-event emission.
        """
        if tags:
            for t in tags:
                if isinstance(t, str) and t.startswith(_LANGGRAPH_NODE_TAG_PREFIX):
                    return True
        if metadata and isinstance(metadata, Mapping):
            if "langgraph_node" in metadata:
                return True
        return False

    @staticmethod
    def _extract_node_name(
        serialized: Optional[Dict[str, Any]],
        metadata: Optional[Mapping[str, Any]],
    ) -> str:
        """Pull the LangGraph node name from the dispatch payload.

        Stable across langgraph 1.x: `serialized["name"]` carries the
        node name (the function name registered via `add_node`); falls
        back to `metadata["langgraph_node"]`, then `"unknown_node"`.
        """
        if serialized:
            name = serialized.get("name")
            if isinstance(name, str) and name:
                return name
        if metadata and isinstance(metadata, Mapping):
            name = metadata.get("langgraph_node")
            if isinstance(name, str) and name:
                return name
        return "unknown_node"

    @staticmethod
    def _extract_tool_name(serialized: Optional[Dict[str, Any]]) -> str:
        """Pull tool name from LangChain's serialized payload."""
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
        ip = kwargs.get("invocation_params")
        if isinstance(ip, dict):
            for k in ("model", "model_name", "model_id"):
                v = ip.get(k)
                if isinstance(v, str):
                    return v
        if not serialized:
            return None
        kw = serialized.get("kwargs")
        if isinstance(kw, dict):
            for k in ("model", "model_name", "model_id"):
                v = kw.get(k)
                if isinstance(v, str):
                    return v
        for k in ("model", "model_name", "model_id"):
            v = serialized.get(k)
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def _render_message(msg: Any) -> Dict[str, Any]:
        msg_type = getattr(msg, "type", None) or type(msg).__name__
        content = getattr(msg, "content", "")
        if isinstance(content, (list, dict, str, int, float, bool)) or content is None:
            return {"role": msg_type, "content": content}
        return {"role": msg_type, "content": str(content)}

    def _render_llm_result(self, response: Any) -> Dict[str, Any]:
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
        if output is None or isinstance(output, (str, int, float, bool, list, dict)):
            return output
        if hasattr(output, "model_dump"):
            try:
                return output.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(output, "dict"):
            try:
                rendered: Any = output.dict()
                return rendered
            except Exception:  # noqa: BLE001
                pass
        return str(output)

    # ── chain hooks (node-aware: emit tool-shape on graph nodes) ───

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
        """Emit `ToolCallAttempted` if this chain is a LangGraph node;
        otherwise treat as outer-graph lifecycle (no event)."""
        is_node = self._is_langgraph_node(tags, metadata)
        if not (is_node and self.emit_node_events):
            self._in_flight_outer_chains[run_id] = {
                "start_ts_ms": self._now_ms(),
                "parent_run_id": parent_run_id,
                "tags": tags,
            }
            return

        node_name = self._extract_node_name(serialized, metadata)
        # `inputs` is the node's input state — we hash it as the
        # "tool args" for the AuditProof.
        tool_call_id = str(run_id)
        event = emit_tool_call_attempted(
            tool_call_id=tool_call_id,
            tool_name=node_name,
            tool_args=inputs,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._in_flight_nodes[run_id] = {
            "start_ts_ms": self._now_ms(),
            "node_name": node_name,
            "tool_call_id": tool_call_id,
            "parent_run_id": parent_run_id,
        }
        self._emit(event)

    def on_chain_end(
        self,
        outputs: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ToolCallCompleted` if this run_id matches an in-flight
        node; otherwise lifecycle no-op."""
        ctx = self._in_flight_nodes.pop(run_id, None)
        if ctx is None:
            self._in_flight_outer_chains.pop(run_id, None)
            return

        rendered = self._render_tool_output(outputs)
        duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
        event = emit_tool_call_completed(
            tool_call_id=ctx["tool_call_id"],
            tool_name=ctx["node_name"],
            tool_result=rendered,
            duration_ms=duration_ms,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._emit(event)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Emit `ToolCallFailed` if this run_id matches an in-flight
        node; otherwise lifecycle no-op."""
        ctx = self._in_flight_nodes.pop(run_id, None)
        if ctx is None:
            self._in_flight_outer_chains.pop(run_id, None)
            return

        duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
        failure_reason = f"{type(error).__name__}: {error}"
        failure_kind = self._classify_tool_error(error)
        event = emit_tool_call_failed(
            tool_call_id=ctx["tool_call_id"],
            tool_name=ctx["node_name"],
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            failure_kind=failure_kind,
            agent_id=self.agent_id,
            parent_event_id=str(parent_run_id) if parent_run_id is not None else None,
            buffer_path=self.buffer_path,
        )
        self._emit(event)

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
        ctx = self._in_flight_llms.pop(run_id, None)
        if ctx is None:
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
        """LLM-level errors do NOT produce ToolCallFailed (same
        discipline as the LangChain integration)."""
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
        tool_name = self._extract_tool_name(serialized)
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
        ctx = self._in_flight_tools.pop(run_id, None)
        if ctx is None:
            tool_call_id = str(run_id)
            tool_name = "unknown"
            duration_ms = 0
        else:
            tool_call_id = ctx["tool_call_id"]
            tool_name = ctx["tool_name"]
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
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
        ctx = self._in_flight_tools.pop(run_id, None)
        if ctx is None:
            tool_call_id = str(run_id)
            tool_name = "unknown"
            duration_ms = 0
        else:
            tool_call_id = ctx["tool_call_id"]
            tool_name = ctx["tool_name"]
            duration_ms = max(0, self._now_ms() - int(ctx["start_ts_ms"]))
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


# Re-exported for caller convenience (tests + customer-debugging scripts).
_canonical_hash_for_test = _canonical_hash
