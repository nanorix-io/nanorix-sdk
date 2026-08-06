/**
 * Order 3 / Gap 2 tests — Capsule.session() LangGraph-shape memory artifact (TS).
 *
 * Mirrors Python tests/test_session.py coverage. Pins state-bytes hashing
 * + metadata binding + end-to-end session flow against mocked API.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import {
  hashStateBytes,
  buildSessionMetadata,
} from "../src/session.js";
import { FetchMock, TEST_API_KEY, TEST_BASE_URL } from "./setup.js";

const subtle = (
  globalThis as unknown as { crypto: Crypto }
).crypto.subtle;

function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

// ── State hashing ──

describe("hashStateBytes", () => {
  it("returns sha512: prefix + 128-char lowercase hex", async () => {
    const out = await hashStateBytes(new TextEncoder().encode("hello"));
    expect(out).toMatch(/^sha512:[0-9a-f]{128}$/);
    const expected =
      "sha512:" +
      bytesToHex(
        await subtle.digest("SHA-512", new TextEncoder().encode("hello").buffer as ArrayBuffer),
      );
    expect(out).toBe(expected);
  });

  it("empty state yields canonical SHA-512('') vector", async () => {
    const out = await hashStateBytes(new Uint8Array(0));
    expect(out).toBe(
      "sha512:cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e",
    );
  });
});

// ── Metadata binding ──

describe("buildSessionMetadata", () => {
  it("includes reserved nanorix: keys", () => {
    const md = buildSessionMetadata({
      sessionId: "agent-7f3a",
      parentCdpId: "ap_xK9aB",
      artifactInputHash: "sha512:abc",
    });
    expect(md["nanorix:session_id"]).toBe("agent-7f3a");
    expect(md["nanorix:parent_cdp_id"]).toBe("ap_xK9aB");
    expect(md["nanorix:artifact_input_hash"]).toBe("sha512:abc");
  });

  it("omits parent_cdp_id for first session in chain", () => {
    const md = buildSessionMetadata({
      sessionId: "agent-7f3a",
      artifactInputHash: "sha512:empty",
    });
    expect(md["nanorix:parent_cdp_id"]).toBeUndefined();
    expect(md["nanorix:session_id"]).toBe("agent-7f3a");
  });

  it("merges customer extra metadata", () => {
    const md = buildSessionMetadata({
      sessionId: "s1",
      artifactInputHash: "sha512:e",
      extra: { "agent:run_id": "r-001" },
    });
    expect(md["agent:run_id"]).toBe("r-001");
    expect(md["nanorix:session_id"]).toBe("s1");
  });
});

// ── End-to-end Capsule.session() against mocked API ──

describe("Capsule.session (e2e)", () => {
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

  function setupLifecycle(stateOutBytes: Uint8Array | string) {
    mock.on("POST", "/v1/capsules", {
      status: 200,
      body: { id: "cap_session1", status: "running", customer_id: "c1" },
    });
    mock.on("POST", "/v1/capsules/cap_session1/upload", {
      status: 200,
      body: {
        filename: "state.bin",
        size_bytes: 5,
        sha256: "sha512:" + "a".repeat(128),
      },
    });
    mock.on("POST", "/v1/capsules/cap_session1/exec", {
      status: 200,
      body: { exit_code: 0, duration_seconds: 1, stdout: "", stderr: "" },
    });
    mock.on("GET", "/v1/capsules/cap_session1/output/state.bin", {
      status: 200,
      body: stateOutBytes,
    });
    mock.on("DELETE", "/v1/capsules/cap_session1", {
      status: 200,
      body: {
        cdp: {
          capsule_id: "cap_session1",
          cdp_version: "1.0",
          chain: [],
          attestation: {},
        },
        verification_cdp: {},
      },
    });
  }

  it("first session in chain (no parent) computes hash chain correctly", async () => {
    const newState = new TextEncoder().encode("new state out");
    setupLifecycle(newState);

    const result = await client.capsules.session({
      capsulefile: "cf_research_agent",
      sessionId: "agent-7f3a",
    });

    expect(result.sessionId).toBe("agent-7f3a");
    // Empty initial → input hash is SHA-512('')
    expect(result.artifactInputHash).toBe(
      "sha512:cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e",
    );
    expect(result.artifactOutputHash).toBe(await hashStateBytes(newState));
  });

  it("chained session with parent + initial_state", async () => {
    const initial = new TextEncoder().encode("prior state");
    const next = new TextEncoder().encode("next state");
    setupLifecycle(next);

    const result = await client.capsules.session({
      capsulefile: "cf_x",
      sessionId: "agent-7f3a",
      parentCdpId: "ap_xK9aB",
      initialState: initial,
    });

    expect(result.artifactInputHash).toBe(await hashStateBytes(initial));
    expect(result.artifactOutputHash).toBe(await hashStateBytes(next));
  });

  it("rejects empty sessionId", async () => {
    await expect(
      client.capsules.session({
        capsulefile: "cf_x",
        sessionId: "",
      }),
    ).rejects.toThrow(/sessionId/);
  });
});
