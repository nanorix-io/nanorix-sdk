/**
 * C-CDA reader → FHIR Bundle adapter for capsule workloads.
 *
 * Consolidated Clinical Document Architecture (C-CDA R2.1). Parses
 * customer-supplied C-CDA XML and produces structural metadata + a
 * FHIR-Bundle-shaped object (DocumentReference + Binary) matching the
 * Epic-side 2026 production pattern of C-CDA-as-FHIR-attachment.
 *
 * Three-role discipline: structural reader; clinical
 * interpretation + IG conformance + terminology mapping remain the
 * customer's responsibility. Adapter does NOT certify upstream-system
 * conformance.
 *
 * Customer-sole-declarer: customer supplies the document
 * bytes; adapter parses structural identifiers only.
 *
 * No XML parser dependency in V1 — the adapter accepts pre-parsed
 * structural identifiers via the `CCDADocumentReference.fromFields`
 * builder. Customer pipeline parses XML using their preferred library
 * (lxml, fast-xml-parser, etc.) and passes the extracted identifiers
 * directly.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "ccda-r2.1";

function sha256Hex(input: string | Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural fields extracted from a C-CDA document header. */
export interface CCDADocumentFields {
  documentTypeCode: string;
  documentTypeDisplay: string;
  documentId: string;
  documentDatetime: string;
  patientIdReference: string;
  authorOrganization: string;
  custodianOrganization: string;
  documentSha256: string;
  sectionTemplateIds?: string[];
  sectionCodes?: string[];
}

/**
 * Structural reader result for a C-CDA XML document.
 *
 * Captures structural identifiers from the ClinicalDocument header
 * plus a SHA-256 hash of the document bytes for integrity anchoring.
 * Clinical content (section bodies) remains in the customer's capsule
 * workspace.
 */
export class CCDADocumentReference {
  readonly documentTypeCode: string;
  readonly documentTypeDisplay: string;
  readonly documentId: string;
  readonly documentDatetime: string;
  readonly patientIdReference: string;
  readonly authorOrganization: string;
  readonly custodianOrganization: string;
  readonly documentSha256: string;
  readonly sectionTemplateIds: ReadonlyArray<string>;
  readonly sectionCodes: ReadonlyArray<string>;

  constructor(fields: CCDADocumentFields) {
    this.documentTypeCode = fields.documentTypeCode;
    this.documentTypeDisplay = fields.documentTypeDisplay;
    this.documentId = fields.documentId;
    this.documentDatetime = fields.documentDatetime;
    this.patientIdReference = fields.patientIdReference;
    this.authorOrganization = fields.authorOrganization;
    this.custodianOrganization = fields.custodianOrganization;
    this.documentSha256 = fields.documentSha256;
    this.sectionTemplateIds = Object.freeze([
      ...(fields.sectionTemplateIds ?? []),
    ]);
    this.sectionCodes = Object.freeze([...(fields.sectionCodes ?? [])]);
    Object.freeze(this);
  }

  /** LOINC 34133-9 — Continuity of Care Document. */
  get isContinuityOfCare(): boolean {
    return this.documentTypeCode === "34133-9";
  }

  /** LOINC 18842-5 — Discharge Summary. */
  get isDischargeSummary(): boolean {
    return this.documentTypeCode === "18842-5";
  }

  /** LOINC 11506-3 — Progress Note. */
  get isProgressNote(): boolean {
    return this.documentTypeCode === "11506-3";
  }

  /**
   * Emit a FHIR-Bundle-shaped object (DocumentReference + Binary).
   * Mirrors the Epic-side 2026 production pattern of C-CDA-as-FHIR-attachment.
   */
  toFhirBundle(rawBytes: Uint8Array): Record<string, unknown> {
    const b64 = Buffer.from(rawBytes).toString("base64");
    const binaryId = "ccda-binary";
    const docRefId = "ccda-document-reference";
    return {
      resourceType: "Bundle",
      type: "collection",
      entry: [
        {
          fullUrl: `urn:uuid:${docRefId}`,
          resource: {
            resourceType: "DocumentReference",
            id: docRefId,
            status: "current",
            type: {
              coding: [
                {
                  system: "http://loinc.org",
                  code: this.documentTypeCode,
                  display: this.documentTypeDisplay,
                },
              ],
            },
            subject: {
              identifier: { value: this.patientIdReference },
            },
            date: this.documentDatetime,
            author: [{ display: this.authorOrganization }],
            custodian: { display: this.custodianOrganization },
            content: [
              {
                attachment: {
                  contentType: "application/xml",
                  url: `urn:uuid:${binaryId}`,
                  hash: this.documentSha256,
                },
              },
            ],
          },
        },
        {
          fullUrl: `urn:uuid:${binaryId}`,
          resource: {
            resourceType: "Binary",
            id: binaryId,
            contentType: "application/xml",
            data: b64,
          },
        },
      ],
    };
  }

  /** Convert to capsule metadata envelope. */
  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: "CCDA",
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      document_type_code: this.documentTypeCode,
      document_type_display: this.documentTypeDisplay,
      document_id: this.documentId,
      document_datetime: this.documentDatetime,
      patient_id_reference: this.patientIdReference,
      author_organization: this.authorOrganization,
      custodian_organization: this.custodianOrganization,
      document_sha256: this.documentSha256,
    };
    if (this.sectionCodes.length > 0) {
      meta.section_codes = this.sectionCodes.join(",");
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

/**
 * C-CDA reader. Customer pipeline parses XML using their preferred
 * library (fast-xml-parser, sax, etc.) and supplies extracted
 * structural fields via `readFromFields`.
 */
export class CCDAReader {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  /**
   * Build a CCDADocumentReference from pre-parsed structural fields
   * plus the raw bytes (for SHA-256 anchoring).
   */
  readFromFields(
    rawBytes: Uint8Array,
    fields: Omit<CCDADocumentFields, "documentSha256">,
  ): CCDADocumentReference {
    if (!(rawBytes instanceof Uint8Array)) {
      throw new Error("readFromFields: rawBytes must be Uint8Array");
    }
    if (rawBytes.length === 0) {
      throw new Error("readFromFields: empty document bytes");
    }

    if (this._strict) {
      if (!fields.documentTypeCode) {
        throw new Error(
          "readFromFields [strict]: documentTypeCode is required",
        );
      }
      if (!fields.documentId) {
        throw new Error(
          "readFromFields [strict]: documentId is required",
        );
      }
      if (!fields.patientIdReference) {
        throw new Error(
          "readFromFields [strict]: patientIdReference is required",
        );
      }
    }

    validateValueLength("document_type_code", fields.documentTypeCode);
    validateValueLength("document_type_display", fields.documentTypeDisplay);
    validateValueLength("document_id", fields.documentId);
    validateValueLength("document_datetime", fields.documentDatetime);
    validateValueLength(
      "patient_id_reference",
      fields.patientIdReference,
    );
    validateValueLength(
      "author_organization",
      fields.authorOrganization,
    );
    validateValueLength(
      "custodian_organization",
      fields.custodianOrganization,
    );

    const documentSha256 = sha256Hex(rawBytes);
    return new CCDADocumentReference({ ...fields, documentSha256 });
  }

  /** Combined readFromFields + toFhirBundle. */
  readToBundle(
    rawBytes: Uint8Array,
    fields: Omit<CCDADocumentFields, "documentSha256">,
  ): { doc: CCDADocumentReference; bundle: Record<string, unknown> } {
    const doc = this.readFromFields(rawBytes, fields);
    const bundle = doc.toFhirBundle(rawBytes);
    return { doc, bundle };
  }

  /** Canonical list of supported C-CDA document types. */
  static supportedDocumentTypes(): Array<[string, string]> {
    return [
      ["34133-9", "Continuity of Care Document"],
      ["18842-5", "Discharge Summary"],
      ["11506-3", "Progress Note"],
      ["28570-0", "Procedure Note"],
      ["57133-1", "Referral Note"],
      ["11488-4", "Consultation Note"],
    ];
  }

  /** Derive metadata + optional customer workflow ID. */
  static deriveMetadata(
    parsed: CCDADocumentReference,
    options?: { customerWorkflowId?: string },
  ): Record<string, string> {
    const meta = parsed.toMetadata();
    if (options?.customerWorkflowId !== undefined) {
      validateValueLength("customerWorkflowId", options.customerWorkflowId);
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
