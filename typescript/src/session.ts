/**
 * Session capsule helpers — `SessionResult` returned by `Capsule.session()`.
 *
 * Mirrors Python `nanorix._session` byte-identically. Per Agent A research:
 * adopted shape mirrors LangGraph CheckpointSaver (thread_id semantics,
 * parent_config chain) + AWS Bedrock AgentCore CMK-encrypted artifact
 * posture.
 */
import type { CdpAccess } from "./models.js";

export interface SessionResult {
  /** Customer's new state bytes (downloaded from /data/output/state.bin). */
  stateOut: Buffer;
  /** SHA-512 wire-format hash (`sha512:...`) of initial_state bytes. */
  artifactInputHash: string;
  /** SHA-512 wire-format hash of state_out (persist for next session). */
  artifactOutputHash: string;
  /** AuditProof for this session capsule. CDP includes session_id +
   *  parent_cdp_id + artifact_input_hash in metadata. */
  auditProof: CdpAccess;
  /** The customer-supplied session_id (echo for convenience). */
  sessionId: string;
}

function getSubtle(): SubtleCrypto {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  if (g.crypto?.subtle) return g.crypto.subtle as SubtleCrypto;
  throw new Error(
    "Web Crypto subtle not available (need Node 18+ or modern browser)",
  );
}

function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

/**
 * Compute SHA-512 wire-format hash of state bytes.
 *
 * Mirrors Python `_session.hash_state_bytes`. Returns `sha512:` prefix
 * + 128-char lowercase hex.
 */
export async function hashStateBytes(
  data: Uint8Array | Buffer,
): Promise<string> {
  const bytes = data instanceof Buffer ? new Uint8Array(data) : data;
  const digest = await getSubtle().digest(
    "SHA-512",
    bytes.buffer as ArrayBuffer,
  );
  return "sha512:" + bytesToHex(digest);
}

/**
 * Assemble metadata bindings for a session capsule.
 *
 * Reserved `nanorix:` keys carry continuity-chain bindings. Customer
 * extras merge underneath.
 */
export function buildSessionMetadata(opts: {
  sessionId: string;
  parentCdpId?: string;
  artifactInputHash: string;
  extra?: Record<string, string>;
}): Record<string, string> {
  const md: Record<string, string> = { ...(opts.extra ?? {}) };
  md["nanorix:session_id"] = opts.sessionId;
  if (opts.parentCdpId !== undefined) {
    md["nanorix:parent_cdp_id"] = opts.parentCdpId;
  }
  md["nanorix:artifact_input_hash"] = opts.artifactInputHash;
  return md;
}
