import { describe, it, expect } from "vitest";
import { AIFairnessContext } from "../src/healthcare/index.js";

describe("AIFairnessContext", () => {
  it("emits required reserved keys + canonical fields", () => {
    const ctx = new AIFairnessContext({
      evaluationMethodology: "error_rate_parity",
      demographicGroupsEvaluated: ["race", "sex"],
      sampleSize: 7166,
      evaluationCompleted: true,
      toleranceBandPp: 5,
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("AI_Fairness_Evaluation");
    expect(meta["nanorix:framework_version"]).toBe("2026-02");
    expect(meta.evaluation_methodology).toBe("error_rate_parity");
    expect(meta.demographic_groups_evaluated).toBe("race,sex");
    expect(meta.sample_size).toBe("7166");
    expect(meta.evaluation_completed).toBe("true");
    expect(meta.tolerance_band_pp).toBe("5");
  });

  it("rejects invalid methodology", () => {
    expect(
      () =>
        new AIFairnessContext({
          // @ts-expect-error — intentional bad enum
          evaluationMethodology: "fairness_via_handwave",
          demographicGroupsEvaluated: ["race"],
          sampleSize: 100,
          evaluationCompleted: true,
        }),
    ).toThrowError(/fairness-evaluation methodology taxonomy/);
  });

  it("rejects empty demographicGroupsEvaluated", () => {
    expect(
      () =>
        new AIFairnessContext({
          evaluationMethodology: "demographic_parity",
          demographicGroupsEvaluated: [],
          sampleSize: 100,
          evaluationCompleted: false,
        }),
    ).toThrowError(/demographicGroupsEvaluated is required/);
  });

  it("rejects non-positive sampleSize", () => {
    expect(
      () =>
        new AIFairnessContext({
          evaluationMethodology: "outcome_parity",
          demographicGroupsEvaluated: ["age"],
          sampleSize: 0,
          evaluationCompleted: false,
        }),
    ).toThrowError(/must be a positive integer/);
  });

  it("rejects toleranceBandPp outside [0, 100]", () => {
    expect(
      () =>
        new AIFairnessContext({
          evaluationMethodology: "equalized_odds",
          demographicGroupsEvaluated: ["sex"],
          sampleSize: 500,
          evaluationCompleted: true,
          toleranceBandPp: 150,
        }),
    ).toThrowError(/must be in \[0\.0, 100\.0\]/);
  });

  it("rejects invalid ISO-8601 evaluationDate", () => {
    expect(
      () =>
        new AIFairnessContext({
          evaluationMethodology: "calibration_parity",
          demographicGroupsEvaluated: ["race"],
          sampleSize: 200,
          evaluationCompleted: true,
          evaluationDate: "May 1 2026",
        }),
    ).toThrowError(/must match ISO-8601 format/);
  });

  it("emits optional fields when set + usesToleranceBand property", () => {
    const ctx = new AIFairnessContext({
      evaluationMethodology: "equalized_odds",
      demographicGroupsEvaluated: ["race", "ethnicity"],
      sampleSize: 1500,
      evaluationCompleted: true,
      toleranceBandPp: 2.5,
      documentationUrl: "https://example.com/eval-report",
      subgroupPerformanceDisclosed: true,
      methodologyPreRegistered: false,
      evaluationDate: "2026-05-28",
      reEvaluationCadence: "annually",
      evaluationOwnerRole: "ai_ethics_lead",
      affectedDecisionCategories: ["clinical_decision_support"],
    });
    expect(ctx.usesToleranceBand).toBe(true);
    const meta = ctx.toMetadata();
    expect(meta.documentation_url).toBe("https://example.com/eval-report");
    expect(meta.subgroup_performance_disclosed).toBe("true");
    expect(meta.methodology_pre_registered).toBe("false");
    expect(meta.evaluation_date).toBe("2026-05-28");
    expect(meta.re_evaluation_cadence).toBe("annually");
    expect(meta.evaluation_owner_role).toBe("ai_ethics_lead");
    expect(meta.affected_decision_categories).toBe("clinical_decision_support");
    expect(meta.tolerance_band_pp).toBe("2.5");
  });
});
