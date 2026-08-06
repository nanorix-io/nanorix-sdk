/**
 * Cap-3 (Phase 1) — ActivityEvent emission helpers tests.
 *
 * Validates wire-shape invariants, hash determinism + JCS-flavor
 * canonical-JSON, closed-enum validation, sometimes-populated reserved
 * slots, buffer-write atomicity, and Python<->TypeScript byte-equivalence.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash, randomBytes } from "node:crypto";

import {
  DEFAULT_BUFFER_PATH,
  REASONING_STEP_KINDS,
  TOOL_CALL_FAILURE_KINDS,
  canonicalHash,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
  emitToolCallCompleted,
  emitToolCallFailed,
} from "../src/helpers/activity_events.js";


// ── Test buffer fixture ─────────────────────────────────────────


let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-cap3-${randomBytes(8).toString("hex")}`);
  await fs.mkdir(testDir, { recursive: true });
  bufferPath = join(testDir, "events.jsonl");
});

afterEach(async () => {
  try {
    await fs.rm(testDir, { recursive: true, force: true });
  } catch {
    // ignore
  }
});

async function readBuffer(): Promise<Record<string, unknown>[]> {
  const data = await fs.readFile(bufferPath, "utf-8");
  return data.trim().split("\n").map((line) => JSON.parse(line));
}


// ── Canonical hash determinism (cross-impl invariant) ──────────


describe("canonicalHash", () => {
  it("is deterministic across dict key orders", () => {
    const a = { z: 1, a: 2, m: [3, 4] };
    const b = { a: 2, m: [3, 4], z: 1 };
    expect(canonicalHash(a)).toEqual(canonicalHash(b));
  });

  it("starts with sha512: prefix and is 128 hex chars", () => {
    const h = canonicalHash({ k: "v" });
    expect(h.startsWith("sha512:")).toBe(true);
    expect(h.length).toEqual("sha512:".length + 128);
  });

  it("matches known JCS-flavor canonical bytes for cross-impl invariant", () => {
    // The Rust runtime + Python helper both hash via this exact byte format.
    const payload = { a: 1, b: "hello" };
    const expectedBytes = '{"a":1,"b":"hello"}';
    const expected = "sha512:" + createHash("sha512").update(expectedBytes, "utf8").digest("hex");
    expect(canonicalHash(payload)).toEqual(expected);
  });

  it("produces byte-equivalent output to Python helper for sorted-key canonical JSON", () => {
    // Mirror of Python `_canonical_hash` test case (cross-impl validation).
    const payload = { b: 2, a: 1 };
    const expectedBytes = '{"a":1,"b":2}';
    const expected = "sha512:" + createHash("sha512").update(expectedBytes, "utf8").digest("hex");
    expect(canonicalHash(payload)).toEqual(expected);
  });
});


// ── tool_call_attempted ───────────────────────────────────────


describe("emitToolCallAttempted", () => {
  it("writes minimal event with hashed args", async () => {
    const event = await emitToolCallAttempted({
      toolCallId: "call_1",
      toolName: "submit_pa_request",
      toolArgs: { patient_id: "X" },
      bufferPath,
    });
    expect(event.event).toEqual("tool_call_attempted");
    expect(event.tool_call_id).toEqual("call_1");
    expect(event.tool_name).toEqual("submit_pa_request");
    expect(typeof event.tool_args_hash).toEqual("string");
    expect((event.tool_args_hash as string).startsWith("sha512:")).toBe(true);
    expect(event.at).toBeDefined();
    // Reserved slots omitted by default.
    expect(event.agent_id).toBeUndefined();
    expect(event.parent_event_id).toBeUndefined();

    const lines = await readBuffer();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toEqual(event);
  });

  it("includes reserved slots when supplied", async () => {
    const event = await emitToolCallAttempted({
      toolCallId: "call_2",
      toolName: "search",
      toolArgs: { q: "active" },
      agentId: "planner",
      parentEventId: "evt_root",
      bufferPath,
    });
    expect(event.agent_id).toEqual("planner");
    expect(event.parent_event_id).toEqual("evt_root");
  });

  it("zero-retention: plaintext args do not appear in serialized event", async () => {
    const secret = { ssn: "123-45-6789", patient_name: "Jane Doe" };
    const event = await emitToolCallAttempted({
      toolCallId: "call_x",
      toolName: "lookup",
      toolArgs: secret,
      bufferPath,
    });
    const serialized = JSON.stringify(event);
    expect(serialized).not.toContain("123-45-6789");
    expect(serialized).not.toContain("Jane Doe");
    expect(event.tool_args_hash).toEqual(canonicalHash(secret));
  });
});


// ── tool_call_completed ────────────────────────────────────────


describe("emitToolCallCompleted", () => {
  it("writes event with hashed result", async () => {
    const event = await emitToolCallCompleted({
      toolCallId: "call_1",
      toolName: "submit",
      toolResult: { submission_id: "PA-001" },
      durationMs: 1200,
      bufferPath,
    });
    expect(event.event).toEqual("tool_call_completed");
    expect((event.tool_result_hash as string).startsWith("sha512:")).toBe(true);
    expect(event.duration_ms).toEqual(1200);
  });

  it("rejects negative durationMs", async () => {
    await expect(
      emitToolCallCompleted({
        toolCallId: "x",
        toolName: "t",
        toolResult: {},
        durationMs: -1,
        bufferPath,
      }),
    ).rejects.toThrow(/durationMs/);
  });
});


// ── tool_call_failed ───────────────────────────────────────────


describe("emitToolCallFailed", () => {
  it("writes event with free-form failure_reason", async () => {
    const event = await emitToolCallFailed({
      toolCallId: "call_1",
      toolName: "upstream_api",
      failureReason: "upstream returned 503",
      durationMs: 500,
      bufferPath,
    });
    expect(event.event).toEqual("tool_call_failed");
    expect(event.failure_reason).toEqual("upstream returned 503");
    expect(event.failure_kind).toBeUndefined();
  });

  it("includes closed-enum failure_kind tagged shape when supplied", async () => {
    const event = await emitToolCallFailed({
      toolCallId: "call_1",
      toolName: "upstream_api",
      failureReason: "rate limited",
      durationMs: 10,
      failureKind: "upstream_failure",
      bufferPath,
    });
    expect(event.failure_kind).toEqual({ type: "upstream_failure" });
  });

  it("rejects unknown failure_kind", async () => {
    await expect(
      emitToolCallFailed({
        toolCallId: "x",
        toolName: "t",
        failureReason: "?",
        durationMs: 1,
        // @ts-expect-error: deliberate runtime check
        failureKind: "not_a_real_kind",
        bufferPath,
      }),
    ).rejects.toThrow(/closed-enum/);
  });

  it("accepts every closed-enum failure_kind value", async () => {
    for (const kind of TOOL_CALL_FAILURE_KINDS) {
      const event = await emitToolCallFailed({
        toolCallId: `c_${kind}`,
        toolName: "t",
        failureReason: "?",
        durationMs: 1,
        failureKind: kind,
        bufferPath,
      });
      expect(event.failure_kind).toEqual({ type: kind });
    }
  });
});


// ── reasoning_step_completed ─────────────────────────────────


describe("emitReasoningStepCompleted", () => {
  it("writes minimal event", async () => {
    const event = await emitReasoningStepCompleted({
      stepIdx: 0,
      prompt: { user: "What's PA criteria?" },
      response: { text: "Here..." },
      durationMs: 2400,
      bufferPath,
    });
    expect(event.event).toEqual("reasoning_step_completed");
    expect(event.step_idx).toEqual(0);
    expect((event.prompt_hash as string).startsWith("sha512:")).toBe(true);
    expect((event.response_hash as string).startsWith("sha512:")).toBe(true);
    expect(event.model).toBeUndefined();
    expect(event.step_kind).toBeUndefined();
    expect(event.summary_hash).toBeUndefined();
  });

  it("includes all reserved slots when supplied", async () => {
    const event = await emitReasoningStepCompleted({
      stepIdx: 3,
      prompt: { q: "Decide" },
      response: { a: "Approve" },
      durationMs: 1800,
      model: "claude-opus-4-7",
      agentId: "decider",
      stepKind: "decide",
      summary: { decision: "approve", rationale: "criteria met" },
      parentEventId: "evt_synthesize_2",
      bufferPath,
    });
    expect(event.model).toEqual("claude-opus-4-7");
    expect(event.agent_id).toEqual("decider");
    expect(event.step_kind).toEqual({ type: "decide" });
    expect((event.summary_hash as string).startsWith("sha512:")).toBe(true);
    expect(event.parent_event_id).toEqual("evt_synthesize_2");
  });

  it("rejects unknown stepKind", async () => {
    await expect(
      emitReasoningStepCompleted({
        stepIdx: 0,
        prompt: { q: "?" },
        response: { a: "?" },
        durationMs: 1,
        // @ts-expect-error: deliberate runtime check
        stepKind: "not_a_real_kind",
        bufferPath,
      }),
    ).rejects.toThrow(/closed-enum/);
  });

  it("accepts every closed-enum reasoning_step_kind value", async () => {
    for (const kind of REASONING_STEP_KINDS) {
      const event = await emitReasoningStepCompleted({
        stepIdx: 0,
        prompt: { q: "?" },
        response: { a: "?" },
        durationMs: 1,
        stepKind: kind,
        bufferPath,
      });
      expect(event.step_kind).toEqual({ type: kind });
    }
  });

  it("rejects negative stepIdx", async () => {
    await expect(
      emitReasoningStepCompleted({
        stepIdx: -1,
        prompt: {},
        response: {},
        durationMs: 1,
        bufferPath,
      }),
    ).rejects.toThrow(/stepIdx/);
  });
});


// ── Multi-event sequence ────────────────────────────────────


describe("multi-event sequence", () => {
  it("appends events in chronological order", async () => {
    await emitReasoningStepCompleted({
      stepIdx: 0,
      prompt: { q: "Plan" },
      response: { a: "Use tool X" },
      durationMs: 100,
      stepKind: "plan",
      bufferPath,
    });
    await emitToolCallAttempted({
      toolCallId: "t1",
      toolName: "X",
      toolArgs: { k: "v" },
      bufferPath,
    });
    await emitToolCallCompleted({
      toolCallId: "t1",
      toolName: "X",
      toolResult: { ok: true },
      durationMs: 50,
      bufferPath,
    });
    await emitReasoningStepCompleted({
      stepIdx: 1,
      prompt: { q: "Decide" },
      response: { a: "Done" },
      durationMs: 80,
      stepKind: "decide",
      bufferPath,
    });

    const lines = await readBuffer();
    expect(lines).toHaveLength(4);
    expect(lines.map((l) => l.event)).toEqual([
      "reasoning_step_completed",
      "tool_call_attempted",
      "tool_call_completed",
      "reasoning_step_completed",
    ]);
  });

  it("creates parent directory when missing", async () => {
    const deepPath = join(testDir, "subdir", "deep", "events.jsonl");
    await emitToolCallAttempted({
      toolCallId: "x",
      toolName: "t",
      toolArgs: {},
      bufferPath: deepPath,
    });
    const stat = await fs.stat(deepPath);
    expect(stat.isFile()).toBe(true);
  });
});


// ── Defaults / constants ────────────────────────────────────


describe("constants", () => {
  it("DEFAULT_BUFFER_PATH matches documented capsule mount", () => {
    expect(DEFAULT_BUFFER_PATH).toEqual("/data/activity_events.jsonl");
  });

  it("TOOL_CALL_FAILURE_KINDS matches Rust enum variants", () => {
    expect([...TOOL_CALL_FAILURE_KINDS].sort()).toEqual([
      "cancelled",
      "invalid_args",
      "permission_denied",
      "reserved",
      "timeout",
      "upstream_failure",
    ]);
  });

  it("REASONING_STEP_KINDS matches Rust enum variants", () => {
    expect([...REASONING_STEP_KINDS].sort()).toEqual([
      "decide",
      "plan",
      "reason",
      "reserved",
      "synthesize",
    ]);
  });
});
