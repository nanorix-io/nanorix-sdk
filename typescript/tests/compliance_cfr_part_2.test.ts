import { describe, it, expect } from "vitest";
import { CFRPart2Context } from "../src/compliance/index.js";

describe("CFRPart2Context", () => {
  it("emits required reserved keys + structural redisclosure flag", () => {
    const ctx = new CFRPart2Context({
      part2Program: "Behavioral Health Clinic",
      minimumNecessaryPurpose: "SUD treatment workflow",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("42_CFR_Part_2");
    expect(meta["nanorix:framework_version"]).toBe("2024-Revision");
    expect(meta.part_2_program).toBe("Behavioral Health Clinic");
    expect(meta.minimum_necessary_purpose).toBe("SUD treatment workflow");
    expect(meta.data_classification).toBe("PHI-SUD");
    expect(meta.disclosure_basis).toBe("not_disclosed");
    expect(meta.treatment_record_redisclosure_restriction).toBe("true");
    expect(ctx.redisclosureRestrictionApplies).toBe(true);
  });

  it("requires consentReference when disclosureBasis='patient_consent'", () => {
    expect(
      () =>
        new CFRPart2Context({
          part2Program: "Clinic",
          minimumNecessaryPurpose: "x",
          disclosureBasis: "patient_consent",
        }),
    ).toThrowError(/disclosureBasis='patient_consent' requires consentReference/);
  });

  it("requires researchAuthorizationReference when disclosureBasis='research'", () => {
    expect(
      () =>
        new CFRPart2Context({
          part2Program: "Clinic",
          minimumNecessaryPurpose: "x",
          disclosureBasis: "research",
        }),
    ).toThrowError(
      /disclosureBasis='research' requires researchAuthorizationReference/,
    );
  });

  it("rejects empty part2Program", () => {
    expect(
      () =>
        new CFRPart2Context({
          part2Program: "",
          minimumNecessaryPurpose: "x",
        }),
    ).toThrowError(/part2Program/);
  });

  it("rejects invalid disclosure_basis", () => {
    expect(
      () =>
        new CFRPart2Context({
          part2Program: "X",
          minimumNecessaryPurpose: "y",
          // @ts-expect-error — intentional bad enum
          disclosureBasis: "informal_sharing",
        }),
    ).toThrowError(/42 CFR Part 2 §2\.31-§2\.65 disclosure-basis set/);
  });

  it("emits all optional fields when set + accepts paired reference", () => {
    const ctx = new CFRPart2Context({
      part2Program: "Clinic",
      minimumNecessaryPurpose: "x",
      dataClassification: "PHI-SUD-clinical-notes",
      disclosureBasis: "patient_consent",
      consentReference: "consent-2026-Q1",
      qsoAgreementReference: "qsoa-v3",
      workforceMemberRole: "sud_counselor",
    });
    const meta = ctx.toMetadata();
    expect(meta.data_classification).toBe("PHI-SUD-clinical-notes");
    expect(meta.disclosure_basis).toBe("patient_consent");
    expect(meta.consent_reference).toBe("consent-2026-Q1");
    expect(meta.qso_agreement_reference).toBe("qsoa-v3");
    expect(meta.workforce_member_role).toBe("sud_counselor");
  });
});
