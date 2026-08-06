/**
 * NCPDP SCRIPT + Telecom Standard structural adapter for PBM
 * e-prescription workloads.
 *
 * Covers two NCPDP families:
 *  - SCRIPT v2017071 — electronic prescription routing (NEWRX, REFREQ/
 *    REFRES, RXCHG/CHGRES, CANRX/CANRES, RXFILL, RXHREQ/RXHRES,
 *    VERIFY/VERRES, ERROR, STATUS, PASSWORD).
 *  - Telecom Standard vD.0 — pharmacy claim billing (B1/B2/B3),
 *    eligibility (E1), info reporting (N1/N2/N3), prior auth (P1-P4),
 *    controlled substance reporting (C1/C2/C3).
 *
 * Three-role discipline (ADR-017): structural adapter. Schedule II-V
 * classification, DEA registration verification, prescriber authority,
 * formulary determination, and downstream PBM adjudication remain the
 * customer's responsibility.
 *
 * Customer-sole-declarer (ADR-013): customer supplies the wire
 * payload; adapter parses structural fields only.
 *
 * Forever-Standard discipline (ADR-006 I0): emits via the existing
 * metadata envelope. Per-record receipt emission uses
 * `pattern_tag="ncpdp_script"` per ADR-039.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION_SCRIPT = "ncpdp-script-2017071";
const FRAMEWORK_VERSION_TELECOM = "ncpdp-telecom-D.0";

/** NCPDP transaction family. */
export type NcpdpTransactionFamily = "SCRIPT" | "TELECOM";

const VALID_SCRIPT_TRANSACTIONS: ReadonlySet<string> = new Set<string>([
  "NEWRX",
  "REFREQ",
  "REFRES",
  "RXCHG",
  "CHGRES",
  "CANRX",
  "CANRES",
  "RXFILL",
  "RXHREQ",
  "RXHRES",
  "VERIFY",
  "VERRES",
  "ERROR",
  "STATUS",
  "PASSWORD",
]);

const VALID_TELECOM_TRANSACTIONS: ReadonlySet<string> = new Set<string>([
  "B1",
  "B2",
  "B3",
  "E1",
  "N1",
  "N2",
  "N3",
  "P1",
  "P2",
  "P3",
  "P4",
  "C1",
  "C2",
  "C3",
]);

/** DEA controlled-substance schedule per 21 CFR 1308. */
export type DeaSchedule = "CII" | "CIII" | "CIV" | "CV";

const VALID_DEA_SCHEDULES: ReadonlySet<DeaSchedule> =
  new Set<DeaSchedule>(["CII", "CIII", "CIV", "CV"]);

const FIELD_SEP = "|";
const SEGMENT_SEP = "\x1e";
const GROUP_SEP = "\x1d";

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for an NCPDP transaction. */
export interface NCPDPParsedTransaction {
  transactionCode: string;
  transactionFamily: NcpdpTransactionFamily;
  messageControlId: string;
  sendingApplication: string;
  receivingApplication: string;
  prescriberId: string;
  pharmacyNcpdpId: string;
  transactionDatetime: string;
  payloadHash: string;
  deaSchedule: string;
  fieldCount: number;
}

/** Whether the customer declared a DEA-controlled substance. */
export function isControlledSubstance(tx: NCPDPParsedTransaction): boolean {
  return VALID_DEA_SCHEDULES.has(tx.deaSchedule as DeaSchedule);
}

/** Convert a parsed NCPDP transaction to capsule metadata envelope. */
export function ncpdpToMetadata(
  tx: NCPDPParsedTransaction,
): Record<string, string> {
  const frameworkVersion =
    tx.transactionFamily === "SCRIPT"
      ? FRAMEWORK_VERSION_SCRIPT
      : FRAMEWORK_VERSION_TELECOM;
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "NCPDP",
    [RESERVED_FRAMEWORK_VERSION_KEY]: frameworkVersion,
    transaction_code: tx.transactionCode,
    transaction_family: tx.transactionFamily,
    message_control_id: tx.messageControlId,
    sending_application: tx.sendingApplication,
    receiving_application: tx.receivingApplication,
    prescriber_id: tx.prescriberId,
    pharmacy_ncpdp_id: tx.pharmacyNcpdpId,
    transaction_datetime: tx.transactionDatetime,
    payload_hash: tx.payloadHash,
    field_count: String(tx.fieldCount),
  };
  if (tx.deaSchedule) meta.dea_schedule = tx.deaSchedule;

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

const SCRIPT_ELEMENT_TO_CODE: Record<string, string> = {
  NewRx: "NEWRX",
  RefillRequest: "REFREQ",
  RefillResponse: "REFRES",
  RxChangeRequest: "RXCHG",
  RxChangeResponse: "CHGRES",
  CancelRx: "CANRX",
  CancelRxResponse: "CANRES",
  RxFill: "RXFILL",
  RxHistoryRequest: "RXHREQ",
  RxHistoryResponse: "RXHRES",
  Verify: "VERIFY",
  VerifyResponse: "VERRES",
  Error: "ERROR",
  Status: "STATUS",
  Password: "PASSWORD",
};

/** NCPDP SCRIPT + Telecom structural adapter. */
export class NCPDPAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedScriptTransactions(): string[] {
    return Array.from(VALID_SCRIPT_TRANSACTIONS).sort();
  }

  get supportedTelecomTransactions(): string[] {
    return Array.from(VALID_TELECOM_TRANSACTIONS).sort();
  }

  get supportedDeaSchedules(): string[] {
    return Array.from(VALID_DEA_SCHEDULES).sort();
  }

  /** Parse a single NCPDP transaction. */
  parseTransaction(raw: Uint8Array | string): NCPDPParsedTransaction {
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

    const text = new TextDecoder("ascii").decode(bytes).trim();
    if (!text) {
      throw new Error(
        "parseTransaction: payload is empty after whitespace strip",
      );
    }

    const fields = text.startsWith("<")
      ? this._parseXml(text)
      : this._parseDelimited(text);

    return this._buildParsed(fields, bytes);
  }

  private _parseXml(text: string): Record<string, string> {
    const fields: Record<string, string> = {};

    let transactionCode = "";
    for (const [elName, code] of Object.entries(SCRIPT_ELEMENT_TO_CODE)) {
      if (text.includes(`<${elName}>`) || text.includes(`<${elName} `)) {
        transactionCode = code;
        break;
      }
    }
    fields.transaction_code = transactionCode;
    fields.transaction_family = "SCRIPT";

    fields.message_control_id =
      this._extractTagText(text, "PrescriberOrderNumber");
    fields.sending_application =
      this._extractTagText(text, "From") ||
      this._extractTagText(text, "SenderID");
    fields.receiving_application =
      this._extractTagText(text, "To") ||
      this._extractTagText(text, "ReceiverID");
    fields.prescriber_id =
      this._extractTagText(text, "Identifier") ||
      this._extractTagText(text, "NPI");
    fields.pharmacy_ncpdp_id =
      this._extractTagText(text, "NCPDPID") ||
      this._extractTagText(text, "PharmacyID");
    fields.transaction_datetime =
      this._extractTagText(text, "TimeStamp") ||
      this._extractTagText(text, "SentTime");
    fields.dea_schedule = this._extractTagText(text, "DEASchedule");
    fields.field_count_raw = String((text.match(/</g) ?? []).length);
    return fields;
  }

  private _parseDelimited(text: string): Record<string, string> {
    const normalized = text
      .replace(new RegExp(SEGMENT_SEP, "g"), "\n")
      .replace(new RegExp(GROUP_SEP, "g"), "\n")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n");
    const segments = normalized.split("\n").filter((s) => s.trim());
    if (segments.length === 0) {
      throw new Error(
        "parseTransaction: no segments after normalization",
      );
    }

    const header = segments[0];
    const headerFields = header.split(FIELD_SEP);
    if (headerFields.length === 0) {
      throw new Error(
        "parseTransaction: header segment is empty after split",
      );
    }

    let transactionCode = "";
    let transactionFamily = "";
    if (headerFields.length >= 2) {
      const candidate = headerFields[1].trim().toUpperCase();
      if (VALID_TELECOM_TRANSACTIONS.has(candidate)) {
        transactionCode = candidate;
        transactionFamily = "TELECOM";
      } else if (VALID_SCRIPT_TRANSACTIONS.has(candidate)) {
        transactionCode = candidate;
        transactionFamily = "SCRIPT";
      }
    }
    if (!transactionCode && headerFields.length >= 1) {
      const candidate = headerFields[0].trim().toUpperCase();
      if (VALID_TELECOM_TRANSACTIONS.has(candidate)) {
        transactionCode = candidate;
        transactionFamily = "TELECOM";
      } else if (VALID_SCRIPT_TRANSACTIONS.has(candidate)) {
        transactionCode = candidate;
        transactionFamily = "SCRIPT";
      }
    }

    const kvLookup = (key: string): string => {
      const keyLower = key.toLowerCase();
      for (const seg of segments) {
        for (const fld of seg.split(FIELD_SEP)) {
          const eqIdx = fld.indexOf("=");
          if (eqIdx >= 0) {
            const k = fld.slice(0, eqIdx).trim().toLowerCase();
            const v = fld.slice(eqIdx + 1).trim();
            if (k === keyLower) return v;
          }
        }
      }
      return "";
    };

    let fieldCount = 0;
    for (const seg of segments) {
      fieldCount += seg.split(FIELD_SEP).length;
    }

    return {
      transaction_code: transactionCode,
      transaction_family: transactionFamily,
      message_control_id:
        kvLookup("messagecontrolid") ||
        kvLookup("prescribernumber") ||
        kvLookup("prescriptionnumber") ||
        kvLookup("rxnumber"),
      sending_application:
        kvLookup("sendingapplication") ||
        kvLookup("senderid") ||
        kvLookup("from"),
      receiving_application:
        kvLookup("receivingapplication") ||
        kvLookup("receiverid") ||
        kvLookup("to"),
      prescriber_id: kvLookup("prescriberid") || kvLookup("npi"),
      pharmacy_ncpdp_id:
        kvLookup("pharmacyncpdpid") ||
        kvLookup("ncpdpid") ||
        kvLookup("nabpnumber"),
      transaction_datetime:
        kvLookup("transactiondatetime") ||
        kvLookup("timestamp") ||
        kvLookup("senttime"),
      dea_schedule: kvLookup("deaschedule") || kvLookup("schedule"),
      field_count_raw: String(fieldCount),
    };
  }

  private _extractTagText(text: string, tag: string): string {
    const openTag = `<${tag}`;
    const closeTag = `</${tag}>`;
    const start = text.indexOf(openTag);
    if (start === -1) return "";
    const gt = text.indexOf(">", start);
    if (gt === -1) return "";
    if (text.charAt(gt - 1) === "/") return "";
    const end = text.indexOf(closeTag, gt + 1);
    if (end === -1) return "";
    return text.slice(gt + 1, end).trim();
  }

  private _buildParsed(
    fields: Record<string, string>,
    raw: Uint8Array,
  ): NCPDPParsedTransaction {
    const transactionCode = fields.transaction_code ?? "";
    if (!transactionCode) {
      throw new Error(
        "parseTransaction: could not determine transaction_code",
      );
    }
    let transactionFamily = fields.transaction_family ?? "";
    if (VALID_SCRIPT_TRANSACTIONS.has(transactionCode)) {
      transactionFamily = transactionFamily || "SCRIPT";
    } else if (VALID_TELECOM_TRANSACTIONS.has(transactionCode)) {
      transactionFamily = transactionFamily || "TELECOM";
    } else {
      throw new Error(
        `parseTransaction: transaction_code=${JSON.stringify(transactionCode)} ` +
          `not in NCPDP SCRIPT or Telecom closed enum`,
      );
    }

    const messageControlId = fields.message_control_id ?? "";
    if (this._strict && !messageControlId) {
      throw new Error(
        "parseTransaction [strict]: message_control_id is empty",
      );
    }

    let deaSchedule = (fields.dea_schedule ?? "").toUpperCase();
    if (deaSchedule && !VALID_DEA_SCHEDULES.has(deaSchedule as DeaSchedule)) {
      if (this._strict) {
        throw new Error(
          `parseTransaction [strict]: dea_schedule=${JSON.stringify(deaSchedule)} ` +
            `not in ${JSON.stringify(Array.from(VALID_DEA_SCHEDULES).sort())}`,
        );
      }
      deaSchedule = "";
    }

    const payloadHash = sha256Hex(raw);
    const fieldCount = Number.parseInt(fields.field_count_raw ?? "0", 10) || 0;

    validateValueLength("transaction_code", transactionCode);
    validateValueLength("message_control_id", messageControlId);
    validateValueLength(
      "sending_application",
      fields.sending_application ?? "",
    );
    validateValueLength(
      "receiving_application",
      fields.receiving_application ?? "",
    );
    validateValueLength("prescriber_id", fields.prescriber_id ?? "");
    validateValueLength(
      "pharmacy_ncpdp_id",
      fields.pharmacy_ncpdp_id ?? "",
    );
    validateValueLength(
      "transaction_datetime",
      fields.transaction_datetime ?? "",
    );

    return {
      transactionCode,
      transactionFamily: transactionFamily as NcpdpTransactionFamily,
      messageControlId,
      sendingApplication: fields.sending_application ?? "",
      receivingApplication: fields.receiving_application ?? "",
      prescriberId: fields.prescriber_id ?? "",
      pharmacyNcpdpId: fields.pharmacy_ncpdp_id ?? "",
      transactionDatetime: fields.transaction_datetime ?? "",
      payloadHash,
      deaSchedule,
      fieldCount,
    };
  }

  /** Parse a SCRIPT-family transaction specifically. */
  parseScript(raw: Uint8Array | string): NCPDPParsedTransaction {
    const parsed = this.parseTransaction(raw);
    if (parsed.transactionFamily !== "SCRIPT") {
      throw new Error(
        `parseScript: transactionFamily=${JSON.stringify(parsed.transactionFamily)}, ` +
          `expected SCRIPT`,
      );
    }
    return parsed;
  }

  /** Parse a Telecom-family transaction specifically. */
  parseTelecom(raw: Uint8Array | string): NCPDPParsedTransaction {
    const parsed = this.parseTransaction(raw);
    if (parsed.transactionFamily !== "TELECOM") {
      throw new Error(
        `parseTelecom: transactionFamily=${JSON.stringify(parsed.transactionFamily)}, ` +
          `expected TELECOM`,
      );
    }
    return parsed;
  }

  /** Canonical per-record receipt pattern_tag for NCPDP. */
  static receiptPatternTag(): string {
    return "ncpdp_script";
  }
}
