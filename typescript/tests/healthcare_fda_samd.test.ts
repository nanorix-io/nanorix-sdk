import { describe, it, expect } from "vitest";
import { FDASaMDContext } from "../src/healthcare/index.js";

describe("FDASaMDContext", () => {
  it("emits required reserved keys + canonical fields", () => {
    const ctx = new FDASaMDContext({
      deviceClass: "II",
      submissionPathway: "510k",
      submissionStatus: "cleared",
      intendedUse: "Voice biomarker diagnostic for depression screening",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("FDA_SaMD");
    expect(meta["nanorix:framework_version"]).toBe("2026-02");
    expect(meta.device_class).toBe("II");
    expect(meta.submission_pathway).toBe("510k");
    expect(meta.submission_status).toBe("cleared");
    expect(meta.intended_use).toBe(
      "Voice biomarker diagnostic for depression screening",
    );
  });

  it("rejects invalid device class", () => {
    expect(
      () =>
        new FDASaMDContext({
          // @ts-expect-error — intentional bad enum
          deviceClass: "IV",
          submissionPathway: "510k",
          submissionStatus: "submitted",
          intendedUse: "x",
        }),
    ).toThrowError(/21 CFR Part 860 device-class taxonomy/);
  });

  it("rejects invalid submission pathway", () => {
    expect(
      () =>
        new FDASaMDContext({
          deviceClass: "II",
          // @ts-expect-error — intentional bad enum
          submissionPathway: "ema_premarket",
          submissionStatus: "submitted",
          intendedUse: "x",
        }),
    ).toThrowError(/FDA pre-market submission pathway taxonomy/);
  });

  it("rejects malformed K-number", () => {
    expect(
      () =>
        new FDASaMDContext({
          deviceClass: "II",
          submissionPathway: "510k",
          submissionStatus: "cleared",
          intendedUse: "x",
          kNumber: "K23",
        }),
    ).toThrowError(/must match format 'K\\d\{6\}'/);
  });

  it("rejects malformed DEN-number", () => {
    expect(
      () =>
        new FDASaMDContext({
          deviceClass: "II",
          submissionPathway: "de_novo",
          submissionStatus: "cleared",
          intendedUse: "x",
          deNovoNumber: "DEN-99",
        }),
    ).toThrowError(/must match format 'DEN\\d\{6\}'/);
  });

  it("accepts valid K-number + emits in metadata", () => {
    const ctx = new FDASaMDContext({
      deviceClass: "II",
      submissionPathway: "510k",
      submissionStatus: "cleared",
      intendedUse: "Dental caries detection AI for adult radiographs",
      kNumber: "K231234",
      subgroupPerformanceDisclosed: true,
    });
    const meta = ctx.toMetadata();
    expect(meta.k_number).toBe("K231234");
    expect(meta.subgroup_performance_disclosed).toBe("true");
    expect(ctx.isClearedOrApproved).toBe(true);
    expect(ctx.isHighRiskClass).toBe(false);
  });

  it("isHighRiskClass true only for Class III", () => {
    const class3 = new FDASaMDContext({
      deviceClass: "III",
      submissionPathway: "pma",
      submissionStatus: "approved",
      intendedUse: "Life-supporting cardiac AI",
    });
    expect(class3.isHighRiskClass).toBe(true);
    expect(class3.isClearedOrApproved).toBe(true);
  });
});
