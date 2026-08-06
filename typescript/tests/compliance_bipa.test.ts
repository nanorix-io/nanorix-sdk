import { describe, it, expect } from "vitest";
import { BipaContext } from "../src/compliance/index.js";

describe("BipaContext", () => {
  it("emits required reserved keys + customer fields", () => {
    const ctx = new BipaContext({
      collectingEntity: "Voice Biomarker Co.",
      biometricDataInScope: true,
      consentObtained: true,
      retentionPolicyDocumented: true,
      biometricIdentifierType: "voiceprint",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("BIPA");
    expect(meta["nanorix:framework_version"]).toBe("2008-operative");
    expect(meta.collecting_entity).toBe("Voice Biomarker Co.");
    expect(meta.biometric_data_in_scope).toBe("true");
    expect(meta.consent_obtained).toBe("true");
    expect(meta.retention_policy_documented).toBe("true");
    expect(meta.biometric_identifier_type).toBe("voiceprint");
    expect(meta.jurisdiction_state).toBe("IL");
  });

  it("emits boolean false values as lowercase 'false' strings", () => {
    const ctx = new BipaContext({
      collectingEntity: "X",
      biometricDataInScope: false,
      consentObtained: false,
      retentionPolicyDocumented: false,
      biometricIdentifierType: "no_biometrics_processed",
    });
    const meta = ctx.toMetadata();
    expect(meta.biometric_data_in_scope).toBe("false");
    expect(meta.consent_obtained).toBe("false");
    expect(meta.retention_policy_documented).toBe("false");
  });

  it("rejects empty collectingEntity at construction", () => {
    expect(
      () =>
        new BipaContext({
          collectingEntity: "",
          biometricDataInScope: true,
          consentObtained: true,
          retentionPolicyDocumented: true,
          biometricIdentifierType: "fingerprint",
        }),
    ).toThrowError(/collectingEntity/);
  });

  it("rejects non-boolean booleans (string 'true' passed accidentally)", () => {
    expect(
      () =>
        new BipaContext({
          collectingEntity: "X",
          // @ts-expect-error — intentional bad type
          biometricDataInScope: "true",
          consentObtained: true,
          retentionPolicyDocumented: true,
          biometricIdentifierType: "fingerprint",
        }),
    ).toThrowError(/biometricDataInScope must be a bool/);
  });

  it("rejects invalid biometricIdentifierType", () => {
    expect(
      () =>
        new BipaContext({
          collectingEntity: "X",
          biometricDataInScope: true,
          consentObtained: true,
          retentionPolicyDocumented: true,
          // @ts-expect-error — intentional bad enum
          biometricIdentifierType: "dna_swab",
        }),
    ).toThrowError(/biometric-identifier taxonomy/);
  });

  it("rejects oversized field values", () => {
    expect(
      () =>
        new BipaContext({
          collectingEntity: "A".repeat(257),
          biometricDataInScope: true,
          consentObtained: true,
          retentionPolicyDocumented: true,
          biometricIdentifierType: "fingerprint",
        }),
    ).toThrowError(/256-char limit/);
  });

  it("emits all optional fields when set", () => {
    const ctx = new BipaContext({
      collectingEntity: "Acme",
      biometricDataInScope: true,
      consentObtained: true,
      retentionPolicyDocumented: true,
      biometricIdentifierType: "voiceprint",
      consentBasis: "written_consent_obtained",
      affectedIndividualCategories: [
        "illinois_residents",
        "illinois_patients",
      ],
      consentRecordReference: "consent-2026-Q1",
      retentionPolicyReference: "policy-v3",
      destructionCadenceReference: "destruction-cadence",
      workforceMemberRole: "voice_biomarker_engineer",
    });
    const meta = ctx.toMetadata();
    expect(meta.consent_basis).toBe("written_consent_obtained");
    expect(meta.affected_individual_categories).toBe(
      "illinois_residents,illinois_patients",
    );
    expect(meta.consent_record_reference).toBe("consent-2026-Q1");
    expect(meta.retention_policy_reference).toBe("policy-v3");
    expect(meta.destruction_cadence_reference).toBe("destruction-cadence");
    expect(meta.workforce_member_role).toBe("voice_biomarker_engineer");
  });
});
