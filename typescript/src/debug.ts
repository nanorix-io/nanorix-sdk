/**
 * Customer debugging tools.
 *
 * Provides:
 *   1. Trace context propagation — W3C Trace Context headers bound to SDK calls.
 *   2. Retry-with-backoff helpers — exponential backoff, 503 Retry-After aware.
 *   3. AuditProof download helper — downloads and saves AuditProof JSON.
 *   4. AuditProof verification helper — pure-TS 4-stage pipeline,
 *      byte-equivalent with Go reference verifier and Rust verifier V1
 *      on the 100-fixture corpus.
 *
 * V1 divergences (match Go reference verifier, documented):
 *   - signature_mismatch / region_mismatch / signing_key_version_unknown /
 *     authority_revoked: stages 5-8 not wired in V1 → valid=true
 *   - canonical_hash_mismatch: V2+ stage 5 JCS variant — not wired in V1
 *   - Category 06 authority_unknown: requires explicit policy pin
 */

import { NanorixError } from "./errors.js";
import type { NanorixClient } from "./client.js";
import { writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

// ─── Trace context ──────────────────────────────────────────────────────────

/** Module-level W3C Trace Context state. */
const _traceContext: { traceparent: string | null; tracestate: string | null } =
  {
    traceparent: null,
    tracestate: null,
  };

const _TRACEPARENT_RE = /^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/;

/**
 * Set W3C Trace Context bound to all subsequent API calls.
 *
 * If `traceparent` is undefined, reads from `OTEL_TRACEPARENT` environment variable.
 * Passing an explicit empty string clears the context.
 *
 * @throws Error if `traceparent` is set but does not match the
 *   `00-<32hex>-<16hex>-<2hex>` pattern.
 */
export function setTraceContext(
  traceparent?: string,
  tracestate?: string,
): void {
  let tp = traceparent;
  if (tp === undefined) {
    tp = process.env["OTEL_TRACEPARENT"] ?? undefined;
  }
  if (tp && tp !== "") {
    if (!_TRACEPARENT_RE.test(tp)) {
      throw new Error(
        `Invalid traceparent format: ${JSON.stringify(tp)}. ` +
          "Expected: 00-<32-hex-trace-id>-<16-hex-parent-id>-<2-hex-flags>",
      );
    }
  }
  _traceContext.traceparent = tp || null;
  _traceContext.tracestate = tracestate || null;
}

/** Return a copy of the current W3C Trace Context. */
export function getTraceContext(): {
  traceparent: string | null;
  tracestate: string | null;
} {
  return { ..._traceContext };
}

/** Clear the W3C Trace Context. */
export function clearTraceContext(): void {
  _traceContext.traceparent = null;
  _traceContext.tracestate = null;
}

/** Build HTTP headers dict from current trace context (non-null values only). */
export function buildTraceHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  if (_traceContext.traceparent)
    headers["traceparent"] = _traceContext.traceparent;
  if (_traceContext.tracestate)
    headers["tracestate"] = _traceContext.tracestate;
  return headers;
}

// ─── Retry with backoff ─────────────────────────────────────────────────────

/** Configuration for retry-with-backoff. */
export interface RetryOptions {
  /** Total number of attempts (first + retries). Default 3. */
  maxAttempts?: number;
  /** Base sleep ms for attempt 0. Doubles each attempt. Default 500. */
  backoffBaseMs?: number;
  /** Maximum sleep cap in ms. Default 30_000. */
  backoffMaxMs?: number;
  /** HTTP status codes that trigger a retry. Default: {429, 500, 502, 503, 504}. */
  retryStatusCodes?: ReadonlySet<number>;
  /** Add random jitter up to 50% of base. Default true. */
  jitter?: boolean;
}

const DEFAULT_RETRY_STATUS_CODES = new Set([429, 500, 502, 503, 504]);

function computeBackoffMs(
  attempt: number,
  opts: Required<RetryOptions>,
): number {
  let base = opts.backoffBaseMs * Math.pow(2, attempt);
  if (opts.jitter) {
    base += Math.random() * base * 0.5;
  }
  return Math.min(base, opts.backoffMaxMs);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Call `operation` with exponential backoff on transient errors.
 *
 * Respects 503 `Retry-After` header embedded in `NanorixError.body`.
 * Idempotent operations only (GET / DELETE / Stripe-idempotency-keyed POSTs).
 *
 * @param operation Zero-argument async function to retry.
 * @param opts RetryOptions controlling backoff parameters.
 * @returns The resolved value of `operation` on success.
 * @throws The last error raised by `operation` after all attempts exhausted.
 */
export async function withRetry<T>(
  operation: () => Promise<T>,
  opts?: RetryOptions,
): Promise<T> {
  const maxAttempts = opts?.maxAttempts ?? 3;
  const backoffBaseMs = opts?.backoffBaseMs ?? 500;
  const backoffMaxMs = opts?.backoffMaxMs ?? 30_000;
  const retryStatusCodes = opts?.retryStatusCodes ?? DEFAULT_RETRY_STATUS_CODES;
  const jitter = opts?.jitter ?? true;

  const resolvedOpts: Required<RetryOptions> = {
    maxAttempts,
    backoffBaseMs,
    backoffMaxMs,
    retryStatusCodes,
    jitter,
  };

  let lastError: unknown;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    try {
      return await operation();
    } catch (err) {
      lastError = err;
      if (attempt >= maxAttempts - 1) break;
      if (!(err instanceof NanorixError)) throw err;
      const status = err.statusCode;
      if (
        status === null ||
        status === undefined ||
        !retryStatusCodes.has(status)
      ) {
        throw err;
      }
      // Respect Retry-After in body
      const body = err.body as Record<string, unknown> | undefined;
      const retryAfter = body?.["retry_after"];
      let sleepMs: number;
      if (typeof retryAfter === "number") {
        sleepMs = retryAfter * 1000;
      } else {
        sleepMs = computeBackoffMs(attempt, resolvedOpts);
      }
      await sleep(sleepMs);
    }
  }
  throw lastError;
}

// ─── AuditProof download ────────────────────────────────────────────────────

/**
 * Download an AuditProof (VerificationCdp) JSON to a local file.
 *
 * Fetches `GET /v1/capsules/{capsuleId}/cdp/verify` and writes the
 * response JSON to `destPath`. Sets file permissions to 0o600 after writing.
 *
 * @param client A `NanorixClient` instance.
 * @param capsuleId The capsule ID to fetch the proof for.
 * @param destPath Destination file path.
 */
export async function downloadAuditProof(
  client: NanorixClient,
  capsuleId: string,
  destPath: string,
): Promise<void> {
  // Access the HTTP transport through the client's internal _http field.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const raw = await (client as any)._http.request(
    "GET",
    `/v1/capsules/${capsuleId}/cdp/verify`,
  );
  const dir = dirname(destPath);
  await mkdir(dir, { recursive: true });
  await writeFile(destPath, JSON.stringify(raw, null, 2), {
    encoding: "utf-8",
    mode: 0o600,
  });
}

// ─── AuditProof verification ────────────────────────────────────────────────

/** Genesis hash — SHA-512(""). Forever-stable. */
export const GENESIS_HASH =
  "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
  "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e";

/** Canonical method strings per subsystem. Forever-stable. */
export const METHOD_MAP: Record<string, string> = {
  eee_namespace: "procfs_verification",
  eee_tmpfs: "mountinfo_verification",
  eee_memory: "dod_5220_multipass_wipe",
  dire_keys: "ed25519_key_destruction",
  dire_identity: "credential_incineration",
  fgx_forensic: "merkle_tree_verification",
  rzl_audit: "hash_chain_validation",
  capsule_destroy: "capsule_lifecycle_verification",
};

const SUPPORTED_CDP_VERSIONS = new Set(["1.0", "2.0", "2.1"]);
const CHAIN_STEP_COUNT = 8;

/** Closed-set failure reason wire types. Forever-stable. Additive only. */
export const FailureReasonType = {
  ALGORITHM_UNSUPPORTED: "algorithm_unsupported",
  AUTHORITY_ID_MISMATCH: "authority_id_mismatch",
  AUTHORITY_MODE_MISMATCH: "authority_mode_mismatch",
  AUTHORITY_REVOKED: "authority_revoked",
  CDP_VERSION_UNSUPPORTED: "cdp_version_unsupported",
  DIAGNOSTIC_PROOF_REFUSED: "diagnostic_proof_refused",
  FINAL_HASH_MISMATCH: "final_hash_mismatch",
  GENESIS_HASH_MISMATCH: "genesis_hash_mismatch",
  REGION_MISMATCH: "region_mismatch",
  REQUIRED_FIELD_MISSING: "required_field_missing",
  RESERVED: "reserved",
  SIGNATURE_MISMATCH: "signature_mismatch",
  SIGNING_KEY_VERSION_UNKNOWN: "signing_key_version_unknown",
  STEP_COUNT_INVALID: "step_count_invalid",
  STEP_HASH_MISMATCH: "step_hash_mismatch",
} as const;

export type FailureReasonTypeValue =
  (typeof FailureReasonType)[keyof typeof FailureReasonType];

/** Structured failure reason. Wire form: {"type": "<snake_case>", ...payload}. */
export interface FailureReason {
  type: string;
  // Per-variant payload fields (undefined when not applicable)
  found?: string;
  field?: string;
  expected?: number;
  step_idx?: number;
  subsystem?: string;
  claimed?: string;
  computed?: string;
  reason?: string;
  version?: string;
  required?: string;
  actual?: string;
  claimed_authority_id?: string | null;
  expected_authority_id?: string;
  drift_position?: number;
  drift_field?: string;
}

/** Structural metadata extracted during verification. */
export interface VerificationMetadata {
  cdpVersion: string | null;
  capsuleId: string | null;
  region: string | null;
  signingKeyVersion: string | null;
  algorithm: string | null;
  stepCount: number | null;
  activityEventCount: number | null;
}

/** Result of AuditProof verification. */
export interface VerificationResult {
  valid: boolean;
  failure_reason: FailureReason | null;
  stage_reached: number;
  metadata: VerificationMetadata;
}

/** Wire-form projection matching fixture corpus expected.json. */
export interface VerificationWireForm {
  valid: boolean;
  failure_reason: FailureReason | null;
}

/** Customer-side policy configuration. Field-additive. */
export interface VerifierPolicy {
  rejectDiagnostic?: boolean;
  requiredRegion?: string;
  requiredAuthorityId?: string;
}

/** Return the wire-form projection for fixture corpus comparison. */
export function toWireForm(result: VerificationResult): VerificationWireForm {
  return {
    valid: result.valid,
    failure_reason: result.failure_reason,
  };
}

// ─── Chain hash computation ─────────────────────────────────────────────────

/** Get a SubtleCrypto instance (Node 18+ / browser). */
function getSubtle(): SubtleCrypto {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  if (g.crypto?.subtle) return g.crypto.subtle as SubtleCrypto;
  throw new Error(
    "Web Crypto subtle not available (need Node 18+ or modern browser)",
  );
}

/** Hex-encode ArrayBuffer or Uint8Array. */
function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * Reproduce one chain step's SHA-512 hash. Forever-stable.
 * Formula: SHA-512(prev || 0x00 || subsystem || 0x00 || "destroy" || 0x00 || method || 0x00 || timestamp)
 */
export async function computeStepHash(
  prevHash: string,
  subsystem: string,
  method: string,
  timestamp: string,
): Promise<string> {
  const enc = new TextEncoder();
  const NULL = new Uint8Array([0]);
  const parts: Uint8Array[] = [
    enc.encode(prevHash),
    NULL,
    enc.encode(subsystem),
    NULL,
    enc.encode("destroy"),
    NULL,
    enc.encode(method),
    NULL,
    enc.encode(timestamp),
  ];
  const totalLen = parts.reduce((acc, p) => acc + p.length, 0);
  const buf = new Uint8Array(totalLen);
  let off = 0;
  for (const p of parts) {
    buf.set(p, off);
    off += p.length;
  }
  const subtle = getSubtle();
  const digest = await subtle.digest("SHA-512", buf.buffer as ArrayBuffer);
  return bytesToHex(digest);
}

/** Return canonical method string for subsystem. Forever-stable. */
export function lookupMethod(subsystem: string): string {
  return METHOD_MAP[subsystem] ?? "";
}

/** Strip sha512: prefix from hash fields. */
function stripSha512(s: string): string {
  return s.startsWith("sha512:") ? s.slice("sha512:".length) : s;
}

function makeEmpty(): VerificationMetadata {
  return {
    cdpVersion: null,
    capsuleId: null,
    region: null,
    signingKeyVersion: null,
    algorithm: null,
    stepCount: null,
    activityEventCount: null,
  };
}

function strOrEmpty(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/**
 * Verify an AuditProof through the 4-stage pipeline.
 *
 * Implements stages 1–4 of the Go reference verifier (verifier.go),
 * byte-equivalent with Rust V1 verifier on the 100-fixture corpus.
 *
 * Stages 5–8 are not wired in V1 per Go verifier contract.
 *
 * @param jsonBytes AuditProof as Uint8Array, Buffer, or already-parsed object.
 * @param policy Optional VerifierPolicy for authority-pin checks.
 */
export async function verifyAuditProof(
  jsonBytes: Uint8Array | Buffer | Record<string, unknown>,
  policy?: VerifierPolicy,
): Promise<VerificationResult> {
  const pol = policy ?? {};
  const meta = makeEmpty();

  // Stage 0: parse
  let proof: Record<string, unknown>;
  if (jsonBytes instanceof Uint8Array || Buffer.isBuffer(jsonBytes)) {
    try {
      proof = JSON.parse(new TextDecoder().decode(jsonBytes)) as Record<
        string,
        unknown
      >;
    } catch {
      return {
        valid: false,
        failure_reason: {
          type: FailureReasonType.REQUIRED_FIELD_MISSING,
          field: "json_root",
        },
        stage_reached: 1,
        metadata: meta,
      };
    }
  } else {
    proof = jsonBytes;
  }

  // Stage 1: cdp_version present
  const cdpVersion = proof["cdp_version"];
  if (typeof cdpVersion !== "string") {
    return {
      valid: false,
      failure_reason: {
        type: FailureReasonType.REQUIRED_FIELD_MISSING,
        field: "cdp_version",
      },
      stage_reached: 1,
      metadata: meta,
    };
  }
  meta.cdpVersion = cdpVersion;

  // Stage 2: cdp_version recognized
  if (!SUPPORTED_CDP_VERSIONS.has(cdpVersion)) {
    return {
      valid: false,
      failure_reason: {
        type: FailureReasonType.CDP_VERSION_UNSUPPORTED,
        found: cdpVersion,
      },
      stage_reached: 2,
      metadata: meta,
    };
  }

  // Populate metadata
  if (typeof proof["capsule_id"] === "string")
    meta.capsuleId = proof["capsule_id"];
  if (typeof proof["region"] === "string") meta.region = proof["region"];
  if (typeof proof["signing_key_version"] === "string")
    meta.signingKeyVersion = proof["signing_key_version"];
  const att = proof["attestation"];
  if (att && typeof att === "object" && !Array.isArray(att)) {
    const algo = (att as Record<string, unknown>)["algorithm"];
    if (typeof algo === "string") meta.algorithm = algo;
  }

  // Policy-pin gate: fires BEFORE chain checks, stage_reached = 2.
  if (pol.requiredAuthorityId) {
    const sa = proof["signing_authority"];
    let claimedId: string | null = null;
    if (sa && typeof sa === "object" && !Array.isArray(sa)) {
      const aid = (sa as Record<string, unknown>)["authority_id"];
      if (typeof aid === "string") claimedId = aid;
    }
    if (claimedId === null) {
      return {
        valid: false,
        failure_reason: {
          type: FailureReasonType.AUTHORITY_ID_MISMATCH,
          claimed_authority_id: null,
          expected_authority_id: pol.requiredAuthorityId,
          reason: "verifier_policy_demands_customer_hsm_audit_proof_has_none",
        },
        stage_reached: 2,
        metadata: meta,
      };
    }
    if (claimedId !== pol.requiredAuthorityId) {
      return {
        valid: false,
        failure_reason: {
          type: FailureReasonType.AUTHORITY_ID_MISMATCH,
          claimed_authority_id: claimedId,
          expected_authority_id: pol.requiredAuthorityId,
          reason: "verifier_policy_authority_id_mismatch",
        },
        stage_reached: 2,
        metadata: meta,
      };
    }
  }

  // Region-pin: V1 not enforced (matches Go V1 behavior).

  // Stage 3: chain reproducibility
  const chainRaw = proof["chain"];
  if (!Array.isArray(chainRaw)) {
    return {
      valid: false,
      failure_reason: {
        type: FailureReasonType.REQUIRED_FIELD_MISSING,
        field: "chain",
      },
      stage_reached: 3,
      metadata: meta,
    };
  }
  const stepCount = chainRaw.length;
  meta.stepCount = stepCount;

  if (stepCount !== CHAIN_STEP_COUNT) {
    return {
      valid: false,
      failure_reason: {
        type: FailureReasonType.STEP_COUNT_INVALID,
        expected: CHAIN_STEP_COUNT,
        found: String(stepCount),
      },
      stage_reached: 3,
      metadata: meta,
    };
  }

  const timestamp = strOrEmpty(proof["destroyed_at"]);
  let prevHash = GENESIS_HASH;

  for (let idx = 0; idx < chainRaw.length; idx++) {
    const stepRaw = chainRaw[idx];
    if (!stepRaw || typeof stepRaw !== "object" || Array.isArray(stepRaw)) {
      return {
        valid: false,
        failure_reason: {
          type: FailureReasonType.STEP_HASH_MISMATCH,
          step_idx: idx,
          subsystem: "",
        },
        stage_reached: 3,
        metadata: meta,
      };
    }
    const step = stepRaw as Record<string, unknown>;
    const subsystem = strOrEmpty(step["subsystem"]);
    const claimedChainHash = strOrEmpty(step["chain_hash"]);
    const method = lookupMethod(subsystem);

    const recomputed = await computeStepHash(
      prevHash,
      subsystem,
      method,
      timestamp,
    );
    const claimedStripped = stripSha512(claimedChainHash);

    if (recomputed !== claimedStripped) {
      return {
        valid: false,
        failure_reason: {
          type: FailureReasonType.STEP_HASH_MISMATCH,
          step_idx: idx,
          subsystem,
        },
        stage_reached: 3,
        metadata: meta,
      };
    }
    prevHash = recomputed;
  }

  // Stage 4: final_hash binding
  const claimedFinal = strOrEmpty(proof["final_hash"]);
  const lastStep = chainRaw[chainRaw.length - 1] as Record<string, unknown>;
  const lastChainHash = lastStep ? strOrEmpty(lastStep["chain_hash"]) : "";

  if (stripSha512(claimedFinal) !== stripSha512(lastChainHash)) {
    return {
      valid: false,
      failure_reason: {
        type: FailureReasonType.FINAL_HASH_MISMATCH,
        claimed: claimedFinal,
        computed: lastChainHash,
      },
      stage_reached: 4,
      metadata: meta,
    };
  }

  // Stages 5–8: not wired in V1 (matches Go verifier).
  return {
    valid: true,
    failure_reason: null,
    stage_reached: 4,
    metadata: meta,
  };
}
