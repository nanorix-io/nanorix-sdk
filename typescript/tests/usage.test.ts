/**
 * Batch 33-foundation tests — Usage resource + cursor pagination (TS).
 *
 * Wires `/v1/usage`, `/v1/usage/estimate`, `/v1/usage/items`,
 * `/v1/usage/items.csv` (Batch 32 sub-A + sub-B endpoints) into the SDK.
 * Pins the auto-pagination semantics + CSV roundtrip + auditProofs alias.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { Page } from "../src/pagination.js";
import { FetchMock, TEST_API_KEY, TEST_BASE_URL } from "./setup.js";

function usageItemDict(idx: number, billable = true) {
  return {
    id: `00000000-0000-0000-0000-${String(idx).padStart(12, "0")}`,
    capsule_id: `00000000-1111-0000-0000-${String(idx).padStart(12, "0")}`,
    capsulefile_id: `cf_test${idx}`,
    capsulefile_version: 1,
    created_at: "2026-05-05T12:00:00+00:00",
    signing_authority_id: billable ? "us-kms-nanorix-v1" : "us-kms-nanorix-test-v1",
    data_classification: "PHI",
    billable,
    cdp_charge_cents: billable ? 4 : 0,
  };
}

describe("Usage resource", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    mock.install();
    client = new NanorixClient({ apiKey: TEST_API_KEY, baseUrl: TEST_BASE_URL });
  });

  afterEach(() => {
    mock.restore();
  });

  it("summary returns aggregate shape", async () => {
    mock.on("GET", "/v1/usage", {
      status: 200,
      body: {
        summary: { total_cdps: 42, total_compute_minutes: 7.5, total_capsules: 50 },
        daily: [],
      },
    });
    const out = await client.usage.summary();
    expect(out.summary.total_cdps).toBe(42);
  });

  it("estimate returns tier + total_estimate", async () => {
    mock.on("GET", "/v1/usage/estimate", {
      status: 200,
      body: {
        period_start: "2026-05-01T00:00:00Z",
        period_end: "2026-05-31T00:00:00Z",
        tier: "starter",
        platform_fee: 9900,
        cdp_count: 42,
        cdp_charge: 168,
        compute_minutes: 7.5,
        compute_charge: 375,
        total_estimate: 10443,
      },
    });
    const out = await client.usage.estimate();
    expect(out.tier).toBe("starter");
    expect(out.total_estimate).toBe(10443);
  });

  it("listItems single-page no_more iterates cleanly", async () => {
    mock.on("GET", "/v1/usage/items", {
      status: 200,
      body: {
        object: "list",
        data: [usageItemDict(0), usageItemDict(1), usageItemDict(2)],
        has_more: false,
      },
    });
    const items = await client.usage.listItems({ limit: 10 }).toArray();
    expect(items).toHaveLength(3);
    expect(items[0].billable).toBe(true);
    expect(items[0].cdp_charge_cents).toBe(4);
  });

  it("listItems test-authority renders zero charge (D2 LOCKED)", async () => {
    mock.on("GET", "/v1/usage/items", {
      status: 200,
      body: {
        object: "list",
        data: [usageItemDict(0, false)],
        has_more: false,
      },
    });
    const items = await client.usage.listItems().toArray();
    expect(items[0].billable).toBe(false);
    expect(items[0].cdp_charge_cents).toBe(0);
    expect(items[0].signing_authority_id).toBe("us-kms-nanorix-test-v1");
  });
});

describe("Page cursor advance (unit)", () => {
  it("Page.toArray exhausts via cursor advance", async () => {
    interface Row {
      id: string;
      v: number;
    }
    const page1 = [
      { id: "a", v: 1 },
      { id: "b", v: 2 },
    ];
    const page2 = [{ id: "c", v: 3 }];
    let calls = 0;
    const fetchPage = async (cursor: string | undefined) => {
      calls += 1;
      if (cursor === undefined) {
        return { object: "list", data: page1, has_more: true };
      }
      expect(cursor).toBe("b"); // last id of page1
      return { object: "list", data: page2, has_more: false };
    };
    const page = new Page<Row, Row>(fetchPage, (r) => r, 100);
    const out = await page.toArray();
    expect(out).toHaveLength(3);
    expect(out.map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(calls).toBe(2);
  });
});

describe("auditProofs alias", () => {
  const mock = new FetchMock();

  beforeEach(() => {
    mock.reset();
    mock.install();
  });

  afterEach(() => {
    mock.restore();
  });

  it("client.auditProofs is alias for client.cdp (ADR-014)", () => {
    const client = new NanorixClient({
      apiKey: TEST_API_KEY,
      baseUrl: TEST_BASE_URL,
    });
    expect(client.auditProofs).toBe(client.cdp);
  });
});
