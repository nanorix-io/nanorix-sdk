import { describe, it, expect } from "vitest";
import { CcpaContext } from "../src/compliance/index.js";

describe("CcpaContext", () => {
  it("emits required reserved keys + structural state flag", () => {
    const ctx = new CcpaContext({
      businessOrganization: "Acme Corp",
      businessRole: "business",
      thresholdBasis: "annual_revenue_25m",
      consumerDataInScope: true,
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("CCPA");
    expect(meta["nanorix:framework_version"]).toBe("2020-operative");
    expect(meta.business_organization).toBe("Acme Corp");
    expect(meta.business_role).toBe("business");
    expect(meta.threshold_basis).toBe("annual_revenue_25m");
    expect(meta.consumer_data_in_scope).toBe("true");
    expect(meta.jurisdiction_state).toBe("CA");
  });

  it("emits pi_categories comma-joined when provided", () => {
    const ctx = new CcpaContext({
      businessOrganization: "Acme",
      businessRole: "service_provider",
      thresholdBasis: "consumer_records_100k",
      consumerDataInScope: true,
      piCategories: ["identifiers", "geolocation_data", "inferences"],
    });
    const meta = ctx.toMetadata();
    expect(meta.pi_categories).toBe(
      "identifiers,geolocation_data,inferences",
    );
  });

  it("rejects invalid businessRole", () => {
    expect(
      () =>
        new CcpaContext({
          businessOrganization: "X",
          // @ts-expect-error — intentional bad enum
          businessRole: "consumer",
          thresholdBasis: "annual_revenue_25m",
          consumerDataInScope: true,
        }),
    ).toThrowError(/CCPA §1798\.140 actor roles/);
  });

  it("rejects invalid threshold_basis", () => {
    expect(
      () =>
        new CcpaContext({
          businessOrganization: "X",
          businessRole: "business",
          // @ts-expect-error — intentional bad enum
          thresholdBasis: "small_business_exemption",
          consumerDataInScope: false,
        }),
    ).toThrowError(/CCPA §1798\.140\(d\) threshold bases/);
  });

  it("rejects non-bool consumerDataInScope", () => {
    expect(
      () =>
        new CcpaContext({
          businessOrganization: "X",
          businessRole: "business",
          thresholdBasis: "not_subject",
          // @ts-expect-error — intentional bad type
          consumerDataInScope: 1,
        }),
    ).toThrowError(/consumerDataInScope must be a bool/);
  });

  it("rejects invalid pi_category item", () => {
    expect(
      () =>
        new CcpaContext({
          businessOrganization: "X",
          businessRole: "business",
          thresholdBasis: "annual_revenue_25m",
          consumerDataInScope: true,
          // @ts-expect-error — intentional bad enum
          piCategories: ["identifiers", "bogus_category"],
        }),
    ).toThrowError(/canonical CCPA §1798\.140\(v\)\(1\) categories/);
  });
});
