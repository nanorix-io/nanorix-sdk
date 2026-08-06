/** Utah Artificial Intelligence Policy Act (SB 149) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2024-operative";
const JURISDICTION_STATE = "UT";

/** Utah AI Policy Act-aligned actor roles. */
export type UtahAiPolicyActActorRole =
  | "generative_ai_provider"
  | "generative_ai_user"
  | "regulated_occupation_provider"
  | "ai_learning_lab_participant";

const VALID_ACTOR_ROLES: ReadonlySet<UtahAiPolicyActActorRole> =
  new Set<UtahAiPolicyActActorRole>([
    "generative_ai_provider",
    "generative_ai_user",
    "regulated_occupation_provider",
    "ai_learning_lab_participant",
  ]);

/** Utah AI Policy Act generative-AI use-case categories. */
export type UtahAiPolicyActGenerativeAiUseCase =
  | "consumer_facing_chatbot"
  | "clinical_decision_support"
  | "patient_engagement"
  | "healthcare_administrative"
  | "customer_service"
  | "content_generation"
  | "code_generation"
  | "internal_analytics"
  | "not_generative_ai"
  | "other";

const VALID_GENERATIVE_AI_USE_CASES: ReadonlySet<UtahAiPolicyActGenerativeAiUseCase> =
  new Set<UtahAiPolicyActGenerativeAiUseCase>([
    "consumer_facing_chatbot",
    "clinical_decision_support",
    "patient_engagement",
    "healthcare_administrative",
    "customer_service",
    "content_generation",
    "code_generation",
    "internal_analytics",
    "not_generative_ai",
    "other",
  ]);

/**
 * Utah AI Policy Act (SB 149) context for a capsule workload.
 *
 * Declares context for tagging this workload under the Utah AI
 * Policy Act. Does NOT certify, attest to satisfaction of, or claim
 * compliance with the Act. Generative-AI determination, regulated-
 * occupation determination, disclosure adequacy, AI Learning
 * Laboratory Program eligibility, and Division-of-Consumer-
 * Protection enforcement matters remain the customer's regulatory
 * counsel's responsibility.
 */
export interface UtahAiPolicyActContextOptions {
  /** Organization providing/using generative AI in Utah. Required. */
  organization: string;
  /** Utah AI Policy Act-aligned actor role. Required. */
  actorRole: UtahAiPolicyActActorRole;
  /** Whether the workload uses generative AI per § 13-72-101. Required. */
  generativeAiInUse: boolean;
  /** Whether § 13-72-202 consumer disclosure is provided. Required. */
  consumerDisclosureProvided: boolean;
  /** Whether § 13-72-203 regulated-occupation disclosure is provided. Required. */
  regulatedOccupationDisclosureProvided: boolean;
  /** Generative-AI use-case category. Required. */
  generativeAiUseCase: UtahAiPolicyActGenerativeAiUseCase;
  /** Customer-declared regulated occupation under Utah law. */
  regulatedOccupation?: string;
  /** Categories of affected Utah consumers (comma-joined). */
  affectedConsumerCategories?: string[];
  /** Customer-side reference to § 13-72-202 consumer-disclosure record. */
  consumerDisclosureReference?: string;
  /** Customer-side reference to § 13-72-203 regulated-occupation disclosure record. */
  regulatedOccupationDisclosureReference?: string;
  /** Customer-side reference to AI Learning Laboratory Program participation. */
  aiLearningLabReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class UtahAiPolicyActContext {
  readonly organization: string;
  readonly actorRole: UtahAiPolicyActActorRole;
  readonly generativeAiInUse: boolean;
  readonly consumerDisclosureProvided: boolean;
  readonly regulatedOccupationDisclosureProvided: boolean;
  readonly generativeAiUseCase: UtahAiPolicyActGenerativeAiUseCase;
  readonly regulatedOccupation?: string;
  readonly affectedConsumerCategories: ReadonlyArray<string>;
  readonly consumerDisclosureReference?: string;
  readonly regulatedOccupationDisclosureReference?: string;
  readonly aiLearningLabReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "UT_SB_149";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get generativeAiUseCaseSet(): UtahAiPolicyActGenerativeAiUseCase[] {
    return [...VALID_GENERATIVE_AI_USE_CASES].sort();
  }

  get actorRoleSet(): UtahAiPolicyActActorRole[] {
    return [...VALID_ACTOR_ROLES].sort();
  }

  constructor(opts: UtahAiPolicyActContextOptions) {
    if (!opts.organization) {
      throw new Error("organization is required and must be non-empty");
    }
    if (!opts.actorRole) {
      throw new Error("actorRole is required and must be non-empty");
    }
    if (!VALID_ACTOR_ROLES.has(opts.actorRole)) {
      throw new Error(
        `actorRole=${JSON.stringify(opts.actorRole)} not in ` +
          `${JSON.stringify(Array.from(VALID_ACTOR_ROLES))} ` +
          `(Utah AI Policy Act-aligned actor roles)`,
      );
    }
    if (typeof opts.generativeAiInUse !== "boolean") {
      throw new Error(
        "generativeAiInUse must be a bool (got " +
          `${typeof opts.generativeAiInUse}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.consumerDisclosureProvided !== "boolean") {
      throw new Error(
        "consumerDisclosureProvided must be a bool (got " +
          `${typeof opts.consumerDisclosureProvided}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.regulatedOccupationDisclosureProvided !== "boolean") {
      throw new Error(
        "regulatedOccupationDisclosureProvided must be a bool (got " +
          `${typeof opts.regulatedOccupationDisclosureProvided}); ` +
          `pass true or false, not the string 'true'`,
      );
    }
    if (!opts.generativeAiUseCase) {
      throw new Error(
        "generativeAiUseCase is required and must be non-empty",
      );
    }
    if (!VALID_GENERATIVE_AI_USE_CASES.has(opts.generativeAiUseCase)) {
      throw new Error(
        `generativeAiUseCase=${JSON.stringify(opts.generativeAiUseCase)} ` +
          `not in ${JSON.stringify(Array.from(VALID_GENERATIVE_AI_USE_CASES))} ` +
          `(Utah AI Policy Act use-case taxonomy)`,
      );
    }
    if (opts.regulatedOccupation !== undefined) {
      if (!opts.regulatedOccupation) {
        throw new Error(
          "regulatedOccupation if provided must be non-empty",
        );
      }
    }

    const affectedConsumerCategories = opts.affectedConsumerCategories ?? [];
    if (affectedConsumerCategories.length > 0) {
      affectedConsumerCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `affectedConsumerCategories[${idx}] must be a string ` +
              `(got ${typeof item})`,
          );
        }
        if (!item) {
          throw new Error(
            `affectedConsumerCategories[${idx}] must be non-empty`,
          );
        }
      });
      validateNoCommaInList(
        "affectedConsumerCategories",
        affectedConsumerCategories,
      );
    }

    validateValueLength("organization", opts.organization);
    validateValueLength("actorRole", opts.actorRole);
    validateValueLength(
      "generativeAiUseCase",
      opts.generativeAiUseCase,
    );
    if (opts.regulatedOccupation !== undefined) {
      validateValueLength(
        "regulatedOccupation",
        opts.regulatedOccupation,
      );
    }
    if (affectedConsumerCategories.length > 0) {
      validateValueLength(
        "affectedConsumerCategories(joined)",
        affectedConsumerCategories.join(","),
      );
    }
    if (opts.consumerDisclosureReference !== undefined) {
      validateValueLength(
        "consumerDisclosureReference",
        opts.consumerDisclosureReference,
      );
    }
    if (opts.regulatedOccupationDisclosureReference !== undefined) {
      validateValueLength(
        "regulatedOccupationDisclosureReference",
        opts.regulatedOccupationDisclosureReference,
      );
    }
    if (opts.aiLearningLabReference !== undefined) {
      validateValueLength(
        "aiLearningLabReference",
        opts.aiLearningLabReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.organization = opts.organization;
    this.actorRole = opts.actorRole;
    this.generativeAiInUse = opts.generativeAiInUse;
    this.consumerDisclosureProvided = opts.consumerDisclosureProvided;
    this.regulatedOccupationDisclosureProvided =
      opts.regulatedOccupationDisclosureProvided;
    this.generativeAiUseCase = opts.generativeAiUseCase;
    this.regulatedOccupation = opts.regulatedOccupation;
    this.affectedConsumerCategories = Object.freeze([
      ...affectedConsumerCategories,
    ]);
    this.consumerDisclosureReference = opts.consumerDisclosureReference;
    this.regulatedOccupationDisclosureReference =
      opts.regulatedOccupationDisclosureReference;
    this.aiLearningLabReference = opts.aiLearningLabReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      organization: this.organization,
      actor_role: this.actorRole,
      generative_ai_in_use: this.generativeAiInUse ? "true" : "false",
      consumer_disclosure_provided: this.consumerDisclosureProvided
        ? "true"
        : "false",
      regulated_occupation_disclosure_provided: this
        .regulatedOccupationDisclosureProvided
        ? "true"
        : "false",
      generative_ai_use_case: this.generativeAiUseCase,
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.regulatedOccupation !== undefined) {
      meta.regulated_occupation = this.regulatedOccupation;
    }
    if (this.affectedConsumerCategories.length > 0) {
      meta.affected_consumer_categories =
        this.affectedConsumerCategories.join(",");
    }
    if (this.consumerDisclosureReference !== undefined) {
      meta.consumer_disclosure_reference = this.consumerDisclosureReference;
    }
    if (this.regulatedOccupationDisclosureReference !== undefined) {
      meta.regulated_occupation_disclosure_reference =
        this.regulatedOccupationDisclosureReference;
    }
    if (this.aiLearningLabReference !== undefined) {
      meta.ai_learning_lab_reference = this.aiLearningLabReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
