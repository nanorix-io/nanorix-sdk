/** HITRUST CSF v11 context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "v11";

/** HITRUST assessment tier per HITRUST Alliance. */
export type HitrustAssessmentType = "e1" | "i1" | "r2";

const VALID_ASSESSMENT_TYPES: ReadonlySet<HitrustAssessmentType> =
  new Set<HitrustAssessmentType>(["e1", "i1", "r2"]);

/** 19 HITRUST CSF v11 control domains in canonical order. */
const HITRUST_DOMAINS: readonly string[] = Object.freeze([
  "01_information_security_management_program",
  "02_endpoint_protection",
  "03_portable_media_security",
  "04_mobile_device_security",
  "05_wireless_security",
  "06_configuration_management",
  "07_vulnerability_management",
  "08_network_protection",
  "09_password_management",
  "10_access_control",
  "11_audit_logging_and_monitoring",
  "12_education_training_and_awareness",
  "13_third_party_assurance",
  "14_incident_management",
  "15_business_continuity_and_disaster_recovery",
  "16_risk_management",
  "17_physical_and_environmental_security",
  "18_data_protection_and_privacy",
  "19_transmission_protection",
]);
const HITRUST_DOMAIN_SET = new Set<string>(HITRUST_DOMAINS);

/**
 * HITRUST CSF v11 context for a capsule workload.
 *
 * Declares context for tagging this workload under HITRUST CSF v11.
 * Does NOT certify, attest to satisfaction of, or claim compliance
 * with HITRUST CSF v11. Compliance determination remains the
 * customer's HITRUST-authorized external assessor's responsibility.
 */
export interface HitrustContextOptions {
  /** The HITRUST-assessed entity owning the data. Required. */
  coveredEntity: string;
  /** Assessment tier — `'e1'`, `'i1'`, or `'r2'`. Required. */
  assessmentType: HitrustAssessmentType;
  /** Data classification (e.g., `'PHI'`, `'PHI-radiology'`, `'tenant_data'`). Required. */
  dataClassification: string;
  /** Subset of the 19 canonical control domains in scope. */
  inScopeDomains?: string[];
  /** HITRUST-authorized assessor (e.g., `'Coalfire'`). */
  assessor?: string;
  /** Optional period tag (e.g., `'2026-Q2'`). */
  assessmentPeriod?: string;
  /** Customer-side certificate or report reference. */
  certReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class HitrustContext {
  readonly coveredEntity: string;
  readonly assessmentType: HitrustAssessmentType;
  readonly dataClassification: string;
  readonly inScopeDomains: ReadonlyArray<string>;
  readonly assessor?: string;
  readonly assessmentPeriod?: string;
  readonly certReference?: string;
  readonly workforceMemberRole?: string;

  /** Canonical framework identifier. */
  get frameworkId(): string {
    return "HITRUST_CSF_v11";
  }

  /** The 19 canonical HITRUST CSF v11 control domains (defensive copy). */
  get domains(): string[] {
    return [...HITRUST_DOMAINS];
  }

  constructor(opts: HitrustContextOptions) {
    if (!opts.coveredEntity) {
      throw new Error("coveredEntity is required and must be non-empty");
    }
    if (!opts.assessmentType) {
      throw new Error(
        "assessmentType is required and must be non-empty " +
          "(pick one of: e1, i1, r2)",
      );
    }
    if (!VALID_ASSESSMENT_TYPES.has(opts.assessmentType)) {
      throw new Error(
        `assessmentType=${JSON.stringify(opts.assessmentType)} not in ` +
          `${JSON.stringify(Array.from(VALID_ASSESSMENT_TYPES))} ` +
          `(HITRUST Alliance assessment tiers)`,
      );
    }
    if (!opts.dataClassification) {
      throw new Error("dataClassification must be non-empty");
    }

    const inScopeDomains = opts.inScopeDomains ?? [];
    if (inScopeDomains.length > 0) {
      inScopeDomains.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `inScopeDomains[${idx}] must be a string (got ${typeof item})`,
          );
        }
        if (!HITRUST_DOMAIN_SET.has(item)) {
          throw new Error(
            `inScopeDomains[${idx}]=${JSON.stringify(item)} not in the ` +
              `19 canonical HITRUST CSF v11 control domains ` +
              `(introspect via HitrustContext.domains)`,
          );
        }
      });
      validateNoCommaInList("inScopeDomains", inScopeDomains);
    }

    validateValueLength("coveredEntity", opts.coveredEntity);
    validateValueLength("assessmentType", opts.assessmentType);
    validateValueLength("dataClassification", opts.dataClassification);
    if (inScopeDomains.length > 0) {
      validateValueLength(
        "inScopeDomains(joined)",
        inScopeDomains.join(","),
      );
    }
    if (opts.assessor !== undefined) {
      validateValueLength("assessor", opts.assessor);
    }
    if (opts.assessmentPeriod !== undefined) {
      validateValueLength("assessmentPeriod", opts.assessmentPeriod);
    }
    if (opts.certReference !== undefined) {
      validateValueLength("certReference", opts.certReference);
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.coveredEntity = opts.coveredEntity;
    this.assessmentType = opts.assessmentType;
    this.dataClassification = opts.dataClassification;
    this.inScopeDomains = Object.freeze([...inScopeDomains]);
    this.assessor = opts.assessor;
    this.assessmentPeriod = opts.assessmentPeriod;
    this.certReference = opts.certReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      covered_entity: this.coveredEntity,
      assessment_type: this.assessmentType,
      data_classification: this.dataClassification,
    };
    if (this.inScopeDomains.length > 0) {
      meta.in_scope_domains = this.inScopeDomains.join(",");
    }
    if (this.assessor !== undefined) meta.assessor = this.assessor;
    if (this.assessmentPeriod !== undefined) {
      meta.assessment_period = this.assessmentPeriod;
    }
    if (this.certReference !== undefined) {
      meta.cert_reference = this.certReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
