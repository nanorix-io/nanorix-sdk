/**
 * Exception hierarchy for the Nanorix SDK.
 *
 * Every non-2xx HTTP response raises a typed exception. The base NanorixError
 * includes the HTTP status code, a human-readable message, and the raw response
 * body for debugging.
 *
 * Design rule: exceptions that involve capsule destruction (ExecutionError,
 * CeilingError) ALWAYS include the CDP, because destruction still happened
 * and the proof must be accessible.
 */
import type { CdpDocument } from "./models.js";

export class NanorixError extends Error {
  readonly statusCode?: number;
  readonly body?: Record<string, unknown>;

  constructor(
    message: string,
    opts?: { statusCode?: number; body?: Record<string, unknown> },
  ) {
    super(message);
    this.name = "NanorixError";
    this.statusCode = opts?.statusCode;
    this.body = opts?.body;
  }
}

/** 401 — Invalid or missing API key. */
export class AuthenticationError extends NanorixError {
  override name = "AuthenticationError";
}

/** 404 — Resource does not exist (or you don't have access). */
export class NotFoundError extends NanorixError {
  override name = "NotFoundError";
}

/** 409 — Resource state conflict (e.g., duplicate capsulefile name, capsule already destroyed). */
export class ConflictError extends NanorixError {
  override name = "ConflictError";
}

/** 400 — Invalid request parameters. */
export class BadRequestError extends NanorixError {
  override name = "BadRequestError";
}

/** 410 — Capsule has been destroyed. Only CDP remains. */
export class GoneError extends NanorixError {
  override name = "GoneError";
}

/** 413 — File or request exceeds tier size limit. */
export class PayloadTooLargeError extends NanorixError {
  override name = "PayloadTooLargeError";
}

/** 402/403 — Tier quota exceeded (capsulefile count, CDP count, concurrent capsules). */
export class QuotaExceededError extends NanorixError {
  override name = "QuotaExceededError";
}

/** 429 — Too many requests. SDK auto-retries with backoff before raising. */
export class RateLimitError extends NanorixError {
  override name = "RateLimitError";
  readonly retryAfter?: number;

  constructor(
    message: string,
    opts?: {
      statusCode?: number;
      body?: Record<string, unknown>;
      retryAfter?: number;
    },
  ) {
    super(message, opts);
    this.retryAfter = opts?.retryAfter;
  }
}

/**
 * Non-zero exit code from capsule execution.
 * The capsule may still be alive (workspace mode) or destroyed (.run() mode).
 * If destroyed, the CDP is attached — destruction still happened.
 */
export class ExecutionError extends NanorixError {
  override name = "ExecutionError";
  readonly exitCode: number;
  readonly stdout?: string;
  readonly stderr?: string;
  cdp?: CdpDocument; // Mutable — attached after destroy in .run()

  constructor(
    message: string,
    opts: {
      exitCode: number;
      stdout?: string;
      stderr?: string;
      cdp?: CdpDocument;
      statusCode?: number;
      body?: Record<string, unknown>;
    },
  ) {
    super(message, { statusCode: opts.statusCode, body: opts.body });
    this.exitCode = opts.exitCode;
    this.stdout = opts.stdout;
    this.stderr = opts.stderr;
    this.cdp = opts.cdp;
  }
}

/**
 * Safety ceiling destroyed the capsule before user-initiated destroy.
 * CDP is always attached — destruction happened, proof was generated.
 */
export class CeilingError extends NanorixError {
  override name = "CeilingError";
  readonly cdp?: CdpDocument;

  constructor(
    message: string,
    opts?: {
      cdp?: CdpDocument;
      statusCode?: number;
      body?: Record<string, unknown>;
    },
  ) {
    super(message, opts);
    this.cdp = opts?.cdp;
  }
}

/**
 * Execution exceeded the specified timeout.
 * The capsule is still alive — user can retry, debug, or destroy.
 */
export class TimeoutError extends NanorixError {
  override name = "TimeoutError";
  readonly capsuleId?: string;

  constructor(
    message: string,
    opts?: {
      capsuleId?: string;
      statusCode?: number;
      body?: Record<string, unknown>;
    },
  ) {
    super(message, opts);
    this.capsuleId = opts?.capsuleId;
  }
}

// ── 422 Unprocessable Entity (P0 #4 — pre-SDK-1.0-publish debuggability) ──

/**
 * 422 — Request well-formed but semantically rejected.
 *
 * Base class for input_provenance_rejected + secrets_injection_rejected.
 * Customer self-resolution paths key off `reason` field.
 */
export class UnprocessableEntityError extends NanorixError {
  override name = "UnprocessableEntityError";
  readonly reason?: string;

  constructor(
    message: string,
    opts?: {
      statusCode?: number;
      body?: Record<string, unknown>;
      reason?: string;
    },
  ) {
    super(message, opts);
    this.reason = opts?.reason;
  }
}

/**
 * 422 input_provenance_rejected — Manifest's input_hash didn't match the
 * runtime's observed hash. Customer cross-checks via `inputHash` field.
 */
export class InputProvenanceRejectedError extends UnprocessableEntityError {
  override name = "InputProvenanceRejectedError";
  readonly inputHash?: string;

  constructor(
    message: string,
    opts?: {
      statusCode?: number;
      body?: Record<string, unknown>;
      reason?: string;
      inputHash?: string;
    },
  ) {
    super(message, opts);
    this.inputHash = opts?.inputHash;
  }
}

/**
 * 422 secrets_injection_rejected — A declared secret failed to inject.
 *
 * Customer self-resolution paths key off `reason`:
 * - `secret_not_found` → rotate_or_recreate(secretId)
 * - `decryption_failed` → alert_security_team(secretId)
 */
export class SecretsInjectionRejectedError extends UnprocessableEntityError {
  override name = "SecretsInjectionRejectedError";
  readonly secretId?: string;

  constructor(
    message: string,
    opts?: {
      statusCode?: number;
      body?: Record<string, unknown>;
      reason?: string;
      secretId?: string;
    },
  ) {
    super(message, opts);
    this.secretId = opts?.secretId;
  }
}

// ── Status code → exception mapping ──

const STATUS_MAP: Record<
  number,
  new (msg: string, opts?: Record<string, unknown>) => NanorixError
> = {
  400: BadRequestError,
  401: AuthenticationError,
  402: QuotaExceededError,
  403: QuotaExceededError,
  404: NotFoundError,
  409: ConflictError,
  410: GoneError,
  413: PayloadTooLargeError,
  422: UnprocessableEntityError, // default; subclasses dispatch by error.code below
  429: RateLimitError,
};

/** Pull a string field from body.error if it's an object. */
function extractErrorField(
  body: Record<string, unknown>,
  field: string,
): string | undefined {
  const err = body.error;
  if (err !== null && typeof err === "object" && !Array.isArray(err)) {
    const v = (err as Record<string, unknown>)[field];
    return typeof v === "string" ? v : undefined;
  }
  return undefined;
}

/** Raise the appropriate exception for a non-2xx response. */
export function raiseForStatus(
  statusCode: number,
  body: Record<string, unknown>,
): void {
  if (statusCode >= 200 && statusCode < 300) return;

  // Extract message + error code: body.error.message preferred (typed errors);
  // fallback to body.error if it's a string; fallback to body.message.
  const err = body.error;
  let message: string;
  let errorCode: string | undefined;
  if (err !== null && typeof err === "object" && !Array.isArray(err)) {
    const errObj = err as Record<string, unknown>;
    message = (errObj.message as string) ?? `HTTP ${statusCode}`;
    errorCode = typeof errObj.code === "string" ? errObj.code : undefined;
  } else if (typeof err === "string") {
    message = err;
  } else {
    message = (body.message as string) ?? `HTTP ${statusCode}`;
  }

  // 422: disambiguate by error.code
  if (statusCode === 422) {
    if (errorCode === "input_provenance_rejected") {
      throw new InputProvenanceRejectedError(message, {
        statusCode,
        body,
        reason: extractErrorField(body, "reason"),
        inputHash: extractErrorField(body, "input_hash"),
      });
    }
    if (errorCode === "secrets_injection_rejected") {
      throw new SecretsInjectionRejectedError(message, {
        statusCode,
        body,
        reason: extractErrorField(body, "reason"),
        secretId: extractErrorField(body, "secret_id"),
      });
    }
    throw new UnprocessableEntityError(message, {
      statusCode,
      body,
      reason: extractErrorField(body, "reason"),
    });
  }

  const ErrorClass = STATUS_MAP[statusCode] ?? NanorixError;

  if (ErrorClass === RateLimitError) {
    throw new RateLimitError(message, {
      statusCode,
      body,
      retryAfter: body.retry_after as number | undefined,
    });
  }

  throw new ErrorClass(message, { statusCode, body });
}
