/**
 * Express.js middleware — Tier C (native framework) integration.
 *
 * TypeScript analog of `nanorix.integrations.flask_middleware` (Python
 * WSGI). Wraps every inbound HTTP request to a customer's Express app
 * in a Nanorix capsule lifecycle. Each request → one capsule → one
 * AuditProof with `cdp_kind: "request"`.
 *
 * ## Three-tier integration model
 *
 * | Tier | Effort | Claim                                              |
 * |------|--------|----------------------------------------------------|
 * | A    | 5 min  | Per-call receipts (LangChain.js callback / proxy)  |
 * | B    | 30 min | Full workflow AuditProof (decorator)        |
 * | C    | 1 hr   | Per-route receipts (Express / Next.js middleware)  |
 *
 * This middleware is Tier C. The claim is per-request: every HTTP
 * request that survives the middleware gate is bound into a sealed
 * capsule lifecycle. The AuditProof carries `cdp_kind: "request"` +
 * the customer-declared framework + agent_id + capsulefile in metadata.
 * Plaintext request / response bodies are NOT bound — only canonical
 * structural identifiers (method, path, status, latency).
 *
 * ## Public API
 *
 * ```ts
 * import express from "express";
 * import { NanorixClient } from "@nanorix/sdk";
 * import {
 *   nanorixMiddleware,
 *   nanorixErrorHandler,
 * } from "@nanorix/sdk";
 *
 * const app = express();
 * const client = new NanorixClient({ apiKey: "nrx_live_..." });
 *
 * app.use(
 *   nanorixMiddleware({
 *     client,
 *     framework: "HIPAA",
 *     capsulefile: "pa-decision-api",
 *     bindAuditProof: true,
 *     excludePaths: ["/health", "/metrics"],
 *   }),
 * );
 *
 * app.post("/v1/decide", async (req, res) => {
 *   // req.nanorix.capsule available
 *   res.json({ ok: true });
 * });
 *
 * app.use(nanorixErrorHandler({ client }));
 * ```
 *
 * ## Hook → ActivityEvent mapping
 *
 * Mirrors `flask_middleware.py` exactly (Tier-C parity invariant):
 *
 * | Lifecycle step              | ActivityEvent emitted        |
 * |-----------------------------|------------------------------|
 * | Middleware enter            | (none — record startTs)      |
 * | `res` finish 2xx-4xx        | reasoning_step_completed     |
 * | `res` finish 5xx            | tool_call_failed             |
 * | Express error path          | tool_call_failed             |
 *
 * ## Trust-boundary discipline
 *
 * - **Customer-sole-declarer.** Customer declares
 *   `framework=` and `capsulefile=` at middleware registration time.
 *   The middleware does NOT classify request payloads.
 * - **Narrow signed claim**.
 *   Captures structural HTTP request facts; NEVER asserts request-content
 *   conformance or regulatory satisfaction.
 * - **Zero-retention.** Request/response bodies NEVER enter
 *   the AuditProof. Only structural identifiers + their canonical-JSON
 *   SHA-512 hex digests bind.
 * - ** brand.** Response header is `X-Nanorix-AuditProof-Id`.
 *
 * ## Optional dependency
 *
 * The `express` package is NOT a runtime dependency of this SDK.
 * Customer brings Express; this module provides minimal structural
 * `Request`/`Response`/`NextFunction` interfaces inlined below.
 */

import {
  DEFAULT_BUFFER_PATH,
  type ToolCallFailureKind,
  emitReasoningStepCompleted,
  emitToolCallFailed,
} from "../helpers/activity_events.js";

// ── Constants ───────────────────────────────────────────────────────

/** reserved-slot wire form for per-HTTP-request AuditProofs. */
export const CDP_KIND_REQUEST = "request";

/** Reserved metadata-key value emitted via `nanorix:wrapper_kind`. */
export const WRAPPER_KIND = "middleware";

export const FRAMEWORK_HIPAA = "HIPAA";
export const FRAMEWORK_GDPR = "GDPR";
export const FRAMEWORK_SOC2 = "SOC2";
export const FRAMEWORK_PCI_DSS = "PCI_DSS";
export const FRAMEWORK_HITRUST = "HITRUST";
export const FRAMEWORK_EU_AI_ACT = "EU_AI_ACT";

/** Canonical framework labels (customer may pass any non-empty string). */
export const ALLOWED_FRAMEWORKS: ReadonlySet<string> = new Set<string>([
  FRAMEWORK_HIPAA,
  FRAMEWORK_GDPR,
  FRAMEWORK_SOC2,
  FRAMEWORK_PCI_DSS,
  FRAMEWORK_HITRUST,
  FRAMEWORK_EU_AI_ACT,
]);

export const RESPONSE_HEADER_AUDIT_PROOF_ID = "X-Nanorix-AuditProof-Id";
export const RESPONSE_HEADER_CDP_KIND = "X-Nanorix-CdpKind";
export const RESPONSE_HEADER_CAPSULE_ID = "X-Nanorix-Capsule-Id";

const STATUS_TO_FAILURE_KIND: Readonly<Record<number, ToolCallFailureKind>> = {
  400: "invalid_args",
  401: "permission_denied",
  403: "permission_denied",
  408: "timeout",
  422: "invalid_args",
  499: "cancelled",
};

function mapStatusToFailureKind(status: number): ToolCallFailureKind {
  if (status in STATUS_TO_FAILURE_KIND) {
    return STATUS_TO_FAILURE_KIND[status];
  }
  return "upstream_failure";
}

function classifyError(err: unknown): ToolCallFailureKind {
  if (!(err instanceof Error)) return "upstream_failure";
  const className = err.constructor?.name ?? "Error";
  const mapping: Readonly<Record<string, ToolCallFailureKind>> = {
    TimeoutError: "timeout",
    AbortError: "cancelled",
    PermissionError: "permission_denied",
    ValidationError: "invalid_args",
    TypeError: "invalid_args",
    RangeError: "invalid_args",
    HTTPError: "upstream_failure",
    FetchError: "upstream_failure",
    NetworkError: "upstream_failure",
  };
  if (className in mapping) return mapping[className];
  let proto = Object.getPrototypeOf(err);
  while (proto && proto.constructor) {
    const parentName: string = proto.constructor.name;
    if (parentName in mapping) return mapping[parentName];
    if (parentName === "Object") break;
    proto = Object.getPrototypeOf(proto);
  }
  return "upstream_failure";
}

function resolveFrameworkForPath(opts: {
  path: string;
  defaultFramework: string;
  frameworkPerRoute?: ReadonlyMap<string, string> | Readonly<Record<string, string>>;
}): string {
  const fpr = opts.frameworkPerRoute;
  if (!fpr) return opts.defaultFramework;
  const entries: [string, string][] =
    fpr instanceof Map
      ? Array.from(fpr.entries())
      : Object.entries(fpr as Record<string, string>);
  if (entries.length === 0) return opts.defaultFramework;
  // Longest-prefix-first match (deterministic).
  entries.sort((a, b) => b[0].length - a[0].length);
  for (const [prefix, framework] of entries) {
    if (opts.path.startsWith(prefix)) return framework;
  }
  return opts.defaultFramework;
}

// ── Minimal Express type surface (inlined; customer brings express) ─

/**
 * Minimal structural `Request` interface. Mirrors the subset of
 * Express `Request` the middleware reads. Customer's actual Express
 * `Request` is structurally compatible.
 */
export interface ExpressRequestLike {
  readonly method: string;
  readonly path: string;
  readonly originalUrl?: string;
  readonly url?: string;
  /** Per-request mutable bag attached by the middleware. */
  nanorix?: NanorixRequestContext;
  /** TypeScript declaration-merging escape hatch. */
  [key: string]: unknown;
}

/**
 * Minimal structural `Response` interface. The middleware writes
 * headers + subscribes to the `finish` event.
 */
export interface ExpressResponseLike {
  readonly statusCode: number;
  setHeader(name: string, value: string): void;
  on(event: "finish" | "close", listener: () => void): void;
}

/** Express `next()` callback (no-args or single-error-arg). */
export type ExpressNextFunction = (err?: unknown) => void;

/** Express middleware signature `(req, res, next) => void`. */
export type ExpressRequestHandler = (
  req: ExpressRequestLike,
  res: ExpressResponseLike,
  next: ExpressNextFunction,
) => void;

/** Express error-handler signature `(err, req, res, next) => void`. */
export type ExpressErrorRequestHandler = (
  err: unknown,
  req: ExpressRequestLike,
  res: ExpressResponseLike,
  next: ExpressNextFunction,
) => void;

// ── Public types ────────────────────────────────────────────────────

/** Per-request mutable bag attached to `req.nanorix` by the middleware. */
export interface NanorixRequestContext {
  /** Underlying capsule's live wrapper (`LiveCapsule`). May be null on
   * exclude path or on capsule-create failure (best-effort discipline). */
  capsule: NanorixCapsuleLike | null;
  /** Capsule UUID for response-header attachment. */
  capsuleId: string | null;
  /** Resolved framework label for this request. */
  framework: string;
  /** Monotonic start time (ms) for duration computation. */
  startTsMs: number;
  /** Resolved metadata envelope built at request start. */
  metadata: Readonly<Record<string, string>>;
}

/** Structural shape of a Nanorix capsule the middleware uses. */
export interface NanorixCapsuleLike {
  readonly id: string;
  destroy(): Promise<unknown>;
}

/** Structural shape of the `NanorixClient.capsules` resource the middleware uses. */
export interface NanorixCapsuleResourceLike {
  create(opts: {
    capsulefile?: string;
    maxLifetimeSeconds?: number;
    egressProfile?: string;
    metadata?: Record<string, string>;
    idempotencyKey?: string;
  }): Promise<NanorixCapsuleLike>;
}

/** Structural shape of a Nanorix client (`NanorixClient`) for DI ergonomics. */
export interface NanorixClientLike {
  readonly capsules: NanorixCapsuleResourceLike;
}

/** Options for `nanorixMiddleware()`. */
export interface NanorixMiddlewareOptions {
  /** REQUIRED. Nanorix client instance. */
  client: NanorixClientLike;
  /** REQUIRED. Compliance framework label (customer-declared). */
  framework: string;
  /** REQUIRED. Capsulefile ID or local name. */
  capsulefile: string;
  /** Optional framework version (e.g., `"2026-02"`). */
  frameworkVersion?: string;
  /** Optional sub-agent identifier. */
  agentId?: string;
  /** Optional path-prefix → framework override map (longest-prefix wins). */
  frameworkPerRoute?:
    | ReadonlyMap<string, string>
    | Readonly<Record<string, string>>;
  /** Optional path prefixes the middleware does NOT wrap. */
  excludePaths?: readonly string[];
  /** Optional capsule lifetime ceiling (default 300s). */
  maxLifetimeSeconds?: number;
  /** Optional egress profile ID (Business+ tier). */
  egressProfile?: string;
  /** Attach AuditProof response headers (default `true`). */
  bindAuditProof?: boolean;
  /** Optional callback invoked after each capsule destroy. */
  onAuditProof?: (cdp: unknown) => void | Promise<void>;
  /** Optional structural logger; falls back to no-op. */
  logger?: NanorixLogger;
  /** Override for the ActivityEvent buffer path. */
  bufferPath?: string;
}

/** Options for `nanorixErrorHandler()`. */
export interface NanorixErrorHandlerOptions {
  /** Optional Nanorix client (only required if errors should reach onAuditProof). */
  client?: NanorixClientLike;
  /** Optional callback invoked after capsule cleanup on error path. */
  onAuditProof?: (cdp: unknown) => void | Promise<void>;
  /** Optional structural logger; falls back to no-op. */
  logger?: NanorixLogger;
  /** Override for the ActivityEvent buffer path. */
  bufferPath?: string;
}

/** Structural logger surface (matches console-shape; customer can plug pino/winston). */
export interface NanorixLogger {
  debug?: (msg: string, meta?: Record<string, unknown>) => void;
  info?: (msg: string, meta?: Record<string, unknown>) => void;
  warn?: (msg: string, meta?: Record<string, unknown>) => void;
  error?: (msg: string, meta?: Record<string, unknown>) => void;
}

const NOOP_LOGGER: NanorixLogger = {};

// ── Public API ──────────────────────────────────────────────────────

/**
 * Build per-request canonical metadata envelope for AuditProof binding.
 *
 * Reserved keys (`nanorix:*`):
 * - `nanorix:wrapper_kind` — `"middleware"`
 * - `nanorix:framework` — customer-declared
 * - `nanorix:framework_version` — optional
 * - `nanorix:agent_id` — optional
 * - `nanorix:request_method` — HTTP method
 * - `nanorix:request_path` — URL path
 *
 * Plaintext request bodies / query params / headers are NOT included —
 * only structural identifiers.
 */
export function toMetadata(opts: {
  method: string;
  path: string;
  framework: string;
  frameworkVersion?: string;
  agentId?: string;
}): Record<string, string> {
  const metadata: Record<string, string> = {
    "nanorix:wrapper_kind": WRAPPER_KIND,
    "nanorix:framework": opts.framework,
    "nanorix:request_method": opts.method,
    "nanorix:request_path": opts.path,
  };
  if (opts.frameworkVersion !== undefined) {
    metadata["nanorix:framework_version"] = opts.frameworkVersion;
  }
  if (opts.agentId !== undefined) {
    metadata["nanorix:agent_id"] = opts.agentId;
  }
  return metadata;
}

/**
 * Express middleware factory.
 *
 * Returns an `ExpressRequestHandler` the customer wires via `app.use()`
 * (or per-route). Wraps every inbound HTTP request in a Nanorix
 * capsule lifecycle.
 *
 * @throws Error if `client`, `framework`, or `capsulefile` is missing
 * or empty (no silent default customer-sole-declarer).
 */
export function nanorixMiddleware(
  opts: NanorixMiddlewareOptions,
): ExpressRequestHandler {
  if (!opts.client) {
    throw new Error(
      "nanorixMiddleware requires an explicit `client` option — a NanorixClient instance.",
    );
  }
  if (!opts.framework || typeof opts.framework !== "string") {
    throw new Error(
      "nanorixMiddleware requires an explicit `framework` option (e.g., framework: 'HIPAA'). " +
        "No silent default customer-sole-declarer discipline.",
    );
  }
  if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
    throw new Error(
      "nanorixMiddleware requires an explicit `capsulefile` option.",
    );
  }

  const bindAuditProof = opts.bindAuditProof ?? true;
  const excludePaths = opts.excludePaths ?? [];
  const maxLifetimeSeconds = opts.maxLifetimeSeconds ?? 300;
  const logger = opts.logger ?? NOOP_LOGGER;
  const bufferPath = opts.bufferPath ?? DEFAULT_BUFFER_PATH;

  function isExcluded(path: string): boolean {
    for (const prefix of excludePaths) {
      if (path.startsWith(prefix)) return true;
    }
    return false;
  }

  return function nanorixRequestHandler(
    req: ExpressRequestLike,
    res: ExpressResponseLike,
    next: ExpressNextFunction,
  ): void {
    const path = req.path;
    if (isExcluded(path)) {
      next();
      return;
    }

    const framework = resolveFrameworkForPath({
      path,
      defaultFramework: opts.framework,
      frameworkPerRoute: opts.frameworkPerRoute,
    });

    const metadata = toMetadata({
      method: req.method,
      path,
      framework,
      frameworkVersion: opts.frameworkVersion,
      agentId: opts.agentId,
    });

    const startTsMs = Date.now();

    // Pre-attach context with null capsule so the route handler can
    // always read `req.nanorix` even if capsule create fails.
    const ctx: NanorixRequestContext = {
      capsule: null,
      capsuleId: null,
      framework,
      startTsMs,
      metadata,
    };
    req.nanorix = ctx;

    // Create the capsule asynchronously; the request proceeds in
    // parallel. If create fails the request is NOT blocked
    // (best-effort discipline mirrors flask_middleware._before_request).
    const createPromise = opts.client.capsules
      .create({
        capsulefile: opts.capsulefile,
        maxLifetimeSeconds,
        egressProfile: opts.egressProfile,
        metadata: { ...metadata },
      })
      .then((capsule) => {
        ctx.capsule = capsule;
        ctx.capsuleId = capsule.id;
        if (bindAuditProof) {
          try {
            res.setHeader(RESPONSE_HEADER_CAPSULE_ID, capsule.id);
            res.setHeader(
              RESPONSE_HEADER_AUDIT_PROOF_ID,
              "cap_" + capsule.id.replace(/-/g, ""),
            );
            res.setHeader(RESPONSE_HEADER_CDP_KIND, CDP_KIND_REQUEST);
          } catch (headerErr) {
            // Headers already sent — best-effort only.
            logger.debug?.("nanorix: setHeader after send", {
              err: String(headerErr),
            });
          }
        }
      })
      .catch((err) => {
        logger.warn?.("nanorix: capsule create failed; request continues unwrapped", {
          err: String(err),
        });
      });

    // Subscribe to `res.finish` for the success/failure-status emission.
    let finished = false;
    res.on("finish", () => {
      if (finished) return;
      finished = true;
      const durationMs = Math.max(0, Date.now() - startTsMs);
      const status = res.statusCode;

      // Emit + destroy AFTER create resolves (so the buffer is open).
      void createPromise.then(async () => {
        await emitRequestOutcome({
          method: req.method,
          path,
          status,
          durationMs,
          err: null,
          agentId: opts.agentId,
          bufferPath,
        });

        const cap = ctx.capsule;
        if (cap !== null) {
          try {
            const cdp = await cap.destroy();
            if (opts.onAuditProof !== undefined) {
              try {
                await opts.onAuditProof(cdp);
              } catch (cbErr) {
                logger.debug?.("nanorix: onAuditProof callback threw", {
                  err: String(cbErr),
                });
              }
            }
          } catch (destroyErr) {
            logger.warn?.("nanorix: capsule destroy failed", {
              err: String(destroyErr),
            });
          }
        }
      });
    });

    next();
  };
}

/**
 * Express error-handler middleware factory.
 *
 * Mounted via `app.use(nanorixErrorHandler(opts))` AFTER the route
 * handlers. Triggers capsule cleanup if a route raised before the
 * `res.finish` event could fire. Mirrors `flask_middleware._teardown_request`.
 *
 * The handler ALWAYS calls `next(err)` so Express's normal error-flow
 * proceeds (the customer's own error renderer / 500-formatter still runs).
 */
export function nanorixErrorHandler(
  opts: NanorixErrorHandlerOptions = {},
): ExpressErrorRequestHandler {
  const logger = opts.logger ?? NOOP_LOGGER;
  const bufferPath = opts.bufferPath ?? DEFAULT_BUFFER_PATH;

  return function nanorixErrorMiddleware(
    err: unknown,
    req: ExpressRequestLike,
    res: ExpressResponseLike,
    next: ExpressNextFunction,
  ): void {
    const ctx = req.nanorix;
    if (ctx !== undefined) {
      const durationMs = Math.max(0, Date.now() - ctx.startTsMs);
      void (async () => {
        await emitRequestOutcome({
          method: req.method,
          path: req.path,
          status: 500,
          durationMs,
          err,
          agentId: undefined,
          bufferPath,
        });
        const cap = ctx.capsule;
        if (cap !== null) {
          try {
            const cdp = await cap.destroy();
            if (opts.onAuditProof !== undefined) {
              try {
                await opts.onAuditProof(cdp);
              } catch (cbErr) {
                logger.debug?.("nanorix: onAuditProof callback threw", {
                  err: String(cbErr),
                });
              }
            }
          } catch (destroyErr) {
            logger.warn?.("nanorix: capsule destroy failed on error path", {
              err: String(destroyErr),
            });
          }
        }
      })();
    }
    next(err);
  };
}

// ── Internals ───────────────────────────────────────────────────────

interface EmitRequestOutcomeArgs {
  method: string;
  path: string;
  status: number;
  durationMs: number;
  err: unknown;
  agentId: string | undefined;
  bufferPath: string;
}

async function emitRequestOutcome(args: EmitRequestOutcomeArgs): Promise<void> {
  const endpoint = `${args.method} ${args.path}`;

  if (args.err !== null && args.err !== undefined) {
    try {
      await emitToolCallFailed({
        toolCallId: `err_${Date.now()}`,
        toolName: endpoint,
        failureReason: renderErrorReason(args.err),
        durationMs: args.durationMs,
        failureKind: classifyError(args.err),
        agentId: args.agentId,
        bufferPath: args.bufferPath,
      });
    } catch {
      // Buffer write failures are non-fatal (no `/data` mount outside capsule).
    }
    return;
  }

  if (args.status >= 500) {
    try {
      await emitToolCallFailed({
        toolCallId: `http_${args.status}_${Date.now()}`,
        toolName: endpoint,
        failureReason: `http_status_${args.status}`,
        durationMs: args.durationMs,
        failureKind: mapStatusToFailureKind(args.status),
        agentId: args.agentId,
        bufferPath: args.bufferPath,
      });
    } catch {
      // non-fatal
    }
    return;
  }

  try {
    await emitReasoningStepCompleted({
      stepIdx: 0,
      prompt: { method: args.method, path: args.path },
      response: { status: args.status },
      durationMs: args.durationMs,
      model: undefined,
      agentId: args.agentId,
      bufferPath: args.bufferPath,
    });
  } catch {
    // non-fatal
  }
}

function renderErrorReason(err: unknown): string {
  if (err instanceof Error) {
    return `${err.constructor?.name ?? "Error"}: ${err.message}`;
  }
  if (typeof err === "string") return err;
  if (err === null || err === undefined) return "unknown error";
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}
