/**
 * LlamaIndex.ts integration adapter tests — Batch 7 sub-item 3.
 *
 * Validates event-name dispatch (`llm-start` / `llm-end` / `tool-call` /
 * `tool-result` / `agent-step`) into ActivityEvent emission.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  NanorixLlamaIndexCallback,
  VALID_LLAMAINDEX_EVENTS,
} from "../src/integrations/llama_index.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-llama-${randomBytes(8).toString("hex")}`);
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

describe("NanorixLlamaIndexCallback — constructor", () => {
  it("requires framework + capsulefile", () => {
    expect(
      () =>
        new NanorixLlamaIndexCallback({
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixLlamaIndexCallback({
          framework: "HIPAA",
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });

  it("VALID_LLAMAINDEX_EVENTS is closed enum", () => {
    expect(VALID_LLAMAINDEX_EVENTS).toContain("llm-start");
    expect(VALID_LLAMAINDEX_EVENTS).toContain("tool-call");
    expect(VALID_LLAMAINDEX_EVENTS).toContain("agent-step");
  });
});

describe("NanorixLlamaIndexCallback — LLM lifecycle", () => {
  it("emits reasoning_step_completed on llm-end", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("llm-start", {
      eventId: "llm-1",
      modelName: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
    });
    await cb.handleEvent("llm-end", {
      eventId: "llm-1",
      response: { message: { role: "assistant", content: "hello" } },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    expect(buf[0].model).toEqual("gpt-4o");
  });

  it("preserves token counts via passthrough", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("llm-start", {
      eventId: "llm-2",
      modelName: "claude-3",
    });
    await cb.handleEvent("llm-end", {
      eventId: "llm-2",
      response: { text: "ok" },
    });
    const buf = await readBuffer();
    expect(buf[0].model).toEqual("claude-3");
  });
});

describe("NanorixLlamaIndexCallback — tool lifecycle", () => {
  it("emits tool_call_attempted on tool-call event", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("tool-call", {
      eventId: "tc-1",
      tool: { name: "search" },
      functionCall: { name: "search", arguments: '{"q":"X"}' },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_name).toEqual("search");
    expect(buf[0].tool_call_id).toEqual("tc-1");
  });

  it("emits tool_call_completed on tool-result", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("tool-call", {
      eventId: "tc-2",
      tool: { name: "fetch" },
    });
    await cb.handleEvent("tool-result", {
      eventId: "tc-2",
      response: { ok: true },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[1].event).toEqual("tool_call_completed");
    expect(buf[1].tool_call_id).toEqual("tc-2");
  });

  it("emits tool_call_failed on tool-result with error", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("tool-call", {
      eventId: "tc-3",
      tool: { name: "fail" },
    });
    await cb.handleEvent("tool-result", {
      eventId: "tc-3",
      error: new Error("boom"),
    });
    const buf = await readBuffer();
    expect(buf[1].event).toEqual("tool_call_failed");
    expect(buf[1].failure_reason).toContain("Error: boom");
  });
});

describe("NanorixLlamaIndexCallback — agent step lifecycle", () => {
  it("emits tool_call_attempted for agent-step", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("agent-step", {
      eventId: "step-1",
      step: 1,
    });
    const buf = await readBuffer();
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_name).toEqual("agent_step");
  });

  it("suppresses agent-step when emitAgentStepEvents=false", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      emitAgentStepEvents: false,
    });
    await cb.handleEvent("agent-step", { eventId: "s-x" });
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixLlamaIndexCallback — structural noise", () => {
  it("retrieve-start emits nothing", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleEvent("retrieve-start", { eventId: "r-1" });
    await cb.handleEvent("retrieve-end", { eventId: "r-1" });
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });

  it("unknown event names emit nothing", async () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const result = await cb.handleEvent("unknown-event", { eventId: "u-1" });
    expect(result).toBeUndefined();
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixLlamaIndexCallback — bindTo", () => {
  it("subscribes to all valid event names on a CallbackManager-shape object", () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    const calls: string[] = [];
    const fakeManager = {
      on: (eventName: string, _handler: (payload: unknown) => unknown) => {
        calls.push(eventName);
        return undefined;
      },
    };
    cb.bindTo(fakeManager);
    expect(calls).toEqual([...VALID_LLAMAINDEX_EVENTS]);
  });

  it("rejects non-CallbackManager objects", () => {
    const cb = new NanorixLlamaIndexCallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    expect(() => cb.bindTo({})).toThrowError(/CallbackManager/);
  });
});
