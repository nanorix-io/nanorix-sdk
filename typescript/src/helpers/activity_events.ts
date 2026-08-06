/**
 * Customer-side ActivityEvent emission helpers — Cap-3 (Phase 1).
 *
 * Mirrors `sdk/python/src/nanorix/helpers/activity_events.py`
 * byte-identically: same event JSON shape, same JCS-flavor canonical-
 * JSON for hashing, same closed-enum sets, same buffer protocol.
 *
 * ADR-021 (Accepted 2026-05-06) shipped 4 ActivityEvent variants for
 * AI-agent tooling — `ToolCallAttempted`, `ToolCallCompleted`,
 * `ToolCallFailed`, `ReasoningStepCompleted` — implemented at
 * `services/api/src/cdp_document.rs:1561-1752`. Until Cap-3, customers
 * had NO SDK helper to construct + emit these events from inside
 * their capsule code. This module closes the gap.
 *
 * ## Trust-boundary discipline
 *
 * - **Customer-sole-declarer (ADR-013).** Helpers do NOT classify,
 *   infer, or augment.
 * - **Zero-retention (ADR-006 I0).** All payload fields are SHA-512
 *   hashed; plaintext NEVER enters the AuditProof.
 * - **Forever-Standard (ADR-006 I0).** Field names + JSON tag
 *   (`event`) + snake_case keys + closed-enum-tagged shapes
 *   (`{"type": "..."}`) mirror the Rust serde shape verbatim.
 *
 * ## Cap-3 boss directive (2026-05-08)
 *
 * > "the helper code shipping inside the existing not-yet-published
 * > SDK package is fine. The publish event remains gated separately."
 *
 * Helpers ship in the SDK source tree but the npm publish event
 * remains gated per `feedback_consumer_surface_after_contract.md`.
 *
 * @example
 * ```ts
 * // Customer's capsule entrypoint (runs inside Nanorix sealed capsule)
 * import {
 *   emitToolCallAttempted,
 *   emitToolCallCompleted,
 *   emitReasoningStepCompleted,
 * } from "@nanorix/sdk/helpers";
 *
 * await emitReasoningStepCompleted({
 *   stepIdx: 0,
 *   prompt: { user: "Decide" },
 *   response: { text: "Approve" },
 *   durationMs: 2400,
 *   stepKind: "decide",
 * });
 *
 * await emitToolCallAttempted({
 *   toolCallId: "call_abc",
 *   toolName: "submit",
 *   toolArgs: { id: "X" },
 * });
 *
 * await emitToolCallCompleted({
 *   toolCallId: "call_abc",
 *   toolName: "submit",
 *   toolResult: { ok: true },
 *   durationMs: 50,
 * });
 * ```
 */

import { promises as fs } from "node:fs";
import { dirname } from "node:path";
import { createHash } from "node:crypto";

/** Default activity-trail buffer path inside a Nanorix capsule.
 *  The daemon mounts `/data` as tmpfs and reads `activity_events.jsonl`
 *  at destroy. */
export const DEFAULT_BUFFER_PATH = "/data/activity_events.jsonl";

/** Closed-enum tool-call failure kinds.
 *
 *  Mirrors `ToolCallFailureKind` at
 *  `services/api/src/cdp_document.rs:97`. ADR-028 D1.
 */
export const TOOL_CALL_FAILURE_KINDS = [
  "timeout",
  "permission_denied",
  "invalid_args",
  "upstream_failure",
  "cancelled",
  "reserved",
] as const;
export type ToolCallFailureKind = (typeof TOOL_CALL_FAILURE_KINDS)[number];

/** Closed-enum reasoning step kinds.
 *
 *  Mirrors `ReasoningStepKind` at
 *  `services/api/src/cdp_document.rs:146`. ADR-028 D1.
 */
export const REASONING_STEP_KINDS = [
  "plan",
  "reason",
  "synthesize",
  "decide",
  "reserved",
] as const;
export type ReasoningStepKind = (typeof REASONING_STEP_KINDS)[number];


// ── Canonical hash (cross-impl invariant) ─────────────────────────


/** Sort an object's keys recursively for canonical-JSON serialization.
 *  JCS-flavor (sort_keys=True, no whitespace) — matches Python helper
 *  `_canonical_hash` and Rust serde_json canonical output. */
function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value !== null && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const sorted: Record<string, unknown> = {};
    for (const key of Object.keys(obj).sort()) {
      sorted[key] = canonicalize(obj[key]);
    }
    return sorted;
  }
  return value;
}

/** Compute SHA-512 hex (with `sha512:` prefix) of a canonical-JSON-
 *  serialized payload. Cross-impl invariant: byte-identical output to
 *  the Python helper's `_canonical_hash` and the Rust runtime's
 *  serde_json canonical hashing. */
export function canonicalHash(payload: unknown): string {
  const canonical = canonicalize(payload);
  const json = JSON.stringify(canonical);
  const hex = createHash("sha512").update(json, "utf8").digest("hex");
  return `sha512:${hex}`;
}


// ── Internal emit (file append) ──────────────────────────────────


function nowIso8601(): string {
  return new Date().toISOString();
}

async function emit(event: Record<string, unknown>, bufferPath: string): Promise<void> {
  const line = JSON.stringify(event) + "\n";
  // Ensure parent dir exists. For default `/data/activity_events.jsonl`,
  // /data is the capsule's tmpfs root — already present.
  const parent = dirname(bufferPath);
  await fs.mkdir(parent, { recursive: true });
  await fs.appendFile(bufferPath, line, { encoding: "utf-8" });
}


// ── Public types ─────────────────────────────────────────────────


export interface EmitToolCallAttemptedArgs {
  toolCallId: string;
  toolName: string;
  toolArgs: unknown;
  at?: string;
  agentId?: string;
  parentEventId?: string;
  bufferPath?: string;
}

export interface EmitToolCallCompletedArgs {
  toolCallId: string;
  toolName: string;
  toolResult: unknown;
  durationMs: number;
  at?: string;
  agentId?: string;
  parentEventId?: string;
  bufferPath?: string;
}

export interface EmitToolCallFailedArgs {
  toolCallId: string;
  toolName: string;
  failureReason: string;
  durationMs: number;
  failureKind?: ToolCallFailureKind;
  at?: string;
  agentId?: string;
  parentEventId?: string;
  bufferPath?: string;
}

export interface EmitReasoningStepCompletedArgs {
  stepIdx: number;
  prompt: unknown;
  response: unknown;
  durationMs: number;
  at?: string;
  model?: string;
  agentId?: string;
  stepKind?: ReasoningStepKind;
  summary?: unknown;
  parentEventId?: string;
  bufferPath?: string;
}


// ── Public emit_* helpers ────────────────────────────────────────


/** Emit a `tool_call_attempted` ActivityEvent.
 *
 *  Customer-declared per ADR-021. Pairs with a subsequent
 *  `tool_call_completed` (success) or `tool_call_failed` (failure)
 *  via `toolCallId`.
 *
 *  `toolArgs` is hashed (SHA-512 hex with `sha512:` prefix); plaintext
 *  arguments NEVER enter the AuditProof (zero-retention discipline).
 */
export async function emitToolCallAttempted(
  args: EmitToolCallAttemptedArgs,
): Promise<Record<string, unknown>> {
  const event: Record<string, unknown> = {
    event: "tool_call_attempted",
    tool_call_id: args.toolCallId,
    tool_name: args.toolName,
    tool_args_hash: canonicalHash(args.toolArgs),
    at: args.at ?? nowIso8601(),
  };
  if (args.agentId !== undefined) event.agent_id = args.agentId;
  if (args.parentEventId !== undefined) event.parent_event_id = args.parentEventId;
  await emit(event, args.bufferPath ?? DEFAULT_BUFFER_PATH);
  return event;
}

/** Emit a `tool_call_completed` ActivityEvent (success path).
 *
 *  Pairs with a prior `tool_call_attempted` via `toolCallId`. Customer's
 *  agent code emits exactly one of `tool_call_completed` OR
 *  `tool_call_failed` per attempted call.
 *
 *  `toolResult` is hashed (SHA-512 hex); plaintext NEVER enters
 *  AuditProof.
 *
 *  @throws RangeError if `durationMs` is negative.
 */
export async function emitToolCallCompleted(
  args: EmitToolCallCompletedArgs,
): Promise<Record<string, unknown>> {
  if (args.durationMs < 0) {
    throw new RangeError(`durationMs must be non-negative; got ${args.durationMs}`);
  }
  const event: Record<string, unknown> = {
    event: "tool_call_completed",
    tool_call_id: args.toolCallId,
    tool_name: args.toolName,
    tool_result_hash: canonicalHash(args.toolResult),
    duration_ms: args.durationMs,
    at: args.at ?? nowIso8601(),
  };
  if (args.agentId !== undefined) event.agent_id = args.agentId;
  if (args.parentEventId !== undefined) event.parent_event_id = args.parentEventId;
  await emit(event, args.bufferPath ?? DEFAULT_BUFFER_PATH);
  return event;
}

/** Emit a `tool_call_failed` ActivityEvent (failure path).
 *
 *  Pairs with a prior `tool_call_attempted` via `toolCallId`.
 *
 *  `failureKind` is the optional closed-enum reduction (ADR-028 D1);
 *  `failureReason` is the customer's free-form narrative.
 *
 *  @throws RangeError if `durationMs` is negative.
 *  @throws Error if `failureKind` is supplied but not in the closed-enum set.
 */
export async function emitToolCallFailed(
  args: EmitToolCallFailedArgs,
): Promise<Record<string, unknown>> {
  if (args.durationMs < 0) {
    throw new RangeError(`durationMs must be non-negative; got ${args.durationMs}`);
  }
  if (
    args.failureKind !== undefined &&
    !TOOL_CALL_FAILURE_KINDS.includes(args.failureKind)
  ) {
    throw new Error(
      `failureKind '${args.failureKind}' is not in the closed-enum set; ` +
        `allowed: ${TOOL_CALL_FAILURE_KINDS.join(", ")}`,
    );
  }
  const event: Record<string, unknown> = {
    event: "tool_call_failed",
    tool_call_id: args.toolCallId,
    tool_name: args.toolName,
    failure_reason: args.failureReason,
    duration_ms: args.durationMs,
    at: args.at ?? nowIso8601(),
  };
  if (args.agentId !== undefined) event.agent_id = args.agentId;
  if (args.parentEventId !== undefined) event.parent_event_id = args.parentEventId;
  if (args.failureKind !== undefined) {
    // Closed-enum-tagged JSON shape mirrors Rust serde's
    // `#[serde(tag = "type", rename_all = "snake_case")]` output.
    event.failure_kind = { type: args.failureKind };
  }
  await emit(event, args.bufferPath ?? DEFAULT_BUFFER_PATH);
  return event;
}

/** Emit a `reasoning_step_completed` ActivityEvent.
 *
 *  Customer's agent code emits this after each LLM-resolution step.
 *  Forms a chronological reasoning trail bound into the AuditProof's
 *  canonical_hash via activity_trail.
 *
 *  `prompt` and `response` are hashed (SHA-512 hex); plaintext NEVER
 *  enters AuditProof. `summary` (ADR-028 D3, if supplied) is also
 *  hashed.
 *
 *  @throws RangeError if `stepIdx` or `durationMs` is negative.
 *  @throws Error if `stepKind` is supplied but not in the closed-enum set.
 */
export async function emitReasoningStepCompleted(
  args: EmitReasoningStepCompletedArgs,
): Promise<Record<string, unknown>> {
  if (args.stepIdx < 0) {
    throw new RangeError(`stepIdx must be non-negative; got ${args.stepIdx}`);
  }
  if (args.durationMs < 0) {
    throw new RangeError(`durationMs must be non-negative; got ${args.durationMs}`);
  }
  if (
    args.stepKind !== undefined &&
    !REASONING_STEP_KINDS.includes(args.stepKind)
  ) {
    throw new Error(
      `stepKind '${args.stepKind}' is not in the closed-enum set; ` +
        `allowed: ${REASONING_STEP_KINDS.join(", ")}`,
    );
  }
  const event: Record<string, unknown> = {
    event: "reasoning_step_completed",
    step_idx: args.stepIdx,
    prompt_hash: canonicalHash(args.prompt),
    response_hash: canonicalHash(args.response),
    duration_ms: args.durationMs,
    at: args.at ?? nowIso8601(),
  };
  if (args.model !== undefined) event.model = args.model;
  if (args.agentId !== undefined) event.agent_id = args.agentId;
  if (args.stepKind !== undefined) event.step_kind = { type: args.stepKind };
  if (args.summary !== undefined) event.summary_hash = canonicalHash(args.summary);
  if (args.parentEventId !== undefined) event.parent_event_id = args.parentEventId;
  await emit(event, args.bufferPath ?? DEFAULT_BUFFER_PATH);
  return event;
}
