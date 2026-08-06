/**
 * X12 EDI 278 services review / prior authorization structural adapter.
 *
 * X12 EDI 278 (Health Care Services Review - Request for Review and
 * Response) is the HIPAA-mandated transaction set for PA + utilization
 * management across the US healthcare payer ecosystem. Parses
 * customer-supplied X12 v5010 wire payloads (005010X217 TR3 IG) and
 * produces structural metadata + payload SHA-256 anchor.
 *
 * Three-role discipline: structural adapter. Medical-
 * necessity adjudication, peer-review interpretation, payer
 * companion-guide conformance, and downstream PA decisioning remain
 * the customer's responsibility.
 *
 * Customer-sole-declarer: customer supplies the wire payload;
 * adapter parses structural fields only. Member IDs are SHA-256-hashed.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "x12-005010X217";

/** 278 transaction direction. */
export type X12_278_Direction = "REQUEST" | "RESPONSE";

const X12_278_TRANSACTION_SET = "278";

/** HCR01 certification action codes (most-common subset). */
export const X12_278_CERT_ACTIONS: Record<string, string> = {
  A1: "Certified in Total",
  A2: "Certified Partial",
  A3: "Not Certified",
  A4: "Pended",
  A5: "Cancelled",
  A6: "Modified",
  C: "Cancel",
  CT: "Contact Payer",
  F: "Final",
  M: "Modified",
  NA: "No Action Required",
  P: "Pended",
};

/** UM01 review type codes. */
export const X12_278_REVIEW_TYPES: Record<string, string> = {
  AR: "Admission Review",
  HS: "Health Services Review",
  IR: "Initial Review",
  SC: "Specialty Care Review",
};

/** UM02 certification request categories. */
export const X12_278_CERT_REQUEST_CATEGORIES: Record<string, string> = {
  AR: "Admission Review",
  CH: "Chiropractic",
  CO: "Consultation",
  DM: "Durable Medical Equipment",
  DS: "Diagnostic Services",
  HC: "Health Care Provider",
  HH: "Home Health Care",
  MS: "Medical Services",
  NS: "Nursing Services",
  PT: "Physical Therapy",
  RN: "Renal Care",
  RT: "Radiation Therapy",
  SC: "Specialty Care",
  SK: "Skilled Nursing Facility",
};

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for an X12 278 transaction. */
export interface X12ServicesReviewTransaction {
  transactionSet: string;
  direction: X12_278_Direction;
  isaControlNumber: string;
  gsControlNumber: string;
  stControlNumber: string;
  submitterId: string;
  receiverId: string;
  payerId: string;
  payerName: string;
  requestingProviderNpi: string;
  serviceProviderNpi: string;
  memberIdHash: string;
  certificationRequestCategory: string;
  reviewType: string;
  certificationAction: string;
  certificationNumber: string;
  transactionDatetime: string;
  payloadHash: string;
  segmentTypesObserved: string;
}

/** Whether the transaction is a 278 REQUEST. */
export function is278Request(tx: X12ServicesReviewTransaction): boolean {
  return tx.direction === "REQUEST";
}

/** Whether the transaction is a 278 RESPONSE. */
export function is278Response(tx: X12ServicesReviewTransaction): boolean {
  return tx.direction === "RESPONSE";
}

/** Whether the response indicates a positive certification result. */
export function is278Approved(tx: X12ServicesReviewTransaction): boolean {
  return tx.certificationAction === "A1" || tx.certificationAction === "A2";
}

/** Whether the response indicates a negative certification result. */
export function is278Denied(tx: X12ServicesReviewTransaction): boolean {
  return tx.certificationAction === "A3";
}

/** Whether the response is pended. */
export function is278Pended(tx: X12ServicesReviewTransaction): boolean {
  return tx.certificationAction === "A4" || tx.certificationAction === "P";
}

/** Convert a parsed 278 transaction to capsule metadata envelope. */
export function x12_278_toMetadata(
  tx: X12ServicesReviewTransaction,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "X12-278",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    transaction_set: tx.transactionSet,
    direction: tx.direction,
    isa_control_number: tx.isaControlNumber,
    st_control_number: tx.stControlNumber,
    payload_hash: tx.payloadHash,
  };
  if (tx.payerId) meta.payer_id = tx.payerId;
  if (tx.payerName) meta.payer_name = tx.payerName;
  if (tx.requestingProviderNpi) {
    meta.requesting_provider_npi = tx.requestingProviderNpi;
  }
  if (tx.serviceProviderNpi) {
    meta.service_provider_npi = tx.serviceProviderNpi;
  }
  if (tx.memberIdHash) meta.member_id_hash = tx.memberIdHash;
  if (tx.certificationRequestCategory) {
    meta.certification_request_category = tx.certificationRequestCategory;
  }
  if (tx.reviewType) meta.review_type = tx.reviewType;
  if (tx.certificationAction) {
    meta.certification_action = tx.certificationAction;
  }
  if (tx.certificationNumber) {
    meta.certification_number = tx.certificationNumber;
  }
  if (tx.transactionDatetime) {
    meta.transaction_datetime = tx.transactionDatetime;
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

/** X12 EDI 278 services review structural adapter. */
export class X12ServicesReviewAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedTransactionSets(): string[] {
    return [X12_278_TRANSACTION_SET];
  }

  get certificationActions(): Record<string, string> {
    return { ...X12_278_CERT_ACTIONS };
  }

  get reviewTypes(): Record<string, string> {
    return { ...X12_278_REVIEW_TYPES };
  }

  get certificationRequestCategories(): Record<string, string> {
    return { ...X12_278_CERT_REQUEST_CATEGORIES };
  }

  /** Parse a single X12 278 transaction. */
  parseTransaction(raw: Uint8Array | string): X12ServicesReviewTransaction {
    let bytes: Uint8Array;
    if (typeof raw === "string") {
      bytes = new TextEncoder().encode(raw);
    } else if (raw instanceof Uint8Array) {
      bytes = raw;
    } else {
      throw new Error(
        `parseTransaction expects Uint8Array or string (got ${typeof raw})`,
      );
    }
    if (bytes.length === 0) throw new Error("parseTransaction: empty payload");

    const text = new TextDecoder("ascii").decode(bytes);
    if (!text.startsWith("ISA")) {
      throw new Error(
        "parseTransaction: payload does not begin with ISA segment per " +
          "X12 ASC standard",
      );
    }
    if (text.length < 106) {
      throw new Error(
        `parseTransaction: ISA segment too short (got ${text.length} bytes, ` +
          `expected >=106)`,
      );
    }

    const elementSep = text.charAt(3);
    const segmentTerminator = text.charAt(105);

    let normalized = text;
    normalized = normalized.split(segmentTerminator + "\n").join(segmentTerminator);
    normalized = normalized.split(segmentTerminator + "\r\n").join(segmentTerminator);
    normalized = normalized.split(segmentTerminator + "\r").join(segmentTerminator);

    const segments = normalized.split(segmentTerminator).filter((s) => s.trim());
    return this._parseSegments(segments, elementSep, bytes);
  }

  private _parseSegments(
    segments: string[],
    elementSep: string,
    raw: Uint8Array,
  ): X12ServicesReviewTransaction {
    const isaSegment = segments[0];
    const isaFields = isaSegment.split(elementSep);
    const _isa = (idx: number): string =>
      idx < isaFields.length ? isaFields[idx].trim() : "";

    const submitterId = _isa(6);
    const receiverId = _isa(8);
    const isaDate = _isa(9);
    const isaTime = _isa(10);
    const isaControlNumber = _isa(13);

    let gsControlNumber = "";
    for (const seg of segments.slice(1)) {
      if (seg.startsWith("GS")) {
        const gsFields = seg.split(elementSep);
        if (gsFields.length >= 7) gsControlNumber = gsFields[6].trim();
        break;
      }
    }

    let transactionSet = "";
    let stControlNumber = "";
    for (const seg of segments) {
      if (seg.startsWith("ST")) {
        const stFields = seg.split(elementSep);
        if (stFields.length >= 3) {
          transactionSet = stFields[1].trim();
          stControlNumber = stFields[2].trim();
        }
        break;
      }
    }

    if (transactionSet !== X12_278_TRANSACTION_SET) {
      throw new Error(
        `parseTransaction: transaction_set=${JSON.stringify(transactionSet)} ` +
          `not in ${JSON.stringify([X12_278_TRANSACTION_SET])}; expected 278`,
      );
    }

    let direction: X12_278_Direction = "REQUEST";
    let directionFound = false;
    for (const seg of segments) {
      if (seg.startsWith("BHT")) {
        const bhtFields = seg.split(elementSep);
        if (bhtFields.length >= 3) {
          const bht02 = bhtFields[2].trim();
          if (bht02 === "13") {
            direction = "REQUEST";
            directionFound = true;
          } else if (bht02 === "11") {
            direction = "RESPONSE";
            directionFound = true;
          }
        }
        break;
      }
    }
    if (!directionFound && this._strict) {
      throw new Error(
        "parseTransaction [strict]: could not determine direction from " +
          "BHT segment",
      );
    }

    let payerId = "";
    let payerName = "";
    let requestingProviderNpi = "";
    let serviceProviderNpi = "";
    let memberIdHash = "";
    let certificationRequestCategory = "";
    let reviewType = "";
    let certificationAction = "";
    let certificationNumber = "";
    const segmentTypesSeen: string[] = [];

    for (const seg of segments) {
      const segId = seg.split(elementSep, 1)[0];
      if (segId && !segmentTypesSeen.includes(segId)) {
        segmentTypesSeen.push(segId);
      }

      if (seg.startsWith("NM1")) {
        const nm1 = seg.split(elementSep);
        if (nm1.length >= 2) {
          const entityId = nm1[1].trim();
          if (entityId === "PR" || entityId === "X3") {
            if (nm1.length >= 4) payerName = nm1[3].trim();
            if (nm1.length >= 10) payerId = nm1[9].trim();
          } else if (entityId === "1P") {
            if (nm1.length >= 10) requestingProviderNpi = nm1[9].trim();
          } else if (entityId === "SJ") {
            if (nm1.length >= 10) serviceProviderNpi = nm1[9].trim();
          } else if (entityId === "IL") {
            if (nm1.length >= 10) {
              const memberRaw = nm1[9].trim();
              if (memberRaw) {
                memberIdHash = sha256Hex(new TextEncoder().encode(memberRaw));
              }
            }
          }
        }
      } else if (seg.startsWith("UM")) {
        const umFields = seg.split(elementSep);
        if (umFields.length >= 2) reviewType = umFields[1].trim();
        if (umFields.length >= 3) {
          certificationRequestCategory = umFields[2].trim();
        }
      } else if (seg.startsWith("HCR")) {
        const hcrFields = seg.split(elementSep);
        if (hcrFields.length >= 2) {
          certificationAction = hcrFields[1].trim();
        }
        if (hcrFields.length >= 3 && !certificationNumber) {
          certificationNumber = hcrFields[2].trim();
        }
      } else if (seg.startsWith("REF")) {
        const refFields = seg.split(elementSep);
        if (refFields.length >= 3) {
          const qualifier = refFields[1].trim();
          if (qualifier === "BB" || qualifier === "G1") {
            certificationNumber = refFields[2].trim();
          }
        }
      }
    }

    const transactionDatetime =
      isaDate && isaTime ? (isaDate + isaTime).trim() : "";
    const payloadHash = sha256Hex(raw);
    const segmentTypesCsv = segmentTypesSeen.join(",");

    validateValueLength("isa_control_number", isaControlNumber);
    validateValueLength("gs_control_number", gsControlNumber);
    validateValueLength("st_control_number", stControlNumber);
    validateValueLength("submitter_id", submitterId);
    validateValueLength("receiver_id", receiverId);
    validateValueLength("payer_id", payerId);
    validateValueLength("payer_name", payerName);
    validateValueLength("requesting_provider_npi", requestingProviderNpi);
    validateValueLength("service_provider_npi", serviceProviderNpi);
    validateValueLength("certification_number", certificationNumber);

    return {
      transactionSet,
      direction,
      isaControlNumber,
      gsControlNumber,
      stControlNumber,
      submitterId,
      receiverId,
      payerId,
      payerName,
      requestingProviderNpi,
      serviceProviderNpi,
      memberIdHash,
      certificationRequestCategory,
      reviewType,
      certificationAction,
      certificationNumber,
      transactionDatetime,
      payloadHash,
      segmentTypesObserved: segmentTypesCsv,
    };
  }

  /** Parse a 278 REQUEST specifically. */
  parseRequest(raw: Uint8Array | string): X12ServicesReviewTransaction {
    const parsed = this.parseTransaction(raw);
    if (!is278Request(parsed)) {
      throw new Error(
        `parseRequest: direction=${JSON.stringify(parsed.direction)}, expected REQUEST`,
      );
    }
    return parsed;
  }

  /** Parse a 278 RESPONSE specifically. */
  parseResponse(raw: Uint8Array | string): X12ServicesReviewTransaction {
    const parsed = this.parseTransaction(raw);
    if (!is278Response(parsed)) {
      throw new Error(
        `parseResponse: direction=${JSON.stringify(parsed.direction)}, expected RESPONSE`,
      );
    }
    return parsed;
  }

  /** Canonical per-record receipt pattern_tag for PA. */
  static receiptPatternTag(): string {
    return "pa";
  }
}
