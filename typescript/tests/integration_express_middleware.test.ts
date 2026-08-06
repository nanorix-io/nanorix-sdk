/**
 * Express.js middleware integration tests — Batch 7 sub-item 4.
 *
 * Validates:
 *  - Constructor strict-mode (client/framework/capsulefile required)
 *  - Middleware factory returns RequestHandler-shape
 *  - req.nanorix attached with capsule/framework/metadata
 *  - res.finish triggers reasoning_step_completed (2xx) / tool_call_failed (5xx)
 *  - Error handler triggers capsule cleanup + tool_call_failed
 *  - excludePaths filter skips wrap
 *  - bindAuditProof:false skips header attachment
 *  - toMetadata envelope shape (reserved `nanorix:*` keys)
 *  - frameworkPerRoute longest-prefix match
 *  - onAuditProof callback fires
 *  - Custom logger receives warn events on create failure
 *  - Capsule create failure does NOT block request (best-effort)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

import {
  CDP_KIND_REQUEST,
  RESPONSE_HEADER_AUDIT_PROOF_ID,
  RESPONSE_HEADER_CAPSULE_ID,
  RESPONSE_HEADER_CDP_KIND,
  WRAPPER_KIND,
  nanorixErrorHandler,
  nanorixMiddleware,
  toMetadata,
  type ExpressRequestLike,
  type ExpressResponseLike,
  type NanorixCapsuleLike,
  type NanorixClientLike,
} from "../src/integrations/express_middleware.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-express-${randomBytes(8).toString("hex")}`);
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
  try {
    const data = await fs.readFile(bufferPath, "utf-8");
    return data.trim().split("\n").map((line) => JSON.parse(line));
  } catch {
    return [];
  }
}

/** Lightweight call recorder (avoids `vi.fn()` quirks with async mocks). */
interface CallRecorder {
  calls: unknown[][];
}

/** Build a structural mock Nanorix client whose capsules.create returns a
 *  configurable mock capsule. Uses hand-rolled call recorders rather than
 *  `vi.fn(async ...)` (which intermittently fails to register `await`-call
 *  invocations through structural references in vitest 1.x). */
function buildMockClient(opts: {
  capsuleId?: string;
  capsuleDestroyResult?: unknown;
  createError?: Error;
  destroyError?: Error;
}): {
  client: NanorixClientLike;
  destroyRec: CallRecorder;
  createRec: CallRecorder;
} {
  const capsuleId = opts.capsuleId ?? "11111111-2222-3333-4444-555555555555";
  const destroyRec: CallRecorder = { calls: [] };
  const createRec: CallRecorder = { calls: [] };
  const destroyFn = async (): Promise<unknown> => {
    destroyRec.calls.push([]);
    if (opts.destroyError) throw opts.destroyError;
    return (
      opts.capsuleDestroyResult ?? {
        audit_proof_id: `cap_${capsuleId.replace(/-/g, "")}`,
      }
    );
  };
  const capsule: NanorixCapsuleLike = { id: capsuleId, destroy: destroyFn };
  const createFn = async (
    args: Parameters<NanorixClientLike["capsules"]["create"]>[0],
  ): Promise<NanorixCapsuleLike> => {
    createRec.calls.push([args]);
    if (opts.createError) throw opts.createError;
    return capsule;
  };
  const client: NanorixClientLike = { capsules: { create: createFn } };
  return { client, destroyRec, createRec };
}

/** Build a mock res with a finish-event subscription. */
function buildMockRes(statusCode: number): {
  res: ExpressResponseLike;
  headers: Record<string, string>;
  fireFinish: () => void;
} {
  const headers: Record<string, string> = {};
  const finishListeners: Array<() => void> = [];
  const res: ExpressResponseLike = {
    statusCode,
    setHeader(name: string, value: string): void {
      headers[name] = value;
    },
    on(event: "finish" | "close", listener: () => void): void {
      if (event === "finish") finishListeners.push(listener);
    },
  };
  return {
    res,
    headers,
    fireFinish(): void {
      for (const l of finishListeners) l();
    },
  };
}

function buildMockReq(method: string, path: string): ExpressRequestLike {
  return { method, path };
}

/** Wait for promise chains + macrotask (I/O) to drain. */
async function flush(times = 10): Promise<void> {
  for (let i = 0; i < times; i++) {
    await new Promise<void>((resolve) => setImmediate(resolve));
    await Promise.resolve();
    await Promise.resolve();
  }
}

/** Wait until a predicate becomes true (polls every macrotask, up to ~1s). */
async function waitFor(pred: () => boolean, maxTicks = 200): Promise<void> {
  for (let i = 0; i < maxTicks; i++) {
    if (pred()) return;
    await new Promise<void>((resolve) => setImmediate(resolve));
    await Promise.resolve();
  }
}

describe("nanorixMiddleware — constructor validation", () => {
  it("requires client", () => {
    expect(() =>
      // @ts-expect-error intentional missing
      nanorixMiddleware({ framework: "HIPAA", capsulefile: "agent" }),
    ).toThrowError(/client/);
  });

  it("requires framework", () => {
    const { client } = buildMockClient({});
    expect(() =>
      nanorixMiddleware({
        client,
        // @ts-expect-error intentional missing
        framework: "",
        capsulefile: "agent",
      }),
    ).toThrowError(/framework/);
  });

  it("requires capsulefile", () => {
    const { client } = buildMockClient({});
    expect(() =>
      nanorixMiddleware({
        client,
        framework: "HIPAA",
        // @ts-expect-error intentional missing
        capsulefile: "",
      }),
    ).toThrowError(/capsulefile/);
  });

  it("returns a RequestHandler-shape function (3-arg)", () => {
    const { client } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
    });
    expect(typeof mw).toBe("function");
    expect(mw.length).toBeGreaterThanOrEqual(3);
  });
});

describe("nanorixMiddleware — request lifecycle", () => {
  it("attaches req.nanorix with capsule + metadata + framework", async () => {
    const { client } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const req = buildMockReq("POST", "/v1/decide");
    const { res } = buildMockRes(200);
    const next = vi.fn();
    mw(req, res, next);
    await flush();

    expect(next).toHaveBeenCalledTimes(1);
    expect(req.nanorix).toBeDefined();
    expect(req.nanorix?.framework).toBe("HIPAA");
    expect(req.nanorix?.capsule?.id).toBeDefined();
    expect(req.nanorix?.metadata["nanorix:request_method"]).toBe("POST");
    expect(req.nanorix?.metadata["nanorix:request_path"]).toBe("/v1/decide");
    expect(req.nanorix?.metadata["nanorix:wrapper_kind"]).toBe(WRAPPER_KIND);
  });

  it("emits reasoning_step_completed on 2xx res.finish", async () => {
    const { client, destroyRec } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const req = buildMockReq("GET", "/items");
    const { res, fireFinish } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush();
    fireFinish();
    // Wait until the post-finish async chain has completed (destroy ran).
    await waitFor(() => destroyRec.calls.length > 0);

    const events = await readBuffer();
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect(events[0].event).toBe("reasoning_step_completed");
  });

  it("emits tool_call_failed on 5xx res.finish", async () => {
    const { client, destroyRec } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const req = buildMockReq("POST", "/v1/process");
    const { res, fireFinish } = buildMockRes(503);
    mw(req, res, vi.fn());
    await flush();
    fireFinish();
    await waitFor(() => destroyRec.calls.length > 0);

    const events = await readBuffer();
    expect(events.length).toBeGreaterThanOrEqual(1);
    expect(events[0].event).toBe("tool_call_failed");
    expect(events[0].failure_reason).toBe("http_status_503");
    expect(events[0].failure_kind).toEqual({ type: "upstream_failure" });
  });

  it("destroys the capsule on res.finish (best-effort)", async () => {
    const { client, destroyRec } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const req = buildMockReq("GET", "/health2");
    const { res, fireFinish } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush();
    fireFinish();
    await waitFor(() => destroyRec.calls.length > 0);
    expect(destroyRec.calls.length).toBe(1);
  });

  it("invokes onAuditProof callback after destroy with the CDP result", async () => {
    const { client } = buildMockClient({ capsuleDestroyResult: { audit_proof_id: "cap_xyz" } });
    const auditProofCalls: unknown[] = [];
    const onAuditProof = (cdp: unknown): void => {
      auditProofCalls.push(cdp);
    };
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      onAuditProof,
    });
    const req = buildMockReq("GET", "/v1/foo");
    const { res, fireFinish } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush();
    fireFinish();
    await waitFor(() => auditProofCalls.length > 0);
    expect(auditProofCalls.length).toBe(1);
    expect(auditProofCalls[0]).toEqual({ audit_proof_id: "cap_xyz" });
  });
});

describe("nanorixMiddleware — configuration", () => {
  it("excludePaths skips wrap (no capsule create)", async () => {
    const { client, createRec } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      excludePaths: ["/health", "/metrics"],
      bufferPath,
    });
    const req = buildMockReq("GET", "/health");
    const { res } = buildMockRes(200);
    const next = vi.fn();
    mw(req, res, next);
    await flush(5);
    expect(next).toHaveBeenCalledTimes(1);
    expect(createRec.calls.length).toBe(0);
    expect(req.nanorix).toBeUndefined();
  });

  it("attaches AuditProof response headers when bindAuditProof:true (default)", async () => {
    const { client } = buildMockClient({ capsuleId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" });
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const req = buildMockReq("GET", "/v1/x");
    const { res, headers } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush(20);
    expect(headers[RESPONSE_HEADER_CAPSULE_ID]).toBe("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee");
    expect(headers[RESPONSE_HEADER_CDP_KIND]).toBe(CDP_KIND_REQUEST);
    expect(headers[RESPONSE_HEADER_AUDIT_PROOF_ID]).toBe(
      "cap_aaaaaaaabbbbccccddddeeeeeeeeeeee",
    );
  });

  it("skips header attachment when bindAuditProof:false", async () => {
    const { client } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bindAuditProof: false,
      bufferPath,
    });
    const req = buildMockReq("GET", "/v1/x");
    const { res, headers } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush(20);
    expect(headers[RESPONSE_HEADER_CAPSULE_ID]).toBeUndefined();
    expect(headers[RESPONSE_HEADER_AUDIT_PROOF_ID]).toBeUndefined();
    expect(headers[RESPONSE_HEADER_CDP_KIND]).toBeUndefined();
  });

  it("frameworkPerRoute applies longest-prefix-first match", async () => {
    const { client } = buildMockClient({});
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      frameworkPerRoute: {
        "/admin": "SOC2",
        "/admin/v2": "GDPR",
        "/public": "PCI_DSS",
      },
      bufferPath,
    });
    const req = buildMockReq("POST", "/admin/v2/audit");
    const { res } = buildMockRes(200);
    mw(req, res, vi.fn());
    await flush();
    expect(req.nanorix?.framework).toBe("GDPR");
  });
});

describe("nanorixMiddleware — best-effort discipline", () => {
  it("capsule create failure does NOT block request", async () => {
    const { client } = buildMockClient({
      createError: new Error("simulated network failure"),
    });
    const logger = { warn: vi.fn() };
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      logger,
    });
    const req = buildMockReq("GET", "/x");
    const { res } = buildMockRes(200);
    const next = vi.fn();
    mw(req, res, next);
    await flush(10);
    expect(next).toHaveBeenCalledTimes(1);
    expect(req.nanorix).toBeDefined();
    expect(req.nanorix?.capsule).toBeNull();
    expect(logger.warn).toHaveBeenCalled();
  });
});

describe("nanorixErrorHandler", () => {
  it("returns a 4-arg ErrorRequestHandler shape", () => {
    const handler = nanorixErrorHandler({});
    expect(typeof handler).toBe("function");
    expect(handler.length).toBeGreaterThanOrEqual(4);
  });

  it("triggers capsule destroy + tool_call_failed on error path", async () => {
    let destroyCount = 0;
    const capsuleId = "11111111-2222-3333-4444-555555555555";
    const destroyFn = async (): Promise<unknown> => {
      destroyCount += 1;
      return { audit_proof_id: `cap_${capsuleId.replace(/-/g, "")}` };
    };
    const capsule: NanorixCapsuleLike = {
      id: capsuleId,
      destroy: destroyFn,
    };
    const client: NanorixClientLike = {
      capsules: {
        async create() {
          return capsule;
        },
      },
    };
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const eh = nanorixErrorHandler({ bufferPath });
    const req = buildMockReq("POST", "/v1/x");
    const { res } = buildMockRes(500);
    mw(req, res, vi.fn());
    await flush(20);

    const err = new TypeError("bad input");
    const next = vi.fn();
    eh(err, req, res, next);
    await waitFor(() => destroyCount > 0);

    expect(next).toHaveBeenCalledWith(err);
    expect(destroyCount).toBeGreaterThanOrEqual(1);
    const events = await readBuffer();
    expect(events.some((e) => e.event === "tool_call_failed")).toBe(true);
    const failedEvent = events.find((e) => e.event === "tool_call_failed");
    expect(failedEvent?.failure_kind).toEqual({ type: "invalid_args" });
  });

  it("onAuditProof fires on error path via error handler", async () => {
    const { client } = buildMockClient({});
    let onAuditProofCalls = 0;
    const onAuditProof = (_cdp: unknown): void => {
      onAuditProofCalls += 1;
    };
    const mw = nanorixMiddleware({
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    const eh = nanorixErrorHandler({ bufferPath, onAuditProof });
    const req = buildMockReq("POST", "/v1/x");
    const { res } = buildMockRes(500);
    mw(req, res, vi.fn());
    await flush(20);
    eh(new Error("boom"), req, res, vi.fn());
    await waitFor(() => onAuditProofCalls > 0);
    expect(onAuditProofCalls).toBeGreaterThanOrEqual(1);
  });
});

describe("toMetadata (Express)", () => {
  it("builds reserved `nanorix:*` envelope keys", () => {
    const meta = toMetadata({
      method: "POST",
      path: "/v1/decide",
      framework: "HIPAA",
      frameworkVersion: "2026-02",
      agentId: "agent-7",
    });
    expect(meta["nanorix:wrapper_kind"]).toBe(WRAPPER_KIND);
    expect(meta["nanorix:framework"]).toBe("HIPAA");
    expect(meta["nanorix:framework_version"]).toBe("2026-02");
    expect(meta["nanorix:agent_id"]).toBe("agent-7");
    expect(meta["nanorix:request_method"]).toBe("POST");
    expect(meta["nanorix:request_path"]).toBe("/v1/decide");
  });

  it("omits optional keys when not provided", () => {
    const meta = toMetadata({
      method: "GET",
      path: "/x",
      framework: "GDPR",
    });
    expect("nanorix:framework_version" in meta).toBe(false);
    expect("nanorix:agent_id" in meta).toBe(false);
    expect(meta["nanorix:framework"]).toBe("GDPR");
  });

  it("respects per-key 4KB / 16-key envelope budget", () => {
    const meta = toMetadata({
      method: "GET",
      path: "/x",
      framework: "HIPAA",
      frameworkVersion: "2026-02",
      agentId: "a",
    });
    // 6 reserved keys ≪ 16-key cap; each value short ≪ 256-char value cap.
    expect(Object.keys(meta).length).toBeLessThanOrEqual(16);
    for (const v of Object.values(meta)) {
      expect(v.length).toBeLessThanOrEqual(256);
    }
    // Total bytes ≪ 4 KB.
    const total = JSON.stringify(meta).length;
    expect(total).toBeLessThan(4096);
  });
});
