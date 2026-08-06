/**
 * Batch result types — `BatchResult` returned by `Capsule.batch()`.
 *
 * Mirrors Python `nanorix._batch` (BatchRecord, BatchResult,
 * compute_batch_merkle_root, parse_results_jsonl, serialize_records_to_jsonl).
 *
 * Per Agent A research (2026-05-05): Anthropic Message Batches inline
 * shape + caller-required `customId` (wire field `custom_id` to match
 * Anthropic + OpenAI convention) + SQS partial-batch-failure outcomes.
 */
import { combinedRecordLeaf, EMPTY_SHA512_HEX, merkleRootFromLeaves } from "./merkle.js";
import type { CdpAccess } from "./models.js";

export interface BatchRecord {
  /** Customer's per-record identifier (Anthropic Batches `custom_id`). */
  customId: string;
  /** Wire-format SHA-512 of the input bytes. `sha512:<128hex>`. */
  recordInputHash: string;
  /** Wire-format SHA-512 of the output bytes. */
  recordOutputHash: string;
  /** "succeeded" | "errored" — closed enum mirroring Rust BatchRecordOutcome. */
  status: "succeeded" | "errored";
  /** 0-based record index — matches Merkle leaf position. */
  recordIdx: number;
  /** Customer-emitted output (status === "succeeded"). */
  output?: unknown;
  /** Customer-defined error category (status === "errored"). */
  errorCode?: string;
  errorMessage?: string;
}

export interface BatchResult {
  /** RFC 6962 Merkle root over per-record (input || output) leaves. 128 hex chars, no prefix. */
  merkleRoot: string;
  /** Single AuditProof for the whole batch. */
  auditProof: CdpAccess;
  successCount: number;
  errorCount: number;
  totalCount: number;
  /** Iterate per-record outcomes in original recordIdx order. */
  records(): IterableIterator<BatchRecord>;
  /** Look up a record by customId. Returns undefined if not found. */
  find(customId: string): BatchRecord | undefined;
  /** Yield only the errored records. */
  iterFailures(): IterableIterator<BatchRecord>;
}

function hexToBytes64(prefixedHex: string): Uint8Array {
  const stripped = prefixedHex.startsWith("sha512:")
    ? prefixedHex.slice("sha512:".length)
    : prefixedHex;
  if (stripped.length !== 128) {
    throw new Error(`Expected 128-hex-char SHA-512, got ${stripped.length}`);
  }
  const bytes = new Uint8Array(64);
  for (let i = 0; i < 64; i++) {
    bytes[i] = parseInt(stripped.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/**
 * Compute batch Merkle root from per-record (input, output) hashes.
 *
 * Sorts by `recordIdx` to ensure tree ordering matches Rust runtime
 * + Python SDK byte-identically (cross-impl invariant).
 *
 * @returns 128-char hex (no `sha512:` prefix).
 */
export async function computeBatchMerkleRoot(
  records: BatchRecord[],
): Promise<string> {
  if (records.length === 0) return EMPTY_SHA512_HEX;
  const ordered = [...records].sort((a, b) => a.recordIdx - b.recordIdx);
  const leaves: Uint8Array[] = [];
  for (const r of ordered) {
    const inputBytes = hexToBytes64(r.recordInputHash);
    const outputBytes = hexToBytes64(r.recordOutputHash);
    leaves.push(await combinedRecordLeaf(inputBytes, outputBytes));
  }
  return await merkleRootFromLeaves(leaves);
}

/**
 * Serialize records to newline-delimited JSON.
 *
 * Validates that each record has the required `recordIdField` value
 * (default "custom_id" per Anthropic + OpenAI Batch convention).
 * Duplicates throw — verifier inclusion proofs require stable IDs.
 */
export function serializeRecordsToJsonl(
  records: Record<string, unknown>[],
  recordIdField: string,
): Uint8Array {
  const seen = new Set<string>();
  const lines: string[] = [];
  for (let idx = 0; idx < records.length; idx++) {
    const rec = records[idx];
    if (rec === null || typeof rec !== "object") {
      throw new Error(`Record at index ${idx} is not an object`);
    }
    const cid = rec[recordIdField];
    if (typeof cid !== "string" || cid.length === 0) {
      throw new Error(
        `Record at index ${idx} missing required ${recordIdField} field (must be non-empty string)`,
      );
    }
    if (seen.has(cid)) {
      throw new Error(
        `Duplicate ${recordIdField}=${cid} at index ${idx} — all batch records must have unique IDs`,
      );
    }
    seen.add(cid);
    lines.push(JSON.stringify(rec));
  }
  return new TextEncoder().encode(lines.join("\n") + (lines.length > 0 ? "\n" : ""));
}

/**
 * Parse customer's results.jsonl + cross-reference records.jsonl.
 *
 * Computes per-record SHA-512 hashes from raw line bytes (not parsed
 * dicts) so verifiers replay deterministically. Records present in
 * records.jsonl but missing from results.jsonl are reported as errored
 * with errorCode="MISSING_OUTPUT". Invalid status normalizes to "errored".
 */
export async function parseResultsJsonl(
  data: Uint8Array,
  recordsJsonl: Uint8Array,
  recordIdField = "custom_id",
): Promise<BatchRecord[]> {
  const dec = new TextDecoder();
  const subtle = (
    globalThis as unknown as { crypto: Crypto }
  ).crypto.subtle;

  // Index records by ID with raw line bytes.
  const recordsByCid = new Map<string, { idx: number; line: Uint8Array }>();
  const recordsLines = dec.decode(recordsJsonl).split("\n");
  let idx = 0;
  for (const line of recordsLines) {
    if (line.trim().length === 0) continue;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line);
    } catch {
      idx++;
      continue;
    }
    const cid = parsed[recordIdField];
    if (typeof cid === "string" && cid.length > 0) {
      recordsByCid.set(cid, { idx, line: new TextEncoder().encode(line) });
    }
    idx++;
  }

  // Index results by ID with raw line bytes + parsed body.
  const resultsByCid = new Map<
    string,
    { line: Uint8Array; parsed: Record<string, unknown> }
  >();
  const resultsLines = dec.decode(data).split("\n");
  for (const line of resultsLines) {
    if (line.trim().length === 0) continue;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue;
    }
    const cid = parsed[recordIdField];
    if (typeof cid === "string" && cid.length > 0) {
      resultsByCid.set(cid, { line: new TextEncoder().encode(line), parsed });
    }
  }

  const out: BatchRecord[] = [];
  for (const [cid, rec] of recordsByCid) {
    const inputHashRaw = new Uint8Array(
      await subtle.digest("SHA-512", rec.line.buffer as ArrayBuffer),
    );
    const inputHashHex = "sha512:" + bytesToHex(inputHashRaw);

    const result = resultsByCid.get(cid);
    if (result === undefined) {
      const emptyHash = new Uint8Array(
        await subtle.digest("SHA-512", new Uint8Array(0).buffer as ArrayBuffer),
      );
      out.push({
        customId: cid,
        recordInputHash: inputHashHex,
        recordOutputHash: "sha512:" + bytesToHex(emptyHash),
        status: "errored",
        recordIdx: rec.idx,
        errorCode: "MISSING_OUTPUT",
        errorMessage:
          "Record present in records.jsonl but no matching result line in results.jsonl",
      });
      continue;
    }

    const outputHashRaw = new Uint8Array(
      await subtle.digest("SHA-512", result.line.buffer as ArrayBuffer),
    );
    const status =
      result.parsed.status === "succeeded" ? "succeeded" : "errored";

    out.push({
      customId: cid,
      recordInputHash: inputHashHex,
      recordOutputHash: "sha512:" + bytesToHex(outputHashRaw),
      status,
      recordIdx: rec.idx,
      output: status === "succeeded" ? result.parsed.output : undefined,
      errorCode:
        status === "errored"
          ? typeof result.parsed.error_code === "string"
            ? result.parsed.error_code
            : undefined
          : undefined,
      errorMessage:
        status === "errored"
          ? typeof result.parsed.error_message === "string"
            ? result.parsed.error_message
            : undefined
          : undefined,
    });
  }

  return out;
}

function bytesToHex(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let out = "";
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}
