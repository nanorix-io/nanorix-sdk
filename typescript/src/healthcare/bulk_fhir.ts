/**
 * Bulk FHIR $export structural adapter for population-data healthcare
 * workloads.
 *
 * HL7 FHIR Bulk Data Access IG v2.0.0 baseline. Parses three classes of
 * bulk-FHIR transaction artifacts:
 *  1. Kickoff request URL (system/patient/group $export endpoints)
 *  2. Status response JSON body
 *  3. NDJSON resource stream (one resource per line)
 *
 * Three-role discipline: structural parser; clinical-content
 * interpretation + FHIR IG conformance + downstream analytics
 * adjudication remain the customer's responsibility.
 *
 * Narrow signed claim: emits only `nanorix:framework` /
 * `nanorix:framework_version` reserved keys plus structural fields.
 * Patient IDs are SHA-256-hashed; raw identifiers are NEVER emitted.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "fhir-bulk-data-2.0.0";

/** $export endpoint type — derived from the kickoff URL path. */
export type BulkFhirEndpointType = "system" | "patient" | "group";

const VALID_ENDPOINT_TYPES: ReadonlySet<BulkFhirEndpointType> =
  new Set<BulkFhirEndpointType>(["system", "patient", "group"]);

/** Bulk Data Status states per the IG. */
export type BulkFhirStatusState =
  | "in-progress"
  | "completed"
  | "failed"
  | "queued";

const VALID_STATUS_STATES: ReadonlySet<BulkFhirStatusState> =
  new Set<BulkFhirStatusState>([
    "in-progress",
    "completed",
    "failed",
    "queued",
  ]);

// FHIR R4/R5 resource type closed enum — appended-only.
// Curated for the most-common bulk-export resources through 2026.
const VALID_FHIR_RESOURCE_TYPES: ReadonlySet<string> = new Set<string>([
  "Patient",
  "Practitioner",
  "PractitionerRole",
  "Organization",
  "Location",
  "Group",
  "RelatedPerson",
  "Person",
  "Observation",
  "Condition",
  "Procedure",
  "Encounter",
  "EpisodeOfCare",
  "AllergyIntolerance",
  "FamilyMemberHistory",
  "ClinicalImpression",
  "Medication",
  "MedicationRequest",
  "MedicationStatement",
  "MedicationAdministration",
  "MedicationDispense",
  "Immunization",
  "DiagnosticReport",
  "ImagingStudy",
  "Specimen",
  "Media",
  "CarePlan",
  "CareTeam",
  "Goal",
  "ServiceRequest",
  "DeviceRequest",
  "NutritionOrder",
  "Coverage",
  "Claim",
  "ClaimResponse",
  "ExplanationOfBenefit",
  "Account",
  "Invoice",
  "DocumentReference",
  "Composition",
  "Bundle",
  "List",
  "Task",
  "Appointment",
  "Schedule",
  "Slot",
  "Provenance",
  "AuditEvent",
  "Questionnaire",
  "QuestionnaireResponse",
  "DocumentManifest",
  "MolecularSequence",
  "MolecularDefinition",
]);

function sha256Hex(input: string | Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

/** Structural parse result for a Bulk FHIR $export kickoff request. */
export interface BulkFhirKickoff {
  endpointType: BulkFhirEndpointType;
  baseUrl: string;
  groupId: string;
  outputFormat: string;
  typeFilter: string;
  since: string;
  typesRequested: string;
  acceptHeader: string;
  requestHash: string;
}

/** Structural parse result for a Bulk FHIR $export status response. */
export interface BulkFhirStatus {
  state: BulkFhirStatusState;
  transactionTime: string;
  requestUrl: string;
  outputCount: number;
  errorCount: number;
  deletedCount: number;
  expiresAt: string;
}

/** Structural parse result for a single FHIR resource from NDJSON. */
export interface BulkFhirResource {
  resourceType: string;
  resourceId: string;
  lastUpdated: string;
  versionId: string;
  profile: string;
  patientIdHash: string;
  resourceHash: string;
}

/** Convert kickoff structural fields to capsule metadata. */
export function kickoffToMetadata(
  k: BulkFhirKickoff,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "FHIR-Bulk",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    kind: "kickoff",
    endpoint_type: k.endpointType,
    base_url: k.baseUrl,
    output_format: k.outputFormat,
    request_hash: k.requestHash,
  };
  if (k.groupId) meta.group_id = k.groupId;
  if (k.typeFilter) meta.type_filter = k.typeFilter;
  if (k.since) meta.since = k.since;
  if (k.typesRequested) meta.types_requested = k.typesRequested;

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

/** Convert status structural fields to capsule metadata. */
export function statusToMetadata(s: BulkFhirStatus): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "FHIR-Bulk",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    kind: "status",
    state: s.state,
    output_count: String(s.outputCount),
    error_count: String(s.errorCount),
    deleted_count: String(s.deletedCount),
  };
  if (s.transactionTime) meta.transaction_time = s.transactionTime;
  if (s.requestUrl) meta.request_url = s.requestUrl;
  if (s.expiresAt) meta.expires_at = s.expiresAt;

  const keyCount = Object.keys(meta).length;
  if (keyCount > MAX_METADATA_KEYS) {
    throw new Error(
      `toMetadata produced ${keyCount} keys, max ${MAX_METADATA_KEYS}`,
    );
  }
  return meta;
}

/**
 * Stable JSON serialization matching Python's
 * `json.dumps(obj, sort_keys=True, separators=(',', ':'))`.
 * Mirrors the resource_hash computation in `bulk_fhir.py::_build_resource`.
 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return JSON.stringify(value);
  }
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  const parts = keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`);
  return `{${parts.join(",")}}`;
}

/**
 * Bulk FHIR $export structural adapter.
 *
 * Parses kickoff URL parameters, status response bodies, and NDJSON
 * resource streams. Customer's pipeline orchestrates the actual HTTP
 * transactions; this adapter handles only structural-metadata
 * extraction.
 */
export class BulkFhirAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedEndpointTypes(): string[] {
    return Array.from(VALID_ENDPOINT_TYPES).sort();
  }

  get supportedResourceTypes(): string[] {
    return Array.from(VALID_FHIR_RESOURCE_TYPES).sort();
  }

  get supportedStatusStates(): string[] {
    return Array.from(VALID_STATUS_STATES).sort();
  }

  /** Parse a `$export` kickoff URL into structural metadata. */
  parseKickoff(
    url: string,
    options?: { acceptHeader?: string; preferHeader?: string },
  ): BulkFhirKickoff {
    if (typeof url !== "string" || !url) {
      throw new Error("parseKickoff: url must be a non-empty string");
    }
    if (!url.includes("$export")) {
      throw new Error(
        "parseKickoff: URL does not contain '$export'; not a bulk FHIR " +
          "kickoff request",
      );
    }

    const acceptHeader = options?.acceptHeader ?? "application/fhir+json";
    const preferHeader = options?.preferHeader ?? "respond-async";

    const [pathPart, queryPart = ""] = url.split("?", 2);

    let endpointType: BulkFhirEndpointType;
    let groupId = "";
    let baseUrl = "";

    if (!pathPart.endsWith("/$export")) {
      throw new Error(
        "parseKickoff: URL path does not end with '/$export'",
      );
    }
    const stem = pathPart.slice(0, -"/$export".length);
    if (stem.endsWith("/Patient")) {
      endpointType = "patient";
      baseUrl = stem.slice(0, -"/Patient".length);
    } else if (stem.includes("/Group/")) {
      endpointType = "group";
      const idx = stem.lastIndexOf("/Group/");
      baseUrl = stem.slice(0, idx);
      groupId = stem.slice(idx + "/Group/".length);
    } else {
      endpointType = "system";
      baseUrl = stem;
    }

    let outputFormat = "application/fhir+ndjson";
    let typeFilter = "";
    let since = "";
    let typesRequested = "";

    if (queryPart) {
      for (const pair of queryPart.split("&")) {
        const [rawKey, ...rest] = pair.split("=");
        const key = (rawKey ?? "").trim();
        const value = rest.join("=").replace(/\+/g, " ").trim();
        if (key === "_outputFormat") outputFormat = value;
        else if (key === "_typeFilter") typeFilter = value;
        else if (key === "_since") since = value;
        else if (key === "_type") typesRequested = value;
      }
    }

    if (typesRequested && this._strict) {
      const unknown = typesRequested
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t && !VALID_FHIR_RESOURCE_TYPES.has(t));
      if (unknown.length > 0) {
        throw new Error(
          `parseKickoff [strict]: unknown resource types: ${JSON.stringify(unknown)}`,
        );
      }
    }

    const canonical = `${url}\n${acceptHeader}\n${preferHeader}`;
    const requestHash = sha256Hex(canonical);

    validateValueLength("baseUrl", baseUrl);
    validateValueLength("groupId", groupId);
    validateValueLength("outputFormat", outputFormat);
    validateValueLength("typeFilter", typeFilter);
    validateValueLength("since", since);
    validateValueLength("typesRequested", typesRequested);

    return {
      endpointType,
      baseUrl,
      groupId,
      outputFormat,
      typeFilter,
      since,
      typesRequested,
      acceptHeader,
      requestHash,
    };
  }

  /** Parse a `$export` status response body (JSON or empty). */
  parseStatus(responseBody: string | Uint8Array): BulkFhirStatus {
    let bodyStr: string;
    if (typeof responseBody === "string") {
      bodyStr = responseBody;
    } else {
      bodyStr = new TextDecoder("utf-8").decode(responseBody);
    }
    if (!bodyStr.trim()) {
      return {
        state: "in-progress",
        transactionTime: "",
        requestUrl: "",
        outputCount: 0,
        errorCount: 0,
        deletedCount: 0,
        expiresAt: "",
      };
    }

    let doc: unknown;
    try {
      doc = JSON.parse(bodyStr);
    } catch (e) {
      throw new Error(`parseStatus: JSON decode error: ${(e as Error).message}`);
    }
    if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
      throw new Error("parseStatus: status response is not a JSON object");
    }
    const obj = doc as Record<string, unknown>;

    let state: BulkFhirStatusState = "in-progress";
    if ("transactionTime" in obj && "output" in obj) {
      state = "completed";
    } else if (
      "resourceType" in obj &&
      obj.resourceType === "OperationOutcome"
    ) {
      state = "failed";
    }

    const transactionTime = (obj.transactionTime as string) ?? "";
    const requestUrl = (obj.request as string) ?? "";
    const output = Array.isArray(obj.output) ? obj.output : [];
    const error = Array.isArray(obj.error) ? obj.error : [];
    const deleted = Array.isArray(obj.deleted) ? obj.deleted : [];

    validateValueLength("transaction_time", transactionTime);
    validateValueLength("request_url", requestUrl);

    return {
      state,
      transactionTime,
      requestUrl,
      outputCount: output.length,
      errorCount: error.length,
      deletedCount: deleted.length,
      expiresAt: "",
    };
  }

  /**
   * Parse an NDJSON FHIR resource stream into a generator of resources.
   * Patient IDs are SHA-256-hashed; raw IDs are NEVER emitted.
   */
  *parseNdjsonStream(ndjson: string | Uint8Array): Generator<BulkFhirResource> {
    const text =
      typeof ndjson === "string"
        ? ndjson
        : new TextDecoder("utf-8").decode(ndjson);
    if (!text) return;

    const lines = text.split(/\r?\n/);
    let lineNo = 0;
    for (const line of lines) {
      lineNo += 1;
      const stripped = line.trim();
      if (!stripped) continue;
      let resource: unknown;
      try {
        resource = JSON.parse(stripped);
      } catch (e) {
        if (this._strict) {
          throw new Error(
            `parseNdjsonStream [strict]: line ${lineNo}: JSON decode error: ${(e as Error).message}`,
          );
        }
        continue;
      }
      if (
        !resource ||
        typeof resource !== "object" ||
        Array.isArray(resource)
      ) {
        if (this._strict) {
          throw new Error(
            `parseNdjsonStream [strict]: line ${lineNo}: not a JSON object`,
          );
        }
        continue;
      }
      yield this._buildResource(resource as Record<string, unknown>, lineNo);
    }
  }

  private _buildResource(
    resource: Record<string, unknown>,
    lineNo: number,
  ): BulkFhirResource {
    const resourceType =
      typeof resource.resourceType === "string" ? resource.resourceType : "";
    if (!resourceType && this._strict) {
      throw new Error(
        `parseNdjsonStream [strict]: line ${lineNo}: missing resourceType`,
      );
    }
    if (
      resourceType &&
      !VALID_FHIR_RESOURCE_TYPES.has(resourceType) &&
      this._strict
    ) {
      throw new Error(
        `parseNdjsonStream [strict]: line ${lineNo}: ` +
          `resourceType=${JSON.stringify(resourceType)} not in supported set`,
      );
    }

    const resourceId =
      resource.id !== undefined && resource.id !== null
        ? String(resource.id)
        : "";

    let lastUpdated = "";
    let versionId = "";
    let profile = "";
    if (resource.meta && typeof resource.meta === "object") {
      const meta = resource.meta as Record<string, unknown>;
      lastUpdated = meta.lastUpdated ? String(meta.lastUpdated) : "";
      versionId = meta.versionId ? String(meta.versionId) : "";
      if (Array.isArray(meta.profile) && meta.profile.length > 0) {
        profile = String(meta.profile[0]);
      }
    }

    let patientIdHash = "";
    if (resourceType === "Patient" && resourceId) {
      patientIdHash = sha256Hex(resourceId);
    } else {
      const subject = resource.subject;
      if (subject && typeof subject === "object") {
        const ref = (subject as Record<string, unknown>).reference;
        if (typeof ref === "string" && ref.startsWith("Patient/")) {
          const patientRefId = ref.split("/", 2)[1];
          if (patientRefId) {
            patientIdHash = sha256Hex(patientRefId);
          }
        }
      }
    }

    const canonical = stableStringify(resource);
    const resourceHash = sha256Hex(canonical);

    return {
      resourceType,
      resourceId,
      lastUpdated,
      versionId,
      profile,
      patientIdHash,
      resourceHash,
    };
  }

  /** Build a patient-ID-hash → resource-type list cross-walk. */
  derivePatientCrosswalk(
    resources: BulkFhirResource[],
  ): Record<string, string[]> {
    const crosswalk: Record<string, string[]> = {};
    for (const r of resources) {
      if (r.patientIdHash) {
        if (!crosswalk[r.patientIdHash]) {
          crosswalk[r.patientIdHash] = [];
        }
        if (!crosswalk[r.patientIdHash].includes(r.resourceType)) {
          crosswalk[r.patientIdHash].push(r.resourceType);
        }
      }
    }
    return crosswalk;
  }

  /** Canonical per-record receipt pattern_tag for FHIR bulk extraction. */
  static receiptPatternTag(): string {
    return "fhir_record";
  }
}
