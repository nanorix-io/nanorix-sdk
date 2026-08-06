/**
 * Tests for the Billing meter readout SDK helpers (TS).
 *
 * Batch 3 sub-item 1 (CR-002, 2026-05-27). Mirrors the Python suite at
 * `sdk/python/tests/test_billing_meter.py` + the Rust handler suite at
 * `services/api/src/routes/billing_meter.rs`. Exercises the parsing
 * layer + URL construction against a mock fetch (offline; no live API
 * roundtrip).
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { FetchMock, TEST_API_KEY, TEST_BASE_URL } from "./setup.js";

function makeUsagePayload() {
  return {
    from: "2026-05-01T00:00:00Z",
    to: "2026-05-31T00:00:00Z",
    tier: "starter",
    capsule_count: 250,
    auditproof_count: 350,
    by_pattern: [
      { pattern_tag: "pa", auditproof_count: 100 },
      { pattern_tag: "rcm_claim", auditproof_count: 150 },
      { pattern_tag: "untagged", auditproof_count: 100 },
    ],
    by_shape: [
      { shape: "small", capsule_count: 200, auditproof_count: 280 },
      { shape: "medium", capsule_count: 40, auditproof_count: 60 },
      { shape: "large", capsule_count: 10, auditproof_count: 10 },
    ],
    by_capsulefile: [
      {
        capsulefile_id: "cf_abc123",
        capsule_count: 200,
        auditproof_count: 280,
      },
    ],
    disclaimer:
      "Usage counts are structural facts derived from emitted AuditProofs. " +
      "The auditor remains the certifying authority for any compliance program.",
  };
}

function makeForecastPayload(tier = "starter"): Record<string, unknown> {
  const base: Record<string, unknown> = {
    billing_cycle_start: "2026-05-01T00:00:00Z",
    billing_cycle_end: "2026-05-31T00:00:00Z",
    tier,
    current_cycle_auditproof_count: 500,
    current_cycle_spend_cents: 2000,
    forecasted_cycle_spend_cents: 6000,
    disclaimer: "Usage counts are structural facts...",
  };
  if (tier === "starter") base.cdp_rate_cents = 4;
  if (tier === "business") base.cdp_rate_cents = 3;
  return base;
}

describe("Billing meter readout", () => {
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

  it("getUsageMeter returns full breakdown", async () => {
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    const usage = await client.billing.getUsageMeter();
    expect(usage.capsule_count).toBe(250);
    expect(usage.auditproof_count).toBe(350);
    expect(usage.tier).toBe("starter");
    expect(usage.from).toBe("2026-05-01T00:00:00Z");
    expect(usage.to).toBe("2026-05-31T00:00:00Z");
  });

  it("getUsageMeter parses by_pattern rows including untagged sentinel", async () => {
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    const usage = await client.billing.getUsageMeter();
    expect(usage.by_pattern).toHaveLength(3);
    const untagged = usage.by_pattern.find((r) => r.pattern_tag === "untagged");
    expect(untagged?.auditproof_count).toBe(100);
  });

  it("getUsageMeter parses by_shape rows mirroring cold-start-benchmark taxonomy", async () => {
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    const usage = await client.billing.getUsageMeter();
    const shapes = new Set(usage.by_shape.map((r) => r.shape));
    expect(shapes.has("small")).toBe(true);
    expect(shapes.has("medium")).toBe(true);
    expect(shapes.has("large")).toBe(true);
  });

  it("getUsageMeter forwards date_range as query params", async () => {
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    await client.billing.getUsageMeter({
      from: "2026-04-01",
      to: "2026-04-30",
    });
    const lastCall = mock.calls.at(-1);
    expect(lastCall?.url).toContain("from=2026-04-01");
    expect(lastCall?.url).toContain("to=2026-04-30");
  });

  it("getUsageByShape returns only the by-shape slice", async () => {
    const payload = [
      { shape: "small", capsule_count: 100, auditproof_count: 150 },
      { shape: "large", capsule_count: 5, auditproof_count: 5 },
    ];
    mock.on("GET", "/v1/billing/meter/usage/by-shape", {
      status: 200,
      body: payload,
    });
    const rows = await client.billing.getUsageByShape();
    expect(rows).toHaveLength(2);
    expect(rows[0].shape).toBe("small");
  });

  it("getUsageByPattern returns only the by-pattern slice", async () => {
    const payload = [
      { pattern_tag: "pa", auditproof_count: 100 },
      { pattern_tag: "untagged", auditproof_count: 10 },
    ];
    mock.on("GET", "/v1/billing/meter/usage/by-pattern", {
      status: 200,
      body: payload,
    });
    const rows = await client.billing.getUsageByPattern();
    expect(rows).toHaveLength(2);
    expect(rows.some((r) => r.pattern_tag === "untagged")).toBe(true);
  });

  it("getCostForecast for Starter tier carries cdp_rate_cents=4", async () => {
    mock.on("GET", "/v1/billing/meter/cost-forecast", {
      status: 200,
      body: makeForecastPayload("starter"),
    });
    const forecast = await client.billing.getCostForecast();
    expect(forecast.tier).toBe("starter");
    expect(forecast.cdp_rate_cents).toBe(4);
    expect(forecast.current_cycle_spend_cents).toBe(2000);
  });

  it("getCostForecast for Free tier omits cdp_rate_cents", async () => {
    const payload: Record<string, unknown> = {
      billing_cycle_start: "2026-05-01T00:00:00Z",
      billing_cycle_end: "2026-05-31T00:00:00Z",
      tier: "free",
      current_cycle_auditproof_count: 50,
      current_cycle_spend_cents: 0,
      forecasted_cycle_spend_cents: 0,
      disclaimer: "...",
    };
    mock.on("GET", "/v1/billing/meter/cost-forecast", {
      status: 200,
      body: payload,
    });
    const forecast = await client.billing.getCostForecast();
    expect(forecast.tier).toBe("free");
    expect(forecast.cdp_rate_cents).toBeUndefined();
    expect(forecast.current_cycle_spend_cents).toBe(0);
  });

  it("disclaimer carries no forbidden vocabulary", async () => {
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    const usage = await client.billing.getUsageMeter();
    for (const fb of ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]) {
      expect(usage.disclaimer).not.toContain(fb);
      expect(usage.disclaimer.toLowerCase()).not.toContain(fb.toLowerCase());
    }
  });

  it("getUsageMeter without params hits the base endpoint with empty query string", async () => {
    // HttpTransport appends `?` for any params object (even empty). The
    // endpoint accepts a trailing `?`; this assertion pins that the path
    // is correct (not that the `?` is absent — that's a transport quirk).
    mock.on("GET", "/v1/billing/meter/usage", {
      status: 200,
      body: makeUsagePayload(),
    });
    await client.billing.getUsageMeter();
    const lastCall = mock.calls.at(-1);
    // Path is correct + no spurious params land in the query string.
    expect(lastCall?.url).toContain("/v1/billing/meter/usage");
    // Trailing `?` ok; no leaked from/to keys.
    const tail = lastCall?.url.split("?")[1] ?? "";
    expect(tail).toBe("");
  });
});
