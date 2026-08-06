/** Texas SB 14 (Texas Responsible AI Governance Act / TRAIGA) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2025-enacted";
const JURISDICTION_STATE = "TX";

/** TRAIGA-aligned actor roles. */
export type TexasSb14DeployerRole = "developer" | "deployer" | "distributor";

const VALID_DEPLOYER_ROLES: ReadonlySet<TexasSb14DeployerRole> =
  new Set<TexasSb14DeployerRole>(["developer", "deployer", "distributor"]);

/** TRAIGA high-risk consequential-decision use-case taxonomy. */
export type TexasSb14HighRiskUseCase =
  | "healthcare_clinical_decision_support"
  | "healthcare_treatment_recommendation"
  | "healthcare_benefits_determination"
  | "employment_decision"
  | "education_decision"
  | "financial_services_decision"
  | "housing_decision"
  | "government_services_decision"
  | "criminal_justice_decision"
  | "insurance_decision"
  | "not_high_risk"
  | "other_high_risk";

const VALID_HIGH_RISK_USE_CASES: ReadonlySet<TexasSb14HighRiskUseCase> =
  new Set<TexasSb14HighRiskUseCase>([
    "healthcare_clinical_decision_support",
    "healthcare_treatment_recommendation",
    "healthcare_benefits_determination",
    "employment_decision",
    "education_decision",
    "financial_services_decision",
    "housing_decision",
    "government_services_decision",
    "criminal_justice_decision",
    "insurance_decision",
    "not_high_risk",
    "other_high_risk",
  ]);

/**
 * Texas SB 14 (TRAIGA) context for a capsule workload.
 *
 * Declares context for tagging this workload under Texas SB 14
 * (Texas Responsible AI Governance Act). Does NOT certify, attest
 * to satisfaction of, or claim compliance with TRAIGA. Statutory
 * applicability determination, consequential-decision scope
 * analysis, and impact assessments remain the customer's
 * regulatory counsel's responsibility.
 */
export interface TexasSb14ContextOptions {
  /** Organization deploying/developing/distributing the AI system in Texas. Required. */
  deployerOrganization: string;
  /** TRAIGA-aligned actor role. Required. */
  deployerRole: TexasSb14DeployerRole;
  /** High-risk consequential-decision use-case. Required. */
  highRiskUseCase: TexasSb14HighRiskUseCase;
  /** Whether the customer has completed a TRAIGA-aligned impact assessment. Required. */
  impactAssessmentCompleted: boolean;
  /** Categories of affected individuals (comma-joined). */
  affectedCategories?: string[];
  /** Customer-side reference to algorithmic impact assessment. */
  impactAssessmentReference?: string;
  /** Customer-side reference to consumer notice. */
  noticeToConsumersReference?: string;
  /** Customer-side bias / disparate-impact audit reference. */
  biasAuditReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class TexasSb14Context {
  readonly deployerOrganization: string;
  readonly deployerRole: TexasSb14DeployerRole;
  readonly highRiskUseCase: TexasSb14HighRiskUseCase;
  readonly impactAssessmentCompleted: boolean;
  readonly affectedCategories: ReadonlyArray<string>;
  readonly impactAssessmentReference?: string;
  readonly noticeToConsumersReference?: string;
  readonly biasAuditReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "TX_SB_14";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get highRiskUseCaseSet(): TexasSb14HighRiskUseCase[] {
    return [...VALID_HIGH_RISK_USE_CASES].sort();
  }

  constructor(opts: TexasSb14ContextOptions) {
    if (!opts.deployerOrganization) {
      throw new Error(
        "deployerOrganization is required and must be non-empty",
      );
    }
    if (!opts.deployerRole) {
      throw new Error("deployerRole is required and must be non-empty");
    }
    if (!VALID_DEPLOYER_ROLES.has(opts.deployerRole)) {
      throw new Error(
        `deployerRole=${JSON.stringify(opts.deployerRole)} not in ` +
          `${JSON.stringify(Array.from(VALID_DEPLOYER_ROLES))} ` +
          `(TRAIGA-aligned actor roles)`,
      );
    }
    if (!opts.highRiskUseCase) {
      throw new Error("highRiskUseCase is required and must be non-empty");
    }
    if (!VALID_HIGH_RISK_USE_CASES.has(opts.highRiskUseCase)) {
      throw new Error(
        `highRiskUseCase=${JSON.stringify(opts.highRiskUseCase)} not in ` +
          `${JSON.stringify(Array.from(VALID_HIGH_RISK_USE_CASES))} ` +
          `(high-risk consequential-decision taxonomy)`,
      );
    }
    if (typeof opts.impactAssessmentCompleted !== "boolean") {
      throw new Error(
        "impactAssessmentCompleted must be a bool (got " +
          `${typeof opts.impactAssessmentCompleted}); pass true or false, ` +
          `not the string 'true'`,
      );
    }

    const affectedCategories = opts.affectedCategories ?? [];
    if (affectedCategories.length > 0) {
      affectedCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `affectedCategories[${idx}] must be a string (got ${typeof item})`,
          );
        }
        if (!item) {
          throw new Error(`affectedCategories[${idx}] must be non-empty`);
        }
      });
      validateNoCommaInList("affectedCategories", affectedCategories);
    }

    validateValueLength(
      "deployerOrganization",
      opts.deployerOrganization,
    );
    validateValueLength("deployerRole", opts.deployerRole);
    validateValueLength("highRiskUseCase", opts.highRiskUseCase);
    if (affectedCategories.length > 0) {
      validateValueLength(
        "affectedCategories(joined)",
        affectedCategories.join(","),
      );
    }
    if (opts.impactAssessmentReference !== undefined) {
      validateValueLength(
        "impactAssessmentReference",
        opts.impactAssessmentReference,
      );
    }
    if (opts.noticeToConsumersReference !== undefined) {
      validateValueLength(
        "noticeToConsumersReference",
        opts.noticeToConsumersReference,
      );
    }
    if (opts.biasAuditReference !== undefined) {
      validateValueLength("biasAuditReference", opts.biasAuditReference);
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.deployerOrganization = opts.deployerOrganization;
    this.deployerRole = opts.deployerRole;
    this.highRiskUseCase = opts.highRiskUseCase;
    this.impactAssessmentCompleted = opts.impactAssessmentCompleted;
    this.affectedCategories = Object.freeze([...affectedCategories]);
    this.impactAssessmentReference = opts.impactAssessmentReference;
    this.noticeToConsumersReference = opts.noticeToConsumersReference;
    this.biasAuditReference = opts.biasAuditReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      deployer_organization: this.deployerOrganization,
      deployer_role: this.deployerRole,
      high_risk_use_case: this.highRiskUseCase,
      impact_assessment_completed: this.impactAssessmentCompleted
        ? "true"
        : "false",
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.affectedCategories.length > 0) {
      meta.affected_categories = this.affectedCategories.join(",");
    }
    if (this.impactAssessmentReference !== undefined) {
      meta.impact_assessment_reference = this.impactAssessmentReference;
    }
    if (this.noticeToConsumersReference !== undefined) {
      meta.notice_to_consumers_reference = this.noticeToConsumersReference;
    }
    if (this.biasAuditReference !== undefined) {
      meta.bias_audit_reference = this.biasAuditReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
