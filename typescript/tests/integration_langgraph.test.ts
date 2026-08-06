/**
 * LangGraph.js integration adapter tests — Batch 7 sub-item 3.
 *
 * Validates node-aware dispatch: a chain tagged `graph:step:*` (or
 * with `metadata.langgraph_node`) emits tool_call_* events;
 * non-node chains stay silent.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import { NanorixLangGraphCallback } from "../src/integrations/langgraph.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-langgraph-${randomBytes(8).toString("hex")}`);
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

describe("NanorixLangGraphCallback — constructor", () => {
  it("requires framework + capsulefile", () => {
    expect(
      () =>
        new NanorixLangGraphCallback({
          // @ts-expect-error intentional
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixLangGraphCallback({
          framework: "HIPAA",
          // @ts-expect-error intentional
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });

  it("emitNodeEvents defaults to true", () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    expect(cb.emitNodeEvents).toBe(true);
  });
});

describe("NanorixLangGraphCallback — node-aware chain hooks", () => {
  it("emits tool_call_attempted for chain tagged graph:step:0", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "planner" },
      { user_input: "X" },
      "node-1",
      undefined,
      ["graph:step:0"],
      {},
    );
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_name).toEqual("planner");
  });

  it("detects langgraph_node metadata as node-signal", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "router" },
      {},
      "node-2",
      undefined,
      undefined,
      { langgraph_node: "router" },
    );
    const buf = await readBuffer();
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_name).toEqual("router");
  });

  it("non-node chain emits nothing", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "outer" },
      {},
      "outer-1",
    );
    await cb.handleChainEnd({}, "outer-1");
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });

  it("handleChainEnd pairs tool_call_completed with attempted", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "agent_step" },
      { input: 1 },
      "node-3",
      undefined,
      ["graph:step:1"],
    );
    await cb.handleChainEnd({ output: "ok" }, "node-3");
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[1].event).toEqual("tool_call_completed");
    expect(buf[1].tool_call_id).toEqual("node-3");
  });

  it("handleChainError emits tool_call_failed for in-flight node", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "worker" },
      {},
      "node-4",
      undefined,
      ["graph:step:2"],
    );
    await cb.handleChainError(new Error("boom"), "node-4");
    const buf = await readBuffer();
    expect(buf[1].event).toEqual("tool_call_failed");
    expect(buf[1].failure_reason).toContain("Error: boom");
  });
});

describe("NanorixLangGraphCallback — LLM + tool hooks", () => {
  it("emits reasoning_step_completed inside a node", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "node_a" },
      {},
      "node-llm",
      undefined,
      ["graph:step:0"],
    );
    await cb.handleLLMStart(
      { kwargs: { model: "gpt-4o" } },
      ["prompt"],
      "llm-1",
      "node-llm",
    );
    await cb.handleLLMEnd({ generations: [] }, "llm-1", "node-llm");
    await cb.handleChainEnd({}, "node-llm");
    const buf = await readBuffer();
    const llmEvent = buf.find((e) => e.event === "reasoning_step_completed");
    expect(llmEvent).toBeDefined();
    expect(llmEvent?.model).toEqual("gpt-4o");
  });

  it("emits tool_call_* for tools invoked inside nodes", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.handleChainStart(
      { name: "n" },
      {},
      "n-1",
      undefined,
      ["graph:step:0"],
    );
    await cb.handleToolStart(
      { name: "calc" },
      "{}",
      "t-1",
      "n-1",
    );
    await cb.handleToolEnd({ r: 42 }, "t-1", "n-1");
    await cb.handleChainEnd({}, "n-1");
    const buf = await readBuffer();
    const toolAttempted = buf.find((e) => e.event === "tool_call_attempted" && e.tool_name === "calc");
    expect(toolAttempted).toBeDefined();
    expect(toolAttempted?.parent_event_id).toEqual("n-1");
  });
});

describe("NanorixLangGraphCallback — emitNodeEvents=false", () => {
  it("suppresses per-node receipts when disabled", async () => {
    const cb = new NanorixLangGraphCallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      emitNodeEvents: false,
    });
    await cb.handleChainStart(
      { name: "node" },
      {},
      "n-x",
      undefined,
      ["graph:step:0"],
    );
    await cb.handleChainEnd({}, "n-x");
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});
