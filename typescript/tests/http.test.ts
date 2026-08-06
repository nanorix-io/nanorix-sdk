import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { HttpTransport } from "../src/http.js";
import { VERSION } from "../src/constants.js";
import { FetchMock, TEST_API_KEY, TEST_BASE_URL } from "./setup.js";

describe("HttpTransport", () => {
  const mock = new FetchMock();

  function makeTransport(): HttpTransport {
    return new HttpTransport({
      apiKey: TEST_API_KEY,
      baseUrl: TEST_BASE_URL,
      timeout: 30_000,
      maxRetries: 3,
    });
  }

  beforeEach(() => {
    mock.reset();
    mock.install();
  });

  afterEach(() => {
    mock.restore();
  });

  it("sends Authorization header", async () => {
    mock.on("GET", "/v1/health", { status: 200, body: { ok: true } });

    const transport = makeTransport();
    await transport.request("GET", "/v1/health");

    expect(mock.calls).toHaveLength(1);
    expect(mock.calls[0].headers?.Authorization).toBe(
      `Bearer ${TEST_API_KEY}`,
    );
  });

  it("sends User-Agent header", async () => {
    mock.on("GET", "/v1/health", { status: 200, body: { ok: true } });

    const transport = makeTransport();
    await transport.request("GET", "/v1/health");

    // Reference VERSION dynamically so future bumps don't drift the
    // assertion. USER_AGENT in constants.ts is `nanorix-node/${VERSION}`.
    expect(mock.calls[0].headers?.["User-Agent"]).toBe(`nanorix-node/${VERSION}`);
  });

  it("retries on 429 then succeeds", async () => {
    // First call returns 429, second returns 200
    let callCount = 0;
    const originalInstall = mock.install.bind(mock);

    // Override fetch directly for this test
    mock.restore();
    const calls: string[] = [];
    globalThis.fetch = async (
      input: RequestInfo | URL,
      _init?: RequestInit,
    ): Promise<Response> => {
      calls.push(typeof input === "string" ? input : input.toString());
      callCount++;
      if (callCount === 1) {
        return new Response(JSON.stringify({ error: "rate limited" }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "0.01" },
        });
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    const transport = makeTransport();
    const result = await transport.request("GET", "/v1/health");

    expect(result).toEqual({ ok: true });
    expect(callCount).toBe(2);
  });
});
