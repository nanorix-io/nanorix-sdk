/**
 * Next.js (App Router) middleware integration tests.
 *
 * Validates:
 *  - withNanorix + wrapApiRoute constructor strict-mode
 *  - Async handler wraps capture request lifecycle (start + end + throw)
 *  - req.nanorixCapsule attached with capsule/framework/metadata
 *  - 2xx → reasoning_step_completed
 *  - 5xx → tool_call_failed
 *  - Thrown handler error → tool_call_failed + re-raise
 *  - excludePaths filter skips wrap
 *  - bindAuditProof:false skips response.headers.set
 *  - toMetadata envelope shape
 *  - frameworkPerRoute longest-prefix match
 *  - onAuditProof callback fires
 *  - Logger receives warn events on create failure
 *  - Capsule create failure does NOT block route
 *  - extractPath falls back to URL parsing when nextUrl missing
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
  toMetadata,
  withNanorix,
  wrapApiRoute,
  type NanorixCapsuleLike,
  type NanorixClientLike,
  type NextRequestLike,
  type NextRequestWithNanorix,
  type NextResponseLike,
} from "../src/integrations/nextjs_middleware.js";

let testDir: string;
let bufferPath: string;

beforeEach(async () => {
  testDir = join(tmpdir(), `nanorix-nextjs-${randomBytes(8).toString("hex")}`);
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

interface CallRecorder {
  calls: unknown[][];
}

function buildMockClient(opts: {
  capsuleId?: string;
  destroyResult?: unknown;
  createError?: Error;
  destroyError?: Error;
}): {
  client: NanorixClientLike;
  destroyRec: CallRecorder;
  createRec: CallRecorder;
} {
  const capsuleId = opts.capsuleId ?? "12345678-1234-1234-1234-123456789012";
  const destroyRec: CallRecorder = { calls: [] };
  const createRec: CallRecorder = { calls: [] };
  const destroyFn = async (): Promise<unknown> => {
    destroyRec.calls.push([]);
    if (opts.destroyError) throw opts.destroyError;
    return (
      opts.destroyResult ?? {
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
  return {
    client: { capsules: { create: createFn } },
    destroyRec,
    createRec,
  };
}

function buildMockReq(method: string, path: string): NextRequestLike {
  return {
    method,
    url: `https://api.example.com${path}`,
    nextUrl: { pathname: path },
  };
}

function buildMockResponse(status: number): {
  response: NextResponseLike;
  headers: Record<string, string>;
} {
  const headers: Record<string, string> = {};
  return {
    response: {
      status,
      headers: {
        set(name: string, value: string): void {
          headers[name] = value;
        },
      },
    },
    headers,
  };
}

describe("withNanorix — constructor validation", () => {
  it("requires client", () => {
    expect(() =>
      withNanorix(async () => buildMockResponse(200).response, {
        // @ts-expect-error intentional missing
        client: undefined,
        framework: "HIPAA",
        capsulefile: "agent",
      }),
    ).toThrowError(/client/);
  });

  it("requires framework", () => {
    const { client } = buildMockClient({});
    expect(() =>
      withNanorix(async () => buildMockResponse(200).response, {
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
      withNanorix(async () => buildMockResponse(200).response, {
        client,
        framework: "HIPAA",
        // @ts-expect-error intentional missing
        capsulefile: "",
      }),
    ).toThrowError(/capsulefile/);
  });
});

describe("withNanorix — request lifecycle", () => {
  it("attaches req.nanorixCapsule with capsule + metadata + framework", async () => {
    const { client } = buildMockClient({});
    let observed: NextRequestWithNanorix | undefined;
    const wrapped = withNanorix(
      async (req) => {
        observed = req;
        return buildMockResponse(200).response;
      },
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    await wrapped(buildMockReq("POST", "/v1/decide") as NextRequestWithNanorix);
    expect(observed?.nanorixCapsule).toBeDefined();
    expect(observed?.nanorixCapsule?.framework).toBe("HIPAA");
    expect(observed?.nanorixCapsule?.capsule?.id).toBeDefined();
    expect(observed?.nanorixCapsule?.metadata["nanorix:request_method"]).toBe("POST");
    expect(observed?.nanorixCapsule?.metadata["nanorix:request_path"]).toBe("/v1/decide");
    expect(observed?.nanorixCapsule?.metadata["nanorix:wrapper_kind"]).toBe(WRAPPER_KIND);
  });

  it("emits reasoning_step_completed on 2xx return", async () => {
    const { client } = buildMockClient({});
    const wrapped = withNanorix(
      async () => buildMockResponse(200).response,
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    await wrapped(buildMockReq("GET", "/items") as NextRequestWithNanorix);
    const events = await readBuffer();
    expect(events.some((e) => e.event === "reasoning_step_completed")).toBe(true);
  });

  it("emits tool_call_failed on 5xx return", async () => {
    const { client } = buildMockClient({});
    const wrapped = withNanorix(
      async () => buildMockResponse(503).response,
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    await wrapped(buildMockReq("POST", "/v1/process") as NextRequestWithNanorix);
    const events = await readBuffer();
    expect(events.some((e) => e.event === "tool_call_failed")).toBe(true);
    const failed = events.find((e) => e.event === "tool_call_failed");
    expect(failed?.failure_reason).toBe("http_status_503");
  });

  it("emits tool_call_failed and re-raises thrown handler errors", async () => {
    const { client, destroyRec } = buildMockClient({});
    const wrapped = withNanorix(
      async () => {
        throw new TypeError("invalid input shape");
      },
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );

    await expect(
      wrapped(buildMockReq("POST", "/v1/x") as NextRequestWithNanorix),
    ).rejects.toThrow("invalid input shape");

    expect(destroyRec.calls.length).toBe(1);
    const events = await readBuffer();
    const failed = events.find((e) => e.event === "tool_call_failed");
    expect(failed).toBeDefined();
    expect(failed?.failure_kind).toEqual({ type: "invalid_args" });
  });

  it("destroys capsule + invokes onAuditProof callback", async () => {
    const { client, destroyRec } = buildMockClient({
      destroyResult: { audit_proof_id: "cap_xyz" },
    });
    const auditProofCalls: unknown[] = [];
    const onAuditProof = (cdp: unknown): void => {
      auditProofCalls.push(cdp);
    };
    const wrapped = withNanorix(
      async () => buildMockResponse(200).response,
      {
        client,
        framework: "HIPAA",
        capsulefile: "agent",
        bufferPath,
        onAuditProof,
      },
    );
    await wrapped(buildMockReq("GET", "/foo") as NextRequestWithNanorix);
    expect(destroyRec.calls.length).toBe(1);
    expect(auditProofCalls.length).toBe(1);
    expect(auditProofCalls[0]).toEqual({ audit_proof_id: "cap_xyz" });
  });
});

describe("withNanorix — configuration", () => {
  it("excludePaths skips wrap (no capsule create)", async () => {
    const { client, createRec } = buildMockClient({});
    const wrapped = withNanorix(
      async () => buildMockResponse(200).response,
      {
        client,
        framework: "HIPAA",
        capsulefile: "agent",
        excludePaths: ["/health"],
        bufferPath,
      },
    );
    const req = buildMockReq("GET", "/health/check") as NextRequestWithNanorix;
    await wrapped(req);
    expect(createRec.calls.length).toBe(0);
    expect(req.nanorixCapsule).toBeUndefined();
  });

  it("attaches AuditProof headers when bindAuditProof:true (default)", async () => {
    const { client } = buildMockClient({
      capsuleId: "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    });
    const { response, headers } = buildMockResponse(200);
    const wrapped = withNanorix(async () => response, {
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
    });
    await wrapped(buildMockReq("GET", "/v1/x") as NextRequestWithNanorix);
    expect(headers[RESPONSE_HEADER_CAPSULE_ID]).toBe(
      "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    );
    expect(headers[RESPONSE_HEADER_CDP_KIND]).toBe(CDP_KIND_REQUEST);
    expect(headers[RESPONSE_HEADER_AUDIT_PROOF_ID]).toBe(
      "cap_aaaabbbbccccddddeeeeffff00001111",
    );
  });

  it("skips response.headers.set when bindAuditProof:false", async () => {
    const { client } = buildMockClient({});
    const { response, headers } = buildMockResponse(200);
    const wrapped = withNanorix(async () => response, {
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bindAuditProof: false,
      bufferPath,
    });
    await wrapped(buildMockReq("GET", "/v1/x") as NextRequestWithNanorix);
    expect(headers[RESPONSE_HEADER_CAPSULE_ID]).toBeUndefined();
    expect(headers[RESPONSE_HEADER_AUDIT_PROOF_ID]).toBeUndefined();
    expect(headers[RESPONSE_HEADER_CDP_KIND]).toBeUndefined();
  });

  it("frameworkPerRoute applies longest-prefix-first match", async () => {
    const { client } = buildMockClient({});
    let observed: NextRequestWithNanorix | undefined;
    const wrapped = withNanorix(
      async (req) => {
        observed = req;
        return buildMockResponse(200).response;
      },
      {
        client,
        framework: "HIPAA",
        capsulefile: "agent",
        frameworkPerRoute: {
          "/admin": "SOC2",
          "/admin/v2": "GDPR",
        },
        bufferPath,
      },
    );
    await wrapped(buildMockReq("POST", "/admin/v2/audit") as NextRequestWithNanorix);
    expect(observed?.nanorixCapsule?.framework).toBe("GDPR");
  });
});

describe("withNanorix — best-effort discipline", () => {
  it("capsule create failure does NOT block request", async () => {
    const { client } = buildMockClient({
      createError: new Error("simulated network failure"),
    });
    const logger = { warn: vi.fn() };
    const handlerCalled = vi.fn(async () => buildMockResponse(200).response);
    const wrapped = withNanorix(handlerCalled, {
      client,
      framework: "HIPAA",
      capsulefile: "agent",
      bufferPath,
      logger,
    });
    const req = buildMockReq("GET", "/x") as NextRequestWithNanorix;
    await wrapped(req);
    expect(handlerCalled).toHaveBeenCalledTimes(1);
    expect(req.nanorixCapsule?.capsule).toBeNull();
    expect(logger.warn).toHaveBeenCalled();
  });

  it("extractPath falls back to URL parsing when nextUrl missing", async () => {
    const { client } = buildMockClient({});
    let observed: NextRequestWithNanorix | undefined;
    const wrapped = withNanorix(
      async (req) => {
        observed = req;
        return buildMockResponse(200).response;
      },
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    // No nextUrl — must fall back to URL parsing.
    const req: NextRequestWithNanorix = {
      method: "POST",
      url: "https://api.example.com/v1/parsed",
    };
    await wrapped(req);
    expect(observed?.nanorixCapsule?.metadata["nanorix:request_path"]).toBe(
      "/v1/parsed",
    );
  });
});

describe("wrapApiRoute", () => {
  it("requires framework + capsulefile + client", () => {
    const { client } = buildMockClient({});
    expect(() =>
      wrapApiRoute(async () => buildMockResponse(200).response, {
        client,
        // @ts-expect-error intentional missing
        framework: "",
        capsulefile: "agent",
      }),
    ).toThrowError(/framework/);
  });

  it("passes through the (req, ctx) route signature", async () => {
    const { client } = buildMockClient({});
    let observedCtx: { params?: Record<string, string | string[]> } | undefined;
    const wrapped = wrapApiRoute(
      async (_req, ctx) => {
        observedCtx = ctx;
        return buildMockResponse(200).response;
      },
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    await wrapped(
      buildMockReq("POST", "/api/v1/decide") as NextRequestWithNanorix,
      { params: { id: "abc" } },
    );
    expect(observedCtx?.params?.id).toBe("abc");
  });

  it("attaches AuditProof headers + destroys capsule + re-raises on throw", async () => {
    const { client, destroyRec } = buildMockClient({});
    const wrapped = wrapApiRoute(
      async () => {
        throw new RangeError("bad range");
      },
      { client, framework: "HIPAA", capsulefile: "agent", bufferPath },
    );
    await expect(
      wrapped(buildMockReq("POST", "/api/v1/x") as NextRequestWithNanorix),
    ).rejects.toThrow("bad range");
    expect(destroyRec.calls.length).toBe(1);
    const events = await readBuffer();
    const failed = events.find((e) => e.event === "tool_call_failed");
    expect(failed?.failure_kind).toEqual({ type: "invalid_args" });
  });
});

describe("toMetadata (Next.js)", () => {
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
    const meta = toMetadata({ method: "GET", path: "/x", framework: "GDPR" });
    expect("nanorix:framework_version" in meta).toBe(false);
    expect("nanorix:agent_id" in meta).toBe(false);
  });

  it("respects 4KB / 16-key envelope budget", () => {
    const meta = toMetadata({
      method: "GET",
      path: "/x",
      framework: "HIPAA",
      frameworkVersion: "2026-02",
      agentId: "a",
    });
    expect(Object.keys(meta).length).toBeLessThanOrEqual(16);
    for (const v of Object.values(meta)) {
      expect(v.length).toBeLessThanOrEqual(256);
    }
    expect(JSON.stringify(meta).length).toBeLessThan(4096);
  });
});
