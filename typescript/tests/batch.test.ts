/**
 * Order 2 / Gap 1 tests — Capsule.batch() Anthropic-Batches inline shape (TS).
 *
 * Mirrors Python tests/test_batch.py coverage. Pins the cross-implementation
 * Merkle root invariant (TS root === Python root === Rust root for same inputs).
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import {
  EMPTY_SHA512_HEX,
  merkleRootFromLeaves,
  combinedRecordLeaf,
} from "../src/merkle.js";
import {
  type BatchRecord,
  computeBatchMerkleRoot,
  parseResultsJsonl,
  serializeRecordsToJsonl,
} from "../src/batch.js";
import { FetchMock, TEST_API_KEY, TEST_BASE_URL } from "./setup.js";

function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

const subtle = (
  globalThis as unknown as { crypto: Crypto }
).crypto.subtle;

async function sha512Hex(data: Uint8Array): Promise<string> {
  return bytesToHex(await subtle.digest("SHA-512", data.buffer as ArrayBuffer));
}

// ── Merkle root cross-impl invariants ──

describe("Merkle (RFC 6962 binary tree)", () => {
  it("empty leaves returns genesis", async () => {
    expect(await merkleRootFromLeaves([])).toBe(EMPTY_SHA512_HEX);
  });

  it("single leaf applies 0x00 leaf prefix", async () => {
    const leaf = new Uint8Array(64).fill(0x61); // 'a'
    const expected = await sha512Hex(
      new Uint8Array([0x00, ...leaf]),
    );
    expect(await merkleRootFromLeaves([leaf])).toBe(expected);
  });

  it("two leaves apply 0x01 inner hash", async () => {
    const l0 = new Uint8Array(64).fill(0x61);
    const l1 = new Uint8Array(64).fill(0x62);
    const h0 = await subtle.digest(
      "SHA-512",
      new Uint8Array([0x00, ...l0]).buffer as ArrayBuffer,
    );
    const h1 = await subtle.digest(
      "SHA-512",
      new Uint8Array([0x00, ...l1]).buffer as ArrayBuffer,
    );
    const inner = new Uint8Array([
      0x01,
      ...new Uint8Array(h0),
      ...new Uint8Array(h1),
    ]);
    const expected = bytesToHex(
      await subtle.digest("SHA-512", inner.buffer as ArrayBuffer),
    );
    expect(await merkleRootFromLeaves([l0, l1])).toBe(expected);
  });

  it("odd count promotes lone tail (RFC 6962 §2.1)", async () => {
    const l0 = new Uint8Array(64).fill(0x61);
    const l1 = new Uint8Array(64).fill(0x62);
    const l2 = new Uint8Array(64).fill(0x63);
    // Build manually
    const h = async (bytes: Uint8Array) =>
      new Uint8Array(
        await subtle.digest("SHA-512", bytes.buffer as ArrayBuffer),
      );
    const h0 = await h(new Uint8Array([0x00, ...l0]));
    const h1 = await h(new Uint8Array([0x00, ...l1]));
    const h2 = await h(new Uint8Array([0x00, ...l2]));
    const inner01 = await h(new Uint8Array([0x01, ...h0, ...h1]));
    const expected = bytesToHex(
      await subtle.digest(
        "SHA-512",
        new Uint8Array([0x01, ...inner01, ...h2]).buffer as ArrayBuffer,
      ),
    );
    expect(await merkleRootFromLeaves([l0, l1, l2])).toBe(expected);
  });

  it("combinedRecordLeaf hashes input || output", async () => {
    const inp = new Uint8Array(64).fill(0x01);
    const out = new Uint8Array(64).fill(0x02);
    const expected = await sha512Hex(new Uint8Array([...inp, ...out]));
    expect(bytesToHex(await combinedRecordLeaf(inp, out))).toBe(expected);
  });

  it("Merkle root pinned to known vector — 4 leaves (Python + TS + Rust must agree)", async () => {
    // Cross-impl invariant pin: identical input → identical root across
    // Python sdk/python/tests/test_batch.py and Rust runtime
    // runtime/eee/src/daemon/streaming.rs::merkle_root_from_leaves.
    // If either implementation drifts, both this test + the Python +
    // Rust counterparts fail — protects against silent cross-impl
    // divergence that would break verifier replay.
    const leaves: Uint8Array[] = [
      new Uint8Array(64).fill(0x00),
      new Uint8Array(64).fill(0x11),
      new Uint8Array(64).fill(0x22),
      new Uint8Array(64).fill(0x33),
    ];
    const expected =
      "55e380af6c5f76695faf51db35f60a579acdde5ec9dca0f3bbe2be441525a66a" +
      "b48781258c8eca8a461ee4d162f501483cd6ee130fbeb1fdb4dbb2bacaec094b";
    expect(await merkleRootFromLeaves(leaves)).toBe(expected);
  });

  it("Merkle root pinned to known vector — 1 leaf (0x00 prefix applied)", async () => {
    const leaves = [new Uint8Array(64).fill(0x00)];
    const expected =
      "19dc6ae12de08b21b36c1ec7f353ce9e7cef73fa4d1354c436234167f0847bc9" +
      "e2b85e2f36208f773ef324e2d79e6af1beca4470e44b8672b47d077efe33a1f8";
    expect(await merkleRootFromLeaves(leaves)).toBe(expected);
  });

  it("computeBatchMerkleRoot orders by recordIdx", async () => {
    async function record(custom: string, idx: number): Promise<BatchRecord> {
      const inp = await sha512Hex(new TextEncoder().encode("in_" + custom));
      const out = await sha512Hex(new TextEncoder().encode("out_" + custom));
      return {
        customId: custom,
        recordInputHash: "sha512:" + inp,
        recordOutputHash: "sha512:" + out,
        status: "succeeded" as const,
        recordIdx: idx,
      };
    }
    const r0 = await record("a", 0);
    const r1 = await record("b", 1);
    const inOrder = await computeBatchMerkleRoot([r0, r1]);
    const reversed = await computeBatchMerkleRoot([r1, r0]);
    expect(inOrder).toBe(reversed);
  });
});

// ── Serialization validators ──

describe("serializeRecordsToJsonl", () => {
  it("rejects record without custom_id", () => {
    expect(() => serializeRecordsToJsonl([{ v: 1 }], "custom_id")).toThrow(
      /missing required/,
    );
  });

  it("rejects duplicate custom_id", () => {
    expect(() =>
      serializeRecordsToJsonl(
        [
          { custom_id: "a", v: 1 },
          { custom_id: "a", v: 2 },
        ],
        "custom_id",
      ),
    ).toThrow(/Duplicate/);
  });

  it("emits one JSONL line per record", () => {
    const out = serializeRecordsToJsonl(
      [
        { custom_id: "a", v: 1 },
        { custom_id: "b", v: 2 },
      ],
      "custom_id",
    );
    const lines = new TextDecoder().decode(out).trim().split("\n");
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0]).custom_id).toBe("a");
  });
});

// ── Results parsing ──

describe("parseResultsJsonl", () => {
  it("joins records.jsonl ↔ results.jsonl by custom_id", async () => {
    const recsJsonl = serializeRecordsToJsonl(
      [
        { custom_id: "p1", v: 1 },
        { custom_id: "p2", v: 2 },
      ],
      "custom_id",
    );
    const resultsLines = [
      JSON.stringify({
        custom_id: "p1",
        status: "succeeded",
        output: { x: 10 },
      }),
      JSON.stringify({
        custom_id: "p2",
        status: "errored",
        error_code: "E1",
      }),
    ].join("\n");
    const results = new TextEncoder().encode(resultsLines);
    const out = await parseResultsJsonl(results, recsJsonl);
    expect(out).toHaveLength(2);
    const byId = new Map(out.map((r) => [r.customId, r]));
    expect(byId.get("p1")!.status).toBe("succeeded");
    expect(byId.get("p1")!.output).toEqual({ x: 10 });
    expect(byId.get("p2")!.status).toBe("errored");
    expect(byId.get("p2")!.errorCode).toBe("E1");
  });

  it("synthesizes errored record for missing output", async () => {
    const recsJsonl = serializeRecordsToJsonl(
      [{ custom_id: "p1" }, { custom_id: "p2" }],
      "custom_id",
    );
    const results = new TextEncoder().encode(
      JSON.stringify({ custom_id: "p1", status: "succeeded" }),
    );
    const out = await parseResultsJsonl(results, recsJsonl);
    expect(out).toHaveLength(2);
    const p2 = out.find((r) => r.customId === "p2")!;
    expect(p2.status).toBe("errored");
    expect(p2.errorCode).toBe("MISSING_OUTPUT");
  });
});

// ── End-to-end Capsule.batch against mocked API ──

describe("Capsule.batch (e2e)", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    mock.install();
    client = new NanorixClient({ apiKey: TEST_API_KEY, baseUrl: TEST_BASE_URL });
  });

  afterEach(() => {
    mock.restore();
  });

  function setupHappyPath(resultsJsonlBody: Uint8Array | string) {
    mock.on("POST", "/v1/capsules", {
      status: 200,
      body: { id: "cap_test1", status: "running", customer_id: "cust_1" },
    });
    mock.on("POST", "/v1/capsules/cap_test1/upload", {
      status: 200,
      body: {
        filename: "records.jsonl",
        size_bytes: 100,
        sha256: "sha512:" + "a".repeat(128),
      },
    });
    mock.on("POST", "/v1/capsules/cap_test1/exec", {
      status: 200,
      body: { exit_code: 0, duration_seconds: 1, stdout: "", stderr: "" },
    });
    // Download endpoint: FetchMock returns JSON-stringified body; for binary
    // it uses the same JSON.stringify path. Since we're testing the SDK's
    // request, the mock body needs to be representable. Override after install
    // for this specific URL.
    mock.on("GET", "/v1/capsules/cap_test1/output/results.jsonl", {
      status: 200,
      body: resultsJsonlBody,
    });
    mock.on("DELETE", "/v1/capsules/cap_test1", {
      status: 200,
      body: {
        cdp: {
          capsule_id: "cap_test1",
          cdp_version: "1.0",
          chain: [],
          attestation: {},
        },
        verification_cdp: {},
      },
    });
  }

  it("happy path: 2 succeeded + 1 errored → BatchResult populated", async () => {
    const resultsJsonl = [
      JSON.stringify({
        custom_id: "p1",
        status: "succeeded",
        output: { x: 1 },
      }),
      JSON.stringify({
        custom_id: "p2",
        status: "succeeded",
        output: { x: 2 },
      }),
      JSON.stringify({ custom_id: "p3", status: "errored", error_code: "E" }),
    ].join("\n");
    setupHappyPath(resultsJsonl);

    const result = await client.capsules.batch({
      capsulefile: "cf_x",
      records: [
        { custom_id: "p1", v: 1 },
        { custom_id: "p2", v: 2 },
        { custom_id: "p3", v: 3 },
      ],
    });

    expect(result.successCount).toBe(2);
    expect(result.errorCount).toBe(1);
    expect(result.totalCount).toBe(3);
    expect(result.merkleRoot).toHaveLength(128);
    expect(result.find("p1")!.status).toBe("succeeded");
    expect(result.find("p3")!.errorCode).toBe("E");
    const failures = Array.from(result.iterFailures());
    expect(failures).toHaveLength(1);
    expect(failures[0].customId).toBe("p3");
  });

  it("rejects empty records", async () => {
    await expect(
      client.capsules.batch({ capsulefile: "cf_x", records: [] }),
    ).rejects.toThrow(/at least one record/);
  });

  it("rejects invalid onRecordError", async () => {
    await expect(
      client.capsules.batch({
        capsulefile: "cf_x",
        records: [{ custom_id: "p1" }],
        // @ts-expect-error testing invalid value
        onRecordError: "silently_continue",
      }),
    ).rejects.toThrow(/onRecordError/);
  });
});
