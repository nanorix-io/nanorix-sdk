/**
 * Billing meter readout helpers — Batch 3 sub-item 1 (CR-002, 2026-05-27).
 *
 * Programmatic surface mirroring Stripe metered billing data via the
 * Nanorix API. Customers — particularly mid-pilot pharma + Mayo + agent
 * customers — consistently ask: "show me spend by capsule shape per
 * tenant per month." Stripe metered billing carries the line items; this
 * module exposes the same data via the Nanorix API for programmatic
 * consumption (dashboards, cost-forecast scripts, CFO reconciliation
 * pipelines).
 *
 * ## Discipline (CCO policy)
 *
 * Usage counts + cost forecasts are STRUCTURAL FACTS, never compliance
 * adjudication. The auditor remains the certifying authority. Forbidden
 * vocabulary anywhere in this module's output: COMPLIANT / SATISFIED /
 * PASSED / MEETS. Output reads "counted N receipts with tag X", never
 * "X satisfies Y".
 *
 * ## Usage
 *
 * ```ts
 * import { NanorixClient } from "@nanorix/sdk";
 *
 * const client = new NanorixClient({ apiKey: "nrx_live_..." });
 *
 * // Full breakdown
 * const usage = await client.billing.getUsageMeter();
 * console.log(`Total AuditProofs: ${usage.auditproof_count}`);
 *
 * // Shape-only slice
 * const byShape = await client.billing.getUsageByShape();
 *
 * // Cost forecast
 * const forecast = await client.billing.getCostForecast();
 * console.log(`Current spend: $${forecast.current_cycle_spend_cents / 100}`);
 * ```
 */
import type { HttpTransport } from "./http.js";

/**
 * One row in the by-pattern breakdown.
 *
 * `pattern_tag` mirrors the closed-enum wire form (snake_case).
 * The sentinel `untagged` covers receipts without a `pattern_tag` field
 * (pre-Wave-N proofs or receipts where the customer omitted the tag).
 */
export interface PatternUsage {
  pattern_tag: string;
  auditproof_count: number;
}

/**
 * One row in the by-shape breakdown.
 *
 * Shape bucket: `small` (<1GB) / `medium` (1-4GB) / `large` (>4GB) /
 * `unknown` (capsules with no linked Capsulefile). Mirrors the
 * cold-start-benchmark taxonomy.
 */
export interface ShapeUsage {
  shape: string;
  capsule_count: number;
  auditproof_count: number;
}

/** One row in the by-capsulefile breakdown (top 50 by capsule_count). */
export interface CapsulefileUsage {
  capsulefile_id: string;
  capsule_count: number;
  auditproof_count: number;
}

/**
 * Top-level billing-meter usage breakdown.
 *
 * Returned by `Billing.getUsageMeter()`. All counts are STRUCTURAL FACTS;
 * production-authority filter applied (test-authority capsules
 * NON-billable per D2 LOCKED). `disclaimer` carries the canonical
 * "auditor adjudicates" surface.
 */
export interface BillingMeterUsage {
  from: string;
  to: string;
  tier: string;
  capsule_count: number;
  auditproof_count: number;
  by_pattern: PatternUsage[];
  by_shape: ShapeUsage[];
  by_capsulefile: CapsulefileUsage[];
  disclaimer: string;
}

/**
 * Per-tenant cost forecast for the current billing cycle.
 *
 * `current_cycle_spend_cents = cdp_count × tier_rate_cents`.
 * `forecasted_cycle_spend_cents = (cdp_count / elapsed_days) × cycle_days`.
 *
 * For Free + Enterprise tiers, `cdp_rate_cents` is omitted (field
 * absent in JSON per `skip_serializing_if`) and both spend fields are 0.
 */
export interface CostForecast {
  billing_cycle_start: string;
  billing_cycle_end: string;
  tier: string;
  current_cycle_auditproof_count: number;
  cdp_rate_cents?: number;
  current_cycle_spend_cents: number;
  forecasted_cycle_spend_cents: number;
  disclaimer: string;
}

/** Optional date-range params (ISO-8601 `YYYY-MM-DD`). */
export interface MeterDateRange {
  from?: string;
  to?: string;
}

function buildParams(opts: MeterDateRange): Record<string, string> {
  const params: Record<string, string> = {};
  if (opts.from) params.from = opts.from;
  if (opts.to) params.to = opts.to;
  return params;
}

/**
 * Billing meter operations.
 *
 * Wires four endpoints (Batch 3 sub-item 1):
 * - `GET /v1/billing/meter/usage`
 * - `GET /v1/billing/meter/usage/by-shape`
 * - `GET /v1/billing/meter/usage/by-pattern`
 * - `GET /v1/billing/meter/cost-forecast`
 */
export class Billing {
  constructor(private readonly http: HttpTransport) {}

  /**
   * Full billing meter breakdown over a window.
   *
   * @param opts Date range. When omitted, defaults to the customer's
   *   current billing cycle.
   *
   * Discipline: counts are STRUCTURAL FACTS; never asserts
   * compliance satisfaction.
   */
  async getUsageMeter(opts: MeterDateRange = {}): Promise<BillingMeterUsage> {
    const params = buildParams(opts);
    return (await this.http.request("GET", "/v1/billing/meter/usage", {
      params,
    })) as unknown as BillingMeterUsage;
  }

  /**
   * Thin slice — by-shape breakdown only.
   *
   * Shape buckets mirror the cold-start-benchmark taxonomy:
   * `small` (<1GB) / `medium` (1-4GB) / `large` (>4GB) / `unknown`.
   */
  async getUsageByShape(opts: MeterDateRange = {}): Promise<ShapeUsage[]> {
    const params = buildParams(opts);
    return (await this.http.request("GET", "/v1/billing/meter/usage/by-shape", {
      params,
    })) as unknown as ShapeUsage[];
  }

  /**
   * Thin slice — by-pattern_tag breakdown only.
   *
   * Returns the closed-enum pattern_tag rows + the
   * `untagged` sentinel for receipts without a tag.
   */
  async getUsageByPattern(opts: MeterDateRange = {}): Promise<PatternUsage[]> {
    const params = buildParams(opts);
    return (await this.http.request(
      "GET",
      "/v1/billing/meter/usage/by-pattern",
      { params },
    )) as unknown as PatternUsage[];
  }

  /**
   * Current-cycle cost forecast at the customer's tier rate.
   *
   * Forecast = `(cdp_count / elapsed_cycle_days) × cycle_days × rate`.
   * Reconciles to `usage.estimate()` per-tier rate table; the
   * rate-table-reconciliation invariant is pinned by the Rust-side
   * test `cost_forecast_rate_table_reconciles_to_estimate_table`.
   */
  async getCostForecast(): Promise<CostForecast> {
    return (await this.http.request(
      "GET",
      "/v1/billing/meter/cost-forecast",
    )) as unknown as CostForecast;
  }
}
