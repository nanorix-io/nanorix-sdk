import { describe, it, expect } from "vitest";
import { Iso42001Context } from "../src/compliance/index.js";

describe("Iso42001Context", () => {
  it("emits required reserved keys + PDCA + role", () => {
    const ctx = new Iso42001Context({
      organization: "Acme AI",
      aiSystemRole: "ai_provider",
      aiSystemPurpose: "clinical-decision-support",
      pdcaPhasesInScope: ["plan", "do"],
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("ISO_IEC_42001_2023");
    expect(meta["nanorix:framework_version"]).toBe("2023");
    expect(meta.organization).toBe("Acme AI");
    expect(meta.ai_system_role).toBe("ai_provider");
    expect(meta.ai_system_purpose).toBe("clinical-decision-support");
    expect(meta.pdca_phases_in_scope).toBe("plan,do");
  });

  it("rejects invalid aiSystemRole", () => {
    expect(
      () =>
        new Iso42001Context({
          organization: "X",
          // @ts-expect-error — intentional bad enum
          aiSystemRole: "ai_overlord",
          aiSystemPurpose: "x",
          pdcaPhasesInScope: ["plan"],
        }),
    ).toThrowError(/AI system role taxonomy/);
  });

  it("rejects empty pdcaPhasesInScope", () => {
    expect(
      () =>
        new Iso42001Context({
          organization: "X",
          aiSystemRole: "ai_user",
          aiSystemPurpose: "x",
          pdcaPhasesInScope: [],
        }),
    ).toThrowError(/pdcaPhasesInScope is required and must be non-empty/);
  });

  it("rejects unknown annex A control", () => {
    expect(
      () =>
        new Iso42001Context({
          organization: "X",
          aiSystemRole: "ai_developer",
          aiSystemPurpose: "x",
          pdcaPhasesInScope: ["plan", "do", "check", "act"],
          annexAControlsInScope: ["A_2_2_ai_policy", "A_99_fabricated_control"],
        }),
    ).toThrowError(/23 canonical ISO\/IEC 42001:2023 Annex A controls/);
  });

  it("annexAControls property returns 23 canonical controls", () => {
    const ctx = new Iso42001Context({
      organization: "X",
      aiSystemRole: "ai_deployer",
      aiSystemPurpose: "x",
      pdcaPhasesInScope: ["plan"],
    });
    expect(ctx.annexAControls).toHaveLength(23);
    expect(ctx.pdcaPhases).toEqual(["plan", "do", "check", "act"]);
  });

  it("emits annex_a_controls_in_scope + all optional fields", () => {
    const ctx = new Iso42001Context({
      organization: "Acme",
      aiSystemRole: "ai_deployer",
      aiSystemPurpose: "diagnostic-imaging",
      pdcaPhasesInScope: ["plan", "do"],
      annexAControlsInScope: [
        "A_2_2_ai_policy",
        "A_3_2_ai_roles_and_responsibilities",
      ],
      certificationBody: "A-LIGN",
      certificationStatus: "certified",
      certReference: "cert-2026",
      aimsScopeStatementReference: "scope-v3",
      workforceMemberRole: "ai_engineer",
    });
    const meta = ctx.toMetadata();
    expect(meta.annex_a_controls_in_scope).toBe(
      "A_2_2_ai_policy,A_3_2_ai_roles_and_responsibilities",
    );
    expect(meta.certification_body).toBe("A-LIGN");
    expect(meta.certification_status).toBe("certified");
    expect(meta.cert_reference).toBe("cert-2026");
    expect(meta.aims_scope_statement_reference).toBe("scope-v3");
    expect(meta.workforce_member_role).toBe("ai_engineer");
  });
});
