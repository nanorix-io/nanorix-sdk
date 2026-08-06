"""
Order 2 / Gap 1 tests — Capsule.batch() Anthropic-Batches inline shape.

Covers serialize_records_to_jsonl validators (custom_id required +
unique), parse_results_jsonl cross-reference (records.jsonl ↔
results.jsonl join + missing-output synthesis + status normalization),
client-side Merkle root determinism, end-to-end Capsule.batch() flow
against mocked /v1/capsules + /v1/capsules/{id}/upload + /exec + /output
+ destroy.
"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from nanorix._batch import (
    BatchRecord,
    compute_batch_merkle_root,
    parse_results_jsonl,
    serialize_records_to_jsonl,
)
from nanorix._exceptions import BadRequestError, ExecutionError
from nanorix._merkle import (
    EMPTY_SHA512_HEX,
    combined_record_leaf,
    merkle_root_from_leaves,
)


# ── Merkle root cross-impl tests ────────────────────────────────────


def test_empty_leaves_returns_genesis():
    assert merkle_root_from_leaves([]) == EMPTY_SHA512_HEX


def test_single_leaf_applies_leaf_prefix():
    leaf = b"a" * 64
    out = merkle_root_from_leaves([leaf])
    expected = hashlib.sha512(b"\x00" + leaf).hexdigest()
    assert out == expected


def test_two_leaves_inner_hash_matches_rfc_6962():
    l0 = b"a" * 64
    l1 = b"b" * 64
    leaf0_hash = hashlib.sha512(b"\x00" + l0).digest()
    leaf1_hash = hashlib.sha512(b"\x00" + l1).digest()
    expected = hashlib.sha512(b"\x01" + leaf0_hash + leaf1_hash).hexdigest()
    assert merkle_root_from_leaves([l0, l1]) == expected


def test_odd_count_promotes_lone_tail():
    """RFC 6962 §2.1: lone tail node carries forward unchanged.

    For 3 leaves: pair (l0, l1) → inner; promote l2; then pair (inner, l2_hashed).
    """
    l0 = b"a" * 64
    l1 = b"b" * 64
    l2 = b"c" * 64
    h0 = hashlib.sha512(b"\x00" + l0).digest()
    h1 = hashlib.sha512(b"\x00" + l1).digest()
    h2 = hashlib.sha512(b"\x00" + l2).digest()
    inner01 = hashlib.sha512(b"\x01" + h0 + h1).digest()
    expected = hashlib.sha512(b"\x01" + inner01 + h2).hexdigest()
    assert merkle_root_from_leaves([l0, l1, l2]) == expected


def test_combined_record_leaf_hashes_concatenation():
    inp = bytes(range(64))
    out = bytes(range(64, 128))
    expected = hashlib.sha512(inp + out).digest()
    assert combined_record_leaf(inp, out) == expected
    assert len(combined_record_leaf(inp, out)) == 64


def test_merkle_root_pinned_to_known_vector_4_leaves():
    """Cross-impl invariant pin: identical input → identical root across
    Python + TypeScript + Rust implementations.

    These hardcoded values let the TS test suite assert the SAME root
    for the SAME input. If either implementation drifts, both tests
    fail — protects against silent cross-impl divergence that would
    break verifier replay.
    """
    leaves = [
        bytes([0x00] * 64),
        bytes([0x11] * 64),
        bytes([0x22] * 64),
        bytes([0x33] * 64),
    ]
    expected = (
        "55e380af6c5f76695faf51db35f60a579acdde5ec9dca0f3bbe2be441525a66a"
        "b48781258c8eca8a461ee4d162f501483cd6ee130fbeb1fdb4dbb2bacaec094b"
    )
    assert merkle_root_from_leaves(leaves) == expected


def test_merkle_root_pinned_to_known_vector_1_leaf():
    """Cross-impl pin: single-leaf case (0x00 leaf prefix applied)."""
    leaves = [bytes([0x00] * 64)]
    expected = (
        "19dc6ae12de08b21b36c1ec7f353ce9e7cef73fa4d1354c436234167f0847bc9"
        "e2b85e2f36208f773ef324e2d79e6af1beca4470e44b8672b47d077efe33a1f8"
    )
    assert merkle_root_from_leaves(leaves) == expected


def test_compute_batch_merkle_root_orders_by_record_idx():
    """Merkle root must be deterministic regardless of records list order;
    sort by record_idx before hashing — matches Rust runtime behavior."""
    r0 = BatchRecord(
        custom_id="a",
        record_input_hash="sha512:" + hashlib.sha512(b"in_a").hexdigest(),
        record_output_hash="sha512:" + hashlib.sha512(b"out_a").hexdigest(),
        status="succeeded",
        record_idx=0,
    )
    r1 = BatchRecord(
        custom_id="b",
        record_input_hash="sha512:" + hashlib.sha512(b"in_b").hexdigest(),
        record_output_hash="sha512:" + hashlib.sha512(b"out_b").hexdigest(),
        status="succeeded",
        record_idx=1,
    )
    root_in_order = compute_batch_merkle_root([r0, r1])
    root_reversed = compute_batch_merkle_root([r1, r0])
    assert root_in_order == root_reversed


# ── Serialization validators ────────────────────────────────────────


def test_serialize_validates_custom_id_required():
    with pytest.raises(ValueError, match="missing required"):
        serialize_records_to_jsonl([{"input": "x"}], "custom_id")


def test_serialize_validates_unique_custom_id():
    with pytest.raises(ValueError, match="Duplicate"):
        serialize_records_to_jsonl(
            [{"custom_id": "a", "v": 1}, {"custom_id": "a", "v": 2}],
            "custom_id",
        )


def test_serialize_emits_one_line_per_record():
    out = serialize_records_to_jsonl(
        [{"custom_id": "a", "v": 1}, {"custom_id": "b", "v": 2}],
        "custom_id",
    )
    lines = out.decode().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["custom_id"] == "a"
    assert json.loads(lines[1])["custom_id"] == "b"


def test_serialize_supports_alternate_id_field():
    out = serialize_records_to_jsonl([{"matter_id": "m1"}, {"matter_id": "m2"}], "matter_id")
    lines = out.decode().strip().split("\n")
    assert json.loads(lines[0])["matter_id"] == "m1"


# ── Results parsing ─────────────────────────────────────────────────


def test_parse_results_jsonl_join_by_custom_id():
    records_in = serialize_records_to_jsonl(
        [{"custom_id": "p1", "v": 1}, {"custom_id": "p2", "v": 2}],
        "custom_id",
    )
    results = b"\n".join(
        [
            json.dumps({"custom_id": "p1", "status": "succeeded", "output": {"r": 10}}).encode(),
            json.dumps({"custom_id": "p2", "status": "errored", "error_code": "E1"}).encode(),
        ]
    )
    out = parse_results_jsonl(results, records_in)
    assert len(out) == 2
    by_id = {r.custom_id: r for r in out}
    assert by_id["p1"].status == "succeeded"
    assert by_id["p1"].output == {"r": 10}
    assert by_id["p2"].status == "errored"
    assert by_id["p2"].error_code == "E1"


def test_parse_missing_output_synthesizes_errored_record():
    records_in = serialize_records_to_jsonl(
        [{"custom_id": "p1"}, {"custom_id": "p2"}],
        "custom_id",
    )
    # Customer entrypoint only emitted result for p1.
    results = json.dumps({"custom_id": "p1", "status": "succeeded"}).encode()
    out = parse_results_jsonl(results, records_in)
    assert len(out) == 2
    by_id = {r.custom_id: r for r in out}
    assert by_id["p2"].status == "errored"
    assert by_id["p2"].error_code == "MISSING_OUTPUT"


def test_parse_invalid_status_normalizes_to_errored():
    records_in = serialize_records_to_jsonl([{"custom_id": "p1"}], "custom_id")
    results = json.dumps({"custom_id": "p1", "status": "weirdo"}).encode()
    out = parse_results_jsonl(results, records_in)
    assert out[0].status == "errored"


# ── End-to-end Capsule.batch() against mocked API ───────────────────


def _mock_capsule_create(mock_api):
    """Mock the POST /v1/capsules endpoint."""
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(
            200, json={"id": "cap_test1", "status": "running", "customer_id": "cust_1"}
        )
    )


def _mock_upload(mock_api):
    mock_api.post("/v1/capsules/cap_test1/upload").mock(
        return_value=httpx.Response(
            200,
            json={
                "filename": "records.jsonl",
                "size_bytes": 100,
                "sha256": "sha512:" + "a" * 128,
            },
        )
    )


def _mock_execute(mock_api):
    mock_api.post("/v1/capsules/cap_test1/exec").mock(
        return_value=httpx.Response(
            200,
            json={
                "exit_code": 0,
                "duration_seconds": 1,
                "stdout": "",
                "stderr": "",
            },
        )
    )


def _mock_destroy(mock_api):
    mock_api.delete("/v1/capsules/cap_test1").mock(
        return_value=httpx.Response(
            200,
            json={
                "cdp": {
                    "capsule_id": "cap_test1",
                    "cdp_version": "1.0",
                    "chain": [],
                    "attestation": {},
                },
                "verification_cdp": {},
            },
        )
    )


def test_capsule_batch_happy_path(mock_api, client):
    """Full Capsule.batch() lifecycle with 2 succeeded + 1 errored record."""
    _mock_capsule_create(mock_api)
    _mock_upload(mock_api)
    _mock_execute(mock_api)
    _mock_destroy(mock_api)

    results_jsonl = b"\n".join(
        [
            json.dumps({"custom_id": "p1", "status": "succeeded", "output": {"x": 1}}).encode(),
            json.dumps({"custom_id": "p2", "status": "succeeded", "output": {"x": 2}}).encode(),
            json.dumps({"custom_id": "p3", "status": "errored", "error_code": "E"}).encode(),
        ]
    )
    mock_api.get("/v1/capsules/cap_test1/output/results.jsonl").mock(
        return_value=httpx.Response(200, content=results_jsonl)
    )

    result = client.capsules.batch(
        capsulefile="cf_x",
        records=[
            {"custom_id": "p1", "v": 1},
            {"custom_id": "p2", "v": 2},
            {"custom_id": "p3", "v": 3},
        ],
    )

    assert result.success_count == 2
    assert result.error_count == 1
    assert result.total_count == 3
    assert result.merkle_root != ""
    assert len(result.merkle_root) == 128  # SHA-512 hex
    assert result.find("p1").ok
    assert not result.find("p3").ok
    assert result.find("p3").error_code == "E"
    failures = list(result.iter_failures())
    assert len(failures) == 1
    assert failures[0].custom_id == "p3"


def test_capsule_batch_rejects_empty_records(mock_api, client):
    with pytest.raises(BadRequestError, match="at least one record"):
        client.capsules.batch(capsulefile="cf_x", records=[])


def test_capsule_batch_rejects_invalid_on_record_error(mock_api, client):
    with pytest.raises(BadRequestError, match="on_record_error"):
        client.capsules.batch(
            capsulefile="cf_x",
            records=[{"custom_id": "p1"}],
            on_record_error="silently_continue",
        )


def test_capsule_batch_abort_batch_raises_on_first_error(mock_api, client):
    _mock_capsule_create(mock_api)
    _mock_upload(mock_api)
    _mock_execute(mock_api)
    _mock_destroy(mock_api)

    results_jsonl = json.dumps(
        {"custom_id": "p1", "status": "errored", "error_code": "E1", "error_message": "bad"}
    ).encode()
    mock_api.get("/v1/capsules/cap_test1/output/results.jsonl").mock(
        return_value=httpx.Response(200, content=results_jsonl)
    )

    with pytest.raises(ExecutionError, match="abort_batch"):
        client.capsules.batch(
            capsulefile="cf_x",
            records=[{"custom_id": "p1"}],
            on_record_error="abort_batch",
        )


def test_capsule_batch_invokes_on_record_callback(mock_api, client):
    _mock_capsule_create(mock_api)
    _mock_upload(mock_api)
    _mock_execute(mock_api)
    _mock_destroy(mock_api)

    results_jsonl = b"\n".join(
        [
            json.dumps({"custom_id": "p1", "status": "succeeded", "output": {}}).encode(),
            json.dumps({"custom_id": "p2", "status": "succeeded", "output": {}}).encode(),
        ]
    )
    mock_api.get("/v1/capsules/cap_test1/output/results.jsonl").mock(
        return_value=httpx.Response(200, content=results_jsonl)
    )

    seen = []
    client.capsules.batch(
        capsulefile="cf_x",
        records=[{"custom_id": "p1"}, {"custom_id": "p2"}],
        on_record=lambda r: seen.append(r.custom_id),
    )
    assert seen == ["p1", "p2"]
