/**
 * Next.js (App Router) middleware — Tier C (native framework) integration.
 *
 * TypeScript analog of `nanorix.integrations.fastapi_middleware`
 * (Python ASGI). Two surfaces:
 *
 *  - `withNanorix(handler, opts)` — wraps the project-root `middleware.ts`
 *    handler (Next.js global / Edge middleware).
 *  - `wrapApiRoute(handler, opts)` — wraps an `app/api/.../route.ts` handler.
 *
 * Each wrapped request → one Nanorix capsule → one AuditProof with
 * `cdp_kind: "request"`.
 *
 * ## Three-tier integration model
 *
 * | Tier | Effort | Claim                                              |
 * |------|--------|----------------------------------------------------|
 * | A    | 5 min  | Per-call receipts (LangChain.js callback / proxy)  |
 * | B    | 30 min | Full workflow destruction proof (decorator)        |
 * | C    | 1 hr   | Per-route receipts (Express / Next.js middleware)  |
 *
 * ## Public API
 *
 * ### Global middleware (`middleware.ts` at project root)
 *
 * ```ts
 * import { NextRequest, NextResponse } from "next/server";
 * import { NanorixClient, withNanorix } from "@nanorix/sdk";
 *
 * const client = new NanorixClient({ apiKey: process.env.NANORIX_API_KEY! });
 *
 * export default withNanorix(
 *   async (req) => {
 *     return NextResponse.next();
 *   },
 *   { client, framework: "HIPAA", capsulefile: "pa-decision-api" },
 * );
 * ```
 *
 * ### Route handler wrapper (`app/api/.../route.ts`)
 *
 * ```ts
 * import { NextResponse } from "next/server";
 * import { wrapApiRoute } from "@nanorix/sdk";
 *
 * export const POST = wrapApiRoute(
 *   async (req, ctx) => {
 *     // ctx.nanorix.capsule available
 *     return NextResponse.json({ ok: true });
 *   },
 *   { client, framework: "HIPAA", capsulefile: "pa-decision-api" },
 * );
 * ```
 *
 * ## Hook → ActivityEvent mapping
 *
 * Mirrors `fastapi_middleware.py` exactly (Tier-C parity invariant):
 *
 * | Lifecycle step              | ActivityEvent emitted        |
 * |-----------------------------|------------------------------|
 * | Handler enter               | (none — record startTsMs)    |
 * | Response 2xx-4xx            | reasoning_step_completed     |
 * | Response 5xx                | tool_call_failed             |
 * | Thrown exception            | tool_call_failed (re-raised) |
 *
 * ## Trust-boundary discipline
 *
 * - **Customer-sole-declarer.** `framework` is REQUIRED.
 * - **Narrow signed claim**. Captures structural HTTP request facts;
 *   NEVER asserts request-content conformance.
 * - **Zero-retention.** Request/response bodies NEVER
 *   enter the AuditProof.
 * - ** brand.** Response header is `X-Nanorix-AuditProof-Id`.
 *
 * ## Optional dependency
 *
 * The `next` package is NOT a runtime dependency. Customer brings
 * Next.js; this module provides minimal structural `NextRequestLike` /
 * `NextResponseLike` interfaces inlined below.
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
  entries.sort((a, b) => b[0].length - a[0].length);
  for (const [prefix, framework] of entries) {
    if (opts.path.startsWith(prefix)) return framework;
  }
  return opts.defaultFramework;
}

// ── Minimal Next.js type surface (inlined; customer brings next) ────

/**
 * Minimal structural shape of `NextRequest`. Mirrors the Web-API
 * `Request` + Next.js extensions the middleware reads.
 */
export interface NextRequestLike {
  readonly method: string;
  readonly url: string;
  /** `nextUrl` may be a `URL` (Web API) or a `URL`-like with `.pathname`. */
  readonly nextUrl?: { readonly pathname: string };
  /** Web-API `Headers`-like. Optional — fallback to method+url. */
  readonly headers?: { get(name: string): string | null };
}

/**
 * Minimal structural shape of `NextResponse`. The middleware reads
 * `status` and writes via `headers.set`.
 */
export interface NextResponseLike {
  readonly status: number;
  readonly headers: { set(name: string, value: string): void };
}

/** Optional second argument (`{ params }`) Next.js passes to route handlers. */
export interface NextRouteContext {
  params?: Record<string, string | string[]>;
}

/** Per-request context attached to the Next.js handler's `req`. */
export interface NanorixRouteContext {
  capsule: NanorixCapsuleLike | null;
  capsuleId: string | null;
  framework: string;
  startTsMs: number;
  metadata: Readonly<Record<string, string>>;
}

/** Augmented request the wrapped handler receives. */
export type NextRequestWithNanorix = NextRequestLike & {
  /** Attached by `withNanorix` / `wrapApiRoute`. */
  nanorixCapsule?: NanorixRouteContext;
};

/** Structural shape of a Nanorix capsule the middleware uses. */
export interface NanorixCapsuleLike {
  readonly id: string;
  destroy(): Promise<unknown>;
}

/** Structural shape of `NanorixClient.capsules`. */
export interface NanorixCapsuleResourceLike {
  create(opts: {
    capsulefile?: string;
    maxLifetimeSeconds?: number;
    egressProfile?: string;
    metadata?: Record<string, string>;
    idempotencyKey?: string;
  }): Promise<NanorixCapsuleLike>;
}

/** Structural shape of `NanorixClient`. */
export interface NanorixClientLike {
  readonly capsules: NanorixCapsuleResourceLike;
}

/** Structural logger surface. */
export interface NanorixLogger {
  debug?: (msg: string, meta?: Record<string, unknown>) => void;
  info?: (msg: string, meta?: Record<string, unknown>) => void;
  warn?: (msg: string, meta?: Record<string, unknown>) => void;
  error?: (msg: string, meta?: Record<string, unknown>) => void;
}

const NOOP_LOGGER: NanorixLogger = {};

// ── Public option shapes ────────────────────────────────────────────

/** Common options shared by `withNanorix` and `wrapApiRoute`. */
export interface NanorixNextOptions {
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

/** Async handler signature for global middleware (`middleware.ts`). */
export type NextMiddlewareHandler = (
  req: NextRequestWithNanorix,
) => Promise<NextResponseLike> | NextResponseLike;

/** Async handler signature for route handlers (`app/api/.../route.ts`). */
export type NextRouteHandler = (
  req: NextRequestWithNanorix,
  ctx?: NextRouteContext,
) => Promise<NextResponseLike> | NextResponseLike;

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
 * Global Next.js middleware wrapper.
 *
 * Wraps a handler intended for the project-root `middleware.ts` file
 * (Edge-compatible). Every request that survives the exclude-paths
 * filter is bound into a sealed Nanorix capsule lifecycle.
 *
 * @throws Error if `client`, `framework`, or `capsulefile` is missing
 * or empty (no silent default customer-sole-declarer).
 */
export function withNanorix(
  handler: NextMiddlewareHandler,
  opts: NanorixNextOptions,
): NextMiddlewareHandler {
  validateOpts(opts, "withNanorix");

  const bindAuditProof = opts.bindAuditProof ?? true;
  const excludePaths = opts.excludePaths ?? [];
  const maxLifetimeSeconds = opts.maxLifetimeSeconds ?? 300;
  const logger = opts.logger ?? NOOP_LOGGER;
  const bufferPath = opts.bufferPath ?? DEFAULT_BUFFER_PATH;

  return async function nanorixNextMiddleware(
    req: NextRequestWithNanorix,
  ): Promise<NextResponseLike> {
    const path = extractPath(req);
    if (isPathExcluded(path, excludePaths)) {
      return await handler(req);
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

    // Best-effort capsule create. Failure does NOT block the request.
    let capsule: NanorixCapsuleLike | null = null;
    try {
      capsule = await opts.client.capsules.create({
        capsulefile: opts.capsulefile,
        maxLifetimeSeconds,
        egressProfile: opts.egressProfile,
        metadata: { ...metadata },
      });
    } catch (createErr) {
      logger.warn?.("nanorix: capsule create failed; request continues unwrapped", {
        err: String(createErr),
      });
    }

    const ctx: NanorixRouteContext = {
      capsule,
      capsuleId: capsule?.id ?? null,
      framework,
      startTsMs,
      metadata,
    };
    req.nanorixCapsule = ctx;

    let response: NextResponseLike | undefined;
    let thrown: unknown = null;
    try {
      response = await handler(req);
    } catch (err) {
      thrown = err;
    }
    const durationMs = Math.max(0, Date.now() - startTsMs);
    const status = thrown !== null ? 500 : (response?.status ?? 0);

    await emitRequestOutcome({
      method: req.method,
      path,
      status,
      durationMs,
      err: thrown,
      agentId: opts.agentId,
      bufferPath,
    });

    if (capsule !== null) {
      try {
        const cdp = await capsule.destroy();
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

    if (thrown !== null) {
      throw thrown;
    }
    // response is non-null because thrown === null.
    const resp = response as NextResponseLike;
    if (bindAuditProof && capsule !== null) {
      try {
        resp.headers.set(RESPONSE_HEADER_CAPSULE_ID, capsule.id);
        resp.headers.set(
          RESPONSE_HEADER_AUDIT_PROOF_ID,
          "cap_" + capsule.id.replace(/-/g, ""),
        );
        resp.headers.set(RESPONSE_HEADER_CDP_KIND, CDP_KIND_REQUEST);
      } catch (headerErr) {
        logger.debug?.("nanorix: response.headers.set failed", {
          err: String(headerErr),
        });
      }
    }
    return resp;
  };
}

/**
 * Wrap a Next.js App Router route handler (`app/api/.../route.ts`).
 *
 * Mirror of `withNanorix` adapted to the route-handler call signature
 * `(req, ctx?) => Promise<NextResponse>`. The augmented request exposes
 * the underlying capsule via `req.nanorixCapsule`.
 *
 * @throws Error if `client`, `framework`, or `capsulefile` is missing.
 */
export function wrapApiRoute(
  handler: NextRouteHandler,
  opts: NanorixNextOptions,
): NextRouteHandler {
  validateOpts(opts, "wrapApiRoute");

  const bindAuditProof = opts.bindAuditProof ?? true;
  const excludePaths = opts.excludePaths ?? [];
  const maxLifetimeSeconds = opts.maxLifetimeSeconds ?? 300;
  const logger = opts.logger ?? NOOP_LOGGER;
  const bufferPath = opts.bufferPath ?? DEFAULT_BUFFER_PATH;

  return async function nanorixWrappedRoute(
    req: NextRequestWithNanorix,
    routeCtx?: NextRouteContext,
  ): Promise<NextResponseLike> {
    const path = extractPath(req);
    if (isPathExcluded(path, excludePaths)) {
      return await handler(req, routeCtx);
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
    let capsule: NanorixCapsuleLike | null = null;
    try {
      capsule = await opts.client.capsules.create({
        capsulefile: opts.capsulefile,
        maxLifetimeSeconds,
        egressProfile: opts.egressProfile,
        metadata: { ...metadata },
      });
    } catch (createErr) {
      logger.warn?.("nanorix: capsule create failed; route continues unwrapped", {
        err: String(createErr),
      });
    }

    const ctx: NanorixRouteContext = {
      capsule,
      capsuleId: capsule?.id ?? null,
      framework,
      startTsMs,
      metadata,
    };
    req.nanorixCapsule = ctx;

    let response: NextResponseLike | undefined;
    let thrown: unknown = null;
    try {
      response = await handler(req, routeCtx);
    } catch (err) {
      thrown = err;
    }
    const durationMs = Math.max(0, Date.now() - startTsMs);
    const status = thrown !== null ? 500 : (response?.status ?? 0);

    await emitRequestOutcome({
      method: req.method,
      path,
      status,
      durationMs,
      err: thrown,
      agentId: opts.agentId,
      bufferPath,
    });

    if (capsule !== null) {
      try {
        const cdp = await capsule.destroy();
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

    if (thrown !== null) {
      throw thrown;
    }
    const resp = response as NextResponseLike;
    if (bindAuditProof && capsule !== null) {
      try {
        resp.headers.set(RESPONSE_HEADER_CAPSULE_ID, capsule.id);
        resp.headers.set(
          RESPONSE_HEADER_AUDIT_PROOF_ID,
          "cap_" + capsule.id.replace(/-/g, ""),
        );
        resp.headers.set(RESPONSE_HEADER_CDP_KIND, CDP_KIND_REQUEST);
      } catch (headerErr) {
        logger.debug?.("nanorix: response.headers.set failed", {
          err: String(headerErr),
        });
      }
    }
    return resp;
  };
}

// ── Internals ───────────────────────────────────────────────────────

function validateOpts(opts: NanorixNextOptions, caller: string): void {
  if (!opts.client) {
    throw new Error(
      `${caller} requires an explicit \`client\` option — a NanorixClient instance.`,
    );
  }
  if (!opts.framework || typeof opts.framework !== "string") {
    throw new Error(
      `${caller} requires an explicit \`framework\` option (e.g., framework: 'HIPAA'). ` +
        "No silent default customer-sole-declarer discipline.",
    );
  }
  if (!opts.capsulefile || typeof opts.capsulefile !== "string") {
    throw new Error(
      `${caller} requires an explicit \`capsulefile\` option.`,
    );
  }
}

function extractPath(req: NextRequestLike): string {
  if (req.nextUrl?.pathname !== undefined) return req.nextUrl.pathname;
  try {
    return new URL(req.url).pathname;
  } catch {
    return req.url || "/";
  }
}

function isPathExcluded(path: string, excludePaths: readonly string[]): boolean {
  for (const prefix of excludePaths) {
    if (path.startsWith(prefix)) return true;
  }
  return false;
}

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
      // non-fatal
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
