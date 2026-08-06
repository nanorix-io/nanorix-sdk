/** CPRA (California Privacy Rights Act) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2023-operative";
const JURISDICTION_STATE = "CA";

/** CPRA-aligned actor roles (mirrors CCPA §1798.140). */
export type CpraBusinessRole =
  | "business"
  | "service_provider"
  | "contractor"
  | "third_party";

const VALID_BUSINESS_ROLES: ReadonlySet<CpraBusinessRole> =
  new Set<CpraBusinessRole>([
    "business",
    "service_provider",
    "contractor",
    "third_party",
  ]);

/** §1798.140(ae) sensitive personal information categories. */
export type CpraSpiCategory =
  | "ssn"
  | "drivers_license"
  | "state_id"
  | "passport_number"
  | "financial_account"
  | "debit_credit_card_with_credentials"
  | "precise_geolocation"
  | "racial_or_ethnic_origin"
  | "religious_beliefs"
  | "philosophical_beliefs"
  | "union_membership"
  | "mail_email_text_content"
  | "genetic_data"
  | "biometric_for_identification"
  | "health"
  | "sex_life_or_sexual_orientation"
  | "no_spi_processed"
  | "other";

const VALID_SPI_CATEGORIES: ReadonlySet<CpraSpiCategory> =
  new Set<CpraSpiCategory>([
    "ssn",
    "drivers_license",
    "state_id",
    "passport_number",
    "financial_account",
    "debit_credit_card_with_credentials",
    "precise_geolocation",
    "racial_or_ethnic_origin",
    "religious_beliefs",
    "philosophical_beliefs",
    "union_membership",
    "mail_email_text_content",
    "genetic_data",
    "biometric_for_identification",
    "health",
    "sex_life_or_sexual_orientation",
    "no_spi_processed",
    "other",
  ]);

/** ADMT-designation taxonomy per CPPA proposed regulations under §1798.185(a)(16). */
export type CpraAdmtDesignation =
  | "uses_admt"
  | "uses_admt_consequential_only"
  | "does_not_use_admt"
  | "admt_determination_pending";

const VALID_ADMT_DESIGNATIONS: ReadonlySet<CpraAdmtDesignation> =
  new Set<CpraAdmtDesignation>([
    "uses_admt",
    "uses_admt_consequential_only",
    "does_not_use_admt",
    "admt_determination_pending",
  ]);

/**
 * CPRA context for a capsule workload.
 *
 * Declares context for tagging this workload under the California
 * Privacy Rights Act amendments to the CCPA. Does NOT certify,
 * attest to satisfaction of, or claim compliance with the CPRA.
 * Threshold-applicability determination, SPI categorization,
 * correction-request handling, ADMT opt-out implementation, and
 * enforcement matters remain the customer's regulatory counsel's
 * responsibility.
 */
export interface CpraContextOptions {
  /** Organization that determines purpose/means of processing CA-resident PI. Required. */
  businessOrganization: string;
  /** CPRA-aligned role. Required. */
  businessRole: CpraBusinessRole;
  /** Whether the workload processes CA-resident SPI per §1798.140(ae). Required. */
  sensitivePiInScope: boolean;
  /** ADMT designation per §1798.185(a)(16). Required. */
  admtDesignation: CpraAdmtDesignation;
  /** Categories of CA-resident SPI (comma-joined). */
  spiCategories?: CpraSpiCategory[];
  /** Storage-limitation tag per §1798.100(a)(3). */
  retentionPeriod?: string;
  /** Purpose of processing per §1798.100(c). */
  purposeOfProcessing?: string;
  /** Privacy notice reference. */
  privacyNoticeReference?: string;
  /** §1798.106 right-to-correct workflow reference. */
  rightToCorrectWorkflowReference?: string;
  /** §1798.121 right-to-limit-use-of-SPI workflow reference. */
  rightToLimitSpiWorkflowReference?: string;
  /** §1798.185(a)(16) ADMT opt-out workflow reference. */
  admtOptOutWorkflowReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class CpraContext {
  readonly businessOrganization: string;
  readonly businessRole: CpraBusinessRole;
  readonly sensitivePiInScope: boolean;
  readonly admtDesignation: CpraAdmtDesignation;
  readonly spiCategories: ReadonlyArray<CpraSpiCategory>;
  readonly retentionPeriod?: string;
  readonly purposeOfProcessing?: string;
  readonly privacyNoticeReference?: string;
  readonly rightToCorrectWorkflowReference?: string;
  readonly rightToLimitSpiWorkflowReference?: string;
  readonly admtOptOutWorkflowReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "CPRA";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  get spiCategorySet(): CpraSpiCategory[] {
    return [...VALID_SPI_CATEGORIES].sort();
  }

  get admtDesignationSet(): CpraAdmtDesignation[] {
    return [...VALID_ADMT_DESIGNATIONS].sort();
  }

  constructor(opts: CpraContextOptions) {
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
          `(CPRA §1798.140 actor roles)`,
      );
    }
    if (typeof opts.sensitivePiInScope !== "boolean") {
      throw new Error(
        "sensitivePiInScope must be a bool (got " +
          `${typeof opts.sensitivePiInScope}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (!opts.admtDesignation) {
      throw new Error("admtDesignation is required and must be non-empty");
    }
    if (!VALID_ADMT_DESIGNATIONS.has(opts.admtDesignation)) {
      throw new Error(
        `admtDesignation=${JSON.stringify(opts.admtDesignation)} not in ` +
          `${JSON.stringify(Array.from(VALID_ADMT_DESIGNATIONS))} ` +
          `(CPPA ADMT-designation taxonomy)`,
      );
    }

    const spiCategories = opts.spiCategories ?? [];
    if (spiCategories.length > 0) {
      spiCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `spiCategories[${idx}] must be a string (got ${typeof item})`,
          );
        }
        if (!VALID_SPI_CATEGORIES.has(item)) {
          throw new Error(
            `spiCategories[${idx}]=${JSON.stringify(item)} not in the ` +
              `canonical CPRA §1798.140(ae) SPI categories ` +
              `(introspect via CpraContext.spiCategorySet)`,
          );
        }
      });
      validateNoCommaInList("spiCategories", [...spiCategories]);
    }

    validateValueLength("businessOrganization", opts.businessOrganization);
    validateValueLength("businessRole", opts.businessRole);
    validateValueLength("admtDesignation", opts.admtDesignation);
    if (spiCategories.length > 0) {
      validateValueLength("spiCategories(joined)", spiCategories.join(","));
    }
    if (opts.retentionPeriod !== undefined) {
      validateValueLength("retentionPeriod", opts.retentionPeriod);
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
    if (opts.rightToCorrectWorkflowReference !== undefined) {
      validateValueLength(
        "rightToCorrectWorkflowReference",
        opts.rightToCorrectWorkflowReference,
      );
    }
    if (opts.rightToLimitSpiWorkflowReference !== undefined) {
      validateValueLength(
        "rightToLimitSpiWorkflowReference",
        opts.rightToLimitSpiWorkflowReference,
      );
    }
    if (opts.admtOptOutWorkflowReference !== undefined) {
      validateValueLength(
        "admtOptOutWorkflowReference",
        opts.admtOptOutWorkflowReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.businessOrganization = opts.businessOrganization;
    this.businessRole = opts.businessRole;
    this.sensitivePiInScope = opts.sensitivePiInScope;
    this.admtDesignation = opts.admtDesignation;
    this.spiCategories = Object.freeze([...spiCategories]);
    this.retentionPeriod = opts.retentionPeriod;
    this.purposeOfProcessing = opts.purposeOfProcessing;
    this.privacyNoticeReference = opts.privacyNoticeReference;
    this.rightToCorrectWorkflowReference =
      opts.rightToCorrectWorkflowReference;
    this.rightToLimitSpiWorkflowReference =
      opts.rightToLimitSpiWorkflowReference;
    this.admtOptOutWorkflowReference = opts.admtOptOutWorkflowReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      business_organization: this.businessOrganization,
      business_role: this.businessRole,
      sensitive_pi_in_scope: this.sensitivePiInScope ? "true" : "false",
      admt_designation: this.admtDesignation,
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.spiCategories.length > 0) {
      meta.spi_categories = this.spiCategories.join(",");
    }
    if (this.retentionPeriod !== undefined) {
      meta.retention_period = this.retentionPeriod;
    }
    if (this.purposeOfProcessing !== undefined) {
      meta.purpose_of_processing = this.purposeOfProcessing;
    }
    if (this.privacyNoticeReference !== undefined) {
      meta.privacy_notice_reference = this.privacyNoticeReference;
    }
    if (this.rightToCorrectWorkflowReference !== undefined) {
      meta.right_to_correct_workflow_reference =
        this.rightToCorrectWorkflowReference;
    }
    if (this.rightToLimitSpiWorkflowReference !== undefined) {
      meta.right_to_limit_spi_workflow_reference =
        this.rightToLimitSpiWorkflowReference;
    }
    if (this.admtOptOutWorkflowReference !== undefined) {
      meta.admt_opt_out_workflow_reference = this.admtOptOutWorkflowReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
