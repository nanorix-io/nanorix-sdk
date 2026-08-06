import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

describe("NanorixClient", () => {
  const mock = new FetchMock();

  beforeEach(() => {
    mock.reset();
    mock.install();
  });

  afterEach(() => {
    mock.restore();
  });

  it("accepts valid live key", () => {
    expect(() => new NanorixClient({ apiKey: "nrx_live_abc1234567890abc" })).not.toThrow();
  });

  it("accepts valid test key", () => {
    expect(() => new NanorixClient({ apiKey: TEST_API_KEY })).not.toThrow();
  });

  it("rejects invalid key", () => {
    expect(() => new NanorixClient({ apiKey: "invalid_key" })).toThrow(
      "Invalid API key",
    );
  });

  it("rejects empty key", () => {
    expect(() => new NanorixClient({ apiKey: "" })).toThrow(
      "Invalid API key",
    );
  });

  it("rejects too-short key", () => {
    expect(() => new NanorixClient({ apiKey: "nrx_live_short" })).toThrow(
      "Invalid API key",
    );
  });

  it("rejects HTTP baseUrl (non-localhost)", () => {
    expect(
      () =>
        new NanorixClient({
          apiKey: TEST_API_KEY,
          baseUrl: "http://api.nanorix.io",
        }),
    ).toThrow("must use HTTPS");
  });

  it("allows HTTP for localhost dev", () => {
    expect(
      () =>
        new NanorixClient({
          apiKey: TEST_API_KEY,
          baseUrl: "http://localhost:3000",
        }),
    ).not.toThrow();
  });

  it("allows HTTP for 127.0.0.1 dev", () => {
    expect(
      () =>
        new NanorixClient({
          apiKey: TEST_API_KEY,
          baseUrl: "http://127.0.0.1:3000",
        }),
    ).not.toThrow();
  });

  it("health() calls GET /v1/health", async () => {
    mock.on("GET", "/v1/health", {
      status: 200,
      body: { status: "ok", engines: { eee: true } },
    });

    const client = new NanorixClient({ apiKey: TEST_API_KEY });
    const result = await client.health();
    expect(result.status).toBe("ok");
    expect(mock.calls).toHaveLength(1);
    expect(mock.calls[0].url).toContain("/v1/health");
  });
});
