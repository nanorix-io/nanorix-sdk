import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { NanorixError } from "../src/errors.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

describe("Capsulefiles", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    client = new NanorixClient({ apiKey: TEST_API_KEY });
  });

  afterEach(() => {
    mock.restore();
  });

  it("create() executes 3-step flow with buffer map", async () => {
    // Step 1: POST /v1/capsulefiles → returns {id}
    mock.on("POST", "/v1/capsulefiles/cf_1/finalize", {
      status: 200,
      body: { id: "cf_1", name: "test", runtime: "python3.11", entrypoint: "python main.py", status: "draft" },
    });
    mock.on("POST", "/v1/capsulefiles/cf_1/files", {
      status: 200,
      body: { id: "file_1", filename: "main.py", size_bytes: 100, sha256: "abc" },
    });
    mock.on("POST", "/v1/capsulefiles", {
      status: 200,
      body: { id: "cf_1" },
    });

    mock.install();

    const capsulefile = await client.capsulefiles.create({
      name: "test",
      runtime: "python3.11",
      entrypoint: "python main.py",
      files: { "main.py": Buffer.from("print('hello')") },
    });

    expect(capsulefile.id).toBe("cf_1");
    // Verify 3 calls: create, upload, finalize
    expect(mock.calls).toHaveLength(3);
    expect(mock.calls[0].url).toContain("/v1/capsulefiles");
    expect(mock.calls[1].url).toContain("/v1/capsulefiles/cf_1/files");
    expect(mock.calls[1].url).toContain("filename=main.py");
    expect(mock.calls[2].url).toContain("/v1/capsulefiles/cf_1/finalize");
  });

  it("create() sends optional fields only when provided", async () => {
    mock.on("POST", "/v1/capsulefiles/cf_2/finalize", {
      status: 200,
      body: { id: "cf_2", name: "t2", runtime: "node18", entrypoint: "node index.js", status: "draft" },
    });
    mock.on("POST", "/v1/capsulefiles/cf_2/files", { status: 200, body: {} });
    mock.on("POST", "/v1/capsulefiles", { status: 200, body: { id: "cf_2" } });

    mock.install();

    await client.capsulefiles.create({
      name: "t2",
      runtime: "node18",
      entrypoint: "node index.js",
      files: { "index.js": Buffer.from("console.log()") },
      description: "desc",
      labels: { env: "test" },
    });

    const createBody = mock.calls[0].body as Record<string, unknown>;
    expect(createBody.description).toBe("desc");
    expect(createBody.labels).toEqual({ env: "test" });
    // Fields not provided should not be in body
    expect(createBody.env_vars).toBeUndefined();
    expect(createBody.capsule_memory_mb).toBeUndefined();
  });

  it("list() returns CapsulefileList", async () => {
    mock.on("GET", "/v1/capsulefiles", {
      status: 200,
      body: { capsulefiles: [{ id: "cf_1", name: "t", runtime: "py", entrypoint: "py" }], has_more: false },
    });
    mock.install();

    const result = await client.capsulefiles.list();
    expect(result.capsulefiles).toHaveLength(1);
    expect(result.capsulefiles[0].id).toBe("cf_1");
  });

  it("get() returns Capsulefile", async () => {
    mock.on("GET", "/v1/capsulefiles/cf_1", {
      status: 200,
      body: { id: "cf_1", name: "t", runtime: "py", entrypoint: "py", status: "finalized" },
    });
    mock.install();

    const cf = await client.capsulefiles.get("cf_1");
    expect(cf.status).toBe("finalized");
  });

  it("delete() calls DELETE", async () => {
    mock.on("DELETE", "/v1/capsulefiles/cf_1", { status: 204 });
    mock.install();

    await client.capsulefiles.delete("cf_1");
    expect(mock.calls[0].method).toBe("DELETE");
  });

  it("waitUntilFinalized() polls until finalized", async () => {
    let callCount = 0;
    mock.restore();
    globalThis.fetch = async (): Promise<Response> => {
      callCount++;
      const status = callCount >= 2 ? "finalized" : "draft";
      return new Response(
        JSON.stringify({ id: "cf_1", name: "t", runtime: "py", entrypoint: "py", status }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    };

    const cf = await client.capsulefiles.waitUntilFinalized("cf_1", {
      pollInterval: 10,
      timeout: 5_000,
    });
    expect(cf.status).toBe("finalized");
    expect(callCount).toBeGreaterThanOrEqual(2);
  });

  it("update() throws base Error (not NanorixError)", async () => {
    mock.install();
    try {
      await client.capsulefiles.update("cf_1");
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(Error);
      expect(err).not.toBeInstanceOf(NanorixError);
      expect((err as Error).message).toContain("not yet implemented");
    }
  });

  it("uploadDir() recursively uploads all files with relative paths", async () => {
    // Create temp directory with nested files
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "nanorix-test-"));
    try {
      fs.writeFileSync(path.join(tmpDir, "main.py"), "print('hello')");
      fs.mkdirSync(path.join(tmpDir, "data"));
      fs.writeFileSync(path.join(tmpDir, "data", "train.csv"), "a,b,c");
      fs.mkdirSync(path.join(tmpDir, "data", "nested"));
      fs.writeFileSync(
        path.join(tmpDir, "data", "nested", "config.json"),
        '{"lr": 0.01}',
      );

      mock.on("POST", "/v1/capsulefiles/cf_dir/files", {
        status: 200,
        body: { filename: "ok", size_bytes: 10, sha256: "abc" },
      });
      mock.install();

      const count = await client.capsulefiles.uploadDir("cf_dir", tmpDir);

      expect(count).toBe(3);

      // Extract uploaded filenames from query params
      const uploadCalls = mock.calls.filter(
        (c) => c.method === "POST" && c.url.includes("/files"),
      );
      expect(uploadCalls).toHaveLength(3);

      const filenames = uploadCalls
        .map((c) => {
          const url = new URL(c.url);
          return url.searchParams.get("filename");
        })
        .sort();

      expect(filenames).toEqual([
        "data/nested/config.json",
        "data/train.csv",
        "main.py",
      ]);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
