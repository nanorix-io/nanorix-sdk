/**
 * DICOMweb (WADO-RS / STOW-RS / QIDO-RS) HTTP API adapter for capsule
 * workloads.
 *
 * DICOMweb is the HTTP-based modernization layer over legacy DICOM
 * DIMSE. Parses HTTP transactions (response Content-Type, status code,
 * multipart/related boundary, DICOM-JSON or application/dicom payloads)
 * and produces structural metadata + SHA-256 anchor of the response
 * body.
 *
 * Three-role discipline (ADR-017): structural adapter; pixel-data
 * interpretation, DICOM IG conformance (IHE RAD-TF, Dental Profile,
 * Multi-Frame Image Profile), PHI classification, and downstream
 * consumer responsibilities remain the customer's.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "dicomweb-ps3.18-2024";

/** DICOMweb verb families covered by the adapter. */
export type DicomwebVerbFamily = "WADO-RS" | "STOW-RS" | "QIDO-RS";

const VALID_VERB_FAMILIES: ReadonlySet<DicomwebVerbFamily> =
  new Set<DicomwebVerbFamily>(["WADO-RS", "STOW-RS", "QIDO-RS"]);

// DICOM UID format per PS3.5 §9.
const UID_PATTERN = /^\d+(\.\d+)*$/;

// DICOM-JSON attribute tags.
export const DICOM_TAG_SOP_CLASS_UID = "00080016";
export const DICOM_TAG_SOP_INSTANCE_UID = "00080018";
export const DICOM_TAG_STUDY_INSTANCE_UID = "0020000D";
export const DICOM_TAG_SERIES_INSTANCE_UID = "0020000E";
export const DICOM_TAG_MODALITY = "00080060";
export const DICOM_TAG_TRANSFER_SYNTAX_UID = "00020010";

function isValidUid(s: string): boolean {
  if (!s || s.length > 64) return false;
  return UID_PATTERN.test(s);
}

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for a DICOMweb HTTP transaction. */
export interface DICOMwebTransaction {
  verbFamily: DicomwebVerbFamily;
  httpStatus: number;
  contentType: string;
  studyInstanceUid: string;
  seriesInstanceUid: string;
  sopInstanceUid: string;
  sopClassUid: string;
  modality: string;
  responseBodySha256: string;
  responseSizeBytes: number;
  instanceCount: number;
}

/** Whether the transaction is a retrieve (WADO-RS). */
export function isRetrieve(tx: DICOMwebTransaction): boolean {
  return tx.verbFamily === "WADO-RS";
}

/** Whether the transaction is a store (STOW-RS). */
export function isStore(tx: DICOMwebTransaction): boolean {
  return tx.verbFamily === "STOW-RS";
}

/** Whether the transaction is a query (QIDO-RS). */
export function isQuery(tx: DICOMwebTransaction): boolean {
  return tx.verbFamily === "QIDO-RS";
}

/** Convert a DICOMwebTransaction to capsule metadata envelope. */
export function transactionToMetadata(
  tx: DICOMwebTransaction,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "DICOMWEB",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    verb_family: tx.verbFamily,
    http_status: String(tx.httpStatus),
    content_type: tx.contentType,
    response_body_sha256: tx.responseBodySha256,
    response_size_bytes: String(tx.responseSizeBytes),
    instance_count: String(tx.instanceCount),
  };
  if (tx.studyInstanceUid) meta.study_instance_uid = tx.studyInstanceUid;
  if (tx.seriesInstanceUid) meta.series_instance_uid = tx.seriesInstanceUid;
  if (tx.sopInstanceUid) meta.sop_instance_uid = tx.sopInstanceUid;
  if (tx.sopClassUid) meta.sop_class_uid = tx.sopClassUid;
  if (tx.modality) meta.modality = tx.modality;

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

/** Options for `DICOMwebAdapter.parseResponse`. */
export interface ParseDICOMwebResponseOptions {
  verbFamily: DicomwebVerbFamily;
  httpStatus: number;
  contentType: string;
  responseBody: Uint8Array;
  studyInstanceUid?: string;
  seriesInstanceUid?: string;
  sopInstanceUid?: string;
  sopClassUid?: string;
  modality?: string;
}

/** DICOMweb structural adapter. */
export class DICOMwebAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedVerbFamilies(): string[] {
    return Array.from(VALID_VERB_FAMILIES).sort();
  }

  /** Parse a single DICOMweb HTTP response. */
  parseResponse(opts: ParseDICOMwebResponseOptions): DICOMwebTransaction {
    if (!VALID_VERB_FAMILIES.has(opts.verbFamily)) {
      throw new Error(
        `unsupported verbFamily '${opts.verbFamily}'; must be one of ` +
          `${JSON.stringify(Array.from(VALID_VERB_FAMILIES).sort())}`,
      );
    }
    if (
      typeof opts.httpStatus !== "number" ||
      !Number.isInteger(opts.httpStatus) ||
      opts.httpStatus < 1 ||
      opts.httpStatus > 599
    ) {
      throw new Error(
        `httpStatus must be int in 1..599 (got ${JSON.stringify(opts.httpStatus)})`,
      );
    }
    if (typeof opts.contentType !== "string" || !opts.contentType) {
      throw new Error("contentType must be a non-empty string");
    }
    if (!(opts.responseBody instanceof Uint8Array)) {
      throw new Error("responseBody must be a Uint8Array");
    }

    const uids: Array<[string, string | undefined]> = [
      ["studyInstanceUid", opts.studyInstanceUid],
      ["seriesInstanceUid", opts.seriesInstanceUid],
      ["sopInstanceUid", opts.sopInstanceUid],
      ["sopClassUid", opts.sopClassUid],
    ];
    for (const [name, value] of uids) {
      if (value && !isValidUid(value) && this._strict) {
        throw new Error(
          `${name} '${value}' does not match DICOM UID structural ` +
            `format (PS3.5 §9: ASCII digits and dots, max 64 chars)`,
        );
      }
    }

    const bodyHash = sha256Hex(opts.responseBody);
    const bodySize = opts.responseBody.length;
    const instanceCount = this._extractInstanceCount(
      opts.verbFamily,
      opts.contentType,
      opts.responseBody,
    );

    return {
      verbFamily: opts.verbFamily,
      httpStatus: opts.httpStatus,
      contentType: opts.contentType,
      studyInstanceUid: opts.studyInstanceUid ?? "",
      seriesInstanceUid: opts.seriesInstanceUid ?? "",
      sopInstanceUid: opts.sopInstanceUid ?? "",
      sopClassUid: opts.sopClassUid ?? "",
      modality: opts.modality ?? "",
      responseBodySha256: bodyHash,
      responseSizeBytes: bodySize,
      instanceCount,
    };
  }

  private _extractInstanceCount(
    verbFamily: DicomwebVerbFamily,
    contentType: string,
    body: Uint8Array,
  ): number {
    try {
      const ctLower = contentType.toLowerCase();
      if (verbFamily === "WADO-RS" && ctLower.includes("multipart")) {
        const boundary = DICOMwebAdapter._extractBoundary(contentType);
        if (boundary) {
          const text = new TextDecoder("latin1").decode(body);
          const marker = `--${boundary}`;
          let raw = 0;
          let idx = 0;
          while ((idx = text.indexOf(marker, idx)) !== -1) {
            raw += 1;
            idx += marker.length;
          }
          if (raw >= 2) return Math.max(0, raw - 1);
          return 0;
        }
      } else if (verbFamily === "QIDO-RS" && ctLower.includes("json")) {
        try {
          const decoded = JSON.parse(new TextDecoder("utf-8").decode(body));
          if (Array.isArray(decoded)) return decoded.length;
        } catch {
          return 0;
        }
      }
    } catch {
      return 0;
    }
    return 0;
  }

  private static _extractBoundary(contentType: string): string {
    for (const part of contentType.split(";")) {
      const stripped = part.trim();
      if (stripped.toLowerCase().startsWith("boundary=")) {
        let value = stripped.slice("boundary=".length).trim();
        if (
          value.startsWith('"') &&
          value.endsWith('"') &&
          value.length >= 2
        ) {
          value = value.slice(1, -1);
        }
        return value;
      }
    }
    return "";
  }

  /** Public DICOM UID structural-format validator. */
  static isValidUid(s: string): boolean {
    return isValidUid(s);
  }
}
