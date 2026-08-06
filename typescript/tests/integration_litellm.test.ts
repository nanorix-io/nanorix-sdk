/**
 * LiteLLM HTTP-API integration adapter tests — Batch 7 sub-item 3.
 *
 * Validates beforeRequest/afterSuccess/afterFailure event-flow,
 * OpenAI-shape tool_calls extraction, and function-calling event
 * fan-out.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  NanorixLiteLLMCallback,
  VALID_LITELLM_HOOKS,
} from "../src/integrations/litellm.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-litellm-${randomBytes(8).toString("hex")}`);
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

describe("NanorixLiteLLMCallback — constructor", () => {
  it("requires framework + capsulefile", () => {
    expect(
      () =>
        new NanorixLiteLLMCallback({
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixLiteLLMCallback({
          framework: "HIPAA",
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });

  it("VALID_LITELLM_HOOKS is closed enum", () => {
    expect(VALID_LITELLM_HOOKS).toEqual([
      "before_request",
      "after_success",
      "after_failure",
    ]);
  });
});

describe("NanorixLiteLLMCallback — success path", () => {
  it("emits reasoning_step_completed on afterSuccess", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    cb.beforeRequest({
      callId: "c-1",
      model: "openai/gpt-4o",
      messages: [{ role: "user", content: "hi" }],
    });
    await cb.afterSuccess({
      callId: "c-1",
      response: {
        model: "gpt-4o",
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: "hello" },
            finish_reason: "stop",
          },
        ],
        usage: { prompt_tokens: 5, completion_tokens: 2, total_tokens: 7 },
      },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    expect(buf[0].model).toEqual("openai/gpt-4o");
  });

  it("emits tool_call_attempted per tool_call in response", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    cb.beforeRequest({ callId: "c-2", model: "openai/gpt-4o" });
    await cb.afterSuccess({
      callId: "c-2",
      response: {
        choices: [
          {
            message: {
              role: "assistant",
              content: "",
              tool_calls: [
                {
                  id: "call_abc",
                  type: "function",
                  function: { name: "submit", arguments: '{"x":1}' },
                },
                {
                  id: "call_def",
                  type: "function",
                  function: { name: "lookup", arguments: '{"y":2}' },
                },
              ],
            },
          },
        ],
      },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(3); // 1 reasoning + 2 tool_call_attempted
    const toolEvents = buf.filter((e) => e.event === "tool_call_attempted");
    expect(toolEvents).toHaveLength(2);
    expect(toolEvents[0].tool_name).toEqual("submit");
    expect(toolEvents[0].tool_call_id).toEqual("call_abc");
    expect(toolEvents[1].tool_name).toEqual("lookup");
  });

  it("emitToolRequestEvents=false suppresses tool fan-out", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      emitToolRequestEvents: false,
    });
    cb.beforeRequest({ callId: "c-3" });
    await cb.afterSuccess({
      callId: "c-3",
      response: {
        choices: [
          {
            message: {
              role: "assistant",
              tool_calls: [
                { id: "x", type: "function", function: { name: "y", arguments: "{}" } },
              ],
            },
          },
        ],
      },
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
  });
});

describe("NanorixLiteLLMCallback — failure path", () => {
  it("emits reasoning_step_completed with failure-shape payload", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    cb.beforeRequest({ callId: "c-4", model: "openai/gpt-4o" });
    await cb.afterFailure({
      callId: "c-4",
      error: new Error("API timeout"),
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    // Failure-shape payload is hashed; we can't introspect content,
    // but the response_hash must be a valid sha512 prefix.
    expect(buf[0].response_hash).toMatch(/^sha512:[a-f0-9]{128}$/);
  });

  it("afterFailure without prior beforeRequest still emits", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.afterFailure({
      callId: "orphan",
      error: "string error",
      model: "claude",
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].model).toEqual("claude");
  });
});

describe("NanorixLiteLLMCallback — request validation", () => {
  it("beforeRequest requires callId", () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    expect(() => cb.beforeRequest({} as { callId: string })).toThrowError(
      /callId/,
    );
  });

  it("onEvent debug hook fires per emission", async () => {
    const seen: string[] = [];
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      onEvent: (ev) => {
        seen.push(String(ev.event));
      },
    });
    cb.beforeRequest({ callId: "x" });
    await cb.afterSuccess({ callId: "x", response: { choices: [] } });
    expect(seen).toContain("reasoning_step_completed");
  });
});

describe("NanorixLiteLLMCallback — multi-call sequence", () => {
  it("step_idx increments monotonically across calls", async () => {
    const cb = new NanorixLiteLLMCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    for (let i = 0; i < 3; i++) {
      cb.beforeRequest({ callId: `call-${i}` });
      await cb.afterSuccess({
        callId: `call-${i}`,
        response: { choices: [] },
      });
    }
    const buf = await readBuffer();
    expect(buf).toHaveLength(3);
    expect(buf[0].step_idx).toEqual(0);
    expect(buf[1].step_idx).toEqual(1);
    expect(buf[2].step_idx).toEqual(2);
  });
});
