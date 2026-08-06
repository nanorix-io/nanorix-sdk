/**
 * LangGraph.js callback handler — Tier A (proxy/library) integration.
 *
 * Bridges LangGraph.js's callback surface into Nanorix's in-capsule
 * ActivityEvent emission helpers. Customer wires the adapter into their
 * compiled graph:
 *
 * ```ts
 * import { NanorixLangGraphCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixLangGraphCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 * await graph.invoke(
 *   { input: "..." },
 *   { callbacks: [callback.asLangChainCallback()] },
 * );
 * ```
 *
 * LangGraph.js does NOT define its own callback base class — it
 * dispatches into the standard LangChain.js BaseCallbackHandler
 * interface. Each graph node invocation surfaces as a
 * `handleChainStart` / `handleChainEnd` cycle, where `serialized.name`
 * is the node name and `tags` includes `"graph:step:N"`; `metadata`
 * carries `langgraph_node`. LLM calls inside nodes route through
 * `handleLLM*` / `handleChatModelStart`, and tool calls route through
 * `handleTool*`. The same 4 ActivityEvent variants shipped by
 * cover the surface — no new variants — Forever-Standard preserved.
 *
 * ## Architectural distinction from LangChain.js
 *
 * A LangGraph node IS structurally a tool from the AuditProof's
 * perspective (it's a unit of work the agent invoked). This adapter
 * emits:
 *
 * - `tool_call_attempted` on `handleChainStart` *when the chain is a
 *   graph node* (heuristic: `tags` contains `"graph:step:*"` OR
 *   `metadata.langgraph_node` is set).
 * - `tool_call_completed` on the matching `handleChainEnd`.
 * - `tool_call_failed` on the matching `handleChainError`.
 * - `reasoning_step_completed` on `handleLLMEnd` (any LLM call inside
 *   any node).
 * - Tool calls invoked from inside a node STILL emit their own
 *   tool_call_* events via `handleTool*`.
 * - Lifecycle hooks for non-node chains (the outer graph wrapper)
 *   write nothing.
 *
 * ## Three-role discipline
 *
 * Structural adapter only. Does NOT certify LangGraph correctness,
 * graph-topology validity, or workflow safety. Does NOT certify LLM
 * output safety, PHI handling, or regulatory-framework conformance —
 * those determinations remain the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Captures structural facts: node invoked at time T, with structural
 * identifiers (run_id, node name, duration_ms). NEVER asserts LangGraph
 * correctness or workflow-level safety.
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
import {
  extractModelName,
  extractToolName,
  renderErrorReason,
  renderLlmResult,
  renderMessage,
  renderToolOutput,
} from "./langchain.js";

// Framework names.
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

/**
 * LangGraph.js dispatch tag prefix. Each node invocation tags the
 * chain run with `"graph:step:N"` plus other tags. The presence of
 * this tag (or `metadata.langgraph_node`) is how we discriminate a
 * node chain from the outer graph wrapper or a generic Runnable.
 */
const LANGGRAPH_NODE_TAG_PREFIX = "graph:step:";

/** Options for constructing a `NanorixLangGraphCallback`. */
export interface NanorixLangGraphCallbackOptions {
  framework: string;
  capsulefile: string;
  apiKey?: string;
  metadata?: Record<string, unknown>;
  frameworkVersion?: string;
  onEvent?: (event: Record<string, unknown>) => void;
  bufferPath?: string;
  agentId?: string;
  /**
   * Whether to emit `tool_call_*` per LangGraph node. Defaults to true.
   * Set to false to suppress per-node receipts (useful for fan-out
   * graphs).
   */
  emitNodeEvents?: boolean;
}

interface InFlightContext {
  startTsMs: number;
  toolName?: string;
  toolCallId?: string;
  prompts?: unknown;
  model?: string;
  parentRunId?: string;
}

/**
 * Discriminate a LangGraph node chain from outer graph wrapper.
 *
 * Heuristic mirrors Python `_is_langgraph_node` byte-equivalently:
 * - `tags` contains a `"graph:step:N"` entry (most reliable).
 * - `metadata.langgraph_node` is set (alternate signal).
 */
function isLangGraphNode(
  tags: string[] | undefined,
  metadata: Record<string, unknown> | undefined,
): boolean {
  if (tags) {
    for (const t of tags) {
      if (typeof t === "string" && t.startsWith(LANGGRAPH_NODE_TAG_PREFIX)) {
        return true;
      }
    }
  }
  if (
    metadata &&
    typeof metadata === "object" &&
    "langgraph_node" in metadata
  ) {
    return true;
  }
  return false;
}

/** Pull the LangGraph node name from the dispatch payload. */
function extractNodeName(
  serialized: Record<string, unknown> | undefined,
  metadata: Record<string, unknown> | undefined,
): string {
  if (serialized) {
    const name = serialized.name;
    if (typeof name === "string" && name) return name;
  }
  if (metadata && typeof metadata === "object") {
    const name = metadata.langgraph_node;
    if (typeof name === "string" && name) return name;
  }
  return "unknown_node";
}

/** LangGraph.js callback for emitting Nanorix ActivityEvents. */
export class NanorixLangGraphCallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;
  readonly emitNodeEvents: boolean;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private readonly _inFlightNodes = new Map<string, InFlightContext>();
  private readonly _inFlightTools = new Map<string, InFlightContext>();
  private readonly _inFlightLlms = new Map<string, InFlightContext>();
  private readonly _inFlightOuterChains = new Map<string, InFlightContext>();
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixLangGraphCallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixLangGraphCallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixLangGraphCallback requires an explicit `capsulefile` option " +
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
    this.emitNodeEvents = opts.emitNodeEvents !== false;
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

  // ── chain hooks (node-aware) ───────────────────────────────────

  /**
   * Emit `tool_call_attempted` if this chain is a LangGraph node;
   * otherwise treat as outer-graph lifecycle (no event).
   */
  async handleChainStart(
    serialized: Record<string, unknown> | undefined,
    inputs: Record<string, unknown> | undefined,
    runId: string,
    parentRunId?: string,
    tags?: string[],
    metadata?: Record<string, unknown>,
  ): Promise<Record<string, unknown> | undefined> {
    const isNode = isLangGraphNode(tags, metadata);
    if (!isNode || !this.emitNodeEvents) {
      this._inFlightOuterChains.set(runId, {
        startTsMs: this._nowMs(),
        parentRunId,
      });
      return undefined;
    }

    const nodeName = extractNodeName(serialized, metadata);
    const toolCallId = runId;
    const event = await emitToolCallAttempted({
      toolCallId,
      toolName: nodeName,
      toolArgs: inputs ?? {},
      agentId: this.agentId,
      parentEventId: parentRunId,
      bufferPath: this.bufferPath,
    });
    this._inFlightNodes.set(runId, {
      startTsMs: this._nowMs(),
      toolName: nodeName,
      toolCallId,
      parentRunId,
    });
    this._emit(event);
    return event;
  }

  /**
   * Emit `tool_call_completed` if this run_id matches an in-flight
   * node; otherwise lifecycle no-op.
   */
  async handleChainEnd(
    outputs: unknown,
    runId: string,
    parentRunId?: string,
  ): Promise<Record<string, unknown> | undefined> {
    const ctx = this._inFlightNodes.get(runId);
    this._inFlightNodes.delete(runId);
    if (ctx === undefined) {
      this._inFlightOuterChains.delete(runId);
      return undefined;
    }
    const durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
    const event = await emitToolCallCompleted({
      toolCallId: ctx.toolCallId ?? runId,
      toolName: ctx.toolName ?? "unknown_node",
      toolResult: renderToolOutput(outputs),
      durationMs,
      agentId: this.agentId,
      parentEventId: parentRunId ?? ctx.parentRunId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /**
   * Emit `tool_call_failed` if this run_id matches an in-flight node;
   * otherwise lifecycle no-op.
   */
  async handleChainError(
    error: unknown,
    runId: string,
    parentRunId?: string,
  ): Promise<Record<string, unknown> | undefined> {
    const ctx = this._inFlightNodes.get(runId);
    this._inFlightNodes.delete(runId);
    if (ctx === undefined) {
      this._inFlightOuterChains.delete(runId);
      return undefined;
    }
    const durationMs = Math.max(0, this._nowMs() - ctx.startTsMs);
    const failureReason = renderErrorReason(error);
    const failureKind = this.classifyToolError(error);
    const event = await emitToolCallFailed({
      toolCallId: ctx.toolCallId ?? runId,
      toolName: ctx.toolName ?? "unknown_node",
      failureReason,
      durationMs,
      failureKind,
      agentId: this.agentId,
      parentEventId: parentRunId ?? ctx.parentRunId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  // ── LLM hooks ──────────────────────────────────────────────────

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

  async handleLLMError(
    _error: unknown,
    runId: string,
    _parentRunId?: string,
  ): Promise<void> {
    this._inFlightLlms.delete(runId);
  }

  // ── tool hooks ────────────────────────────────────────────────

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
    const event = await emitToolCallCompleted({
      toolCallId,
      toolName,
      toolResult: renderToolOutput(output),
      durationMs,
      agentId: this.agentId,
      parentEventId: parentRunId ?? ctx?.parentRunId,
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

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

  /** Return a structural callback for the LangChain.js `callbacks: []` slot. */
  asLangChainCallback(): Record<string, unknown> {
    return {
      name: "NanorixLangGraphCallback",
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

  /** Expose the on-event debug hook for fan-out. */
  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}
