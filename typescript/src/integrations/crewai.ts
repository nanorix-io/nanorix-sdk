/**
 * CrewAI structural callback — Tier A (proxy/library) integration.
 *
 * CrewAI is Python-primary; there is no first-party TypeScript runtime
 * as of mid-2026. This module provides a **framework-agnostic
 * structural callback class** that customers wire into any TypeScript
 * crew/task/agent orchestrator following CrewAI's lifecycle pattern
 * (task = unit of work for crew member, step = agent's reasoning/tool
 * loop). The callback emits the existing 4 ActivityEvent variants
 * shipped by ADR-021 — no new variants — Forever-Standard preserved
 * (ADR-006 I0).
 *
 * Customer pattern:
 *
 * ```ts
 * import { NanorixCrewAICallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixCrewAICallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 *
 * // Inside your custom TS crew orchestrator:
 * callback.taskCallback(taskOutput);     // after each task completion
 * callback.stepCallback(agentStep);      // at each agent reasoning step
 * callback.onTaskFailed(task, error);    // on task error (escape hatch)
 * ```
 *
 * Public lifecycle methods:
 *
 * | Public method      | ActivityEvent emitted                     | When                             |
 * |--------------------|-------------------------------------------|----------------------------------|
 * | `taskCallback`     | `tool_call_attempted` + `tool_call_completed` | Task returns successfully    |
 * | `stepCallback`     | `reasoning_step_completed`                | Agent decided action / finished  |
 * | `onTaskFailed`     | `tool_call_failed`                        | Task raised                      |
 * | `onStepError`      | `tool_call_failed`                        | Step-level error                 |
 *
 * Task structural shape:
 *
 * - **task start** → `tool_call_attempted` (task is structural unit
 *   the agent invoked; conceptually a tool in AuditProof sense).
 * - **task completed** → `tool_call_completed` (success).
 * - **task failed** → `tool_call_failed` (failure).
 *
 * Step structural shape:
 *
 * - **AgentAction step** → `reasoning_step_completed` (agent decided
 *   to invoke a tool; chain-of-thought + args + observation hashed).
 * - **AgentFinish step** → `reasoning_step_completed` (final answer).
 *
 * ## Three-role discipline (ADR-017)
 *
 * Structural adapter only. Does NOT certify CrewAI correctness, crew
 * orchestration safety, or LLM-output safety — those determinations
 * remain the customer's responsibility.
 *
 * ## Narrow signed claim
 *
 * Captures structural facts: task invoked at time T, with structural
 * identifiers. NEVER asserts crew correctness, task-output validity,
 * or regulatory-framework conformance.
 */

import {
  DEFAULT_BUFFER_PATH,
  TOOL_CALL_FAILURE_KINDS,
  type ReasoningStepKind,
  type ToolCallFailureKind,
  canonicalHash,
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

/** Options for constructing a `NanorixCrewAICallback`. */
export interface NanorixCrewAICallbackOptions {
  framework: string;
  capsulefile: string;
  apiKey?: string;
  metadata?: Record<string, unknown>;
  frameworkVersion?: string;
  onEvent?: (event: Record<string, unknown>) => void;
  bufferPath?: string;
  agentId?: string;
  /** Whether to emit task-shape events. Defaults to true. */
  emitTaskEvents?: boolean;
  /** Whether to emit step-shape reasoning events. Defaults to true. */
  emitStepEvents?: boolean;
}

/** Options for `onTaskFailed`. */
export interface OnTaskFailedOptions {
  durationMs?: number;
  agentId?: string;
}

/** Options for `onStepError`. */
export interface OnStepErrorOptions {
  agentId?: string;
}

/** CrewAI-shape structural callback for emitting Nanorix ActivityEvents. */
export class NanorixCrewAICallback {
  readonly framework: string;
  readonly capsulefile: string;
  readonly apiKey?: string;
  readonly metadata: Record<string, unknown>;
  readonly frameworkVersion?: string;
  readonly bufferPath: string;
  readonly agentId?: string;
  readonly emitTaskEvents: boolean;
  readonly emitStepEvents: boolean;

  private readonly _onEvent?: (event: Record<string, unknown>) => void;
  private _reasoningStepCounter = 0;

  constructor(opts: NanorixCrewAICallbackOptions) {
    if (!opts.framework || typeof opts.framework !== "string") {
      throw new Error(
        "NanorixCrewAICallback requires an explicit `framework` option " +
          "(e.g., framework: 'HIPAA'). No silent default per ADR-013 " +
          "customer-sole-declarer discipline.",
      );
    }
    if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
      throw new Error(
        "NanorixCrewAICallback requires an explicit `capsulefile` option.",
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
    this.emitTaskEvents = opts.emitTaskEvents !== false;
    this.emitStepEvents = opts.emitStepEvents !== false;
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

  // ── CrewAI-shape callback methods ──────────────────────────────

  /** Emit `tool_call_attempted` + `tool_call_completed` for a finished task. */
  async taskCallback(taskOutput: unknown): Promise<unknown> {
    if (!this.emitTaskEvents) return taskOutput;
    const taskId = extractTaskId(taskOutput);
    const taskName = extractTaskName(taskOutput);
    const taskArgs = renderTaskArgs(taskOutput);
    const taskResult = renderTaskResult(taskOutput);
    const durationMs = extractTaskDurationMs(taskOutput);
    const agentField =
      taskOutput !== null && typeof taskOutput === "object"
        ? (taskOutput as Record<string, unknown>).agent
        : undefined;
    const agentOverride =
      typeof agentField === "string" ? agentField : undefined;
    const resolvedAgentId = this._resolveAgentId(agentOverride);

    const attempt = await emitToolCallAttempted({
      toolCallId: taskId,
      toolName: taskName,
      toolArgs: taskArgs,
      agentId: resolvedAgentId,
      bufferPath: this.bufferPath,
    });
    this._emit(attempt);

    const completion = await emitToolCallCompleted({
      toolCallId: taskId,
      toolName: taskName,
      toolResult: taskResult,
      durationMs,
      agentId: resolvedAgentId,
      bufferPath: this.bufferPath,
    });
    this._emit(completion);

    return taskOutput;
  }

  /** Emit `reasoning_step_completed` for an agent step. */
  async stepCallback(step: unknown): Promise<unknown> {
    if (!this.emitStepEvents) return step;
    const rendered = renderStep(step);
    const log =
      rendered !== null && typeof rendered === "object"
        ? (rendered as Record<string, unknown>).log
        : undefined;
    const prompt: unknown = typeof log === "string" ? log : [];
    const response = rendered;
    let stepKind: ReasoningStepKind | undefined;
    if (rendered !== null && typeof rendered === "object") {
      const r = rendered as Record<string, unknown>;
      if (r.kind === "agent_finish") stepKind = "decide";
      else if (r.kind === "agent_action") stepKind = "reason";
    }
    const event = await emitReasoningStepCompleted({
      stepIdx: this._reasoningStepCounter,
      prompt,
      response,
      durationMs: 0,
      agentId: this._resolveAgentId(undefined),
      stepKind,
      bufferPath: this.bufferPath,
    });
    this._reasoningStepCounter += 1;
    this._emit(event);
    return step;
  }

  /** Emit `tool_call_failed` for a task that raised during execution. */
  async onTaskFailed(
    taskOrId: unknown,
    error: unknown,
    opts: OnTaskFailedOptions = {},
  ): Promise<Record<string, unknown>> {
    let taskId: string;
    let taskName: string;
    if (typeof taskOrId === "string") {
      taskId = taskOrId;
      taskName = "crewai_task";
    } else {
      taskId = extractTaskId(taskOrId);
      taskName = extractTaskName(taskOrId);
    }
    const failureReason = renderErrorReason(error);
    const failureKind = this.classifyToolError(error);
    const event = await emitToolCallFailed({
      toolCallId: taskId,
      toolName: taskName,
      failureReason,
      durationMs: opts.durationMs ?? 0,
      failureKind,
      agentId: this._resolveAgentId(opts.agentId),
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  /** Emit `tool_call_failed` for a step-level error. */
  async onStepError(
    stepOrId: unknown,
    error: unknown,
    opts: OnStepErrorOptions = {},
  ): Promise<Record<string, unknown>> {
    let stepId: string;
    let toolName: string;
    if (typeof stepOrId === "string") {
      stepId = stepOrId;
      toolName = "crewai_step";
    } else {
      const s =
        stepOrId !== null && typeof stepOrId === "object"
          ? (stepOrId as Record<string, unknown>)
          : ({} as Record<string, unknown>);
      const sTool = s.tool;
      toolName = typeof sTool === "string" && sTool ? sTool : "crewai_step";
      const sId = s.id;
      stepId =
        typeof sId === "string" && sId
          ? sId
          : `crewai_step:${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
    }
    const failureReason = renderErrorReason(error);
    const failureKind = this.classifyToolError(error);
    const event = await emitToolCallFailed({
      toolCallId: stepId,
      toolName,
      failureReason,
      durationMs: 0,
      failureKind,
      agentId: this._resolveAgentId(opts.agentId),
      bufferPath: this.bufferPath,
    });
    this._emit(event);
    return event;
  }

  get onEvent(): ((event: Record<string, unknown>) => void) | undefined {
    return this._onEvent;
  }
}

// ── CrewAI-shape extractors ─────────────────────────────────────────

/** Pull a stable task_id from a TaskOutput-shape or Task-shape value. */
export function extractTaskId(taskOrOutput: unknown): string {
  if (taskOrOutput === null || typeof taskOrOutput !== "object") {
    return `crewai_task:${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
  }
  const rec = taskOrOutput as Record<string, unknown>;
  for (const k of ["task_id", "id", "taskId"]) {
    const v = rec[k];
    if (typeof v === "string" && v) return v;
  }
  const task = rec.task;
  if (task !== null && task !== undefined && typeof task === "object") {
    const t = task as Record<string, unknown>;
    for (const k of ["id", "task_id", "taskId"]) {
      const v = t[k];
      if (typeof v === "string" && v) return v;
    }
  }
  const desc = rec.description;
  if (typeof desc === "string" && desc) {
    return `crewai_task:${canonicalHash(desc).slice(7, 23)}`;
  }
  return `crewai_task:${Date.now()}-${Math.floor(Math.random() * 1_000_000)}`;
}

/** Pull a human-readable task name. */
export function extractTaskName(taskOrOutput: unknown): string {
  if (taskOrOutput === null || typeof taskOrOutput !== "object") {
    return "crewai_task";
  }
  const rec = taskOrOutput as Record<string, unknown>;
  for (const k of ["name", "description"]) {
    const v = rec[k];
    if (typeof v === "string" && v) return v.slice(0, 80);
  }
  const task = rec.task;
  if (task !== null && task !== undefined && typeof task === "object") {
    const t = task as Record<string, unknown>;
    for (const k of ["name", "description"]) {
      const v = t[k];
      if (typeof v === "string" && v) return v.slice(0, 80);
    }
  }
  return "crewai_task";
}

/** Pull execution duration (milliseconds) from a TaskOutput. */
export function extractTaskDurationMs(taskOutput: unknown): number {
  if (taskOutput === null || typeof taskOutput !== "object") return 0;
  const rec = taskOutput as Record<string, unknown>;
  const execTime = rec.execution_time ?? rec.executionTime;
  if (typeof execTime === "number" && execTime >= 0) {
    // Per Python: seconds float → ms int; integer → assumed already ms
    if (Number.isInteger(execTime)) return execTime;
    return Math.floor(execTime * 1000);
  }
  const dur = rec.duration;
  if (typeof dur === "number" && dur >= 0) return Math.floor(dur);
  const durMs = rec.durationMs;
  if (typeof durMs === "number" && durMs >= 0) return Math.floor(durMs);
  return 0;
}

/** Render task inputs as `tool_args` for canonical hashing. */
export function renderTaskArgs(taskOrOutput: unknown): Record<string, unknown> {
  if (taskOrOutput === null || typeof taskOrOutput !== "object") return {};
  const rec = taskOrOutput as Record<string, unknown>;
  const task = rec.task !== undefined ? (rec.task as Record<string, unknown>) : rec;
  const rendered: Record<string, unknown> = {};
  for (const attr of ["description", "expected_output", "expectedOutput"]) {
    const v = (task as Record<string, unknown>)[attr];
    if (typeof v === "string") {
      rendered[attr === "expectedOutput" ? "expected_output" : attr] = v;
    }
  }
  const inputs =
    (task as Record<string, unknown>).inputs ??
    (task as Record<string, unknown>).context;
  if (inputs !== undefined && inputs !== null) {
    rendered.inputs = sanitizeValue(inputs);
  }
  return rendered;
}

/** Render a TaskOutput as `tool_result` for canonical hashing. */
export function renderTaskResult(taskOutput: unknown): unknown {
  if (taskOutput === null || taskOutput === undefined) return null;
  if (
    typeof taskOutput === "string" ||
    typeof taskOutput === "number" ||
    typeof taskOutput === "boolean" ||
    Array.isArray(taskOutput)
  ) {
    return taskOutput;
  }
  if (typeof taskOutput !== "object") return String(taskOutput);
  const rec = taskOutput as Record<string, unknown>;
  const rendered: Record<string, unknown> = {};
  if (typeof rec.raw === "string") rendered.raw = rec.raw;
  const pyd = rec.pydantic;
  if (pyd !== null && pyd !== undefined) {
    if (typeof pyd === "object") rendered.pydantic = pyd;
    else rendered.pydantic = String(pyd);
  }
  const jsonDict = rec.json_dict ?? rec.jsonDict;
  if (
    jsonDict !== null &&
    jsonDict !== undefined &&
    typeof jsonDict === "object" &&
    !Array.isArray(jsonDict)
  ) {
    rendered.json_dict = jsonDict;
  }
  if (typeof rec.agent === "string") rendered.agent = rec.agent;
  if (Object.keys(rendered).length === 0) return String(taskOutput);
  return rendered;
}

/** Render an AgentAction or AgentFinish step for canonical hashing. */
export function renderStep(step: unknown): Record<string, unknown> {
  if (step === null || step === undefined) return { kind: "agent_action" };
  if (typeof step !== "object") {
    return { kind: "agent_action", text: String(step) };
  }
  const rec = step as Record<string, unknown>;
  // AgentFinish detection
  const returnValues = rec.return_values ?? rec.returnValues;
  if (returnValues !== undefined && returnValues !== null) {
    const out: Record<string, unknown> = { kind: "agent_finish" };
    if (typeof returnValues === "object") {
      out.return_values = returnValues;
    }
    if (typeof rec.log === "string") out.log = rec.log;
    return out;
  }
  // AgentAction
  const out: Record<string, unknown> = { kind: "agent_action" };
  for (const attr of ["tool", "tool_input", "toolInput", "log", "text"]) {
    const v = rec[attr];
    if (
      typeof v === "string" ||
      typeof v === "number" ||
      typeof v === "boolean" ||
      Array.isArray(v) ||
      (v !== null && typeof v === "object")
    ) {
      const k = attr === "toolInput" ? "tool_input" : attr;
      if (out[k] === undefined) out[k] = v;
    }
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
  if (value instanceof Set) return Array.from(value).map(sanitizeValue);
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
