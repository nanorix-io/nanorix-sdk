import { describe, it, expect } from "vitest";
import { NistAiRmfContext } from "../src/compliance/index.js";

describe("NistAiRmfContext", () => {
  it("emits required reserved keys + core functions", () => {
    const ctx = new NistAiRmfContext({
      aiSystemOwner: "Acme AI",
      useCaseCategory: "clinical-decision-support",
      coreFunctionsInScope: ["govern", "measure"],
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("NIST_AI_RMF_1.0");
    expect(meta["nanorix:framework_version"]).toBe("1.0");
    expect(meta.ai_system_owner).toBe("Acme AI");
    expect(meta.use_case_category).toBe("clinical-decision-support");
    expect(meta.core_functions_in_scope).toBe("govern,measure");
  });

  it("rejects empty coreFunctionsInScope", () => {
    expect(
      () =>
        new NistAiRmfContext({
          aiSystemOwner: "Acme",
          useCaseCategory: "x",
          coreFunctionsInScope: [],
        }),
    ).toThrowError(/coreFunctionsInScope is required and must be non-empty/);
  });

  it("rejects invalid core function", () => {
    expect(
      () =>
        new NistAiRmfContext({
          aiSystemOwner: "Acme",
          useCaseCategory: "x",
          // @ts-expect-error — intentional bad enum
          coreFunctionsInScope: ["govern", "ignore"],
        }),
    ).toThrowError(/not in/);
  });

  it("rejects invalid risk category", () => {
    expect(
      () =>
        new NistAiRmfContext({
          aiSystemOwner: "Acme",
          useCaseCategory: "x",
          coreFunctionsInScope: ["govern"],
          // @ts-expect-error — intentional bad enum
          riskCategories: ["safety", "personality"],
        }),
    ).toThrowError(/seven trustworthy AI characteristics/);
  });

  it("rejects invalid lifecycle_stage", () => {
    expect(
      () =>
        new NistAiRmfContext({
          aiSystemOwner: "Acme",
          useCaseCategory: "x",
          coreFunctionsInScope: ["map"],
          // @ts-expect-error — intentional bad enum
          lifecycleStage: "retired",
        }),
    ).toThrowError(/not in/);
  });

  it("emits all optional fields when set", () => {
    const ctx = new NistAiRmfContext({
      aiSystemOwner: "Acme",
      useCaseCategory: "medical-imaging",
      coreFunctionsInScope: ["govern", "map", "measure", "manage"],
      riskCategories: ["safety", "fairness_bias", "privacy"],
      lifecycleStage: "deploy_use",
      riskAssessmentReference: "ra-2026",
      modelCardReference: "mc-v2",
      subgroupEvaluationReference: "subgroup-eval",
    });
    const meta = ctx.toMetadata();
    expect(meta.risk_categories).toBe("safety,fairness_bias,privacy");
    expect(meta.lifecycle_stage).toBe("deploy_use");
    expect(meta.risk_assessment_reference).toBe("ra-2026");
    expect(meta.model_card_reference).toBe("mc-v2");
    expect(meta.subgroup_evaluation_reference).toBe("subgroup-eval");
  });
});
