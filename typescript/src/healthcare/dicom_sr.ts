/**
 * DICOM SR (Structured Reporting) AuditProof binding helper for
 * capsule workloads.
 *
 * Binds DICOM SR document evidence into AuditProof metadata: SR
 * document SHA-256 hash, SOP Instance UID, content template ID
 * (TID 1500 / TID 4019 / etc.), creator role (HUMAN / AI), and
 * (when AI-generated) the algorithm identification.
 *
 * Three-role discipline: structural binding only; clinical
 * interpretation, template conformance, FDA SaMD post-market
 * surveillance + EU MDR Annex VIII regulatory determination remain
 * the customer's responsibility.
 *
 * AI-output discipline: when `creator='AI'`, `algorithmIdentification`
 * is REQUIRED per FDA SaMD post-market surveillance expectations.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "dicom-sr-ps3.3-2024";

/** SR document creator role. */
export type DicomSrCreator = "HUMAN" | "AI";

const VALID_CREATOR_ROLES: ReadonlySet<DicomSrCreator> = new Set<DicomSrCreator>(
  ["HUMAN", "AI"],
);

// Common SR template IDs per PS3.16.
export const SR_TEMPLATE_MEASUREMENT_REPORT = "TID 1500";
export const SR_TEMPLATE_AI_ANALYSIS_REPORT = "TID 4019";
export const SR_TEMPLATE_IMAGE_LIBRARY = "TID 4030";
export const SR_TEMPLATE_BASIC_DIAGNOSTIC_IMAGING_REPORT = "TID 2000";

// DICOM UID format per PS3.5 §9.
const UID_PATTERN = /^\d+(\.\d+)*$/;
// DICOM SR Content Template ID format per PS3.16 §A.3.3.2.2.
const TID_PATTERN = /^TID \d{1,6}$/;

function sha256Hex(input: string | Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Public structural-format validator for DICOM UIDs (PS3.5 §9). */
export function isValidDicomUid(s: string): boolean {
  if (!s || s.length > 64) return false;
  return UID_PATTERN.test(s);
}

function isValidTemplateId(s: string): boolean {
  return TID_PATTERN.test(s);
}

/** Structural binding fields for a DICOM SR document. */
export interface DICOMSRBindingFields {
  sopInstanceUid: string;
  sopClassUid: string;
  contentTemplateId: string;
  documentSha256: string;
  documentSizeBytes: number;
  creator: DicomSrCreator;
  algorithmIdentification: string;
  studyInstanceUid: string;
  seriesInstanceUid: string;
}

/** Options for `DICOMSRBinding.bind`. */
export interface DICOMSRBindOptions {
  sopInstanceUid: string;
  documentBytes: Uint8Array;
  creator: DicomSrCreator;
  contentTemplateId: string;
  sopClassUid?: string;
  algorithmIdentification?: string;
  studyInstanceUid?: string;
  seriesInstanceUid?: string;
}

/**
 * Structural binding for a DICOM SR document.
 *
 * Captures SOP Instance UID, content template ID, SHA-256 of the
 * PS3.10 file bytes, creator role, and (when AI) algorithm ID. Does
 * NOT include structured content items or PHI-bearing fields.
 */
export class DICOMSRBinding {
  readonly sopInstanceUid: string;
  readonly sopClassUid: string;
  readonly contentTemplateId: string;
  readonly documentSha256: string;
  readonly documentSizeBytes: number;
  readonly creator: DicomSrCreator;
  readonly algorithmIdentification: string;
  readonly studyInstanceUid: string;
  readonly seriesInstanceUid: string;

  constructor(fields: DICOMSRBindingFields) {
    this.sopInstanceUid = fields.sopInstanceUid;
    this.sopClassUid = fields.sopClassUid;
    this.contentTemplateId = fields.contentTemplateId;
    this.documentSha256 = fields.documentSha256;
    this.documentSizeBytes = fields.documentSizeBytes;
    this.creator = fields.creator;
    this.algorithmIdentification = fields.algorithmIdentification;
    this.studyInstanceUid = fields.studyInstanceUid;
    this.seriesInstanceUid = fields.seriesInstanceUid;
    Object.freeze(this);
  }

  /** Convert to capsule metadata envelope. */
  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: "DICOM_SR",
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      sop_instance_uid: this.sopInstanceUid,
      document_sha256: this.documentSha256,
      document_size_bytes: String(this.documentSizeBytes),
      creator: this.creator,
      content_template_id: this.contentTemplateId,
    };
    if (this.sopClassUid) meta.sop_class_uid = this.sopClassUid;
    if (this.algorithmIdentification) {
      meta.algorithm_identification = this.algorithmIdentification;
    }
    if (this.studyInstanceUid) meta.study_instance_uid = this.studyInstanceUid;
    if (this.seriesInstanceUid) {
      meta.series_instance_uid = this.seriesInstanceUid;
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

  /** Build a binding from structural identifiers + raw document bytes. */
  static bind(opts: DICOMSRBindOptions): DICOMSRBinding {
    if (typeof opts.sopInstanceUid !== "string" || !opts.sopInstanceUid) {
      throw new Error("sopInstanceUid required (non-empty string)");
    }
    if (!isValidDicomUid(opts.sopInstanceUid)) {
      throw new Error(
        `sopInstanceUid '${opts.sopInstanceUid}' does not match DICOM ` +
          `UID structural format (PS3.5 §9)`,
      );
    }
    if (!(opts.documentBytes instanceof Uint8Array)) {
      throw new Error("documentBytes must be a Uint8Array");
    }
    if (opts.documentBytes.length === 0) {
      throw new Error("documentBytes must be non-empty");
    }
    if (!VALID_CREATOR_ROLES.has(opts.creator)) {
      throw new Error(
        `creator '${opts.creator}' invalid; must be one of ` +
          `${JSON.stringify(Array.from(VALID_CREATOR_ROLES).sort())}`,
      );
    }
    if (!opts.contentTemplateId) {
      throw new Error(
        "contentTemplateId required (non-empty TID string, " +
          "e.g., 'TID 1500' for Measurement Report or 'TID 4019' for " +
          "AI Analysis Report)",
      );
    }
    if (!isValidTemplateId(opts.contentTemplateId)) {
      throw new Error(
        `contentTemplateId '${opts.contentTemplateId}' invalid; ` +
          `must match 'TID NNNN' pattern (e.g., 'TID 1500')`,
      );
    }

    const algorithmIdentification = opts.algorithmIdentification ?? "";
    const sopClassUid = opts.sopClassUid ?? "";
    const studyInstanceUid = opts.studyInstanceUid ?? "";
    const seriesInstanceUid = opts.seriesInstanceUid ?? "";

    if (opts.creator === "AI") {
      if (!algorithmIdentification) {
        throw new Error(
          "algorithmIdentification required when creator='AI' " +
            "(FDA SaMD post-market surveillance: AI-generated SR must " +
            "carry algorithm identification for performance attribution)",
        );
      }
      if (algorithmIdentification.length > 512) {
        throw new Error(
          `algorithmIdentification length ${algorithmIdentification.length}; ` +
            "max 512 chars",
        );
      }
    }

    if (sopClassUid && !isValidDicomUid(sopClassUid)) {
      throw new Error(
        `sopClassUid '${sopClassUid}' does not match DICOM UID ` +
          `structural format`,
      );
    }
    if (studyInstanceUid && !isValidDicomUid(studyInstanceUid)) {
      throw new Error(
        `studyInstanceUid '${studyInstanceUid}' does not match DICOM ` +
          `UID structural format`,
      );
    }
    if (seriesInstanceUid && !isValidDicomUid(seriesInstanceUid)) {
      throw new Error(
        `seriesInstanceUid '${seriesInstanceUid}' does not match DICOM ` +
          `UID structural format`,
      );
    }

    const documentSha256 = sha256Hex(opts.documentBytes);
    return new DICOMSRBinding({
      sopInstanceUid: opts.sopInstanceUid,
      sopClassUid,
      contentTemplateId: opts.contentTemplateId,
      documentSha256,
      documentSizeBytes: opts.documentBytes.length,
      creator: opts.creator,
      algorithmIdentification,
      studyInstanceUid,
      seriesInstanceUid,
    });
  }
}
