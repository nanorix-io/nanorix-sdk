/** NYC Local Law 144 of 2021 (NYC AEDT hiring law) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2026-02";
const JURISDICTION_STATE = "NY";
const JURISDICTION_CITY = "NYC";

/** NYC LL 144 employer-role taxonomy. */
export type NycLl144EmployerRole =
  | "employer"
  | "employment_agency"
  | "vendor_to_employer";

const VALID_EMPLOYER_ROLES: ReadonlySet<NycLl144EmployerRole> =
  new Set<NycLl144EmployerRole>([
    "employer",
    "employment_agency",
    "vendor_to_employer",
  ]);

/** NYC LL 144 in-scope decision categories. */
export type NycLl144DecisionType =
  | "screening_for_employment"
  | "promotion_decision"
  | "screening_and_promotion"
  | "not_in_scope"
  | "other";

const VALID_DECISION_TYPES: ReadonlySet<NycLl144DecisionType> =
  new Set<NycLl144DecisionType>([
    "screening_for_employment",
    "promotion_decision",
    "screening_and_promotion",
    "not_in_scope",
    "other",
  ]);

/**
 * NYC Local Law 144 of 2021 context for a capsule workload.
 *
 * Declares context for tagging this workload under New York City
 * Local Law 144 of 2021 (NYC AEDT law) and the DCWP Final Rule.
 * Does NOT certify, attest to satisfaction of, or claim compliance
 * with LL 144. AEDT determination, bias-audit independence
 * determination, audit methodology adequacy, public-posting
 * placement, candidate-notice sufficiency, and DCWP enforcement
 * matters remain the customer's regulatory counsel's responsibility.
 */
export interface NycLl144ContextOptions {
  /** Organization deploying/using the AEDT in NYC. Required. */
  employerOrganization: string;
  /** LL 144-aligned actor role. Required. */
  employerRole: NycLl144EmployerRole;
  /** Whether the workload uses an in-scope AEDT. Required. */
  aedtInUse: boolean;
  /** Whether an independent bias audit was completed within the past year per § 20-871(b). Required. */
  biasAuditCompleted: boolean;
  /** Whether candidate notice was provided per § 20-871(b)(2). Required. */
  candidateNoticeProvided: boolean;
  /** In-scope decision category. Required. */
  decisionType: NycLl144DecisionType;
  /** Categories of affected candidates (comma-joined). */
  affectedCandidateCategories?: string[];
  /** Customer-side reference to independent bias-audit record. */
  biasAuditReference?: string;
  /** Customer-side reference to publicly available bias-audit summary URL. */
  biasAuditPublicUrl?: string;
  /** Customer-side reference to candidate notice record. */
  candidateNoticeReference?: string;
  /** AEDT distribution date for the public posting. */
  aedtDistributionDate?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class NycLl144Context {
  readonly employerOrganization: string;
  readonly employerRole: NycLl144EmployerRole;
  readonly aedtInUse: boolean;
  readonly biasAuditCompleted: boolean;
  readonly candidateNoticeProvided: boolean;
  readonly decisionType: NycLl144DecisionType;
  readonly affectedCandidateCategories: ReadonlyArray<string>;
  readonly biasAuditReference?: string;
  readonly biasAuditPublicUrl?: string;
  readonly candidateNoticeReference?: string;
  readonly aedtDistributionDate?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "NYC_LL_144";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get jurisdictionCity(): string {
    return JURISDICTION_CITY;
  }

  get decisionTypeSet(): NycLl144DecisionType[] {
    return [...VALID_DECISION_TYPES].sort();
  }

  get employerRoleSet(): NycLl144EmployerRole[] {
    return [...VALID_EMPLOYER_ROLES].sort();
  }

  constructor(opts: NycLl144ContextOptions) {
    if (!opts.employerOrganization) {
      throw new Error(
        "employerOrganization is required and must be non-empty",
      );
    }
    if (!opts.employerRole) {
      throw new Error("employerRole is required and must be non-empty");
    }
    if (!VALID_EMPLOYER_ROLES.has(opts.employerRole)) {
      throw new Error(
        `employerRole=${JSON.stringify(opts.employerRole)} not in ` +
          `${JSON.stringify(Array.from(VALID_EMPLOYER_ROLES))} ` +
          `(NYC LL 144-aligned actor roles)`,
      );
    }
    if (typeof opts.aedtInUse !== "boolean") {
      throw new Error(
        "aedtInUse must be a bool (got " +
          `${typeof opts.aedtInUse}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.biasAuditCompleted !== "boolean") {
      throw new Error(
        "biasAuditCompleted must be a bool (got " +
          `${typeof opts.biasAuditCompleted}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.candidateNoticeProvided !== "boolean") {
      throw new Error(
        "candidateNoticeProvided must be a bool (got " +
          `${typeof opts.candidateNoticeProvided}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (!opts.decisionType) {
      throw new Error("decisionType is required and must be non-empty");
    }
    if (!VALID_DECISION_TYPES.has(opts.decisionType)) {
      throw new Error(
        `decisionType=${JSON.stringify(opts.decisionType)} not in ` +
          `${JSON.stringify(Array.from(VALID_DECISION_TYPES))} ` +
          `(NYC LL 144 in-scope decision taxonomy)`,
      );
    }

    const affectedCandidateCategories =
      opts.affectedCandidateCategories ?? [];
    if (affectedCandidateCategories.length > 0) {
      affectedCandidateCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `affectedCandidateCategories[${idx}] must be a string ` +
              `(got ${typeof item})`,
          );
        }
        if (!item) {
          throw new Error(
            `affectedCandidateCategories[${idx}] must be non-empty`,
          );
        }
      });
      validateNoCommaInList(
        "affectedCandidateCategories",
        affectedCandidateCategories,
      );
    }

    validateValueLength("employerOrganization", opts.employerOrganization);
    validateValueLength("employerRole", opts.employerRole);
    validateValueLength("decisionType", opts.decisionType);
    if (affectedCandidateCategories.length > 0) {
      validateValueLength(
        "affectedCandidateCategories(joined)",
        affectedCandidateCategories.join(","),
      );
    }
    if (opts.biasAuditReference !== undefined) {
      validateValueLength("biasAuditReference", opts.biasAuditReference);
    }
    if (opts.biasAuditPublicUrl !== undefined) {
      validateValueLength("biasAuditPublicUrl", opts.biasAuditPublicUrl);
    }
    if (opts.candidateNoticeReference !== undefined) {
      validateValueLength(
        "candidateNoticeReference",
        opts.candidateNoticeReference,
      );
    }
    if (opts.aedtDistributionDate !== undefined) {
      validateValueLength(
        "aedtDistributionDate",
        opts.aedtDistributionDate,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.employerOrganization = opts.employerOrganization;
    this.employerRole = opts.employerRole;
    this.aedtInUse = opts.aedtInUse;
    this.biasAuditCompleted = opts.biasAuditCompleted;
    this.candidateNoticeProvided = opts.candidateNoticeProvided;
    this.decisionType = opts.decisionType;
    this.affectedCandidateCategories = Object.freeze([
      ...affectedCandidateCategories,
    ]);
    this.biasAuditReference = opts.biasAuditReference;
    this.biasAuditPublicUrl = opts.biasAuditPublicUrl;
    this.candidateNoticeReference = opts.candidateNoticeReference;
    this.aedtDistributionDate = opts.aedtDistributionDate;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      employer_organization: this.employerOrganization,
      employer_role: this.employerRole,
      aedt_in_use: this.aedtInUse ? "true" : "false",
      bias_audit_completed: this.biasAuditCompleted ? "true" : "false",
      candidate_notice_provided: this.candidateNoticeProvided
        ? "true"
        : "false",
      decision_type: this.decisionType,
      jurisdiction_state: JURISDICTION_STATE,
      jurisdiction_city: JURISDICTION_CITY,
    };
    if (this.affectedCandidateCategories.length > 0) {
      meta.affected_candidate_categories =
        this.affectedCandidateCategories.join(",");
    }
    if (this.biasAuditReference !== undefined) {
      meta.bias_audit_reference = this.biasAuditReference;
    }
    if (this.biasAuditPublicUrl !== undefined) {
      meta.bias_audit_public_url = this.biasAuditPublicUrl;
    }
    if (this.candidateNoticeReference !== undefined) {
      meta.candidate_notice_reference = this.candidateNoticeReference;
    }
    if (this.aedtDistributionDate !== undefined) {
      meta.aedt_distribution_date = this.aedtDistributionDate;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
