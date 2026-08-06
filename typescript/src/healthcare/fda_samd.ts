/**
 * FDA Software as a Medical Device (SaMD) framework context for
 * capsule workloads.
 *
 * 21 CFR Part 860 device classification (I/II/III) + pre-market
 * submission pathway (510(k) / De Novo / PMA / Exempt) + submission
 * lifecycle status. Tied to FDA AI-Enabled Device Software Functions
 * 2025 draft guidance (89 FR 1543) for subgroup performance disclosure
 * — which is an EMERGING expectation, not a finalized mandate.
 *
 * Three-role discipline: records the customer-declared
 * status. Does NOT certify, attest to, or claim FDA clearance,
 * approval, or any pre-market submission status. K-number / DEN-number
 * authentication, submission-pathway selection, clinical validation,
 * and post-market surveillance remain the customer's regulatory
 * affairs counsel + clinical team's responsibility.
 *
 * Narrow signed claim discipline: NEVER asserts "IS COMPLIANT" /
 * "MEETS" / "SATISFIES" / "PASSED" FDA expectations. Status fields
 * record customer-declared values only.
 */

import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "2026-02";
const FRAMEWORK_ID = "FDA_SaMD";

/** 21 CFR Part 860 device class. */
export type FDADeviceClass = "I" | "II" | "III";

const VALID_DEVICE_CLASSES: ReadonlySet<FDADeviceClass> =
  new Set<FDADeviceClass>(["I", "II", "III"]);

/** FDA pre-market submission pathway. */
export type FDASubmissionPathway = "510k" | "de_novo" | "pma" | "exempt";

const VALID_SUBMISSION_PATHWAYS: ReadonlySet<FDASubmissionPathway> =
  new Set<FDASubmissionPathway>(["510k", "de_novo", "pma", "exempt"]);

/** FDA pre-market submission lifecycle status. */
export type FDASubmissionStatus =
  | "pre_submission"
  | "submitted"
  | "in_review"
  | "cleared"
  | "approved"
  | "denied"
  | "withdrawn";

const VALID_SUBMISSION_STATUSES: ReadonlySet<FDASubmissionStatus> =
  new Set<FDASubmissionStatus>([
    "pre_submission",
    "submitted",
    "in_review",
    "cleared",
    "approved",
    "denied",
    "withdrawn",
  ]);

/** Clinical validation phase taxonomy. */
export type FDAClinicalValidationStatus =
  | "not_started"
  | "in_progress"
  | "completed";

const VALID_CLINICAL_VALIDATION_STATUSES: ReadonlySet<FDAClinicalValidationStatus> =
  new Set<FDAClinicalValidationStatus>([
    "not_started",
    "in_progress",
    "completed",
  ]);

const K_NUMBER_PATTERN = /^K\d{6}$/;
const DEN_NUMBER_PATTERN = /^DEN\d{6}$/;

/** Options for `FDASaMDContext` construction. */
export interface FDASaMDContextOptions {
  deviceClass: FDADeviceClass;
  submissionPathway: FDASubmissionPathway;
  submissionStatus: FDASubmissionStatus;
  intendedUse: string;
  indicationsForUse?: string;
  clinicalValidationStatus?: FDAClinicalValidationStatus;
  kNumber?: string;
  deNovoNumber?: string;
  subgroupPerformanceDisclosed?: boolean;
  predicateDeviceReference?: string;
  qualitySystemReference?: string;
  submissionOwnerRole?: string;
  affectedDeviceCategories?: string[];
}

export class FDASaMDContext {
  readonly deviceClass: FDADeviceClass;
  readonly submissionPathway: FDASubmissionPathway;
  readonly submissionStatus: FDASubmissionStatus;
  readonly intendedUse: string;
  readonly indicationsForUse?: string;
  readonly clinicalValidationStatus?: FDAClinicalValidationStatus;
  readonly kNumber?: string;
  readonly deNovoNumber?: string;
  readonly subgroupPerformanceDisclosed?: boolean;
  readonly predicateDeviceReference?: string;
  readonly qualitySystemReference?: string;
  readonly submissionOwnerRole?: string;
  readonly affectedDeviceCategories: ReadonlyArray<string>;

  get frameworkId(): string {
    return FRAMEWORK_ID;
  }

  get frameworkVersion(): string {
    return FRAMEWORK_VERSION;
  }

  /** Whether the device is Class III (highest risk under 21 CFR Part 860). */
  get isHighRiskClass(): boolean {
    return this.deviceClass === "III";
  }

  /**
   * Whether the customer-declared submission_status reaches
   * cleared (510(k) / De Novo) or approved (PMA). Helper does NOT
   * verify against FDA's public databases.
   */
  get isClearedOrApproved(): boolean {
    return (
      this.submissionStatus === "cleared" ||
      this.submissionStatus === "approved"
    );
  }

  constructor(opts: FDASaMDContextOptions) {
    if (!opts.deviceClass) {
      throw new Error("deviceClass is required and must be non-empty");
    }
    if (!VALID_DEVICE_CLASSES.has(opts.deviceClass)) {
      throw new Error(
        `deviceClass=${JSON.stringify(opts.deviceClass)} not in ` +
          `${JSON.stringify(Array.from(VALID_DEVICE_CLASSES).sort())} ` +
          `(21 CFR Part 860 device-class taxonomy)`,
      );
    }
    if (!opts.submissionPathway) {
      throw new Error(
        "submissionPathway is required and must be non-empty",
      );
    }
    if (!VALID_SUBMISSION_PATHWAYS.has(opts.submissionPathway)) {
      throw new Error(
        `submissionPathway=${JSON.stringify(opts.submissionPathway)} not in ` +
          `${JSON.stringify(Array.from(VALID_SUBMISSION_PATHWAYS).sort())} ` +
          `(FDA pre-market submission pathway taxonomy)`,
      );
    }
    if (!opts.submissionStatus) {
      throw new Error(
        "submissionStatus is required and must be non-empty",
      );
    }
    if (!VALID_SUBMISSION_STATUSES.has(opts.submissionStatus)) {
      throw new Error(
        `submissionStatus=${JSON.stringify(opts.submissionStatus)} not in ` +
          `${JSON.stringify(Array.from(VALID_SUBMISSION_STATUSES).sort())} ` +
          `(FDA submission lifecycle taxonomy)`,
      );
    }
    if (!opts.intendedUse) {
      throw new Error("intendedUse is required and must be non-empty");
    }

    if (opts.clinicalValidationStatus !== undefined) {
      if (!opts.clinicalValidationStatus) {
        throw new Error(
          "clinicalValidationStatus if provided must be non-empty",
        );
      }
      if (
        !VALID_CLINICAL_VALIDATION_STATUSES.has(opts.clinicalValidationStatus)
      ) {
        throw new Error(
          `clinicalValidationStatus=${JSON.stringify(opts.clinicalValidationStatus)} not in ` +
            `${JSON.stringify(Array.from(VALID_CLINICAL_VALIDATION_STATUSES).sort())}`,
        );
      }
    }

    if (opts.kNumber !== undefined) {
      if (!opts.kNumber) {
        throw new Error("kNumber if provided must be non-empty");
      }
      if (!K_NUMBER_PATTERN.test(opts.kNumber)) {
        throw new Error(
          `kNumber=${JSON.stringify(opts.kNumber)} must match format ` +
            `'K\\d{6}' (e.g., 'K231234'); helper validates structural ` +
            `format only`,
        );
      }
    }

    if (opts.deNovoNumber !== undefined) {
      if (!opts.deNovoNumber) {
        throw new Error("deNovoNumber if provided must be non-empty");
      }
      if (!DEN_NUMBER_PATTERN.test(opts.deNovoNumber)) {
        throw new Error(
          `deNovoNumber=${JSON.stringify(opts.deNovoNumber)} must match ` +
            `format 'DEN\\d{6}' (e.g., 'DEN230001'); helper validates ` +
            `structural format only`,
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

    const affected = opts.affectedDeviceCategories ?? [];
    affected.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `affectedDeviceCategories[${idx}] must be a string ` +
            `(got ${typeof item})`,
        );
      }
      if (!item) {
        throw new Error(
          `affectedDeviceCategories[${idx}] must be non-empty`,
        );
      }
      if (item.includes(",")) {
        throw new Error(
          `affectedDeviceCategories[${idx}]=${JSON.stringify(item)} ` +
            `contains ',' which is the comma-join separator`,
        );
      }
    });

    validateValueLength("deviceClass", opts.deviceClass);
    validateValueLength("submissionPathway", opts.submissionPathway);
    validateValueLength("submissionStatus", opts.submissionStatus);
    validateValueLength("intendedUse", opts.intendedUse);
    if (opts.indicationsForUse !== undefined) {
      validateValueLength("indicationsForUse", opts.indicationsForUse);
    }
    if (opts.clinicalValidationStatus !== undefined) {
      validateValueLength(
        "clinicalValidationStatus",
        opts.clinicalValidationStatus,
      );
    }
    if (opts.kNumber !== undefined) {
      validateValueLength("kNumber", opts.kNumber);
    }
    if (opts.deNovoNumber !== undefined) {
      validateValueLength("deNovoNumber", opts.deNovoNumber);
    }
    if (opts.predicateDeviceReference !== undefined) {
      validateValueLength(
        "predicateDeviceReference",
        opts.predicateDeviceReference,
      );
    }
    if (opts.qualitySystemReference !== undefined) {
      validateValueLength(
        "qualitySystemReference",
        opts.qualitySystemReference,
      );
    }
    if (opts.submissionOwnerRole !== undefined) {
      validateValueLength(
        "submissionOwnerRole",
        opts.submissionOwnerRole,
      );
    }
    if (affected.length > 0) {
      validateValueLength(
        "affectedDeviceCategories(joined)",
        affected.join(","),
      );
    }

    this.deviceClass = opts.deviceClass;
    this.submissionPathway = opts.submissionPathway;
    this.submissionStatus = opts.submissionStatus;
    this.intendedUse = opts.intendedUse;
    this.indicationsForUse = opts.indicationsForUse;
    this.clinicalValidationStatus = opts.clinicalValidationStatus;
    this.kNumber = opts.kNumber;
    this.deNovoNumber = opts.deNovoNumber;
    this.subgroupPerformanceDisclosed = opts.subgroupPerformanceDisclosed;
    this.predicateDeviceReference = opts.predicateDeviceReference;
    this.qualitySystemReference = opts.qualitySystemReference;
    this.submissionOwnerRole = opts.submissionOwnerRole;
    this.affectedDeviceCategories = Object.freeze([...affected]);
    Object.freeze(this);
  }

  /** Convert to the metadata Record format for `capsules.create()`. */
  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: FRAMEWORK_ID,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      device_class: this.deviceClass,
      submission_pathway: this.submissionPathway,
      submission_status: this.submissionStatus,
      intended_use: this.intendedUse,
    };
    if (this.indicationsForUse !== undefined) {
      meta.indications_for_use = this.indicationsForUse;
    }
    if (this.clinicalValidationStatus !== undefined) {
      meta.clinical_validation_status = this.clinicalValidationStatus;
    }
    if (this.kNumber !== undefined) {
      meta.k_number = this.kNumber;
    }
    if (this.deNovoNumber !== undefined) {
      meta.de_novo_number = this.deNovoNumber;
    }
    if (this.subgroupPerformanceDisclosed !== undefined) {
      meta.subgroup_performance_disclosed = this.subgroupPerformanceDisclosed
        ? "true"
        : "false";
    }
    if (this.predicateDeviceReference !== undefined) {
      meta.predicate_device_reference = this.predicateDeviceReference;
    }
    if (this.qualitySystemReference !== undefined) {
      meta.quality_system_reference = this.qualitySystemReference;
    }
    if (this.submissionOwnerRole !== undefined) {
      meta.submission_owner_role = this.submissionOwnerRole;
    }
    if (this.affectedDeviceCategories.length > 0) {
      meta.affected_device_categories =
        this.affectedDeviceCategories.join(",");
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
