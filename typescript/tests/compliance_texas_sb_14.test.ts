import { describe, it, expect } from "vitest";
import { TexasSb14Context } from "../src/compliance/index.js";

describe("TexasSb14Context", () => {
  it("emits required reserved keys + jurisdiction flag", () => {
    const ctx = new TexasSb14Context({
      deployerOrganization: "Texas Clinical AI",
      deployerRole: "deployer",
      highRiskUseCase: "healthcare_clinical_decision_support",
      impactAssessmentCompleted: true,
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("TX_SB_14");
    expect(meta["nanorix:framework_version"]).toBe("2025-enacted");
    expect(meta.deployer_organization).toBe("Texas Clinical AI");
    expect(meta.deployer_role).toBe("deployer");
    expect(meta.high_risk_use_case).toBe(
      "healthcare_clinical_decision_support",
    );
    expect(meta.impact_assessment_completed).toBe("true");
    expect(meta.jurisdiction_state).toBe("TX");
  });

  it("rejects invalid deployerRole", () => {
    expect(
      () =>
        new TexasSb14Context({
          deployerOrganization: "X",
          // @ts-expect-error — intentional bad enum
          deployerRole: "operator",
          highRiskUseCase: "not_high_risk",
          impactAssessmentCompleted: false,
        }),
    ).toThrowError(/TRAIGA-aligned actor roles/);
  });

  it("rejects invalid highRiskUseCase", () => {
    expect(
      () =>
        new TexasSb14Context({
          deployerOrganization: "X",
          deployerRole: "developer",
          // @ts-expect-error — intentional bad enum
          highRiskUseCase: "weather_forecasting",
          impactAssessmentCompleted: false,
        }),
    ).toThrowError(/consequential-decision taxonomy/);
  });

  it("rejects non-boolean impactAssessmentCompleted", () => {
    expect(
      () =>
        new TexasSb14Context({
          deployerOrganization: "X",
          deployerRole: "deployer",
          highRiskUseCase: "employment_decision",
          // @ts-expect-error — intentional bad type
          impactAssessmentCompleted: 0,
        }),
    ).toThrowError(/impactAssessmentCompleted must be a bool/);
  });

  it("emits affected_categories comma-joined", () => {
    const ctx = new TexasSb14Context({
      deployerOrganization: "Acme",
      deployerRole: "deployer",
      highRiskUseCase: "healthcare_treatment_recommendation",
      impactAssessmentCompleted: true,
      affectedCategories: ["texas_residents", "texas_medicaid_beneficiaries"],
    });
    const meta = ctx.toMetadata();
    expect(meta.affected_categories).toBe(
      "texas_residents,texas_medicaid_beneficiaries",
    );
  });

  it("emits all optional fields when set", () => {
    const ctx = new TexasSb14Context({
      deployerOrganization: "Acme",
      deployerRole: "distributor",
      highRiskUseCase: "other_high_risk",
      impactAssessmentCompleted: false,
      impactAssessmentReference: "ia-ref-v1",
      noticeToConsumersReference: "notice-2026",
      biasAuditReference: "bias-audit-2026",
      workforceMemberRole: "claims_adjudicator",
    });
    const meta = ctx.toMetadata();
    expect(meta.impact_assessment_reference).toBe("ia-ref-v1");
    expect(meta.notice_to_consumers_reference).toBe("notice-2026");
    expect(meta.bias_audit_reference).toBe("bias-audit-2026");
    expect(meta.workforce_member_role).toBe("claims_adjudicator");
  });
});
