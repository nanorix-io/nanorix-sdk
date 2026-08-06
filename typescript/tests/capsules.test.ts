import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { ExecutionError } from "../src/errors.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

const MOCK_CAPSULE = {
  id: "cap_1",
  status: "active",
  customer_id: "cust_1",
  capsulefile_id: "cf_1",
  created_at: "2026-03-25T00:00:00Z",
};

const MOCK_CDP = {
  cdp: {
    version: "2.0",
    capsule_id: "cap_1",
    destruction: { trigger: "user", chain_hash: "sha256:abc" },
    attestation: { algorithm: "Ed25519", public_key: "base64:pk", signature: "base64:sig" },
  },
  verification_cdp: {
    version: "2.0",
    capsule_id: "cap_1",
    verify: "https://nanorix.io/verify/cap_1",
  },
};

describe("Capsules", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    client = new NanorixClient({ apiKey: TEST_API_KEY });
  });

  afterEach(() => {
    mock.restore();
  });

  it("create() returns LiveCapsule", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.install();

    const capsule = await client.capsules.create();
    expect(capsule.id).toBe("cap_1");
    expect(capsule.status).toBe("active");
  });

  it("create() sends capsulefile_id when capsulefile provided", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.install();

    await client.capsules.create({ capsulefile: "cf_1" });
    const body = mock.calls[0].body as Record<string, unknown>;
    expect(body.capsulefile_id).toBe("cf_1");
  });

  it("upload() sends raw body with filename param", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.on("POST", "/v1/capsules/cap_1/upload", {
      status: 200,
      body: { file_id: "f1", filename: "data.csv", size_bytes: 5, sha256: "abc" },
    });
    mock.install();

    const capsule = await client.capsules.create();
    const file = await capsule.upload("data.csv", Buffer.from("hello"));

    expect(file.filename).toBe("data.csv");
    expect(mock.calls[1].url).toContain("filename=data.csv");
  });

  it("execute() success returns result", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.on("POST", "/v1/capsules/cap_1/exec", {
      status: 200,
      body: { stdout: "hello\n", stderr: "", exit_code: 0, duration_seconds: 1.2 },
    });
    mock.install();

    const capsule = await client.capsules.create();
    const result = await capsule.execute("echo hello");
    expect(result.exit_code).toBe(0);
    expect(result.stdout).toBe("hello\n");
  });

  it("execute() non-zero exit throws ExecutionError", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.on("POST", "/v1/capsules/cap_1/exec", {
      status: 200,
      body: { stdout: "", stderr: "error\n", exit_code: 1 },
    });
    mock.install();

    const capsule = await client.capsules.create();
    try {
      await capsule.execute("bad_cmd");
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ExecutionError);
      const e = err as ExecutionError;
      expect(e.exitCode).toBe(1);
      expect(e.stderr).toBe("error\n");
    }
  });

  it("download() returns Buffer", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.restore();

    // Need custom mock for raw response
    let callIdx = 0;
    globalThis.fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      callIdx++;
      if (callIdx === 1) {
        // capsule start
        return new Response(JSON.stringify(MOCK_CAPSULE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      // download
      return new Response("file content", {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      });
    };

    const capsule = await client.capsules.create();
    const data = await capsule.download("result.json");
    expect(Buffer.isBuffer(data)).toBe(true);
  });

  it("listOutputs() returns FileInfo array", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.on("GET", "/v1/capsules/cap_1/output", {
      status: 200,
      body: { files: [{ filename: "out.csv", size_bytes: 100, sha256: "abc" }] },
    });
    mock.install();

    const capsule = await client.capsules.create();
    const files = await capsule.listOutputs();
    expect(files).toHaveLength(1);
    expect(files[0].filename).toBe("out.csv");
  });

  it("destroy() returns CdpAccess", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.on("DELETE", "/v1/capsules/cap_1", { status: 200, body: MOCK_CDP });
    mock.install();

    const capsule = await client.capsules.create();
    const cdp = await capsule.destroy();
    expect(cdp.id).toBe("cap_1");
    expect(cdp.full().destruction?.chain_hash).toBe("sha256:abc");
    expect(cdp.verification().verify).toBe("https://nanorix.io/verify/cap_1");
  });

  it("run() chains full lifecycle", async () => {
    mock.restore();
    let callIdx = 0;
    const responses = [
      // start
      { body: MOCK_CAPSULE, status: 200 },
      // upload
      { body: { file_id: "f1", filename: "in.csv", size_bytes: 5, sha256: "abc" }, status: 200 },
      // execute
      { body: { stdout: "done", stderr: "", exit_code: 0 }, status: 200 },
      // list outputs
      { body: { files: [{ filename: "out.csv", size_bytes: 10, sha256: "def" }] }, status: 200 },
      // download
      { body: "output data", status: 200 },
      // destroy
      { body: MOCK_CDP, status: 200 },
    ];

    globalThis.fetch = async (): Promise<Response> => {
      const resp = responses[callIdx++];
      const body = typeof resp.body === "string" ? resp.body : JSON.stringify(resp.body);
      return new Response(body, {
        status: resp.status,
        headers: { "Content-Type": "application/json" },
      });
    };

    const [outputs, cdp] = await client.capsules.run({
      capsulefile: "cf_1",
      inputs: { "in.csv": Buffer.from("data") },
      command: "process",
    });

    expect(Object.keys(outputs)).toContain("out.csv");
    expect(cdp.id).toBe("cap_1");
    expect(callIdx).toBe(6); // All 6 steps called
  });

  it("run() with ExecutionError still destroys and attaches CDP", async () => {
    mock.restore();
    let callIdx = 0;
    const responses = [
      // start
      { body: MOCK_CAPSULE, status: 200 },
      // execute (fails)
      { body: { stdout: "", stderr: "fail", exit_code: 1 }, status: 200 },
      // destroy (cleanup)
      { body: MOCK_CDP, status: 200 },
    ];

    globalThis.fetch = async (): Promise<Response> => {
      const resp = responses[callIdx++];
      return new Response(JSON.stringify(resp.body), {
        status: resp.status,
        headers: { "Content-Type": "application/json" },
      });
    };

    try {
      await client.capsules.run({ capsulefile: "cf_1", command: "fail" });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ExecutionError);
      const e = err as ExecutionError;
      expect(e.exitCode).toBe(1);
      expect(e.cdp).toBeDefined();
      expect(e.cdp?.capsule_id).toBe("cap_1");
    }
    // Verify destroy was called (3 total calls: start, exec, destroy)
    expect(callIdx).toBe(3);
  });
});
