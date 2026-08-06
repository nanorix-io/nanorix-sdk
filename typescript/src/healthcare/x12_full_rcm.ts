/**
 * X12 EDI 834/835/837 full revenue-cycle structural adapter.
 *
 * Covers three HIPAA-mandated RCM transaction sets:
 *  - 834 (Benefit Enrollment and Maintenance) per 005010X220A1
 *  - 835 (Health Care Claim Payment/Advice) per 005010X221A1
 *  - 837 (Health Care Claim) — three flavors:
 *    * 837P Professional per 005010X222A1
 *    * 837I Institutional per 005010X223A2
 *    * 837D Dental per 005010X224A2
 *
 * Three-role discipline: structural adapter. Claim
 * adjudication, payment determination, denial-rationale interpretation,
 * payer companion-guide conformance, and downstream RCM workflows
 * remain the customer's responsibility.
 *
 * Customer-sole-declarer: customer supplies the wire payload;
 * adapter parses structural fields only. Member IDs are SHA-256-hashed.
 *
 * Forever-Standard discipline: emits via the existing
 * metadata envelope; per-record receipts use `pattern_tag="rcm_claim"`
 * (837 + 834) or `pattern_tag="rcm_remit"` (835).
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

/** X12 RCM transaction set. */
export type X12RCMTransactionSet = "834" | "835" | "837";

const VALID_TRANSACTION_SETS: ReadonlySet<X12RCMTransactionSet> =
  new Set<X12RCMTransactionSet>(["834", "835", "837"]);

const FRAMEWORK_VERSIONS: Record<string, string> = {
  "834": "x12-005010X220A1",
  "835": "x12-005010X221A1",
  "837P": "x12-005010X222A1",
  "837I": "x12-005010X223A2",
  "837D": "x12-005010X224A2",
};

const FLAVOR_TO_GS08: Record<string, string> = {
  "837P": "005010X222A1",
  "837I": "005010X223A2",
  "837D": "005010X224A2",
};

/** 834 maintenance type codes (INS03). */
export const X12_834_MAINTENANCE_CODES: Record<string, string> = {
  "001": "Change",
  "021": "Addition",
  "024": "Cancel/Termination",
  "025": "Reinstatement",
  "030": "Audit/Compare",
};

/** 835 claim status codes (CLP02). */
export const X12_835_CLAIM_STATUS_CODES: Record<string, string> = {
  "1": "Processed as Primary",
  "2": "Processed as Secondary",
  "3": "Processed as Tertiary",
  "4": "Denied",
  "5": "Pended",
  "19": "Processed as Primary, Forwarded to Additional Payer",
  "20": "Processed as Secondary, Forwarded to Additional Payer",
  "21": "Processed as Tertiary, Forwarded to Additional Payer",
  "22": "Reversal of Previous Payment",
  "23": "Not Our Claim, Forwarded",
  "25": "Predetermination Pricing Only",
};

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for an X12 834/835/837 transaction. */
export interface X12RCMTransaction {
  transactionSet: X12RCMTransactionSet;
  flavor: string;
  frameworkVersion: string;
  isaControlNumber: string;
  gsControlNumber: string;
  stControlNumber: string;
  submitterId: string;
  receiverId: string;
  payerId: string;
  payerName: string;
  providerNpi: string;
  memberIdHash: string;
  claimCount: number;
  totalChargeAmountCents: number;
  totalPaymentAmountCents: number;
  claimStatusDistribution: string;
  maintenanceTypeDistribution: string;
  transactionDatetime: string;
  payloadHash: string;
  segmentTypesObserved: string;
}

/** Whether the transaction is an 837 claim. */
export function isClaim(tx: X12RCMTransaction): boolean {
  return tx.transactionSet === "837";
}

/** Whether the transaction is an 835 remittance. */
export function isRemittance(tx: X12RCMTransaction): boolean {
  return tx.transactionSet === "835";
}

/** Whether the transaction is an 834 enrollment. */
export function isEnrollment(tx: X12RCMTransaction): boolean {
  return tx.transactionSet === "834";
}

/** Whether any 835 claim status indicates denial (CLP02=4). */
export function hasDeniedClaims(tx: X12RCMTransaction): boolean {
  if (!tx.claimStatusDistribution) return false;
  return tx.claimStatusDistribution.split(",").includes("4");
}

/** Convert a parsed RCM transaction to capsule metadata envelope. */
export function x12RcmToMetadata(tx: X12RCMTransaction): Record<string, string> {
  let frameworkLabel = "X12-834";
  if (tx.transactionSet === "835") frameworkLabel = "X12-835";
  else if (tx.transactionSet === "837") frameworkLabel = `X12-837${tx.flavor}`;

  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: frameworkLabel,
    [RESERVED_FRAMEWORK_VERSION_KEY]: tx.frameworkVersion,
    transaction_set: tx.transactionSet,
    isa_control_number: tx.isaControlNumber,
    st_control_number: tx.stControlNumber,
    payload_hash: tx.payloadHash,
    claim_count: String(tx.claimCount),
  };
  if (tx.flavor) meta.flavor = tx.flavor;
  if (tx.payerId) meta.payer_id = tx.payerId;
  if (tx.payerName) meta.payer_name = tx.payerName;
  if (tx.providerNpi) meta.provider_npi = tx.providerNpi;
  if (tx.memberIdHash) meta.member_id_hash = tx.memberIdHash;
  if (tx.totalChargeAmountCents) {
    meta.total_charge_amount_cents = String(tx.totalChargeAmountCents);
  }
  if (tx.totalPaymentAmountCents) {
    meta.total_payment_amount_cents = String(tx.totalPaymentAmountCents);
  }
  if (tx.claimStatusDistribution) {
    meta.claim_status_distribution = tx.claimStatusDistribution;
  }
  if (tx.maintenanceTypeDistribution) {
    meta.maintenance_type_distribution = tx.maintenanceTypeDistribution;
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

function dollarsToCents(amount: string): number {
  if (!amount) return 0;
  const [dollarsStr, centsStr = ""] = amount.split(".", 2);
  const dollars = Number.parseInt(dollarsStr, 10);
  if (Number.isNaN(dollars)) return 0;
  const cents = Number.parseInt((centsStr + "00").slice(0, 2), 10);
  if (Number.isNaN(cents)) return 0;
  return dollars * 100 + cents;
}

/** X12 EDI 834/835/837 full RCM structural adapter. */
export class X12FullRCMAdapter {
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

  get supported837Flavors(): string[] {
    return ["D", "I", "P"];
  }

  get claimStatusCodes(): Record<string, string> {
    return { ...X12_835_CLAIM_STATUS_CODES };
  }

  get maintenanceCodes(): Record<string, string> {
    return { ...X12_834_MAINTENANCE_CODES };
  }

  /** Parse a single X12 834, 835, or 837 transaction. */
  parseTransaction(raw: Uint8Array | string): X12RCMTransaction {
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
  ): X12RCMTransaction {
    const isaFields = segments[0].split(elementSep);
    const _isa = (idx: number): string =>
      idx < isaFields.length ? isaFields[idx].trim() : "";

    const submitterId = _isa(6);
    const receiverId = _isa(8);
    const isaDate = _isa(9);
    const isaTime = _isa(10);
    const isaControlNumber = _isa(13);

    let gsControlNumber = "";
    let gs08 = "";
    for (const seg of segments.slice(1)) {
      if (seg.startsWith("GS")) {
        const gs = seg.split(elementSep);
        if (gs.length >= 7) gsControlNumber = gs[6].trim();
        if (gs.length >= 9) gs08 = gs[8].trim();
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

    if (!VALID_TRANSACTION_SETS.has(transactionSet as X12RCMTransactionSet)) {
      throw new Error(
        `parseTransaction: transaction_set=${JSON.stringify(transactionSet)} ` +
          `not in ${JSON.stringify(Array.from(VALID_TRANSACTION_SETS).sort())}; ` +
          `expected 834, 835, or 837`,
      );
    }

    let flavor = "";
    let frameworkVersion = FRAMEWORK_VERSIONS[transactionSet] ?? "";
    if (transactionSet === "837") {
      for (const [flav, ig] of Object.entries(FLAVOR_TO_GS08)) {
        if (gs08.includes(ig)) {
          flavor = flav.replace("837", "");
          frameworkVersion = FRAMEWORK_VERSIONS[flav];
          break;
        }
      }
      if (!flavor && this._strict) {
        throw new Error(
          `parseTransaction [strict]: 837 flavor could not be determined ` +
            `from GS08=${JSON.stringify(gs08)}`,
        );
      }
      if (!flavor) {
        flavor = "P";
        frameworkVersion = FRAMEWORK_VERSIONS["837P"];
      }
    }

    let payerId = "";
    let payerName = "";
    let providerNpi = "";
    let memberIdHash = "";
    let claimCount = 0;
    let totalChargeCents = 0;
    let totalPaymentCents = 0;
    const claimStatusSeen: string[] = [];
    const maintenanceSeen: string[] = [];
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
          } else if (entityId === "85") {
            if (nm1.length >= 10) providerNpi = nm1[9].trim();
          } else if (entityId === "BL" && !providerNpi) {
            if (nm1.length >= 10) providerNpi = nm1[9].trim();
          } else if (entityId === "IL" && !memberIdHash) {
            if (nm1.length >= 10) {
              const raw = nm1[9].trim();
              if (raw) memberIdHash = sha256Hex(new TextEncoder().encode(raw));
            }
          } else if (entityId === "QC" && !memberIdHash) {
            if (nm1.length >= 10) {
              const raw = nm1[9].trim();
              if (raw) memberIdHash = sha256Hex(new TextEncoder().encode(raw));
            }
          }
        }
      } else if (seg.startsWith("N1")) {
        const n1 = seg.split(elementSep);
        if (n1.length >= 2) {
          const entityId = n1[1].trim();
          if (entityId === "PR" && !payerId) {
            if (n1.length >= 3) payerName = n1[2].trim();
            if (n1.length >= 5) payerId = n1[4].trim();
          } else if (entityId === "PE" && !providerNpi) {
            if (n1.length >= 5) providerNpi = n1[4].trim();
          }
        }
      } else if (seg.startsWith("CLM") && transactionSet === "837") {
        claimCount += 1;
        const clm = seg.split(elementSep);
        if (clm.length >= 3) {
          totalChargeCents += dollarsToCents(clm[2].trim());
        }
      } else if (seg.startsWith("CLP") && transactionSet === "835") {
        claimCount += 1;
        const clp = seg.split(elementSep);
        if (clp.length >= 3) {
          const statusCode = clp[2].trim();
          if (statusCode && !claimStatusSeen.includes(statusCode)) {
            claimStatusSeen.push(statusCode);
          }
        }
        if (clp.length >= 5) {
          totalPaymentCents += dollarsToCents(clp[4].trim());
        }
      } else if (seg.startsWith("INS") && transactionSet === "834") {
        claimCount += 1;
        const ins = seg.split(elementSep);
        if (ins.length >= 4) {
          const maintCode = ins[3].trim();
          if (maintCode && !maintenanceSeen.includes(maintCode)) {
            maintenanceSeen.push(maintCode);
          }
        }
      }
    }

    const transactionDatetime =
      isaDate && isaTime ? (isaDate + isaTime).trim() : "";
    const payloadHash = sha256Hex(raw);

    validateValueLength("isa_control_number", isaControlNumber);
    validateValueLength("gs_control_number", gsControlNumber);
    validateValueLength("st_control_number", stControlNumber);
    validateValueLength("payer_id", payerId);
    validateValueLength("payer_name", payerName);
    validateValueLength("provider_npi", providerNpi);

    return {
      transactionSet: transactionSet as X12RCMTransactionSet,
      flavor,
      frameworkVersion,
      isaControlNumber,
      gsControlNumber,
      stControlNumber,
      submitterId,
      receiverId,
      payerId,
      payerName,
      providerNpi,
      memberIdHash,
      claimCount,
      totalChargeAmountCents: totalChargeCents,
      totalPaymentAmountCents: totalPaymentCents,
      claimStatusDistribution: claimStatusSeen.join(","),
      maintenanceTypeDistribution: maintenanceSeen.join(","),
      transactionDatetime,
      payloadHash,
      segmentTypesObserved: segmentTypesSeen.join(","),
    };
  }

  /** Parse an 834 enrollment transaction specifically. */
  parse834(raw: Uint8Array | string): X12RCMTransaction {
    const parsed = this.parseTransaction(raw);
    if (!isEnrollment(parsed)) {
      throw new Error(
        `parse834: transactionSet=${JSON.stringify(parsed.transactionSet)}, expected 834`,
      );
    }
    return parsed;
  }

  /** Parse an 835 remittance transaction specifically. */
  parse835(raw: Uint8Array | string): X12RCMTransaction {
    const parsed = this.parseTransaction(raw);
    if (!isRemittance(parsed)) {
      throw new Error(
        `parse835: transactionSet=${JSON.stringify(parsed.transactionSet)}, expected 835`,
      );
    }
    return parsed;
  }

  /** Parse an 837 claim transaction specifically. */
  parse837(raw: Uint8Array | string): X12RCMTransaction {
    const parsed = this.parseTransaction(raw);
    if (!isClaim(parsed)) {
      throw new Error(
        `parse837: transactionSet=${JSON.stringify(parsed.transactionSet)}, expected 837`,
      );
    }
    return parsed;
  }

  /** Canonical per-record receipt pattern_tag for an RCM transaction set. */
  static receiptPatternTag(transactionSet: string): string {
    if (transactionSet === "837") return "rcm_claim";
    if (transactionSet === "835") return "rcm_remit";
    if (transactionSet === "834") return "rcm_claim";
    throw new Error(
      `receiptPatternTag: unsupported transaction_set=${JSON.stringify(transactionSet)}; ` +
        `expected 834/835/837`,
    );
  }
}
