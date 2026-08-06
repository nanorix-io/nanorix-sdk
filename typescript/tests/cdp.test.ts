import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

const MOCK_FULL_CDP = {
  version: "2.0",
  capsule_id: "cap_1",
  activity: [{ event: "created", at: "2026-03-25T00:00:00Z" }],
  destruction: { trigger: "user", chain_hash: "sha256:abc" },
  attestation: { algorithm: "Ed25519", public_key: "base64:pk", signature: "base64:sig" },
  document_hash: "sha256:docHash",
};

const MOCK_VERIFY_CDP = {
  version: "2.0",
  capsule_id: "cap_1",
  environment: { runtime: "python3.11" },
  residency: { started: "2026-03-25T00:00:00Z", destroyed: "2026-03-25T00:01:00Z", total_seconds: 60 },
  destruction: { trigger: "user", chain_hash: "sha256:abc" },
  attestation: { algorithm: "Ed25519" },
  verify: "https://nanorix.io/verify/cap_1",
};

describe("CdpResource", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    client = new NanorixClient({ apiKey: TEST_API_KEY });
  });

  afterEach(() => {
    mock.restore();
  });

  it("get() returns CdpAccess with both layers", async () => {
    mock.on("GET", "/v1/capsules/cap_1/cdp/verify", { status: 200, body: MOCK_VERIFY_CDP });
    mock.on("GET", "/v1/capsules/cap_1/cdp", { status: 200, body: MOCK_FULL_CDP });
    mock.install();

    const access = await client.cdp.get("cap_1");
    expect(access.id).toBe("cap_1");

    const full = access.full();
    expect(full.capsule_id).toBe("cap_1");
    expect(full.activity).toHaveLength(1);
    expect(full.destruction?.chain_hash).toBe("sha256:abc");

    const verify = access.verification();
    expect(verify.capsule_id).toBe("cap_1");
    expect(verify.environment?.runtime).toBe("python3.11");
  });

  it("verify() returns VerificationCdp", async () => {
    mock.on("GET", "/v1/capsules/cap_1/cdp/verify", { status: 200, body: MOCK_VERIFY_CDP });
    mock.install();

    const verify = await client.cdp.verify("cap_1");
    expect(verify.capsule_id).toBe("cap_1");
    expect(verify.verify).toBe("https://nanorix.io/verify/cap_1");
  });

  it("verifyUrl returns URL from verification CDP", async () => {
    mock.on("GET", "/v1/capsules/cap_1/cdp/verify", { status: 200, body: MOCK_VERIFY_CDP });
    mock.on("GET", "/v1/capsules/cap_1/cdp", { status: 200, body: MOCK_FULL_CDP });
    mock.install();

    const access = await client.cdp.get("cap_1");
    expect(access.verifyUrl).toBe("https://nanorix.io/verify/cap_1");
  });

  it("toDict() returns raw full CDP", async () => {
    mock.on("GET", "/v1/capsules/cap_1/cdp/verify", { status: 200, body: MOCK_VERIFY_CDP });
    mock.on("GET", "/v1/capsules/cap_1/cdp", { status: 200, body: MOCK_FULL_CDP });
    mock.install();

    const access = await client.cdp.get("cap_1");
    const raw = access.toDict();
    expect(raw.capsule_id).toBe("cap_1");
    expect(raw.document_hash).toBe("sha256:docHash");
  });
});
