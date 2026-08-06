/**
 * AI fairness evaluation context for capsule workloads.
 *
 * Cross-vertical adapter: AI fairness evaluation applies to AI
 * deployments across hiring, housing, financial services, criminal
 * justice, education, healthcare. The helper ships under
 * `healthcare/` because healthcare AI is the lead beachhead but is
 * intentionally framework-agnostic with respect to vertical.
 *
 * Three-role discipline: Nanorix is an evidence vendor
 * (role 1). Declares framework context for an AI workload's
 * fairness-evaluation posture. Does NOT certify that the customer's
 * evaluation satisfies any specific regulatory or technical standard,
 * does NOT validate statistical sufficiency, does NOT verify
 * documentation against the customer's evaluation artifacts.
 * Methodology selection, demographic-group scoping, sample-size
 * determination, tolerance-band selection, and pre-registration
 * discipline remain the customer's AI ethics + governance team's
 * responsibility.
 *
 * Narrow signed claim discipline
 * (feedback_narrow_signed_claim_auditor_certifies.md): the metadata
 * envelope records the customer-declared evaluation context only;
 * never asserts "IS COMPLIANT" / "MEETS" / "SATISFIES" any framework.
 */

import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateNoCommaInList,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "2026-02";
const FRAMEWORK_ID = "AI_Fairness_Evaluation";

/** Canonical fairness-evaluation methodology taxonomy. */
export type AIFairnessEvaluationMethodology =
  | "error_rate_parity"
  | "outcome_parity"
  | "calibration_parity"
  | "equalized_odds"
  | "demographic_parity"
  | "custom";

const VALID_EVALUATION_METHODOLOGIES: ReadonlySet<AIFairnessEvaluationMethodology> =
  new Set<AIFairnessEvaluationMethodology>([
    "error_rate_parity",
    "outcome_parity",
    "calibration_parity",
    "equalized_odds",
    "demographic_parity",
    "custom",
  ]);

/** Canonical re-evaluation cadence taxonomy. */
export type AIFairnessReEvaluationCadence =
  | "quarterly"
  | "biannually"
  | "annually"
  | "on_significant_change"
  | "ad_hoc";

const VALID_RE_EVALUATION_CADENCES: ReadonlySet<AIFairnessReEvaluationCadence> =
  new Set<AIFairnessReEvaluationCadence>([
    "quarterly",
    "biannually",
    "annually",
    "on_significant_change",
    "ad_hoc",
  ]);

// ISO-8601 calendar-date structural validator (YYYY-MM-DD).
const ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/**
 * AI fairness evaluation context for a capsule workload.
 *
 * Records:
 *  - Evaluation methodology (closed enum)
 *  - Demographic groups evaluated (required non-empty)
 *  - Tolerance band (optional; applies to error-rate/calibration/equalized-odds)
 *  - Sample size (positive integer)
 *  - Evaluation completed flag
 *  - Documentation URL
 *  - Subgroup performance disclosed (composes with FDA SaMD)
 *  - Methodology pre-registered flag
 *  - Evaluation date (ISO-8601)
 *  - Re-evaluation cadence (closed enum)
 */
export interface AIFairnessContextOptions {
  evaluationMethodology: AIFairnessEvaluationMethodology;
  demographicGroupsEvaluated: string[];
  sampleSize: number;
  evaluationCompleted: boolean;
  toleranceBandPp?: number;
  documentationUrl?: string;
  subgroupPerformanceDisclosed?: boolean;
  methodologyPreRegistered?: boolean;
  evaluationDate?: string;
  reEvaluationCadence?: AIFairnessReEvaluationCadence;
  evaluationOwnerRole?: string;
  affectedDecisionCategories?: string[];
}

export class AIFairnessContext {
  readonly evaluationMethodology: AIFairnessEvaluationMethodology;
  readonly demographicGroupsEvaluated: ReadonlyArray<string>;
  readonly sampleSize: number;
  readonly evaluationCompleted: boolean;
  readonly toleranceBandPp?: number;
  readonly documentationUrl?: string;
  readonly subgroupPerformanceDisclosed?: boolean;
  readonly methodologyPreRegistered?: boolean;
  readonly evaluationDate?: string;
  readonly reEvaluationCadence?: AIFairnessReEvaluationCadence;
  readonly evaluationOwnerRole?: string;
  readonly affectedDecisionCategories: ReadonlyArray<string>;

  get frameworkId(): string {
    return FRAMEWORK_ID;
  }

  get frameworkVersion(): string {
    return FRAMEWORK_VERSION;
  }

  /** Whether the configured methodology admits a tolerance band. */
  get usesToleranceBand(): boolean {
    return (
      this.evaluationMethodology === "error_rate_parity" ||
      this.evaluationMethodology === "calibration_parity" ||
      this.evaluationMethodology === "equalized_odds"
    );
  }

  constructor(opts: AIFairnessContextOptions) {
    if (!opts.evaluationMethodology) {
      throw new Error(
        "evaluationMethodology is required and must be non-empty",
      );
    }
    if (!VALID_EVALUATION_METHODOLOGIES.has(opts.evaluationMethodology)) {
      throw new Error(
        `evaluationMethodology=${JSON.stringify(opts.evaluationMethodology)} not in ` +
          `${JSON.stringify(Array.from(VALID_EVALUATION_METHODOLOGIES))} ` +
          `(fairness-evaluation methodology taxonomy)`,
      );
    }

    if (
      !opts.demographicGroupsEvaluated ||
      opts.demographicGroupsEvaluated.length === 0
    ) {
      throw new Error(
        "demographicGroupsEvaluated is required and must be non-empty " +
          "(declare at least one demographic category evaluated)",
      );
    }
    opts.demographicGroupsEvaluated.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `demographicGroupsEvaluated[${idx}] must be a string ` +
            `(got ${typeof item})`,
        );
      }
      if (!item) {
        throw new Error(
          `demographicGroupsEvaluated[${idx}] must be non-empty`,
        );
      }
    });
    validateNoCommaInList(
      "demographicGroupsEvaluated",
      opts.demographicGroupsEvaluated,
    );

    if (
      typeof opts.sampleSize !== "number" ||
      !Number.isInteger(opts.sampleSize)
    ) {
      throw new Error(
        `sampleSize must be an integer (got ${typeof opts.sampleSize})`,
      );
    }
    if (opts.sampleSize <= 0) {
      throw new Error(
        `sampleSize=${opts.sampleSize} must be a positive integer ` +
          `(number of evaluation cases)`,
      );
    }

    if (typeof opts.evaluationCompleted !== "boolean") {
      throw new Error(
        "evaluationCompleted must be a bool (got " +
          `${typeof opts.evaluationCompleted}); pass true or false, ` +
          `not the string 'true'`,
      );
    }

    if (opts.toleranceBandPp !== undefined) {
      if (typeof opts.toleranceBandPp !== "number") {
        throw new Error(
          `toleranceBandPp must be a number ` +
            `(got ${typeof opts.toleranceBandPp})`,
        );
      }
      if (opts.toleranceBandPp < 0 || opts.toleranceBandPp > 100) {
        throw new Error(
          `toleranceBandPp=${opts.toleranceBandPp} must be in [0.0, 100.0] ` +
            `(percentage-point band)`,
        );
      }
    }

    if (
      opts.subgroupPerformanceDisclosed !== undefined &&
      typeof opts.subgroupPerformanceDisclosed !== "boolean"
    ) {
      throw new Error(
        "subgroupPerformanceDisclosed must be a bool when provided " +
          `(got ${typeof opts.subgroupPerformanceDisclosed})`,
      );
    }
    if (
      opts.methodologyPreRegistered !== undefined &&
      typeof opts.methodologyPreRegistered !== "boolean"
    ) {
      throw new Error(
        "methodologyPreRegistered must be a bool when provided " +
          `(got ${typeof opts.methodologyPreRegistered})`,
      );
    }

    if (opts.evaluationDate !== undefined) {
      if (!opts.evaluationDate) {
        throw new Error(
          "evaluationDate if provided must be non-empty",
        );
      }
      if (!ISO_DATE_PATTERN.test(opts.evaluationDate)) {
        throw new Error(
          `evaluationDate=${JSON.stringify(opts.evaluationDate)} must match ` +
            `ISO-8601 format 'YYYY-MM-DD'`,
        );
      }
    }

    if (opts.reEvaluationCadence !== undefined) {
      if (!opts.reEvaluationCadence) {
        throw new Error(
          "reEvaluationCadence if provided must be non-empty",
        );
      }
      if (!VALID_RE_EVALUATION_CADENCES.has(opts.reEvaluationCadence)) {
        throw new Error(
          `reEvaluationCadence=${JSON.stringify(opts.reEvaluationCadence)} ` +
            `not in ${JSON.stringify(Array.from(VALID_RE_EVALUATION_CADENCES))} ` +
            `(re-evaluation cadence taxonomy)`,
        );
      }
    }

    const affected = opts.affectedDecisionCategories ?? [];
    affected.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `affectedDecisionCategories[${idx}] must be a string ` +
            `(got ${typeof item})`,
        );
      }
      if (!item) {
        throw new Error(
          `affectedDecisionCategories[${idx}] must be non-empty`,
        );
      }
      if (item.includes(",")) {
        throw new Error(
          `affectedDecisionCategories[${idx}]=${JSON.stringify(item)} ` +
            `contains ',' which is the comma-join separator; remove or escape`,
        );
      }
    });

    validateValueLength("evaluationMethodology", opts.evaluationMethodology);
    validateValueLength(
      "demographicGroupsEvaluated(joined)",
      opts.demographicGroupsEvaluated.join(","),
    );
    if (opts.documentationUrl !== undefined) {
      validateValueLength("documentationUrl", opts.documentationUrl);
    }
    if (opts.evaluationDate !== undefined) {
      validateValueLength("evaluationDate", opts.evaluationDate);
    }
    if (opts.reEvaluationCadence !== undefined) {
      validateValueLength("reEvaluationCadence", opts.reEvaluationCadence);
    }
    if (opts.evaluationOwnerRole !== undefined) {
      validateValueLength("evaluationOwnerRole", opts.evaluationOwnerRole);
    }
    if (affected.length > 0) {
      validateValueLength(
        "affectedDecisionCategories(joined)",
        affected.join(","),
      );
    }

    this.evaluationMethodology = opts.evaluationMethodology;
    this.demographicGroupsEvaluated = Object.freeze([
      ...opts.demographicGroupsEvaluated,
    ]);
    this.sampleSize = opts.sampleSize;
    this.evaluationCompleted = opts.evaluationCompleted;
    this.toleranceBandPp = opts.toleranceBandPp;
    this.documentationUrl = opts.documentationUrl;
    this.subgroupPerformanceDisclosed = opts.subgroupPerformanceDisclosed;
    this.methodologyPreRegistered = opts.methodologyPreRegistered;
    this.evaluationDate = opts.evaluationDate;
    this.reEvaluationCadence = opts.reEvaluationCadence;
    this.evaluationOwnerRole = opts.evaluationOwnerRole;
    this.affectedDecisionCategories = Object.freeze([...affected]);

    Object.freeze(this);
  }

  /**
   * Convert to the metadata Record format for `capsules.create()`.
   * Mirrors the Python `to_metadata()` byte-for-byte on key names.
   */
  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: FRAMEWORK_ID,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      evaluation_methodology: this.evaluationMethodology,
      demographic_groups_evaluated: this.demographicGroupsEvaluated.join(","),
      sample_size: String(this.sampleSize),
      evaluation_completed: this.evaluationCompleted ? "true" : "false",
    };
    if (this.toleranceBandPp !== undefined) {
      // Emit with up to two decimal places; strip trailing zeros.
      let formatted = this.toleranceBandPp.toFixed(2).replace(/0+$/, "");
      formatted = formatted.replace(/\.$/, "");
      if (!formatted) formatted = "0";
      meta.tolerance_band_pp = formatted;
    }
    if (this.documentationUrl !== undefined) {
      meta.documentation_url = this.documentationUrl;
    }
    if (this.subgroupPerformanceDisclosed !== undefined) {
      meta.subgroup_performance_disclosed =
        this.subgroupPerformanceDisclosed ? "true" : "false";
    }
    if (this.methodologyPreRegistered !== undefined) {
      meta.methodology_pre_registered = this.methodologyPreRegistered
        ? "true"
        : "false";
    }
    if (this.evaluationDate !== undefined) {
      meta.evaluation_date = this.evaluationDate;
    }
    if (this.reEvaluationCadence !== undefined) {
      meta.re_evaluation_cadence = this.reEvaluationCadence;
    }
    if (this.evaluationOwnerRole !== undefined) {
      meta.evaluation_owner_role = this.evaluationOwnerRole;
    }
    if (this.affectedDecisionCategories.length > 0) {
      meta.affected_decision_categories =
        this.affectedDecisionCategories.join(",");
    }

    const keyCount = Object.keys(meta).length;
    if (keyCount > MAX_METADATA_KEYS) {
      throw new Error(
        `toMetadata produced ${keyCount} keys, max ${MAX_METADATA_KEYS}`,
      );
    }
    const size = estimateSerializedSize(meta);
    if (size > 4096) {
      throw new Error(`toMetadata serializes to ${size} bytes, max 4096`);
    }
    return meta;
  }
}
