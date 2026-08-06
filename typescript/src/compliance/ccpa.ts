/** CCPA (California Consumer Privacy Act) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2020-operative";
const JURISDICTION_STATE = "CA";

/** CCPA §1798.140 actor roles. */
export type CcpaBusinessRole =
  | "business"
  | "service_provider"
  | "contractor"
  | "third_party";

const VALID_BUSINESS_ROLES: ReadonlySet<CcpaBusinessRole> =
  new Set<CcpaBusinessRole>([
    "business",
    "service_provider",
    "contractor",
    "third_party",
  ]);

/** CCPA §1798.140(d) threshold bases. */
export type CcpaThresholdBasis =
  | "annual_revenue_25m"
  | "consumer_records_100k"
  | "revenue_share_50_pct_from_selling_pi"
  | "not_subject"
  | "other";

const VALID_THRESHOLD_BASES: ReadonlySet<CcpaThresholdBasis> =
  new Set<CcpaThresholdBasis>([
    "annual_revenue_25m",
    "consumer_records_100k",
    "revenue_share_50_pct_from_selling_pi",
    "not_subject",
    "other",
  ]);

/** CCPA §1798.140(v)(1) personal-information categories. */
export type CcpaPiCategory =
  | "identifiers"
  | "personal_records"
  | "protected_classifications"
  | "commercial_information"
  | "biometric_information"
  | "internet_activity"
  | "geolocation_data"
  | "sensory_data"
  | "professional_employment_information"
  | "education_information"
  | "inferences"
  | "no_pi_processed"
  | "other";

const VALID_PI_CATEGORIES: ReadonlySet<CcpaPiCategory> =
  new Set<CcpaPiCategory>([
    "identifiers",
    "personal_records",
    "protected_classifications",
    "commercial_information",
    "biometric_information",
    "internet_activity",
    "geolocation_data",
    "sensory_data",
    "professional_employment_information",
    "education_information",
    "inferences",
    "no_pi_processed",
    "other",
  ]);

/**
 * CCPA context for a capsule workload.
 *
 * Declares context for tagging this workload under the California
 * Consumer Privacy Act (Cal. Civ. Code §§1798.100-1798.199.100). Does
 * NOT certify, attest to satisfaction of, or claim compliance with
 * the CCPA. Threshold-applicability determination, consumer-rights
 * request handling, and enforcement matters remain the customer's
 * regulatory counsel's responsibility.
 */
export interface CcpaContextOptions {
  /** Organization that determines purpose/means of processing CA-resident PI. Required. */
  businessOrganization: string;
  /** CCPA-aligned role. Required. */
  businessRole: CcpaBusinessRole;
  /** §1798.140(d) threshold basis. Required. */
  thresholdBasis: CcpaThresholdBasis;
  /** Whether the workload processes CA-resident PI. Required. */
  consumerDataInScope: boolean;
  /** Categories of CA-resident PI processed (comma-joined). */
  piCategories?: CcpaPiCategory[];
  /** Purpose of processing. */
  purposeOfProcessing?: string;
  /** Customer-side reference to §1798.100(b) privacy notice. */
  privacyNoticeReference?: string;
  /** Customer-side reference to consumer-rights request workflow. */
  consumerRequestWorkflowReference?: string;
  /** Customer-side reference to §1798.140(ag)/(j) written contract. */
  serviceProviderContractReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class CcpaContext {
  readonly businessOrganization: string;
  readonly businessRole: CcpaBusinessRole;
  readonly thresholdBasis: CcpaThresholdBasis;
  readonly consumerDataInScope: boolean;
  readonly piCategories: ReadonlyArray<CcpaPiCategory>;
  readonly purposeOfProcessing?: string;
  readonly privacyNoticeReference?: string;
  readonly consumerRequestWorkflowReference?: string;
  readonly serviceProviderContractReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "CCPA";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get thresholdBasisSet(): CcpaThresholdBasis[] {
    return [...VALID_THRESHOLD_BASES].sort();
  }

  get piCategorySet(): CcpaPiCategory[] {
    return [...VALID_PI_CATEGORIES].sort();
  }

  constructor(opts: CcpaContextOptions) {
    if (!opts.businessOrganization) {
      throw new Error(
        "businessOrganization is required and must be non-empty",
      );
    }
    if (!opts.businessRole) {
      throw new Error("businessRole is required and must be non-empty");
    }
    if (!VALID_BUSINESS_ROLES.has(opts.businessRole)) {
      throw new Error(
        `businessRole=${JSON.stringify(opts.businessRole)} not in ` +
          `${JSON.stringify(Array.from(VALID_BUSINESS_ROLES))} ` +
          `(CCPA §1798.140 actor roles)`,
      );
    }
    if (!opts.thresholdBasis) {
      throw new Error("thresholdBasis is required and must be non-empty");
    }
    if (!VALID_THRESHOLD_BASES.has(opts.thresholdBasis)) {
      throw new Error(
        `thresholdBasis=${JSON.stringify(opts.thresholdBasis)} not in ` +
          `${JSON.stringify(Array.from(VALID_THRESHOLD_BASES))} ` +
          `(CCPA §1798.140(d) threshold bases)`,
      );
    }
    if (typeof opts.consumerDataInScope !== "boolean") {
      throw new Error(
        "consumerDataInScope must be a bool (got " +
          `${typeof opts.consumerDataInScope}); pass true or false, ` +
          `not the string 'true'`,
      );
    }

    const piCategories = opts.piCategories ?? [];
    if (piCategories.length > 0) {
      piCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `piCategories[${idx}] must be a string (got ${typeof item})`,
          );
        }
        if (!VALID_PI_CATEGORIES.has(item)) {
          throw new Error(
            `piCategories[${idx}]=${JSON.stringify(item)} not in the ` +
              `canonical CCPA §1798.140(v)(1) categories ` +
              `(introspect via CcpaContext.piCategorySet)`,
          );
        }
      });
      validateNoCommaInList("piCategories", [...piCategories]);
    }

    validateValueLength(
      "businessOrganization",
      opts.businessOrganization,
    );
    validateValueLength("businessRole", opts.businessRole);
    validateValueLength("thresholdBasis", opts.thresholdBasis);
    if (piCategories.length > 0) {
      validateValueLength("piCategories(joined)", piCategories.join(","));
    }
    if (opts.purposeOfProcessing !== undefined) {
      validateValueLength("purposeOfProcessing", opts.purposeOfProcessing);
    }
    if (opts.privacyNoticeReference !== undefined) {
      validateValueLength(
        "privacyNoticeReference",
        opts.privacyNoticeReference,
      );
    }
    if (opts.consumerRequestWorkflowReference !== undefined) {
      validateValueLength(
        "consumerRequestWorkflowReference",
        opts.consumerRequestWorkflowReference,
      );
    }
    if (opts.serviceProviderContractReference !== undefined) {
      validateValueLength(
        "serviceProviderContractReference",
        opts.serviceProviderContractReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.businessOrganization = opts.businessOrganization;
    this.businessRole = opts.businessRole;
    this.thresholdBasis = opts.thresholdBasis;
    this.consumerDataInScope = opts.consumerDataInScope;
    this.piCategories = Object.freeze([...piCategories]);
    this.purposeOfProcessing = opts.purposeOfProcessing;
    this.privacyNoticeReference = opts.privacyNoticeReference;
    this.consumerRequestWorkflowReference =
      opts.consumerRequestWorkflowReference;
    this.serviceProviderContractReference =
      opts.serviceProviderContractReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      business_organization: this.businessOrganization,
      business_role: this.businessRole,
      threshold_basis: this.thresholdBasis,
      consumer_data_in_scope: this.consumerDataInScope ? "true" : "false",
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.piCategories.length > 0) {
      meta.pi_categories = this.piCategories.join(",");
    }
    if (this.purposeOfProcessing !== undefined) {
      meta.purpose_of_processing = this.purposeOfProcessing;
    }
    if (this.privacyNoticeReference !== undefined) {
      meta.privacy_notice_reference = this.privacyNoticeReference;
    }
    if (this.consumerRequestWorkflowReference !== undefined) {
      meta.consumer_request_workflow_reference =
        this.consumerRequestWorkflowReference;
    }
    if (this.serviceProviderContractReference !== undefined) {
      meta.service_provider_contract_reference =
        this.serviceProviderContractReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
