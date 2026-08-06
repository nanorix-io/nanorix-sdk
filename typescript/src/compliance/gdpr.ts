/** GDPR (Regulation (EU) 2016/679) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2016-679";

/** GDPR Article 6(1) closed enum of lawful bases. */
export type GdprLegalBasis =
  | "consent"
  | "contract"
  | "legal_obligation"
  | "vital_interests"
  | "public_task"
  | "legitimate_interests";

const VALID_LEGAL_BASIS: ReadonlySet<GdprLegalBasis> = new Set<GdprLegalBasis>([
  "consent",
  "contract",
  "legal_obligation",
  "vital_interests",
  "public_task",
  "legitimate_interests",
]);

export interface GdprContextOptions {
  /** Data controller (Article 4(7)). Required. */
  dataController: string;
  /** Article 6(1) lawful basis. Required; must be one of the closed enum. */
  legalBasis: GdprLegalBasis;
  /**
   * Why the personal data is being processed. Required for the
   * purpose-limitation principle (Article 5(1)(b)).
   */
  processingPurpose: string;
  /**
   * Default `'personal_data'`; customer can narrow (e.g.,
   * `'special_category'` for Article 9 categories).
   */
  dataClassification?: string;
  /** Data processor identifier (Article 28) when controller uses one. */
  dataProcessor?: string;
  /** Comma-separated category tags (e.g., `'employees,customers'`). */
  dataSubjectCategories?: string;
  /** DPO contact reference. Helper does NOT verify a DPO exists. */
  dpoContact?: string;
  /**
   * Articles 44-50 transfer basis (e.g., `'adequacy'`, `'sccs'`,
   * `'bcrs'`, `'derogation'`).
   */
  crossBorderTransferBasis?: string;
  /** Storage-limitation tag (Article 5(1)(e)). */
  retentionPeriod?: string;
}

export class GdprContext {
  readonly dataController: string;
  readonly legalBasis: GdprLegalBasis;
  readonly processingPurpose: string;
  readonly dataClassification: string;
  readonly dataProcessor?: string;
  readonly dataSubjectCategories?: string;
  readonly dpoContact?: string;
  readonly crossBorderTransferBasis?: string;
  readonly retentionPeriod?: string;

  constructor(opts: GdprContextOptions) {
    if (!opts.dataController) {
      throw new Error("dataController is required and must be non-empty");
    }
    if (!opts.legalBasis) {
      throw new Error("legalBasis is required and must be non-empty");
    }
    if (!VALID_LEGAL_BASIS.has(opts.legalBasis)) {
      throw new Error(
        `legalBasis=${JSON.stringify(opts.legalBasis)} not in ` +
          `${JSON.stringify(Array.from(VALID_LEGAL_BASIS))} ` +
          `(Article 6(1) closed enum)`,
      );
    }
    if (!opts.processingPurpose) {
      throw new Error("processingPurpose is required and must be non-empty");
    }
    const dataClassification = opts.dataClassification ?? "personal_data";
    if (!dataClassification) {
      throw new Error("dataClassification must be non-empty");
    }

    validateValueLength("dataController", opts.dataController);
    validateValueLength("legalBasis", opts.legalBasis);
    validateValueLength("processingPurpose", opts.processingPurpose);
    validateValueLength("dataClassification", dataClassification);
    if (opts.dataProcessor !== undefined) {
      validateValueLength("dataProcessor", opts.dataProcessor);
    }
    if (opts.dataSubjectCategories !== undefined) {
      validateValueLength("dataSubjectCategories", opts.dataSubjectCategories);
    }
    if (opts.dpoContact !== undefined) {
      validateValueLength("dpoContact", opts.dpoContact);
    }
    if (opts.crossBorderTransferBasis !== undefined) {
      validateValueLength(
        "crossBorderTransferBasis",
        opts.crossBorderTransferBasis,
      );
    }
    if (opts.retentionPeriod !== undefined) {
      validateValueLength("retentionPeriod", opts.retentionPeriod);
    }

    this.dataController = opts.dataController;
    this.legalBasis = opts.legalBasis;
    this.processingPurpose = opts.processingPurpose;
    this.dataClassification = dataClassification;
    this.dataProcessor = opts.dataProcessor;
    this.dataSubjectCategories = opts.dataSubjectCategories;
    this.dpoContact = opts.dpoContact;
    this.crossBorderTransferBasis = opts.crossBorderTransferBasis;
    this.retentionPeriod = opts.retentionPeriod;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: "GDPR",
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      data_controller: this.dataController,
      legal_basis: this.legalBasis,
      processing_purpose: this.processingPurpose,
      data_classification: this.dataClassification,
    };
    if (this.dataProcessor !== undefined) meta.data_processor = this.dataProcessor;
    if (this.dataSubjectCategories !== undefined) {
      meta.data_subject_categories = this.dataSubjectCategories;
    }
    if (this.dpoContact !== undefined) meta.dpo_contact = this.dpoContact;
    if (this.crossBorderTransferBasis !== undefined) {
      meta.cross_border_transfer_basis = this.crossBorderTransferBasis;
    }
    if (this.retentionPeriod !== undefined) {
      meta.retention_period = this.retentionPeriod;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
