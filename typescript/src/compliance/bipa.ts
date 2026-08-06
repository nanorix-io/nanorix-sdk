/** BIPA (Illinois Biometric Information Privacy Act, 740 ILCS 14) context. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2008-operative";
const JURISDICTION_STATE = "IL";

/** §14/10 biometric identifier categories. */
export type BipaBiometricIdentifierType =
  | "retina_iris_scan"
  | "fingerprint"
  | "voiceprint"
  | "hand_geometry_scan"
  | "face_geometry_scan"
  | "biometric_information_derived"
  | "no_biometrics_processed"
  | "other";

const VALID_BIOMETRIC_IDENTIFIER_TYPES: ReadonlySet<BipaBiometricIdentifierType> =
  new Set<BipaBiometricIdentifierType>([
    "retina_iris_scan",
    "fingerprint",
    "voiceprint",
    "hand_geometry_scan",
    "face_geometry_scan",
    "biometric_information_derived",
    "no_biometrics_processed",
    "other",
  ]);

/** §14/15(b) consent-basis taxonomy. */
export type BipaConsentBasis =
  | "written_consent_obtained"
  | "healthcare_exemption_claimed"
  | "court_ordered"
  | "no_consent_required"
  | "consent_pending"
  | "other";

const VALID_CONSENT_BASES: ReadonlySet<BipaConsentBasis> =
  new Set<BipaConsentBasis>([
    "written_consent_obtained",
    "healthcare_exemption_claimed",
    "court_ordered",
    "no_consent_required",
    "consent_pending",
    "other",
  ]);

/**
 * BIPA context for a capsule workload.
 *
 * Declares context for tagging this workload under the Illinois
 * Biometric Information Privacy Act (740 ILCS 14). Does NOT certify,
 * attest to satisfaction of, or claim compliance with BIPA. Consent
 * acquisition, retention-policy authoring + publication, destruction-
 * cadence enforcement, and private-right-of-action litigation defense
 * remain the customer's regulatory counsel's responsibility. The
 * Illinois Attorney General has parallel enforcement authority; the
 * dominant enforcement mechanism is private litigation (740 ILCS
 * 14/20).
 */
export interface BipaContextOptions {
  /** The private entity collecting biometric identifiers/information. Required. */
  collectingEntity: string;
  /** Whether the workload processes Illinois-resident biometric data. Required. */
  biometricDataInScope: boolean;
  /** Whether §14/15(b) written informed consent has been obtained. Required. */
  consentObtained: boolean;
  /** Whether §14/15(a) publicly available retention/destruction policy is documented. Required. */
  retentionPolicyDocumented: boolean;
  /** §14/10 biometric identifier category. Required. */
  biometricIdentifierType: BipaBiometricIdentifierType;
  /** §14/15(b) consent-basis taxonomy. Optional. */
  consentBasis?: BipaConsentBasis;
  /** Categories of affected individuals (stored comma-joined). */
  affectedIndividualCategories?: string[];
  /** Customer-side reference to written-consent records. */
  consentRecordReference?: string;
  /** Customer-side reference to public retention policy. */
  retentionPolicyReference?: string;
  /** Customer-side reference to destruction cadence record. */
  destructionCadenceReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class BipaContext {
  readonly collectingEntity: string;
  readonly biometricDataInScope: boolean;
  readonly consentObtained: boolean;
  readonly retentionPolicyDocumented: boolean;
  readonly biometricIdentifierType: BipaBiometricIdentifierType;
  readonly consentBasis?: BipaConsentBasis;
  readonly affectedIndividualCategories: ReadonlyArray<string>;
  readonly consentRecordReference?: string;
  readonly retentionPolicyReference?: string;
  readonly destructionCadenceReference?: string;
  readonly workforceMemberRole?: string;

  get frameworkId(): string {
    return "BIPA";
  }

  get jurisdictionState(): string {
    return JURISDICTION_STATE;
  }

  /** Canonical §14/10 biometric-identifier-type taxonomy (defensive copy, sorted). */
  get biometricIdentifierTypeSet(): BipaBiometricIdentifierType[] {
    return [...VALID_BIOMETRIC_IDENTIFIER_TYPES].sort();
  }

  /** Canonical §14/15(b) consent-basis taxonomy (defensive copy, sorted). */
  get consentBasisSet(): BipaConsentBasis[] {
    return [...VALID_CONSENT_BASES].sort();
  }

  constructor(opts: BipaContextOptions) {
    if (!opts.collectingEntity) {
      throw new Error("collectingEntity is required and must be non-empty");
    }
    if (typeof opts.biometricDataInScope !== "boolean") {
      throw new Error(
        "biometricDataInScope must be a bool (got " +
          `${typeof opts.biometricDataInScope}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.consentObtained !== "boolean") {
      throw new Error(
        "consentObtained must be a bool (got " +
          `${typeof opts.consentObtained}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (typeof opts.retentionPolicyDocumented !== "boolean") {
      throw new Error(
        "retentionPolicyDocumented must be a bool (got " +
          `${typeof opts.retentionPolicyDocumented}); pass true or false, ` +
          `not the string 'true'`,
      );
    }
    if (!opts.biometricIdentifierType) {
      throw new Error(
        "biometricIdentifierType is required and must be non-empty",
      );
    }
    if (!VALID_BIOMETRIC_IDENTIFIER_TYPES.has(opts.biometricIdentifierType)) {
      throw new Error(
        `biometricIdentifierType=${JSON.stringify(opts.biometricIdentifierType)} ` +
          `not in ${JSON.stringify(Array.from(VALID_BIOMETRIC_IDENTIFIER_TYPES))} ` +
          `(§14/10 biometric-identifier taxonomy)`,
      );
    }
    if (opts.consentBasis !== undefined) {
      if (!opts.consentBasis) {
        throw new Error("consentBasis if provided must be non-empty");
      }
      if (!VALID_CONSENT_BASES.has(opts.consentBasis)) {
        throw new Error(
          `consentBasis=${JSON.stringify(opts.consentBasis)} not in ` +
            `${JSON.stringify(Array.from(VALID_CONSENT_BASES))} ` +
            `(§14/15(b) consent-basis taxonomy)`,
        );
      }
    }

    const affectedIndividualCategories = opts.affectedIndividualCategories ?? [];
    if (affectedIndividualCategories.length > 0) {
      affectedIndividualCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `affectedIndividualCategories[${idx}] must be a string ` +
              `(got ${typeof item})`,
          );
        }
        if (!item) {
          throw new Error(
            `affectedIndividualCategories[${idx}] must be non-empty`,
          );
        }
      });
      validateNoCommaInList(
        "affectedIndividualCategories",
        affectedIndividualCategories,
      );
    }

    validateValueLength("collectingEntity", opts.collectingEntity);
    validateValueLength(
      "biometricIdentifierType",
      opts.biometricIdentifierType,
    );
    if (opts.consentBasis !== undefined) {
      validateValueLength("consentBasis", opts.consentBasis);
    }
    if (affectedIndividualCategories.length > 0) {
      validateValueLength(
        "affectedIndividualCategories(joined)",
        affectedIndividualCategories.join(","),
      );
    }
    if (opts.consentRecordReference !== undefined) {
      validateValueLength(
        "consentRecordReference",
        opts.consentRecordReference,
      );
    }
    if (opts.retentionPolicyReference !== undefined) {
      validateValueLength(
        "retentionPolicyReference",
        opts.retentionPolicyReference,
      );
    }
    if (opts.destructionCadenceReference !== undefined) {
      validateValueLength(
        "destructionCadenceReference",
        opts.destructionCadenceReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.collectingEntity = opts.collectingEntity;
    this.biometricDataInScope = opts.biometricDataInScope;
    this.consentObtained = opts.consentObtained;
    this.retentionPolicyDocumented = opts.retentionPolicyDocumented;
    this.biometricIdentifierType = opts.biometricIdentifierType;
    this.consentBasis = opts.consentBasis;
    this.affectedIndividualCategories = Object.freeze([
      ...affectedIndividualCategories,
    ]);
    this.consentRecordReference = opts.consentRecordReference;
    this.retentionPolicyReference = opts.retentionPolicyReference;
    this.destructionCadenceReference = opts.destructionCadenceReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      collecting_entity: this.collectingEntity,
      biometric_data_in_scope: this.biometricDataInScope ? "true" : "false",
      consent_obtained: this.consentObtained ? "true" : "false",
      retention_policy_documented: this.retentionPolicyDocumented
        ? "true"
        : "false",
      biometric_identifier_type: this.biometricIdentifierType,
      jurisdiction_state: JURISDICTION_STATE,
    };
    if (this.consentBasis !== undefined) {
      meta.consent_basis = this.consentBasis;
    }
    if (this.affectedIndividualCategories.length > 0) {
      meta.affected_individual_categories =
        this.affectedIndividualCategories.join(",");
    }
    if (this.consentRecordReference !== undefined) {
      meta.consent_record_reference = this.consentRecordReference;
    }
    if (this.retentionPolicyReference !== undefined) {
      meta.retention_policy_reference = this.retentionPolicyReference;
    }
    if (this.destructionCadenceReference !== undefined) {
      meta.destruction_cadence_reference = this.destructionCadenceReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
