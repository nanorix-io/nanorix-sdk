"""
OpenTelemetry GenAI semantic-conventions exporter — second-channel observability.

Per / item 8 of the second-wave integration ship sequence: emits
OTel-conformant GenAI spans **alongside** Nanorix's own ActivityEvent
emission. The two channels coexist:

- **Channel 1 (existing)**: `ActivityEvent` records → in-capsule
  `/data/activity_events.jsonl` → drained at destroy → bound into AuditProof
  hash chain ( 4 closed-set variants — Forever-Standard).
- **Channel 2 (this module)**: OTel `gen_ai.*` spans → customer's existing
  TracerProvider → flows into customer's existing observability stack
  (Datadog, Splunk, Honeycomb, Logfire, New Relic, etc.). NOT bound into
  AuditProof. NOT cryptographically attested. Pure observability.

The two channels carry the same per-call signal but in two different
shapes for two different audiences:

- Auditor reads the AuditProof's activity_trail (cryptographically
  attested, sealed, binary-comparable).
- Engineer reads the OTel spans in their existing dashboard (latency
  histograms, error-rate panels, distributed traces).

## Architectural distinction from `litellm.callbacks = ["otel"]`

litellm's built-in OTel exporter ships `gen_ai.*` spans directly from
litellm's success/failure hooks. This module is **also** wired into the
same hook fan-out, but emits Nanorix-tagged spans with an extra
`nanorix.audit_proof_pending = true` attribute so that customer dashboards
can correlate OTel spans → AuditProof at capsule destroy. Customers who
already use litellm's native OTel exporter can keep doing so; this module
is for customers who don't have OTel instrumented yet AND want the
correlation tag.

## OTel GenAI semantic-conventions conformance

Per <https://opentelemetry.io/docs/specs/semconv/gen-ai/> v1.36.0
(reference checkpoint; conventions are NOT yet Stable — Development
status). This module emits:

**REQUIRED**:
- `gen_ai.operation.name` ("chat" | "text_completion" | "embedding")
- `gen_ai.provider.name` (e.g., "openai", "anthropic", "aws.bedrock")

**CONDITIONALLY REQUIRED**:
- `error.type` (when the operation errored)
- `gen_ai.request.model` (when known; populated for chat/text_completion)
- `gen_ai.conversation.id` (when known)

**RECOMMENDED**:
- `gen_ai.request.{temperature, top_k, top_p, max_tokens}`
- `gen_ai.usage.{input_tokens, output_tokens}`
- `gen_ai.response.{id, model, finish_reasons, time_to_first_chunk}`

**OPT-IN (sensitive — OFF by default per Nanorix zero-retention)**:
- `gen_ai.system_instructions`
- `gen_ai.input.messages`
- `gen_ai.output.messages`
- `gen_ai.tool.definitions`
- `gen_ai.tool.call.{arguments, result}`

When `capture_content=False` (default), the sensitive content attributes
are populated with the value `"[redacted; see AuditProof for hash]"` —
the customer can still cross-reference the OTel span back to the
AuditProof's hash via the `nanorix.audit_proof_pending` correlation tag
plus the timestamp window.

When `capture_content=True`, the customer has consented to emitting full
content into their observability stack. Their compliance scope decision;
their downstream pipeline (Datadog / Splunk / Honeycomb) inherits the
content sensitivity.

## Two usage modes

### Mode A — observe an existing Nanorix integration (recommended)

```python
from nanorix.integrations.litellm import NanorixLiteLLMCallback
from nanorix.integrations.otel_genai import NanorixOTelGenAIExporter
from opentelemetry import trace
import litellm

tracer = trace.get_tracer("my-app")
nx_callback = NanorixLiteLLMCallback(framework="HIPAA", capsulefile="agent")
otel_exporter = NanorixOTelGenAIExporter(
    tracer=tracer, framework="HIPAA", capsulefile="agent",
)
otel_exporter.attach_to(nx_callback)

litellm.callbacks = [nx_callback]
# now every litellm.completion(...) emits BOTH ActivityEvents AND OTel spans
```

`attach_to()` replaces the callback's `on_event` hook with a fan-out that
preserves the customer's pre-existing on_event AND dispatches each event
into the OTel exporter.

### Mode B — standalone (explicit emission at customer call sites)

```python
exporter = NanorixOTelGenAIExporter(
    tracer=tracer, framework="HIPAA", capsulefile="agent",
)

with exporter.span_for_llm_call(
    model="gpt-4o", provider="openai",
    operation="chat",
) as span:
    response = openai_client.chat.completions.create(...)
    exporter.record_response(
        span,
        response_id=response.id,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
        finish_reasons=[c.finish_reason for c in response.choices],
    )
```

## Critical discipline anchors

- ** Forever-Standard**: 4 ActivityEvent variants ONLY. This
  module ADDS A SECOND CHANNEL — it does NOT add new variants. The OTel
  exporter consumes the existing 4-variant ActivityEvent stream
  (Mode A) or emits standalone spans without writing to the activity
  buffer (Mode B).
- ** customer-sole-declarer**: `framework=` REQUIRED — no silent
  default.
- ** brand discipline**: AuditProof / Capsulefile vocabulary in
  all customer-facing strings.
- **Zero-retention**: prompt/completion content emission is OPT-IN OFF
  by default. Default behavior emits `"[redacted; see AuditProof for
  hash]"` for sensitive attributes.
- **Customer-call-path safety**: every emission is wrapped in
  try/except — an OTel exporter bug NEVER crashes customer code.
- **Optional dep**: `pip install nanorix[otel-genai]` requires
  `opentelemetry-api>=1.20` + `opentelemetry-sdk>=1.20`.
- **Status flag**: emitted attributes use the OTel GenAI namespace,
  which is in **Development** status per the v1.36.0 spec. The exporter
  module docstring (this) flags the status; customer dashboards may
  treat the attributes as experimental until OTel marks them Stable.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

# Optional dep — imported lazily; the remediation error is raised on
# first use (constructing the exporter), never at module import.
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.trace import (
        Span,
        SpanKind,
        Status,
        StatusCode,
        Tracer,
    )

    _OTEL_AVAILABLE = True
    _OTEL_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    _otel_trace = None
    Span = SpanKind = Status = StatusCode = Tracer = None
    _OTEL_AVAILABLE = False
    _OTEL_IMPORT_ERROR = _exc

_OTEL_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.otel_genai requires the 'otel-genai' optional "
    "extra. Install with:  pip install 'nanorix[otel-genai]'  "
    "(or pip install opentelemetry-api>=1.20 opentelemetry-sdk>=1.20 "
    "alongside nanorix)."
)


__all__ = [
    "NanorixOTelGenAIExporter",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
    "REDACTED_PLACEHOLDER",
    "OTEL_GENAI_SEMCONV_VERSION",
]


# ── Constants ───────────────────────────────────────────────────────


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
"""Recommended canonical framework labels (same set as other integrations)."""


REDACTED_PLACEHOLDER = "[redacted; see AuditProof for hash]"
"""Default substitute for sensitive content when `capture_content=False`.

Customer dashboards see this string instead of plaintext; the
plaintext-equivalent SHA-512 hash lives on the AuditProof, retrievable
via the `nanorix.audit_proof_pending` correlation tag + capsule_id +
timestamp window.
"""


OTEL_GENAI_SEMCONV_VERSION = "1.36.0-development"
"""Reference checkpoint of the OTel GenAI semantic conventions.

NOT yet Stable per the OTel project. Customer dashboards should treat
attributes under the `gen_ai.*` namespace as experimental until OTel
graduates the conventions to Stable.
"""


# Closed-enum operation names per OTel GenAI spec.
_ALLOWED_OPERATIONS = frozenset(
    {
        "chat",
        "text_completion",
        "embedding",
        "create_agent",
        "execute_tool",
        "generate_content",
    }
)


# Provider-prefix → canonical OTel `gen_ai.provider.name` value.
# Mirrors litellm's provider-prefix convention (the most common multi-
# provider entrypoint). Customer can override per-call.
_PROVIDER_PREFIX_MAP: Mapping[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "bedrock": "aws.bedrock",
    "vertex": "gcp.vertex_ai",
    "vertex_ai": "gcp.vertex_ai",
    "gemini": "gcp.gemini",
    "azure": "azure.ai.openai",
    "azure_openai": "azure.ai.openai",
    "cohere": "cohere",
    "mistral": "mistral_ai",
    "groq": "groq",
    "together_ai": "together_ai",
    "deepseek": "deepseek",
    "perplexity": "perplexity",
    "xai": "xai",
}


# ── Internal helpers ────────────────────────────────────────────────


def _infer_provider_from_model(model: Optional[str]) -> Optional[str]:
    """Map a litellm-style provider-prefixed model id → OTel provider name.

    Examples:
        - "openai/gpt-4o" → "openai"
        - "anthropic/claude-3-5-sonnet-20241022" → "anthropic"
        - "bedrock/anthropic.claude-3-sonnet-20240229-v1:0" → "aws.bedrock"
        - "gpt-4o" → None (no prefix; customer must supply provider=)
    """
    if not isinstance(model, str) or "/" not in model:
        return None
    prefix = model.split("/", 1)[0].lower()
    return _PROVIDER_PREFIX_MAP.get(prefix)


def _infer_provider_from_event_model(model: Optional[str]) -> Optional[str]:
    """Same as `_infer_provider_from_model` but tolerates None gracefully."""
    return _infer_provider_from_model(model) if model else None


def _strip_provider_prefix(model: Optional[str]) -> Optional[str]:
    """If model has a provider/ prefix, return the unprefixed model id.

    Otherwise return the input unchanged.
    """
    if not isinstance(model, str):
        return model
    if "/" not in model:
        return model
    prefix, rest = model.split("/", 1)
    if prefix.lower() in _PROVIDER_PREFIX_MAP:
        return rest
    return model


def _coerce_attr_value(v: Any) -> Any:
    """Coerce a value into an OTel-attribute-compatible type.

    OTel attributes accept: str, bool, int, float, sequences thereof.
    Anything else is stringified.
    """
    if v is None:
        return ""
    if isinstance(v, (str, bool, int, float)):
        return v
    if isinstance(v, (list, tuple)) and all(
        isinstance(x, (str, bool, int, float)) for x in v
    ):
        return list(v)
    return str(v)


# ── Public exporter class ──────────────────────────────────────────


class NanorixOTelGenAIExporter:
    """OTel GenAI semantic-conventions exporter for Nanorix capsules.

    Two usage modes:

    **Mode A — Observe an existing Nanorix integration callback.**
    Call `exporter.attach_to(callback)` to fan out every emitted
    ActivityEvent into a corresponding OTel span. The customer's
    pre-existing `on_event` hook (if any) is preserved and chained.

    **Mode B — Standalone explicit emission.**
    Call `exporter.span_for_llm_call(...)` as a context manager around
    the customer's LLM call site, then `exporter.record_response(...)`
    to populate response attributes. Or use the lower-level
    `emit_llm_call(...)` for a one-shot emission.

    Args:
        tracer: An OpenTelemetry `Tracer` instance the customer already
            has wired into their observability stack. Spans emitted by
            this exporter flow into whatever `TracerProvider` the
            customer configured.
        framework: REQUIRED. Compliance framework label (e.g. ``"HIPAA"``,
            ``"GDPR"``). Customer-declared; no silent default.
            Recorded as `nanorix.compliance.framework` attribute on every
            emitted span.
        capsulefile: REQUIRED. Capsulefile name the agent runs under.
            Recorded as `nanorix.capsulefile` attribute.
        capture_content: Whether to emit full prompt/completion content
            into OTel attributes. Defaults to ``False`` per Nanorix
            zero-retention discipline. When ``False``, sensitive
            attributes (`gen_ai.input.messages`, `gen_ai.output.messages`,
            `gen_ai.system_instructions`, `gen_ai.tool.call.arguments`,
            `gen_ai.tool.call.result`) emit `REDACTED_PLACEHOLDER`. When
            ``True``, the customer has consented to plaintext content in
            their observability stack — their compliance scope.
        framework_version: Optional framework version (e.g. ``"2026-02"``).
            Recorded as `nanorix.compliance.framework_version`.
        api_key: Optional Nanorix API key. Defaults to env
            `NANORIX_API_KEY`. Not transmitted by the exporter; recorded
            for downstream-API correlation only.
        agent_id: Optional sub-agent identifier. Recorded as
            `nanorix.agent_id` attribute on every span.
        capsule_id: Optional capsule_id. Recorded as
            `nanorix.capsule_id` attribute (correlates the span window
            to the AuditProof at capsule destroy).
        on_span: Optional callback invoked with each completed span
            (debugging hook).

    Raises:
        ValueError: if `framework` or `capsulefile` is missing/empty.
    """

    def __init__(
        self,
        *,
        tracer: Tracer,
        framework: str,
        capsulefile: str,
        capture_content: bool = False,
        framework_version: Optional[str] = None,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        capsule_id: Optional[str] = None,
        on_span: Optional[Callable[[Span], None]] = None,
    ) -> None:
        if not _OTEL_AVAILABLE:
            raise ImportError(
                _OTEL_IMPORT_ERROR_MESSAGE
            ) from _OTEL_IMPORT_ERROR
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixOTelGenAIExporter requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default per "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixOTelGenAIExporter requires an explicit "
                "`capsulefile=` argument (e.g., capsulefile='pa-decision-agent')."
            )
        if tracer is None:
            raise ValueError(
                "NanorixOTelGenAIExporter requires an explicit `tracer=` "
                "argument (an OpenTelemetry Tracer instance the customer "
                "already wired into their observability stack)."
            )

        self.tracer = tracer
        self.framework = framework
        self.capsulefile = capsulefile
        self.capture_content = bool(capture_content)
        self.framework_version = framework_version
        self.api_key = (
            api_key if api_key is not None else os.environ.get("NANORIX_API_KEY")
        )
        self.agent_id = agent_id
        self.capsule_id = capsule_id
        self.on_span = on_span

        # In-flight LLM-call spans, keyed by tool_call_id / step_idx for
        # later correlation. Mode-A attach uses this to pair
        # ActivityEvent → OTel span on the same call.
        self._call_spans: Dict[str, Span] = {}

    # ── Mode A: attach to existing Nanorix integration callback ────

    def attach_to(self, callback: Any) -> None:
        """Wire this exporter into an existing Nanorix integration callback.

        Replaces the callback's `on_event` hook with a fan-out that
        preserves any pre-existing hook AND dispatches each emitted
        ActivityEvent into this exporter's OTel-span emission.

        Compatible with any callback exposing an ``on_event`` attribute
        — the LangChain / LangGraph / LiteLLM / LlamaIndex callbacks
        all do (their constructor ``on_event`` arg). Re-callable safely;
        successive ``attach_to`` calls chain hooks.

        Args:
            callback: A Nanorix integration callback instance (e.g.,
                ``NanorixLiteLLMCallback``).

        Raises:
            ValueError: if `callback` doesn't expose an `on_event`
                attribute (not a Nanorix integration callback).
        """
        if not hasattr(callback, "on_event"):
            raise ValueError(
                "NanorixOTelGenAIExporter.attach_to() requires a callback "
                "with an `on_event` attribute (e.g., NanorixCallback, "
                "NanorixLiteLLMCallback, NanorixLlamaIndexCallback, "
                "NanorixLangGraphCallback)."
            )
        prior_hook = getattr(callback, "on_event", None)

        def _fanout(event: Dict[str, Any]) -> None:
            # Customer's pre-existing on_event runs first
            if prior_hook is not None:
                try:
                    prior_hook(event)
                except Exception:  # noqa: BLE001 - never crash callback
                    pass
            # Then the OTel exporter consumes the event
            try:
                self.handle_activity_event(event)
            except Exception:  # noqa: BLE001 - never crash callback
                pass

        callback.on_event = _fanout

    def handle_activity_event(self, event: Mapping[str, Any]) -> Optional[Span]:
        """Translate one ActivityEvent dict into an OTel GenAI span.

        Emits the span synchronously (start + end). Returns the span
        (post-end; useful for tests + custom on_span callback).

        ActivityEvent variant → OTel mapping:

        - ``reasoning_step_completed`` → `gen_ai.operation.name=chat`
          span with model + token usage + duration.
        - ``tool_call_attempted`` → `gen_ai.operation.name=execute_tool`
          span (start-only; pairs with `_completed`/`_failed` via
          `tool_call_id`).
        - ``tool_call_completed`` → closes the matching `_attempted`
          span with success status + duration.
        - ``tool_call_failed`` → closes the matching `_attempted` span
          with `Status(StatusCode.ERROR)` + `error.type` attribute.

        Defensive: malformed event → logged-and-dropped (caller's
        try/except in `_fanout` already handles).
        """
        event_type = event.get("event")
        if event_type == "reasoning_step_completed":
            return self._handle_reasoning_step(event)
        if event_type == "tool_call_attempted":
            return self._handle_tool_call_attempted(event)
        if event_type == "tool_call_completed":
            return self._handle_tool_call_completed(event)
        if event_type == "tool_call_failed":
            return self._handle_tool_call_failed(event)
        # Unknown event type — drop silently (Forever-Standard discipline:
        # the 4 variants are closed-set; anything else is a producer bug).
        return None

    def _handle_reasoning_step(
        self, event: Mapping[str, Any]
    ) -> Span:
        model = event.get("model")
        provider = _infer_provider_from_event_model(model)
        bare_model = _strip_provider_prefix(model)
        duration_ms = event.get("duration_ms")

        # The ActivityEvent failure-shape has the same `event` type
        # (`reasoning_step_completed`) but a `failure_reason` may be
        # detectable via the response_hash content; we don't have access
        # to the unhashed payload, so we emit a non-error span and let
        # the customer's downstream tooling correlate via the AuditProof.
        # If a `failure_reason` field is explicitly set on the event,
        # we mark the span as errored.
        is_failure = bool(event.get("failure_reason")) or bool(
            event.get("is_failure")
        )

        span_name = f"chat {bare_model}" if bare_model else "chat"
        span = self.tracer.start_span(span_name, kind=SpanKind.CLIENT)
        try:
            self._set_common_attrs(span)
            span.set_attribute("gen_ai.operation.name", "chat")
            if provider:
                span.set_attribute("gen_ai.provider.name", provider)
            if bare_model:
                span.set_attribute("gen_ai.request.model", bare_model)
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                span.set_attribute("nanorix.duration_ms", int(duration_ms))
            step_idx = event.get("step_idx")
            if isinstance(step_idx, int):
                span.set_attribute("nanorix.step_idx", step_idx)
            step_kind = event.get("step_kind")
            if isinstance(step_kind, dict):
                kind_str = step_kind.get("type")
                if isinstance(kind_str, str):
                    span.set_attribute("nanorix.step_kind", kind_str)
            elif isinstance(step_kind, str):
                span.set_attribute("nanorix.step_kind", step_kind)
            agent_id = event.get("agent_id") or self.agent_id
            if agent_id:
                span.set_attribute("nanorix.agent_id", str(agent_id))
            parent_event_id = event.get("parent_event_id")
            if parent_event_id:
                span.set_attribute(
                    "nanorix.parent_event_id", str(parent_event_id)
                )
            # Hash references (sometimes-populated; not sensitive).
            for hash_field in ("prompt_hash", "response_hash", "summary_hash"):
                v = event.get(hash_field)
                if isinstance(v, str):
                    span.set_attribute(f"nanorix.{hash_field}", v)
            # Sensitive-content placeholders — OPT-IN
            self._set_redacted_or_content(
                span,
                "gen_ai.input.messages",
                None,  # callback path doesn't have plaintext
            )
            self._set_redacted_or_content(
                span,
                "gen_ai.output.messages",
                None,
            )
            if is_failure:
                span.set_attribute("error.type", "llm_error")
                span.set_status(
                    Status(StatusCode.ERROR, "LLM call failed")
                )
            else:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        self._invoke_on_span(span)
        return span

    def _handle_tool_call_attempted(self, event: Mapping[str, Any]) -> Span:
        tool_call_id = str(event.get("tool_call_id") or "unknown")
        tool_name = event.get("tool_name") or "unknown"
        span_name = f"execute_tool {tool_name}"
        span = self.tracer.start_span(span_name, kind=SpanKind.INTERNAL)
        try:
            self._set_common_attrs(span)
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", str(tool_name))
            span.set_attribute("gen_ai.tool.call.id", tool_call_id)
            agent_id = event.get("agent_id") or self.agent_id
            if agent_id:
                span.set_attribute("nanorix.agent_id", str(agent_id))
            parent_event_id = event.get("parent_event_id")
            if parent_event_id:
                span.set_attribute(
                    "nanorix.parent_event_id", str(parent_event_id)
                )
            tool_args_hash = event.get("tool_args_hash")
            if isinstance(tool_args_hash, str):
                span.set_attribute("nanorix.tool_args_hash", tool_args_hash)
            self._set_redacted_or_content(
                span,
                "gen_ai.tool.call.arguments",
                None,
            )
        except Exception:  # noqa: BLE001
            # Never crash customer callback; close the span and continue.
            span.end()
            return span
        # Hold the span open; matching completed/failed event closes it.
        self._call_spans[tool_call_id] = span
        return span

    def _handle_tool_call_completed(self, event: Mapping[str, Any]) -> Optional[Span]:
        tool_call_id = str(event.get("tool_call_id") or "unknown")
        duration_ms = event.get("duration_ms")
        span = self._call_spans.pop(tool_call_id, None)
        if span is None:
            # Defensive: completed without attempted. Open + immediately
            # close a synthetic span so the customer dashboard reflects
            # the observation.
            span = self.tracer.start_span(
                f"execute_tool {event.get('tool_name') or 'unknown'}",
                kind=SpanKind.INTERNAL,
            )
            try:
                self._set_common_attrs(span)
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute(
                    "gen_ai.tool.name", str(event.get("tool_name") or "unknown")
                )
                span.set_attribute("gen_ai.tool.call.id", tool_call_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            tool_result_hash = event.get("tool_result_hash")
            if isinstance(tool_result_hash, str):
                span.set_attribute(
                    "nanorix.tool_result_hash", tool_result_hash
                )
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                span.set_attribute("nanorix.duration_ms", int(duration_ms))
            self._set_redacted_or_content(
                span,
                "gen_ai.tool.call.result",
                None,
            )
            span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        self._invoke_on_span(span)
        return span

    def _handle_tool_call_failed(self, event: Mapping[str, Any]) -> Optional[Span]:
        tool_call_id = str(event.get("tool_call_id") or "unknown")
        duration_ms = event.get("duration_ms")
        failure_reason = event.get("failure_reason") or ""
        failure_kind = event.get("failure_kind")
        if isinstance(failure_kind, dict):
            failure_kind_str = failure_kind.get("type")
        elif isinstance(failure_kind, str):
            failure_kind_str = failure_kind
        else:
            failure_kind_str = None

        span = self._call_spans.pop(tool_call_id, None)
        if span is None:
            span = self.tracer.start_span(
                f"execute_tool {event.get('tool_name') or 'unknown'}",
                kind=SpanKind.INTERNAL,
            )
            try:
                self._set_common_attrs(span)
                span.set_attribute("gen_ai.operation.name", "execute_tool")
                span.set_attribute(
                    "gen_ai.tool.name", str(event.get("tool_name") or "unknown")
                )
                span.set_attribute("gen_ai.tool.call.id", tool_call_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                span.set_attribute("nanorix.duration_ms", int(duration_ms))
            error_type = failure_kind_str or "tool_error"
            span.set_attribute("error.type", str(error_type))
            if failure_kind_str:
                span.set_attribute(
                    "nanorix.tool_failure_kind", str(failure_kind_str)
                )
            if failure_reason:
                span.set_attribute(
                    "nanorix.tool_failure_reason",
                    str(failure_reason)[:1024],  # cap at 1KB for OTel
                )
            span.record_exception(_SyntheticToolFailure(str(failure_reason)))
            span.set_status(
                Status(
                    StatusCode.ERROR,
                    description=str(failure_reason)[:256] if failure_reason else "Tool failed",
                )
            )
        finally:
            span.end()
        self._invoke_on_span(span)
        return span

    # ── Mode B: standalone explicit emission ──────────────────────

    @contextmanager
    def span_for_llm_call(
        self,
        *,
        model: str,
        provider: Optional[str] = None,
        operation: str = "chat",
        request_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        temperature: Optional[float] = None,
        top_k: Optional[int] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_instructions: Optional[Any] = None,
        input_messages: Optional[Any] = None,
        tool_definitions: Optional[Any] = None,
    ) -> Iterator[Span]:
        """Open an OTel GenAI span around an LLM call site.

        Customer wraps their LLM call in this context manager. On exit,
        the span is closed and dispatched to the configured tracer.
        Errors raised inside the block mark the span errored and
        re-raise (the customer's call path is preserved).

        Args:
            model: Model identifier. Provider-prefixed (e.g.,
                ``"openai/gpt-4o"``) is supported; the prefix is parsed
                into `gen_ai.provider.name` and the remainder into
                `gen_ai.request.model`.
            provider: Override for `gen_ai.provider.name` (when model
                isn't provider-prefixed).
            operation: One of `"chat"`, `"text_completion"`,
                `"embedding"`, `"create_agent"`, `"execute_tool"`,
                `"generate_content"`. Defaults to ``"chat"``.
            request_id: Optional customer request id (for trace
                correlation).
            conversation_id: Optional conversation id
                (`gen_ai.conversation.id`).
            temperature, top_k, top_p, max_tokens: Optional request
                parameters per OTel spec.
            system_instructions, input_messages, tool_definitions:
                Sensitive content. Emitted only when
                `capture_content=True`; otherwise REDACTED_PLACEHOLDER.

        Yields:
            The active OTel `Span`. Customer can call
            `exporter.record_response(span, ...)` inside the block to
            populate response attributes.
        """
        if operation not in _ALLOWED_OPERATIONS:
            raise ValueError(
                f"operation '{operation}' is not in the closed-enum set; "
                f"allowed: {sorted(_ALLOWED_OPERATIONS)}"
            )
        bare_model = _strip_provider_prefix(model)
        inferred_provider = _infer_provider_from_model(model)
        final_provider = provider or inferred_provider
        span_name = f"{operation} {bare_model}" if bare_model else operation
        span = self.tracer.start_span(span_name, kind=SpanKind.CLIENT)
        try:
            self._set_common_attrs(span)
            span.set_attribute("gen_ai.operation.name", operation)
            if final_provider:
                span.set_attribute("gen_ai.provider.name", final_provider)
            if bare_model:
                span.set_attribute("gen_ai.request.model", bare_model)
            if request_id:
                span.set_attribute("nanorix.request_id", str(request_id))
            if conversation_id:
                span.set_attribute("gen_ai.conversation.id", str(conversation_id))
            if temperature is not None:
                span.set_attribute("gen_ai.request.temperature", float(temperature))
            if top_k is not None:
                span.set_attribute("gen_ai.request.top_k", int(top_k))
            if top_p is not None:
                span.set_attribute("gen_ai.request.top_p", float(top_p))
            if max_tokens is not None:
                span.set_attribute("gen_ai.request.max_tokens", int(max_tokens))
            self._set_redacted_or_content(
                span, "gen_ai.system_instructions", system_instructions
            )
            self._set_redacted_or_content(
                span, "gen_ai.input.messages", input_messages
            )
            self._set_redacted_or_content(
                span, "gen_ai.tool.definitions", tool_definitions
            )
            yield span
            # If we reach here, no exception inside the block.
            current_status = span.status.status_code if hasattr(span, "status") else None
            if current_status != StatusCode.ERROR:
                span.set_status(Status(StatusCode.OK))
        except BaseException as exc:  # noqa: BLE001 - re-raise after marking
            try:
                span.set_attribute("error.type", type(exc).__name__)
                span.record_exception(exc)
                span.set_status(
                    Status(
                        StatusCode.ERROR, description=f"{type(exc).__name__}: {exc}"[:256]
                    )
                )
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            span.end()
            self._invoke_on_span(span)

    def record_response(
        self,
        span: Span,
        *,
        response_id: Optional[str] = None,
        response_model: Optional[str] = None,
        finish_reasons: Optional[List[str]] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        time_to_first_chunk: Optional[float] = None,
        output_messages: Optional[Any] = None,
    ) -> None:
        """Populate response-side attributes on an open span.

        Use inside the `span_for_llm_call` context manager:

        ```python
        with exporter.span_for_llm_call(model="gpt-4o") as span:
            response = openai_client.chat.completions.create(...)
            exporter.record_response(
                span,
                response_id=response.id,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                finish_reasons=[c.finish_reason for c in response.choices],
            )
        ```
        """
        if span is None:
            return
        try:
            if response_id:
                span.set_attribute("gen_ai.response.id", str(response_id))
            if response_model:
                span.set_attribute(
                    "gen_ai.response.model", str(_strip_provider_prefix(response_model))
                )
            if finish_reasons is not None:
                span.set_attribute(
                    "gen_ai.response.finish_reasons",
                    [str(r) for r in finish_reasons],
                )
            if isinstance(input_tokens, (int, float)) and input_tokens >= 0:
                span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
            if isinstance(output_tokens, (int, float)) and output_tokens >= 0:
                span.set_attribute(
                    "gen_ai.usage.output_tokens", int(output_tokens)
                )
            if isinstance(time_to_first_chunk, (int, float)) and time_to_first_chunk >= 0:
                span.set_attribute(
                    "gen_ai.response.time_to_first_chunk", float(time_to_first_chunk)
                )
            self._set_redacted_or_content(
                span, "gen_ai.output.messages", output_messages
            )
        except Exception:  # noqa: BLE001 - never crash customer
            pass

    def emit_llm_call(
        self,
        *,
        model: str,
        provider: Optional[str] = None,
        operation: str = "chat",
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        finish_reasons: Optional[List[str]] = None,
        response_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
        is_error: bool = False,
        error_type: Optional[str] = None,
    ) -> Span:
        """One-shot LLM-call span emission (no context manager needed).

        For customers who don't want the context-manager shape — pass
        all attributes at once and the exporter opens + immediately
        closes the span. Returns the closed span.
        """
        if operation not in _ALLOWED_OPERATIONS:
            raise ValueError(
                f"operation '{operation}' is not in the closed-enum set; "
                f"allowed: {sorted(_ALLOWED_OPERATIONS)}"
            )
        bare_model = _strip_provider_prefix(model)
        inferred_provider = _infer_provider_from_model(model)
        final_provider = provider or inferred_provider
        span_name = f"{operation} {bare_model}" if bare_model else operation
        span = self.tracer.start_span(span_name, kind=SpanKind.CLIENT)
        try:
            self._set_common_attrs(span)
            span.set_attribute("gen_ai.operation.name", operation)
            if final_provider:
                span.set_attribute("gen_ai.provider.name", final_provider)
            if bare_model:
                span.set_attribute("gen_ai.request.model", bare_model)
            if response_id:
                span.set_attribute("gen_ai.response.id", str(response_id))
            if isinstance(input_tokens, (int, float)) and input_tokens >= 0:
                span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
            if isinstance(output_tokens, (int, float)) and output_tokens >= 0:
                span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
            if finish_reasons is not None:
                span.set_attribute(
                    "gen_ai.response.finish_reasons",
                    [str(r) for r in finish_reasons],
                )
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                span.set_attribute("nanorix.duration_ms", int(duration_ms))
            if is_error:
                span.set_attribute("error.type", error_type or "llm_error")
                span.set_status(
                    Status(StatusCode.ERROR, description=error_type or "LLM error")
                )
            else:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        self._invoke_on_span(span)
        return span

    def emit_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        duration_ms: Optional[int] = None,
        is_error: bool = False,
        error_type: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> Span:
        """One-shot tool-execution span emission."""
        span = self.tracer.start_span(
            f"execute_tool {tool_name}", kind=SpanKind.INTERNAL
        )
        try:
            self._set_common_attrs(span)
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", str(tool_name))
            span.set_attribute("gen_ai.tool.call.id", str(tool_call_id))
            if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
                span.set_attribute("nanorix.duration_ms", int(duration_ms))
            if is_error:
                span.set_attribute("error.type", error_type or "tool_error")
                if failure_reason:
                    span.set_attribute(
                        "nanorix.tool_failure_reason", str(failure_reason)[:1024]
                    )
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=str(failure_reason or error_type or "Tool error")[:256],
                    )
                )
            else:
                span.set_status(Status(StatusCode.OK))
        finally:
            span.end()
        self._invoke_on_span(span)
        return span

    # ── shared helpers ────────────────────────────────────────────

    def _set_common_attrs(self, span: Span) -> None:
        """Populate Nanorix correlation attributes on every emitted span.

        These attributes let customer dashboards correlate OTel spans →
        AuditProof at capsule destroy:

        - `nanorix.compliance.framework` (e.g., HIPAA)
        - `nanorix.capsulefile` (the Capsulefile name)
        - `nanorix.capsule_id` (when known; bound at capsule creation)
        - `nanorix.audit_proof_pending` = true (signals that the AuditProof
          for this span hasn't been finalized yet — the AuditProof's
          activity_trail will reach the customer at capsule destroy)
        - `nanorix.semconv.version` (the OTel GenAI semconv version this
          exporter targets)
        """
        try:
            span.set_attribute("nanorix.compliance.framework", self.framework)
            span.set_attribute("nanorix.capsulefile", self.capsulefile)
            span.set_attribute("nanorix.audit_proof_pending", True)
            span.set_attribute(
                "nanorix.semconv.version", OTEL_GENAI_SEMCONV_VERSION
            )
            if self.framework_version:
                span.set_attribute(
                    "nanorix.compliance.framework_version",
                    str(self.framework_version),
                )
            if self.capsule_id:
                span.set_attribute("nanorix.capsule_id", str(self.capsule_id))
            if self.agent_id:
                span.set_attribute("nanorix.agent_id", str(self.agent_id))
        except Exception:  # noqa: BLE001
            pass

    def _set_redacted_or_content(
        self,
        span: Span,
        attr_name: str,
        content: Any,
    ) -> None:
        """Set a sensitive attribute respecting `capture_content` flag.

        When `capture_content=False` (default), emits
        `REDACTED_PLACEHOLDER`. When `capture_content=True` AND
        `content is not None`, emits the rendered content.
        """
        try:
            if not self.capture_content:
                span.set_attribute(attr_name, REDACTED_PLACEHOLDER)
                return
            if content is None:
                # capture_content=True but customer didn't supply
                # content this turn — emit empty string per OTel
                # convention (attribute present but empty).
                span.set_attribute(attr_name, "")
                return
            # Customer opted into content emission.
            rendered = _coerce_attr_value(content)
            if isinstance(rendered, list):
                # OTel sequence-of-strings; coerce each element.
                span.set_attribute(
                    attr_name, [str(x) for x in rendered]
                )
            else:
                span.set_attribute(attr_name, str(rendered))
        except Exception:  # noqa: BLE001
            pass

    def _invoke_on_span(self, span: Span) -> None:
        """Invoke the customer's `on_span` debug hook (if configured)."""
        if self.on_span is None:
            return
        try:
            self.on_span(span)
        except Exception:  # noqa: BLE001 - debug hook must not crash
            pass


# ── Internal exception type for span.record_exception() ─────────────


class _SyntheticToolFailure(Exception):
    """Synthetic exception type used to populate `record_exception` on tool failure.

    Tool-call failures arrive at the OTel exporter as ActivityEvent
    dicts (no live exception object). OTel's `Span.record_exception`
    requires an Exception instance; we synthesize one carrying the
    `failure_reason` narrative so customer dashboards see a stable
    error-record shape.
    """

    pass


# ── Re-exports for caller convenience ───────────────────────────────


# Reference to the OTel trace module for customers who want to set up
# a TracerProvider without a separate import.
otel_trace = _otel_trace
__all__ += ["otel_trace"]
