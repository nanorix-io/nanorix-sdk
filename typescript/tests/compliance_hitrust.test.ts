import { describe, it, expect } from "vitest";
import { HitrustContext } from "../src/compliance/index.js";

describe("HitrustContext", () => {
  it("emits required reserved keys + customer fields", () => {
    const ctx = new HitrustContext({
      coveredEntity: "Acme Hospital",
      assessmentType: "r2",
      dataClassification: "PHI",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("HITRUST_CSF_v11");
    expect(meta["nanorix:framework_version"]).toBe("v11");
    expect(meta.covered_entity).toBe("Acme Hospital");
    expect(meta.assessment_type).toBe("r2");
    expect(meta.data_classification).toBe("PHI");
  });

  it("rejects invalid assessment_type", () => {
    expect(
      () =>
        new HitrustContext({
          coveredEntity: "Acme",
          // @ts-expect-error — intentional bad enum
          assessmentType: "r3",
          dataClassification: "PHI",
        }),
    ).toThrowError(/HITRUST Alliance assessment tiers/);
  });

  it("emits in_scope_domains comma-joined when provided", () => {
    const ctx = new HitrustContext({
      coveredEntity: "Acme",
      assessmentType: "i1",
      dataClassification: "PHI",
      inScopeDomains: ["10_access_control", "11_audit_logging_and_monitoring"],
    });
    const meta = ctx.toMetadata();
    expect(meta.in_scope_domains).toBe(
      "10_access_control,11_audit_logging_and_monitoring",
    );
  });

  it("rejects unknown control domain", () => {
    expect(
      () =>
        new HitrustContext({
          coveredEntity: "Acme",
          assessmentType: "r2",
          dataClassification: "PHI",
          inScopeDomains: ["20_quantum_security"],
        }),
    ).toThrowError(/19 canonical HITRUST CSF v11 control domains/);
  });

  it("domains property returns 19 canonical domains as defensive copy", () => {
    const ctx = new HitrustContext({
      coveredEntity: "X",
      assessmentType: "e1",
      dataClassification: "tenant_data",
    });
    expect(ctx.domains).toHaveLength(19);
    const mutated = ctx.domains;
    mutated.push("99_bogus");
    expect(ctx.domains).toHaveLength(19); // defensive copy
  });

  it("emits all optional fields when set", () => {
    const ctx = new HitrustContext({
      coveredEntity: "Acme",
      assessmentType: "r2",
      dataClassification: "PHI",
      assessor: "Coalfire",
      assessmentPeriod: "2026-Q2",
      certReference: "cert-2026-r2",
      workforceMemberRole: "attending_radiologist",
    });
    const meta = ctx.toMetadata();
    expect(meta.assessor).toBe("Coalfire");
    expect(meta.assessment_period).toBe("2026-Q2");
    expect(meta.cert_reference).toBe("cert-2026-r2");
    expect(meta.workforce_member_role).toBe("attending_radiologist");
  });
});
