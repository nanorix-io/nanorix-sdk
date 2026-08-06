/**
 * IHE XDS-I.b + XDR profile structural adapter for capsule workloads.
 *
 * Covers two IHE profiles dominant in enterprise medical-imaging
 * integration as of 2026:
 *  - XDS-I.b (Cross-Enterprise Document Sharing for Imaging) — used by
 *    hospital enterprise archives, regional HIEs, and federated PACS
 *    networks for KOS-based imaging-document submission/retrieval.
 *  - XDR (Cross-Enterprise Document Reliable Interchange) — used by
 *    XCA-style federation gateways and direct EHR-to-EHR document push.
 *
 * Transactions covered: ITI-41, ITI-43, ITI-38, ITI-39, RAD-68, RAD-69.
 *
 * Three-role discipline (ADR-017): structural adapter; IHE conformance
 * certification, RAD-TF/ITI-TF Implementation Guide validation, IHE
 * Connectathon test certification, document content interpretation,
 * and PHI classification remain the customer's responsibility.
 *
 * Customer-sole-declarer (ADR-013): customer pipeline parses the SOAP
 * envelope and supplies structural identifiers (UIDs, OIDs, repository
 * URIs) directly; adapter validates structural format only.
 *
 * Narrow signed claim: emits `payload_sha256` as the structural-bytes
 * anchor; NEVER asserts IHE conformance, RAD-TF profile pass, or
 * HIPAA applicability.
 */

import { createHash } from "crypto";
import {
  MAX_METADATA_KEYS,
  MAX_VALUE_LENGTH,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "ihe-iti-rad-2024";

/** Supported IHE profiles. */
export type IheProfile = "XDS-I.b" | "XDR";

const VALID_PROFILES: ReadonlySet<IheProfile> = new Set<IheProfile>([
  "XDS-I.b",
  "XDR",
]);

/** Supported IHE transaction identifiers. */
export type IheTransactionId =
  | "ITI-41"
  | "ITI-43"
  | "ITI-38"
  | "ITI-39"
  | "RAD-68"
  | "RAD-69";

const VALID_TRANSACTION_TYPES: ReadonlySet<IheTransactionId> =
  new Set<IheTransactionId>([
    "ITI-41",
    "ITI-43",
    "ITI-38",
    "ITI-39",
    "RAD-68",
    "RAD-69",
  ]);

const TRANSACTION_PROFILES: Record<IheTransactionId, ReadonlySet<IheProfile>> = {
  "ITI-41": new Set<IheProfile>(["XDS-I.b", "XDR"]),
  "ITI-43": new Set<IheProfile>(["XDS-I.b"]),
  "ITI-38": new Set<IheProfile>(["XDR"]),
  "ITI-39": new Set<IheProfile>(["XDR"]),
  "RAD-68": new Set<IheProfile>(["XDS-I.b"]),
  "RAD-69": new Set<IheProfile>(["XDS-I.b"]),
};

// OID/UID structural pattern per ISO/IEC 9834-1 + DICOM PS3.5 §9.
const UID_PATTERN = /^\d+(\.\d+)*$/;

function isValidUid(s: string): boolean {
  if (!s || s.length > 64) return false;
  return UID_PATTERN.test(s);
}

function sha256Hex(input: Uint8Array): string {
  return createHash("sha256").update(input).digest("hex");
}

function ensureIso8601Utc(timestamp: string): string {
  if (!timestamp) {
    const now = new Date();
    return now.toISOString().replace(/\.\d{3}Z$/, "Z");
  }
  return timestamp;
}

/**
 * Structural anomaly during IHE profile mapping.
 *
 * Three-role discipline (ADR-017): NEVER signals IHE conformance
 * failure, IHE Connectathon test failure, or clinical document
 * validation failure — those determinations are the customer's.
 */
export class IheBindingError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IheBindingError";
  }
}

/**
 * Closed-enum wrapper for an IHE transaction identifier with its
 * canonical profile mapping. Mirrors the Python dataclass pattern.
 */
export class IheTransactionType {
  readonly identifier: IheTransactionId;
  readonly profiles: ReadonlyArray<IheProfile>;

  constructor(identifier: IheTransactionId, profiles: ReadonlyArray<IheProfile>) {
    this.identifier = identifier;
    this.profiles = Object.freeze([...profiles]);
    Object.freeze(this);
  }

  /** Build from a string identifier; validates against the closed enum. */
  static fromIdentifier(identifier: string): IheTransactionType {
    if (typeof identifier !== "string" || !identifier) {
      throw new IheBindingError(
        "transaction_id required (non-empty string); must be one of " +
          `${JSON.stringify(Array.from(VALID_TRANSACTION_TYPES).sort())}`,
      );
    }
    if (!VALID_TRANSACTION_TYPES.has(identifier as IheTransactionId)) {
      throw new IheBindingError(
        `unsupported transaction_id '${identifier}'; must be one of ` +
          `${JSON.stringify(Array.from(VALID_TRANSACTION_TYPES).sort())}`,
      );
    }
    const id = identifier as IheTransactionId;
    const profiles = Array.from(TRANSACTION_PROFILES[id]).sort() as IheProfile[];
    return new IheTransactionType(id, profiles);
  }

  /** Return all supported transaction types sorted by identifier. */
  static allValues(): IheTransactionType[] {
    return Array.from(VALID_TRANSACTION_TYPES)
      .sort()
      .map((id) => IheTransactionType.fromIdentifier(id));
  }

  /**
   * Whether this transaction applies to the given profile. Uses the
   * narrow-claim verb `appliesTo` per
   * feedback_narrow_signed_claim_auditor_certifies.md.
   */
  appliesToProfile(profile: string): boolean {
    return (this.profiles as ReadonlyArray<string>).includes(profile);
  }
}

function validateProfile(profile: string): void {
  if (typeof profile !== "string" || !profile) {
    throw new IheBindingError(
      "profile required (non-empty string); must be one of " +
        `${JSON.stringify(Array.from(VALID_PROFILES).sort())}`,
    );
  }
  if (!VALID_PROFILES.has(profile as IheProfile)) {
    throw new IheBindingError(
      `unsupported profile '${profile}'; must be one of ` +
        `${JSON.stringify(Array.from(VALID_PROFILES).sort())}`,
    );
  }
}

/** Structural parse result for an IHE XDS-I.b / XDR transaction. */
export interface IheTransaction {
  transactionId: IheTransactionId;
  profile: IheProfile;
  repositoryUri: string;
  submissionSetUid: string;
  documentUids: ReadonlyArray<string>;
  studyInstanceUids: ReadonlyArray<string>;
  homeCommunityId: string;
  payloadSha256: string;
  instanceCount: number;
  transactionTimestamp: string;
}

/** Whether the transaction is a submission (ITI-41 / RAD-68). */
export function isIheSubmission(tx: IheTransaction): boolean {
  return tx.transactionId === "ITI-41" || tx.transactionId === "RAD-68";
}

/** Whether the transaction is a retrieve (ITI-43 / RAD-69 / ITI-39). */
export function isIheRetrieve(tx: IheTransaction): boolean {
  return (
    tx.transactionId === "ITI-43" ||
    tx.transactionId === "RAD-69" ||
    tx.transactionId === "ITI-39"
  );
}

/** Whether the transaction is a query (ITI-38). */
export function isIheQuery(tx: IheTransaction): boolean {
  return tx.transactionId === "ITI-38";
}

/** Convert an IheTransaction to capsule metadata envelope. */
export function iheTransactionToMetadata(
  tx: IheTransaction,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "IHE",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    transaction_id: tx.transactionId,
    profile: tx.profile,
    repository_uri: tx.repositoryUri,
    payload_sha256: tx.payloadSha256,
    instance_count: String(tx.instanceCount),
    transaction_timestamp: tx.transactionTimestamp,
  };
  if (tx.submissionSetUid) meta.submission_set_uid = tx.submissionSetUid;
  if (tx.homeCommunityId) meta.home_community_id = tx.homeCommunityId;
  if (tx.documentUids.length > 0) {
    const joined = tx.documentUids.join(",");
    if (joined.length <= MAX_VALUE_LENGTH) {
      meta.document_uids = joined;
    } else {
      meta.document_uids_count = String(tx.documentUids.length);
    }
  }
  if (tx.studyInstanceUids.length > 0) {
    const joined = tx.studyInstanceUids.join(",");
    if (joined.length <= MAX_VALUE_LENGTH) {
      meta.study_instance_uids = joined;
    } else {
      meta.study_instance_uids_count = String(tx.studyInstanceUids.length);
    }
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

/** Options shared by `parseSubmission` / `parseQueryResponse`. */
export interface ParseIheEnvelopeOptions {
  profile: IheProfile;
  transactionId: IheTransactionId;
  soapEnvelopeBytes: Uint8Array;
  repositoryUri?: string;
  submissionSetUid?: string;
  documentUids?: string[];
  studyInstanceUids?: string[];
  homeCommunityId?: string;
  transactionTimestamp?: string;
}

/** IHE XDS-I.b + XDR profile structural adapter. */
export class IheAdapter {
  private readonly _strict: boolean;

  constructor(strict: boolean = false) {
    this._strict = strict;
  }

  get strictMode(): boolean {
    return this._strict;
  }

  get supportedProfiles(): string[] {
    return Array.from(VALID_PROFILES).sort();
  }

  get supportedTransactions(): IheTransactionType[] {
    return IheTransactionType.allValues();
  }

  /** Parse an IHE submission envelope (ITI-41 / RAD-68). */
  parseSubmission(opts: ParseIheEnvelopeOptions): IheTransaction {
    return this._parseEnvelope(opts, true);
  }

  /** Parse an IHE query/retrieve response envelope. */
  parseQueryResponse(opts: ParseIheEnvelopeOptions): IheTransaction {
    return this._parseEnvelope(opts, false);
  }

  private _parseEnvelope(
    opts: ParseIheEnvelopeOptions,
    expectSubmission: boolean,
  ): IheTransaction {
    validateProfile(opts.profile);
    const txType = IheTransactionType.fromIdentifier(opts.transactionId);
    if (!txType.appliesToProfile(opts.profile)) {
      throw new IheBindingError(
        `transaction_id '${opts.transactionId}' is not defined under ` +
          `profile '${opts.profile}'; valid profiles for this transaction ` +
          `are ${JSON.stringify(Array.from(txType.profiles))}`,
      );
    }

    if (!(opts.soapEnvelopeBytes instanceof Uint8Array)) {
      throw new IheBindingError(
        `soapEnvelopeBytes expects Uint8Array (got ${typeof opts.soapEnvelopeBytes})`,
      );
    }
    if (opts.soapEnvelopeBytes.length === 0) {
      throw new IheBindingError(
        "soapEnvelopeBytes must be non-empty; the structural envelope " +
          "is required for AuditProof anchoring",
      );
    }

    const repositoryUri = opts.repositoryUri ?? "";
    const submissionSetUid = opts.submissionSetUid ?? "";
    const homeCommunityId = opts.homeCommunityId ?? "";
    const documentUids = Object.freeze([...(opts.documentUids ?? [])]);
    const studyInstanceUids = Object.freeze([
      ...(opts.studyInstanceUids ?? []),
    ]);

    if (this._strict) {
      if (!repositoryUri) {
        throw new IheBindingError(
          "repository_uri required in strict mode " +
            `(transaction_id=${opts.transactionId})`,
        );
      }
      if (
        expectSubmission &&
        (opts.transactionId === "ITI-41" || opts.transactionId === "RAD-68")
      ) {
        if (!submissionSetUid) {
          throw new IheBindingError(
            "submission_set_uid required in strict mode for submission " +
              `transactions (transaction_id=${opts.transactionId})`,
          );
        }
        if (documentUids.length === 0) {
          throw new IheBindingError(
            "document_uids required non-empty in strict mode for " +
              `submission transactions (transaction_id=${opts.transactionId})`,
          );
        }
      }
      if (opts.profile === "XDR" && !homeCommunityId) {
        throw new IheBindingError(
          "home_community_id required in strict mode for XDR profile",
        );
      }
    }

    if (submissionSetUid && !isValidUid(submissionSetUid)) {
      if (this._strict) {
        throw new IheBindingError(
          `submission_set_uid '${submissionSetUid}' does not match OID ` +
            `structural format (ISO/IEC 9834-1)`,
        );
      }
    }
    if (homeCommunityId && !isValidUid(homeCommunityId)) {
      if (this._strict) {
        throw new IheBindingError(
          `home_community_id '${homeCommunityId}' does not match OID ` +
            `structural format`,
        );
      }
    }
    documentUids.forEach((uid, idx) => {
      if (!isValidUid(uid) && this._strict) {
        throw new IheBindingError(
          `document_uids[${idx}] '${uid}' does not match OID structural format`,
        );
      }
    });
    studyInstanceUids.forEach((uid, idx) => {
      if (!isValidUid(uid) && this._strict) {
        throw new IheBindingError(
          `study_instance_uids[${idx}] '${uid}' does not match DICOM UID ` +
            `structural format`,
        );
      }
    });

    const envelopeHash = sha256Hex(opts.soapEnvelopeBytes);
    const instanceCount = documentUids.length;
    const resolvedTimestamp = ensureIso8601Utc(opts.transactionTimestamp ?? "");

    return {
      transactionId: opts.transactionId,
      profile: opts.profile,
      repositoryUri,
      submissionSetUid,
      documentUids,
      studyInstanceUids,
      homeCommunityId,
      payloadSha256: envelopeHash,
      instanceCount,
      transactionTimestamp: resolvedTimestamp,
    };
  }

  /** Public structural-format validator for OIDs / DICOM UIDs. */
  static isValidUid(s: string): boolean {
    return isValidUid(s);
  }
}
