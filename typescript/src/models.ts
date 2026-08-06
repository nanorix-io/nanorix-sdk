/**
 * TypeScript interfaces for all Nanorix API objects.
 *
 * Rules:
 * - Interfaces, not classes (zero runtime overhead)
 * - All timestamps are strings (ISO 8601)
 * - Optional fields explicitly marked
 * - Matches Python SDK Pydantic models exactly
 * - [key: string]: unknown for API forward-compatibility (equiv to extra="allow")
 */

// ── Capsulefiles ──

export interface CapsulefileFile {
  id: string;
  filename: string;
  size_bytes: number;
  /** SHA-512 hash (field name is legacy, value is sha512 hex) */
  sha256: string;
  [key: string]: unknown;
}

export interface Capsulefile {
  id: string;
  name: string;
  runtime: string;
  entrypoint: string;
  description?: string;
  labels?: Record<string, string>;
  input_path?: string;
  output_path?: string;
  env_vars?: Record<string, string>;
  capsule_memory_mb?: number;
  content_hash?: string;
  status?: string;
  version?: number;
  total_size_bytes?: number;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
  ready_at?: string;
  files?: CapsulefileFile[];
  [key: string]: unknown;
}

export interface CapsulefileList {
  capsulefiles: Capsulefile[];
  cursor?: string;
  has_more?: boolean;
  [key: string]: unknown;
}

// ── Capsules ──

export interface Capsule {
  id: string;
  status: string;
  customer_id?: string;
  capsulefile_id?: string;
  capsulefile_version?: number;
  max_lifetime_seconds?: number;
  created_at?: string;
  destroyed_at?: string;
  [key: string]: unknown;
}

export interface CapsuleList {
  capsules: Capsule[];
  [key: string]: unknown;
}

export interface ExecutionResult {
  stdout?: string;
  stderr?: string;
  exit_code: number;
  duration_seconds?: number;
  [key: string]: unknown;
}

export interface FileInfo {
  file_id?: string;
  filename: string;
  size_bytes: number;
  /** SHA-512 hash (field name is legacy, value is sha512 hex) */
  sha256: string;
  uploaded_at?: string;
  [key: string]: unknown;
}

// ── CDP ──

export interface ActivityEvent {
  event: string;
  at: string;
  [key: string]: unknown;
}

export interface ResidencyInfo {
  started?: string;
  destroyed?: string;
  total_seconds?: number;
  [key: string]: unknown;
}

export interface DestructionInfo {
  trigger?: string;
  chain?: string;
  chain_hash?: string;
  steps?: Record<string, unknown>[];
  [key: string]: unknown;
}

export interface AttestationInfo {
  algorithm?: string;
  public_key?: string;
  signature?: string;
  [key: string]: unknown;
}

export interface DataSummary {
  file_count: number;
  total_bytes: number;
  content_hash?: string;
  [key: string]: unknown;
}

/**
 * Per-record receipt — mirror of the canonical `RecordReceipt` wire type.
 *
 * Forever-Standard: field-additive optional fields with
 * `[key: string]: unknown` so SDK-side parsing keeps working as the Rust
 * struct grows.
 *
 * **No `control_tags` field by design.** Section "Receipt as direct
 * evidence primitive" + RE-SCOPED: control IDs are NEVER stamped
 * into the signed receipt. Adapters derive `(jurisdiction x classification
 * x pattern_tag) -> string[]` at ingestion time via the
 * published mapping artifact (`./control_map`). Three-layer
 * separation: receipt (immutable signed primitive) / mapping (versioned
 * artifact) / adapter (vendor pace).
 */
export interface RecordReceipt {
  record_index: number;
  record_id: string;
  record_input_hash: string;
  record_output_hash: string;
  record_chain_hash: string;
  record_activity_trail?: unknown[];
  pattern_tag?: string;
  merkle_inclusion_proof?: string[];
  [key: string]: unknown;
}

/**
 * Cross-org parent-proof link — mirror of
 * the canonical `ParentProofLink` wire type.
 *
 * Forever-Standard: optional fields skip-serialize when
 * undefined so cross-impl byte-equivalence is preserved.
 */
export interface ParentProofLink {
  parent_chain_hash: string;
  parent_key_id: string;
  parent_signature: string;
  parent_role?: string;
  parent_jurisdiction?: string;
  parent_organization_tag?: string;
  [key: string]: unknown;
}

/** AuditRecord — the complete activity trail. Private to the data processor. */
export interface CdpDocument {
  version?: string;
  capsule_id: string;
  activity?: ActivityEvent[];
  residency?: ResidencyInfo;
  destruction?: DestructionInfo;
  attestation?: AttestationInfo;
  document_hash?: string;
  destruction_trigger?: string;
  /**
   * Reserved-slot discriminator. V1 always undefined /
   * absent (workload scope implicit). ungated values:
   * "workload" | "request" | "call" | "batch".
   */
  cdp_kind?: string;
  /**
   * Wave-N per-record receipts + cross-org parent
   * composition. Field-additive Optional per Forever-Standard:
   * pre-Wave-N AuditProofs canonicalize byte-identically (both fields skip-
   * serialize when undefined). Receipts carry `pattern_tag` only — control
   * IDs are NEVER stamped into the signed primitive; adapters apply the
   * mapping artifact at ingestion time.
   */
  record_receipts?: RecordReceipt[];
  record_receipts_merkle_root?: string;
  parent_proof_hashes?: ParentProofLink[];
  parent_proofs_merkle_root?: string;
  [key: string]: unknown;
}

/**
 * AuditProof — the shareable proof. Zero operational details.
 * Safe to share with auditors, regulators, and data subjects.
 */
export interface VerificationCdp {
  version?: string;
  capsule_id: string;
  environment?: Record<string, unknown>;
  data_ingested?: DataSummary;
  data_extracted?: DataSummary;
  residency?: ResidencyInfo;
  destruction?: DestructionInfo;
  attestation?: AttestationInfo;
  document_hash?: string;
  verify?: string;
  /** reserved-slot discriminator. V1 always undefined. */
  cdp_kind?: string;
  /**
   * mirrored on the shareable AuditProof surface. See
   * CdpDocument for full semantics. Field-additive Optional per Forever-
   * Standard: pre-Wave-N AuditProofs canonicalize byte-
   * identically when undefined.
   */
  record_receipts?: RecordReceipt[];
  record_receipts_merkle_root?: string;
  parent_proof_hashes?: ParentProofLink[];
  parent_proofs_merkle_root?: string;
  [key: string]: unknown;
}

// ── CDP Access Wrapper ──

/**
 * Wrapper returned after capsule destruction.
 * Provides typed access to both CDP layers.
 */
export class CdpAccess {
  private readonly _fullData: Record<string, unknown>;
  private readonly _verificationData: Record<string, unknown>;
  private readonly _capsuleId: string;

  constructor(
    fullData: Record<string, unknown>,
    verificationData: Record<string, unknown>,
    capsuleId: string,
  ) {
    this._fullData = fullData;
    this._verificationData = verificationData;
    this._capsuleId = capsuleId;
  }

  /** Capsule ID. */
  get id(): string {
    return this._capsuleId;
  }

  /** AuditRecord with complete activity trail. Private to data processor. */
  full(): CdpDocument {
    return this._fullData as CdpDocument;
  }

  /** AuditProof. Safe to share with auditors and regulators. */
  verification(): VerificationCdp {
    return this._verificationData as VerificationCdp;
  }

  /** URL for independent CDP verification at nanorix.io/verify. */
  get verifyUrl(): string {
    const v = this.verification();
    return v.verify ?? `https://nanorix.io/verify/${this._capsuleId}`;
  }

  /** Raw full CDP as dictionary. */
  toDict(): Record<string, unknown> {
    return this._fullData;
  }
}
