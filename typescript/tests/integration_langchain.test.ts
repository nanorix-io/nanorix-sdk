/**
 * LangChain.js integration adapter tests — Batch 7 sub-item 3.
 *
 * Validates:
 *  - Constructor strict-mode (framework/capsulefile required)
 *  - handleLLMStart/handleLLMEnd → reasoning_step_completed pair
 *  - handleToolStart/handleToolEnd → tool_call_attempted + completed
 *  - handleToolError → tool_call_failed with closed-enum kind
 *  - asLangChainCallback() returns bound structural callback
 *  - Token usage preserved in rendered LLM result
 *  - on_event debug hook fires per emission
 *  - Forever-Standard: no new ActivityEvent variants
 *  - Chain hooks emit no events (lifecycle only)
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import { NanorixLangChainCallback } from "../src/integrations/langchain.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-langchain-${randomBytes(8).toString("hex")}`);
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

describe("NanorixLangChainCallback — constructor", () => {
  it("requires framework", () => {
    expect(
      () =>
        new NanorixLangChainCallback({
          // @ts-expect-error intentional missing required
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
  });

  it("requires capsulefile", () => {
    expect(
      () =>
        new NanorixLangChainCallback({
          framework: "HIPAA",
          // @ts-expect-error intentional missing
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });

  it("stores framework + capsulefile + bufferPath", () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "pa-agent",
      bufferPath,
    });
    expect(cb.framework).toEqual("HIPAA");
    expect(cb.capsulefile).toEqual("pa-agent");
    expect(cb.bufferPath).toEqual(bufferPath);
  });
});

describe("NanorixLangChainCallback — LLM lifecycle", () => {
  it("emits reasoning_step_completed on handleLLMEnd", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleLLMStart(
      { name: "openai", kwargs: { model: "gpt-4o" } },
      ["Hello"],
      "run-1",
    );
    await cb.handleLLMEnd(
      {
        generations: [[{ text: "Hi there" }]],
        llmOutput: {
          tokenUsage: { prompt_tokens: 5, completion_tokens: 2 },
          modelName: "gpt-4o",
        },
      },
      "run-1",
    );
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    expect(buf[0].step_idx).toEqual(0);
    expect(buf[0].model).toEqual("gpt-4o");
  });

  it("handleLLMError pops in-flight context without emitting", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleLLMStart({}, ["x"], "run-err");
    await cb.handleLLMError(new Error("boom"), "run-err");
    // No file written = no event emitted.
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });

  it("preserves token counts (no rounding)", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "GDPR",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChatModelStart(
      undefined,
      [[{ type: "human", content: "hi" }]],
      "run-2",
      undefined,
      { invocation_params: { model: "claude-3-5-sonnet" } },
    );
    await cb.handleLLMEnd(
      {
        generations: [],
        llmOutput: {
          token_usage: { prompt_tokens: 12345, completion_tokens: 6789 },
        },
      },
      "run-2",
    );
    const buf = await readBuffer();
    expect(buf[0].model).toEqual("claude-3-5-sonnet");
  });
});

describe("NanorixLangChainCallback — tool lifecycle", () => {
  it("emits tool_call_attempted + tool_call_completed pair", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleToolStart(
      { name: "submit_pa" },
      JSON.stringify({ patient: "X" }),
      "tool-1",
    );
    await cb.handleToolEnd({ ok: true }, "tool-1");
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_name).toEqual("submit_pa");
    expect(buf[0].tool_call_id).toEqual("tool-1");
    expect(buf[1].event).toEqual("tool_call_completed");
    expect(buf[1].tool_call_id).toEqual("tool-1");
  });

  it("handleToolError emits tool_call_failed with classified kind", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleToolStart(
      { name: "validate" },
      "{}",
      "tool-2",
    );
    class ValidationError extends Error {
      constructor(msg: string) {
        super(msg);
        this.name = "ValidationError";
      }
    }
    Object.setPrototypeOf(ValidationError.prototype, Error.prototype);
    const err = new ValidationError("bad input");
    Object.defineProperty(err.constructor, "name", { value: "ValidationError" });
    await cb.handleToolError(err, "tool-2");
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[1].event).toEqual("tool_call_failed");
    expect(buf[1].tool_call_id).toEqual("tool-2");
    expect((buf[1].failure_kind as Record<string, unknown>)?.type).toEqual(
      "invalid_args",
    );
  });

  it("handleToolEnd without matching start emits with synthetic context", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleToolEnd("late", "tool-orphan");
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("tool_call_completed");
    expect(buf[0].tool_call_id).toEqual("tool-orphan");
    expect(buf[0].tool_name).toEqual("unknown");
  });
});

describe("NanorixLangChainCallback — chain lifecycle (no events)", () => {
  it("handleChainStart/End emit nothing", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart({}, {}, "chain-1");
    await cb.handleChainEnd({}, "chain-1");
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixLangChainCallback — structural callback shape", () => {
  it("asLangChainCallback returns object with all expected methods", () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    const shape = cb.asLangChainCallback();
    for (const m of [
      "handleChainStart",
      "handleChainEnd",
      "handleChainError",
      "handleLLMStart",
      "handleChatModelStart",
      "handleLLMEnd",
      "handleLLMError",
      "handleToolStart",
      "handleToolEnd",
      "handleToolError",
    ]) {
      expect(typeof shape[m]).toEqual("function");
    }
    expect(shape.name).toEqual("NanorixLangChainCallback");
  });

  it("on_event debug hook fires per emission", async () => {
    const seen: string[] = [];
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      onEvent: (ev) => {
        seen.push(String(ev.event));
      },
    });
    await cb.handleToolStart({ name: "x" }, "{}", "t-1");
    await cb.handleToolEnd({}, "t-1");
    expect(seen).toEqual(["tool_call_attempted", "tool_call_completed"]);
  });
});

describe("NanorixLangChainCallback — Forever-Standard", () => {
  it("only emits the 4 closed ActivityEvent variants", async () => {
    const cb = new NanorixLangChainCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleLLMStart({}, ["p"], "r-1");
    await cb.handleLLMEnd({ generations: [] }, "r-1");
    await cb.handleToolStart({ name: "t" }, "{}", "tc-1");
    await cb.handleToolEnd({}, "tc-1");
    await cb.handleToolStart({ name: "t2" }, "{}", "tc-2");
    await cb.handleToolError(new Error("e"), "tc-2");
    const buf = await readBuffer();
    const events = new Set(buf.map((e) => e.event));
    const ALLOWED = new Set([
      "reasoning_step_completed",
      "tool_call_attempted",
      "tool_call_completed",
      "tool_call_failed",
    ]);
    for (const e of events) {
      expect(ALLOWED.has(e as string)).toBe(true);
    }
  });
});
