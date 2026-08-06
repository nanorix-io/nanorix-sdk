/** 42 CFR Part 2 (Substance Use Disorder records) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2024-Revision";

/** 42 CFR Part 2 §2.31-§2.65 disclosure-basis taxonomy. */
export type CFRPart2DisclosureBasis =
  | "patient_consent"
  | "medical_emergency"
  | "research"
  | "audit_evaluation"
  | "court_order"
  | "qso_agreement"
  | "not_disclosed";

const VALID_DISCLOSURE_BASES: ReadonlySet<CFRPart2DisclosureBasis> =
  new Set<CFRPart2DisclosureBasis>([
    "patient_consent",
    "medical_emergency",
    "research",
    "audit_evaluation",
    "court_order",
    "qso_agreement",
    "not_disclosed",
  ]);

/**
 * 42 CFR Part 2 context for a capsule workload.
 *
 * Declares context for tagging this workload under 42 CFR Part 2
 * (Confidentiality of Substance Use Disorder Patient Records). Does
 * NOT certify, attest to satisfaction of, or claim compliance with
 * 42 CFR Part 2. The customer's regulatory counsel determines Part 2
 * applicability and posture; the redisclosure prohibition (§2.32),
 * patient consent requirements (§2.31), and Qualified Service
 * Organization (QSO) obligations (§2.11) remain the customer's
 * programmatic and legal responsibility.
 */
export interface CFRPart2ContextOptions {
  /** The federally-assisted Part 2 program owning the data. Required. */
  part2Program: string;
  /** Why this workload needs Part 2-protected record access. Required. */
  minimumNecessaryPurpose: string;
  /** Default `'PHI-SUD'`. */
  dataClassification?: string;
  /** §2.31-§2.65 disclosure basis. Defaults to `'not_disclosed'`. */
  disclosureBasis?: CFRPart2DisclosureBasis;
  /** Customer-side QSO Agreement (QSOA) reference. */
  qsoAgreementReference?: string;
  /** When `disclosureBasis = 'patient_consent'`, the consent record reference. Required in that case. */
  consentReference?: string;
  /** When `disclosureBasis = 'research'`, the §2.52 research-authorization reference. Required in that case. */
  researchAuthorizationReference?: string;
  /** Who initiated this workload (e.g., `'sud_counselor'`). */
  workforceMemberRole?: string;
}

export class CFRPart2Context {
  readonly part2Program: string;
  readonly minimumNecessaryPurpose: string;
  readonly dataClassification: string;
  readonly disclosureBasis: CFRPart2DisclosureBasis;
  readonly qsoAgreementReference?: string;
  readonly consentReference?: string;
  readonly researchAuthorizationReference?: string;
  readonly workforceMemberRole?: string;

  /** Whether §2.32 redisclosure restriction applies — always `true` for any workload using this helper. */
  get redisclosureRestrictionApplies(): boolean {
    return true;
  }

  /** Canonical framework identifier. */
  get frameworkId(): string {
    return "42_CFR_Part_2";
  }

  constructor(opts: CFRPart2ContextOptions) {
    if (!opts.part2Program) {
      throw new Error("part2Program is required and must be non-empty");
    }
    if (!opts.minimumNecessaryPurpose) {
      throw new Error(
        "minimumNecessaryPurpose is required and must be non-empty",
      );
    }
    const dataClassification = opts.dataClassification ?? "PHI-SUD";
    if (!dataClassification) {
      throw new Error("dataClassification must be non-empty");
    }
    const disclosureBasis: CFRPart2DisclosureBasis =
      opts.disclosureBasis ?? "not_disclosed";
    if (!VALID_DISCLOSURE_BASES.has(disclosureBasis)) {
      throw new Error(
        `disclosureBasis=${JSON.stringify(disclosureBasis)} not in ` +
          `${JSON.stringify(Array.from(VALID_DISCLOSURE_BASES))} ` +
          `(42 CFR Part 2 §2.31-§2.65 disclosure-basis set)`,
      );
    }

    // Conditional cross-checks: paired-reference fields required by basis.
    if (
      disclosureBasis === "patient_consent" &&
      opts.consentReference === undefined
    ) {
      throw new Error(
        "disclosureBasis='patient_consent' requires consentReference to " +
          "be set (§2.31 consent-record reference; helper does NOT verify " +
          "the consent)",
      );
    }
    if (
      disclosureBasis === "research" &&
      opts.researchAuthorizationReference === undefined
    ) {
      throw new Error(
        "disclosureBasis='research' requires researchAuthorizationReference " +
          "to be set (§2.52 research-authorization reference; helper does " +
          "NOT verify the authorization)",
      );
    }

    validateValueLength("part2Program", opts.part2Program);
    validateValueLength(
      "minimumNecessaryPurpose",
      opts.minimumNecessaryPurpose,
    );
    validateValueLength("dataClassification", dataClassification);
    validateValueLength("disclosureBasis", disclosureBasis);
    if (opts.qsoAgreementReference !== undefined) {
      validateValueLength("qsoAgreementReference", opts.qsoAgreementReference);
    }
    if (opts.consentReference !== undefined) {
      validateValueLength("consentReference", opts.consentReference);
    }
    if (opts.researchAuthorizationReference !== undefined) {
      validateValueLength(
        "researchAuthorizationReference",
        opts.researchAuthorizationReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.part2Program = opts.part2Program;
    this.minimumNecessaryPurpose = opts.minimumNecessaryPurpose;
    this.dataClassification = dataClassification;
    this.disclosureBasis = disclosureBasis;
    this.qsoAgreementReference = opts.qsoAgreementReference;
    this.consentReference = opts.consentReference;
    this.researchAuthorizationReference = opts.researchAuthorizationReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      part_2_program: this.part2Program,
      minimum_necessary_purpose: this.minimumNecessaryPurpose,
      data_classification: this.dataClassification,
      disclosure_basis: this.disclosureBasis,
      treatment_record_redisclosure_restriction: "true",
    };
    if (this.qsoAgreementReference !== undefined) {
      meta.qso_agreement_reference = this.qsoAgreementReference;
    }
    if (this.consentReference !== undefined) {
      meta.consent_reference = this.consentReference;
    }
    if (this.researchAuthorizationReference !== undefined) {
      meta.research_authorization_reference =
        this.researchAuthorizationReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
