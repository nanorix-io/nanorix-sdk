/**
 * X12 EDI 270/271 eligibility verification structural adapter.
 *
 * X12 EDI 270 (Health Care Eligibility Benefit Inquiry) and X12 EDI 271
 * (Health Care Eligibility Benefit Response) are the HIPAA-mandated
 * transaction sets for eligibility verification across the US healthcare
 * payer ecosystem. Parses customer-supplied X12 v5010 wire payloads
 * (005010X279A1 TR3 IG) and produces structural metadata + payload
 * SHA-256 anchor.
 *
 * Three-role discipline: structural adapter. Coverage
 * interpretation, payer companion-guide conformance, member-eligibility
 * determination, and downstream RCM adjudication remain the customer's
 * responsibility.
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

const FRAMEWORK_VERSION = "x12-005010X279A1";

/** X12 270/271 transaction set identifiers. */
export type X12EligibilityTransactionSet = "270" | "271";

const VALID_TRANSACTION_SETS: ReadonlySet<X12EligibilityTransactionSet> =
  new Set<X12EligibilityTransactionSet>(["270", "271"]);

/** Common X12 service-type codes (curated subset). */
export const X12_270_COMMON_SERVICE_TYPES: Record<string, string> = {
  "1": "Medical Care",
  "30": "Health Benefit Plan Coverage",
  "33": "Chiropractic",
  "35": "Dental Care",
  "47": "Hospital",
  "48": "Hospital - Inpatient",
  "50": "Hospital - Outpatient",
  "51": "Hospital - Emergency Accident",
  "52": "Hospital - Emergency Medical",
  "53": "Hospital - Ambulatory Surgical",
  "62": "MRI/CAT Scan",
  "65": "Newborn Care",
  "68": "Well Baby Care",
  "73": "Diagnostic Medical",
  "76": "Dialysis",
  "78": "Chemotherapy",
  "80": "Immunizations",
  "81": "Routine Physical",
  "82": "Family Planning",
  "86": "Emergency Services",
  "88": "Pharmacy",
  "93": "Podiatry",
  "98": "Professional (Physician) Visit - Office",
  "99": "Professional (Physician) Visit - Inpatient",
  AL: "Vision (Optometry)",
  MH: "Mental Health",
  UC: "Urgent Care",
};

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for an X12 270 or 271 transaction. */
export interface X12EligibilityTransaction {
  transactionSet: X12EligibilityTransactionSet;
  isaControlNumber: string;
  gsControlNumber: string;
  stControlNumber: string;
  submitterId: string;
  receiverId: string;
  payerId: string;
  payerName: string;
  memberIdHash: string;
  serviceTypeCodes: string;
  transactionDatetime: string;
  payloadHash: string;
  segmentTypesObserved: string;
}

/** Whether the transaction is a 270 inquiry. */
export function is270Inquiry(tx: X12EligibilityTransaction): boolean {
  return tx.transactionSet === "270";
}

/** Whether the transaction is a 271 response. */
export function is271Response(tx: X12EligibilityTransaction): boolean {
  return tx.transactionSet === "271";
}

/** Convert a parsed transaction to capsule metadata envelope. */
export function x12EligibilityToMetadata(
  tx: X12EligibilityTransaction,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "X12-270-271",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    transaction_set: tx.transactionSet,
    isa_control_number: tx.isaControlNumber,
    gs_control_number: tx.gsControlNumber,
    st_control_number: tx.stControlNumber,
    submitter_id: tx.submitterId,
    receiver_id: tx.receiverId,
    payload_hash: tx.payloadHash,
  };
  if (tx.payerId) meta.payer_id = tx.payerId;
  if (tx.payerName) meta.payer_name = tx.payerName;
  if (tx.memberIdHash) meta.member_id_hash = tx.memberIdHash;
  if (tx.serviceTypeCodes) meta.service_type_codes = tx.serviceTypeCodes;
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

/** X12 EDI 270/271 structural adapter. */
export class X12EligibilityAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedTransactionSets(): string[] {
    return Array.from(VALID_TRANSACTION_SETS).sort();
  }

  get commonServiceTypes(): Record<string, string> {
    return { ...X12_270_COMMON_SERVICE_TYPES };
  }

  /** Parse a single X12 270 or 271 transaction. */
  parseTransaction(raw: Uint8Array | string): X12EligibilityTransaction {
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
    const subelementSep = text.charAt(104);
    const segmentTerminator = text.charAt(105);

    if (
      (elementSep === "A" || elementSep === "B" || elementSep === "C") ||
      (subelementSep === "A" || subelementSep === "B")
    ) {
      if (this._strict) {
        throw new Error(
          `parseTransaction [strict]: ISA separators look malformed ` +
            `(elem=${JSON.stringify(elementSep)}, subelem=${JSON.stringify(subelementSep)})`,
        );
      }
    }

    let normalized = text;
    normalized = normalized.split(segmentTerminator + "\n").join(segmentTerminator);
    normalized = normalized.split(segmentTerminator + "\r\n").join(segmentTerminator);
    normalized = normalized.split(segmentTerminator + "\r").join(segmentTerminator);
    const segments = normalized.split(segmentTerminator).filter((s) => s.trim());

    return this._parseSegments(segments, elementSep, subelementSep, bytes);
  }

  private _parseSegments(
    segments: string[],
    elementSep: string,
    subelementSep: string,
    raw: Uint8Array,
  ): X12EligibilityTransaction {
    const isaFields = segments[0].split(elementSep);
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
        const gs = seg.split(elementSep);
        if (gs.length >= 7) gsControlNumber = gs[6].trim();
        break;
      }
    }

    let transactionSet = "";
    let stControlNumber = "";
    for (const seg of segments) {
      if (seg.startsWith("ST")) {
        const st = seg.split(elementSep);
        if (st.length >= 3) {
          transactionSet = st[1].trim();
          stControlNumber = st[2].trim();
        }
        break;
      }
    }

    if (
      !VALID_TRANSACTION_SETS.has(transactionSet as X12EligibilityTransactionSet)
    ) {
      throw new Error(
        `parseTransaction: transaction_set=${JSON.stringify(transactionSet)} ` +
          `not in ${JSON.stringify(Array.from(VALID_TRANSACTION_SETS).sort())}; ` +
          `expected 270 or 271`,
      );
    }

    let payerId = "";
    let payerName = "";
    let memberIdHash = "";
    const serviceTypeCodes: string[] = [];
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
          if (entityId === "PR") {
            if (nm1.length >= 4) payerName = nm1[3].trim();
            if (nm1.length >= 10) payerId = nm1[9].trim();
          } else if (entityId === "IL") {
            if (nm1.length >= 10) {
              const memberRaw = nm1[9].trim();
              if (memberRaw) {
                memberIdHash = sha256Hex(new TextEncoder().encode(memberRaw));
              }
            }
          }
        }
      } else if (seg.startsWith("EQ")) {
        const eqFields = seg.split(elementSep);
        if (eqFields.length >= 2) {
          const eq01 = eqFields[1].trim();
          for (const stc of eq01.split(subelementSep)) {
            const trimmed = stc.trim();
            if (trimmed && !serviceTypeCodes.includes(trimmed)) {
              serviceTypeCodes.push(trimmed);
            }
          }
        }
      }
    }

    if (!isaControlNumber && this._strict) {
      throw new Error(
        "parseTransaction [strict]: ISA13 control number is empty",
      );
    }

    const transactionDatetime =
      isaDate && isaTime ? (isaDate + isaTime).trim() : "";
    const payloadHash = sha256Hex(raw);

    validateValueLength("isa_control_number", isaControlNumber);
    validateValueLength("gs_control_number", gsControlNumber);
    validateValueLength("st_control_number", stControlNumber);
    validateValueLength("submitter_id", submitterId);
    validateValueLength("receiver_id", receiverId);
    validateValueLength("payer_id", payerId);
    validateValueLength("payer_name", payerName);
    validateValueLength("transaction_datetime", transactionDatetime);

    return {
      transactionSet: transactionSet as X12EligibilityTransactionSet,
      isaControlNumber,
      gsControlNumber,
      stControlNumber,
      submitterId,
      receiverId,
      payerId,
      payerName,
      memberIdHash,
      serviceTypeCodes: serviceTypeCodes.join(","),
      transactionDatetime,
      payloadHash,
      segmentTypesObserved: segmentTypesSeen.join(","),
    };
  }

  /** Parse a 270 inquiry specifically. */
  parse270(raw: Uint8Array | string): X12EligibilityTransaction {
    const parsed = this.parseTransaction(raw);
    if (parsed.transactionSet !== "270") {
      throw new Error(
        `parse270: transactionSet=${JSON.stringify(parsed.transactionSet)}, expected 270`,
      );
    }
    return parsed;
  }

  /** Parse a 271 response specifically. */
  parse271(raw: Uint8Array | string): X12EligibilityTransaction {
    const parsed = this.parseTransaction(raw);
    if (parsed.transactionSet !== "271") {
      throw new Error(
        `parse271: transactionSet=${JSON.stringify(parsed.transactionSet)}, expected 271`,
      );
    }
    return parsed;
  }

  /** Canonical per-record receipt pattern_tag for eligibility. */
  static receiptPatternTag(): string {
    return "rcm_eligibility";
  }
}
