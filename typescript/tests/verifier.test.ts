/**
 * Batch 33-foundation sub-D tests — TS offline AuditProof verifier.
 *
 * Mirrors Python sub-C test coverage: builds synthetic-but-valid CDPs
 * using the canonical chain hash formula, signs with Web Crypto Ed25519,
 * verifies the result. Failure modes: bad signature, broken chain,
 * subsystem reorder, missing subsystem, wrong algorithm, malformed input.
 *
 * Requires Node 18+ (Web Crypto Ed25519). Skips on older Node.
 */
import { describe, it, expect } from "vitest";
import {
  verify,
  GENESIS_HASH,
  CANONICAL_SUBSYSTEMS,
} from "../src/verifier/index.js";

// Detect Web Crypto Ed25519 support; skip suite if not available.
async function ed25519Supported(): Promise<boolean> {
  try {
    const c = (globalThis as unknown as { crypto?: Crypto }).crypto;
    if (!c?.subtle) return false;
    await c.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
    return true;
  } catch {
    return false;
  }
}

const subtle = (globalThis as unknown as { crypto: Crypto }).crypto.subtle;

function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

function bytesToBase64(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  const bin = String.fromCharCode(...bytes);
  return Buffer.from(bin, "binary").toString("base64");
}

async function computeStepHash(
  prev: string,
  subsystem: string,
  method: string,
  timestamp: string,
): Promise<string> {
  const enc = new TextEncoder();
  const NULL = new Uint8Array([0]);
  const parts = [
    enc.encode(prev),
    NULL,
    enc.encode(subsystem),
    NULL,
    enc.encode("destroy"),
    NULL,
    enc.encode(method),
    NULL,
    enc.encode(timestamp),
  ];
  const total = parts.reduce((a, p) => a + p.length, 0);
  const buf = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    buf.set(p, off);
    off += p.length;
  }
  const digest = await subtle.digest("SHA-512", buf);
  return bytesToHex(digest);
}

async function makeSyntheticProof(): Promise<{
  proof: Record<string, unknown>;
  privateKey: CryptoKey;
}> {
  const keyPair = (await subtle.generateKey({ name: "Ed25519" }, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const pubBytes = new Uint8Array(
    await subtle.exportKey("raw", keyPair.publicKey),
  );

  const methods = [
    "procfs_verification",
    "mountinfo_verification",
    "dod_5220_multipass_wipe",
    "ed25519_key_destruction",
    "credential_incineration",
    "merkle_tree_verification",
    "hash_chain_validation",
    "capsule_lifecycle_verification",
  ];
  const timestamp = "2026-05-05T12:00:00.000000000Z";
  const chain: Record<string, unknown>[] = [];
  let prev = GENESIS_HASH;
  for (let i = 0; i < CANONICAL_SUBSYSTEMS.length; i++) {
    const subsys = CANONICAL_SUBSYSTEMS[i];
    const method = methods[i];
    const h = await computeStepHash(prev, subsys, method, timestamp);
    chain.push({
      step: i + 1,
      subsystem: subsys,
      operation: "destroy",
      method,
      timestamp,
      evidence_hash: "sha512:" + "0".repeat(128),
      chain_hash: "sha512:" + h,
    });
    prev = h;
  }
  const finalChainHash = prev;
  const sig = new Uint8Array(
    await subtle.sign(
      { name: "Ed25519" },
      keyPair.privateKey,
      new TextEncoder().encode(finalChainHash),
    ),
  );

  return {
    proof: {
      cdp_version: "1.0",
      chain,
      attestation: {
        algorithm: "Ed25519",
        key_id: "test-key-1",
        public_key: "base64:" + bytesToBase64(pubBytes),
        signature: "base64:" + bytesToBase64(sig),
      },
    },
    privateKey: keyPair.privateKey,
  };
}

describe.skipIf(!(await ed25519Supported()))("Verifier (Web Crypto Ed25519)", () => {
  it("synthetic proof verifies ok", async () => {
    const { proof } = await makeSyntheticProof();
    const result = await verify(proof);
    expect(result.ok).toBe(true);
    expect(result.chainValid).toBe(true);
    expect(result.signatureValid).toBe(true);
    expect(result.subsystemsAttested).toEqual(Array.from(CANONICAL_SUBSYSTEMS));
    expect(result.failureReason).toBe("");
  });

  it("genesis hash matches canonical SHA-512('') vector", () => {
    expect(GENESIS_HASH).toBe(
      "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e",
    );
  });

  it("chain break at step 5 fails with failedStep=5", async () => {
    const { proof } = await makeSyntheticProof();
    const chain = (proof as { chain: Record<string, unknown>[] }).chain;
    chain[4].chain_hash = "sha512:" + "0".repeat(128);
    const result = await verify(proof);
    expect(result.ok).toBe(false);
    expect(result.chainValid).toBe(false);
    expect(result.failedStep).toBe(5);
    expect(result.failureReason).toContain("step 5");
  });

  it("signature mismatch fails with chainValid=true, signatureValid=false", async () => {
    const { proof } = await makeSyntheticProof();
    // Replace signature with valid signature over different content.
    const otherKey = (await subtle.generateKey({ name: "Ed25519" }, true, [
      "sign",
      "verify",
    ])) as CryptoKeyPair;
    const bogus = new Uint8Array(
      await subtle.sign(
        { name: "Ed25519" },
        otherKey.privateKey,
        new TextEncoder().encode("different message"),
      ),
    );
    (proof as { attestation: { signature: string } }).attestation.signature =
      "base64:" + bytesToBase64(bogus);
    const result = await verify(proof);
    expect(result.ok).toBe(false);
    expect(result.chainValid).toBe(true);
    expect(result.signatureValid).toBe(false);
    expect(result.failureReason).toContain("signature did not verify");
  });

  it("subsystem reorder fails", async () => {
    const { proof } = await makeSyntheticProof();
    const chain = (proof as { chain: Record<string, unknown>[] }).chain;
    const tmp = chain[2].subsystem;
    chain[2].subsystem = chain[3].subsystem;
    chain[3].subsystem = tmp;
    const result = await verify(proof);
    expect(result.ok).toBe(false);
    expect(result.failureReason).toContain("Subsystem mismatch");
  });

  it("missing subsystem fails", async () => {
    const { proof } = await makeSyntheticProof();
    (proof as { chain: Record<string, unknown>[] }).chain.pop();
    const result = await verify(proof);
    expect(result.ok).toBe(false);
    expect(result.failureReason).toContain("Subsystem mismatch");
  });

  it("wrong algorithm fails", async () => {
    const { proof } = await makeSyntheticProof();
    (proof as { attestation: { algorithm: string } }).attestation.algorithm =
      "RSA-PSS-SHA256";
    const result = await verify(proof);
    expect(result.ok).toBe(false);
    expect(result.signatureValid).toBe(false);
    expect(result.failureReason).toContain("Unsupported signature algorithm");
  });

  it("missing chain field fails", async () => {
    const result = await verify({ attestation: {}, cdp_version: "1.0" });
    expect(result.ok).toBe(false);
    expect(result.failureReason.toLowerCase()).toContain("chain");
  });

  it("non-object input fails gracefully", async () => {
    const result = await verify(null);
    expect(result.ok).toBe(false);
    expect(result.failureReason).toContain("expected object");
  });
});
