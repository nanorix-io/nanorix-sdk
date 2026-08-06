/** Capsules resource — LiveCapsule workspace + Capsules CRUD + .run() one-shot. */
import { randomUUID } from "node:crypto";
import type {
  Capsule,
  CapsuleList,
  ExecutionResult,
  FileInfo,
} from "../models.js";
import { CdpAccess } from "../models.js";
import type { HttpTransport } from "../http.js";
import {
  DEFAULT_UPLOAD_TIMEOUT,
  DEFAULT_EXECUTE_TIMEOUT,
} from "../constants.js";
import { ExecutionError } from "../errors.js";
import {
  type BatchRecord,
  type BatchResult,
  computeBatchMerkleRoot,
  parseResultsJsonl,
  serializeRecordsToJsonl,
} from "../batch.js";
import {
  type SessionResult,
  buildSessionMetadata,
  hashStateBytes,
} from "../session.js";
import {
  type ParentProofOptions,
  RecordContext,
  type RecordOptions,
  buildParentLinkBody,
  validateParentLinkFields,
  withRecordContext,
} from "../capsule_record.js";

/** A running capsule with workspace methods. Returned by client.capsules.create(). */
export class LiveCapsule {
  private _capsule: Capsule;
  private readonly _http: HttpTransport;

  constructor(capsule: Capsule, http: HttpTransport) {
    this._capsule = capsule;
    this._http = http;
  }

  get id(): string {
    return this._capsule.id;
  }

  get status(): string {
    return this._capsule.status;
  }

  /** Upload a file to the capsule's input directory. */
  async upload(
    filename: string,
    data: Buffer | Uint8Array,
  ): Promise<FileInfo> {
    const result = await this._http.request(
      "POST",
      `/v1/capsules/${this.id}/upload`,
      {
        body: data instanceof Buffer ? data : Buffer.from(data),
        headers: { "Content-Type": "application/octet-stream" },
        params: { filename },
        timeout: DEFAULT_UPLOAD_TIMEOUT,
      },
    );
    return result as unknown as FileInfo;
  }

  /**
   * Execute a command in the capsule.
   * If no command provided, uses the capsulefile entrypoint.
   */
  async execute(
    command?: string,
    opts?: { timeout?: number },
  ): Promise<ExecutionResult> {
    const body: Record<string, unknown> = {};
    if (command !== undefined) body.command = command;
    if (opts?.timeout !== undefined) body.timeout = opts.timeout;

    const result = await this._http.request(
      "POST",
      `/v1/capsules/${this.id}/exec`,
      {
        json: Object.keys(body).length > 0 ? body : undefined,
        timeout: DEFAULT_EXECUTE_TIMEOUT,
      },
    );

    const execResult = result as unknown as ExecutionResult;
    if (execResult.exit_code !== 0) {
      throw new ExecutionError(
        `Command exited with code ${execResult.exit_code}`,
        {
          exitCode: execResult.exit_code,
          stdout: execResult.stdout,
          stderr: execResult.stderr,
        },
      );
    }
    return execResult;
  }

  /** Download an output file from the capsule. */
  async download(filename: string): Promise<Buffer> {
    return await this._http.requestRaw(
      "GET",
      `/v1/capsules/${this.id}/output/${filename}`,
    );
  }

  /** List all output files in the capsule. */
  async listOutputs(): Promise<FileInfo[]> {
    const result = await this._http.request(
      "GET",
      `/v1/capsules/${this.id}/output`,
    );
    const filesData =
      (result as Record<string, unknown>).files ?? result;
    return (Array.isArray(filesData) ? filesData : []) as FileInfo[];
  }

  /**
   * Destroy the capsule and return the CDP.
   * After this call, the capsule is gone. Only the proof remains.
   */
  async destroy(): Promise<CdpAccess> {
    const result = await this._http.request(
      "DELETE",
      `/v1/capsules/${this.id}`,
    );
    return new CdpAccess(
      (result as Record<string, unknown>).cdp as Record<string, unknown> ??
        result,
      ((result as Record<string, unknown>).verification_cdp as Record<
        string,
        unknown
      >) ?? {},
      this.id,
    );
  }

  /** Refresh capsule status from API. */
  async refresh(): Promise<this> {
    this._capsule = (await this._http.request(
      "GET",
      `/v1/capsules/${this.id}`,
    )) as unknown as Capsule;
    return this;
  }

  // ── consumers ─────────────────────────────

  /**
   * Open a per-record receipt context (section "Architecture").
   *
   * Per-record receipts buffer server-side until destroy and bind into
   * the AuditProof's outer 8-step chain via Merkle root incorporation
   * at Step 8 (the canonical verifier).
   *
   * The receipt carries `patternTag` (signed primitive). **Control IDs
   * are NEVER stamped into the signed receipt.** Adapters apply the
   * published mapping artifact (`./control_map`) at ingestion
   * time to derive `(jurisdiction x classification x pattern_tag) ->
   * string[]`. Three-layer separation: receipt (immutable) / mapping
   * (versioned) / adapter (vendor pace).
   *
   * @param opts.recordId Customer-declared opaque identifier (PA ID,
   *   claim ID, study UID, molecule ID). Length cap: 256 chars.
   * @param opts.patternTag Optional closed-enum workflow tag (15 variants).
   * @returns A `RecordContext`. Buffer bind/event calls, then `await
   *   ctx.commit()` to POST. For the auto-commit / auto-abort callback
   *   sugar use `capsule.withRecord(opts, async (r) => { ... })`.
   *
   * @example
   *   const ctx = capsule.record({ recordId: "pa_123", patternTag: "pa" });
   *   await ctx.bindOutput(result);
   *   ctx.event({ type: "ReasoningStepCompleted", step_id: "ddx" });
   *   await ctx.commit();
   */
  record(opts: RecordOptions): RecordContext {
    return new RecordContext(
      this.id,
      opts.recordId,
      opts.patternTag,
      this._postRecord.bind(this),
    );
  }

  /**
   * Callback-sugar wrapper around `record(opts)`. Auto-commits on resolve,
   * auto-aborts on throw. Mirrors Python's `with capsule.record(...) as r:`
   * lifecycle for caller ergonomics.
   *
   * @example
   *   for (const pa of batch) {
   *     await capsule.withRecord({ recordId: pa.id, patternTag: "pa" }, async (r) => {
   *       const result = await processPa(pa);
   *       await r.bindOutput(result);
   *       r.event({ type: "ReasoningStepCompleted", step_id: "ddx" });
   *     });
   *   }
   */
  async withRecord<T>(
    opts: RecordOptions,
    callback: (r: RecordContext) => Promise<T>,
  ): Promise<T> {
    return await withRecordContext(this.record(opts), callback);
  }

  /** Internal — POST to `/v1/capsules/:id/record`. Used by RecordContext. */
  private async _postRecord(
    capsuleId: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return await this._http.request("POST", `/v1/capsules/${capsuleId}/record`, {
      json: body,
    });
  }

  /**
   * Declare a cross-org parent-proof link (section "Architecture").
   *
   * Parent links buffer server-side until destroy and bind into the
   * AuditProof's outer 8-step chain via Merkle root incorporation at
   * Step 8 alongside record receipts. Parent signatures verify
   * independently of Nanorix accounts via Portable Pubkey Bundles
   * (section "Pubkey discovery gap").
   *
   * Each link attests to lineage, NOT to compliance —
   * three-role liability split. The auditor adjudicates substantive
   * acceptability; receipts carry verified-lineage only.
   *
   * @param opts.parentChainHash SHA-512 chain hash of the upstream
   *   AuditProof's Step 8. Prefix `sha512:{hex}`. Length cap: 144 chars.
   * @param opts.parentKeyId Authority key ID of the parent's signer.
   *   Canonical formats: `nrx-verify-{terminated_at}-{cdp_id[..8]}`
   *   (Nanorix-managed) OR `cust-auth-{authority_id}` (customer-rooted
   *). Length cap: 256 chars.
   * @param opts.parentSignature Ed25519 signature `base64:{b64}` over
   *   `parentChainHash`. Length cap: 256 chars.
   * @param opts.parentRole Optional opaque role tag (e.g.
   *   `"input-data-source"`, `"rag-retrieval"`). Length cap: 128 chars.
   * @param opts.parentJurisdiction Optional ISO-3166 / region tag. Length
   *   cap: 16 chars.
   * @param opts.parentOrganizationTag Optional opaque organization
   *   identifier. Length cap: 256 chars.
   * @returns The server response, containing `capsule_id`,
   *   `parent_chain_hash`, `buffered_count`, and a `disclaimer`.
   * @throws BadRequestError if any field-length cap is violated.
   */
  async addParentProof(
    opts: ParentProofOptions,
  ): Promise<Record<string, unknown>> {
    validateParentLinkFields(opts);
    const body = buildParentLinkBody(opts);
    return await this._http.request("POST", `/v1/capsules/${this.id}/parent`, {
      json: body,
    });
  }
}

/** Capsule operations — create, run, list, get. */
export class Capsules {
  constructor(private readonly _http: HttpTransport) {}

  /**
   * Create a new capsule. Returns a LiveCapsule with workspace methods.
   *
   * `metadata` binds compliance-binding context into the AuditProof's
   * `metadata_hash`. Use the `nanorix/compliance` module
   * to construct typed framework context (HIPAA, GDPR, SOC 2) or pass a
   * plain `Record<string, string>`. Server constraints: 16 keys max,
   * 64-char keys, 256-char values, 4 KB total. `nanorix:*` keys are
   * reserved for helper-emitted reserved keys; customer-supplied
   * `nanorix:*` keys are rejected by the API.
   */
  async create(opts?: {
    capsulefile?: string;
    maxLifetimeSeconds?: number;
    egressProfile?: string;
    metadata?: Record<string, string>;
    /**
     * Optional. When omitted the SDK auto-generates a UUIDv4
     * `Idempotency-Key` header so a transient network error + SDK-level
     * retry never silently double-creates a capsule ( /
     * Stripe-pattern 24h dedup per Migration 058). Pass an
     * explicit value to scope idempotency under your own dedup contract
     * (e.g. one Idempotency-Key per business workflow id).
     */
    idempotencyKey?: string;
  }): Promise<LiveCapsule> {
    const body: Record<string, unknown> = {};
    if (opts?.capsulefile !== undefined) body.capsulefile_id = opts.capsulefile;
    if (opts?.maxLifetimeSeconds !== undefined)
      body.max_lifetime_seconds = opts.maxLifetimeSeconds;
    if (opts?.egressProfile !== undefined)
      body.egress_profile = opts.egressProfile;
    if (opts?.metadata !== undefined) body.metadata = opts.metadata;

    const key =
      opts?.idempotencyKey !== undefined ? opts.idempotencyKey : randomUUID();
    const result = await this._http.request("POST", "/v1/capsules", {
      json: body,
      headers: { "Idempotency-Key": key },
    });
    return new LiveCapsule(result as unknown as Capsule, this._http);
  }

  /**
   * One-shot processing: create → upload → execute → download → destroy.
   *
   * Returns [outputs, cdp] where:
   * - outputs: Record<string, Buffer> mapping filename → content
   * - cdp: CdpAccess with .full() and .verification()
   *
   * `metadata` flows through to the underlying `create()` call; see that
   * method's doc-comment for binding semantics.
   */
  async run(opts: {
    capsulefile: string;
    inputs?: Record<string, Buffer | Uint8Array>;
    command?: string;
    timeout?: number;
    maxLifetimeSeconds?: number;
    metadata?: Record<string, string>;
  }): Promise<[Record<string, Buffer>, CdpAccess]> {
    const capsule = await this.create({
      capsulefile: opts.capsulefile,
      maxLifetimeSeconds: opts.maxLifetimeSeconds,
      metadata: opts.metadata,
    });

    try {
      // Upload inputs
      if (opts.inputs !== undefined) {
        for (const [filename, data] of Object.entries(opts.inputs)) {
          await capsule.upload(
            filename,
            data instanceof Buffer ? data : Buffer.from(data),
          );
        }
      }

      // Execute
      await capsule.execute(opts.command, { timeout: opts.timeout });

      // Download all outputs
      const outputList = await capsule.listOutputs();
      const outputs: Record<string, Buffer> = {};
      for (const f of outputList) {
        outputs[f.filename] = await capsule.download(f.filename);
      }

      // Destroy
      const cdp = await capsule.destroy();
      return [outputs, cdp];
    } catch (err) {
      if (err instanceof ExecutionError) {
        try {
          const cdp = await capsule.destroy();
          err.cdp = cdp.full();
        } catch {
          /* best effort */
        }
      } else {
        try {
          await capsule.destroy();
        } catch {
          /* best effort cleanup */
        }
      }
      throw err;
    }
  }

  /**
   * Batch processing — Anthropic-Batches inline shape (Gap 1).
   *
   * Per Agent A research: adopted shape mirrors Anthropic Message Batches
   * `requests[]` inline format and SQS `ReportBatchItemFailures` per-record
   * outcome semantics. Each record MUST carry a unique `custom_id`
   * (configurable via `recordIdField`) for inclusion-proof stability.
   *
   * V1 SDK-driven flow: customer's entrypoint reads
   * /data/input/records.jsonl + writes /data/output/results.jsonl.
   * SDK uploads, executes, downloads, parses, computes Merkle root
   * client-side (mirrors Rust runtime byte-identically), destroys
   * → ONE CdpAccess for the whole batch.
   */
  async batch(opts: {
    capsulefile: string;
    records: Record<string, unknown>[];
    onRecord?: (r: BatchRecord) => void | Promise<void>;
    recordIdField?: string;
    onRecordError?: "fail_isolated" | "abort_batch";
    maxLifetimeSeconds?: number;
    metadata?: Record<string, string>;
    timeout?: number;
  }): Promise<BatchResult> {
    const recordIdField = opts.recordIdField ?? "custom_id";
    const onRecordError = opts.onRecordError ?? "fail_isolated";

    if (
      onRecordError !== "fail_isolated" &&
      onRecordError !== "abort_batch"
    ) {
      throw new Error(
        `onRecordError must be 'fail_isolated' or 'abort_batch'; got ${onRecordError}`,
      );
    }
    if (opts.records.length === 0) {
      throw new Error("Capsule.batch requires at least one record");
    }

    const recordsJsonl = serializeRecordsToJsonl(opts.records, recordIdField);

    const capsule = await this.create({
      capsulefile: opts.capsulefile,
      maxLifetimeSeconds: opts.maxLifetimeSeconds,
      metadata: opts.metadata,
    });

    try {
      await capsule.upload("records.jsonl", Buffer.from(recordsJsonl));
      await capsule.execute(undefined, { timeout: opts.timeout });

      let resultsData: Buffer;
      try {
        resultsData = await capsule.download("results.jsonl");
      } catch (err) {
        throw new Error(
          `Customer entrypoint did not produce results.jsonl: ${err instanceof Error ? err.message : String(err)}`,
        );
      }

      const batchRecords = await parseResultsJsonl(
        new Uint8Array(resultsData),
        recordsJsonl,
        recordIdField,
      );

      const successCount = batchRecords.filter(
        (r) => r.status === "succeeded",
      ).length;
      const errorCount = batchRecords.filter(
        (r) => r.status === "errored",
      ).length;

      if (onRecordError === "abort_batch" && errorCount > 0) {
        const firstFailure = batchRecords.find((r) => r.status === "errored")!;
        // Still destroy + attach CDP via the error.
        const cdp = await capsule.destroy();
        const err = new ExecutionError(
          `Batch aborted on first record error (onRecordError='abort_batch'): ${firstFailure.customId} → ${firstFailure.errorCode}`,
          {
            exitCode: 0,
            stdout: "",
            stderr: firstFailure.errorMessage ?? "",
          },
        );
        err.cdp = cdp.full();
        throw err;
      }

      if (opts.onRecord !== undefined) {
        for (const r of batchRecords) {
          await opts.onRecord(r);
        }
      }

      const cdp = await capsule.destroy();
      const merkleRoot = await computeBatchMerkleRoot(batchRecords);

      const result: BatchResult = {
        merkleRoot,
        auditProof: cdp,
        successCount,
        errorCount,
        totalCount: batchRecords.length,
        records: function* () {
          yield* batchRecords;
        },
        find(customId: string) {
          return batchRecords.find((r) => r.customId === customId);
        },
        iterFailures: function* () {
          for (const r of batchRecords) {
            if (r.status === "errored") yield r;
          }
        },
      };
      return result;
    } catch (err) {
      try {
        await capsule.destroy();
      } catch {
        /* best effort cleanup */
      }
      throw err;
    }
  }

  /**
   * Session-mode processing — LangGraph-shape memory artifact (Gap 2).
   *
   * Mirrors Python `Capsule.session()` byte-identically. Per Agent A:
   * adopted shape is LangGraph CheckpointSaver (thread_id) + Bedrock
   * AgentCore CMK posture. V1 SDK-driven flow: customer pre-fetches
   * + decrypts state.bin, SDK uploads/executes/downloads, customer
   * re-encrypts + persists for next session in chain.
   */
  async session(opts: {
    capsulefile: string;
    sessionId: string;
    parentCdpId?: string;
    initialState?: Buffer | Uint8Array;
    maxLifetimeSeconds?: number;
    metadata?: Record<string, string>;
    timeout?: number;
  }): Promise<SessionResult> {
    if (!opts.sessionId) {
      throw new Error(
        "Capsule.session requires non-empty sessionId (LangGraph thread_id convention)",
      );
    }

    const stateBytes =
      opts.initialState !== undefined
        ? opts.initialState instanceof Buffer
          ? opts.initialState
          : Buffer.from(opts.initialState)
        : Buffer.alloc(0);

    const artifactInputHash = await hashStateBytes(stateBytes);
    const sessionMetadata = buildSessionMetadata({
      sessionId: opts.sessionId,
      parentCdpId: opts.parentCdpId,
      artifactInputHash,
      extra: opts.metadata,
    });

    const capsule = await this.create({
      capsulefile: opts.capsulefile,
      maxLifetimeSeconds: opts.maxLifetimeSeconds,
      metadata: sessionMetadata,
    });

    try {
      await capsule.upload("state.bin", stateBytes);
      await capsule.execute(undefined, { timeout: opts.timeout });

      let stateOut: Buffer;
      try {
        stateOut = await capsule.download("state.bin");
      } catch (err) {
        throw new Error(
          `Customer entrypoint did not produce state.bin: ${err instanceof Error ? err.message : String(err)}`,
        );
      }

      const artifactOutputHash = await hashStateBytes(stateOut);
      const cdp = await capsule.destroy();

      return {
        stateOut,
        artifactInputHash,
        artifactOutputHash,
        auditProof: cdp,
        sessionId: opts.sessionId,
      };
    } catch (err) {
      try {
        await capsule.destroy();
      } catch {
        /* best effort */
      }
      throw err;
    }
  }

  /** List capsules. Supports ?status= query param. */
  async list(params?: Record<string, string>): Promise<CapsuleList> {
    return (await this._http.request("GET", "/v1/capsules", {
      params,
    })) as unknown as CapsuleList;
  }

  /** Get a capsule by ID. */
  async get(capsuleId: string): Promise<Capsule> {
    return (await this._http.request(
      "GET",
      `/v1/capsules/${capsuleId}`,
    )) as unknown as Capsule;
  }
}
