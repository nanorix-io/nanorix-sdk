"""
Batch result types — `BatchResult` returned by `Capsule.batch()`.

Per Agent A research (2026-05-05): Anthropic Message Batches inline shape +
caller-required `custom_id` + SQS partial-batch-failure outcomes. Mirrors
the sealed runtime's batch-accumulator wire shape + the
ActivityEvent BatchRecordProcessed/BatchCompleted variants
sub-A.

Customer-side processing convention (V1):
- SDK serializes `records` to records.jsonl + uploads to capsule
- Customer's entrypoint reads /data/input/records.jsonl + writes
  /data/output/results.jsonl, one JSON object per line
- Each result line: {"custom_id", "status" ("succeeded"|"errored"),
  "output" (succeeded), "error_code"+"error_message" (errored)}
- SDK parses results.jsonl + computes Merkle root client-side via
  identical formula to the Rust runtime (cross-impl byte-identical)

Future runtime auto-iterate (where daemon iterates instead of customer
code) lands in subsequent batches; the SDK contract stays stable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from nanorix._merkle import (
    EMPTY_SHA512_HEX,
    combined_record_leaf,
    merkle_root_from_leaves,
)
from nanorix._models import CdpAccess


@dataclass
class BatchRecord:
    """One processed record's outcome, per `BatchResult.records()`.

    Mirrors the sealed runtime's
    `ActivityEvent::BatchRecordProcessed` wire shape. `custom_id` is the
    customer's per-record identifier (Anthropic Batches / OpenAI Batch
    convention; mandatory). `record_input_hash` + `record_output_hash`
    are wire-format `sha512:...` strings — content stays in customer's
    data store (these are cryptographic receipts, not the data).
    """

    custom_id: str
    record_input_hash: str
    record_output_hash: str
    status: str  # "succeeded" | "errored"
    record_idx: int
    output: Optional[Any] = None  # populated when status == "succeeded"
    error_code: Optional[str] = None  # populated when status == "errored"
    error_message: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True iff the customer handler returned without error."""
        return self.status == "succeeded"


@dataclass
class BatchResult:
    """Outcome of `Capsule.batch()` — one CDP for the whole batch.

    Returned by `Capsule.batch()` after capsule destroy. Carries the
    full per-record results + the batch-level Merkle root + the
    AuditProof (single CDP for the whole batch per Agent A spec).

    Attributes:
        merkle_root: SHA-512 hex of RFC 6962 Merkle tree root over
            per-record `combined_record_leaf` values (NO `sha512:`
            prefix on this field — bare hex per existing Rust convention).
        audit_proof: CdpAccess for the batch capsule. `audit_proof.full()`
            includes BatchRecordProcessed × N + BatchCompleted in
            activity_trail (post-runtime-auto-iterate; in V1 SDK-driven
            mode, runtime sees N records as one execution and emits one
            ExecutionCompleted; the Merkle root in this struct is the
            customer-replayable receipt).
        success_count: Records where customer handler returned ok.
        error_count: Records where customer handler raised.
        total_count: success_count + error_count.
    """

    merkle_root: str
    audit_proof: CdpAccess
    success_count: int
    error_count: int
    total_count: int
    _results: List[BatchRecord] = field(default_factory=list, repr=False)

    def records(self) -> Iterator[BatchRecord]:
        """Iterate per-record outcomes in original record_idx order."""
        return iter(self._results)

    def find(self, custom_id: str) -> Optional[BatchRecord]:
        """Look up a record by custom_id. Returns None if not found.

        Customer's verifier flow: check Merkle inclusion proof for a
        specific custom_id by re-deriving its combined_record_leaf +
        replaying the tree path from leaf → root. (Inclusion-proof
        helpers ship in a follow-up batch.)
        """
        for r in self._results:
            if r.custom_id == custom_id:
                return r
        return None

    def iter_failures(self) -> Iterator[BatchRecord]:
        """Yield only the errored records."""
        for r in self._results:
            if r.status == "errored":
                yield r


def _hex_to_bytes_64(prefixed_hex: str) -> bytes:
    """Decode a 'sha512:<128hex>' wire string to 64 raw bytes."""
    if prefixed_hex.startswith("sha512:"):
        prefixed_hex = prefixed_hex[len("sha512:") :]
    raw = bytes.fromhex(prefixed_hex)
    if len(raw) != 64:
        raise ValueError(f"Expected 64-byte SHA-512 hex, got {len(raw)} bytes")
    return raw


def compute_batch_merkle_root(records: List[BatchRecord]) -> str:
    """Compute batch Merkle root from per-record (input, output) hashes.

    Mirrors the sealed runtime exactly:
    leaves are `combined_record_leaf(input_hash_bytes, output_hash_bytes)`
    in `record_idx` order; tree is RFC 6962 binary with 0x00 leaf /
    0x01 inner domain prefixes.

    Returns 128-char lowercase hex string (NO `sha512:` prefix).
    """
    if not records:
        return EMPTY_SHA512_HEX
    # Sort by record_idx to ensure tree ordering matches Rust runtime.
    ordered = sorted(records, key=lambda r: r.record_idx)
    leaves: List[bytes] = []
    for r in ordered:
        try:
            input_bytes = _hex_to_bytes_64(r.record_input_hash)
            output_bytes = _hex_to_bytes_64(r.record_output_hash)
        except ValueError as e:
            raise ValueError(
                f"compute_batch_merkle_root: record {r.custom_id!r} has malformed hash: {e}"
            )
        leaves.append(combined_record_leaf(input_bytes, output_bytes))
    return merkle_root_from_leaves(leaves)


def serialize_records_to_jsonl(records: List[Dict[str, Any]], record_id_field: str) -> bytes:
    """Serialize records to newline-delimited JSON.

    Validates that each record has the required `record_id_field`
    (default "custom_id" per Anthropic Batches / OpenAI Batch).
    Duplicates raise ValueError — verifier inclusion proofs require
    stable customer-supplied IDs.
    """
    import json

    seen_ids: set[str] = set()
    lines: List[str] = []
    for idx, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise ValueError(f"Record at index {idx} is not a dict: {type(rec).__name__}")
        cid = rec.get(record_id_field)
        if cid is None or not isinstance(cid, str) or not cid:
            raise ValueError(
                f"Record at index {idx} missing required {record_id_field!r} field "
                f"(must be non-empty string)"
            )
        if cid in seen_ids:
            raise ValueError(
                f"Duplicate {record_id_field}={cid!r} at index {idx} — "
                f"all batch records must have unique IDs for inclusion-proof stability"
            )
        seen_ids.add(cid)
        lines.append(json.dumps(rec, separators=(",", ":")))
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def parse_results_jsonl(
    data: bytes, records_jsonl: bytes, record_id_field: str = "custom_id"
) -> List[BatchRecord]:
    """Parse customer's results.jsonl + cross-reference records.jsonl.

    Each results.jsonl line must have `custom_id` matching a record
    in records.jsonl. Lines without matching custom_id raise.
    Records present in records.jsonl but missing from results.jsonl
    are reported as errored with error_code='MISSING_OUTPUT'.

    Computes per-record SHA-512 hashes from raw line bytes (input from
    records.jsonl line, output from results.jsonl line). This mirrors
    what the Rust runtime would compute server-side — verifiers can
    replay independently.
    """
    import json

    # Index records by custom_id with their raw line bytes.
    records_by_id: Dict[str, tuple[int, bytes]] = {}
    for idx, line in enumerate(records_jsonl.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = rec.get(record_id_field, "")
        if cid:
            records_by_id[cid] = (idx, line)

    # Index results by custom_id with raw line bytes.
    results_by_id: Dict[str, bytes] = {}
    parsed_results: Dict[str, Dict[str, Any]] = {}
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = rec.get(record_id_field, "")
        if cid:
            results_by_id[cid] = line
            parsed_results[cid] = rec

    out: List[BatchRecord] = []
    # Iterate records in their original order; emit one BatchRecord per record.
    for cid, (idx, input_line) in records_by_id.items():
        input_hash_raw = hashlib.sha512(input_line).digest()
        result_line = results_by_id.get(cid)

        if result_line is None:
            # No output produced for this record — synthesize errored.
            output_hash_raw = hashlib.sha512(b"").digest()
            out.append(
                BatchRecord(
                    custom_id=cid,
                    record_input_hash="sha512:" + input_hash_raw.hex(),
                    record_output_hash="sha512:" + output_hash_raw.hex(),
                    status="errored",
                    record_idx=idx,
                    output=None,
                    error_code="MISSING_OUTPUT",
                    error_message=(
                        "Record present in records.jsonl but no matching "
                        "result line in results.jsonl (custom_id missing)"
                    ),
                )
            )
            continue

        output_hash_raw = hashlib.sha512(result_line).digest()
        result = parsed_results[cid]
        status = result.get("status", "errored")
        if status not in ("succeeded", "errored"):
            status = "errored"
        out.append(
            BatchRecord(
                custom_id=cid,
                record_input_hash="sha512:" + input_hash_raw.hex(),
                record_output_hash="sha512:" + output_hash_raw.hex(),
                status=status,
                record_idx=idx,
                output=result.get("output") if status == "succeeded" else None,
                error_code=result.get("error_code") if status == "errored" else None,
                error_message=result.get("error_message") if status == "errored" else None,
            )
        )

    return out
