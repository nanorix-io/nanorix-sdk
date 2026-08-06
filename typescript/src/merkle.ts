/**
 * RFC 6962 binary Merkle tree — pure TypeScript via Web Crypto.
 *
 * Mirrors Python `nanorix._merkle` and Rust
 * the sealed runtime EXACTLY.
 * Used by `Capsule.batch()` to compute client-side Merkle root that
 * matches what the server-side runtime would emit.
 *
 * Domain-separation per RFC 6962 §2.1:
 * - Leaf: SHA-512(0x00 || leaf_bytes)
 * - Inner: SHA-512(0x01 || left || right)
 * - Odd count: lone tail node promoted unchanged.
 *
 * **FROZEN** — changing this would invalidate every
 * previously-emitted batch Merkle root.
 */

/** SHA-512 of empty input — matches Python EMPTY_SHA512_HEX + Rust GENESIS_HASH. */
export const EMPTY_SHA512_HEX =
  "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce" +
  "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e";

const LEAF_PREFIX = new Uint8Array([0x00]);
const INNER_PREFIX = new Uint8Array([0x01]);

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

async function sha512(...parts: Uint8Array[]): Promise<Uint8Array> {
  const total = parts.reduce((a, p) => a + p.length, 0);
  const buf = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    buf.set(p, off);
    off += p.length;
  }
  const digest = await getSubtle().digest("SHA-512", buf.buffer as ArrayBuffer);
  return new Uint8Array(digest);
}

/**
 * Compute RFC 6962 binary Merkle tree root.
 *
 * @param leaves  Array of 64-byte SHA-512 hashes (each Uint8Array length 64).
 *                Order matters — matches `recordIdx` in BatchRecord.
 * @returns 128-character lowercase hex (no `sha512:` prefix).
 */
export async function merkleRootFromLeaves(
  leaves: Uint8Array[],
): Promise<string> {
  if (leaves.length === 0) return EMPTY_SHA512_HEX;
  for (const leaf of leaves) {
    if (!(leaf instanceof Uint8Array) || leaf.length !== 64) {
      throw new Error(
        `merkleRootFromLeaves: each leaf must be 64-byte SHA-512 output; got length ${leaf?.length ?? "?"}`,
      );
    }
  }

  // Apply leaf-domain prefix.
  let level: Uint8Array[] = [];
  for (const leaf of leaves) {
    level.push(await sha512(LEAF_PREFIX, leaf));
  }

  while (level.length > 1) {
    const next: Uint8Array[] = [];
    let i = 0;
    while (i + 1 < level.length) {
      next.push(await sha512(INNER_PREFIX, level[i], level[i + 1]));
      i += 2;
    }
    if (i < level.length) {
      next.push(level[i]); // odd tail promoted unchanged
    }
    level = next;
  }

  return bytesToHex(level[0]);
}

/**
 * Derive Merkle leaf from per-record (input, output) hash pair.
 *
 * Mirrors Python `combined_record_leaf` and Rust `combined_record_leaf`.
 *
 * @param inputHash  64-byte raw SHA-512 of input bytes.
 * @param outputHash 64-byte raw SHA-512 of output bytes.
 * @returns 64-byte SHA-512 of the concatenation.
 */
export async function combinedRecordLeaf(
  inputHash: Uint8Array,
  outputHash: Uint8Array,
): Promise<Uint8Array> {
  if (inputHash.length !== 64 || outputHash.length !== 64) {
    throw new Error(
      `combinedRecordLeaf: input + output must each be 64-byte; got ${inputHash.length} + ${outputHash.length}`,
    );
  }
  return await sha512(inputHash, outputHash);
}

/** SHA-512 helper returning lowercase hex with `sha512:` prefix. */
export async function hashRecordBytes(data: Uint8Array): Promise<string> {
  const digest = await sha512(data);
  return "sha512:" + bytesToHex(digest);
}
