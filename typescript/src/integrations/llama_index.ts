/**
 * LlamaIndex.ts callback handler — Tier A (proxy/library) integration.
 *
 * Bridges LlamaIndex.ts's callback surface into Nanorix's in-capsule
 * ActivityEvent emission helpers. Customer wires the adapter into
 * LlamaIndex.ts's callback manager:
 *
 * ```ts
 * import { Settings, CallbackManager } from "llamaindex";
 * import { NanorixLlamaIndexCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixLlamaIndexCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 * Settings.callbackManager = new CallbackManager();
 * callback.bindTo(Settings.callbackManager);
 * ```
 *
 * LlamaIndex.ts dispatches events through a `CallbackManager.on(event,
 * handler)` pattern. Each event carries a discriminated payload shape
 * keyed by event name (`llm-start` / `llm-end` / `tool-call` /
 * `tool-result` / `agent-step` / `retrieve-start` / `retrieve-end` /
 * etc.) — equivalent structurally to Python's `CBEventType` enum
 * discrimination.
 *
 * ## Hook → ActivityEvent mapping
 *
 * | LlamaIndex.ts event   | Nanorix emission                      |
 * |-----------------------|---------------------------------------|
 * | `llm-start`           | (none — record llm_call_id)           |
 * | `llm-end`             | `reasoning_step_completed`            |
 * | `tool-call` (start)   | `tool_call_attempted`                 |
 * | `tool-result` (end)   | `tool_call_completed` (or _failed)    |
 * | `agent-step` (start)  | `tool_call_attempted` (if enabled)    |
 * | `agent-step` (end)    | `tool_call_completed` (if enabled)    |
 * | `retrieve-*`          | (none — structural noise)             |
 *
 * The 4 ActivityEvent variants shipped by ADR-021 cover the entire
 * surface — no new variants — Forever-Standard preserved (ADR-006 I0).
 *
 * ## Three-role discipline (ADR-017)
 *
 * Structural adapter only. Does NOT certify LlamaIndex correctness,
 * retrieval relevance, or LLM-output safety — those determinations
 * remain the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Captures structural facts: event happened at time T, with structural
 * identifiers. NEVER asserts retrieval correctness, hallucination-
 * freedom, or regulatory-framework conformance.
 */

import {
  DEFAULT_BUFFER_PATH,
  TOOL_CALL_FAILURE_KINDS,
  type ToolCallFailureKind,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
  emitToolCallCompleted,
  emitToolCallFailed,
} from "../helpers/activity_events.js";
import { renderErrorReason, renderToolOutput } from "./langchain.js";

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
} as const;

/** Closed-set of LlamaIndex.ts event names this adapter recognizes. */
export const VALID_LLAMAINDEX_EVENTS = [
  "llm-start",
  "llm-end",
  "tool-call",
  "tool-result",
  "agent-step",
  "agent-step-end",
  "retrieve-start",
  "retrieve-end",
  "query-start",
  "query-end",
] as const;
export type LlamaIndexEventName = (typeof VALID_LLAMAINDEX_EVENTS)[number];

/** Options for constructing a `NanorixLlamaIndexCallback`. */
export interface NanorixLlamaIndexCallbackOptions {
  framework: string;
  capsulefile: string;
  apiKey?: string;
  metadata?: Record<string, unknown>;
  frameworkVersion?: string;
  onEvent?: (event: Record<string, unknown>) => void;
  bufferPath?: string;
  agentId?: string;
  /** Whether to emit tool_call_* for `agent-step` events. Defaults true. */
  emitAgentStepEvents?: boolean;
}

interface InFlightContext {
  startTsMs: number;
  toolName?: string;
  toolCallId?: string;
  parentId?: string;
  prompts?: unknown;
  model?: string;
}

/** LlamaIndex.ts callback for emitting Nanorix ActivityEvents. */
export class NanorixLlamaIndexCallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;
  readonly emitAgentStepEvents: boolean;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private readonly _inFlightTools = new Map<string, InFlightContext>();
  private readonly _inFlightLlms = new Map<string, InFlightContext>();
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixLlamaIndexCallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixLlamaIndexCallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default per ADR-013 " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixLlamaIndexCallback requires an explicit `capsulefile` option " +
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
    this.emitAgentStepEvents = opts.emitAgentStepEvents !== false;
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

  // ── event-name-dispatched API ──────────────────────────────────

  /**
   * Process an LlamaIndex.ts event by name.
   *
   * Customer wires this via `callbackManager.on("event-name", (payload) =>
   * callback.handleEvent("event-name", payload))`. Returns the emitted
   * ActivityEvent (if any), or undefined for events that are silently
   * dropped as structural noise.
   */
  async handleEvent(
    eventName: string,
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown> | undefined> {
    switch (eventName) {
      case "llm-start":
        return this.handleLLMStart(payload);
      case "llm-end":
        return this.handleLLMEnd(payload);
      case "tool-call":
        return this.handleToolCall(payload);
      case "tool-result":
        return this.handleToolResult(payload);
      case "agent-step":
        if (this.emitAgentStepEvents) return this.handleAgentStepStart(payload);
        return undefined;
      case "agent-step-end":
        if (this.emitAgentStepEvents) return this.handleAgentStepEnd(payload);
        return undefined;
      default:
        // Structural noise (retrieve, query, embedding, tree, etc.)
        return undefined;
    }
  }

  /** Open an LLM-call context. */
  async handleLLMStart(
    payload: Record<string, unknown> | undefined,
  ): Promise<undefined> {
    const eventId = String(payload?.eventId ?? payload?.event_id ?? "");
    if (!eventId) return undefined;
    const prompts = extractLlmInputs(payload);
    const model = extractModelNameFromPayload(payload);
    this._inFlightLlms.set(eventId, {
      startTsMs: this._nowMs(),
      prompts,
      model,
      parentId: payload?.parentId as string | undefined,
    });
    return undefined;
  }

  /** Emit `reasoning_step_completed` for the just-finished LLM call. */
  async handleLLMEnd(
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown>> {
    const eventId = String(payload?.eventId ?? payload?.event_id ?? "");
    const ctx = this._inFlightLlms.get(eventId);
    if (eventId) this._inFlightLlms.delete(eventId);
    let durationMs = 0;
    let prompts: unknown = [];
    let model: string | undefined;
    if (ctx !== undefined) {
      durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
      prompts = ctx.prompts ?? [];
      model = ctx.model;
    }
    const response = extractLlmResponse(payload);
    const event = await emitReasoningStepCompleted({
      stepIdx: this._reasoningStepCounter,
      prompt: prompts,
      response,
      durationMs,
      model,
      agentId: this.agentId,
      bufferPath: this.bufferPath,
    });
    this._reasoningStepCounter += 1;
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_attempted` and record start_ts. */
  async handleToolCall(
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown>> {
    const eventId = String(payload?.eventId ?? payload?.event_id ?? `tool:${this._nowMs()}`);
    const { toolName, toolArgs } = extractToolNameAndArgs(payload);
    const toolCallId = eventId;
    const event = await emitToolCallAttempted({
      toolCallId,
      toolName,
      toolArgs,
      agentId: this.agentId,
      parentEventId: payload?.parentId as string | undefined,
      bufferPath: this.bufferPath,
    });
    this._inFlightTools.set(eventId, {
      startTsMs: this._nowMs(),
      toolName,
      toolCallId,
      parentId: payload?.parentId as string | undefined,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_completed` or `tool_call_failed`. */
  async handleToolResult(
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown> | undefined> {
    const eventId = String(payload?.eventId ?? payload?.event_id ?? "");
    const ctx = this._inFlightTools.get(eventId);
    if (eventId) this._inFlightTools.delete(eventId);
    if (ctx === undefined) return undefined;
    const durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);

    const errorField = payload?.error ?? payload?.exception;
    if (errorField !== undefined && errorField !== null) {
      const failureReason = renderErrorReason(errorField);
      const failureKind = this.classifyToolError(errorField);
      const event = await emitToolCallFailed({
        toolCallId: ctx.toolCallId ?? eventId,
        toolName: ctx.toolName ?? "unknown",
        failureReason,
        durationMs,
        failureKind,
        agentId: this.agentId,
        parentEventId: ctx.parentId,
        bufferPath: this.bufferPath,
      });
      this._emit(event);
      return event;
    }
    const output =
      payload?.functionOutput ?? payload?.response ?? payload?.result ?? null;
    const event = await emitToolCallCompleted({
      toolCallId: ctx.toolCallId ?? eventId,
      toolName: ctx.toolName ?? "unknown",
      toolResult: renderToolOutput(output),
      durationMs,
      agentId: this.agentId,
      parentEventId: ctx.parentId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_attempted` for an agent step. */
  async handleAgentStepStart(
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown>> {
    const eventId = String(
      payload?.eventId ?? payload?.event_id ?? `agent_step:${this._nowMs()}`,
    );
    const toolCallId = eventId;
    const toolName = "agent_step";
    const event = await emitToolCallAttempted({
      toolCallId,
      toolName,
      toolArgs: sanitizePayload(payload),
      agentId: this.agentId,
      parentEventId: payload?.parentId as string | undefined,
      bufferPath: this.bufferPath,
    });
    this._inFlightTools.set(eventId, {
      startTsMs: this._nowMs(),
      toolName,
      toolCallId,
      parentId: payload?.parentId as string | undefined,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_completed` for an agent step. */
  async handleAgentStepEnd(
    payload: Record<string, unknown> | undefined,
  ): Promise<Record<string, unknown> | undefined> {
    const eventId = String(payload?.eventId ?? payload?.event_id ?? "");
    const ctx = this._inFlightTools.get(eventId);
    if (eventId) this._inFlightTools.delete(eventId);
    if (ctx === undefined) return undefined;
    const durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
    const event = await emitToolCallCompleted({
      toolCallId: ctx.toolCallId ?? eventId,
      toolName: ctx.toolName ?? "agent_step",
      toolResult: sanitizePayload(payload),
      durationMs,
      agentId: this.agentId,
      parentEventId: ctx.parentId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /**
   * Bind the adapter to a LlamaIndex.ts CallbackManager-shape object.
   *
   * The CallbackManager object must expose `on(eventName, handler)`. We
   * subscribe to all valid event names; handlers dispatch via
   * `handleEvent`. Returns an unbind function to remove the
   * subscriptions.
   */
  bindTo(callbackManager: {
    on?: (eventName: string, handler: (payload: unknown) => unknown) => unknown;
  }): () => void {
    if (typeof callbackManager?.on !== "function") {
      throw new TypeError(
        "bindTo requires a LlamaIndex.ts CallbackManager-shape object " +
          "(must expose an `on(eventName, handler)` method).",
      );
    }
    const handlers: Array<{ name: string; fn: (p: unknown) => unknown }> = [];
    for (const eventName of VALID_LLAMAINDEX_EVENTS) {
      const fn = (p: unknown): unknown => {
        const payload =
          p && typeof p === "object" ? (p as Record<string, unknown>) : {};
        // Fire-and-forget for event-bus shape; errors swallowed.
        this.handleEvent(eventName, payload).catch(() => undefined);
        return undefined;
      };
      try {
        callbackManager.on(eventName, fn);
        handlers.push({ name: eventName, fn });
      } catch {
        // Silent — some event names may not be valid for the customer's
        // CallbackManager variant. Best-effort subscription.
      }
    }
    return () => {
      const cm = callbackManager as Record<string, unknown>;
      const off = cm.off ?? cm.removeListener;
      if (typeof off !== "function") return;
      const offBound = (off as (...args: unknown[]) => unknown).bind(
        callbackManager,
      );
      for (const h of handlers) {
        try {
          offBound(h.name, h.fn);
        } catch {
          // ignore
        }
      }
    };
  }

  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}

// ── payload extraction helpers ─────────────────────────────────────

function extractLlmInputs(
  payload: Record<string, unknown> | undefined,
): unknown {
  if (!payload) return [];
  const messages = payload.messages;
  if (messages !== undefined) return renderMessages(messages);
  const prompt = payload.formattedPrompt ?? payload.prompt;
  if (prompt !== undefined) return prompt;
  return [];
}

function extractLlmResponse(
  payload: Record<string, unknown> | undefined,
): unknown {
  if (!payload) return "";
  const resp = payload.response ?? payload.completion;
  if (resp === undefined || resp === null) return "";
  if (
    typeof resp === "string" ||
    typeof resp === "number" ||
    typeof resp === "boolean"
  ) {
    return resp;
  }
  if (typeof resp === "object") {
    const recResp = resp as Record<string, unknown>;
    if (recResp.message !== undefined) {
      return renderMessages([recResp.message])[0] ?? "";
    }
    if (typeof recResp.text === "string") return { text: recResp.text };
    if (Array.isArray(recResp)) return recResp;
    return recResp;
  }
  return String(resp);
}

function extractModelNameFromPayload(
  payload: Record<string, unknown> | undefined,
): string | undefined {
  if (!payload) return undefined;
  const m = payload.modelName ?? payload.model;
  if (typeof m === "string") return m;
  return undefined;
}

function extractToolNameAndArgs(
  payload: Record<string, unknown> | undefined,
): { toolName: string; toolArgs: unknown } {
  let toolName = "unknown";
  let toolArgs: unknown = {};
  if (!payload) return { toolName, toolArgs };

  const tool = payload.tool;
  if (tool !== null && tool !== undefined && typeof tool === "object") {
    const toolRec = tool as Record<string, unknown>;
    if (typeof toolRec.name === "string" && toolRec.name) {
      toolName = toolRec.name;
    }
  }
  const fnCall = payload.functionCall ?? payload.function_call;
  if (fnCall !== null && fnCall !== undefined) {
    if (typeof fnCall === "object" && !Array.isArray(fnCall)) {
      const fnRec = fnCall as Record<string, unknown>;
      if (toolName === "unknown" && typeof fnRec.name === "string") {
        toolName = fnRec.name;
      }
      toolArgs = fnRec;
    } else {
      toolArgs = fnCall;
    }
  }
  // Direct tool_args / arguments key
  if (toolArgs === undefined || (typeof toolArgs === "object" && Object.keys(toolArgs as object).length === 0)) {
    const direct = payload.arguments ?? payload.args ?? payload.input;
    if (direct !== undefined) toolArgs = direct;
  }
  // Direct toolName override
  const directName = payload.toolName ?? payload.name;
  if (toolName === "unknown" && typeof directName === "string" && directName) {
    toolName = directName;
  }
  return { toolName, toolArgs };
}

function renderMessages(messages: unknown): Record<string, unknown>[] {
  const out: Record<string, unknown>[] = [];
  if (!Array.isArray(messages)) return out;
  for (const m of messages) {
    if (m === null || m === undefined) continue;
    if (typeof m !== "object") {
      out.push({ role: "?", content: String(m) });
      continue;
    }
    const rec = m as Record<string, unknown>;
    let role: unknown = rec.role;
    if (role !== undefined && typeof role !== "string") {
      const roleObj = role as { value?: unknown };
      role = typeof roleObj.value === "string" ? roleObj.value : String(role);
    }
    let content: unknown = rec.content;
    if (
      content !== null &&
      content !== undefined &&
      typeof content !== "string" &&
      typeof content !== "number" &&
      typeof content !== "boolean" &&
      !Array.isArray(content) &&
      typeof content !== "object"
    ) {
      content = String(content);
    }
    out.push({ role: (role as string) || "?", content: content ?? "" });
  }
  return out;
}

function sanitizePayload(
  payload: Record<string, unknown> | undefined,
): Record<string, unknown> {
  if (!payload) return {};
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(payload)) {
    out[k] = sanitizeValue(payload[k]);
  }
  return out;
}

function sanitizeValue(value: unknown): unknown {
  if (
    value === null ||
    value === undefined ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (Array.isArray(value)) return value.map(sanitizeValue);
  if (value instanceof Set) {
    return Array.from(value).map(sanitizeValue);
  }
  if (typeof value === "object") {
    const rec = value as Record<string, unknown>;
    if (typeof rec.toJSON === "function") {
      try {
        return (rec.toJSON as () => unknown)();
      } catch {
        // fallthrough
      }
    }
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(rec)) {
      out[k] = sanitizeValue(rec[k]);
    }
    return out;
  }
  return String(value);
}

// Export helpers for downstream module reuse (otel_genai / autogen, etc.).
export { sanitizePayload as _sanitizeLlamaIndexPayload };
