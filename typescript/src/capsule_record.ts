/**
 * Per-record receipt + parent-proof SDK ergonomics (ADR-039 + ADR-041).
 *
 * This module exposes the customer-facing surface for emitting per-record
 * receipts (ADR-039) and parent-proof links (ADR-041) against a live
 * capsule. Receipts and parent links buffer server-side per Wave A
 * (`services/api/src/routes/records.rs`) and bind into the AuditProof's
 * outer 8-step chain at capsule-destroy time via Merkle root incorporation
 * into Step 8 (`governance/rzl/src/wave_n.rs::compute_step_8_amended`).
 *
 * ## Three-layer separation discipline
 *
 * | Layer | Carries | Evolves at |
 * |---|---|---|
 * | Receipt (ADR-039) | `pattern_tag` (signed primitive) | ADR-006 I0 Forever-Standard pace |
 * | Mapping artifact (ADR-040) | `(j x c x p) -> string[]` | Framework revision pace |
 * | Adapter (Vanta / Drata / Anecdotes / internal) | Reads signed receipt + applies mapping | Vendor pace |
 *
 * **Control IDs are NEVER stamped into the signed receipt** — they are
 * derived at adapter time by applying the ADR-040 published mapping
 * artifact (`./control_map`) to `(jurisdiction x classification x
 * pattern_tag)`. Stamping mapping output inside the cryptographic receipt
 * would freeze historical receipts with stale control IDs forever when
 * frameworks revise.
 *
 * The signed receipt carries `pattern_tag` only; the adapter applies the
 * mapping freshly at ingestion time using whichever `framework_version` is
 * current at the moment of GRC evidence-filing.
 *
 * ## Vocabulary discipline (CCO policy)
 *
 * - Required relationship vocabulary: `applies_to` / `references` / `related_to`.
 * - The receipt attests to evidence; the auditor adjudicates substantive
 *   acceptability (ADR-017 three-role liability split).
 * - See `./control_map::DISCLAIMER` for the canonical disclaimer surface
 *   that every adapter consumer surfaces alongside the resolved control IDs.
 * - Verdict vocabulary (the four banned terms) is enforced by the pre-publish
 *   6-scan gate in `tests/capsule_record.test.ts` — kept out of every
 *   customer-facing SDK surface.
 *
 * ## Usage — per-record emission (callback sugar — recommended)
 *
 * ```typescript
 * import { NanorixClient } from "@nanorix/sdk";
 *
 * const client = new NanorixClient({ apiKey: "nrx_live_..." });
 * const capsule = await client.capsules.create({ capsulefile: "cf_pa" });
 *
 * for (const pa of batch) {
 *   await capsule.withRecord({ recordId: pa.id, patternTag: "pa" }, async (r) => {
 *     const result = await processPa(pa);
 *     await r.bindOutput(result);
 *     r.event({ type: "ReasoningStepCompleted", step_id: "ddx" });
 *   });
 * }
 *
 * // capsule destroy returns the AuditProof with receipts[] populated
 * const auditProof = (await capsule.destroy()).verification();
 * ```
 *
 * ## Usage — explicit lifecycle
 *
 * ```typescript
 * const ctx = capsule.record({ recordId: "pa_123", patternTag: "pa" });
 * await ctx.bindOutput({ approved: true });
 * ctx.event({ type: "ReasoningStepCompleted", step_id: "ddx" });
 * await ctx.commit();   // POST /v1/capsules/:id/record
 * console.log(ctx.recordChainHash, ctx.recordIndex);
 * ```
 *
 * ## Usage — cross-org parent proof
 *
 * ```typescript
 * await capsule.addParentProof({
 *   parentChainHash: "sha512:def...",
 *   parentKeyId: "cust-auth-banjo-prod",
 *   parentSignature: "base64:...",
 *   parentRole: "input-data-source",
 *   parentOrganizationTag: "vendor:banjo-health",
 *   parentJurisdiction: "US",
 * });
 * ```
 */

import { BadRequestError } from "./errors.js";
import { canonicalizeBytes } from "./_jcs.js";

// ── Closed-enum PatternTag mirror of Rust `nanorix_rzl::types::PatternTag` ──
//
// Per ADR-039 section "pattern_tag rule" and `governance/rzl/src/types.rs::PatternTag`,
// the closed enum is append-only at Forever-Standard pace. The wire form is
// snake_case (matches the Rust serde rename_all="snake_case" output).
//
// Per ADR-006 I0: this tuple is APPEND-ONLY. New variants land via ADR
// amendment; existing variants are NEVER renamed or removed.

/**
 * Canonical closed enum of receipt `pattern_tag` variants (ADR-039).
 *
 * Mirrors the Rust `PatternTag` enum's serde rename_all="snake_case" wire
 * form. Append-only per ADR-006 I0 Forever-Standard.
 */
export const PATTERN_TAGS = [
  // Pattern 1 — extraction / decision
  "pa",
  "extraction",
  "annotation",
  // Pattern 2 — multi-step agent
  "agent_step",
  "agent_turn",
  // Pattern 4 — high-volume per-record
  "rcm_claim",
  "rcm_eligibility",
  "rcm_remit",
  "ncpdp_script",
  // Pattern 6 — imaging
  "dicom_study",
  "dicom_sr",
  // Pattern 8 — long-horizon
  "screening_hit",
  "fhir_record",
  // Generic
  "ehr_document",
  "custom",
] as const;

/**
 * Compile-time PatternTag union type. Exhaustive checking is enforced
 * via `typeof PATTERN_TAGS[number]` so adding a variant to `PATTERN_TAGS`
 * automatically widens the type.
 */
export type PatternTag = (typeof PATTERN_TAGS)[number];

/** Runtime set view for fast membership checks. */
const PATTERN_TAG_SET: ReadonlySet<string> = new Set(PATTERN_TAGS);

// ── Field length limits (mirror `services/api/src/routes/records.rs`) ──

export const MAX_RECORD_ID_LEN = 256;
/** "sha512:" prefix (7 chars) + 128 hex chars + headroom for future prefixes. */
export const MAX_HASH_LEN = 144;
export const MAX_PARENT_KEY_ID_LEN = 256;
export const MAX_PARENT_SIG_LEN = 256;
export const MAX_PARENT_ROLE_LEN = 128;
export const MAX_PARENT_JURISDICTION_LEN = 16;
export const MAX_PARENT_ORG_TAG_LEN = 256;
export const MAX_ACTIVITY_TRAIL_LEN = 1024;

/**
 * Validate that `patternTag` matches the closed enum.
 *
 * Per ADR-039 section "pattern_tag rule" + ADR-013 customer-sole-declarer:
 * Nanorix records the customer-declared pattern verbatim; this validator
 * only enforces that the declared value is one of the canonical closed-
 * enum variants. Case-sensitive snake_case matching the Rust serde
 * rename_all="snake_case" wire form.
 *
 * @returns The pattern_tag unchanged when valid (or undefined when undefined passed in).
 * @throws BadRequestError if `patternTag` is not a recognized variant.
 */
export function validatePatternTag(
  patternTag: string | undefined,
): PatternTag | undefined {
  if (patternTag === undefined) return undefined;
  if (!PATTERN_TAG_SET.has(patternTag)) {
    throw new BadRequestError(
      `pattern_tag '${patternTag}' is not a recognized variant. ` +
        `Valid variants per ADR-039 section 'pattern_tag rule': ` +
        PATTERN_TAGS.join(", "),
      { statusCode: 400 },
    );
  }
  return patternTag as PatternTag;
}

// ── Hash helpers (cross-impl byte-equivalence with Rust + Python) ──────

/**
 * SHA-512 of arbitrary bytes / UTF-8 string. Browser- + Node-portable via
 * the WebCrypto SubtleCrypto API. Returns the `sha512:{hex}` wire form
 * that matches `RecordReceipt::record_input_hash` per ADR-039.
 *
 * Async because `crypto.subtle.digest` is async. Customer code typically
 * awaits this via `bindInput()` / `bindOutput()`.
 */
export async function sha512Hex(data: Uint8Array | string): Promise<string> {
  // Force an ArrayBuffer-backed view so the WebCrypto BufferSource accepts
  // it on Node 22+ (Uint8Array<ArrayBufferLike> would otherwise widen to
  // SharedArrayBuffer in the lib.dom types).
  const bytes: Uint8Array =
    typeof data === "string"
      ? new TextEncoder().encode(data)
      : new Uint8Array(data);
  const buf = await globalThis.crypto.subtle.digest(
    "SHA-512",
    bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer,
  );
  return "sha512:" + bufferToHex(buf);
}

function bufferToHex(buf: ArrayBuffer): string {
  const view = new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < view.length; i++) {
    out += view[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * JCS-canonicalize `value` per RFC 8785 + SHA-512 -> `sha512:{hex}`.
 *
 * Mirrors the Rust `serde_jcs::to_vec(value)` -> `SHA-512` path used by
 * `compute_activity_root` and other cryptographic-boundary primitives.
 * Cross-impl byte-equivalent to the Python `canonical_hash` and the Rust
 * serializer (see `./_jcs.ts` for the RFC 8785 implementation).
 *
 * @param value JSON-serializable value (string, number, boolean, null,
 *   object, array, or nested combinations). Uint8Array inputs are not
 *   JSON-serializable; pass `sha512Hex(bytes)` instead.
 * @returns `sha512:{128 hex chars}` matching the Rust `RecordReceipt`
 *   `record_input_hash` / `record_output_hash` wire form.
 */
export async function canonicalHash(value: unknown): Promise<string> {
  const bytes = canonicalizeBytes(value);
  return await sha512Hex(bytes);
}

/** Internal: hash an arbitrary bind payload to `sha512:{hex}` wire form. */
async function hashPayload(
  data: Uint8Array | string | Record<string, unknown> | unknown[],
): Promise<string> {
  if (data instanceof Uint8Array) {
    return await sha512Hex(data);
  }
  if (typeof data === "string") {
    return await sha512Hex(data);
  }
  if (Array.isArray(data) || (typeof data === "object" && data !== null)) {
    return await canonicalHash(data);
  }
  throw new BadRequestError(
    `bindOutput / bindInput value must be Uint8Array, string, object, or ` +
      `array; got ${typeof data}`,
    { statusCode: 400 },
  );
}

// ── RecordContext ──────────────────────────────────────────────────────

/** Activity event shape passed to `record.event(...)`. */
export interface ActivityEventInput {
  [key: string]: unknown;
}

/**
 * Internal callback signature provided by `LiveCapsule` to decouple the
 * receipt context from the HTTP transport (testability + circular-import
 * avoidance, same pattern as the Python SDK).
 */
export type PostRecordCallback = (
  capsuleId: string,
  body: Record<string, unknown>,
) => Promise<Record<string, unknown>>;

/** Options object for `capsule.record({...})`. */
export interface RecordOptions {
  /** Customer-declared opaque identifier. Length cap: 256 chars. */
  recordId: string;
  /** Optional closed-enum workflow pattern tag (one of 15 variants). */
  patternTag?: PatternTag;
}

/**
 * Per-record receipt context (ADR-039 section "Architecture").
 *
 * Returned by `LiveCapsule.record(...)`. Customer buffers
 * `bindInput` / `bindOutput` / `event` calls then commits via either:
 *
 *  - `await ctx.commit()` — explicit POST to `/v1/capsules/:id/record`
 *  - `await capsule.withRecord(opts, async (r) => { ... })` — callback
 *    sugar that auto-commits on resolve, aborts on throw (mirrors
 *    Python's `with capsule.record(...) as r:` context manager).
 *
 * The Wave A endpoint computes the `record_chain_hash` server-side and
 * appends the receipt to the in-memory wave-N buffer; the receipt is
 * bound into the AuditProof's outer chain via Merkle root at capsule
 * destroy time.
 *
 * The receipt carries `pattern_tag` only. **Control IDs are NEVER
 * stamped into the signed receipt** — adapters apply the ADR-040
 * published mapping at ingestion time via `./control_map`.
 */
export class RecordContext {
  readonly capsuleId: string;
  readonly recordId: string;
  readonly patternTag: PatternTag | undefined;

  private readonly _post: PostRecordCallback;
  private _recordInputHash: string | undefined;
  private _recordOutputHash: string | undefined;
  private _activityTrail: ActivityEventInput[] = [];
  private _committed = false;
  private _aborted = false;
  private _response: Record<string, unknown> | undefined;

  constructor(
    capsuleId: string,
    recordId: string,
    patternTag: string | undefined,
    postCallback: PostRecordCallback,
  ) {
    if (typeof recordId !== "string" || recordId.length === 0) {
      throw new BadRequestError(
        "recordId must be a non-empty string per ADR-039 section 'Architecture' " +
          "(customer-sole-declarer per ADR-013)",
        { statusCode: 400 },
      );
    }
    if (recordId.length > MAX_RECORD_ID_LEN) {
      throw new BadRequestError(
        `recordId length must be <= ${MAX_RECORD_ID_LEN} chars ` +
          `(got ${recordId.length})`,
        { statusCode: 400 },
      );
    }

    this.capsuleId = capsuleId;
    this.recordId = recordId;
    this.patternTag = validatePatternTag(patternTag);
    this._post = postCallback;
  }

  /**
   * Bind the record's input payload as a SHA-512 hash on the receipt.
   *
   * Uint8Array -> SHA-512 of the bytes. string -> SHA-512 of UTF-8 bytes.
   * object / array -> JCS canonical-bytes -> SHA-512 (RFC 8785).
   * The resulting `sha512:{hex}` value is sent in the
   * `record_input_hash` field of the create-record request.
   */
  async bindInput(
    data: Uint8Array | string | Record<string, unknown> | unknown[],
  ): Promise<void> {
    this._ensureOpen();
    this._recordInputHash = await hashPayload(data);
  }

  /**
   * Bind the record's output payload as a SHA-512 hash on the receipt.
   *
   * Same hashing semantics as `bindInput`. The resulting `sha512:{hex}`
   * value is sent in the `record_output_hash` field.
   */
  async bindOutput(
    data: Uint8Array | string | Record<string, unknown> | unknown[],
  ): Promise<void> {
    this._ensureOpen();
    this._recordOutputHash = await hashPayload(data);
  }

  /**
   * Append a per-record activity event to the receipt's activity trail.
   *
   * The Rust API consumes the trail as `Vec<serde_json::Value>` per
   * `services/api/src/routes/records.rs::CreateRecordRequest`; arbitrary
   * JSON structure is accepted but client-side payload limit is
   * `MAX_ACTIVITY_TRAIL_LEN` events.
   *
   * Per ADR-028 + Wave 18-11 cdp_kind discipline: customer-supplied
   * events flow through Nanorix verbatim. The activity trail is
   * canonicalized via JCS + chained via `compute_activity_root` at
   * chain-hash computation time.
   */
  event(activityEvent: ActivityEventInput): void {
    this._ensureOpen();
    if (
      activityEvent === null ||
      typeof activityEvent !== "object" ||
      Array.isArray(activityEvent)
    ) {
      throw new BadRequestError(
        "event must be an object per the ActivityEvent wire form",
        { statusCode: 400 },
      );
    }
    if (this._activityTrail.length >= MAX_ACTIVITY_TRAIL_LEN) {
      throw new BadRequestError(
        `activity_trail length must be <= ${MAX_ACTIVITY_TRAIL_LEN}`,
        { statusCode: 400 },
      );
    }
    this._activityTrail.push(activityEvent);
  }

  /**
   * Commit the buffered receipt: POST `/v1/capsules/:id/record`.
   *
   * Idempotent: calling `commit()` twice on the same context is a no-op
   * after the first successful commit (matches Python's `__exit__`
   * single-fire semantics).
   *
   * On error: the server response is parsed via the same `raiseForStatus`
   * pipeline as the rest of the SDK; the context is NOT marked committed
   * so the caller can retry once the underlying issue is resolved.
   */
  async commit(): Promise<void> {
    if (this._committed || this._aborted) return;
    const body = this._buildRequestBody();
    this._response = await this._post(this.capsuleId, body);
    this._committed = true;
  }

  /**
   * Abort the buffered receipt without committing.
   *
   * Mirrors Python `__exit__` on exception path: when the customer's
   * record-emission flow blows up mid-record, no half-receipt is sent.
   * The customer is free to re-enter a fresh `record(...)` context for
   * any retry / partial-failure record.
   */
  abort(): void {
    if (this._committed) return;
    this._aborted = true;
  }

  /** Server-computed `record_chain_hash` (available after `commit()`). */
  get recordChainHash(): string | undefined {
    if (this._response === undefined) return undefined;
    const v = this._response.record_chain_hash;
    return typeof v === "string" ? v : undefined;
  }

  /** Server-computed zero-indexed `record_index` (after `commit()`). */
  get recordIndex(): number | undefined {
    if (this._response === undefined) return undefined;
    const v = this._response.record_index;
    return typeof v === "number" ? v : undefined;
  }

  /** Raw server response from `commit()`. */
  get response(): Record<string, unknown> | undefined {
    return this._response;
  }

  /** True once `commit()` has been awaited successfully. */
  get committed(): boolean {
    return this._committed;
  }

  /** True if `abort()` was called before commit. */
  get aborted(): boolean {
    return this._aborted;
  }

  private _ensureOpen(): void {
    if (this._committed) {
      throw new BadRequestError(
        "record context already committed — open a new context for the next record",
        { statusCode: 400 },
      );
    }
    if (this._aborted) {
      throw new BadRequestError(
        "record context was aborted — open a new context for the next record",
        { statusCode: 400 },
      );
    }
  }

  private _buildRequestBody(): Record<string, unknown> {
    // Only fields with declared values are emitted; the Rust handler
    // treats absent fields as the documented default (genesis hash for
    // unset input/output; None for unset pattern_tag).
    const body: Record<string, unknown> = { record_id: this.recordId };
    if (this.patternTag !== undefined) {
      body.pattern_tag = this.patternTag;
    }
    if (this._recordInputHash !== undefined) {
      body.record_input_hash = this._recordInputHash;
    }
    if (this._recordOutputHash !== undefined) {
      body.record_output_hash = this._recordOutputHash;
    }
    if (this._activityTrail.length > 0) {
      body.activity_trail = [...this._activityTrail];
    }
    return body;
  }
}

/**
 * Callback-sugar wrapper. Mirrors Python's `with capsule.record(...) as r:`
 * lifecycle: auto-commits on resolve, auto-aborts on throw, never swallows
 * caller exceptions.
 *
 * @param ctx The RecordContext returned from `capsule.record({...})`.
 * @param callback Customer callback that buffers bind/event calls. Caller
 *   exceptions propagate after the receipt is aborted; commit happens
 *   exactly once on the success path.
 * @returns The value returned by `callback`.
 */
export async function withRecordContext<T>(
  ctx: RecordContext,
  callback: (r: RecordContext) => Promise<T>,
): Promise<T> {
  let result: T;
  try {
    result = await callback(ctx);
  } catch (err) {
    ctx.abort();
    throw err;
  }
  await ctx.commit();
  return result;
}

// ── Parent proof validation helpers ────────────────────────────────────

/** Options object for `capsule.addParentProof({...})`. */
export interface ParentProofOptions {
  /** SHA-512 chain hash of the upstream AuditProof's Step 8. `sha512:{hex}`. */
  parentChainHash: string;
  /** Authority key ID of the parent's signer. */
  parentKeyId: string;
  /** Ed25519 signature `base64:{b64}` over `parentChainHash`. */
  parentSignature: string;
  /** Optional opaque role tag. Length cap: 128 chars. */
  parentRole?: string;
  /** Optional ISO-3166 / region tag. Length cap: 16 chars. */
  parentJurisdiction?: string;
  /** Optional opaque organization identifier. Length cap: 256 chars. */
  parentOrganizationTag?: string;
}

/**
 * Validate parent-proof link fields against ADR-041 + API length caps.
 *
 * Field constraints mirror `services/api/src/routes/records.rs::create_parent`.
 *
 * @throws BadRequestError on any field-length violation.
 */
export function validateParentLinkFields(opts: ParentProofOptions): void {
  const {
    parentChainHash,
    parentKeyId,
    parentSignature,
    parentRole,
    parentJurisdiction,
    parentOrganizationTag,
  } = opts;

  if (typeof parentChainHash !== "string" || parentChainHash.length === 0) {
    throw new BadRequestError(
      "parentChainHash must be a non-empty string per ADR-041",
      { statusCode: 400 },
    );
  }
  if (parentChainHash.length > MAX_HASH_LEN) {
    throw new BadRequestError(
      `parentChainHash length must be <= ${MAX_HASH_LEN} chars ` +
        `(got ${parentChainHash.length})`,
      { statusCode: 400 },
    );
  }
  if (typeof parentKeyId !== "string" || parentKeyId.length === 0) {
    throw new BadRequestError(
      "parentKeyId must be a non-empty string per ADR-041",
      { statusCode: 400 },
    );
  }
  if (parentKeyId.length > MAX_PARENT_KEY_ID_LEN) {
    throw new BadRequestError(
      `parentKeyId length must be <= ${MAX_PARENT_KEY_ID_LEN} chars`,
      { statusCode: 400 },
    );
  }
  if (typeof parentSignature !== "string" || parentSignature.length === 0) {
    throw new BadRequestError(
      "parentSignature must be a non-empty string per ADR-041",
      { statusCode: 400 },
    );
  }
  if (parentSignature.length > MAX_PARENT_SIG_LEN) {
    throw new BadRequestError(
      `parentSignature length must be <= ${MAX_PARENT_SIG_LEN} chars`,
      { statusCode: 400 },
    );
  }
  if (parentRole !== undefined && parentRole.length > MAX_PARENT_ROLE_LEN) {
    throw new BadRequestError(
      `parentRole length must be <= ${MAX_PARENT_ROLE_LEN} chars`,
      { statusCode: 400 },
    );
  }
  if (
    parentJurisdiction !== undefined &&
    parentJurisdiction.length > MAX_PARENT_JURISDICTION_LEN
  ) {
    throw new BadRequestError(
      `parentJurisdiction length must be <= ${MAX_PARENT_JURISDICTION_LEN} chars`,
      { statusCode: 400 },
    );
  }
  if (
    parentOrganizationTag !== undefined &&
    parentOrganizationTag.length > MAX_PARENT_ORG_TAG_LEN
  ) {
    throw new BadRequestError(
      `parentOrganizationTag length must be <= ${MAX_PARENT_ORG_TAG_LEN} chars`,
      { statusCode: 400 },
    );
  }
}

/**
 * Build the JSON body for `POST /v1/capsules/:id/parent`.
 *
 * Only fields with declared values are emitted; the Rust handler treats
 * absent optional fields as `None` per ADR-041 section "Architecture".
 * Field names are converted to snake_case to match the Rust wire form.
 */
export function buildParentLinkBody(
  opts: ParentProofOptions,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    parent_chain_hash: opts.parentChainHash,
    parent_key_id: opts.parentKeyId,
    parent_signature: opts.parentSignature,
  };
  if (opts.parentRole !== undefined) {
    body.parent_role = opts.parentRole;
  }
  if (opts.parentJurisdiction !== undefined) {
    body.parent_jurisdiction = opts.parentJurisdiction;
  }
  if (opts.parentOrganizationTag !== undefined) {
    body.parent_organization_tag = opts.parentOrganizationTag;
  }
  return body;
}
