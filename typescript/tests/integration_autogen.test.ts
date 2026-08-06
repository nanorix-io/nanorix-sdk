/**
 * AutoGen structural-callback adapter tests — Batch 7 sub-item 3.
 *
 * Validates 4-method lifecycle dispatch, OpenAI tool_calls + Anthropic
 * content-block shape extraction, and parent_event_id pairing.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  NanorixAutoGenCallback,
  extractToolCalls as autogenExtractToolCalls,
  extractToolCallId as autogenExtractToolCallId,
  extractAutogenToolName,
  extractAutogenToolArgs,
} from "../src/integrations/autogen.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-autogen-${randomBytes(8).toString("hex")}`);
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

describe("NanorixAutoGenCallback — constructor", () => {
  it("requires framework + capsulefile", () => {
    expect(
      () =>
        new NanorixAutoGenCallback({
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixAutoGenCallback({
          framework: "HIPAA",
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });
});

describe("NanorixAutoGenCallback — tool_calls extraction", () => {
  it("extracts OpenAI tool_calls from dict-shape message", () => {
    const tcs = autogenExtractToolCalls({
      role: "assistant",
      content: "",
      tool_calls: [
        {
          id: "call_X",
          type: "function",
          function: { name: "submit", arguments: '{"x":1}' },
        },
      ],
    });
    expect(tcs).toHaveLength(1);
    expect(extractAutogenToolName(tcs[0])).toEqual("submit");
    expect(extractAutogenToolArgs(tcs[0])).toEqual('{"x":1}');
    expect(autogenExtractToolCallId(tcs[0])).toEqual("call_X");
  });

  it("extracts Anthropic content-block tool_use shape", () => {
    const tcs = autogenExtractToolCalls({
      role: "assistant",
      content: [
        { type: "text", text: "thinking..." },
        {
          type: "tool_use",
          id: "toolu_abc",
          name: "lookup",
          input: { q: "foo" },
        },
      ],
    });
    expect(tcs).toHaveLength(1);
    expect(extractAutogenToolName(tcs[0])).toEqual("lookup");
    expect(extractAutogenToolArgs(tcs[0])).toEqual({ q: "foo" });
    expect(autogenExtractToolCallId(tcs[0])).toEqual("toolu_abc");
  });

  it("returns empty list for messages without tool_calls", () => {
    expect(
      autogenExtractToolCalls({ role: "assistant", content: "hi" }),
    ).toEqual([]);
    expect(autogenExtractToolCalls(null)).toEqual([]);
    expect(autogenExtractToolCalls("text-only")).toEqual([]);
  });
});

describe("NanorixAutoGenCallback — onAgentMessageReceived", () => {
  it("emits tool_call_attempted per tool_call entry", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const ids = await cb.onAgentMessageReceived({
      role: "assistant",
      tool_calls: [
        {
          id: "call_1",
          type: "function",
          function: { name: "first", arguments: "{}" },
        },
        {
          id: "call_2",
          type: "function",
          function: { name: "second", arguments: '{"x":1}' },
        },
      ],
    });
    expect(ids).toEqual(["call_1", "call_2"]);
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[0].tool_name).toEqual("first");
    expect(buf[1].tool_name).toEqual("second");
  });

  it("returns empty array + writes nothing when no tool_calls", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const ids = await cb.onAgentMessageReceived({
      role: "assistant",
      content: "no tools here",
    });
    expect(ids).toEqual([]);
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixAutoGenCallback — onAgentMessageSent", () => {
  it("emits reasoning_step_completed with model + duration", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onAgentMessageSent(
      { role: "assistant", content: "decision: approve" },
      { model: "gpt-4o", durationMs: 1500, agentId: "supervisor" },
    );
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    expect(buf[0].model).toEqual("gpt-4o");
    expect(buf[0].duration_ms).toEqual(1500);
    expect(buf[0].agent_id).toEqual("supervisor");
  });

  it("step_idx increments per call", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onAgentMessageSent({ role: "assistant", content: "1" });
    await cb.onAgentMessageSent({ role: "assistant", content: "2" });
    await cb.onAgentMessageSent({ role: "assistant", content: "3" });
    const buf = await readBuffer();
    expect(buf[0].step_idx).toEqual(0);
    expect(buf[1].step_idx).toEqual(1);
    expect(buf[2].step_idx).toEqual(2);
  });
});

describe("NanorixAutoGenCallback — onToolCallCompleted", () => {
  it("emits tool_call_completed on success", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onAgentMessageReceived({
      tool_calls: [
        { id: "tc-X", type: "function", function: { name: "calc", arguments: "{}" } },
      ],
    });
    await cb.onToolCallCompleted("tc-X", { toolResult: { sum: 42 } });
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[1].event).toEqual("tool_call_completed");
    expect(buf[1].tool_call_id).toEqual("tc-X");
  });

  it("emits tool_call_failed on error", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onAgentMessageReceived({
      tool_calls: [
        { id: "tc-fail", type: "function", function: { name: "boom", arguments: "{}" } },
      ],
    });
    class TimeoutError extends Error {
      constructor() {
        super("exceeded 30s");
      }
    }
    Object.defineProperty(TimeoutError.prototype.constructor, "name", {
      value: "TimeoutError",
    });
    const err = new TimeoutError();
    Object.defineProperty(err.constructor, "name", { value: "TimeoutError" });
    await cb.onToolCallCompleted("tc-fail", { error: err });
    const buf = await readBuffer();
    expect(buf[1].event).toEqual("tool_call_failed");
    expect((buf[1].failure_kind as Record<string, unknown>)?.type).toEqual(
      "timeout",
    );
  });
});

describe("NanorixAutoGenCallback — onAgentError", () => {
  it("emits tool_call_failed when in_flight_tool_call_id matches", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onAgentMessageReceived({
      tool_calls: [
        { id: "tc-Z", type: "function", function: { name: "x", arguments: "{}" } },
      ],
    });
    await cb.onAgentError(new Error("agent crashed"), {
      inFlightToolCallId: "tc-Z",
    });
    const buf = await readBuffer();
    expect(buf[1].event).toEqual("tool_call_failed");
  });

  it("emits nothing when no in_flight_tool_call_id provided", async () => {
    const cb = new NanorixAutoGenCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const ev = await cb.onAgentError(new Error("orphan"));
    expect(ev).toBeUndefined();
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});
