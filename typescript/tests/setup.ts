/**
 * Simple fetch mock helper for tests.
 * Register mock responses, install to intercept globalThis.fetch, restore when done.
 */
export interface MockResponse {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}

interface MockEntry {
  method: string;
  url: string | RegExp;
  response: MockResponse;
}

interface MockCall {
  method: string;
  url: string;
  body?: unknown;
  headers?: Record<string, string>;
}

export class FetchMock {
  private responses: MockEntry[] = [];
  private _calls: MockCall[] = [];
  private originalFetch: typeof fetch;

  constructor() {
    this.originalFetch = globalThis.fetch;
  }

  /** Register a mock response for a method + URL pattern. More specific patterns should be registered first. */
  on(
    method: string,
    url: string | RegExp,
    response: MockResponse,
  ): this {
    this.responses.push({ method, url, response });
    return this;
  }

  /** Install the mock — replaces globalThis.fetch. */
  install(): void {
    globalThis.fetch = async (
      input: RequestInfo | URL,
      init?: RequestInit,
    ): Promise<Response> => {
      const requestUrl =
        typeof input === "string" ? input : input.toString();
      const requestMethod = init?.method ?? "GET";

      let body: unknown;
      if (init?.body !== undefined && init.body !== null) {
        try {
          body = JSON.parse(init.body as string);
        } catch {
          body = init.body;
        }
      }

      const headers: Record<string, string> = {};
      if (init?.headers !== undefined) {
        const h = init.headers as Record<string, string>;
        for (const [k, v] of Object.entries(h)) {
          headers[k] = v;
        }
      }

      this._calls.push({ method: requestMethod, url: requestUrl, body, headers });

      // Find best match: prefer longest string match to avoid /v1/capsules matching /v1/capsules/cap_1/exec
      const candidates = this.responses.filter(
        (r) =>
          r.method === requestMethod &&
          (typeof r.url === "string"
            ? requestUrl.includes(r.url)
            : r.url.test(requestUrl)),
      );
      // Sort by specificity: longest string URL first
      candidates.sort((a, b) => {
        const aLen = typeof a.url === "string" ? a.url.length : 0;
        const bLen = typeof b.url === "string" ? b.url.length : 0;
        return bLen - aLen;
      });
      const match = candidates[0];

      if (match === undefined) {
        return new Response(
          JSON.stringify({ error: `No mock matched: ${requestMethod} ${requestUrl}` }),
          { status: 500, headers: { "Content-Type": "application/json" } },
        );
      }

      // Body may be: parsed JSON object (default), raw string (passthrough),
      // or Uint8Array (raw bytes). String + Uint8Array bypass JSON.stringify
      // so callers can return text/csv, JSONL streams, etc. without double-
      // encoding.
      let responseBody: BodyInit | null;
      if (match.response.body === undefined) {
        responseBody = null;
      } else if (typeof match.response.body === "string") {
        responseBody = match.response.body;
      } else if (match.response.body instanceof Uint8Array) {
        responseBody = match.response.body as BodyInit;
      } else {
        responseBody = JSON.stringify(match.response.body);
      }

      return new Response(responseBody, {
        status: match.response.status,
        headers: {
          "Content-Type": "application/json",
          ...(match.response.headers ?? {}),
        },
      });
    };
  }

  /** Restore original fetch. */
  restore(): void {
    globalThis.fetch = this.originalFetch;
  }

  /** Get all recorded calls. */
  get calls(): MockCall[] {
    return this._calls;
  }

  /** Reset mocks and call history. */
  reset(): void {
    this.responses = [];
    this._calls = [];
  }
}

export const TEST_API_KEY = "nrx_test_sk_1234567890abcdef";
export const TEST_BASE_URL = "https://api.nanorix.io";
