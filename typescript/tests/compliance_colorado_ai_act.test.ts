import { describe, it, expect } from "vitest";
import { ColoradoAiActContext } from "../src/compliance/index.js";

describe("ColoradoAiActContext", () => {
  it("emits required reserved keys + 3 load-bearing booleans + state flag", () => {
    const ctx = new ColoradoAiActContext({
      organization: "Clinical AI Co.",
      actorRole: "deployer",
      highRiskAiSystem: true,
      impactAssessmentCompleted: true,
      consumerNoticeProvided: true,
      consequentialDecisionCategory: "healthcare",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("CO_SB24_205");
    expect(meta["nanorix:framework_version"]).toBe("2026-02");
    expect(meta.organization).toBe("Clinical AI Co.");
    expect(meta.actor_role).toBe("deployer");
    expect(meta.high_risk_ai_system).toBe("true");
    expect(meta.impact_assessment_completed).toBe("true");
    expect(meta.consumer_notice_provided).toBe("true");
    expect(meta.consequential_decision_category).toBe("healthcare");
    expect(meta.jurisdiction_state).toBe("CO");
  });

  it("rejects invalid actorRole", () => {
    expect(
      () =>
        new ColoradoAiActContext({
          organization: "X",
          // @ts-expect-error — intentional bad enum
          actorRole: "supplier",
          highRiskAiSystem: true,
          impactAssessmentCompleted: true,
          consumerNoticeProvided: true,
          consequentialDecisionCategory: "healthcare",
        }),
    ).toThrowError(/Colorado AI Act-aligned actor roles/);
  });

  it("rejects invalid consequentialDecisionCategory", () => {
    expect(
      () =>
        new ColoradoAiActContext({
          organization: "X",
          actorRole: "developer",
          highRiskAiSystem: true,
          impactAssessmentCompleted: true,
          consumerNoticeProvided: true,
          // @ts-expect-error — intentional bad enum
          consequentialDecisionCategory: "entertainment",
        }),
    ).toThrowError(/Consequential Decision taxonomy/);
  });

  it("rejects non-boolean impactAssessmentCompleted", () => {
    expect(
      () =>
        new ColoradoAiActContext({
          organization: "X",
          actorRole: "developer",
          highRiskAiSystem: true,
          // @ts-expect-error — intentional bad type
          impactAssessmentCompleted: "yes",
          consumerNoticeProvided: true,
          consequentialDecisionCategory: "healthcare",
        }),
    ).toThrowError(/impactAssessmentCompleted must be a bool/);
  });

  it("validates consumerNoticeTiming enum when provided", () => {
    expect(
      () =>
        new ColoradoAiActContext({
          organization: "X",
          actorRole: "developer",
          highRiskAiSystem: true,
          impactAssessmentCompleted: true,
          consumerNoticeProvided: true,
          consequentialDecisionCategory: "healthcare",
          // @ts-expect-error — intentional bad enum
          consumerNoticeTiming: "telepathic",
        }),
    ).toThrowError(/consumer-notice-timing taxonomy/);
  });

  it("emits common optional fields when set (envelope-budget safe)", () => {
    // NOTE: Colorado AI Act has 9 structural keys; we only set as many
    // optional fields as fit the 16-key envelope cap. Real customers
    // rarely set every reference field on a single capsule.
    const ctx = new ColoradoAiActContext({
      organization: "Acme",
      actorRole: "developer_and_deployer",
      highRiskAiSystem: false,
      impactAssessmentCompleted: false,
      consumerNoticeProvided: false,
      consequentialDecisionCategory: "not_consequential_decision",
      consumerNoticeTiming: "not_required",
      affectedConsumerCategories: ["colorado_residents"],
      impactAssessmentReference: "ia-2026-Q1",
      riskManagementPolicyReference: "rmp-v2",
      developerDisclosureReference: "dev-disc",
      appealWorkflowReference: "appeal-wf",
      workforceMemberRole: "loan_officer",
    });
    const meta = ctx.toMetadata();
    expect(meta.high_risk_ai_system).toBe("false");
    expect(meta.consumer_notice_timing).toBe("not_required");
    expect(meta.affected_consumer_categories).toBe("colorado_residents");
    expect(meta.impact_assessment_reference).toBe("ia-2026-Q1");
    expect(meta.risk_management_policy_reference).toBe("rmp-v2");
    expect(meta.developer_disclosure_reference).toBe("dev-disc");
    expect(meta.appeal_workflow_reference).toBe("appeal-wf");
    expect(meta.workforce_member_role).toBe("loan_officer");
    expect(Object.keys(meta).length).toBeLessThanOrEqual(16);
  });

  it("throws when too many optional fields exceed the 16-key envelope cap", () => {
    // ADR-014 + ADR-010 A4 envelope cap is 16 keys. Setting every
    // optional reference together would produce 17 keys — fail-fast at
    // toMetadata().
    const ctx = new ColoradoAiActContext({
      organization: "Acme",
      actorRole: "developer_and_deployer",
      highRiskAiSystem: false,
      impactAssessmentCompleted: false,
      consumerNoticeProvided: false,
      consequentialDecisionCategory: "not_consequential_decision",
      consumerNoticeTiming: "not_required",
      affectedConsumerCategories: ["colorado_residents"],
      impactAssessmentReference: "ia-2026-Q1",
      riskManagementPolicyReference: "rmp-v2",
      developerDisclosureReference: "dev-disc",
      adverseDecisionExplanationReference: "ade-wf",
      appealWorkflowReference: "appeal-wf",
      workforceMemberRole: "loan_officer",
    });
    expect(() => ctx.toMetadata()).toThrowError(/17 keys, max 16/);
  });
});
