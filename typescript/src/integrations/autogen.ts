/**
 * AutoGen structural callback — Tier A (proxy/library) integration.
 *
 * AutoGen is Python-primary (autogen-agentchat / pyautogen); there is
 * no first-party TypeScript runtime as of mid-2026. This module
 * provides a **framework-agnostic structural callback class** that
 * customers wire into any TypeScript agent framework following
 * AutoGen's lifecycle pattern (multi-agent conversation, tool-call
 * dispatch, agent-error surfacing). The callback emits the existing
 * 4 ActivityEvent variants shipped by — no new variants —
 * Forever-Standard preserved.
 *
 * Customer pattern:
 *
 * ```ts
 * import { NanorixAutoGenCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixAutoGenCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 *
 * // Inside your custom TS agent loop:
 * callback.onAgentMessageReceived(inboundMessage);
 * // ... process message ...
 * callback.onAgentMessageSent(replyMessage, { model: "gpt-4o", durationMs: 1200 });
 * // ... tool execution ...
 * callback.onToolCallCompleted(toolCallId, { toolName, toolResult, durationMs });
 * ```
 *
 * The 4 public lifecycle methods map 1:1 to the 4 ActivityEvent
 * variants:
 *
 * | Public method               | ActivityEvent emitted             | When                          |
 * |-----------------------------|-----------------------------------|-------------------------------|
 * | `onAgentMessageReceived`    | `tool_call_attempted` (per tool)  | Inbound message with tool_calls |
 * | `onAgentMessageSent`        | `reasoning_step_completed`        | Outbound reply (model output) |
 * | `onToolCallCompleted`       | `tool_call_completed` OR _failed  | Tool execution returns/errors |
 * | `onAgentError`              | `tool_call_failed` (or none)      | Agent-level error             |
 *
 * Messages without `tool_calls` flow through `onAgentMessageSent`
 * only. Messages with `tool_calls` emit one `tool_call_attempted`
 * per tool_call entry; the matching `tool_call_completed` /
 * `tool_call_failed` come from the customer's downstream tool
 * execution path.
 *
 * ## Three-role discipline
 *
 * Structural adapter only. Does NOT certify AutoGen correctness,
 * multi-agent conversation safety, or LLM-output safety — those
 * determinations remain the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Captures structural facts: message arrived at time T, with
 * structural identifiers. NEVER asserts agent correctness, dialog
 * coherence, or regulatory-framework conformance.
 */

import {
  DEFAULT_BUFFER_PATH,
  TOOL_CALL_FAILURE_KINDS,
  type ReasoningStepKind,
  type ToolCallFailureKind,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
  emitToolCallCompleted,
  emitToolCallFailed,
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

/** Options for constructing a `NanorixAutoGenCallback`. */
export interface NanorixAutoGenCallbackOptions {
  framework: string;
  capsulefile: string;
  apiKey?: string;
  metadata?: Record<string, unknown>;
  frameworkVersion?: string;
  onEvent?: (event: Record<string, unknown>) => void;
  bufferPath?: string;
  agentId?: string;
}

/** Options for `onAgentMessageReceived`. */
export interface OnAgentMessageReceivedOptions {
  sender?: string;
  recipient?: string;
  agentId?: string;
  parentEventId?: string;
}

/** Options for `onAgentMessageSent`. */
export interface OnAgentMessageSentOptions {
  sender?: string;
  recipient?: string;
  prompt?: unknown;
  model?: string;
  durationMs?: number;
  agentId?: string;
  stepKind?: ReasoningStepKind;
  parentEventId?: string;
}

/** Options for `onToolCallCompleted`. */
export interface OnToolCallCompletedOptions {
  toolName?: string;
  toolResult?: unknown;
  durationMs?: number;
  error?: unknown;
  agentId?: string;
  parentEventId?: string;
}

/** Options for `onAgentError`. */
export interface OnAgentErrorOptions {
  inFlightToolCallId?: string;
  agentId?: string;
  parentEventId?: string;
}

interface InFlightToolContext {
  startTsMs: number;
  toolName: string;
  agentId?: string;
  parentEventId?: string;
  sender?: string;
  recipient?: string;
}

/** AutoGen-shape structural callback for emitting Nanorix ActivityEvents. */
export class NanorixAutoGenCallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private readonly _inFlightTools = new Map<string, InFlightToolContext>();
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixAutoGenCallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixAutoGenCallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixAutoGenCallback requires an explicit `capsulefile` option.",
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
  }

  private _nowMs(): number {
    return Math.floor(Date.now());
  }

  private _emit(event: Record<string, unknown>): void {
    if (this._onEvent !== undefined) {
      try {
        this._onEvent(event);
      } catch {
        // never crash callback path
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

  private _resolveAgentId(override: string | undefined): string | undefined {
    if (override !== undefined) return override;
    return this.agentId;
  }

  // ── public lifecycle methods (4 — one per ActivityEvent variant) ──

  /**
   * Process an inbound message; emit `tool_call_attempted` per tool_call.
   *
   * When the message includes `tool_calls`, one `tool_call_attempted`
   * event is emitted per tool_call entry. The callback records each
   * in-flight tool's `(tool_call_id, start_ts_ms, tool_name)` so
   * `onToolCallCompleted` can pair attempt + completion via
   * tool_call_id and compute `duration_ms`.
   *
   * Returns the list of emitted tool_call_ids.
   */
  async onAgentMessageReceived(
    message: unknown,
    opts: OnAgentMessageReceivedOptions = {},
  ): Promise<string[]> {
    const toolCalls = extractToolCalls(message);
    const emittedIds: string[] = [];
    if (toolCalls.length === 0) return emittedIds;
    for (const tc of toolCalls) {
      const toolCallId = extractToolCallId(tc);
      const toolName = extractAutogenToolName(tc);
      const toolArgs = extractAutogenToolArgs(tc);
      const resolvedAgentId = this._resolveAgentId(opts.agentId);
      const event = await emitToolCallAttempted({
        toolCallId,
        toolName,
        toolArgs,
        agentId: resolvedAgentId,
        parentEventId: opts.parentEventId,
        bufferPath: this.bufferPath,
      });
      this._inFlightTools.set(toolCallId, {
        startTsMs: this._nowMs(),
        toolName,
        agentId: resolvedAgentId,
        parentEventId: opts.parentEventId,
        sender: opts.sender,
        recipient: opts.recipient,
      });
      this._emit(event);
      emittedIds.push(toolCallId);
    }
    return emittedIds;
  }

  /** Emit `reasoning_step_completed` for an outbound model-produced reply. */
  async onAgentMessageSent(
    message: unknown,
    opts: OnAgentMessageSentOptions = {},
  ): Promise<Record<string, unknown>> {
    const rendered = renderMessageForReasoning(message);
    const promptPayload: unknown = opts.prompt !== undefined ? opts.prompt : [];
    const event = await emitReasoningStepCompleted({
      stepIdx: this._reasoningStepCounter,
      prompt: promptPayload,
      response: rendered,
      durationMs: opts.durationMs ?? 0,
      model: opts.model,
      agentId: this._resolveAgentId(opts.agentId),
      stepKind: opts.stepKind,
      parentEventId: opts.parentEventId,
      bufferPath: this.bufferPath,
    });
    this._reasoningStepCounter += 1;
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_completed` (success) OR `tool_call_failed` (error). */
  async onToolCallCompleted(
    toolCallId: string,
    opts: OnToolCallCompletedOptions = {},
  ): Promise<Record<string, unknown>> {
    const ctx = this._inFlightTools.get(toolCallId);
    this._inFlightTools.delete(toolCallId);

    let recordedToolName: string;
    let elapsedMs: number;
    let recordedAgentId: string | undefined;
    let recordedParent: string | undefined;
    if (ctx === undefined) {
      recordedToolName = opts.toolName ?? "unknown";
      elapsedMs = opts.durationMs ?? 0;
      recordedAgentId = this._resolveAgentId(opts.agentId);
      recordedParent = opts.parentEventId;
    } else {
      recordedToolName = opts.toolName ?? ctx.toolName;
      elapsedMs =
        opts.durationMs !== undefined
          ? opts.durationMs
          : Math.max(0, this._nowMs() - ctx.startTsMs);
      recordedAgentId =
        opts.agentId !== undefined
          ? this._resolveAgentId(opts.agentId)
          : ctx.agentId;
      recordedParent =
        opts.parentEventId !== undefined
          ? opts.parentEventId
          : ctx.parentEventId;
    }

    let event: Record<string, unknown>;
    if (opts.error !== undefined && opts.error !== null) {
      const failureReason = renderErrorReason(opts.error);
      const failureKind = this.classifyToolError(opts.error);
      event = await emitToolCallFailed({
        toolCallId,
        toolName: recordedToolName,
        failureReason,
        durationMs: elapsedMs,
        failureKind,
        agentId: recordedAgentId,
        parentEventId: recordedParent,
        bufferPath: this.bufferPath,
      });
    } else {
      event = await emitToolCallCompleted({
        toolCallId,
        toolName: recordedToolName,
        toolResult: opts.toolResult,
        durationMs: elapsedMs,
        agentId: recordedAgentId,
        parentEventId: recordedParent,
        bufferPath: this.bufferPath,
      });
    }
    this._emit(event);
    return event;
  }

  /** Surface an agent-loop error. */
  async onAgentError(
    error: unknown,
    opts: OnAgentErrorOptions = {},
  ): Promise<Record<string, unknown> | undefined> {
    if (opts.inFlightToolCallId === undefined) return undefined;
    return this.onToolCallCompleted(opts.inFlightToolCallId, {
      error,
      agentId: opts.agentId,
      parentEventId: opts.parentEventId,
    });
  }

  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}

// ── tool-call shape extractors (OpenAI + Anthropic + AutoGen shapes) ──

/**
 * Pull tool_calls list from an AutoGen-shape message.
 *
 * Stable across v0.2 dict-shape and v0.4 attribute-shape messages.
 * Supports OpenAI tool_calls schema + Anthropic content-block schema.
 */
export function extractToolCalls(message: unknown): Record<string, unknown>[] {
  if (message === null || message === undefined) return [];
  // Dict-shape (OpenAI raw + AutoGen v0.2)
  if (typeof message === "object") {
    const m = message as Record<string, unknown>;
    const tc = m.tool_calls ?? m.toolCalls;
    if (Array.isArray(tc)) {
      return tc.filter(
        (x): x is Record<string, unknown> => x !== null && typeof x === "object",
      );
    }
    // Anthropic-style: content is a list of blocks with type=tool_use
    const content = m.content;
    if (Array.isArray(content)) {
      const calls: Record<string, unknown>[] = [];
      for (const entry of content) {
        if (
          entry !== null &&
          typeof entry === "object" &&
          (entry as Record<string, unknown>).type === "tool_use"
        ) {
          calls.push(entry as Record<string, unknown>);
        }
      }
      return calls;
    }
  }
  return [];
}

/** Pull a stable tool_call_id from a tool_call entry. */
export function extractToolCallId(tc: unknown): string {
  if (tc !== null && typeof tc === "object") {
    const rec = tc as Record<string, unknown>;
    const tcid = rec.id ?? rec.tool_call_id ?? rec.toolCallId;
    if (typeof tcid === "string" && tcid) return tcid;
  }
  return `autogen_tc:${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
}

/** Pull tool name from a tool_call entry. OpenAI + Anthropic + AutoGen shapes. */
export function extractAutogenToolName(tc: unknown): string {
  if (tc === null || tc === undefined || typeof tc !== "object") {
    return "unknown";
  }
  const rec = tc as Record<string, unknown>;
  const fn = rec.function;
  if (fn !== null && fn !== undefined && typeof fn === "object") {
    const f = fn as Record<string, unknown>;
    if (typeof f.name === "string" && f.name) return f.name;
  }
  if (typeof rec.name === "string" && rec.name) return rec.name;
  return "unknown";
}

/** Pull tool arguments from a tool_call entry. */
export function extractAutogenToolArgs(tc: unknown): unknown {
  if (tc === null || tc === undefined || typeof tc !== "object") return {};
  const rec = tc as Record<string, unknown>;
  const fn = rec.function;
  if (fn !== null && fn !== undefined && typeof fn === "object") {
    const f = fn as Record<string, unknown>;
    if (f.arguments !== undefined) return f.arguments;
  }
  if (rec.input !== undefined) return rec.input;
  if (rec.arguments !== undefined) return rec.arguments;
  return {};
}

/** Render an AutoGen message for reasoning_step_completed hashing. */
export function renderMessageForReasoning(message: unknown): unknown {
  if (message === null || message === undefined) return "";
  if (typeof message !== "object") return String(message);
  const m = message as Record<string, unknown>;
  const rendered: Record<string, unknown> = {};
  for (const k of ["role", "content", "name", "source"]) {
    const v = m[k];
    if (v !== undefined) {
      if (k === "source" && rendered.role === undefined) {
        // v0.4 ChatMessage uses `source` instead of `role`
        if (typeof v === "string") rendered.role = v;
      } else {
        rendered[k] = v;
      }
    }
  }
  return Object.keys(rendered).length > 0 ? rendered : String(message);
}
