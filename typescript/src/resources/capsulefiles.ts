/** Capsulefiles resource — create, list, get, delete, waitUntilFinalized. */
import * as fs from "node:fs";
import * as path from "node:path";

import type { Capsulefile, CapsulefileList } from "../models.js";
import type { HttpTransport } from "../http.js";
import { DEFAULT_UPLOAD_TIMEOUT } from "../constants.js";
import { NanorixError } from "../errors.js";

export class Capsulefiles {
  constructor(private readonly http: HttpTransport) {}

  /**
   * Create a capsulefile with files. Wraps the 3-step API flow:
   * 1. POST /v1/capsulefiles — create metadata
   * 2. POST /v1/capsulefiles/:id/files?filename= — upload each file (raw body)
   * 3. POST /v1/capsulefiles/:id/finalize — finalize
   *
   * `files` accepts:
   * - string[] — file paths on disk (reads and uploads each)
   * - Record<string, Buffer | Uint8Array> — filename → content bytes
   */
  async create(opts: {
    name: string;
    runtime: string;
    entrypoint: string;
    files: string[] | Record<string, Buffer | Uint8Array>;
    description?: string;
    labels?: Record<string, string>;
    env_vars?: Record<string, string>;
    capsule_memory_mb?: number;
    input_path?: string;
    output_path?: string;
  }): Promise<Capsulefile> {
    // Step 1: Create metadata
    const body: Record<string, unknown> = {
      name: opts.name,
      runtime: opts.runtime,
      entrypoint: opts.entrypoint,
    };
    if (opts.description !== undefined) body.description = opts.description;
    if (opts.labels !== undefined) body.labels = opts.labels;
    if (opts.env_vars !== undefined) body.env_vars = opts.env_vars;
    if (opts.capsule_memory_mb !== undefined)
      body.capsule_memory_mb = opts.capsule_memory_mb;
    if (opts.input_path !== undefined) body.input_path = opts.input_path;
    if (opts.output_path !== undefined) body.output_path = opts.output_path;

    const result = await this.http.request("POST", "/v1/capsulefiles", {
      json: body,
    });
    const capsulefileId = result.id as string;

    // Step 2: Upload each file (raw body, not multipart)
    const fileMap = Capsulefiles.resolveFiles(opts.files);
    for (const [filename, content] of Object.entries(fileMap)) {
      await this.http.request(
        "POST",
        `/v1/capsulefiles/${capsulefileId}/files`,
        {
          body: content instanceof Buffer ? content : Buffer.from(content),
          headers: { "Content-Type": "application/octet-stream" },
          params: { filename },
          timeout: DEFAULT_UPLOAD_TIMEOUT,
        },
      );
    }

    // Step 3: Finalize
    const finalized = await this.http.request(
      "POST",
      `/v1/capsulefiles/${capsulefileId}/finalize`,
    );
    return finalized as unknown as Capsulefile;
  }

  /** List capsulefiles. Supports ?status=finalized, ?cursor=, ?limit= query params. */
  async list(params?: Record<string, string>): Promise<CapsulefileList> {
    const result = await this.http.request("GET", "/v1/capsulefiles", {
      params,
    });
    return result as unknown as CapsulefileList;
  }

  /** Get a capsulefile by ID. */
  async get(capsulefileId: string): Promise<Capsulefile> {
    return (await this.http.request(
      "GET",
      `/v1/capsulefiles/${capsulefileId}`,
    )) as unknown as Capsulefile;
  }

  /** Delete a capsulefile (crypto-shreds encryption key, deletes files). */
  async delete(capsulefileId: string): Promise<void> {
    await this.http.request("DELETE", `/v1/capsulefiles/${capsulefileId}`);
  }

  /** Poll capsulefile status until 'finalized' or timeout. */
  async waitUntilFinalized(
    capsulefileId: string,
    opts?: { timeout?: number; pollInterval?: number },
  ): Promise<Capsulefile> {
    const timeout = opts?.timeout ?? 120_000;
    const pollInterval = opts?.pollInterval ?? 2_000;
    const deadline = Date.now() + timeout;

    while (Date.now() < deadline) {
      const cf = await this.get(capsulefileId);
      if (cf.status === "finalized") return cf;
      if (cf.status === "failed") {
        throw new NanorixError(
          `Capsulefile build failed: ${cf.error_message}`,
        );
      }
      await new Promise((r) => setTimeout(r, pollInterval));
    }
    throw new NanorixError(
      `Capsulefile ${capsulefileId} not finalized after ${timeout}ms`,
    );
  }

  /** Update a capsulefile. Not yet implemented. */
  async update(
    _capsulefileId: string,
    _opts?: Record<string, unknown>,
  ): Promise<never> {
    // Amendment 3: throw base Error, not NanorixError — matches Python's NotImplementedError
    throw new Error(
      "PUT /v1/capsulefiles/:id not yet implemented. Coming in v1.1.",
    );
  }

  /**
   * Upload all files in a directory (recursively) to a capsulefile.
   *
   * Walks the directory tree and uploads each file via
   * `POST /v1/capsulefiles/:id/files?filename=<relative_path>` with raw bytes.
   *
   * @param capsulefileId - The capsulefile to upload into.
   * @param dirPath - Path to the directory on disk.
   * @returns The number of files uploaded.
   */
  async uploadDir(capsulefileId: string, dirPath: string): Promise<number> {
    const entries = fs.readdirSync(dirPath, {
      recursive: true,
      withFileTypes: true,
    });
    let count = 0;
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      // entry.parentPath available in Node 20+; fall back to entry.path for Node 18
      const parentDir =
        (entry as unknown as { parentPath?: string }).parentPath ??
        (entry as unknown as { path?: string }).path ??
        dirPath;
      const fullPath = path.join(parentDir, entry.name);
      const relativePath = path.relative(dirPath, fullPath).split(path.sep).join("/");
      const content = fs.readFileSync(fullPath);
      await this.http.request(
        "POST",
        `/v1/capsulefiles/${capsulefileId}/files`,
        {
          body: content,
          headers: { "Content-Type": "application/octet-stream" },
          params: { filename: relativePath },
          timeout: DEFAULT_UPLOAD_TIMEOUT,
        },
      );
      count++;
    }
    return count;
  }

  /** Resolve files from path list or buffer map to a uniform Record<string, Buffer>. */
  private static resolveFiles(
    files: string[] | Record<string, Buffer | Uint8Array>,
  ): Record<string, Buffer | Uint8Array> {
    if (Array.isArray(files)) {
      const result: Record<string, Buffer> = {};
      for (const filePath of files) {
        result[path.basename(filePath)] = fs.readFileSync(filePath);
      }
      return result;
    }
    return files;
  }
}
