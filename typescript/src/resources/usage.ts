/**
 * Usage resource — wires `/v1/usage`, `/v1/usage/estimate`,
 * `/v1/usage/items`, `/v1/usage/items.csv`.
 *
 * `/v1/usage/items` is the Stripe-pattern cursor-paginated per-CDP listing
 * shipped. SDK auto-pages via `Page<UsageItemRaw, UsageItem>`.
 * `/v1/usage/items.csv` is exposed via `.exportCsv()`
 * returning Buffer for direct save / pipeline injection.
 */
import type { HttpTransport } from "../http.js";
import { Page } from "../pagination.js";

/**
 * One CDP in the per-CDP usage listing.
 *
 * `billable` flag is server-derived from `signing_authority_id`
 * (production authority only); `cdp_charge_cents` is the per-CDP
 * USD-cent rate at the customer's tier (test-authority CDPs always 0).
 */
export interface UsageItem {
  id: string;
  capsule_id: string;
  capsulefile_id: string | null;
  capsulefile_version: number | null;
  created_at: string;
  signing_authority_id: string;
  data_classification: string | null;
  billable: boolean;
  cdp_charge_cents: number;
}

export interface UsageSummary {
  summary: {
    total_cdps: number;
    total_compute_minutes: number;
    total_capsules: number;
  };
  daily: Array<{
    date: string;
    cdps: number;
    compute_seconds: number;
    capsules_created: number;
  }>;
}

export interface UsageEstimate {
  period_start: string;
  period_end: string;
  tier: string;
  platform_fee: number;
  cdp_count: number;
  cdp_charge: number;
  compute_minutes: number;
  compute_charge: number;
  total_estimate: number;
}

export interface ListItemsOptions {
  limit?: number;
  startingAfter?: string;
}

export class Usage {
  constructor(private readonly http: HttpTransport) {}

  /** Aggregate usage summary + daily breakdown. */
  async summary(opts: { from?: string; to?: string } = {}): Promise<UsageSummary> {
    const params: Record<string, string> = {};
    if (opts.from) params.from = opts.from;
    if (opts.to) params.to = opts.to;
    return (await this.http.request("GET", "/v1/usage", {
      params,
    })) as unknown as UsageSummary;
  }

  /** Current billing-cycle cost estimate. */
  async estimate(): Promise<UsageEstimate> {
    return (await this.http.request(
      "GET",
      "/v1/usage/estimate",
    )) as unknown as UsageEstimate;
  }

  /**
   * Auto-paginated per-CDP listing.
   *
   * Iterate via `for await`:
   * ```ts
   * for await (const item of client.usage.listItems()) {
   *   if (item.billable) console.log(item.id, item.cdp_charge_cents);
   * }
   * ```
   *
   * Per-page roundtrip; transparently chains pages via
   * `starting_after = last.id`. Stops when server returns `has_more: false`.
   */
  listItems(opts: ListItemsOptions = {}): Page<UsageItem, UsageItem> {
    const limit = opts.limit ?? 100;
    const fetchPage = async (
      cursor: string | undefined,
      pageLimit: number,
    ) => {
      const params: Record<string, string> = { limit: String(pageLimit) };
      if (cursor) params.starting_after = cursor;
      const raw = (await this.http.request("GET", "/v1/usage/items", {
        params,
      })) as unknown as {
        object: string;
        data: UsageItem[];
        has_more: boolean;
      };
      return raw;
    };
    return new Page<UsageItem, UsageItem>(
      fetchPage,
      (raw) => raw,
      limit,
      opts.startingAfter,
    );
  }

  /**
   * Export per-CDP listing as CSV bytes.
   *
   * GET /v1/usage/items.csv. Returns one page (limit-bound). For full
   * historical export, iterate `listItems()` page-by-page or call
   * repeatedly with `startingAfter`.
   *
   * Columns: id, capsule_id, capsulefile_id, capsulefile_version,
   * created_at, signing_authority_id, data_classification, billable,
   * cdp_charge_cents.
   */
  async exportCsv(opts: ListItemsOptions = {}): Promise<Buffer> {
    const params: Record<string, string> = {
      limit: String(opts.limit ?? 100),
    };
    if (opts.startingAfter) params.starting_after = opts.startingAfter;
    return await this.http.requestRaw("GET", "/v1/usage/items.csv", {
      params,
    });
  }
}
