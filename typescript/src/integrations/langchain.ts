/**
 * LangChain.js callback handler — Tier A (proxy/library) integration.
 *
 * Bridges LangChain.js's existing callback system into Nanorix's
 * in-capsule ActivityEvent emission helpers. Customer wires the
 * adapter's bound methods into their existing chain or agent_executor:
 *
 * ```ts
 * import { NanorixLangChainCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixLangChainCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 * await chain.invoke(
 *   { input: "..." },
 *   { callbacks: [callback.asLangChainCallback()] },
 * );
 * ```
 *
 * The callback runs INSIDE a Nanorix capsule (customer wraps the agent
 * run via `client.capsules.create()` or the future `@nanorix.workload`
 * decorator). LangChain.js's hook surface maps onto the 4 existing
 * ActivityEvent variants shipped by (`tool_call_attempted` /
 * `tool_call_completed` / `tool_call_failed` /
 * `reasoning_step_completed`). No new variants —
 * Forever-Standard discipline preserved.
 *
 * ## Three-tier integration model
 *
 * | Tier | Effort | Claim                                              |
 * |------|--------|----------------------------------------------------|
 * | A    | 5 min  | Per-call receipts (this callback)                  |
 * | B    | 30 min | Full workflow destruction proof (decorator)        |
 * | C    | 1 hr   | Per-route receipts (Express/Next.js middleware)    |
 *
 * This callback is Tier A. The claim is partial: per-LLM-call and
 * per-tool-call receipts streaming into the capsule's activity_trail
 * buffer; the buffer drains into the AuditProof at capsule destroy.
 *
 * ## Hook → ActivityEvent mapping
 *
 * | LangChain.js hook      | ActivityEvent emitted          |
 * |------------------------|--------------------------------|
 * | handleChainStart       | (none — record start_ts)       |
 * | handleLLMStart         | (none — record llm_call_id)    |
 * | handleChatModelStart   | (none — record llm_call_id)    |
 * | handleLLMEnd           | reasoning_step_completed       |
 * | handleToolStart        | tool_call_attempted            |
 * | handleToolEnd          | tool_call_completed            |
 * | handleToolError        | tool_call_failed               |
 * | handleChainEnd         | (none — flush buffer)          |
 * | handleChainError       | (none — mark incomplete)       |
 *
 * Per-method snake_case → camelCase translation: LangChain.js callback
 * method naming uses camelCase (`handleLLMStart`, `handleToolEnd`)
 * versus Python's snake_case (`on_llm_start`, `on_tool_end`). The
 * TypeScript surface mirrors LangChain.js conventions, while preserving
 * the byte-identical ActivityEvent JSON shape on the wire.
 *
 * ## Three-role discipline
 *
 * Structural adapter only. Does NOT certify LangChain conformance,
 * LangChain agent correctness, LLM output safety, or PHI handling —
 * those determinations remain the customer's responsibility.
 *
 * ## Narrow signed claim (feedback_narrow_signed_claim_auditor_certifies.md)
 *
 * Captures structural facts: event happened at time T, with
 * structural identifiers (tool_call_id, model name, duration_ms).
 * NEVER asserts LLM correctness, hallucination-freedom, or
 * regulatory-framework conformance.
 *
 * ## Design constraints
 *
 * - `framework` REQUIRED — no silent default.
 * - No PHI scanning (Alt B). Adapter is structural.
 * - No raw payload retention. SHA-512 hex digests only.
 * - No actual LangChain.js framework imports — customer brings the
 *   framework; this module provides the callback shape.
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

// Framework names match `compliance/*Context.framework_id` values.
export const FRAMEWORK_HIPAA = "HIPAA";
export const FRAMEWORK_GDPR = "GDPR";
export const FRAMEWORK_SOC2 = "SOC2";
export const FRAMEWORK_PCI_DSS = "PCI_DSS";
export const FRAMEWORK_HITRUST = "HITRUST";
export const FRAMEWORK_EU_AI_ACT = "EU_AI_ACT";

/** Canonical framework labels (customer may pass any non-empty string). */
export const ALLOWED_FRAMEWORKS: ReadonlySet<string> = new Set<string>([
  FRAMEWORK_HIPAA,
  FRAMEWORK_GDPR,
  FRAMEWORK_SOC2,
  FRAMEWORK_PCI_DSS,
  FRAMEWORK_HITRUST,
  FRAMEWORK_EU_AI_ACT,
]);

// Map common Error class names → closed-enum ToolCallFailureKind strings.
// Customer can override via subclass + `classifyToolError`.
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

/** Options for constructing a `NanorixLangChainCallback`. */
export interface NanorixLangChainCallbackOptions {
  /** Compliance framework label (e.g., `"HIPAA"`). REQUIRED. */
  framework: string;
  /** Capsulefile name the agent is running under. REQUIRED. */
  capsulefile: string;
  /** Optional Nanorix API key. Defaults to env `NANORIX_API_KEY`. */
  apiKey?: string;
  /** Optional metadata dict for logging context (NOT bound into AuditProof). */
  metadata?: Record<string, unknown>;
  /** Optional framework version (e.g., `"2026-02"`). */
  frameworkVersion?: string;
  /** Optional debugging hook invoked for every emitted ActivityEvent. */
  onEvent?: (event: Record<string, unknown>) => void;
  /** Override for the activity-trail buffer location. */
  bufferPath?: string;
  /** Optional agent identifier propagated to every emitted event. */
  agentId?: string;
}

/**
 * Internal in-flight context for an LLM or tool call.
 *
 * Tracks monotonic start time + identifier metadata so `handleLLMEnd` /
 * `handleToolEnd` can compute `duration_ms` and recover the matching
 * `tool_call_id` / `tool_name` without depending on the end-hook's
 * payload.
 */
interface InFlightContext {
  startTsMs: number;
  toolName?: string;
  toolCallId?: string;
  prompts?: unknown;
  model?: string;
  parentRunId?: string;
}

/**
 * LangChain.js-shape callback for emitting Nanorix ActivityEvents.
 *
 * Customer instantiates once per agent run, passes
 * `callback.asLangChainCallback()` into the LangChain.js `callbacks: []`
 * slot. Each handler method maps directly onto a LangChain.js
 * BaseCallbackHandler method (camelCase TypeScript convention).
 */
export class NanorixLangChainCallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private readonly _inFlightChains = new Map<string, InFlightContext>();
  private readonly _inFlightLlms = new Map<string, InFlightContext>();
  private readonly _inFlightTools = new Map<string, InFlightContext>();
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixLangChainCallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixLangChainCallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixLangChainCallback requires an explicit `capsulefile` option " +
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
  }

  // ── helpers ────────────────────────────────────────────────────

  private _nowMs(): number {
    // Monotonic-ish; performance.now isn't available in all Node envs.
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

  /** Map an Error's class name to a closed-enum failure_kind. */
  classifyToolError(error: unknown): ToolCallFailureKind | undefined {
    if (!(error instanceof Error)) return undefined;
    const className = error.constructor?.name ?? "Error";
    const direct = DEFAULT_FAILURE_KIND_MAP[className];
    if (direct !== undefined && TOOL_CALL_FAILURE_KINDS.includes(direct)) {
      return direct;
    }
    // Walk prototype chain for parent-class match.
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

  // ── chain hooks (no events emitted; track lifecycle) ───────────

  /** Open a chain run context. No event written. */
  async handleChainStart(
    _serialized: Record<string, unknown> | undefined,
    _inputs: Record<string, unknown> | undefined,
    runId: string,
    parentRunId?: string,
  ): Promise<void> {
    this._inFlightChains.set(runId, {
      startTsMs: this._nowMs(),
      parentRunId,
    });
  }

  /** Close a chain run context. No event written. */
  async handleChainEnd(
    _outputs: unknown,
    runId: string,
    _parentRunId?: string,
  ): Promise<void> {
    this._inFlightChains.delete(runId);
  }

  /** Mark chain context as errored. No event written. */
  async handleChainError(
    _error: unknown,
    runId: string,
    _parentRunId?: string,
  ): Promise<void> {
    this._inFlightChains.delete(runId);
  }

  // ── LLM hooks (emit reasoning_step_completed at end) ───────────

  /** Record LLM call start. No event written. */
  async handleLLMStart(
    serialized: Record<string, unknown> | undefined,
    prompts: string[] | undefined,
    runId: string,
    parentRunId?: string,
    extraParams?: Record<string, unknown>,
  ): Promise<void> {
    const model = extractModelName(serialized, extraParams);
    this._inFlightLlms.set(runId, {
      startTsMs: this._nowMs(),
      prompts: prompts ? [...prompts] : [],
      model,
      parentRunId,
    });
  }

  /** Record chat-model call start. */
  async handleChatModelStart(
    serialized: Record<string, unknown> | undefined,
    messages: unknown[][] | undefined,
    runId: string,
    parentRunId?: string,
    extraParams?: Record<string, unknown>,
  ): Promise<void> {
    const rendered: Record<string, unknown>[] = [];
    if (Array.isArray(messages)) {
      for (const batch of messages) {
        if (Array.isArray(batch)) {
          for (const msg of batch) {
            rendered.push(renderMessage(msg));
          }
        }
      }
    }
    const model = extractModelName(serialized, extraParams);
    this._inFlightLlms.set(runId, {
      startTsMs: this._nowMs(),
      prompts: rendered,
      model,
      parentRunId,
    });
  }

  /** Emit `reasoning_step_completed` for the just-finished LLM call. */
  async handleLLMEnd(
    response: unknown,
    runId: string,
    _parentRunId?: string,
  ): Promise<Record<string, unknown>> {
    const ctx = this._inFlightLlms.get(runId);
    this._inFlightLlms.delete(runId);
    let durationMs = 0;
    let prompts: unknown = [];
    let model: string | undefined;
    if (ctx !== undefined) {
      durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
      prompts = ctx.prompts ?? [];
      model = ctx.model;
    }
    const rendered = renderLlmResult(response);
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
    return event;
  }

  /** Pop the in-flight LLM context. No ActivityEvent emitted. */
  async handleLLMError(
    _error: unknown,
    runId: string,
    _parentRunId?: string,
  ): Promise<void> {
    this._inFlightLlms.delete(runId);
  }

  // ── tool hooks (emit attempted + completed/failed) ─────────────

  /** Emit `tool_call_attempted` and record start_ts. */
  async handleToolStart(
    serialized: Record<string, unknown> | undefined,
    inputStr: string | undefined,
    runId: string,
    parentRunId?: string,
    _tags?: string[],
    _metadata?: Record<string, unknown>,
    _name?: string,
    inputs?: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const toolName = extractToolName(serialized);
    const toolArgs: unknown = inputs !== undefined ? inputs : (inputStr ?? "");
    const toolCallId = runId;
    const event = await emitToolCallAttempted({
      toolCallId,
      toolName,
      toolArgs,
      agentId: this.agentId,
      parentEventId: parentRunId,
      bufferPath: this.bufferPath,
    });
    this._inFlightTools.set(runId, {
      startTsMs: this._nowMs(),
      toolName,
      toolCallId,
      parentRunId,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_completed` for the just-finished tool call. */
  async handleToolEnd(
    output: unknown,
    runId: string,
    parentRunId?: string,
  ): Promise<Record<string, unknown>> {
    const ctx = this._inFlightTools.get(runId);
    this._inFlightTools.delete(runId);
    const toolCallId = ctx?.toolCallId ?? runId;
    const toolName = ctx?.toolName ?? "unknown";
    const durationMs =
      ctx !== undefined ? Math.max(0, this._nowMs() - ctx.startTsMs) : 0;
    const rendered = renderToolOutput(output);
    const event = await emitToolCallCompleted({
      toolCallId,
      toolName,
      toolResult: rendered,
      durationMs,
      agentId: this.agentId,
      parentEventId: parentRunId ?? ctx?.parentRunId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_failed` for the just-failed tool call. */
  async handleToolError(
    error: unknown,
    runId: string,
    parentRunId?: string,
  ): Promise<Record<string, unknown>> {
    const ctx = this._inFlightTools.get(runId);
    this._inFlightTools.delete(runId);
    const toolCallId = ctx?.toolCallId ?? runId;
    const toolName = ctx?.toolName ?? "unknown";
    const durationMs =
      ctx !== undefined ? Math.max(0, this._nowMs() - ctx.startTsMs) : 0;
    const failureReason = renderErrorReason(error);
    const failureKind = this.classifyToolError(error);
    const event = await emitToolCallFailed({
      toolCallId,
      toolName,
      failureReason,
      durationMs,
      failureKind,
      agentId: this.agentId,
      parentEventId: parentRunId ?? ctx?.parentRunId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /**
   * Return a plain-object structural callback handler suitable for the
   * LangChain.js `callbacks: []` slot.
   *
   * The returned object exposes the bound camelCase handler methods
   * LangChain.js expects from a `BaseCallbackHandler`. Customer brings
   * the framework — this method returns a structural shape, not a
   * `BaseCallbackHandler` subclass instance.
   */
  asLangChainCallback(): Record<string, unknown> {
    return {
      name: "NanorixLangChainCallback",
      handleChainStart: this.handleChainStart.bind(this),
      handleChainEnd: this.handleChainEnd.bind(this),
      handleChainError: this.handleChainError.bind(this),
      handleLLMStart: this.handleLLMStart.bind(this),
      handleChatModelStart: this.handleChatModelStart.bind(this),
      handleLLMEnd: this.handleLLMEnd.bind(this),
      handleLLMError: this.handleLLMError.bind(this),
      handleToolStart: this.handleToolStart.bind(this),
      handleToolEnd: this.handleToolEnd.bind(this),
      handleToolError: this.handleToolError.bind(this),
    };
  }

  /** Expose the on-event debug hook for fan-out (e.g., OTel exporter). */
  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}

// ── shared rendering helpers (also reused by langgraph.ts) ─────────

/** Pull tool name from LangChain.js serialized payload. */
export function extractToolName(
  serialized: Record<string, unknown> | undefined,
): string {
  if (!serialized) return "unknown";
  const name = serialized.name;
  if (typeof name === "string" && name) return name;
  const sid = serialized.id;
  if (Array.isArray(sid) && sid.length > 0) {
    const last = sid[sid.length - 1];
    if (typeof last === "string") return last;
  }
  return "unknown";
}

/** Pull model identifier from LangChain.js serialized + extraParams. */
export function extractModelName(
  serialized: Record<string, unknown> | undefined,
  extraParams: Record<string, unknown> | undefined,
): string | undefined {
  const ip = extraParams?.invocation_params;
  if (ip && typeof ip === "object") {
    const ipRec = ip as Record<string, unknown>;
    for (const k of ["model", "model_name", "model_id"]) {
      const v = ipRec[k];
      if (typeof v === "string") return v;
    }
  }
  if (!serialized) return undefined;
  const kw = serialized.kwargs;
  if (kw && typeof kw === "object") {
    const kwRec = kw as Record<string, unknown>;
    for (const k of ["model", "model_name", "model_id"]) {
      const v = kwRec[k];
      if (typeof v === "string") return v;
    }
  }
  for (const k of ["model", "model_name", "model_id"]) {
    const v = serialized[k];
    if (typeof v === "string") return v;
  }
  return undefined;
}

/** Render a LangChain.js message-like value into a JSON-safe dict. */
export function renderMessage(msg: unknown): Record<string, unknown> {
  if (msg === null || msg === undefined) return { role: "?", content: "" };
  if (typeof msg !== "object") return { role: "?", content: String(msg) };
  const rec = msg as Record<string, unknown>;
  let role: unknown = rec._getType
    ? typeof rec._getType === "function"
      ? (rec._getType as () => string)()
      : undefined
    : undefined;
  if (role === undefined) role = rec.type;
  if (role === undefined) role = rec.role;
  if (typeof role !== "string") role = rec.constructor?.name ?? "?";
  let content: unknown = rec.content;
  if (content === undefined) content = "";
  if (
    typeof content !== "string" &&
    typeof content !== "number" &&
    typeof content !== "boolean" &&
    content !== null &&
    !Array.isArray(content) &&
    typeof content !== "object"
  ) {
    content = String(content);
  }
  return { role: role as string, content };
}

/** Render a LangChain.js LLM result into a JSON-safe dict. */
export function renderLlmResult(response: unknown): Record<string, unknown> {
  const rendered: Record<string, unknown> = { generations: [] };
  if (response === null || response === undefined || typeof response !== "object") {
    return rendered;
  }
  const rec = response as Record<string, unknown>;
  const gens = rec.generations;
  if (Array.isArray(gens)) {
    const out: Record<string, unknown>[][] = [];
    for (const batch of gens) {
      if (!Array.isArray(batch)) continue;
      const batchOut: Record<string, unknown>[] = [];
      for (const g of batch) {
        if (g === null || g === undefined || typeof g !== "object") continue;
        const gr = g as Record<string, unknown>;
        const item: Record<string, unknown> = {};
        if (typeof gr.text === "string") item.text = gr.text;
        if (gr.message !== undefined) item.message = renderMessage(gr.message);
        if (Object.keys(item).length > 0) batchOut.push(item);
      }
      out.push(batchOut);
    }
    rendered.generations = out;
  }
  const llmOutput = rec.llmOutput ?? rec.llm_output;
  if (llmOutput && typeof llmOutput === "object") {
    const lo = llmOutput as Record<string, unknown>;
    const usage = lo.tokenUsage ?? lo.token_usage ?? lo.usage;
    if (usage && typeof usage === "object") {
      const usageRec = usage as Record<string, unknown>;
      const usageFiltered: Record<string, unknown> = {};
      for (const k of Object.keys(usageRec)) {
        const v = usageRec[k];
        if (
          typeof v === "number" ||
          typeof v === "string" ||
          typeof v === "boolean"
        ) {
          usageFiltered[k] = v;
        }
      }
      rendered.token_usage = usageFiltered;
    }
    const model = lo.modelName ?? lo.model_name ?? lo.model;
    if (typeof model === "string") rendered.model = model;
  }
  return rendered;
}

/** Render a tool output for canonical-JSON hashing. */
export function renderToolOutput(output: unknown): unknown {
  if (
    output === null ||
    output === undefined ||
    typeof output === "string" ||
    typeof output === "number" ||
    typeof output === "boolean" ||
    Array.isArray(output)
  ) {
    return output;
  }
  if (typeof output === "object") {
    const rec = output as Record<string, unknown>;
    // Pydantic-like model_dump (rare in TS but possible)
    if (typeof rec.toJSON === "function") {
      try {
        return (rec.toJSON as () => unknown)();
      } catch {
        // fallthrough
      }
    }
    return output;
  }
  return String(output);
}

/** Render an error into a `failure_reason` string. */
export function renderErrorReason(error: unknown): string {
  if (error instanceof Error) {
    return `${error.constructor?.name ?? "Error"}: ${error.message}`;
  }
  if (typeof error === "string") return error;
  if (error === null || error === undefined) return "unknown error";
  try {
    return JSON.stringify(error);
  } catch {
    return String(error);
  }
}
