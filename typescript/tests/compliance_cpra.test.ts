import { describe, it, expect } from "vitest";
import { CpraContext } from "../src/compliance/index.js";

describe("CpraContext", () => {
  it("emits required reserved keys + SPI scope + ADMT designation", () => {
    const ctx = new CpraContext({
      businessOrganization: "Acme",
      businessRole: "business",
      sensitivePiInScope: true,
      admtDesignation: "uses_admt",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("CPRA");
    expect(meta["nanorix:framework_version"]).toBe("2023-operative");
    expect(meta.business_organization).toBe("Acme");
    expect(meta.business_role).toBe("business");
    expect(meta.sensitive_pi_in_scope).toBe("true");
    expect(meta.admt_designation).toBe("uses_admt");
    expect(meta.jurisdiction_state).toBe("CA");
  });

  it("rejects invalid admtDesignation", () => {
    expect(
      () =>
        new CpraContext({
          businessOrganization: "X",
          businessRole: "business",
          sensitivePiInScope: true,
          // @ts-expect-error — intentional bad enum
          admtDesignation: "uses_admt_sometimes",
        }),
    ).toThrowError(/CPPA ADMT-designation taxonomy/);
  });

  it("rejects invalid spi_category item", () => {
    expect(
      () =>
        new CpraContext({
          businessOrganization: "X",
          businessRole: "business",
          sensitivePiInScope: true,
          admtDesignation: "does_not_use_admt",
          // @ts-expect-error — intentional bad enum
          spiCategories: ["ssn", "favorite_color"],
        }),
    ).toThrowError(/canonical CPRA §1798\.140\(ae\) SPI categories/);
  });

  it("rejects non-boolean sensitivePiInScope", () => {
    expect(
      () =>
        new CpraContext({
          businessOrganization: "X",
          businessRole: "business",
          // @ts-expect-error — intentional bad type
          sensitivePiInScope: "true",
          admtDesignation: "uses_admt",
        }),
    ).toThrowError(/sensitivePiInScope must be a bool/);
  });

  it("emits spi_categories comma-joined", () => {
    const ctx = new CpraContext({
      businessOrganization: "Acme",
      businessRole: "business",
      sensitivePiInScope: true,
      admtDesignation: "uses_admt_consequential_only",
      spiCategories: ["ssn", "health", "genetic_data"],
    });
    const meta = ctx.toMetadata();
    expect(meta.spi_categories).toBe("ssn,health,genetic_data");
  });

  it("emits all optional fields when set", () => {
    const ctx = new CpraContext({
      businessOrganization: "Acme",
      businessRole: "business",
      sensitivePiInScope: true,
      admtDesignation: "uses_admt",
      retentionPeriod: "30_days",
      purposeOfProcessing: "service_delivery",
      privacyNoticeReference: "notice-2026",
      rightToCorrectWorkflowReference: "rtc-wf",
      rightToLimitSpiWorkflowReference: "rtlspi-wf",
      admtOptOutWorkflowReference: "admt-oo",
      workforceMemberRole: "privacy_engineer",
    });
    const meta = ctx.toMetadata();
    expect(meta.retention_period).toBe("30_days");
    expect(meta.purpose_of_processing).toBe("service_delivery");
    expect(meta.privacy_notice_reference).toBe("notice-2026");
    expect(meta.right_to_correct_workflow_reference).toBe("rtc-wf");
    expect(meta.right_to_limit_spi_workflow_reference).toBe("rtlspi-wf");
    expect(meta.admt_opt_out_workflow_reference).toBe("admt-oo");
    expect(meta.workforce_member_role).toBe("privacy_engineer");
  });
});
