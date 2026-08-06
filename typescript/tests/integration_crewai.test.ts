/**
 * CrewAI structural-callback adapter tests — Batch 7 sub-item 3.
 *
 * Validates task-shape (tool_call_attempted + completed) + step-shape
 * (reasoning_step_completed) emission, AgentAction/AgentFinish
 * discrimination, and failure-path coverage.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  NanorixCrewAICallback,
  extractTaskId as crewaiExtractTaskId,
  extractTaskName as crewaiExtractTaskName,
  extractTaskDurationMs as crewaiExtractTaskDurationMs,
  renderStep as crewaiRenderStep,
} from "../src/integrations/crewai.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-crewai-${randomBytes(8).toString("hex")}`);
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

describe("NanorixCrewAICallback — constructor", () => {
  it("requires framework + capsulefile", () => {
    expect(
      () =>
        new NanorixCrewAICallback({
          framework: "",
          capsulefile: "agent",
        }),
    ).toThrowError(/framework/);
    expect(
      () =>
        new NanorixCrewAICallback({
          framework: "HIPAA",
          capsulefile: "",
        }),
    ).toThrowError(/capsulefile/);
  });

  it("emitTaskEvents + emitStepEvents default to true", () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
    });
    expect(cb.emitTaskEvents).toBe(true);
    expect(cb.emitStepEvents).toBe(true);
  });
});

describe("NanorixCrewAICallback — task lifecycle", () => {
  it("taskCallback emits tool_call_attempted + tool_call_completed pair", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.taskCallback({
      task_id: "task-1",
      description: "Summarize patient records",
      raw: "Patient X needs PA approval...",
      execution_time: 2.5,
      agent: "summarizer",
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(2);
    expect(buf[0].event).toEqual("tool_call_attempted");
    expect(buf[0].tool_call_id).toEqual("task-1");
    expect(buf[0].agent_id).toEqual("summarizer");
    expect(buf[1].event).toEqual("tool_call_completed");
    expect(buf[1].duration_ms).toEqual(2500); // 2.5s → 2500ms
  });

  it("taskCallback suppressed when emitTaskEvents=false", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      emitTaskEvents: false,
    });
    await cb.taskCallback({ task_id: "x", description: "do it" });
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixCrewAICallback — step lifecycle", () => {
  it("stepCallback emits reasoning_step_completed for AgentAction", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.stepCallback({
      tool: "search",
      tool_input: { q: "PA criteria" },
      log: "I need to search for criteria",
    });
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("reasoning_step_completed");
    // AgentAction → "reason" step_kind
    expect((buf[0].step_kind as Record<string, unknown>)?.type).toEqual(
      "reason",
    );
  });

  it("stepCallback emits AgentFinish as decide step_kind", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.stepCallback({
      return_values: { output: "Final answer: approve" },
      log: "Decision made",
    });
    const buf = await readBuffer();
    expect((buf[0].step_kind as Record<string, unknown>)?.type).toEqual(
      "decide",
    );
  });

  it("stepCallback suppressed when emitStepEvents=false", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      emitStepEvents: false,
    });
    await cb.stepCallback({ tool: "x", log: "y" });
    await expect(fs.access(bufferPath)).rejects.toThrow();
  });
});

describe("NanorixCrewAICallback — failure path", () => {
  it("onTaskFailed emits tool_call_failed with classified kind", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    class TimeoutError extends Error {
      constructor() {
        super("task exceeded budget");
      }
    }
    const err = new TimeoutError();
    Object.defineProperty(err.constructor, "name", { value: "TimeoutError" });
    await cb.onTaskFailed(
      { task_id: "task-fail", description: "long" },
      err,
      { durationMs: 30000 },
    );
    const buf = await readBuffer();
    expect(buf).toHaveLength(1);
    expect(buf[0].event).toEqual("tool_call_failed");
    expect(buf[0].tool_call_id).toEqual("task-fail");
    expect(buf[0].duration_ms).toEqual(30000);
    expect((buf[0].failure_kind as Record<string, unknown>)?.type).toEqual(
      "timeout",
    );
  });

  it("onStepError emits tool_call_failed", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onStepError(
      { id: "step-X", tool: "calc" },
      new Error("step bad"),
    );
    const buf = await readBuffer();
    expect(buf[0].event).toEqual("tool_call_failed");
    expect(buf[0].tool_name).toEqual("calc");
  });

  it("onTaskFailed accepts string task_id directly", async () => {
    const cb = new NanorixCrewAICallback({
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await cb.onTaskFailed("orphan-task", new Error("escaped"));
    const buf = await readBuffer();
    expect(buf[0].tool_call_id).toEqual("orphan-task");
    expect(buf[0].tool_name).toEqual("crewai_task");
  });
});

describe("NanorixCrewAICallback — extractor helpers", () => {
  it("crewaiExtractTaskId pulls from task_id / id / nested task", () => {
    expect(crewaiExtractTaskId({ task_id: "T1" })).toEqual("T1");
    expect(crewaiExtractTaskId({ id: "T2" })).toEqual("T2");
    expect(crewaiExtractTaskId({ task: { id: "T3" } })).toEqual("T3");
    // Hash-from-description fallback yields stable prefix
    const hashed = crewaiExtractTaskId({ description: "do X" });
    expect(hashed.startsWith("crewai_task:")).toBe(true);
  });

  it("crewaiExtractTaskName truncates description to 80 chars", () => {
    const longDesc = "A".repeat(120);
    expect(crewaiExtractTaskName({ description: longDesc }).length).toEqual(80);
  });

  it("crewaiExtractTaskDurationMs handles seconds + ms shapes", () => {
    expect(crewaiExtractTaskDurationMs({ execution_time: 2.5 })).toEqual(2500);
    // Integer execution_time treated as already-ms by Python convention
    expect(crewaiExtractTaskDurationMs({ execution_time: 1500 })).toEqual(1500);
    expect(crewaiExtractTaskDurationMs({ duration: 750 })).toEqual(750);
    expect(crewaiExtractTaskDurationMs({})).toEqual(0);
  });

  it("crewaiRenderStep discriminates AgentFinish vs AgentAction", () => {
    expect(crewaiRenderStep({ return_values: { output: "done" } })).toEqual(
      expect.objectContaining({ kind: "agent_finish" }),
    );
    expect(crewaiRenderStep({ tool: "search", tool_input: "x" })).toEqual(
      expect.objectContaining({ kind: "agent_action" }),
    );
  });
});
