/**
 * OpenTelemetry GenAI semantic-conventions exporter — second-channel observability.
 *
 * Emits OTel-conformant GenAI spans **alongside** Nanorix's own
 * ActivityEvent emission. The two channels coexist:
 *
 * - **Channel 1 (existing)**: `ActivityEvent` records → in-capsule
 *   `/data/activity_events.jsonl` → drained at destroy → bound into
 *   AuditProof hash chain (ADR-021's 4 closed-set variants — Forever-
 *   Standard per ADR-006 I0).
 * - **Channel 2 (this module)**: OTel `gen_ai.*` spans → customer's
 *   existing TracerProvider → flows into customer's existing
 *   observability stack (Datadog, Splunk, Honeycomb, Logfire, New Relic,
 *   etc.). NOT bound into AuditProof. NOT cryptographically attested.
 *   Pure observability.
 *
 * The two channels carry the same per-call signal but in two different
 * shapes for two different audiences:
 *
 * - Auditor reads the AuditProof's activity_trail (cryptographically
 *   attested, sealed, binary-comparable).
 * - Engineer reads the OTel spans in their existing dashboard.
 *
 * ## OTel GenAI semantic-conventions conformance
 *
 * Per https://opentelemetry.io/docs/specs/semconv/gen-ai/ v1.36.0
 * (reference checkpoint; conventions are NOT yet Stable — Development
 * status). This module emits the following attribute keys (BYTE-
 * IDENTICAL to consumer's existing OTel collector pipeline):
 *
 * **REQUIRED**:
 * - `gen_ai.operation.name` (`"chat"` | `"text_completion"` | `"embedding"`)
 * - `gen_ai.provider.name` (e.g., `"openai"`, `"anthropic"`, `"aws.bedrock"`)
 *
 * **CONDITIONALLY REQUIRED**:
 * - `error.type` (when the operation errored)
 * - `gen_ai.request.model`
 * - `gen_ai.conversation.id`
 *
 * **RECOMMENDED**:
 * - `gen_ai.request.{temperature, top_k, top_p, max_tokens}`
 * - `gen_ai.usage.{input_tokens, output_tokens}`
 * - `gen_ai.response.{id, model, finish_reasons, time_to_first_chunk}`
 *
 * **OPT-IN (sensitive — OFF by default per Nanorix zero-retention)**:
 * - `gen_ai.system_instructions`
 * - `gen_ai.input.messages`
 * - `gen_ai.output.messages`
 * - `gen_ai.tool.definitions`
 * - `gen_ai.tool.call.{arguments, result}`
 *
 * When `captureContent=false` (default), sensitive content attributes
 * emit `REDACTED_PLACEHOLDER`.
 *
 * ## Two usage modes
 *
 * ### Mode A — observe an existing Nanorix integration
 *
 * ```ts
 * import { NanorixLiteLLMCallback, NanorixOtelGenaiTracer } from "@nanorix/sdk";
 * import { trace } from "@opentelemetry/api";
 *
 * const tracer = trace.getTracer("my-app");
 * const nxCallback = new NanorixLiteLLMCallback({ framework: "HIPAA", capsulefile: "agent" });
 * const otel = new NanorixOtelGenaiTracer({ tracer, framework: "HIPAA", capsulefile: "agent" });
 * otel.attachTo(nxCallback);
 * ```
 *
 * ### Mode B — standalone explicit emission
 *
 * ```ts
 * const span = otel.startLlmCallSpan({ model: "openai/gpt-4o", operation: "chat" });
 * try {
 *   const response = await openai.chat.completions.create(...);
 *   otel.recordResponse(span, { inputTokens: response.usage.prompt_tokens, ... });
 * } finally {
 *   otel.endSpan(span);
 * }
 * ```
 *
 * ## Three-role discipline (ADR-017)
 *
 * Structural span emission only. Does NOT certify OTel conformance,
 * observability-stack correctness, or model behavior — those
 * determinations remain the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Span emission is OBSERVABILITY only — these spans are NEVER bound
 * into the AuditProof. The AuditProof's activity_trail (Channel 1)
 * is the only cryptographically attested signal.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export const FRAMEWORK_HIPAA = "HIPAA";
export const FRAMEWORK_GDPR = "GDPR";
export const FRAMEWORK_SOC2 = "SOC2";
export const FRAMEWORK_PCI_DSS = "PCI_DSS";
export const FRAMEWORK_HITRUST = "HITRUST";
export const FRAMEWORK_EU_AI_ACT = "EU_AI_ACT";

export const ALLOWED_FRAMEWORKS: ReadonlySet<string> = new Set<string>([
  FRAMEWORK_HIPAA,
  FRAMEWORK_GDPR,
  FRAMEWORK_SOC2,
  FRAMEWORK_PCI_DSS,
  FRAMEWORK_HITRUST,
  FRAMEWORK_EU_AI_ACT,
]);

/** Substitute for sensitive content when `captureContent=false`. */
export const REDACTED_PLACEHOLDER = "[redacted; see AuditProof for hash]";

/** Reference checkpoint of the OTel GenAI semantic conventions. */
export const OTEL_GENAI_SEMCONV_VERSION = "1.36.0-development";

/**
 * Closed-enum operation names per OTel GenAI spec. Byte-identical to
 * the Python `_ALLOWED_OPERATIONS` set.
 */
export const ALLOWED_OPERATIONS = [
  "chat",
  "text_completion",
  "embedding",
  "create_agent",
  "execute_tool",
  "generate_content",
] as const;
export type OtelGenaiOperation = (typeof ALLOWED_OPERATIONS)[number];

/**
 * Provider-prefix → canonical OTel `gen_ai.provider.name` value.
 * Byte-identical to Python `_PROVIDER_PREFIX_MAP`.
 */
export const PROVIDER_PREFIX_MAP: Readonly<Record<string, string>> = {
  openai: "openai",
  anthropic: "anthropic",
  claude: "anthropic",
  bedrock: "aws.bedrock",
  vertex: "gcp.vertex_ai",
  vertex_ai: "gcp.vertex_ai",
  gemini: "gcp.gemini",
  azure: "azure.ai.openai",
  azure_openai: "azure.ai.openai",
  cohere: "cohere",
  mistral: "mistral_ai",
  groq: "groq",
  together_ai: "together_ai",
  deepseek: "deepseek",
  perplexity: "perplexity",
  xai: "xai",
} as const;

/** Span kind constants mirroring OTel `SpanKind` enum values. */
export const SPAN_KIND_INTERNAL = 0;
export const SPAN_KIND_CLIENT = 2;

/** Status code constants mirroring OTel `SpanStatusCode` enum values. */
export const STATUS_CODE_UNSET = 0;
export const STATUS_CODE_OK = 1;
export const STATUS_CODE_ERROR = 2;

/**
 * Map a litellm-style provider-prefixed model id → OTel provider name.
 *
 * Examples:
 *   `"openai/gpt-4o"` → `"openai"`
 *   `"anthropic/claude-3-5-sonnet-20241022"` → `"anthropic"`
 *   `"bedrock/anthropic.claude-3-sonnet"` → `"aws.bedrock"`
 *   `"gpt-4o"` → undefined (no prefix; caller must supply provider)
 */
export function inferProviderFromModel(
  model: string | undefined,
): string | undefined {
  if (typeof model !== "string" || !model.includes("/")) return undefined;
  const prefix = model.split("/", 1)[0].toLowerCase();
  return PROVIDER_PREFIX_MAP[prefix];
}

/** Strip a known provider/ prefix from a model id. */
export function stripProviderPrefix(
  model: string | undefined,
): string | undefined {
  if (typeof model !== "string") return model;
  if (!model.includes("/")) return model;
  const idx = model.indexOf("/");
  const prefix = model.slice(0, idx);
  const rest = model.slice(idx + 1);
  if (PROVIDER_PREFIX_MAP[prefix.toLowerCase()] !== undefined) return rest;
  return model;
}

/**
 * Coerce a value into an OTel-attribute-compatible type.
 * OTel attributes accept: string, boolean, number, sequences thereof.
 */
export function coerceAttrValue(v: unknown): unknown {
  if (v === null || v === undefined) return "";
  if (
    typeof v === "string" ||
    typeof v === "boolean" ||
    typeof v === "number"
  ) {
    return v;
  }
  if (Array.isArray(v)) {
    if (
      v.every(
        (x) =>
          typeof x === "string" ||
          typeof x === "boolean" ||
          typeof x === "number",
      )
    ) {
      return [...v];
    }
  }
  return String(v);
}

/**
 * Minimal Tracer interface — matches `@opentelemetry/api` `Tracer`
 * shape so customer-supplied tracers from their existing OTel SDK
 * work without modification. NO actual `@opentelemetry/api` import
 * here — customer brings the framework.
 */
export interface OtelTracerLike {
  startSpan(
    name: string,
    options?: { kind?: number; attributes?: Record<string, unknown> },
  ): OtelSpanLike;
}

/**
 * Minimal Span interface — matches OTel `Span` shape. NO direct
 * `@opentelemetry/api` import.
 */
export interface OtelSpanLike {
  setAttribute(key: string, value: unknown): unknown;
  setStatus(status: { code: number; message?: string }): unknown;
  recordException?(exception: unknown): unknown;
  end(endTime?: number): unknown;
  spanContext?(): unknown;
  isRecording?(): boolean;
}

/** Options for constructing a `NanorixOtelGenaiTracer`. */
export interface NanorixOtelGenaiTracerOptions {
  tracer: OtelTracerLike;
  framework: string;
  capsulefile: string;
  /** Whether to emit full content into OTel attributes. Default false. */
  captureContent?: boolean;
  frameworkVersion?: string;
  apiKey?: string;
  agentId?: string;
  capsuleId?: string;
  onSpan?: (span: OtelSpanLike) => void;
}

/** Options for `startLlmCallSpan` / `emitLlmCall`. */
export interface LlmCallSpanOptions {
  model: string;
  provider?: string;
  operation?: OtelGenaiOperation;
  requestId?: string;
  conversationId?: string;
  temperature?: number;
  topK?: number;
  topP?: number;
  maxTokens?: number;
  systemInstructions?: unknown;
  inputMessages?: unknown;
  toolDefinitions?: unknown;
}

/** Options for `recordResponse`. */
export interface RecordResponseOptions {
  responseId?: string;
  responseModel?: string;
  finishReasons?: string[];
  inputTokens?: number;
  outputTokens?: number;
  timeToFirstChunk?: number;
  outputMessages?: unknown;
}

/** Options for `emitLlmCall`. */
export interface EmitLlmCallOptions extends LlmCallSpanOptions {
  inputTokens?: number;
  outputTokens?: number;
  finishReasons?: string[];
  responseId?: string;
  durationMs?: number;
  isError?: boolean;
  errorType?: string;
}

/** Options for `emitToolCall`. */
export interface EmitToolCallOptions {
  toolName: string;
  toolCallId: string;
  durationMs?: number;
  isError?: boolean;
  errorType?: string;
  failureReason?: string;
}

/** OTel GenAI semantic-conventions exporter for Nanorix capsules. */
export class NanorixOtelGenaiTracer {
  readonly tracer: OtelTracerLike;
  readonly framework: string;
  readonly capsulefile: string;
  readonly captureContent: boolean;
  readonly frameworkVersion?: string;
  readonly apiKey?: string;
  readonly agentId?: string;
  readonly capsuleId?: string;
  readonly onSpan?: (span: OtelSpanLike) => void;

  private readonly _callSpans = new Map<string, OtelSpanLike>();

  constructor(opts: NanorixOtelGenaiTracerOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixOtelGenaiTracer requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default per ADR-013 " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixOtelGenaiTracer requires an explicit `capsulefile` option.",
      );
    }
    if (!opts.tracer) {
      throw new Error(
        "NanorixOtelGenaiTracer requires an explicit `tracer` option " +
          "(an OpenTelemetry Tracer instance).",
      );
    }
    this.tracer = opts.tracer;
    this.framework = opts.framework;
    this.capsulefile = opts.capsulefile;
    this.captureContent = opts.captureContent === true;
    this.frameworkVersion = opts.frameworkVersion;
    this.apiKey = opts.apiKey ?? process.env.NANORIX_API_KEY;
    this.agentId = opts.agentId;
    this.capsuleId = opts.capsuleId;
    this.onSpan = opts.onSpan;
  }

  // ── Mode A: attach to existing Nanorix integration callback ────

  /**
   * Wire this exporter into an existing Nanorix integration callback.
   *
   * Replaces the callback's `onEvent` hook with a fan-out that
   * preserves any pre-existing hook AND dispatches each emitted
   * ActivityEvent into this exporter's OTel-span emission.
   */
  attachTo(callback: {
    onEvent?: ((event: Record<string, unknown>) => void) | undefined;
    _onEvent?: ((event: Record<string, unknown>) => void) | undefined;
  } & {
    [k: string]: unknown;
  }): void {
    if (callback === null || typeof callback !== "object") {
      throw new Error(
        "attachTo requires a Nanorix integration callback object.",
      );
    }
    const priorHook =
      typeof callback.onEvent === "function" ? callback.onEvent : undefined;
    const fanout = (event: Record<string, unknown>): void => {
      if (priorHook) {
        try {
          priorHook(event);
        } catch {
          // never crash
        }
      }
      try {
        this.handleActivityEvent(event);
      } catch {
        // never crash
      }
    };
    // Assign to the internal `_onEvent` slot used by Nanorix callbacks.
    (callback as Record<string, unknown>)._onEvent = fanout;
    // Also assign to public `onEvent` if writable (cleaner test access).
    try {
      (callback as Record<string, unknown>).onEvent = fanout;
    } catch {
      // ignore — read-only on Nanorix callback class (uses getter).
    }
  }

  /** Translate one ActivityEvent dict into an OTel GenAI span. */
  handleActivityEvent(
    event: Record<string, unknown>,
  ): OtelSpanLike | undefined {
    const eventType = event.event;
    switch (eventType) {
      case "reasoning_step_completed":
        return this._handleReasoningStep(event);
      case "tool_call_attempted":
        return this._handleToolCallAttempted(event);
      case "tool_call_completed":
        return this._handleToolCallCompleted(event);
      case "tool_call_failed":
        return this._handleToolCallFailed(event);
      default:
        return undefined;
    }
  }

  private _handleReasoningStep(
    event: Record<string, unknown>,
  ): OtelSpanLike {
    const model = typeof event.model === "string" ? event.model : undefined;
    const provider = inferProviderFromModel(model);
    const bareModel = stripProviderPrefix(model);
    const durationMs = event.duration_ms;
    const isFailure =
      Boolean(event.failure_reason) || Boolean(event.is_failure);

    const spanName = bareModel ? `chat ${bareModel}` : "chat";
    const span = this.tracer.startSpan(spanName, {
      kind: SPAN_KIND_CLIENT,
    });
    try {
      this._setCommonAttrs(span);
      span.setAttribute("gen_ai.operation.name", "chat");
      if (provider) span.setAttribute("gen_ai.provider.name", provider);
      if (bareModel) span.setAttribute("gen_ai.request.model", bareModel);
      if (typeof durationMs === "number" && durationMs >= 0) {
        span.setAttribute("nanorix.duration_ms", Math.floor(durationMs));
      }
      if (typeof event.step_idx === "number") {
        span.setAttribute("nanorix.step_idx", event.step_idx);
      }
      const stepKind = event.step_kind;
      if (stepKind !== null && stepKind !== undefined) {
        if (
          typeof stepKind === "object" &&
          typeof (stepKind as Record<string, unknown>).type === "string"
        ) {
          span.setAttribute(
            "nanorix.step_kind",
            (stepKind as Record<string, unknown>).type as string,
          );
        } else if (typeof stepKind === "string") {
          span.setAttribute("nanorix.step_kind", stepKind);
        }
      }
      const agentId = event.agent_id ?? this.agentId;
      if (agentId) span.setAttribute("nanorix.agent_id", String(agentId));
      const parentEventId = event.parent_event_id;
      if (parentEventId) {
        span.setAttribute("nanorix.parent_event_id", String(parentEventId));
      }
      for (const k of ["prompt_hash", "response_hash", "summary_hash"]) {
        const v = event[k];
        if (typeof v === "string") span.setAttribute(`nanorix.${k}`, v);
      }
      this._setRedactedOrContent(span, "gen_ai.input.messages", null);
      this._setRedactedOrContent(span, "gen_ai.output.messages", null);
      if (isFailure) {
        span.setAttribute("error.type", "llm_error");
        span.setStatus({ code: STATUS_CODE_ERROR, message: "LLM call failed" });
      } else {
        span.setStatus({ code: STATUS_CODE_OK });
      }
    } finally {
      span.end();
    }
    this._invokeOnSpan(span);
    return span;
  }

  private _handleToolCallAttempted(
    event: Record<string, unknown>,
  ): OtelSpanLike {
    const toolCallId = String(event.tool_call_id ?? "unknown");
    const toolName = String(event.tool_name ?? "unknown");
    const spanName = `execute_tool ${toolName}`;
    const span = this.tracer.startSpan(spanName, {
      kind: SPAN_KIND_INTERNAL,
    });
    try {
      this._setCommonAttrs(span);
      span.setAttribute("gen_ai.operation.name", "execute_tool");
      span.setAttribute("gen_ai.tool.name", toolName);
      span.setAttribute("gen_ai.tool.call.id", toolCallId);
      const agentId = event.agent_id ?? this.agentId;
      if (agentId) span.setAttribute("nanorix.agent_id", String(agentId));
      const parentEventId = event.parent_event_id;
      if (parentEventId) {
        span.setAttribute("nanorix.parent_event_id", String(parentEventId));
      }
      if (typeof event.tool_args_hash === "string") {
        span.setAttribute("nanorix.tool_args_hash", event.tool_args_hash);
      }
      this._setRedactedOrContent(span, "gen_ai.tool.call.arguments", null);
    } catch {
      span.end();
      return span;
    }
    this._callSpans.set(toolCallId, span);
    return span;
  }

  private _handleToolCallCompleted(
    event: Record<string, unknown>,
  ): OtelSpanLike {
    const toolCallId = String(event.tool_call_id ?? "unknown");
    const durationMs = event.duration_ms;
    let span = this._callSpans.get(toolCallId);
    this._callSpans.delete(toolCallId);
    if (span === undefined) {
      const toolName = String(event.tool_name ?? "unknown");
      span = this.tracer.startSpan(`execute_tool ${toolName}`, {
        kind: SPAN_KIND_INTERNAL,
      });
      try {
        this._setCommonAttrs(span);
        span.setAttribute("gen_ai.operation.name", "execute_tool");
        span.setAttribute("gen_ai.tool.name", toolName);
        span.setAttribute("gen_ai.tool.call.id", toolCallId);
      } catch {
        // ignore
      }
    }
    try {
      if (typeof event.tool_result_hash === "string") {
        span.setAttribute("nanorix.tool_result_hash", event.tool_result_hash);
      }
      if (typeof durationMs === "number" && durationMs >= 0) {
        span.setAttribute("nanorix.duration_ms", Math.floor(durationMs));
      }
      this._setRedactedOrContent(span, "gen_ai.tool.call.result", null);
      span.setStatus({ code: STATUS_CODE_OK });
    } finally {
      span.end();
    }
    this._invokeOnSpan(span);
    return span;
  }

  private _handleToolCallFailed(
    event: Record<string, unknown>,
  ): OtelSpanLike {
    const toolCallId = String(event.tool_call_id ?? "unknown");
    const durationMs = event.duration_ms;
    const failureReason =
      typeof event.failure_reason === "string" ? event.failure_reason : "";
    const failureKind = event.failure_kind;
    let failureKindStr: string | undefined;
    if (
      failureKind !== null &&
      failureKind !== undefined &&
      typeof failureKind === "object" &&
      typeof (failureKind as Record<string, unknown>).type === "string"
    ) {
      failureKindStr = (failureKind as Record<string, unknown>).type as string;
    } else if (typeof failureKind === "string") {
      failureKindStr = failureKind;
    }

    let span = this._callSpans.get(toolCallId);
    this._callSpans.delete(toolCallId);
    if (span === undefined) {
      const toolName = String(event.tool_name ?? "unknown");
      span = this.tracer.startSpan(`execute_tool ${toolName}`, {
        kind: SPAN_KIND_INTERNAL,
      });
      try {
        this._setCommonAttrs(span);
        span.setAttribute("gen_ai.operation.name", "execute_tool");
        span.setAttribute("gen_ai.tool.name", toolName);
        span.setAttribute("gen_ai.tool.call.id", toolCallId);
      } catch {
        // ignore
      }
    }
    try {
      if (typeof durationMs === "number" && durationMs >= 0) {
        span.setAttribute("nanorix.duration_ms", Math.floor(durationMs));
      }
      const errorType = failureKindStr ?? "tool_error";
      span.setAttribute("error.type", errorType);
      if (failureKindStr) {
        span.setAttribute("nanorix.tool_failure_kind", failureKindStr);
      }
      if (failureReason) {
        span.setAttribute(
          "nanorix.tool_failure_reason",
          failureReason.slice(0, 1024),
        );
      }
      if (typeof span.recordException === "function") {
        const err = new Error(failureReason || "Tool failed");
        try {
          span.recordException(err);
        } catch {
          // ignore
        }
      }
      span.setStatus({
        code: STATUS_CODE_ERROR,
        message: (failureReason || "Tool failed").slice(0, 256),
      });
    } finally {
      span.end();
    }
    this._invokeOnSpan(span);
    return span;
  }

  // ── Mode B: standalone explicit emission ──────────────────────

  /**
   * Open an OTel GenAI span around an LLM call site.
   *
   * Customer wraps their LLM call in `try { ... } finally { tracer.endSpan(span) }`.
   * Returns the open span. Use `recordResponse(span, ...)` to populate
   * response attributes before `endSpan(span)`.
   */
  startLlmCallSpan(opts: LlmCallSpanOptions): OtelSpanLike {
    const operation = opts.operation ?? "chat";
    if (!ALLOWED_OPERATIONS.includes(operation)) {
      throw new Error(
        `operation '${operation}' is not in the closed-enum set; allowed: ` +
          JSON.stringify([...ALLOWED_OPERATIONS]),
      );
    }
    const bareModel = stripProviderPrefix(opts.model);
    const inferredProvider = inferProviderFromModel(opts.model);
    const finalProvider = opts.provider ?? inferredProvider;
    const spanName = bareModel ? `${operation} ${bareModel}` : operation;
    const span = this.tracer.startSpan(spanName, { kind: SPAN_KIND_CLIENT });
    try {
      this._setCommonAttrs(span);
      span.setAttribute("gen_ai.operation.name", operation);
      if (finalProvider) {
        span.setAttribute("gen_ai.provider.name", finalProvider);
      }
      if (bareModel) span.setAttribute("gen_ai.request.model", bareModel);
      if (opts.requestId) {
        span.setAttribute("nanorix.request_id", String(opts.requestId));
      }
      if (opts.conversationId) {
        span.setAttribute(
          "gen_ai.conversation.id",
          String(opts.conversationId),
        );
      }
      if (typeof opts.temperature === "number") {
        span.setAttribute("gen_ai.request.temperature", opts.temperature);
      }
      if (typeof opts.topK === "number") {
        span.setAttribute("gen_ai.request.top_k", Math.floor(opts.topK));
      }
      if (typeof opts.topP === "number") {
        span.setAttribute("gen_ai.request.top_p", opts.topP);
      }
      if (typeof opts.maxTokens === "number") {
        span.setAttribute(
          "gen_ai.request.max_tokens",
          Math.floor(opts.maxTokens),
        );
      }
      this._setRedactedOrContent(
        span,
        "gen_ai.system_instructions",
        opts.systemInstructions ?? null,
      );
      this._setRedactedOrContent(
        span,
        "gen_ai.input.messages",
        opts.inputMessages ?? null,
      );
      this._setRedactedOrContent(
        span,
        "gen_ai.tool.definitions",
        opts.toolDefinitions ?? null,
      );
    } catch {
      // never crash
    }
    return span;
  }

  /** Populate response-side attributes on an open span. */
  recordResponse(span: OtelSpanLike, opts: RecordResponseOptions): void {
    if (!span) return;
    try {
      if (opts.responseId) {
        span.setAttribute("gen_ai.response.id", String(opts.responseId));
      }
      if (opts.responseModel) {
        span.setAttribute(
          "gen_ai.response.model",
          String(stripProviderPrefix(opts.responseModel)),
        );
      }
      if (Array.isArray(opts.finishReasons)) {
        span.setAttribute(
          "gen_ai.response.finish_reasons",
          opts.finishReasons.map((r) => String(r)),
        );
      }
      if (typeof opts.inputTokens === "number" && opts.inputTokens >= 0) {
        span.setAttribute(
          "gen_ai.usage.input_tokens",
          Math.floor(opts.inputTokens),
        );
      }
      if (typeof opts.outputTokens === "number" && opts.outputTokens >= 0) {
        span.setAttribute(
          "gen_ai.usage.output_tokens",
          Math.floor(opts.outputTokens),
        );
      }
      if (
        typeof opts.timeToFirstChunk === "number" &&
        opts.timeToFirstChunk >= 0
      ) {
        span.setAttribute(
          "gen_ai.response.time_to_first_chunk",
          opts.timeToFirstChunk,
        );
      }
      this._setRedactedOrContent(
        span,
        "gen_ai.output.messages",
        opts.outputMessages ?? null,
      );
    } catch {
      // never crash
    }
  }

  /**
   * Close an open span. Wrapper around `span.end()` that invokes the
   * `onSpan` debug hook after closure.
   */
  endSpan(span: OtelSpanLike, status: "ok" | "error" = "ok"): void {
    if (!span) return;
    try {
      if (status === "error") {
        span.setStatus({ code: STATUS_CODE_ERROR });
      } else {
        span.setStatus({ code: STATUS_CODE_OK });
      }
    } catch {
      // ignore
    }
    try {
      span.end();
    } catch {
      // ignore
    }
    this._invokeOnSpan(span);
  }

  /** One-shot LLM-call span emission (no context-manager needed). */
  emitLlmCall(opts: EmitLlmCallOptions): OtelSpanLike {
    const operation = opts.operation ?? "chat";
    if (!ALLOWED_OPERATIONS.includes(operation)) {
      throw new Error(
        `operation '${operation}' is not in the closed-enum set; allowed: ` +
          JSON.stringify([...ALLOWED_OPERATIONS]),
      );
    }
    const bareModel = stripProviderPrefix(opts.model);
    const inferredProvider = inferProviderFromModel(opts.model);
    const finalProvider = opts.provider ?? inferredProvider;
    const spanName = bareModel ? `${operation} ${bareModel}` : operation;
    const span = this.tracer.startSpan(spanName, { kind: SPAN_KIND_CLIENT });
    try {
      this._setCommonAttrs(span);
      span.setAttribute("gen_ai.operation.name", operation);
      if (finalProvider) {
        span.setAttribute("gen_ai.provider.name", finalProvider);
      }
      if (bareModel) span.setAttribute("gen_ai.request.model", bareModel);
      if (opts.responseId) {
        span.setAttribute("gen_ai.response.id", String(opts.responseId));
      }
      if (typeof opts.inputTokens === "number" && opts.inputTokens >= 0) {
        span.setAttribute(
          "gen_ai.usage.input_tokens",
          Math.floor(opts.inputTokens),
        );
      }
      if (typeof opts.outputTokens === "number" && opts.outputTokens >= 0) {
        span.setAttribute(
          "gen_ai.usage.output_tokens",
          Math.floor(opts.outputTokens),
        );
      }
      if (Array.isArray(opts.finishReasons)) {
        span.setAttribute(
          "gen_ai.response.finish_reasons",
          opts.finishReasons.map((r) => String(r)),
        );
      }
      if (typeof opts.durationMs === "number" && opts.durationMs >= 0) {
        span.setAttribute("nanorix.duration_ms", Math.floor(opts.durationMs));
      }
      if (opts.isError) {
        span.setAttribute("error.type", opts.errorType ?? "llm_error");
        span.setStatus({
          code: STATUS_CODE_ERROR,
          message: opts.errorType ?? "LLM error",
        });
      } else {
        span.setStatus({ code: STATUS_CODE_OK });
      }
    } finally {
      span.end();
    }
    this._invokeOnSpan(span);
    return span;
  }

  /** One-shot tool-execution span emission. */
  emitToolCall(opts: EmitToolCallOptions): OtelSpanLike {
    const span = this.tracer.startSpan(`execute_tool ${opts.toolName}`, {
      kind: SPAN_KIND_INTERNAL,
    });
    try {
      this._setCommonAttrs(span);
      span.setAttribute("gen_ai.operation.name", "execute_tool");
      span.setAttribute("gen_ai.tool.name", opts.toolName);
      span.setAttribute("gen_ai.tool.call.id", opts.toolCallId);
      if (typeof opts.durationMs === "number" && opts.durationMs >= 0) {
        span.setAttribute("nanorix.duration_ms", Math.floor(opts.durationMs));
      }
      if (opts.isError) {
        span.setAttribute("error.type", opts.errorType ?? "tool_error");
        if (opts.failureReason) {
          span.setAttribute(
            "nanorix.tool_failure_reason",
            String(opts.failureReason).slice(0, 1024),
          );
        }
        span.setStatus({
          code: STATUS_CODE_ERROR,
          message: String(
            opts.failureReason ?? opts.errorType ?? "Tool error",
          ).slice(0, 256),
        });
      } else {
        span.setStatus({ code: STATUS_CODE_OK });
      }
    } finally {
      span.end();
    }
    this._invokeOnSpan(span);
    return span;
  }

  // ── shared helpers ────────────────────────────────────────────

  private _setCommonAttrs(span: OtelSpanLike): void {
    try {
      span.setAttribute("nanorix.compliance.framework", this.framework);
      span.setAttribute("nanorix.capsulefile", this.capsulefile);
      span.setAttribute("nanorix.audit_proof_pending", true);
      span.setAttribute("nanorix.semconv.version", OTEL_GENAI_SEMCONV_VERSION);
      if (this.frameworkVersion) {
        span.setAttribute(
          "nanorix.compliance.framework_version",
          String(this.frameworkVersion),
        );
      }
      if (this.capsuleId) {
        span.setAttribute("nanorix.capsule_id", String(this.capsuleId));
      }
      if (this.agentId) {
        span.setAttribute("nanorix.agent_id", String(this.agentId));
      }
    } catch {
      // never crash
    }
  }

  private _setRedactedOrContent(
    span: OtelSpanLike,
    attrName: string,
    content: unknown,
  ): void {
    try {
      if (!this.captureContent) {
        span.setAttribute(attrName, REDACTED_PLACEHOLDER);
        return;
      }
      if (content === null || content === undefined) {
        span.setAttribute(attrName, "");
        return;
      }
      const rendered = coerceAttrValue(content);
      if (Array.isArray(rendered)) {
        span.setAttribute(attrName, rendered.map((x) => String(x)));
      } else {
        span.setAttribute(attrName, String(rendered));
      }
    } catch {
      // never crash
    }
  }

  private _invokeOnSpan(span: OtelSpanLike): void {
    if (this.onSpan === undefined) return;
    try {
      this.onSpan(span);
    } catch {
      // debug hook must not crash
    }
  }
}
