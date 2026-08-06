/** Colorado AI Act (SB24-205) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2026-02";
const JURISDICTION_STATE = "CO";

/** Colorado AI Act-aligned actor roles. */
export type ColoradoAiActActorRole =
  | "developer"
  | "deployer"
  | "developer_and_deployer";

const VALID_ACTOR_ROLES: ReadonlySet<ColoradoAiActActorRole> =
  new Set<ColoradoAiActActorRole>([
    "developer",
    "deployer",
    "developer_and_deployer",
  ]);

/** § 6-1-1701(3) Consequential Decision categories. */
export type ColoradoAiActConsequentialDecisionCategory =
  | "education"
  | "employment"
  | "financial_or_lending"
  | "essential_government_service"
  | "healthcare"
  | "housing"
  | "insurance"
  | "legal_service"
  | "not_consequential_decision"
  | "other";

const VALID_CONSEQUENTIAL_DECISION_CATEGORIES: ReadonlySet<ColoradoAiActConsequentialDecisionCategory> =
  new Set<ColoradoAiActConsequentialDecisionCategory>([
    "education",
    "employment",
    "financial_or_lending",
    "essential_government_service",
    "healthcare",
    "housing",
    "insurance",
    "legal_service",
    "not_consequential_decision",
    "other",
  ]);

/** § 6-1-1703(4) consumer-notice-timing taxonomy. */
export type ColoradoAiActConsumerNoticeTiming =
  | "before_consequential_decision"
  | "concurrent_with_decision"
  | "post_decision_adverse_explanation"
  | "no_notice_provided"
  | "not_required"
  | "other";

const VALID_CONSUMER_NOTICE_TIMINGS: ReadonlySet<ColoradoAiActConsumerNoticeTiming> =
  new Set<ColoradoAiActConsumerNoticeTiming>([
    "before_consequential_decision",
    "concurrent_with_decision",
    "post_decision_adverse_explanation",
    "no_notice_provided",
    "not_required",
    "other",
  ]);

/**
 * Colorado AI Act (SB24-205) context for a capsule workload.
 *
 * Declares context for tagging this workload under the Colorado AI
 * Act. Does NOT certify, attest to satisfaction of, or claim
 * compliance with the Act. High-Risk-AI-System determination,
 * Consequential Decision scope analysis, impact-assessment
 * adequacy, reasonable-care determination, and Attorney-General
 * enforcement matters remain the customer's regulatory counsel's
 * responsibility.
 */
export interface ColoradoAiActContextOptions {
  /** Organization developing/deploying the High-Risk AI System. Required. */
  organization: string;
  /** Colorado AI Act-aligned actor role. Required. */
  actorRole: ColoradoAiActActorRole;
  /** Whether this workload is a High-Risk AI System per § 6-1-1701(9). Required. */
  highRiskAiSystem: boolean;
  /** Whether a § 6-1-1703(3) impact assessment is completed. Required. */
  impactAssessmentCompleted: boolean;
  /** Whether § 6-1-1703(4)(a) consumer notice is provided. Required. */
  consumerNoticeProvided: boolean;
  /** Consequential Decision category. Required. */
  consequentialDecisionCategory: ColoradoAiActConsequentialDecisionCategory;
  /** Consumer-notice timing. Optional. */
  consumerNoticeTiming?: ColoradoAiActConsumerNoticeTiming;
  /** Categories of affected consumers (comma-joined). */
  affectedConsumerCategories?: string[];
  /** Customer-side reference to § 6-1-1703(3) impact-assessment record. */
  impactAssessmentReference?: string;
  /** Customer-side reference to § 6-1-1703(2) risk-management policy. */
  riskManagementPolicyReference?: string;
  /** Customer-side reference to § 6-1-1702(2) developer disclosure. */
  developerDisclosureReference?: string;
  /** Customer-side reference to § 6-1-1703(4)(b) adverse-decision explanation workflow. */
  adverseDecisionExplanationReference?: string;
  /** Customer-side reference to § 6-1-1703(4)(c) consumer-appeal workflow. */
  appealWorkflowReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class ColoradoAiActContext {
  readonly organization: string;
  readonly actorRole: ColoradoAiActActorRole;
  readonly highRiskAiSystem: boolean;
  readonly impactAssessmentCompleted: boolean;
  readonly consumerNoticeProvided: boolean;
  readonly consequentialDecisionCategory: ColoradoAiActConsequentialDecisionCategory;
  readonly consumerNoticeTiming?: ColoradoAiActConsumerNoticeTiming;
  readonly affectedConsumerCategories: ReadonlyArray<string>;
  readonly impactAssessmentReference?: string;
  readonly riskManagementPolicyReference?: string;
  readonly developerDisclosureReference?: string;
  readonly adverseDecisionExplanationReference?: string;
  readonly appealWorkflowReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "CO_SB24_205";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get consequentialDecisionCategorySet(): ColoradoAiActConsequentialDecisionCategory[] {
    return [...VALID_CONSEQUENTIAL_DECISION_CATEGORIES].sort();
  }

  get consumerNoticeTimingSet(): ColoradoAiActConsumerNoticeTiming[] {
    return [...VALID_CONSUMER_NOTICE_TIMINGS].sort();
  }

  get actorRoleSet(): ColoradoAiActActorRole[] {
    return [...VALID_ACTOR_ROLES].sort();
  }

  constructor(opts: ColoradoAiActContextOptions) {
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
          `(Colorado AI Act-aligned actor roles)`,
      );
    }
    if (typeof opts.highRiskAiSystem !== "boolean") {
      throw new Error(
        "highRiskAiSystem must be a bool (got " +
          `${typeof opts.highRiskAiSystem}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.impactAssessmentCompleted !== "boolean") {
      throw new Error(
        "impactAssessmentCompleted must be a bool (got " +
          `${typeof opts.impactAssessmentCompleted}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.consumerNoticeProvided !== "boolean") {
      throw new Error(
        "consumerNoticeProvided must be a bool (got " +
          `${typeof opts.consumerNoticeProvided}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (!opts.consequentialDecisionCategory) {
      throw new Error(
        "consequentialDecisionCategory is required and must be non-empty",
      );
    }
    if (
      !VALID_CONSEQUENTIAL_DECISION_CATEGORIES.has(
        opts.consequentialDecisionCategory,
      )
    ) {
      throw new Error(
        `consequentialDecisionCategory=${JSON.stringify(opts.consequentialDecisionCategory)} ` +
          `not in ${JSON.stringify(Array.from(VALID_CONSEQUENTIAL_DECISION_CATEGORIES))} ` +
          `(§ 6-1-1701(3) Consequential Decision taxonomy)`,
      );
    }
    if (opts.consumerNoticeTiming !== undefined) {
      if (!opts.consumerNoticeTiming) {
        throw new Error(
          "consumerNoticeTiming if provided must be non-empty",
        );
      }
      if (!VALID_CONSUMER_NOTICE_TIMINGS.has(opts.consumerNoticeTiming)) {
        throw new Error(
          `consumerNoticeTiming=${JSON.stringify(opts.consumerNoticeTiming)} ` +
            `not in ${JSON.stringify(Array.from(VALID_CONSUMER_NOTICE_TIMINGS))} ` +
            `(§ 6-1-1703(4) consumer-notice-timing taxonomy)`,
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
      "consequentialDecisionCategory",
      opts.consequentialDecisionCategory,
    );
    if (opts.consumerNoticeTiming !== undefined) {
      validateValueLength(
        "consumerNoticeTiming",
        opts.consumerNoticeTiming,
      );
    }
    if (affectedConsumerCategories.length > 0) {
      validateValueLength(
        "affectedConsumerCategories(joined)",
        affectedConsumerCategories.join(","),
      );
    }
    if (opts.impactAssessmentReference !== undefined) {
      validateValueLength(
        "impactAssessmentReference",
        opts.impactAssessmentReference,
      );
    }
    if (opts.riskManagementPolicyReference !== undefined) {
      validateValueLength(
        "riskManagementPolicyReference",
        opts.riskManagementPolicyReference,
      );
    }
    if (opts.developerDisclosureReference !== undefined) {
      validateValueLength(
        "developerDisclosureReference",
        opts.developerDisclosureReference,
      );
    }
    if (opts.adverseDecisionExplanationReference !== undefined) {
      validateValueLength(
        "adverseDecisionExplanationReference",
        opts.adverseDecisionExplanationReference,
      );
    }
    if (opts.appealWorkflowReference !== undefined) {
      validateValueLength(
        "appealWorkflowReference",
        opts.appealWorkflowReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.organization = opts.organization;
    this.actorRole = opts.actorRole;
    this.highRiskAiSystem = opts.highRiskAiSystem;
    this.impactAssessmentCompleted = opts.impactAssessmentCompleted;
    this.consumerNoticeProvided = opts.consumerNoticeProvided;
    this.consequentialDecisionCategory = opts.consequentialDecisionCategory;
    this.consumerNoticeTiming = opts.consumerNoticeTiming;
    this.affectedConsumerCategories = Object.freeze([
      ...affectedConsumerCategories,
    ]);
    this.impactAssessmentReference = opts.impactAssessmentReference;
    this.riskManagementPolicyReference = opts.riskManagementPolicyReference;
    this.developerDisclosureReference = opts.developerDisclosureReference;
    this.adverseDecisionExplanationReference =
      opts.adverseDecisionExplanationReference;
    this.appealWorkflowReference = opts.appealWorkflowReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      organization: this.organization,
      actor_role: this.actorRole,
      high_risk_ai_system: this.highRiskAiSystem ? "true" : "false",
      impact_assessment_completed: this.impactAssessmentCompleted
        ? "true"
        : "false",
      consumer_notice_provided: this.consumerNoticeProvided
        ? "true"
        : "false",
      consequential_decision_category: this.consequentialDecisionCategory,
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.consumerNoticeTiming !== undefined) {
      meta.consumer_notice_timing = this.consumerNoticeTiming;
    }
    if (this.affectedConsumerCategories.length > 0) {
      meta.affected_consumer_categories =
        this.affectedConsumerCategories.join(",");
    }
    if (this.impactAssessmentReference !== undefined) {
      meta.impact_assessment_reference = this.impactAssessmentReference;
    }
    if (this.riskManagementPolicyReference !== undefined) {
      meta.risk_management_policy_reference =
        this.riskManagementPolicyReference;
    }
    if (this.developerDisclosureReference !== undefined) {
      meta.developer_disclosure_reference = this.developerDisclosureReference;
    }
    if (this.adverseDecisionExplanationReference !== undefined) {
      meta.adverse_decision_explanation_reference =
        this.adverseDecisionExplanationReference;
    }
    if (this.appealWorkflowReference !== undefined) {
      meta.appeal_workflow_reference = this.appealWorkflowReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
