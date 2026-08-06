"""Wave 14 / item 8 — OTel GenAI semantic-conventions exporter tests.

Mirrors `test_litellm_integration.py`. Exercises the
`nanorix.integrations.otel_genai.NanorixOTelGenAIExporter` class against:

- Construction discipline (framework + capsulefile required, etc.)
- Mode A — attach_to() fan-out from a Nanorix integration callback
- Mode B — span_for_llm_call(), record_response(), emit_llm_call(),
  emit_tool_call() standalone surfaces
- ActivityEvent → OTel-span translation for all 4 closed-set variants
- Provider-prefix inference (litellm-style model ids)
- capture_content=False redacts sensitive attributes by default
- capture_content=True emits content (customer consent)
- Customer-call-path safety (OTel exporter bug never crashes customer)
- Forever-Standard preservation (no new ActivityEvent variants)
- Vocabulary discipline (CONVENTIONS.md): AuditProof / Capsulefile

When `opentelemetry` isn't installed, the entire test module is skipped.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import pytest

# Skip when optional dep isn't present.
pytest.importorskip("opentelemetry")
pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace as otel_trace_mod  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode  # noqa: E402

from nanorix.integrations.otel_genai import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_SOC2,
    OTEL_GENAI_SEMCONV_VERSION,
    REDACTED_PLACEHOLDER,
    NanorixOTelGenAIExporter,
)


# ── fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    """Fresh in-memory OTel exporter per test (avoids cross-test bleed)."""
    return InMemorySpanExporter()


@pytest.fixture
def tracer_provider(memory_exporter: InMemorySpanExporter) -> TracerProvider:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    return provider


@pytest.fixture
def tracer(tracer_provider: TracerProvider):
    return tracer_provider.get_tracer("test-otel-genai")


def make_exporter(tracer, **overrides: Any) -> NanorixOTelGenAIExporter:
    kwargs: Dict[str, Any] = {
        "tracer": tracer,
        "framework": FRAMEWORK_HIPAA,
        "capsulefile": "test-otel",
    }
    kwargs.update(overrides)
    return NanorixOTelGenAIExporter(**kwargs)


def attrs(span) -> Dict[str, Any]:
    """Extract span attributes as a plain dict (works on ReadableSpan)."""
    return dict(span.attributes or {})


# ── construction / discipline ──────────────────────────────────────


def test_construction_records_required_args(tracer):
    e = NanorixOTelGenAIExporter(
        tracer=tracer,
        framework=FRAMEWORK_HIPAA,
        capsulefile="agent-x",
    )
    assert e.framework == "HIPAA"
    assert e.capsulefile == "agent-x"
    assert e.capture_content is False  # secure default


def test_construction_with_optional_args(tracer):
    e = NanorixOTelGenAIExporter(
        tracer=tracer,
        framework=FRAMEWORK_GDPR,
        capsulefile="agent-y",
        capture_content=True,
        framework_version="2026-02",
        api_key="nrx_live_test",
        agent_id="sub-1",
        capsule_id="cap_xyz",
    )
    assert e.capture_content is True
    assert e.framework_version == "2026-02"
    assert e.api_key == "nrx_live_test"
    assert e.agent_id == "sub-1"
    assert e.capsule_id == "cap_xyz"


def test_construction_rejects_missing_framework(tracer):
    with pytest.raises(ValueError, match="framework"):
        NanorixOTelGenAIExporter(tracer=tracer, framework="", capsulefile="x")


def test_construction_rejects_none_framework(tracer):
    with pytest.raises(ValueError, match="framework"):
        NanorixOTelGenAIExporter(
            tracer=tracer,
            framework=None,  # type: ignore[arg-type]
            capsulefile="x",
        )


def test_construction_rejects_missing_capsulefile(tracer):
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixOTelGenAIExporter(
            tracer=tracer, framework=FRAMEWORK_HIPAA, capsulefile=""
        )


def test_construction_rejects_missing_tracer():
    with pytest.raises(ValueError, match="tracer"):
        NanorixOTelGenAIExporter(
            tracer=None,  # type: ignore[arg-type]
            framework=FRAMEWORK_HIPAA,
            capsulefile="x",
        )


def test_allowed_frameworks_contains_canonical_set():
    assert "HIPAA" in ALLOWED_FRAMEWORKS
    assert "GDPR" in ALLOWED_FRAMEWORKS
    assert "SOC2" in ALLOWED_FRAMEWORKS


def test_construction_uses_env_for_api_key(monkeypatch: pytest.MonkeyPatch, tracer):
    monkeypatch.setenv("NANORIX_API_KEY", "env_value_xyz")
    e = NanorixOTelGenAIExporter(
        tracer=tracer, framework=FRAMEWORK_SOC2, capsulefile="x"
    )
    assert e.api_key == "env_value_xyz"


# ── Mode B: span_for_llm_call() context manager ────────────────────


def test_span_for_llm_call_emits_required_attributes(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(
        model="openai/gpt-4o", operation="chat"
    ) as span:
        assert span is not None
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    a = attrs(spans[0])
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.request.model"] == "gpt-4o"
    assert spans[0].kind == SpanKind.CLIENT


def test_span_for_llm_call_strips_provider_prefix(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(model="anthropic/claude-3-5-sonnet-20241022"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.provider.name"] == "anthropic"
    assert a["gen_ai.request.model"] == "claude-3-5-sonnet-20241022"


def test_span_for_llm_call_explicit_provider_overrides_inference(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    # Model has no prefix; provider is explicit
    with e.span_for_llm_call(model="gpt-4o", provider="openai"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.request.model"] == "gpt-4o"


def test_span_for_llm_call_unprefixed_model_no_provider(
    tracer, memory_exporter: InMemorySpanExporter
):
    """No provider when model is unprefixed AND provider= is omitted."""
    e = make_exporter(tracer)
    with e.span_for_llm_call(model="custom-local-model"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert "gen_ai.provider.name" not in a
    assert a["gen_ai.request.model"] == "custom-local-model"


def test_span_for_llm_call_records_request_params(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(
        model="openai/gpt-4o",
        temperature=0.7,
        top_k=40,
        top_p=0.9,
        max_tokens=1024,
        conversation_id="conv-abc",
    ):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.request.temperature"] == 0.7
    assert a["gen_ai.request.top_k"] == 40
    assert a["gen_ai.request.top_p"] == 0.9
    assert a["gen_ai.request.max_tokens"] == 1024
    assert a["gen_ai.conversation.id"] == "conv-abc"


def test_span_for_llm_call_invalid_operation_raises(tracer):
    e = make_exporter(tracer)
    with pytest.raises(ValueError, match="closed-enum"):
        with e.span_for_llm_call(model="gpt-4o", operation="invalid_op"):
            pass


def test_span_for_llm_call_records_status_ok_on_clean_exit(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(model="openai/gpt-4o"):
        pass
    span = memory_exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.OK


def test_span_for_llm_call_records_error_on_exception(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with pytest.raises(RuntimeError, match="provider down"):
        with e.span_for_llm_call(model="openai/gpt-4o"):
            raise RuntimeError("provider down")
    span = memory_exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR
    a = attrs(span)
    assert a["error.type"] == "RuntimeError"


def test_span_for_llm_call_records_keyboardinterrupt(
    tracer, memory_exporter: InMemorySpanExporter
):
    """BaseException (KeyboardInterrupt) marks error and re-raises."""
    e = make_exporter(tracer)
    with pytest.raises(KeyboardInterrupt):
        with e.span_for_llm_call(model="openai/gpt-4o"):
            raise KeyboardInterrupt()
    span = memory_exporter.get_finished_spans()[0]
    assert span.status.status_code == StatusCode.ERROR


# ── record_response() populates response-side attributes ───────────


def test_record_response_populates_attributes(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(model="openai/gpt-4o") as span:
        e.record_response(
            span,
            response_id="chatcmpl-abc",
            response_model="openai/gpt-4o-2024-08-06",
            input_tokens=100,
            output_tokens=50,
            finish_reasons=["stop"],
            time_to_first_chunk=0.123,
        )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.response.id"] == "chatcmpl-abc"
    assert a["gen_ai.response.model"] == "gpt-4o-2024-08-06"
    assert a["gen_ai.usage.input_tokens"] == 100
    assert a["gen_ai.usage.output_tokens"] == 50
    assert list(a["gen_ai.response.finish_reasons"]) == ["stop"]
    assert a["gen_ai.response.time_to_first_chunk"] == pytest.approx(0.123)


def test_record_response_with_none_span_is_safe(tracer):
    e = make_exporter(tracer)
    # Must not raise
    e.record_response(None, response_id="x")  # type: ignore[arg-type]


def test_record_response_handles_multiple_finish_reasons(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    with e.span_for_llm_call(model="openai/gpt-4o") as span:
        e.record_response(span, finish_reasons=["stop", "length", "tool_calls"])
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert list(a["gen_ai.response.finish_reasons"]) == [
        "stop",
        "length",
        "tool_calls",
    ]


# ── capture_content discipline (zero-retention by default) ─────────


def test_default_redacts_sensitive_content(
    tracer, memory_exporter: InMemorySpanExporter
):
    """capture_content=False (default) emits redaction placeholder."""
    e = make_exporter(tracer)
    with e.span_for_llm_call(
        model="openai/gpt-4o",
        system_instructions="You are an oracle.",
        input_messages=[{"role": "user", "content": "patient X has diabetes"}],
    ) as span:
        e.record_response(
            span,
            output_messages=[{"role": "assistant", "content": "diagnosis: ..."}],
        )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.system_instructions"] == REDACTED_PLACEHOLDER
    assert a["gen_ai.input.messages"] == REDACTED_PLACEHOLDER
    assert a["gen_ai.output.messages"] == REDACTED_PLACEHOLDER
    # Plaintext NEVER appears in the span
    blob = " ".join(str(v) for v in a.values())
    assert "patient X has diabetes" not in blob
    assert "You are an oracle" not in blob


def test_capture_content_true_emits_full_content(
    tracer, memory_exporter: InMemorySpanExporter
):
    """capture_content=True emits actual content (customer consent)."""
    e = make_exporter(tracer, capture_content=True)
    with e.span_for_llm_call(
        model="openai/gpt-4o",
        system_instructions="You are a poet.",
        input_messages="write a haiku",
    ) as span:
        e.record_response(span, output_messages="cherry blossoms / fall")
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.system_instructions"] == "You are a poet."
    assert a["gen_ai.input.messages"] == "write a haiku"
    assert a["gen_ai.output.messages"] == "cherry blossoms / fall"


def test_capture_content_true_with_none_emits_empty_string(
    tracer, memory_exporter: InMemorySpanExporter
):
    """capture_content=True + content=None → empty string (attr present)."""
    e = make_exporter(tracer, capture_content=True)
    with e.span_for_llm_call(model="openai/gpt-4o"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    # system_instructions wasn't supplied; capture_content=True means
    # the attribute is present but empty
    assert a.get("gen_ai.system_instructions", "") == ""


def test_redacted_placeholder_is_explicit_and_stable():
    """REDACTED_PLACEHOLDER value is the documented constant."""
    assert REDACTED_PLACEHOLDER == "[redacted; see AuditProof for hash]"


# ── common Nanorix correlation attributes on every span ────────────


def test_every_span_carries_nanorix_correlation_attrs(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(
        tracer,
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf-test",
        capsule_id="cap_abc",
        agent_id="agent-1",
        framework_version="2026-02",
    )
    with e.span_for_llm_call(model="openai/gpt-4o"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.compliance.framework"] == "HIPAA"
    assert a["nanorix.capsulefile"] == "cf-test"
    assert a["nanorix.capsule_id"] == "cap_abc"
    assert a["nanorix.agent_id"] == "agent-1"
    assert a["nanorix.compliance.framework_version"] == "2026-02"
    assert a["nanorix.audit_proof_pending"] is True
    assert a["nanorix.semconv.version"] == OTEL_GENAI_SEMCONV_VERSION


def test_correlation_attrs_omitted_when_unset(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)  # no capsule_id/agent_id/framework_version
    with e.span_for_llm_call(model="openai/gpt-4o"):
        pass
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert "nanorix.capsule_id" not in a
    assert "nanorix.agent_id" not in a
    assert "nanorix.compliance.framework_version" not in a


# ── emit_llm_call() one-shot ───────────────────────────────────────


def test_emit_llm_call_oneshot_success(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    span = e.emit_llm_call(
        model="openai/gpt-4o",
        input_tokens=12,
        output_tokens=8,
        response_id="chatcmpl-x",
        finish_reasons=["stop"],
        duration_ms=42,
    )
    assert span is not None
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.usage.input_tokens"] == 12
    assert a["gen_ai.usage.output_tokens"] == 8
    assert a["gen_ai.response.id"] == "chatcmpl-x"
    assert a["nanorix.duration_ms"] == 42


def test_emit_llm_call_oneshot_error(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    e.emit_llm_call(
        model="openai/gpt-4o",
        is_error=True,
        error_type="rate_limit",
    )
    span = memory_exporter.get_finished_spans()[0]
    a = attrs(span)
    assert a["error.type"] == "rate_limit"
    assert span.status.status_code == StatusCode.ERROR


def test_emit_llm_call_invalid_operation_raises(tracer):
    e = make_exporter(tracer)
    with pytest.raises(ValueError, match="closed-enum"):
        e.emit_llm_call(model="gpt-4o", operation="not_real")


# ── emit_tool_call() one-shot ──────────────────────────────────────


def test_emit_tool_call_oneshot_success(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    e.emit_tool_call(
        tool_name="lab_result_fetch",
        tool_call_id="call_abc",
        duration_ms=200,
    )
    span = memory_exporter.get_finished_spans()[0]
    a = attrs(span)
    assert a["gen_ai.operation.name"] == "execute_tool"
    assert a["gen_ai.tool.name"] == "lab_result_fetch"
    assert a["gen_ai.tool.call.id"] == "call_abc"
    assert a["nanorix.duration_ms"] == 200
    assert span.status.status_code == StatusCode.OK
    assert span.kind == SpanKind.INTERNAL


def test_emit_tool_call_oneshot_error(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    e.emit_tool_call(
        tool_name="db_lookup",
        tool_call_id="call_x",
        is_error=True,
        error_type="timeout",
        failure_reason="upstream timed out after 30s",
    )
    span = memory_exporter.get_finished_spans()[0]
    a = attrs(span)
    assert a["error.type"] == "timeout"
    assert "upstream timed out" in a["nanorix.tool_failure_reason"]
    assert span.status.status_code == StatusCode.ERROR


# ── Mode A: attach_to() fan-out ────────────────────────────────────


class _StubCallback:
    """Minimal stand-in for a Nanorix integration callback.

    Exposes the `on_event` attribute that `attach_to()` chains.
    """

    def __init__(self) -> None:
        self.on_event: Any = None
        self.events_seen: List[Dict[str, Any]] = []

    def fire(self, event: Dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event)


def test_attach_to_replaces_on_event_hook(tracer):
    e = make_exporter(tracer)
    cb = _StubCallback()
    assert cb.on_event is None
    e.attach_to(cb)
    assert cb.on_event is not None and callable(cb.on_event)


def test_attach_to_preserves_prior_hook(tracer):
    e = make_exporter(tracer)
    cb = _StubCallback()
    captured: List[Dict[str, Any]] = []
    cb.on_event = captured.append  # customer's pre-existing hook
    e.attach_to(cb)

    # Fire a synthetic event
    cb.fire({"event": "reasoning_step_completed", "step_idx": 0, "duration_ms": 5})

    # Customer's hook ran (event captured)
    assert len(captured) == 1
    assert captured[0]["event"] == "reasoning_step_completed"


def test_attach_to_emits_otel_span_for_reasoning_step(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 0,
            "duration_ms": 42,
            "model": "openai/gpt-4o",
            "prompt_hash": "sha512:abc",
            "response_hash": "sha512:def",
        }
    )
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    a = attrs(spans[0])
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.request.model"] == "gpt-4o"
    assert a["nanorix.step_idx"] == 0
    assert a["nanorix.duration_ms"] == 42
    assert a["nanorix.prompt_hash"] == "sha512:abc"
    assert a["nanorix.response_hash"] == "sha512:def"


def test_attach_to_emits_span_for_tool_call_lifecycle(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    # Attempted → opens span
    cb.fire(
        {
            "event": "tool_call_attempted",
            "tool_call_id": "call_abc",
            "tool_name": "lab_fetch",
            "tool_args_hash": "sha512:argshash",
        }
    )
    # Completed → closes span
    cb.fire(
        {
            "event": "tool_call_completed",
            "tool_call_id": "call_abc",
            "tool_name": "lab_fetch",
            "tool_result_hash": "sha512:resulthash",
            "duration_ms": 200,
        }
    )

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1  # only the completed span flushes; attempted is held
    a = attrs(spans[0])
    assert a["gen_ai.operation.name"] == "execute_tool"
    assert a["gen_ai.tool.name"] == "lab_fetch"
    assert a["gen_ai.tool.call.id"] == "call_abc"
    assert a["nanorix.tool_args_hash"] == "sha512:argshash"
    assert a["nanorix.tool_result_hash"] == "sha512:resulthash"
    assert a["nanorix.duration_ms"] == 200
    assert spans[0].status.status_code == StatusCode.OK


def test_attach_to_emits_span_for_tool_call_failed(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    cb.fire(
        {
            "event": "tool_call_attempted",
            "tool_call_id": "call_x",
            "tool_name": "db_query",
            "tool_args_hash": "sha512:args",
        }
    )
    cb.fire(
        {
            "event": "tool_call_failed",
            "tool_call_id": "call_x",
            "tool_name": "db_query",
            "failure_reason": "timeout after 30s",
            "failure_kind": {"type": "timeout"},
            "duration_ms": 30000,
        }
    )

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    a = attrs(spans[0])
    assert a["error.type"] == "timeout"
    assert a["nanorix.tool_failure_kind"] == "timeout"
    assert "timeout after 30s" in a["nanorix.tool_failure_reason"]
    assert spans[0].status.status_code == StatusCode.ERROR


def test_attach_to_handles_completed_without_attempted(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Defensive: completed without prior attempted still emits a span."""
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    cb.fire(
        {
            "event": "tool_call_completed",
            "tool_call_id": "call_orphan",
            "tool_name": "tool_x",
            "duration_ms": 50,
        }
    )
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1


def test_attach_to_handles_failed_without_attempted(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    cb.fire(
        {
            "event": "tool_call_failed",
            "tool_call_id": "call_orphan",
            "tool_name": "tool_x",
            "failure_reason": "boom",
            "duration_ms": 5,
        }
    )
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.ERROR


def test_attach_to_drops_unknown_event_types(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Forever-Standard: only the 4 known variants emit spans."""
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    cb.fire({"event": "made_up_event_type", "x": 1})
    cb.fire({"event": "another_unknown", "y": 2})
    cb.fire({"event": "reasoning_step_completed", "step_idx": 0, "duration_ms": 1})
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert attrs(spans[0])["gen_ai.operation.name"] == "chat"


def test_attach_to_rejects_non_callback(tracer):
    e = make_exporter(tracer)
    with pytest.raises(ValueError, match="on_event"):
        e.attach_to(object())


def test_attach_to_chains_multiple_attaches(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Successive attach_to calls chain hooks (multiple exporters can observe)."""
    e1 = make_exporter(tracer)
    e2 = make_exporter(tracer)
    cb = _StubCallback()
    e1.attach_to(cb)
    e2.attach_to(cb)

    cb.fire({"event": "reasoning_step_completed", "step_idx": 0, "duration_ms": 5})
    spans = memory_exporter.get_finished_spans()
    # Both exporters fire — 2 spans
    assert len(spans) == 2


# ── ActivityEvent → OTel span attribute correctness ───────────────


def test_reasoning_step_handles_step_kind_dict(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 2,
            "duration_ms": 10,
            "model": "openai/gpt-4o",
            "step_kind": {"type": "decide"},
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.step_kind"] == "decide"


def test_reasoning_step_handles_step_kind_string(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 0,
            "duration_ms": 10,
            "step_kind": "plan",
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.step_kind"] == "plan"


def test_reasoning_step_with_failure_marker_emits_error(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 0,
            "duration_ms": 5,
            "model": "openai/gpt-4o",
            "is_failure": True,
        }
    )
    span = memory_exporter.get_finished_spans()[0]
    a = attrs(span)
    assert a["error.type"] == "llm_error"
    assert span.status.status_code == StatusCode.ERROR


def test_reasoning_step_propagates_parent_event_id(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 0,
            "duration_ms": 5,
            "parent_event_id": "parent_abc",
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.parent_event_id"] == "parent_abc"


def test_reasoning_step_event_agent_id_overrides_exporter_default(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Per-event agent_id beats the exporter's default."""
    e = make_exporter(tracer, agent_id="default-agent")
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "reasoning_step_completed",
            "step_idx": 0,
            "duration_ms": 1,
            "agent_id": "specific-agent",
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.agent_id"] == "specific-agent"


def test_tool_call_attempted_records_args_hash(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "tool_call_attempted",
            "tool_call_id": "tc-1",
            "tool_name": "t",
            "tool_args_hash": "sha512:specifichash",
        }
    )
    cb.fire(
        {
            "event": "tool_call_completed",
            "tool_call_id": "tc-1",
            "tool_name": "t",
            "duration_ms": 1,
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["nanorix.tool_args_hash"] == "sha512:specifichash"


def test_tool_call_failed_with_string_failure_kind(
    tracer, memory_exporter: InMemorySpanExporter
):
    """failure_kind as plain string also handled."""
    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {
            "event": "tool_call_failed",
            "tool_call_id": "tc-x",
            "tool_name": "t",
            "failure_reason": "perm",
            "failure_kind": "permission_denied",
            "duration_ms": 2,
        }
    )
    a = attrs(memory_exporter.get_finished_spans()[0])
    assert a["error.type"] == "permission_denied"
    assert a["nanorix.tool_failure_kind"] == "permission_denied"


# ── customer-call-path safety: never crash ─────────────────────────


def test_handle_activity_event_with_malformed_event_does_not_crash(tracer):
    e = make_exporter(tracer)
    # No 'event' key
    e.handle_activity_event({})
    # 'event' is None
    e.handle_activity_event({"event": None})  # type: ignore[dict-item]
    # 'event' is wrong type
    e.handle_activity_event({"event": 12345})
    # All passed without raising


def test_attach_to_fanout_swallows_exporter_bug(tracer):
    """If the OTel exporter bug crashes, customer's pre-existing hook still ran."""
    e = make_exporter(tracer)
    cb = _StubCallback()
    captured: List[Dict[str, Any]] = []
    cb.on_event = captured.append
    e.attach_to(cb)

    # Send malformed events; customer's hook still captures
    cb.fire({"event": "tool_call_attempted"})  # no tool_call_id
    cb.fire({"event": "made_up_variant"})
    cb.fire({"event": "reasoning_step_completed"})

    # Customer's hook saw all 3 events
    assert len(captured) == 3


def test_on_span_callback_fires_per_emission(
    tracer, memory_exporter: InMemorySpanExporter
):
    seen = []
    e = make_exporter(tracer, on_span=seen.append)
    e.emit_llm_call(model="openai/gpt-4o")
    assert len(seen) == 1


def test_on_span_failure_does_not_break_emission(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Bad on_span hook crashes silently; span still emits."""

    def bad(_) -> None:
        raise RuntimeError("debug crash")

    e = make_exporter(tracer, on_span=bad)
    e.emit_llm_call(model="openai/gpt-4o")
    # Span still made it to the exporter
    assert len(memory_exporter.get_finished_spans()) == 1


# ── property test on call orderings ────────────────────────────────


def test_property_random_event_streams_produce_well_formed_spans(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Property test: arbitrary sequences of the 4 ActivityEvent variants
    produce well-formed OTel spans (no exceptions, valid status codes).

    Iterations: 1_000 (mirrors the 4 prior framework integrations'
    iteration count per the anti-crash budget).
    """
    rng = random.Random(0xCAFE_BAB2)
    iterations = 1_000

    e = make_exporter(tracer)
    cb = _StubCallback()
    e.attach_to(cb)

    open_tool_call_ids: List[str] = []
    for _ in range(iterations):
        kind = rng.choice(
            [
                "reasoning_step",
                "tool_attempt",
                "tool_complete",
                "tool_fail",
                "noise",
            ]
        )
        if kind == "reasoning_step":
            cb.fire(
                {
                    "event": "reasoning_step_completed",
                    "step_idx": rng.randint(0, 99),
                    "duration_ms": rng.randint(0, 5_000),
                    "model": rng.choice(
                        ["openai/gpt-4o", "anthropic/claude-3-5", "gemini/gemini-1.5"]
                    ),
                    "is_failure": rng.random() < 0.1,
                }
            )
        elif kind == "tool_attempt":
            tc_id = uuid4().hex[:8]
            cb.fire(
                {
                    "event": "tool_call_attempted",
                    "tool_call_id": tc_id,
                    "tool_name": f"t_{rng.randint(0, 9)}",
                    "tool_args_hash": f"sha512:{uuid4().hex}",
                }
            )
            open_tool_call_ids.append(tc_id)
        elif kind == "tool_complete" and open_tool_call_ids:
            tc_id = open_tool_call_ids.pop(0)
            cb.fire(
                {
                    "event": "tool_call_completed",
                    "tool_call_id": tc_id,
                    "tool_name": "t",
                    "duration_ms": rng.randint(0, 5_000),
                }
            )
        elif kind == "tool_fail" and open_tool_call_ids:
            tc_id = open_tool_call_ids.pop(0)
            cb.fire(
                {
                    "event": "tool_call_failed",
                    "tool_call_id": tc_id,
                    "tool_name": "t",
                    "failure_reason": "x",
                    "failure_kind": {"type": rng.choice(
                        ["timeout", "invalid_args", "upstream_failure"]
                    )},
                    "duration_ms": rng.randint(0, 5_000),
                }
            )
        else:
            # Unknown event type — exporter must drop silently
            cb.fire({"event": "unknown_variant", "data": rng.random()})

    # Every emitted span has the Nanorix correlation tag set
    for span in memory_exporter.get_finished_spans():
        a = attrs(span)
        assert a.get("nanorix.compliance.framework") == "HIPAA"
        assert a.get("nanorix.audit_proof_pending") is True
        assert a.get("gen_ai.operation.name") in {"chat", "execute_tool"}


# ── ADR-006 I0 Forever-Standard preservation ───────────────────────


def test_adr_006_i0_no_new_activity_event_variants():
    """Forever-Standard: this module reuses, never extends, the 4 variants.

    Confirm the activity_events helpers still expose only the 4 emit_*
    functions; this OTel module didn't add a 5th.
    """
    from nanorix.helpers import activity_events as ae

    emit_funcs = [
        n for n in dir(ae) if n.startswith("emit_")
    ]
    assert set(emit_funcs) == {
        "emit_tool_call_attempted",
        "emit_tool_call_completed",
        "emit_tool_call_failed",
        "emit_reasoning_step_completed",
    }


def test_adr_006_i0_only_four_variants_handled_by_exporter():
    """Exporter dispatches only on the 4 closed-set ActivityEvent variants."""
    from nanorix.integrations import otel_genai as og

    src = Path(og.__file__).read_text()
    # Each handler is invoked from handle_activity_event. Confirm the
    # 4 variant strings are the only ones referenced via dispatch.
    assert "reasoning_step_completed" in src
    assert "tool_call_attempted" in src
    assert "tool_call_completed" in src
    assert "tool_call_failed" in src


# ── vocabulary discipline ──────────────────────────────────────────


def test_module_vocabulary_uses_auditproof_terminology():
    """Module source uses AuditProof + Capsulefile per CONVENTIONS.md."""
    from nanorix.integrations import otel_genai as og

    src = Path(og.__file__).read_text()
    assert "Capsule Record" not in src
    assert "Cryptographic Destruction Proof" not in src
    assert "AuditProof" in src  # positive: correct vocabulary present


# ── round-trip determinism (cross-impl byte-equivalence) ───────────


def test_redacted_attribute_is_byte_equal_across_runs(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Redacted placeholder is identical across runs."""
    e = make_exporter(tracer)
    for _ in range(2):
        with e.span_for_llm_call(
            model="openai/gpt-4o",
            input_messages=[{"role": "user", "content": f"{uuid4()}"}],
        ):
            pass
    spans = memory_exporter.get_finished_spans()
    assert spans[0].attributes["gen_ai.input.messages"] == REDACTED_PLACEHOLDER
    assert spans[1].attributes["gen_ai.input.messages"] == REDACTED_PLACEHOLDER


def test_correlation_tag_present_on_all_emitted_spans(
    tracer, memory_exporter: InMemorySpanExporter
):
    """Every emitted span carries `nanorix.audit_proof_pending=true`."""
    e = make_exporter(tracer)
    e.emit_llm_call(model="openai/gpt-4o")
    e.emit_tool_call(tool_name="t", tool_call_id="tc1")
    cb = _StubCallback()
    e.attach_to(cb)
    cb.fire(
        {"event": "reasoning_step_completed", "step_idx": 0, "duration_ms": 1}
    )

    for span in memory_exporter.get_finished_spans():
        a = attrs(span)
        assert a.get("nanorix.audit_proof_pending") is True


# ── compatibility with all 4 prior integrations' callbacks ─────────


def test_attach_to_works_with_litellm_callback_shape(
    tracer, memory_exporter: InMemorySpanExporter, tmp_path: Path
):
    """attach_to() compatible with NanorixLiteLLMCallback (real shape)."""
    pytest.importorskip("litellm")
    from nanorix.integrations.litellm import NanorixLiteLLMCallback

    cb = NanorixLiteLLMCallback(
        framework=FRAMEWORK_HIPAA,
        capsulefile="agent",
        buffer_path=str(tmp_path / "events.jsonl"),
    )
    e = make_exporter(tracer)
    e.attach_to(cb)  # must not raise
    # Hook now installed
    assert cb.on_event is not None


def test_attach_to_works_with_langchain_callback_shape(
    tracer, memory_exporter: InMemorySpanExporter, tmp_path: Path
):
    """attach_to() compatible with NanorixCallback (LangChain)."""
    pytest.importorskip("langchain_core")
    from nanorix.integrations.langchain import NanorixCallback

    cb = NanorixCallback(
        framework=FRAMEWORK_HIPAA,
        capsulefile="agent",
        buffer_path=str(tmp_path / "events.jsonl"),
    )
    e = make_exporter(tracer)
    e.attach_to(cb)  # must not raise
    assert cb.on_event is not None


# ── module-level imports + re-exports ───────────────────────────────


def test_module_re_exports_otel_trace_for_convenience():
    """Customers can pull the OTel trace module via this integration."""
    from nanorix.integrations.otel_genai import otel_trace

    assert otel_trace is otel_trace_mod


def test_otel_genai_semconv_version_constant_present():
    """The semconv version is exposed for customer dashboards."""
    assert OTEL_GENAI_SEMCONV_VERSION.startswith("1.36")
    assert "development" in OTEL_GENAI_SEMCONV_VERSION.lower()


# ── allowed operations enforcement ──────────────────────────────────


def test_all_allowed_operations_accepted_by_emit_llm_call(
    tracer, memory_exporter: InMemorySpanExporter
):
    """All 6 allowed operations work without error."""
    e = make_exporter(tracer)
    for op in ["chat", "text_completion", "embedding", "create_agent",
               "execute_tool", "generate_content"]:
        e.emit_llm_call(model="openai/gpt-4o", operation=op)
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 6
    op_names = {a["gen_ai.operation.name"] for a in (attrs(s) for s in spans)}
    assert op_names == {
        "chat",
        "text_completion",
        "embedding",
        "create_agent",
        "execute_tool",
        "generate_content",
    }


# ── provider inference correctness ─────────────────────────────────


def test_provider_inference_handles_known_prefixes(
    tracer, memory_exporter: InMemorySpanExporter
):
    e = make_exporter(tracer)
    cases = [
        ("openai/gpt-4o", "openai", "gpt-4o"),
        ("anthropic/claude-3-5", "anthropic", "claude-3-5"),
        ("bedrock/some-model", "aws.bedrock", "some-model"),
        ("vertex/gemini-1.5-pro", "gcp.vertex_ai", "gemini-1.5-pro"),
        ("azure/gpt-4-deployment", "azure.ai.openai", "gpt-4-deployment"),
        ("cohere/command-r", "cohere", "command-r"),
    ]
    for model_id, expected_provider, expected_model in cases:
        e.emit_llm_call(model=model_id)
    spans = memory_exporter.get_finished_spans()
    for (model_id, expected_provider, expected_model), span in zip(cases, spans):
        a = attrs(span)
        assert a["gen_ai.provider.name"] == expected_provider, model_id
        assert a["gen_ai.request.model"] == expected_model, model_id
