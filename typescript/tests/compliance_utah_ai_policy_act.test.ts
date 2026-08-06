import { describe, it, expect } from "vitest";
import { UtahAiPolicyActContext } from "../src/compliance/index.js";

describe("UtahAiPolicyActContext", () => {
  it("emits required reserved keys + 3 disclosure booleans + state flag", () => {
    const ctx = new UtahAiPolicyActContext({
      organization: "Healthcare Chatbot Co.",
      actorRole: "regulated_occupation_provider",
      generativeAiInUse: true,
      consumerDisclosureProvided: true,
      regulatedOccupationDisclosureProvided: true,
      generativeAiUseCase: "clinical_decision_support",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("UT_SB_149");
    expect(meta["nanorix:framework_version"]).toBe("2024-operative");
    expect(meta.organization).toBe("Healthcare Chatbot Co.");
    expect(meta.actor_role).toBe("regulated_occupation_provider");
    expect(meta.generative_ai_in_use).toBe("true");
    expect(meta.consumer_disclosure_provided).toBe("true");
    expect(meta.regulated_occupation_disclosure_provided).toBe("true");
    expect(meta.generative_ai_use_case).toBe("clinical_decision_support");
    expect(meta.jurisdiction_state).toBe("UT");
  });

  it("rejects invalid actorRole", () => {
    expect(
      () =>
        new UtahAiPolicyActContext({
          organization: "X",
          // @ts-expect-error — intentional bad enum
          actorRole: "researcher",
          generativeAiInUse: true,
          consumerDisclosureProvided: true,
          regulatedOccupationDisclosureProvided: false,
          generativeAiUseCase: "consumer_facing_chatbot",
        }),
    ).toThrowError(/Utah AI Policy Act-aligned actor roles/);
  });

  it("rejects invalid generativeAiUseCase", () => {
    expect(
      () =>
        new UtahAiPolicyActContext({
          organization: "X",
          actorRole: "generative_ai_user",
          generativeAiInUse: true,
          consumerDisclosureProvided: true,
          regulatedOccupationDisclosureProvided: false,
          // @ts-expect-error — intentional bad enum
          generativeAiUseCase: "law_enforcement",
        }),
    ).toThrowError(/Utah AI Policy Act use-case taxonomy/);
  });

  it("rejects non-bool regulatedOccupationDisclosureProvided", () => {
    expect(
      () =>
        new UtahAiPolicyActContext({
          organization: "X",
          actorRole: "regulated_occupation_provider",
          generativeAiInUse: true,
          consumerDisclosureProvided: true,
          // @ts-expect-error — intentional bad type
          regulatedOccupationDisclosureProvided: "yes",
          generativeAiUseCase: "clinical_decision_support",
        }),
    ).toThrowError(/regulatedOccupationDisclosureProvided must be a bool/);
  });

  it("emits affected_consumer_categories comma-joined", () => {
    const ctx = new UtahAiPolicyActContext({
      organization: "Acme",
      actorRole: "regulated_occupation_provider",
      generativeAiInUse: true,
      consumerDisclosureProvided: true,
      regulatedOccupationDisclosureProvided: true,
      generativeAiUseCase: "patient_engagement",
      affectedConsumerCategories: ["utah_residents", "utah_patients"],
    });
    const meta = ctx.toMetadata();
    expect(meta.affected_consumer_categories).toBe(
      "utah_residents,utah_patients",
    );
  });

  it("emits all optional fields when set", () => {
    const ctx = new UtahAiPolicyActContext({
      organization: "Acme",
      actorRole: "regulated_occupation_provider",
      generativeAiInUse: true,
      consumerDisclosureProvided: true,
      regulatedOccupationDisclosureProvided: true,
      generativeAiUseCase: "clinical_decision_support",
      regulatedOccupation: "healthcare_physician",
      consumerDisclosureReference: "cd-ref",
      regulatedOccupationDisclosureReference: "rod-ref",
      aiLearningLabReference: "lab-ref",
      workforceMemberRole: "attending_physician",
    });
    const meta = ctx.toMetadata();
    expect(meta.regulated_occupation).toBe("healthcare_physician");
    expect(meta.consumer_disclosure_reference).toBe("cd-ref");
    expect(meta.regulated_occupation_disclosure_reference).toBe("rod-ref");
    expect(meta.ai_learning_lab_reference).toBe("lab-ref");
    expect(meta.workforce_member_role).toBe("attending_physician");
  });
});
