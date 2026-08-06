/** HIPAA Privacy + Security Rule context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2013-Omnibus";

/**
 * HIPAA Privacy + Security Rule context for a capsule workload.
 *
 * Declares context for tagging this workload under HIPAA. Does NOT
 * certify, attest to satisfaction of, or claim compliance with HIPAA.
 * Compliance determination remains the customer's auditor's
 * responsibility.
 *
 * Required fields encode information needed to identify the workload
 * under HIPAA's covered-entity / business-associate framework. Optional
 * fields encode additional context auditors typically request.
 */
export interface HipaaContextOptions {
  /** The HIPAA-covered entity owning the data. Required. */
  coveredEntity: string;

  /**
   * Why this workload needs PHI access. Required for the minimum-
   * necessary standard at 45 CFR §164.502(b).
   */
  minimumNecessaryPurpose: string;

  /**
   * Default `'PHI'`; customer can narrow (e.g., `'PHI-radiology'`,
   * `'PHI-imaging'`).
   */
  dataClassification?: string;

  /**
   * Business-associate identifier when applicable; omitted when the
   * covered entity processes its own data.
   */
  businessAssociate?: string;

  /**
   * Customer-side BAA contract reference. Helper does NOT verify a
   * BAA exists — pure context tag.
   */
  baaReference?: string;

  /**
   * Who initiated this workload (e.g., `'attending_radiologist'`).
   * Useful for the AuditProof audit trail.
   */
  workforceMemberRole?: string;
}

export class HipaaContext {
  readonly coveredEntity: string;
  readonly minimumNecessaryPurpose: string;
  readonly dataClassification: string;
  readonly businessAssociate?: string;
  readonly baaReference?: string;
  readonly workforceMemberRole?: string;

  constructor(opts: HipaaContextOptions) {
    if (!opts.coveredEntity) {
      throw new Error("coveredEntity is required and must be non-empty");
    }
    if (!opts.minimumNecessaryPurpose) {
      throw new Error(
        "minimumNecessaryPurpose is required and must be non-empty",
      );
    }
    const dataClassification = opts.dataClassification ?? "PHI";
    if (!dataClassification) {
      throw new Error("dataClassification must be non-empty");
    }

    validateValueLength("coveredEntity", opts.coveredEntity);
    validateValueLength("minimumNecessaryPurpose", opts.minimumNecessaryPurpose);
    validateValueLength("dataClassification", dataClassification);
    if (opts.businessAssociate !== undefined) {
      validateValueLength("businessAssociate", opts.businessAssociate);
    }
    if (opts.baaReference !== undefined) {
      validateValueLength("baaReference", opts.baaReference);
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.coveredEntity = opts.coveredEntity;
    this.minimumNecessaryPurpose = opts.minimumNecessaryPurpose;
    this.dataClassification = dataClassification;
    this.businessAssociate = opts.businessAssociate;
    this.baaReference = opts.baaReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  /**
   * Convert to the metadata Record format for `capsules.create()`.
   *
   * Returns a Record keyed by the reserved keys `nanorix:framework` +
   * `nanorix:framework_version` (whitelisted server-side per ADR-017 +
   * post-G2 validation amendment) plus customer-declared fields.
   */
  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: "HIPAA",
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      covered_entity: this.coveredEntity,
      minimum_necessary_purpose: this.minimumNecessaryPurpose,
      data_classification: this.dataClassification,
    };
    if (this.businessAssociate !== undefined) {
      meta.business_associate = this.businessAssociate;
    }
    if (this.baaReference !== undefined) {
      meta.baa_reference = this.baaReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
