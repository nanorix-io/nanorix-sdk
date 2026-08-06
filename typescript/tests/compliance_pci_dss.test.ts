import { describe, it, expect } from "vitest";
import { PciDssContext } from "../src/compliance/index.js";

describe("PciDssContext", () => {
  it("emits required reserved keys + CDE scope flag", () => {
    const ctx = new PciDssContext({
      merchantOrServiceProvider: "Acme PBM",
      merchantLevel: "level_1",
      assessmentType: "roc",
      cardholderDataInScope: true,
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("PCI_DSS");
    expect(meta["nanorix:framework_version"]).toBe("4.0");
    expect(meta.merchant_or_service_provider).toBe("Acme PBM");
    expect(meta.merchant_level).toBe("level_1");
    expect(meta.assessment_type).toBe("roc");
    expect(meta.cardholder_data_in_scope).toBe("true");
  });

  it("rejects invalid merchantLevel", () => {
    expect(
      () =>
        new PciDssContext({
          merchantOrServiceProvider: "X",
          // @ts-expect-error — intentional bad enum
          merchantLevel: "level_5",
          assessmentType: "saq_a",
          cardholderDataInScope: false,
        }),
    ).toThrowError(/PCI SSC merchant \+ service-provider levels/);
  });

  it("rejects invalid assessmentType", () => {
    expect(
      () =>
        new PciDssContext({
          merchantOrServiceProvider: "X",
          merchantLevel: "level_2",
          // @ts-expect-error — intentional bad enum
          assessmentType: "saq_z",
          cardholderDataInScope: false,
        }),
    ).toThrowError(/PCI DSS v4\.0 SAQ \+ ROC types/);
  });

  it("rejects non-bool cardholderDataInScope", () => {
    expect(
      () =>
        new PciDssContext({
          merchantOrServiceProvider: "X",
          merchantLevel: "level_1",
          assessmentType: "roc",
          // @ts-expect-error — intentional bad type
          cardholderDataInScope: "true",
        }),
    ).toThrowError(/cardholderDataInScope must be a bool/);
  });

  it("rejects unknown requirement", () => {
    expect(
      () =>
        new PciDssContext({
          merchantOrServiceProvider: "X",
          merchantLevel: "level_1",
          assessmentType: "roc",
          cardholderDataInScope: true,
          inScopeRequirements: ["13_quantum_resistance"],
        }),
    ).toThrowError(/12 canonical PCI DSS v4\.0 requirements/);
  });

  it("requirements property returns 12 canonical requirements", () => {
    const ctx = new PciDssContext({
      merchantOrServiceProvider: "X",
      merchantLevel: "service_provider_level_1",
      assessmentType: "roc",
      cardholderDataInScope: true,
    });
    expect(ctx.requirements).toHaveLength(12);
  });

  it("emits all optional fields when set", () => {
    const ctx = new PciDssContext({
      merchantOrServiceProvider: "Acme RCM",
      merchantLevel: "service_provider_level_2",
      assessmentType: "saq_d_service_provider",
      cardholderDataInScope: true,
      inScopeRequirements: [
        "07_restrict_access_by_business_need_to_know",
        "08_identify_users_authenticate_access",
      ],
      qsaFirm: "Coalfire",
      assessmentPeriod: "2026-Q2",
      aocReference: "aoc-2026",
      cdeSegmentationMethod: "cde_capsules_only",
      workforceMemberRole: "claims_router",
    });
    const meta = ctx.toMetadata();
    expect(meta.in_scope_requirements).toBe(
      "07_restrict_access_by_business_need_to_know,08_identify_users_authenticate_access",
    );
    expect(meta.qsa_firm).toBe("Coalfire");
    expect(meta.assessment_period).toBe("2026-Q2");
    expect(meta.aoc_reference).toBe("aoc-2026");
    expect(meta.cde_segmentation_method).toBe("cde_capsules_only");
    expect(meta.workforce_member_role).toBe("claims_router");
  });
});
