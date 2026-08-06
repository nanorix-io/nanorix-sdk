"""
Order 3 / Gap 2 tests — Capsule.session() LangGraph-shape memory artifact.

Covers state-bytes hashing + metadata binding (session_id +
parent_cdp_id + artifact_input_hash) + end-to-end Capsule.session()
flow against mocked /v1/capsules + /v1/capsules/{id}/upload + /exec
+ /output + destroy.
"""

from __future__ import annotations

import hashlib

import httpx

from nanorix._exceptions import BadRequestError, ExecutionError
from nanorix._session import (
    SessionResult,
    build_session_metadata,
    hash_state_bytes,
)


# ── State hashing ────────────────────────────────────────────────────


def test_hash_state_bytes_returns_wire_format():
    """Wire format: `sha512:` prefix + 128-char lowercase hex."""
    out = hash_state_bytes(b"hello")
    assert out.startswith("sha512:")
    assert len(out) == len("sha512:") + 128
    expected = "sha512:" + hashlib.sha512(b"hello").hexdigest()
    assert out == expected


def test_hash_state_bytes_handles_empty():
    """Empty state bytes (first session in chain) yields canonical SHA-512('')."""
    out = hash_state_bytes(b"")
    expected = (
        "sha512:cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
    )
    assert out == expected


# ── Metadata binding ────────────────────────────────────────────────


def test_build_session_metadata_includes_reserved_keys():
    md = build_session_metadata(
        session_id="agent-7f3a",
        parent_cdp_id="ap_xK9aB",
        artifact_input_hash="sha512:abc",
    )
    assert md["nanorix:session_id"] == "agent-7f3a"
    assert md["nanorix:parent_cdp_id"] == "ap_xK9aB"
    assert md["nanorix:artifact_input_hash"] == "sha512:abc"


def test_build_session_metadata_omits_parent_for_first_session():
    md = build_session_metadata(
        session_id="agent-7f3a",
        parent_cdp_id=None,
        artifact_input_hash="sha512:empty",
    )
    assert "nanorix:parent_cdp_id" not in md
    assert md["nanorix:session_id"] == "agent-7f3a"


def test_build_session_metadata_merges_customer_metadata():
    md = build_session_metadata(
        session_id="agent-7f3a",
        parent_cdp_id=None,
        artifact_input_hash="sha512:empty",
        extra={"agent:run_id": "r-001", "hipaa:bundle_id": "B-1"},
    )
    assert md["agent:run_id"] == "r-001"
    assert md["hipaa:bundle_id"] == "B-1"
    assert md["nanorix:session_id"] == "agent-7f3a"


# ── End-to-end Capsule.session() against mocked API ────────────────


def _mock_session_lifecycle(mock_api):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(
            200, json={"id": "cap_session1", "status": "running", "customer_id": "c1"}
        )
    )
    mock_api.post("/v1/capsules/cap_session1/upload").mock(
        return_value=httpx.Response(
            200,
            json={
                "filename": "state.bin",
                "size_bytes": 5,
                "sha256": "sha512:" + "a" * 128,
            },
        )
    )
    mock_api.post("/v1/capsules/cap_session1/exec").mock(
        return_value=httpx.Response(
            200, json={"exit_code": 0, "duration_seconds": 1, "stdout": "", "stderr": ""}
        )
    )
    mock_api.delete("/v1/capsules/cap_session1").mock(
        return_value=httpx.Response(
            200,
            json={
                "cdp": {
                    "capsule_id": "cap_session1",
                    "cdp_version": "1.0",
                    "chain": [],
                    "attestation": {},
                },
                "verification_cdp": {},
            },
        )
    )


def test_capsule_session_first_session_no_parent(mock_api, client):
    """First session in chain — parent_cdp_id=None, initial_state=None.

    Customer entrypoint reads /data/input/state.bin (empty) + writes
    /data/output/state.bin (new state). SDK downloads + returns
    SessionResult with both hashes.
    """
    _mock_session_lifecycle(mock_api)

    new_state = b"first session output state"
    mock_api.get("/v1/capsules/cap_session1/output/state.bin").mock(
        return_value=httpx.Response(200, content=new_state)
    )

    result = client.capsules.session(
        capsulefile="cf_research_agent",
        session_id="agent-7f3a",
        parent_cdp_id=None,
        initial_state=None,
    )

    assert isinstance(result, SessionResult)
    assert result.session_id == "agent-7f3a"
    assert result.state_out == new_state
    # First session: input hash = SHA-512(empty)
    assert result.artifact_input_hash == hash_state_bytes(b"")
    assert result.artifact_output_hash == hash_state_bytes(new_state)


def test_capsule_session_chained_session_with_parent(mock_api, client):
    """Chained session — parent_cdp_id + initial_state both set.

    Verifier checks current.artifact_input_hash equals
    parent.artifact_output_hash; this test just pins the hash
    computation chain works deterministically.
    """
    _mock_session_lifecycle(mock_api)

    initial = b"prior session output state"
    next_state = b"this session new state"
    mock_api.get("/v1/capsules/cap_session1/output/state.bin").mock(
        return_value=httpx.Response(200, content=next_state)
    )

    result = client.capsules.session(
        capsulefile="cf_research_agent",
        session_id="agent-7f3a",
        parent_cdp_id="ap_xK9aB",
        initial_state=initial,
    )

    assert result.artifact_input_hash == hash_state_bytes(initial)
    assert result.artifact_output_hash == hash_state_bytes(next_state)
    assert result.state_out == next_state


def test_capsule_session_rejects_empty_session_id(mock_api, client):
    """session_id is required — matches LangGraph thread_id convention."""
    import pytest

    with pytest.raises(BadRequestError, match="session_id"):
        client.capsules.session(
            capsulefile="cf_x",
            session_id="",
        )


def test_capsule_session_raises_when_state_bin_not_produced(mock_api, client):
    """Customer entrypoint MUST produce /data/output/state.bin."""
    import pytest

    _mock_session_lifecycle(mock_api)
    mock_api.get("/v1/capsules/cap_session1/output/state.bin").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )

    with pytest.raises(ExecutionError, match="state.bin"):
        client.capsules.session(
            capsulefile="cf_x",
            session_id="agent-1",
        )


def test_capsule_session_metadata_binding_includes_session_id(mock_api, client):
    """The capsule create call MUST include nanorix:session_id metadata."""
    import json

    _mock_session_lifecycle(mock_api)
    mock_api.get("/v1/capsules/cap_session1/output/state.bin").mock(
        return_value=httpx.Response(200, content=b"out")
    )

    client.capsules.session(
        capsulefile="cf_x",
        session_id="agent-7f3a",
        parent_cdp_id="ap_xyz",
        initial_state=b"in",
        metadata={"agent:run_id": "r-001"},
    )

    # Inspect the POST /v1/capsules call body.
    create_calls = [
        req
        for req in mock_api.calls
        if req.request.method == "POST" and req.request.url.path == "/v1/capsules"
    ]
    assert len(create_calls) == 1
    body = json.loads(create_calls[0].request.content)
    assert "metadata" in body
    md = body["metadata"]
    assert md["nanorix:session_id"] == "agent-7f3a"
    assert md["nanorix:parent_cdp_id"] == "ap_xyz"
    assert md["nanorix:artifact_input_hash"] == hash_state_bytes(b"in")
    assert md["agent:run_id"] == "r-001"
