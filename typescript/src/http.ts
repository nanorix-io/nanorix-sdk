/**
 * HTTP transport layer using native fetch (Node 18+).
 *
 * Handles:
 * - Bearer token auth on every request
 * - User-Agent header
 * - Retry with exponential backoff on 429 and 5xx
 * - JSON parsing with error raising
 * - Raw body upload for files (not multipart)
 * - Raw byte download for output files
 *
 * API endpoints (VERIFIED against Rust handlers):
 *   POST   /v1/capsulefiles                      -> Create capsulefile metadata
 *   POST   /v1/capsulefiles/:id/files?filename=  -> Upload capsulefile file (raw body)
 *   POST   /v1/capsulefiles/:id/finalize         -> Finalize capsulefile
 *   GET    /v1/capsulefiles                      -> List capsulefiles
 *   GET    /v1/capsulefiles/:id                  -> Get capsulefile
 *   DELETE /v1/capsulefiles/:id                  -> Delete capsulefile
 *   POST   /v1/capsules                       -> Create capsule
 *   POST   /v1/capsules/:id/upload?filename=  -> Upload file to capsule (raw body)
 *   POST   /v1/capsules/:id/exec              -> Execute command
 *   GET    /v1/capsules/:id/output            -> List output files
 *   GET    /v1/capsules/:id/output/:filename  -> Download output file
 *   DELETE /v1/capsules/:id                   -> Destroy capsule -> returns CDP
 *   GET    /v1/capsules/:id/cdp               -> AuditRecord (auth required)
 *   GET    /v1/capsules/:id/cdp/verify        -> AuditProof (public)
 *   GET    /v1/capsules                       -> List capsules
 *   GET    /v1/capsules/:id                   -> Get capsule
 *   GET    /v1/health                         -> Health check
 */
import {
  DEFAULT_TIMEOUT,
  MAX_RETRIES,
  RETRY_STATUS_CODES,
  USER_AGENT,
} from "./constants.js";
import { NanorixError, raiseForStatus } from "./errors.js";

export interface RequestOptions {
  json?: Record<string, unknown>;
  body?: Uint8Array | Buffer;
  headers?: Record<string, string>;
  params?: Record<string, string>;
  timeout?: number;
}

export class HttpTransport {
  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly timeout: number;
  private readonly maxRetries: number;

  constructor(opts: {
    apiKey: string;
    baseUrl: string;
    timeout: number;
    maxRetries: number;
  }) {
    this.apiKey = opts.apiKey;
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.timeout = opts.timeout;
    this.maxRetries = opts.maxRetries;
  }

  /** Make an HTTP request, parse JSON response. */
  async request(
    method: string,
    path: string,
    opts: RequestOptions = {},
  ): Promise<Record<string, unknown>> {
    const response = await this.fetchWithRetry(method, path, opts);

    // 204 No Content
    if (response.status === 204) return {};

    let body: Record<string, unknown>;
    try {
      body = (await response.json()) as Record<string, unknown>;
    } catch {
      body = { error: await response.text() };
    }
    raiseForStatus(response.status, body);
    return body;
  }

  /** Make an HTTP request, return raw bytes. For file downloads. */
  async requestRaw(
    method: string,
    path: string,
    opts: RequestOptions = {},
  ): Promise<Buffer> {
    const response = await this.fetchWithRetry(method, path, opts);

    if (response.status >= 400) {
      let body: Record<string, unknown>;
      try {
        body = (await response.json()) as Record<string, unknown>;
      } catch {
        body = { error: await response.text() };
      }
      raiseForStatus(response.status, body);
    }
    const buffer = await response.arrayBuffer();
    return Buffer.from(buffer);
  }

  /** Core fetch with retry, backoff, timeout, and auth. */
  private async fetchWithRetry(
    method: string,
    path: string,
    opts: RequestOptions,
  ): Promise<Response> {
    let url = `${this.baseUrl}${path}`;
    if (opts.params !== undefined) {
      const qs = new URLSearchParams(opts.params).toString();
      url += `?${qs}`;
    }

    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "User-Agent": USER_AGENT,
      ...(opts.headers ?? {}),
    };

    let fetchBody: BodyInit | undefined;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchBody = JSON.stringify(opts.json);
    } else if (opts.body !== undefined) {
      headers["Content-Type"] =
        headers["Content-Type"] ?? "application/octet-stream";
      fetchBody =
        opts.body instanceof Buffer ? opts.body : Buffer.from(opts.body);
    }

    const requestTimeout = opts.timeout ?? this.timeout;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), requestTimeout);

      try {
        const response = await fetch(url, {
          method,
          headers,
          body: fetchBody,
          signal: controller.signal,
        });

        clearTimeout(timer);

        if (
          RETRY_STATUS_CODES.has(response.status) &&
          attempt < this.maxRetries
        ) {
          await this.backoff(attempt, response);
          continue;
        }

        return response;
      } catch (err) {
        clearTimeout(timer);
        if (err instanceof NanorixError) throw err;
        if ((err as Error).name === "AbortError") {
          if (attempt < this.maxRetries) {
            await this.backoff(attempt);
            continue;
          }
          throw new NanorixError(
            `Request timed out after ${requestTimeout}ms`,
          );
        }
        if (attempt < this.maxRetries) {
          await this.backoff(attempt);
          continue;
        }
        throw new NanorixError(`HTTP error: ${(err as Error).message}`);
      }
    }

    throw new NanorixError("Max retries exceeded");
  }

  private async backoff(
    attempt: number,
    response?: Response,
  ): Promise<void> {
    if (response !== undefined && response.status === 429) {
      const retryAfter = response.headers.get("Retry-After");
      if (retryAfter !== null) {
        await this.sleep(parseFloat(retryAfter) * 1000);
        return;
      }
    }
    const base = 500 * Math.pow(2, attempt); // 500ms, 1000ms, 2000ms
    const jitter = Math.random() * base * 0.5;
    await this.sleep(base + jitter);
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
