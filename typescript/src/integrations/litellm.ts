/**
 * LiteLLM proxy HTTP-API callback handler — Tier A (proxy/library) integration.
 *
 * LiteLLM is a Python-primary LLM-router (universal LLM-provider proxy);
 * its HTTP API (OpenAI-compatible chat-completions schema) is
 * language-agnostic. This module provides a structural callback class
 * that Node.js / TypeScript backends use when routing LLM traffic
 * through a LiteLLM proxy endpoint:
 *
 * ```ts
 * import { NanorixLiteLLMCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixLiteLLMCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 * // Wrap your fetch to LiteLLM's /chat/completions endpoint:
 * callback.beforeRequest({ callId, model, messages });
 * const response = await fetch(LITELLM_URL, { ... });
 * callback.afterSuccess({ callId, response: await response.json() });
 * ```
 *
 * litellm is the universal LLM-provider proxy — one call surface that
 * routes across OpenAI / Anthropic / Bedrock / Vertex / Azure / Gemini
 * / 100+ backends. The TypeScript surface mirrors the HTTP request/
 * response cycle: customer calls `beforeRequest` before issuing the
 * fetch, then `afterSuccess` / `afterFailure` after the response.
 *
 * ## Hook → ActivityEvent mapping
 *
 * | Adapter hook        | Nanorix emission                              |
 * |---------------------|-----------------------------------------------|
 * | `beforeRequest`     | (none — record start_ts ctx)                  |
 * | `afterSuccess`      | `reasoning_step_completed`                    |
 * | `afterSuccess` (tool_calls) | + `tool_call_attempted` per tool      |
 * | `afterFailure`      | `reasoning_step_completed` (failure shape)    |
 *
 * Function-calling completions populate `response.choices[*].message.
 * tool_calls`. Each tool-call entry produces a `tool_call_attempted`
 * event (the LLM REQUESTED a tool call). The matching `tool_call_
 * completed` / `tool_call_failed` for the tool's actual execution must
 * be emitted by the customer's own agent loop OR via a separate
 * framework callback (LangChain.js / LangGraph.js).
 *
 * ## Three-role discipline (ADR-017)
 *
 * Structural adapter only. Does NOT certify LiteLLM correctness, LLM-
 * output safety, or provider conformance — those determinations remain
 * the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Captures structural facts: completion happened at time T, with
 * structural identifiers (call_id, model name, duration_ms, finish
 * reasons, token counts). NEVER asserts LLM correctness, hallucination-
 * freedom, or regulatory-framework conformance.
 */

import {
  DEFAULT_BUFFER_PATH,
  TOOL_CALL_FAILURE_KINDS,
  type ToolCallFailureKind,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
} from "../helpers/activity_events.js";
import { renderErrorReason } from "./langchain.js";

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

/**
 * Closed-set of LiteLLM hook names this adapter emits events for.
 */
export const VALID_LITELLM_HOOKS = [
  "before_request",
  "after_success",
  "after_failure",
] as const;
export type LiteLLMHookName = (typeof VALID_LITELLM_HOOKS)[number];

const DEFAULT_FAILURE_KIND_MAP: Readonly<Record<string, ToolCallFailureKind>> = {
  TimeoutError: "timeout",
  AbortError: "cancelled",
  PermissionError: "permission_denied",
  ValidationError: "invalid_args",
  TypeError: "invalid_args",
  RangeError: "invalid_args",
  HTTPError: "upstream_failure",
  FetchError: "upstream_failure",
  NetworkError: "upstream_failure",
  APIError: "upstream_failure",
  RateLimitError: "upstream_failure",
  ServiceUnavailableError: "upstream_failure",
} as const;

/** Options for constructing a `NanorixLiteLLMCallback`. */
export interface NanorixLiteLLMCallbackOptions {
  framework: string;
  capsulefile: string;
  apiKey?: string;
  metadata?: Record<string, unknown>;
  frameworkVersion?: string;
  onEvent?: (event: Record<string, unknown>) => void;
  bufferPath?: string;
  agentId?: string;
  /** Whether to emit `tool_call_attempted` per tool_call in response. */
  emitToolRequestEvents?: boolean;
}

/** Inputs for `beforeRequest`. */
export interface LiteLLMBeforeRequest {
  /** Unique LiteLLM call_id (UUID-shaped). REQUIRED. */
  callId: string;
  /** Model identifier (e.g., `"openai/gpt-4o"`). */
  model?: string;
  /** OpenAI-shape messages list. */
  messages?: unknown[];
}

/** Inputs for `afterSuccess`. */
export interface LiteLLMAfterSuccess {
  callId: string;
  /** OpenAI-shape response object (ModelResponse). */
  response: Record<string, unknown>;
}

/** Inputs for `afterFailure`. */
export interface LiteLLMAfterFailure {
  callId: string;
  /** Error or failure reason. */
  error?: unknown;
  /** Optional model identifier for failure context. */
  model?: string;
}

interface InFlightCall {
  startTsMs: number;
  model?: string;
  messages: unknown[];
}

/** LiteLLM HTTP-API callback for emitting Nanorix ActivityEvents. */
export class NanorixLiteLLMCallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;
  readonly emitToolRequestEvents: boolean;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private readonly _inFlightCalls = new Map<string, InFlightCall>();
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixLiteLLMCallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixLiteLLMCallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default per ADR-013 " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixLiteLLMCallback requires an explicit `capsulefile` option " +
          "(e.g., capsulefile: 'pa-decision-agent').",
      );
    }

    this.framework = opts.framework;
    this.capsulefile = opts.capsulefile;
    this.apiKey = opts.apiKey ?? process.env.NANORIX_API_KEY;
    this.metadata = opts.metadata ? { ...opts.metadata } : {};
    this.frameworkVersion = opts.frameworkVersion;
    this._onEvent = opts.onEvent;
    this.bufferPath = opts.bufferPath ?? DEFAULT_BUFFER_PATH;
    this.agentId = opts.agentId;
    this.emitToolRequestEvents = opts.emitToolRequestEvents !== false;
  }

  private _nowMs(): number {
    return Math.floor(Date.now());
  }

  private _emit(event: Record<string, unknown>): void {
    if (this._onEvent !== undefined) {
      try {
        this._onEvent(event);
      } catch {
        // Debug hook must not crash callback path.
      }
    }
  }

  classifyToolError(error: unknown): ToolCallFailureKind | undefined {
    if (!(error instanceof Error)) return undefined;
    const className = error.constructor?.name ?? "Error";
    const direct = DEFAULT_FAILURE_KIND_MAP[className];
    if (direct !== undefined && TOOL_CALL_FAILURE_KINDS.includes(direct)) {
      return direct;
    }
    let proto = Object.getPrototypeOf(error);
    while (proto && proto.constructor) {
      const parentName: string = proto.constructor.name;
      const candidate = DEFAULT_FAILURE_KIND_MAP[parentName];
      if (
        candidate !== undefined &&
        TOOL_CALL_FAILURE_KINDS.includes(candidate)
      ) {
        return candidate;
      }
      if (parentName === "Object") break;
      proto = Object.getPrototypeOf(proto);
    }
    return undefined;
  }

  // ── public lifecycle methods ───────────────────────────────────

  /** Open the in-flight context for a LiteLLM completion. */
  beforeRequest(input: LiteLLMBeforeRequest): void {
    if (!input?.callId) {
      throw new Error("beforeRequest requires `callId`");
    }
    this._inFlightCalls.set(input.callId, {
      startTsMs: this._nowMs(),
      model: input.model,
      messages: Array.isArray(input.messages) ? [...input.messages] : [],
    });
  }

  /**
   * Emit `reasoning_step_completed` (success) + tool-request events.
   */
  async afterSuccess(
    input: LiteLLMAfterSuccess,
  ): Promise<Record<string, unknown>> {
    const { callId, response } = input;
    const ctx = this._inFlightCalls.get(callId);
    this._inFlightCalls.delete(callId);
    let durationMs = 0;
    let prompts: unknown = [];
    let model: string | undefined;
    if (ctx !== undefined) {
      durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
      prompts = ctx.messages;
      model = ctx.model;
    } else if (typeof response.model === "string") {
      model = response.model;
    }

    const rendered = renderResponse(response);
    const event = await emitReasoningStepCompleted({
      stepIdx: this._reasoningStepCounter,
      prompt: prompts,
      response: rendered,
      durationMs,
      model,
      agentId: this.agentId,
      bufferPath: this.bufferPath,
    });
    this._reasoningStepCounter += 1;
    this._emit(event);

    // Emit tool-request events for function-calling responses.
    if (this.emitToolRequestEvents) {
      const toolCalls = extractToolCallsFromResponse(response);
      for (const tc of toolCalls) {
        const tcId =
          (typeof tc.id === "string" && tc.id) ||
          `${callId}-tc-${this._reasoningStepCounter}`;
        const fnObj =
          (typeof tc.function === "object" &&
            tc.function !== null &&
            (tc.function as Record<string, unknown>)) ||
          {};
        const toolName =
          (typeof fnObj.name === "string" && fnObj.name) || "unknown";
        const rawArgs = fnObj.arguments;
        const toolArgs: unknown =
          rawArgs !== undefined && rawArgs !== null ? rawArgs : {};
        const tcEvent = await emitToolCallAttempted({
          toolCallId: tcId,
          toolName,
          toolArgs,
          agentId: this.agentId,
          parentEventId: callId,
          bufferPath: this.bufferPath,
        });
        this._emit(tcEvent);
      }
    }

    return event;
  }

  /** Emit `reasoning_step_completed` with a failure-shape payload. */
  async afterFailure(
    input: LiteLLMAfterFailure,
  ): Promise<Record<string, unknown>> {
    const ctx = this._inFlightCalls.get(input.callId);
    this._inFlightCalls.delete(input.callId);
    let durationMs = 0;
    let prompts: unknown = [];
    let model: string | undefined;
    if (ctx !== undefined) {
      durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
      prompts = ctx.messages;
      model = ctx.model;
    } else {
      model = input.model;
    }
    const failureReason = input.error
      ? renderErrorReason(input.error)
      : "unknown failure";
    const responsePayload: Record<string, unknown> = {
      _failure: true,
      failure_reason: failureReason,
    };
    const event = await emitReasoningStepCompleted({
      stepIdx: this._reasoningStepCounter,
      prompt: prompts,
      response: responsePayload,
      durationMs,
      model,
      agentId: this.agentId,
      bufferPath: this.bufferPath,
    });
    this._reasoningStepCounter += 1;
    this._emit(event);
    return event;
  }

  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}

// ── response rendering helpers ─────────────────────────────────────

function renderResponse(
  response: Record<string, unknown>,
): Record<string, unknown> {
  const rendered: Record<string, unknown> = { choices: [] };
  const choices = response.choices;
  if (Array.isArray(choices)) {
    const out: Record<string, unknown>[] = [];
    for (const choice of choices) {
      if (choice === null || choice === undefined || typeof choice !== "object") {
        continue;
      }
      const c = choice as Record<string, unknown>;
      const item: Record<string, unknown> = {};
      if (c.message !== undefined) {
        item.message = renderMessage(c.message);
      }
      if (typeof c.index === "number") item.index = c.index;
      if (typeof c.finish_reason === "string") {
        item.finish_reason = c.finish_reason;
      } else if (typeof c.finishReason === "string") {
        item.finish_reason = c.finishReason;
      }
      out.push(item);
    }
    rendered.choices = out;
  }
  const usage = response.usage;
  if (usage && typeof usage === "object") {
    const u = usage as Record<string, unknown>;
    const usageDict: Record<string, unknown> = {};
    for (const k of Object.keys(u)) {
      const v = u[k];
      if (
        typeof v === "number" ||
        typeof v === "string" ||
        typeof v === "boolean"
      ) {
        usageDict[k] = v;
      }
    }
    if (Object.keys(usageDict).length > 0) rendered.usage = usageDict;
  }
  if (typeof response.model === "string") rendered.model = response.model;
  return rendered;
}

function renderMessage(msg: unknown): Record<string, unknown> {
  if (msg === null || msg === undefined || typeof msg !== "object") {
    return { role: "?", content: String(msg ?? "") };
  }
  const m = msg as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  if (typeof m.role === "string") result.role = m.role;
  const content = m.content;
  if (
    typeof content === "string" ||
    typeof content === "number" ||
    typeof content === "boolean" ||
    Array.isArray(content) ||
    (content !== null && typeof content === "object") ||
    content === null
  ) {
    result.content = content;
  } else {
    result.content = String(content);
  }
  const toolCalls = m.tool_calls ?? m.toolCalls;
  if (Array.isArray(toolCalls) && toolCalls.length > 0) {
    const rendered: Record<string, unknown>[] = [];
    for (const tc of toolCalls) {
      rendered.push(renderToolCall(tc));
    }
    result.tool_calls = rendered;
  }
  return result;
}

function renderToolCall(tc: unknown): Record<string, unknown> {
  if (tc === null || tc === undefined || typeof tc !== "object") {
    return {};
  }
  const t = tc as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  if (typeof t.id === "string") result.id = t.id;
  if (typeof t.type === "string") result.type = t.type;
  const fn = t.function;
  if (fn !== null && fn !== undefined && typeof fn === "object") {
    const f = fn as Record<string, unknown>;
    const fnDict: Record<string, unknown> = {};
    if (typeof f.name === "string") fnDict.name = f.name;
    if (f.arguments !== undefined && f.arguments !== null) {
      fnDict.arguments =
        typeof f.arguments === "string" ? f.arguments : String(f.arguments);
    }
    if (Object.keys(fnDict).length > 0) result.function = fnDict;
  }
  return result;
}

function extractToolCallsFromResponse(
  response: Record<string, unknown>,
): Record<string, unknown>[] {
  const choices = response.choices;
  if (!Array.isArray(choices) || choices.length === 0) return [];
  const first = choices[0];
  if (first === null || first === undefined || typeof first !== "object") {
    return [];
  }
  const msg = (first as Record<string, unknown>).message;
  if (msg === null || msg === undefined || typeof msg !== "object") {
    return [];
  }
  const toolCalls =
    (msg as Record<string, unknown>).tool_calls ??
    (msg as Record<string, unknown>).toolCalls;
  if (!Array.isArray(toolCalls)) return [];
  return toolCalls.map((tc) => renderToolCall(tc));
}
