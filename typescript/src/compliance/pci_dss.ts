/** PCI DSS v4.0 context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "4.0";

/** PCI SSC merchant + service-provider levels. */
export type PciDssMerchantLevel =
  | "level_1"
  | "level_2"
  | "level_3"
  | "level_4"
  | "service_provider_level_1"
  | "service_provider_level_2";

const VALID_MERCHANT_LEVELS: ReadonlySet<PciDssMerchantLevel> =
  new Set<PciDssMerchantLevel>([
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "service_provider_level_1",
    "service_provider_level_2",
  ]);

/** PCI DSS v4.0 assessment types — SAQ variants plus ROC. */
export type PciDssAssessmentType =
  | "saq_a"
  | "saq_a_ep"
  | "saq_b"
  | "saq_b_ip"
  | "saq_c"
  | "saq_c_vt"
  | "saq_d_merchant"
  | "saq_d_service_provider"
  | "saq_p2pe"
  | "roc";

const VALID_ASSESSMENT_TYPES: ReadonlySet<PciDssAssessmentType> =
  new Set<PciDssAssessmentType>([
    "saq_a",
    "saq_a_ep",
    "saq_b",
    "saq_b_ip",
    "saq_c",
    "saq_c_vt",
    "saq_d_merchant",
    "saq_d_service_provider",
    "saq_p2pe",
    "roc",
  ]);

/** 12 PCI DSS v4.0 top-level requirements in canonical order. */
const PCI_DSS_REQUIREMENTS: readonly string[] = Object.freeze([
  "01_install_maintain_network_security_controls",
  "02_apply_secure_configurations",
  "03_protect_stored_account_data",
  "04_protect_cardholder_data_with_strong_cryptography_during_transmission",
  "05_protect_systems_from_malicious_software",
  "06_develop_maintain_secure_systems_software",
  "07_restrict_access_by_business_need_to_know",
  "08_identify_users_authenticate_access",
  "09_restrict_physical_access",
  "10_log_monitor_access",
  "11_test_security_of_systems_networks",
  "12_support_information_security_with_organizational_policies",
]);
const PCI_DSS_REQUIREMENT_SET = new Set<string>(PCI_DSS_REQUIREMENTS);

/**
 * PCI DSS v4.0 context for a capsule workload.
 *
 * Declares context for tagging this workload under PCI DSS v4.0.
 * Does NOT certify, attest to satisfaction of, or claim compliance
 * with PCI DSS. CDE scoping, SAQ selection, and ROC determinations
 * remain the customer's QSA/ISA's responsibility.
 */
export interface PciDssContextOptions {
  /** PCI-assessed entity owning the cardholder data context. Required. */
  merchantOrServiceProvider: string;
  /** PCI SSC merchant or service-provider level. Required. */
  merchantLevel: PciDssMerchantLevel;
  /** PCI DSS v4.0 assessment type. Required. */
  assessmentType: PciDssAssessmentType;
  /** Whether this capsule is in the customer's CDE scope. Required. */
  cardholderDataInScope: boolean;
  /** Subset of the 12 top-level requirements in scope. */
  inScopeRequirements?: string[];
  /** PCI-authorized QSA firm. */
  qsaFirm?: string;
  /** Optional period tag. */
  assessmentPeriod?: string;
  /** Customer-side Attestation of Compliance reference. */
  aocReference?: string;
  /** Customer's declared CDE segmentation method. */
  cdeSegmentationMethod?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class PciDssContext {
  readonly merchantOrServiceProvider: string;
  readonly merchantLevel: PciDssMerchantLevel;
  readonly assessmentType: PciDssAssessmentType;
  readonly cardholderDataInScope: boolean;
  readonly inScopeRequirements: ReadonlyArray<string>;
  readonly qsaFirm?: string;
  readonly assessmentPeriod?: string;
  readonly aocReference?: string;
  readonly cdeSegmentationMethod?: string;
  readonly workforceMemberRole?: string;

  /** Canonical framework identifier. */
  get frameworkId(): string {
    return "PCI_DSS";
  }

  /** The 12 canonical PCI DSS v4.0 top-level requirements (defensive copy). */
  get requirements(): string[] {
    return [...PCI_DSS_REQUIREMENTS];
  }

  constructor(opts: PciDssContextOptions) {
    if (!opts.merchantOrServiceProvider) {
      throw new Error(
        "merchantOrServiceProvider is required and must be non-empty",
      );
    }
    if (!opts.merchantLevel) {
      throw new Error("merchantLevel is required and must be non-empty");
    }
    if (!VALID_MERCHANT_LEVELS.has(opts.merchantLevel)) {
      throw new Error(
        `merchantLevel=${JSON.stringify(opts.merchantLevel)} not in ` +
          `${JSON.stringify(Array.from(VALID_MERCHANT_LEVELS))} ` +
          `(PCI SSC merchant + service-provider levels)`,
      );
    }
    if (!opts.assessmentType) {
      throw new Error("assessmentType is required and must be non-empty");
    }
    if (!VALID_ASSESSMENT_TYPES.has(opts.assessmentType)) {
      throw new Error(
        `assessmentType=${JSON.stringify(opts.assessmentType)} not in ` +
          `${JSON.stringify(Array.from(VALID_ASSESSMENT_TYPES))} ` +
          `(PCI DSS v4.0 SAQ + ROC types)`,
      );
    }
    if (typeof opts.cardholderDataInScope !== "boolean") {
      throw new Error(
        "cardholderDataInScope must be a bool (got " +
          `${typeof opts.cardholderDataInScope}); pass true or false, ` +
          `not the string 'true'`,
      );
    }

    const inScopeRequirements = opts.inScopeRequirements ?? [];
    if (inScopeRequirements.length > 0) {
      inScopeRequirements.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `inScopeRequirements[${idx}] must be a string ` +
              `(got ${typeof item})`,
          );
        }
        if (!PCI_DSS_REQUIREMENT_SET.has(item)) {
          throw new Error(
            `inScopeRequirements[${idx}]=${JSON.stringify(item)} not in ` +
              `the 12 canonical PCI DSS v4.0 requirements ` +
              `(introspect via PciDssContext.requirements)`,
          );
        }
      });
      validateNoCommaInList("inScopeRequirements", inScopeRequirements);
    }

    validateValueLength(
      "merchantOrServiceProvider",
      opts.merchantOrServiceProvider,
    );
    validateValueLength("merchantLevel", opts.merchantLevel);
    validateValueLength("assessmentType", opts.assessmentType);
    if (inScopeRequirements.length > 0) {
      validateValueLength(
        "inScopeRequirements(joined)",
        inScopeRequirements.join(","),
      );
    }
    if (opts.qsaFirm !== undefined) {
      validateValueLength("qsaFirm", opts.qsaFirm);
    }
    if (opts.assessmentPeriod !== undefined) {
      validateValueLength("assessmentPeriod", opts.assessmentPeriod);
    }
    if (opts.aocReference !== undefined) {
      validateValueLength("aocReference", opts.aocReference);
    }
    if (opts.cdeSegmentationMethod !== undefined) {
      validateValueLength(
        "cdeSegmentationMethod",
        opts.cdeSegmentationMethod,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.merchantOrServiceProvider = opts.merchantOrServiceProvider;
    this.merchantLevel = opts.merchantLevel;
    this.assessmentType = opts.assessmentType;
    this.cardholderDataInScope = opts.cardholderDataInScope;
    this.inScopeRequirements = Object.freeze([...inScopeRequirements]);
    this.qsaFirm = opts.qsaFirm;
    this.assessmentPeriod = opts.assessmentPeriod;
    this.aocReference = opts.aocReference;
    this.cdeSegmentationMethod = opts.cdeSegmentationMethod;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      merchant_or_service_provider: this.merchantOrServiceProvider,
      merchant_level: this.merchantLevel,
      assessment_type: this.assessmentType,
      cardholder_data_in_scope: this.cardholderDataInScope ? "true" : "false",
    };
    if (this.inScopeRequirements.length > 0) {
      meta.in_scope_requirements = this.inScopeRequirements.join(",");
    }
    if (this.qsaFirm !== undefined) meta.qsa_firm = this.qsaFirm;
    if (this.assessmentPeriod !== undefined) {
      meta.assessment_period = this.assessmentPeriod;
    }
    if (this.aocReference !== undefined) {
      meta.aoc_reference = this.aocReference;
    }
    if (this.cdeSegmentationMethod !== undefined) {
      meta.cde_segmentation_method = this.cdeSegmentationMethod;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
