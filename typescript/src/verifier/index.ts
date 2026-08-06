/**
 * Offline AuditProof (CDP) verifier — pure TypeScript, no network.
 *
 * Re-implements the canonical 8-step SHA-512 chain + Ed25519 attestation
 * defined in `governance/rzl/src/cdp.rs` and `governance/rzl/src/proofs/mod.rs`.
 * Customers, auditors, and air-gapped systems can verify any AuditProof
 * independently without trusting the Nanorix API.
 *
 * Uses Web Crypto subtle API for Ed25519 (Node 18+ and modern browsers).
 *
 * Usage:
 * ```ts
 * import { verify } from "@nanorix/sdk/verifier";
 *
 * const result = await verify(proof);
 * if (result.ok) {
 *   console.log("VERIFIED:", result.chainHash);
 * } else {
 *   console.log("FAIL:", result.failureReason);
 * }
 * ```
 */

// Genesis hash (SHA-512 of empty input). Must match GENESIS_HASH in
// governance/rzl/src/proofs/mod.rs.
export const GENESIS_HASH =
  "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
  "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e";

// Canonical 8-step subsystem order (must match CHAIN_DEFS in cdp.rs).
export const CANONICAL_SUBSYSTEMS = [
  "eee_namespace",
  "eee_tmpfs",
  "eee_memory",
  "dire_keys",
  "dire_identity",
  "fgx_forensic",
  "rzl_audit",
  "capsule_destroy",
] as const;

export interface VerifyResult {
  ok: boolean;
  chainValid: boolean;
  signatureValid: boolean;
  subsystemsAttested: string[];
  failedStep: number | null;
  failureReason: string;
  chainHash: string;
}

interface ChainStep {
  step?: number;
  subsystem?: string;
  operation?: string;
  method?: string;
  timestamp?: string;
  evidence_hash?: string;
  chain_hash?: string;
}

interface Attestation {
  algorithm?: string;
  key_id?: string;
  public_key?: string;
  signature?: string;
}

/**
 * Wave 16-A reserved-slot scope discriminator (2026-05-10).
 *
 * AuditProof scope. V1 always undefined / absent (workload scope is implicit).
 * Future Items 2 (sealed-proxy = "call") and 4 (sealed-middleware = "request")
 * populate this; Pattern 4 high-volume per-record AuditProofs use "batch".
 *
 * Forever-Standard discipline: field-additive Optional with
 * skip_serializing_if mechanic on the Rust side. The verifier MUST treat
 * this as opaque — chain integrity verification is independent of cdp_kind.
 */
export type CdpKind = "workload" | "request" | "call" | "batch";

interface ProofShape {
  cdp_version?: string;
  chain?: ChainStep[];
  attestation?: Attestation;
  /** Wave 16-A reserved slot — opaque to the verifier. */
  cdp_kind?: CdpKind;
}

/** Get a SubtleCrypto instance — works in Node 18+ and browsers. */
function getSubtle(): SubtleCrypto {
  // Node 18+ exposes globalThis.crypto.subtle. Browsers expose it directly.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  if (g.crypto?.subtle) return g.crypto.subtle as SubtleCrypto;
  throw new Error(
    "Web Crypto subtle not available (need Node 18+ or modern browser)",
  );
}

/** Base64 → Uint8Array. */
function base64Decode(b64: string): Uint8Array {
  // Strip "base64:" prefix if present.
  const stripped = b64.startsWith("base64:") ? b64.slice("base64:".length) : b64;
  if (typeof Buffer !== "undefined") {
    return new Uint8Array(Buffer.from(stripped, "base64"));
  }
  // Browser fallback
  const bin = atob(stripped);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** Strip "sha512:" prefix used in API JSON output. */
function stripSha512Prefix(value: string): string {
  return value.startsWith("sha512:") ? value.slice("sha512:".length) : value;
}

/** Hex-encode an ArrayBuffer / Uint8Array. */
function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * Re-implement compute_step_hash from governance/rzl/src/proofs/mod.rs.
 *
 * `chain_hash = SHA-512(prev \x00 subsystem \x00 "destroy" \x00 method \x00 timestamp)`
 *
 * `action` is hardcoded to "destroy" — `operation` field is descriptive only.
 */
async function computeStepHash(
  prevHash: string,
  subsystem: string,
  method: string,
  timestamp: string,
): Promise<string> {
  const enc = new TextEncoder();
  const NULL = new Uint8Array([0]);
  const parts: Uint8Array[] = [
    enc.encode(prevHash),
    NULL,
    enc.encode(subsystem),
    NULL,
    enc.encode("destroy"),
    NULL,
    enc.encode(method),
    NULL,
    enc.encode(timestamp),
  ];
  const totalLen = parts.reduce((acc, p) => acc + p.length, 0);
  const buf = new Uint8Array(totalLen);
  let off = 0;
  for (const p of parts) {
    buf.set(p, off);
    off += p.length;
  }
  const subtle = getSubtle();
  const digest = await subtle.digest("SHA-512", buf.buffer as ArrayBuffer);
  return bytesToHex(digest);
}

/**
 * Verify an AuditProof (CDP) offline.
 *
 * Three checks: chain integrity, Ed25519 signature, subsystem coverage.
 *
 * @param proof  AuditProof as parsed JSON object.
 * @returns VerifyResult with `.ok=true` iff all three checks pass.
 */
export async function verify(proof: unknown): Promise<VerifyResult> {
  const result: VerifyResult = {
    ok: false,
    chainValid: false,
    signatureValid: false,
    subsystemsAttested: [],
    failedStep: null,
    failureReason: "",
    chainHash: "",
  };

  if (typeof proof !== "object" || proof === null) {
    result.failureReason = `verify() expected object; got ${typeof proof}`;
    return result;
  }
  const p = proof as ProofShape;

  if (!Array.isArray(p.chain)) {
    result.failureReason = "Proof missing 'chain' array";
    return result;
  }

  // Subsystem coverage
  const subsystems = p.chain.map((s) => s.subsystem ?? "");
  result.subsystemsAttested = subsystems;
  if (
    subsystems.length !== CANONICAL_SUBSYSTEMS.length ||
    !subsystems.every((s, i) => s === CANONICAL_SUBSYSTEMS[i])
  ) {
    result.failureReason = `Subsystem mismatch: expected ${JSON.stringify(CANONICAL_SUBSYSTEMS)}, got ${JSON.stringify(subsystems)}`;
    return result;
  }

  // Chain integrity
  let prevHash = GENESIS_HASH;
  for (let idx = 0; idx < p.chain.length; idx++) {
    const step = p.chain[idx];
    const stored = stripSha512Prefix(step.chain_hash ?? "");
    const computed = await computeStepHash(
      prevHash,
      step.subsystem ?? "",
      step.method ?? "",
      step.timestamp ?? "",
    );
    if (stored !== computed) {
      result.failedStep = idx + 1;
      result.failureReason = `Chain hash mismatch at step ${idx + 1} (${step.subsystem}): stored=${stored.slice(0, 16)}... computed=${computed.slice(0, 16)}...`;
      result.chainHash = stored;
      return result;
    }
    prevHash = computed;
  }
  const finalChainHash = prevHash;
  result.chainValid = true;
  result.chainHash = finalChainHash;

  // Ed25519 signature
  const att = p.attestation ?? {};
  if (att.algorithm !== "Ed25519") {
    result.failureReason = `Unsupported signature algorithm: ${JSON.stringify(att.algorithm)} (expected Ed25519)`;
    return result;
  }

  let pubKeyBytes: Uint8Array;
  let sigBytes: Uint8Array;
  try {
    pubKeyBytes = base64Decode(att.public_key ?? "");
    sigBytes = base64Decode(att.signature ?? "");
  } catch (err) {
    result.failureReason = `Could not decode base64 attestation: ${err instanceof Error ? err.message : String(err)}`;
    return result;
  }

  let publicKey: CryptoKey;
  try {
    const subtle = getSubtle();
    publicKey = await subtle.importKey(
      "raw",
      pubKeyBytes.buffer as ArrayBuffer,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
  } catch (err) {
    result.failureReason = `Could not import Ed25519 public key: ${err instanceof Error ? err.message : String(err)}`;
    return result;
  }

  // CRITICAL: signature is over chain_hash AS ASCII HEX BYTES (not raw binary).
  // Per cdp.rs sign_and_destroy + cdp_document.rs:1100.
  const message = new TextEncoder().encode(finalChainHash);

  let valid: boolean;
  try {
    const subtle = getSubtle();
    valid = await subtle.verify(
      { name: "Ed25519" },
      publicKey,
      sigBytes.buffer as ArrayBuffer,
      message.buffer as ArrayBuffer,
    );
  } catch (err) {
    result.failureReason = `Ed25519 verify error: ${err instanceof Error ? err.message : String(err)}`;
    return result;
  }

  if (!valid) {
    result.failureReason = "Ed25519 signature did not verify against final chain_hash";
    return result;
  }

  result.signatureValid = true;
  result.ok = true;
  return result;
}

// ── Wave-N re-exports ───────────────────────────────────
//
// Per-record receipt + cross-org parent-proof composition surface. The Wave-N
// primitives live in `./wave_n.ts` and are re-exported here so consumers can
// `import { verifyFullAuditProof, verifyRecordReceipt } from "@nanorix/sdk/verifier"`.
//
// Forever-Standard: pre-Wave-N AuditProofs verify byte-identically
// via the (null, null) Step 8 branch in `computeStep8Amended`.
export {
  GENESIS_SHA512_HEX,
  PARENT_PROOF_MAX_DEPTH,
  PATTERN_TAGS_WIRE,
  WaveNVerifyError,
  merklePairHash,
  merkleRootSha512NullSeparated,
  computeRecordReceiptsMerkleRoot,
  computeParentProofsMerkleRoot,
  buildMerkleInclusionProof,
  verifyMerkleInclusionProof,
  computeActivityRoot,
  computeRecordChainHash,
  computeStep8Base,
  computeStep8Amended,
  detectParentProofCycle,
  enforceDepthCap,
  verifyRecordReceipt,
  verifyFullAuditProof,
} from "./wave_n.js";
export type {
  WaveNRecordReceipt,
  WaveNParentProofLink,
  WaveNVerifyResult,
  VerifyRecordReceiptOptions,
} from "./wave_n.js";

// Wave B Item 7 — Portable Receipt Bundle (.prb.json) surface.
export {
  PORTABLE_RECEIPT_BUNDLE_DISCLAIMER,
  BundleError,
  extractReceiptBundle,
  verifyReceiptBundle,
} from "./bundle.js";
export type { AuditProofAnchors, BundleErrorKind, PortableReceiptBundle } from "./bundle.js";

// Wave B Item 8 — Portable Pubkey Bundle (.ppb.json) surface.
export {
  PORTABLE_PUBKEY_BUNDLE_DISCLAIMER,
  PubkeyBundleError,
  buildPubkeyBundle,
  resolveParentKey,
  resolveParentKeyForever,
  verifyPubkeyBundle,
} from "./pubkey_bundle.js";
export type {
  BundleSignature,
  PortablePubkeyBundle,
  PubKeyEntry,
  PubkeyBundleErrorKind,
} from "./pubkey_bundle.js";
