/**
 * HL7 V2 ADT/ORU message structural adapter for capsule workloads.
 *
 * Parses incoming HL7 V2 ADT (Admission/Discharge/Transfer) and ORU
 * (Observation Result) messages — the two highest-volume HL7 V2
 * families in US hospital integration environments through 2026.
 *
 * Three-role discipline (ADR-017): structural adapter; clinical
 * interpretation, IG conformance, and PHI classification inside
 * segments remain the customer's responsibility. Adapter does NOT
 * certify upstream-system conformance.
 *
 * Customer-sole-declarer (ADR-013): customer supplies message bytes;
 * adapter parses MSH structural identifiers only.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "hl7-v2.5";

/** HL7 V2 message families covered. */
export type Hl7V2MessageType = "ADT" | "ORU";

const VALID_MESSAGE_TYPES: ReadonlySet<Hl7V2MessageType> =
  new Set<Hl7V2MessageType>(["ADT", "ORU"]);

const VALID_ADT_TRIGGERS: ReadonlySet<string> = new Set<string>([
  "A01",
  "A02",
  "A03",
  "A04",
  "A05",
  "A06",
  "A07",
  "A08",
  "A11",
  "A12",
  "A13",
]);

const VALID_ORU_TRIGGERS: ReadonlySet<string> = new Set<string>([
  "R01",
  "R03",
  "R30",
  "R31",
  "R32",
]);

const DEFAULT_FIELD_SEP = "|";
const DEFAULT_COMPONENT_SEP = "^";

function sha256Hex(input: string | Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for an HL7 V2 message. */
export interface HL7v2ParsedMessage {
  messageType: Hl7V2MessageType;
  triggerEvent: string;
  messageControlId: string;
  sendingApplication: string;
  receivingApplication: string;
  sendingFacility: string;
  receivingFacility: string;
  messageDatetime: string;
  processingId: string;
  versionId: string;
  mshSegmentHash: string;
  segmentTypesObserved: ReadonlyArray<string>;
}

/** Canonical message-type-with-trigger identifier (e.g., `"ADT^A01"`). */
export function messageTypeWithTrigger(m: HL7v2ParsedMessage): string {
  return `${m.messageType}^${m.triggerEvent}`;
}

/** Convert a parsed HL7 V2 message to capsule metadata envelope. */
export function parsedMessageToMetadata(
  m: HL7v2ParsedMessage,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "HL7V2",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    message_type: m.messageType,
    trigger_event: m.triggerEvent,
    message_control_id: m.messageControlId,
    sending_application: m.sendingApplication,
    receiving_application: m.receivingApplication,
    sending_facility: m.sendingFacility,
    receiving_facility: m.receivingFacility,
    message_datetime: m.messageDatetime,
    processing_id: m.processingId,
    version_id: m.versionId,
    msh_segment_hash: m.mshSegmentHash,
  };
  if (m.segmentTypesObserved.length > 0) {
    meta.segment_types_observed = m.segmentTypesObserved.join(",");
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

/** HL7 V2 ADT/ORU message structural adapter. */
export class HL7v2Adapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedMessageTypes(): string[] {
    return Array.from(VALID_MESSAGE_TYPES).sort();
  }

  get supportedAdtTriggers(): string[] {
    return Array.from(VALID_ADT_TRIGGERS).sort();
  }

  get supportedOruTriggers(): string[] {
    return Array.from(VALID_ORU_TRIGGERS).sort();
  }

  /** Parse a single HL7 V2 message. */
  parseMessage(raw: Uint8Array | string): HL7v2ParsedMessage {
    let text: string;
    if (typeof raw === "string") {
      text = raw;
    } else if (raw instanceof Uint8Array) {
      text = new TextDecoder("ascii").decode(raw);
    } else {
      throw new Error(
        `parseMessage expects Uint8Array or string (got ${typeof raw})`,
      );
    }
    if (!text) throw new Error("parseMessage: empty message bytes");

    const normalized = text.replace(/\r\n/g, "\r").replace(/\n/g, "\r");
    const segments = normalized.split("\r").filter((s) => s);
    if (segments.length === 0) {
      throw new Error("parseMessage: no segments found");
    }

    const msh = segments[0];
    if (!msh.startsWith("MSH")) {
      throw new Error(
        `parseMessage: first segment is not MSH (got ${JSON.stringify(msh.slice(0, 3))}); ` +
          `HL7 V2 messages must start with MSH`,
      );
    }

    return this._parseMsh(msh, segments);
  }

  private _parseMsh(msh: string, segments: string[]): HL7v2ParsedMessage {
    if (msh.length < 5) {
      throw new Error(
        "parseMessage: MSH segment too short to contain field separator",
      );
    }
    const fieldSep = msh.charAt(3);
    if (fieldSep !== DEFAULT_FIELD_SEP && this._strict) {
      throw new Error(
        `parseMessage [strict]: non-standard field separator ${JSON.stringify(fieldSep)}; ` +
          `expected ${JSON.stringify(DEFAULT_FIELD_SEP)}`,
      );
    }

    const fields = msh.split(fieldSep);
    const _get = (idx: number): string =>
      idx < fields.length ? fields[idx].trim() : "";

    const sendingApp = _get(2);
    const sendingFacility = _get(3);
    const receivingApp = _get(4);
    const receivingFacility = _get(5);
    const messageDatetime = _get(6);
    const msh9 = _get(8);
    const messageControlId = _get(9);
    const processingId = _get(10);
    const versionId = _get(11);

    if (!msh9) {
      throw new Error(
        "parseMessage: MSH-9 (message type) is empty; cannot determine " +
          "message type or trigger event",
      );
    }
    const msh9Parts = msh9.split(DEFAULT_COMPONENT_SEP);
    const messageTypeStr = msh9Parts[0]?.trim() ?? "";
    const triggerEvent = msh9Parts[1]?.trim() ?? "";

    if (!VALID_MESSAGE_TYPES.has(messageTypeStr as Hl7V2MessageType)) {
      throw new Error(
        `parseMessage: messageType=${JSON.stringify(messageTypeStr)} not in ` +
          `${JSON.stringify(Array.from(VALID_MESSAGE_TYPES).sort())}; ` +
          `supported families are ADT + ORU`,
      );
    }
    const messageType = messageTypeStr as Hl7V2MessageType;

    if (messageType === "ADT" && !VALID_ADT_TRIGGERS.has(triggerEvent)) {
      if (this._strict) {
        throw new Error(
          `parseMessage [strict]: triggerEvent=${JSON.stringify(triggerEvent)} not in ` +
            `supported ADT set`,
        );
      }
    } else if (messageType === "ORU" && !VALID_ORU_TRIGGERS.has(triggerEvent)) {
      if (this._strict) {
        throw new Error(
          `parseMessage [strict]: triggerEvent=${JSON.stringify(triggerEvent)} not in ` +
            `supported ORU set`,
        );
      }
    }

    if (!messageControlId && this._strict) {
      throw new Error(
        "parseMessage [strict]: MSH-10 (message control ID) is empty",
      );
    }
    if (!messageDatetime && this._strict) {
      throw new Error(
        "parseMessage [strict]: MSH-7 (message datetime) is empty",
      );
    }

    const mshHash = sha256Hex(msh);

    const segmentTypes: string[] = [];
    for (const seg of segments) {
      if (seg.length >= 3) {
        const segType = seg.slice(0, 3);
        if (!segmentTypes.includes(segType)) {
          segmentTypes.push(segType);
        }
      }
    }

    validateValueLength("sending_application", sendingApp);
    validateValueLength("receiving_application", receivingApp);
    validateValueLength("sending_facility", sendingFacility);
    validateValueLength("receiving_facility", receivingFacility);
    validateValueLength("message_control_id", messageControlId);
    validateValueLength("message_datetime", messageDatetime);
    validateValueLength("processing_id", processingId);
    validateValueLength("version_id", versionId);

    return {
      messageType,
      triggerEvent,
      messageControlId,
      sendingApplication: sendingApp,
      receivingApplication: receivingApp,
      sendingFacility,
      receivingFacility,
      messageDatetime,
      processingId,
      versionId,
      mshSegmentHash: mshHash,
      segmentTypesObserved: Object.freeze([...segmentTypes]),
    };
  }

  /** Parse an HL7 V2 ADT message specifically. */
  parseAdt(raw: Uint8Array | string): HL7v2ParsedMessage {
    const parsed = this.parseMessage(raw);
    if (parsed.messageType !== "ADT") {
      throw new Error(
        `parseAdt: messageType=${JSON.stringify(parsed.messageType)}, expected ADT`,
      );
    }
    return parsed;
  }

  /** Parse an HL7 V2 ORU message specifically. */
  parseOru(raw: Uint8Array | string): HL7v2ParsedMessage {
    const parsed = this.parseMessage(raw);
    if (parsed.messageType !== "ORU") {
      throw new Error(
        `parseOru: messageType=${JSON.stringify(parsed.messageType)}, expected ORU`,
      );
    }
    return parsed;
  }

  /** Derive AuditProof metadata + optional customer workflow ID. */
  static deriveMetadata(
    parsed: HL7v2ParsedMessage,
    options?: { customerWorkflowId?: string },
  ): Record<string, string> {
    const meta = parsedMessageToMetadata(parsed);
    if (options?.customerWorkflowId !== undefined) {
      validateValueLength(
        "customerWorkflowId",
        options.customerWorkflowId,
      );
      if (Object.keys(meta).length >= MAX_METADATA_KEYS) {
        throw new Error(
          "deriveMetadata: cannot add customer_workflow_id; metadata " +
            "already at MAX_METADATA_KEYS",
        );
      }
      meta.customer_workflow_id = options.customerWorkflowId;
      const size = estimateSerializedSize(meta);
      if (size > 4096) {
        throw new Error(
          `deriveMetadata serializes to ${size} bytes, max 4096`,
        );
      }
    }
    return meta;
  }
}
