import { describe, it, expect } from "vitest";
import { NycLl144Context } from "../src/compliance/index.js";

describe("NycLl144Context", () => {
  it("emits required reserved keys + 3 load-bearing booleans + jurisdiction flags", () => {
    const ctx = new NycLl144Context({
      employerOrganization: "Acme Healthcare",
      employerRole: "employer",
      aedtInUse: true,
      biasAuditCompleted: true,
      candidateNoticeProvided: true,
      decisionType: "screening_for_employment",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("NYC_LL_144");
    expect(meta["nanorix:framework_version"]).toBe("2026-02");
    expect(meta.employer_organization).toBe("Acme Healthcare");
    expect(meta.employer_role).toBe("employer");
    expect(meta.aedt_in_use).toBe("true");
    expect(meta.bias_audit_completed).toBe("true");
    expect(meta.candidate_notice_provided).toBe("true");
    expect(meta.decision_type).toBe("screening_for_employment");
    expect(meta.jurisdiction_state).toBe("NY");
    expect(meta.jurisdiction_city).toBe("NYC");
  });

  it("rejects invalid employerRole", () => {
    expect(
      () =>
        new NycLl144Context({
          employerOrganization: "X",
          // @ts-expect-error — intentional bad enum
          employerRole: "contractor",
          aedtInUse: true,
          biasAuditCompleted: true,
          candidateNoticeProvided: true,
          decisionType: "screening_for_employment",
        }),
    ).toThrowError(/NYC LL 144-aligned actor roles/);
  });

  it("rejects invalid decisionType", () => {
    expect(
      () =>
        new NycLl144Context({
          employerOrganization: "X",
          employerRole: "employer",
          aedtInUse: true,
          biasAuditCompleted: true,
          candidateNoticeProvided: true,
          // @ts-expect-error — intentional bad enum
          decisionType: "salary_adjustment",
        }),
    ).toThrowError(/in-scope decision taxonomy/);
  });

  it("rejects non-bool aedtInUse", () => {
    expect(
      () =>
        new NycLl144Context({
          employerOrganization: "X",
          employerRole: "employer",
          // @ts-expect-error — intentional bad type
          aedtInUse: "true",
          biasAuditCompleted: true,
          candidateNoticeProvided: true,
          decisionType: "promotion_decision",
        }),
    ).toThrowError(/aedtInUse must be a bool/);
  });

  it("emits affected_candidate_categories comma-joined", () => {
    const ctx = new NycLl144Context({
      employerOrganization: "Acme",
      employerRole: "employment_agency",
      aedtInUse: true,
      biasAuditCompleted: true,
      candidateNoticeProvided: true,
      decisionType: "screening_and_promotion",
      affectedCandidateCategories: [
        "nyc_resident_candidates",
        "nyc_resident_employees",
      ],
    });
    const meta = ctx.toMetadata();
    expect(meta.affected_candidate_categories).toBe(
      "nyc_resident_candidates,nyc_resident_employees",
    );
  });

  it("emits all optional fields when set", () => {
    const ctx = new NycLl144Context({
      employerOrganization: "Acme",
      employerRole: "vendor_to_employer",
      aedtInUse: false,
      biasAuditCompleted: false,
      candidateNoticeProvided: false,
      decisionType: "not_in_scope",
      biasAuditReference: "audit-2026",
      biasAuditPublicUrl: "https://acme.example/audit-summary",
      candidateNoticeReference: "notice-template-v1",
      aedtDistributionDate: "2026-01-15",
      workforceMemberRole: "talent_acquisition_engineer",
    });
    const meta = ctx.toMetadata();
    expect(meta.aedt_in_use).toBe("false");
    expect(meta.bias_audit_reference).toBe("audit-2026");
    expect(meta.bias_audit_public_url).toBe(
      "https://acme.example/audit-summary",
    );
    expect(meta.candidate_notice_reference).toBe("notice-template-v1");
    expect(meta.aedt_distribution_date).toBe("2026-01-15");
    expect(meta.workforce_member_role).toBe("talent_acquisition_engineer");
  });
});
