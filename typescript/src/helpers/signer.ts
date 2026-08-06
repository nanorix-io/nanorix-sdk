/**
 * Customer-side per-event signing helpers — Order 6 / Gap 3 (TS).
 *
 * Mirrors Python `nanorix.helpers.signer` byte-identically. Per Agent C
 * research: customer hosts an HTTPS-mTLS signing endpoint that receives
 * ActivityEvent payloads + returns Ed25519 signatures. Nanorix daemon's
 * signing-flow background task POSTs to this endpoint when capsulefile
 * has signing_config.per_event=true.
 *
 * This module provides the customer-side SDK harness for Node.js +
 * modern browsers via Web Crypto (Ed25519). Customer's KMS integration
 * is THEIR responsibility; this SDK ships the signing primitive +
 * Ed25519 dev-mode key.
 */

/** Per-event customer signature row matching the wire-format schema. */
export interface EventAttestation {
  algorithm: string; // "Ed25519" V1; "RSA-PSS-SHA256" reserved post-V1
  publicKey: string; // base64-encoded with "base64:" prefix
  signature: string; // base64-encoded with "base64:" prefix
  eventId: string;
  signedAt: string; // RFC 3339 UTC timestamp
}

/** Render EventAttestation as wire-format dict for HTTPS endpoint response. */
export function attestationToWireDict(att: EventAttestation): Record<string, string> {
  return {
    algorithm: att.algorithm,
    public_key: att.publicKey,
    signature: att.signature,
    event_id: att.eventId,
    signed_at: att.signedAt,
  };
}

/**
 * Customer-implementable signer interface.
 *
 * Production customers wire their KMS (AWS KMS / GCP KMS / Azure Key
 * Vault) by implementing this interface. Dev/testing uses
 * `Ed25519GeneratedKey`.
 */
export interface EventSigner {
  sign(message: Uint8Array): Promise<Uint8Array>;
  publicKeyBytes(): Promise<Uint8Array>;
  algorithmName(): string;
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
  for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, "0");
  return out;
}

function bytesToBase64(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function base64ToBytes(b64: string): Uint8Array {
  const stripped = b64.startsWith("base64:") ? b64.slice("base64:".length) : b64;
  if (typeof Buffer !== "undefined") {
    return new Uint8Array(Buffer.from(stripped, "base64"));
  }
  const bin = atob(stripped);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/**
 * Web-Crypto-generated Ed25519 keypair for dev/testing.
 *
 * Production customers should implement `EventSigner` against their
 * KMS. This class generates a fresh keypair in-memory each instantiation
 * — suitable for unit tests + local dev only.
 */
export class Ed25519GeneratedKey implements EventSigner {
  private privateKey: CryptoKey;
  private publicKey: CryptoKey;
  private cachedPublicBytes: Uint8Array | null = null;

  private constructor(privateKey: CryptoKey, publicKey: CryptoKey) {
    this.privateKey = privateKey;
    this.publicKey = publicKey;
  }

  /** Generate a fresh Ed25519 keypair. */
  static async generate(): Promise<Ed25519GeneratedKey> {
    const subtle = getSubtle();
    const keyPair = (await subtle.generateKey({ name: "Ed25519" }, true, [
      "sign",
      "verify",
    ])) as CryptoKeyPair;
    return new Ed25519GeneratedKey(keyPair.privateKey, keyPair.publicKey);
  }

  async sign(message: Uint8Array): Promise<Uint8Array> {
    const subtle = getSubtle();
    const sig = await subtle.sign(
      { name: "Ed25519" },
      this.privateKey,
      message.buffer as ArrayBuffer,
    );
    return new Uint8Array(sig);
  }

  async publicKeyBytes(): Promise<Uint8Array> {
    if (this.cachedPublicBytes !== null) return this.cachedPublicBytes;
    const subtle = getSubtle();
    const raw = await subtle.exportKey("raw", this.publicKey);
    this.cachedPublicBytes = new Uint8Array(raw);
    return this.cachedPublicBytes;
  }

  algorithmName(): string {
    return "Ed25519";
  }
}

/**
 * Compute canonical-JSON byte representation of an ActivityEvent.
 *
 * Mirrors Python `canonicalize_event` byte-identically: sorted keys,
 * no whitespace. Uses TextEncoder for UTF-8 byte form.
 */
export function canonicalizeEvent(event: Record<string, unknown>): Uint8Array {
  const sortedJson = JSON.stringify(event, Object.keys(event).sort());
  // Recursively sort nested objects (JSON.stringify with array doesn't recurse).
  const recursiveSort = (obj: unknown): unknown => {
    if (obj === null || typeof obj !== "object") return obj;
    if (Array.isArray(obj)) return obj.map(recursiveSort);
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[k] = recursiveSort((obj as Record<string, unknown>)[k]);
    }
    return sorted;
  };
  // Re-stringify with deep sort (top-level was sorted via JSON.stringify with array).
  const deepSorted = recursiveSort(event);
  void sortedJson;
  return new TextEncoder().encode(JSON.stringify(deepSorted));
}

/**
 * Sign one ActivityEvent and return an EventAttestation.
 *
 * @param signer  EventSigner implementation.
 * @param event   Parsed ActivityEvent dict.
 * @param eventId Customer-assigned event ID; matches activity_trail correlation.
 */
export async function signActivityEvent(
  signer: EventSigner,
  event: Record<string, unknown>,
  opts: { eventId: string; capsuleId?: string },
): Promise<EventAttestation> {
  const canonicalBytes = canonicalizeEvent(event);
  const subtle = getSubtle();
  const messageHash = new Uint8Array(
    await subtle.digest("SHA-512", canonicalBytes.buffer as ArrayBuffer),
  );
  const signature = await signer.sign(messageHash);
  const publicKey = await signer.publicKeyBytes();

  const signedAt = new Date().toISOString();

  return {
    algorithm: signer.algorithmName(),
    publicKey: "base64:" + bytesToBase64(publicKey),
    signature: "base64:" + bytesToBase64(signature),
    eventId: opts.eventId,
    signedAt,
  };
}

/**
 * Verify a customer-signed EventAttestation against its event.
 *
 * Mirrors Python `verify_event_attestation`. Returns true iff the
 * signature verifies; false on any error (corrupt sig, tampered
 * event, unsupported algorithm).
 */
export async function verifyEventAttestation(
  attestation: EventAttestation,
  event: Record<string, unknown>,
): Promise<boolean> {
  if (attestation.algorithm !== "Ed25519") return false;
  const pubKeyBytes = base64ToBytes(attestation.publicKey);
  const sigBytes = base64ToBytes(attestation.signature);
  const canonicalBytes = canonicalizeEvent(event);
  const subtle = getSubtle();
  try {
    const messageHash = new Uint8Array(
      await subtle.digest("SHA-512", canonicalBytes.buffer as ArrayBuffer),
    );
    const publicKey = await subtle.importKey(
      "raw",
      pubKeyBytes.buffer as ArrayBuffer,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    return await subtle.verify(
      { name: "Ed25519" },
      publicKey,
      sigBytes.buffer as ArrayBuffer,
      messageHash.buffer as ArrayBuffer,
    );
  } catch {
    return false;
  }
}

void bytesToHex; // exported via attestation hashes; reserved for future use
