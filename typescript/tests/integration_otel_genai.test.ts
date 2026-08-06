/**
 * OpenTelemetry GenAI exporter tests.
 *
 * Validates byte-identical `gen_ai.*` attribute key emission,
 * provider-prefix mapping, captureContent OFF-by-default redaction,
 * and ActivityEvent fan-out via attachTo.
 */

import { describe, expect, it } from "vitest";

import {
  NanorixOtelGenaiTracer,
  REDACTED_PLACEHOLDER,
  OTEL_GENAI_SEMCONV_VERSION,
  PROVIDER_PREFIX_MAP,
  ALLOWED_OPERATIONS,
  inferProviderFromModel,
  stripProviderPrefix,
  coerceAttrValue,
  STATUS_CODE_OK,
  STATUS_CODE_ERROR,
  type OtelSpanLike,
  type OtelTracerLike,
} from "../src/integrations/otel_genai.js";

// ── In-memory fake tracer for testing ───────────────────────────

interface RecordedSpan {
  name: string;
  kind?: number;
  attributes: Record<string, unknown>;
  statusCode?: number;
  statusMessage?: string;
  ended: boolean;
  exceptions: unknown[];
}

function makeFakeTracer(): { tracer: OtelTracerLike; spans: RecordedSpan[] } {
  const spans: RecordedSpan[] = [];
  const tracer: OtelTracerLike = {
    startSpan(
      name: string,
      options?: { kind?: number; attributes?: Record<string, unknown> },
    ): OtelSpanLike {
      const recorded: RecordedSpan = {
        name,
        kind: options?.kind,
        attributes: { ...(options?.attributes ?? {}) },
        ended: false,
        exceptions: [],
      };
      spans.push(recorded);
      const span: OtelSpanLike = {
        setAttribute(key: string, value: unknown) {
          recorded.attributes[key] = value;
          return undefined;
        },
        setStatus(status: { code: number; message?: string }) {
          recorded.statusCode = status.code;
          recorded.statusMessage = status.message;
          return undefined;
        },
        recordException(exc: unknown) {
          recorded.exceptions.push(exc);
          return undefined;
        },
        end() {
          recorded.ended = true;
          return undefined;
        },
      };
      return span;
    },
  };
  return { tracer, spans };
}

describe("NanorixOtelGenaiTracer — constants + helpers", () => {
  it("OTEL_GENAI_SEMCONV_VERSION reflects v1.36 development", () => {
    expect(OTEL_GENAI_SEMCONV_VERSION).toEqual("1.36.0-development");
  });

  it("ALLOWED_OPERATIONS is the closed set per spec", () => {
    expect(ALLOWED_OPERATIONS).toContain("chat");
    expect(ALLOWED_OPERATIONS).toContain("execute_tool");
    expect(ALLOWED_OPERATIONS).toContain("embedding");
  });

  it("PROVIDER_PREFIX_MAP maps litellm-style prefixes", () => {
    expect(PROVIDER_PREFIX_MAP.openai).toEqual("openai");
    expect(PROVIDER_PREFIX_MAP.anthropic).toEqual("anthropic");
    expect(PROVIDER_PREFIX_MAP.bedrock).toEqual("aws.bedrock");
    expect(PROVIDER_PREFIX_MAP.vertex).toEqual("gcp.vertex_ai");
  });

  it("inferProviderFromModel handles provider-prefixed models", () => {
    expect(inferProviderFromModel("openai/gpt-4o")).toEqual("openai");
    expect(inferProviderFromModel("anthropic/claude-3-5-sonnet")).toEqual(
      "anthropic",
    );
    expect(inferProviderFromModel("bedrock/anthropic.claude-3")).toEqual(
      "aws.bedrock",
    );
    expect(inferProviderFromModel("gpt-4o")).toBeUndefined();
  });

  it("stripProviderPrefix unprefixes known providers", () => {
    expect(stripProviderPrefix("openai/gpt-4o")).toEqual("gpt-4o");
    expect(stripProviderPrefix("anthropic/claude-3-5-sonnet-20241022")).toEqual(
      "claude-3-5-sonnet-20241022",
    );
    expect(stripProviderPrefix("gpt-4o")).toEqual("gpt-4o");
    expect(stripProviderPrefix("unknown_provider/model")).toEqual(
      "unknown_provider/model",
    );
  });

  it("coerceAttrValue handles JSON-safe types + falls back to String", () => {
    expect(coerceAttrValue("hello")).toEqual("hello");
    expect(coerceAttrValue(42)).toEqual(42);
    expect(coerceAttrValue(true)).toEqual(true);
    expect(coerceAttrValue([1, 2, 3])).toEqual([1, 2, 3]);
    expect(coerceAttrValue(null)).toEqual("");
    expect(coerceAttrValue({ a: 1 })).toEqual("[object Object]");
  });
});

describe("NanorixOtelGenaiTracer — constructor", () => {
  it("requires framework + capsulefile + tracer", () => {
    const { tracer } = makeFakeTracer();
    expect(
      () =>
        new NanorixOtelGenaiTracer({
          framework: "",
          capsulefile: "agent",
          tracer,
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixOtelGenaiTracer({
          framework: "HIPAA",
          capsulefile: "",
          tracer,
        }),
    ).toThrowError(/capsulefile/);
    expect(
      () =>
        new NanorixOtelGenaiTracer({
          framework: "HIPAA",
          capsulefile: "agent",
          tracer: undefined as unknown as OtelTracerLike,
        }),
    ).toThrowError(/tracer/);
  });

  it("captureContent defaults to false", () => {
    const { tracer } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    expect(otel.captureContent).toBe(false);
  });
});

describe("NanorixOtelGenaiTracer — Mode B (standalone)", () => {
  it("emitLlmCall emits gen_ai.* attributes byte-identically", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.emitLlmCall({
      model: "openai/gpt-4o",
      operation: "chat",
      inputTokens: 100,
      outputTokens: 50,
      finishReasons: ["stop"],
      responseId: "resp_x",
      durationMs: 2400,
    });
    expect(spans).toHaveLength(1);
    expect(spans[0].attributes["gen_ai.operation.name"]).toEqual("chat");
    expect(spans[0].attributes["gen_ai.provider.name"]).toEqual("openai");
    expect(spans[0].attributes["gen_ai.request.model"]).toEqual("gpt-4o");
    expect(spans[0].attributes["gen_ai.usage.input_tokens"]).toEqual(100);
    expect(spans[0].attributes["gen_ai.usage.output_tokens"]).toEqual(50);
    expect(spans[0].attributes["gen_ai.response.finish_reasons"]).toEqual([
      "stop",
    ]);
    expect(spans[0].attributes["gen_ai.response.id"]).toEqual("resp_x");
    expect(spans[0].attributes["nanorix.duration_ms"]).toEqual(2400);
    expect(spans[0].statusCode).toEqual(STATUS_CODE_OK);
    expect(spans[0].ended).toBe(true);
  });

  it("emitToolCall emits execute_tool span", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.emitToolCall({
      toolName: "submit",
      toolCallId: "tc_x",
      durationMs: 50,
    });
    expect(spans[0].attributes["gen_ai.operation.name"]).toEqual(
      "execute_tool",
    );
    expect(spans[0].attributes["gen_ai.tool.name"]).toEqual("submit");
    expect(spans[0].attributes["gen_ai.tool.call.id"]).toEqual("tc_x");
    expect(spans[0].statusCode).toEqual(STATUS_CODE_OK);
  });

  it("emitToolCall with isError marks ERROR status", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.emitToolCall({
      toolName: "fail",
      toolCallId: "tc_err",
      isError: true,
      errorType: "timeout",
      failureReason: "exceeded 30s",
    });
    expect(spans[0].statusCode).toEqual(STATUS_CODE_ERROR);
    expect(spans[0].attributes["error.type"]).toEqual("timeout");
    expect(spans[0].attributes["nanorix.tool_failure_reason"]).toContain(
      "exceeded 30s",
    );
  });

  it("emitLlmCall rejects unknown operation", () => {
    const { tracer } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    expect(() =>
      otel.emitLlmCall({
        model: "x",
        operation: "invalid_op" as never,
      }),
    ).toThrowError(/closed-enum/);
  });

  it("startLlmCallSpan + recordResponse + endSpan happy path", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    const span = otel.startLlmCallSpan({
      model: "anthropic/claude-3-5-sonnet",
      operation: "chat",
      temperature: 0.5,
    });
    otel.recordResponse(span, {
      responseId: "msg_x",
      inputTokens: 8,
      outputTokens: 24,
      finishReasons: ["end_turn"],
    });
    otel.endSpan(span, "ok");
    expect(spans[0].attributes["gen_ai.request.model"]).toEqual(
      "claude-3-5-sonnet",
    );
    expect(spans[0].attributes["gen_ai.provider.name"]).toEqual("anthropic");
    expect(spans[0].attributes["gen_ai.request.temperature"]).toEqual(0.5);
    expect(spans[0].attributes["gen_ai.response.id"]).toEqual("msg_x");
    expect(spans[0].attributes["gen_ai.usage.input_tokens"]).toEqual(8);
    expect(spans[0].statusCode).toEqual(STATUS_CODE_OK);
  });
});

describe("NanorixOtelGenaiTracer — captureContent redaction", () => {
  it("emits REDACTED_PLACEHOLDER by default", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    const span = otel.startLlmCallSpan({
      model: "gpt-4o",
      inputMessages: [{ role: "user", content: "PHI here" }],
    });
    otel.endSpan(span);
    expect(spans[0].attributes["gen_ai.input.messages"]).toEqual(
      REDACTED_PLACEHOLDER,
    );
  });

  it("emits raw content when captureContent=true", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
      captureContent: true,
    });
    const span = otel.startLlmCallSpan({
      model: "gpt-4o",
      systemInstructions: "Be terse.",
    });
    otel.endSpan(span);
    expect(spans[0].attributes["gen_ai.system_instructions"]).toEqual(
      "Be terse.",
    );
  });
});

describe("NanorixOtelGenaiTracer — common attributes", () => {
  it("populates nanorix.* correlation attributes on every span", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "pa-agent",
      tracer,
      frameworkVersion: "2026-02",
      agentId: "supervisor",
      capsuleId: "cap_123",
    });
    otel.emitLlmCall({ model: "openai/gpt-4o" });
    expect(spans[0].attributes["nanorix.compliance.framework"]).toEqual(
      "HIPAA",
    );
    expect(spans[0].attributes["nanorix.capsulefile"]).toEqual("pa-agent");
    expect(spans[0].attributes["nanorix.audit_proof_pending"]).toBe(true);
    expect(spans[0].attributes["nanorix.compliance.framework_version"]).toEqual(
      "2026-02",
    );
    expect(spans[0].attributes["nanorix.agent_id"]).toEqual("supervisor");
    expect(spans[0].attributes["nanorix.capsule_id"]).toEqual("cap_123");
    expect(spans[0].attributes["nanorix.semconv.version"]).toEqual(
      OTEL_GENAI_SEMCONV_VERSION,
    );
  });
});

describe("NanorixOtelGenaiTracer — Mode A (ActivityEvent fan-out)", () => {
  it("handleActivityEvent maps reasoning_step_completed → chat span", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.handleActivityEvent({
      event: "reasoning_step_completed",
      step_idx: 0,
      model: "openai/gpt-4o",
      duration_ms: 1500,
      prompt_hash: "sha512:abc",
      response_hash: "sha512:def",
    });
    expect(spans[0].attributes["gen_ai.operation.name"]).toEqual("chat");
    expect(spans[0].attributes["gen_ai.request.model"]).toEqual("gpt-4o");
    expect(spans[0].attributes["gen_ai.provider.name"]).toEqual("openai");
    expect(spans[0].attributes["nanorix.step_idx"]).toEqual(0);
    expect(spans[0].attributes["nanorix.duration_ms"]).toEqual(1500);
    expect(spans[0].attributes["nanorix.prompt_hash"]).toEqual("sha512:abc");
  });

  it("handleActivityEvent pairs tool_call_attempted + completed", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.handleActivityEvent({
      event: "tool_call_attempted",
      tool_call_id: "tc_x",
      tool_name: "submit",
      tool_args_hash: "sha512:hash",
    });
    expect(spans).toHaveLength(1);
    expect(spans[0].ended).toBe(false);
    otel.handleActivityEvent({
      event: "tool_call_completed",
      tool_call_id: "tc_x",
      tool_name: "submit",
      tool_result_hash: "sha512:result",
      duration_ms: 50,
    });
    expect(spans).toHaveLength(1);
    expect(spans[0].ended).toBe(true);
    expect(spans[0].attributes["nanorix.tool_args_hash"]).toEqual("sha512:hash");
    expect(spans[0].attributes["nanorix.tool_result_hash"]).toEqual(
      "sha512:result",
    );
  });

  it("handleActivityEvent maps tool_call_failed → ERROR status", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    otel.handleActivityEvent({
      event: "tool_call_attempted",
      tool_call_id: "tc_fail",
      tool_name: "boom",
    });
    otel.handleActivityEvent({
      event: "tool_call_failed",
      tool_call_id: "tc_fail",
      tool_name: "boom",
      failure_reason: "timeout 30s",
      failure_kind: { type: "timeout" },
      duration_ms: 30000,
    });
    expect(spans[0].statusCode).toEqual(STATUS_CODE_ERROR);
    expect(spans[0].attributes["error.type"]).toEqual("timeout");
    expect(spans[0].attributes["nanorix.tool_failure_kind"]).toEqual("timeout");
  });

  it("attachTo wraps callback's onEvent into fan-out", () => {
    const { tracer, spans } = makeFakeTracer();
    const otel = new NanorixOtelGenaiTracer({
      framework: "HIPAA",
      capsulefile: "agent",
      tracer,
    });
    const seen: string[] = [];
    const fakeCallback: Record<string, unknown> = {
      onEvent: (ev: Record<string, unknown>) => {
        seen.push(String(ev.event));
      },
    };
    otel.attachTo(fakeCallback as never);
    const fanout = fakeCallback._onEvent as (
      ev: Record<string, unknown>,
    ) => void;
    fanout({
      event: "reasoning_step_completed",
      step_idx: 0,
      model: "gpt-4o",
    });
    expect(seen).toEqual(["reasoning_step_completed"]);
    expect(spans).toHaveLength(1);
  });
});
