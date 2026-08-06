"""Wave B Item 2 — Python SDK per-record receipt + parent-proof tests.

Covers `Capsule.record(...)` + `Capsule.add_parent_proof(...)` ergonomic
surfaces. Validates:

- RecordContext lifecycle (enter / bind / event / exit POST)
- pattern_tag closed-enum validation (15 valid variants accepted; invalid rejected)
- Field length limits (record_id, parent_*) enforced client-side
- SHA-512 of canonical-JCS bound output matches the expected hash
- HTTP mock for `/v1/capsules/:id/record` + `/v1/capsules/:id/parent`
- AuditProof response parsing with record_receipts + parent_proof_hashes populated
- ParentProofLink validation (sha512 / base64 prefixes carried as customer-supplied)
- Adapter-side resolver flow: receive AuditProof -> iterate receipts -> resolve mapping
- Forever-Standard round-trip: None receipts -> JSON -> parse -> still None
- RecordReceipt has NO `control_tags` field (three-layer separation)
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

import httpx
import pytest

from nanorix import (
    PATTERN_TAGS,
    CONTROL_MAP_DISCLAIMER,
    CONTROL_MAP_FRAMEWORK_VERSION,
    ParentProofLink,
    RecordContext,
    RecordReceipt,
    VerificationCdp,
    load_mapping,
    resolve_controls,
    validate_pattern_tag,
)
from nanorix._exceptions import BadRequestError
from nanorix._jcs import canonicalize
from nanorix._models import CdpDocument
from nanorix.capsule_record import (
    MAX_HASH_LEN,
    MAX_PARENT_JURISDICTION_LEN,
    MAX_PARENT_KEY_ID_LEN,
    MAX_PARENT_ORG_TAG_LEN,
    MAX_PARENT_ROLE_LEN,
    MAX_PARENT_SIG_LEN,
    MAX_RECORD_ID_LEN,
    AsyncRecordContext,
    build_parent_link_body,
    canonical_hash,
    sha512_hex,
    validate_parent_link_fields,
)


# ── Constants ──

CAPSULE_RESPONSE = {
    "id": "cap_test001",
    "status": "active",
    "capsulefile_id": "cf_abc",
    "max_lifetime_seconds": 300,
    "created_at": "2026-05-26T00:00:00Z",
}


def _record_response(
    record_index: int,
    record_id: str = "pa_123",
    capsule_id: str = "cap_test001",
) -> Dict[str, Any]:
    """Synthesize the Wave A `/v1/capsules/:id/record` response."""
    return {
        "capsule_id": capsule_id,
        "record_index": record_index,
        "record_id": record_id,
        "record_chain_hash": f"sha512:{'b' * 128}",
        "buffered_count": record_index + 1,
        "disclaimer": (
            "The receipt is signed evidence. Control IDs are derived at adapter "
            "time via the published mapping artifact; whether the "
            "evidence satisfies a control is determined by the customer's "
            "compliance program and the auditor's adjudication, not by Nanorix."
        ),
    }


def _parent_response(
    capsule_id: str = "cap_test001",
    parent_chain_hash: str = "sha512:" + "d" * 128,
) -> Dict[str, Any]:
    """Synthesize the Wave A `/v1/capsules/:id/parent` response."""
    return {
        "capsule_id": capsule_id,
        "parent_chain_hash": parent_chain_hash,
        "buffered_count": 1,
        "disclaimer": (
            "parent_proof_hashes attest to lineage, not to compliance. Each "
            "link attests to its own boundary; auditor adjudicates substantive "
            "satisfaction."
        ),
    }


# ── 1. PatternTag closed-enum validation ──


def test_pattern_tags_count_is_15():
    """PatternTag is a closed enum of 15 variants."""
    assert len(PATTERN_TAGS) == 15


def test_pattern_tags_canonical_set_matches_rust_wire_form():
    """The Python tuple must match the Rust `PatternTag::as_wire` outputs.

    Source of truth: the canonical verifier.
    """
    expected = {
        "pa",
        "extraction",
        "annotation",
        "agent_step",
        "agent_turn",
        "rcm_claim",
        "rcm_eligibility",
        "rcm_remit",
        "ncpdp_script",
        "dicom_study",
        "dicom_sr",
        "screening_hit",
        "fhir_record",
        "ehr_document",
        "custom",
    }
    assert set(PATTERN_TAGS) == expected


@pytest.mark.parametrize("tag", list(PATTERN_TAGS))
def test_validate_pattern_tag_accepts_every_canonical_variant(tag):
    assert validate_pattern_tag(tag) == tag


def test_validate_pattern_tag_accepts_none():
    assert validate_pattern_tag(None) is None


def test_validate_pattern_tag_rejects_invalid_variant():
    with pytest.raises(BadRequestError) as excinfo:
        validate_pattern_tag("invalid_pattern")
    assert "not a recognized variant" in str(excinfo.value)


def test_validate_pattern_tag_is_case_sensitive():
    """Per Rust serde rename_all='snake_case': case-sensitive."""
    with pytest.raises(BadRequestError):
        validate_pattern_tag("PA")  # uppercase rejected
    with pytest.raises(BadRequestError):
        validate_pattern_tag("Pa")  # mixed-case rejected


def test_validate_pattern_tag_rejects_kebab_case():
    """Spec prose used kebab-case (`rcm-claim`); wire form is snake_case."""
    with pytest.raises(BadRequestError):
        validate_pattern_tag("rcm-claim")
    with pytest.raises(BadRequestError):
        validate_pattern_tag("dicom-study")


# ── 2. Field length limits ──


def test_record_id_length_limit():
    """Record_id length cap: 256 chars, matching the server."""
    with pytest.raises(BadRequestError) as excinfo:
        RecordContext(
            capsule_id="cap_test001",
            record_id="x" * (MAX_RECORD_ID_LEN + 1),
            pattern_tag=None,
            post_callback=lambda _cap, _body: {},
        )
    assert "256" in str(excinfo.value)


def test_record_id_empty_rejected():
    with pytest.raises(BadRequestError):
        RecordContext(
            capsule_id="cap_test001",
            record_id="",
            pattern_tag=None,
            post_callback=lambda _cap, _body: {},
        )


def test_parent_chain_hash_length_limit():
    with pytest.raises(BadRequestError) as excinfo:
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * (MAX_HASH_LEN),  # too long
            parent_key_id="cust-auth-banjo",
            parent_signature="base64:" + "b" * 88,
        )
    assert "144" in str(excinfo.value)


def test_parent_key_id_length_limit():
    with pytest.raises(BadRequestError):
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * 128,
            parent_key_id="k" * (MAX_PARENT_KEY_ID_LEN + 1),
            parent_signature="base64:" + "b" * 88,
        )


def test_parent_signature_length_limit():
    with pytest.raises(BadRequestError):
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * 128,
            parent_key_id="cust-auth-banjo",
            parent_signature="base64:" + "b" * (MAX_PARENT_SIG_LEN),
        )


def test_parent_role_length_limit():
    with pytest.raises(BadRequestError):
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * 128,
            parent_key_id="cust-auth-banjo",
            parent_signature="base64:" + "b" * 88,
            parent_role="r" * (MAX_PARENT_ROLE_LEN + 1),
        )


def test_parent_jurisdiction_length_limit():
    with pytest.raises(BadRequestError):
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * 128,
            parent_key_id="cust-auth-banjo",
            parent_signature="base64:" + "b" * 88,
            parent_jurisdiction="j" * (MAX_PARENT_JURISDICTION_LEN + 1),
        )


def test_parent_org_tag_length_limit():
    with pytest.raises(BadRequestError):
        validate_parent_link_fields(
            parent_chain_hash="sha512:" + "a" * 128,
            parent_key_id="cust-auth-banjo",
            parent_signature="base64:" + "b" * 88,
            parent_organization_tag="o" * (MAX_PARENT_ORG_TAG_LEN + 1),
        )


# ── 3. Hash helpers (cross-impl byte-equivalence) ──


def test_sha512_hex_prefix_form():
    """`sha512:{128 hex}` matches the Rust API wire form.

    Length math: `"sha512:"` (7 chars) + 128 hex chars = 135 chars. The Rust
    `MAX_HASH_LEN` cap is 144 (headroom for prefix variants / future-proofing
    per the Nanorix API service).
    """
    out = sha512_hex(b"hello")
    assert out.startswith("sha512:")
    assert len(out) == 7 + 128  # "sha512:" prefix (7) + 128 hex chars
    assert len(out) <= MAX_HASH_LEN  # within the Rust API cap
    expected = "sha512:" + hashlib.sha512(b"hello").hexdigest()
    assert out == expected


def test_canonical_hash_dict_matches_jcs():
    """Canonical_hash dispatches through JCS — RFC 8785 canonical form."""
    data = {"b": 2, "a": 1}  # keys unsorted intentionally
    out = canonical_hash(data)
    # JCS sorts keys lexically
    canonical_bytes = canonicalize(data)
    assert canonical_bytes == b'{"a":1,"b":2}'
    expected = "sha512:" + hashlib.sha512(canonical_bytes).hexdigest()
    assert out == expected


def test_canonical_hash_deterministic_across_input_order():
    """JCS canonicalization makes the hash key-order independent."""
    out1 = canonical_hash({"x": 1, "y": 2, "z": 3})
    out2 = canonical_hash({"z": 3, "y": 2, "x": 1})
    assert out1 == out2


# ── 4. RecordContext lifecycle ──


def test_record_context_enter_returns_self():
    """Context manager protocol: __enter__ -> self."""
    ctx = RecordContext(
        capsule_id="cap_test001",
        record_id="pa_001",
        pattern_tag="pa",
        post_callback=lambda _cap, _body: _record_response(0),
    )
    with ctx as r:
        assert r is ctx


def test_record_context_exit_posts_request():
    """Context exit POSTs the buffered receipt to the server."""
    posts: List[Dict[str, Any]] = []

    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append({"capsule_id": capsule_id, "body": body})
        return _record_response(0)

    with RecordContext(
        capsule_id="cap_test001",
        record_id="pa_001",
        pattern_tag="pa",
        post_callback=fake_post,
    ) as r:
        r.bind_output({"decision": "approved"})

    assert len(posts) == 1
    assert posts[0]["capsule_id"] == "cap_test001"
    assert posts[0]["body"]["record_id"] == "pa_001"
    assert posts[0]["body"]["pattern_tag"] == "pa"
    assert posts[0]["body"]["record_output_hash"].startswith("sha512:")


def test_record_context_response_fields_exposed():
    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return _record_response(7, record_id=body["record_id"])

    ctx = RecordContext(
        capsule_id="cap_test001",
        record_id="claim_xyz",
        pattern_tag="rcm_claim",
        post_callback=fake_post,
    )
    with ctx as r:
        r.bind_input({"input": "x"})
        r.bind_output({"output": "y"})

    assert ctx.record_chain_hash is not None
    assert ctx.record_chain_hash.startswith("sha512:")
    assert ctx.record_index == 7


def test_record_context_no_emit_on_exception():
    """If the customer's `with` body raises, no receipt is POSTed."""
    posts: List[Dict[str, Any]] = []

    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append(body)
        return _record_response(0)

    with pytest.raises(ValueError):
        with RecordContext(
            capsule_id="cap_test001",
            record_id="pa_001",
            pattern_tag="pa",
            post_callback=fake_post,
        ) as r:
            r.bind_output({"x": 1})
            raise ValueError("customer code blew up")

    assert posts == []


def test_record_context_event_buffer():
    """`r.event(...)` appends to the activity_trail; emitted on exit."""
    posts: List[Dict[str, Any]] = []

    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append(body)
        return _record_response(0)

    with RecordContext(
        capsule_id="cap_test001",
        record_id="pa_001",
        pattern_tag="pa",
        post_callback=fake_post,
    ) as r:
        r.event({"type": "ReasoningStepCompleted", "step_id": "ddx"})
        r.event({"type": "ReasoningStepCompleted", "step_id": "rx"})
        r.bind_output({"approved": True})

    body = posts[0]
    assert "activity_trail" in body
    assert len(body["activity_trail"]) == 2
    assert body["activity_trail"][0]["step_id"] == "ddx"
    assert body["activity_trail"][1]["step_id"] == "rx"


def test_record_context_omits_unset_pattern_tag():
    posts: List[Dict[str, Any]] = []

    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append(body)
        return _record_response(0)

    with RecordContext(
        capsule_id="cap_test001",
        record_id="pa_001",
        pattern_tag=None,
        post_callback=fake_post,
    ) as r:
        r.bind_output(b"raw bytes")

    assert "pattern_tag" not in posts[0]


def test_record_context_bind_input_bytes():
    posts: List[Dict[str, Any]] = []

    def fake_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append(body)
        return _record_response(0)

    with RecordContext(
        capsule_id="cap_test001",
        record_id="pa_001",
        pattern_tag="pa",
        post_callback=fake_post,
    ) as r:
        r.bind_input(b"raw input bytes")
        r.bind_output(b"raw output bytes")

    assert posts[0]["record_input_hash"] == sha512_hex(b"raw input bytes")
    assert posts[0]["record_output_hash"] == sha512_hex(b"raw output bytes")


# ── 5. End-to-end against mocked HTTP transport ──


def _mock_capsule_create(mock_api):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )


def test_record_endpoint_called_via_client(mock_api, client):
    """End-to-end: client.capsules.create() -> capsule.record() -> POST /record."""
    _mock_capsule_create(mock_api)
    route = mock_api.post("/v1/capsules/cap_test001/record").mock(
        return_value=httpx.Response(201, json=_record_response(0))
    )

    capsule = client.capsules.create()
    with capsule.record(record_id="pa_123", pattern_tag="pa") as r:
        r.bind_output({"decision": "approved"})

    assert len(route.calls) == 1
    body = json.loads(route.calls[0].request.content)
    assert body["record_id"] == "pa_123"
    assert body["pattern_tag"] == "pa"
    assert body["record_output_hash"].startswith("sha512:")


def test_parent_endpoint_called_via_client(mock_api, client):
    _mock_capsule_create(mock_api)
    parent_hash = "sha512:" + "d" * 128
    route = mock_api.post("/v1/capsules/cap_test001/parent").mock(
        return_value=httpx.Response(201, json=_parent_response(parent_chain_hash=parent_hash))
    )

    capsule = client.capsules.create()
    response = capsule.add_parent_proof(
        parent_chain_hash=parent_hash,
        parent_key_id="cust-auth-banjo-prod",
        parent_signature="base64:" + "b" * 88,
        parent_role="input-data-source",
        parent_organization_tag="vendor:banjo-health",
        parent_jurisdiction="US",
    )

    assert len(route.calls) == 1
    body = json.loads(route.calls[0].request.content)
    assert body["parent_chain_hash"] == parent_hash
    assert body["parent_key_id"] == "cust-auth-banjo-prod"
    assert body["parent_role"] == "input-data-source"
    assert body["parent_organization_tag"] == "vendor:banjo-health"
    assert response["buffered_count"] == 1


def test_parent_endpoint_optional_fields_omitted_when_none(mock_api, client):
    _mock_capsule_create(mock_api)
    route = mock_api.post("/v1/capsules/cap_test001/parent").mock(
        return_value=httpx.Response(201, json=_parent_response())
    )

    capsule = client.capsules.create()
    capsule.add_parent_proof(
        parent_chain_hash="sha512:" + "a" * 128,
        parent_key_id="cust-auth-x",
        parent_signature="base64:" + "b" * 88,
    )

    body = json.loads(route.calls[0].request.content)
    # Optional fields are omitted when None per Forever-Standard.
    assert "parent_role" not in body
    assert "parent_jurisdiction" not in body
    assert "parent_organization_tag" not in body


def test_many_record_emits_in_sequence(mock_api, client):
    """Pattern 4 high-volume case: emit 10 receipts in one capsule lifetime."""
    _mock_capsule_create(mock_api)

    calls = {"n": 0}

    def respond(request):
        calls["n"] += 1
        return httpx.Response(201, json=_record_response(calls["n"] - 1))

    mock_api.post("/v1/capsules/cap_test001/record").mock(side_effect=respond)

    capsule = client.capsules.create()
    for i in range(10):
        with capsule.record(record_id=f"claim_{i}", pattern_tag="rcm_claim") as r:
            r.bind_output({"claim_id": i, "amount": 100 + i})

    assert calls["n"] == 10


# ── 6. AuditProof response parsing ──


def test_audit_proof_parses_record_receipts():
    """Customer pulls FullCdp + iterates record_receipts."""
    raw = {
        "capsule_id": "cap_test001",
        "activity": [],
        "record_receipts": [
            {
                "record_index": 0,
                "record_id": "pa_001",
                "record_input_hash": "sha512:" + "1" * 128,
                "record_output_hash": "sha512:" + "2" * 128,
                "record_chain_hash": "sha512:" + "3" * 128,
                "pattern_tag": "pa",
                "merkle_inclusion_proof": [],
            },
            {
                "record_index": 1,
                "record_id": "pa_002",
                "record_input_hash": "sha512:" + "4" * 128,
                "record_output_hash": "sha512:" + "5" * 128,
                "record_chain_hash": "sha512:" + "6" * 128,
                "pattern_tag": "pa",
                "merkle_inclusion_proof": ["7" * 128],
            },
        ],
        "record_receipts_merkle_root": "sha512:" + "8" * 128,
    }
    doc = CdpDocument.model_validate(raw)
    assert doc.record_receipts is not None
    assert len(doc.record_receipts) == 2
    assert doc.record_receipts[0].record_id == "pa_001"
    assert doc.record_receipts[0].pattern_tag == "pa"
    assert doc.record_receipts[1].merkle_inclusion_proof == ["7" * 128]
    assert doc.record_receipts_merkle_root == "sha512:" + "8" * 128


def test_audit_proof_parses_parent_proof_hashes():
    raw = {
        "capsule_id": "cap_test001",
        "activity": [],
        "parent_proof_hashes": [
            {
                "parent_chain_hash": "sha512:" + "d" * 128,
                "parent_key_id": "cust-auth-banjo-prod",
                "parent_signature": "base64:" + "b" * 88,
                "parent_role": "input-data-source",
                "parent_organization_tag": "vendor:banjo-health",
                "parent_jurisdiction": "US",
            }
        ],
        "parent_proofs_merkle_root": "sha512:" + "e" * 128,
    }
    doc = CdpDocument.model_validate(raw)
    assert doc.parent_proof_hashes is not None
    assert len(doc.parent_proof_hashes) == 1
    p = doc.parent_proof_hashes[0]
    assert p.parent_key_id == "cust-auth-banjo-prod"
    assert p.parent_role == "input-data-source"
    assert doc.parent_proofs_merkle_root == "sha512:" + "e" * 128


def test_verification_cdp_parses_record_receipts():
    """Same wave-N fields on the public-shareable AuditProof surface."""
    raw = {
        "capsule_id": "cap_test001",
        "record_receipts": [
            {
                "record_index": 0,
                "record_id": "claim_001",
                "record_input_hash": "sha512:" + "1" * 128,
                "record_output_hash": "sha512:" + "2" * 128,
                "record_chain_hash": "sha512:" + "3" * 128,
                "pattern_tag": "rcm_claim",
                "merkle_inclusion_proof": [],
            }
        ],
        "record_receipts_merkle_root": "sha512:" + "8" * 128,
    }
    proof = VerificationCdp.model_validate(raw)
    assert proof.record_receipts is not None
    assert proof.record_receipts[0].pattern_tag == "rcm_claim"


# ── 7. Forever-Standard discipline ──


def test_forever_standard_pre_wave_n_none_round_trip():
    """Pre-Wave-N AuditProof (no receipts / no parents) -> JSON -> parse -> still None."""
    raw = {
        "capsule_id": "cap_pre_wave_n",
        "activity": [],
        "destruction_trigger": "user_explicit",
    }
    doc = CdpDocument.model_validate(raw)
    assert doc.record_receipts is None
    assert doc.record_receipts_merkle_root is None
    assert doc.parent_proof_hashes is None
    assert doc.parent_proofs_merkle_root is None

    serialized = doc.model_dump(exclude_none=True)
    assert "record_receipts" not in serialized
    assert "record_receipts_merkle_root" not in serialized
    assert "parent_proof_hashes" not in serialized
    assert "parent_proofs_merkle_root" not in serialized


def test_record_receipt_has_no_control_tags_field():
    """Three-layer separation discipline: control_tags MUST NOT be a field.

    RE-SCOPED (founder direction 2026-05-12): control IDs are
    NEVER stamped into the signed receipt. Adapters apply the published
    mapping artifact at ingestion time.

    This test catches future regressions that would re-introduce
    control_tags as a field (architectural drift).
    """
    declared_fields = set(RecordReceipt.model_fields.keys())
    assert "control_tags" not in declared_fields, (
        f"RecordReceipt MUST NOT have a control_tags field "
        f"RE-SCOPED + three-layer separation. Found fields: "
        f"{sorted(declared_fields)}"
    )


def test_parent_proof_link_field_set_matches_adr_041():
    """ParentProofLink's declared field set MUST match."""
    declared_fields = set(ParentProofLink.model_fields.keys())
    expected = {
        "parent_chain_hash",
        "parent_key_id",
        "parent_signature",
        "parent_role",
        "parent_jurisdiction",
        "parent_organization_tag",
    }
    assert declared_fields == expected


# ── 8. Adapter-side resolver flow (three-layer demo) ──


def test_adapter_flow_receipt_to_control_ids():
    """End-to-end three-layer flow:

    1. Adapter receives AuditProof with signed record_receipts.
    2. Adapter reads pattern_tag from each receipt (signed primitive).
    3. Adapter applies mapping at ingestion time.
    4. Mapping returns canonical control IDs for `(jurisdiction × classification × pattern_tag)`.
    """
    # 1. Simulated AuditProof from Nanorix API.
    raw_proof = {
        "capsule_id": "cap_anterior_pa",
        "record_receipts": [
            {
                "record_index": 0,
                "record_id": "pa_a",
                "record_input_hash": "sha512:" + "1" * 128,
                "record_output_hash": "sha512:" + "2" * 128,
                "record_chain_hash": "sha512:" + "3" * 128,
                "pattern_tag": "pa",
                "merkle_inclusion_proof": [],
            },
            {
                "record_index": 1,
                "record_id": "pa_b",
                "record_input_hash": "sha512:" + "4" * 128,
                "record_output_hash": "sha512:" + "5" * 128,
                "record_chain_hash": "sha512:" + "6" * 128,
                "pattern_tag": "pa",
                "merkle_inclusion_proof": ["x" * 128],
            },
        ],
    }
    proof = VerificationCdp.model_validate(raw_proof)

    # 2 + 3 + 4. Adapter applies the mapping artifact.
    mapping = load_mapping(framework_version=CONTROL_MAP_FRAMEWORK_VERSION)
    customer_jurisdiction = "US"
    customer_classification = "PHI"

    for receipt in proof.record_receipts or []:
        control_ids = mapping.resolve(
            jurisdiction=customer_jurisdiction,
            classification=customer_classification,
            pattern_tag=receipt.pattern_tag or "custom",
        )
        # PA receipts under (US, PHI) should reference HIPAA + URAC.
        assert "HIPAA-164.312(b)" in control_ids
        assert "URAC-UM-AC-1" in control_ids


def test_adapter_flow_convenience_resolve_function():
    """Same adapter flow but using the top-level `resolve_controls` helper."""
    control_ids = resolve_controls(
        jurisdiction="US",
        classification="PHI",
        pattern_tag="rcm_claim",
    )
    assert "HIPAA-164.312(b)" in control_ids
    assert "HITRUST-02.e" in control_ids
    assert "ACA-1557" in control_ids


# ── 9. Forbidden language discipline (CCO policy) ──


def test_capsule_record_module_no_forbidden_language():
    """Scan the source of capsule_record.py for forbidden verdict language.

    Per CCO policy:
    SDK code must NEVER use COMPLIANT / SATISFIED / PASSED / MEETS in
    customer-facing surfaces.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "nanorix" / "capsule_record.py"
    text = src.read_text()
    for forbidden in ("COMPLIANT", "SATISFIED", "MEETS"):
        assert forbidden not in text, (
            f"capsule_record.py must not use verdict language '{forbidden}' "
            f"per CCO policy; receipts attest to evidence + auditor adjudicates"
        )
    # Lowercase variants — "passed" appears in "passed in" / "passed to" prose
    # legitimately for function arguments. Check only verdict contexts.
    for forbidden in ("satisfied", "is compliant", "meets the"):
        assert forbidden.lower() not in text.lower(), (
            f"capsule_record.py must not contain verdict phrase '{forbidden}'"
        )


def test_capsule_record_disclaimer_mirrors_adr_017_three_role():
    """The receipt disclaimer mentions auditor adjudication."""
    # The control_map module's disclaimer is the canonical disclaimer surface;
    # SDK's capsule_record module mirrors three-layer separation in its docstring.
    from nanorix import capsule_record

    doc = capsule_record.__doc__ or ""
    assert "Three-layer separation" in doc
    assert "auditor" in doc
    assert "adjudicat" in doc


# ── 10. CONTROL_MAP_DISCLAIMER vocabulary check ──


def test_control_map_disclaimer_uses_required_vocabulary():
    """The mapping disclaimer must use 'references' relationship vocabulary."""
    assert "references" in CONTROL_MAP_DISCLAIMER
    assert "auditor" in CONTROL_MAP_DISCLAIMER
    # And NEVER verdict language.
    for forbidden in ("COMPLIANT", "SATISFIED", "PASSED", "MEETS"):
        assert forbidden not in CONTROL_MAP_DISCLAIMER


# ── 11. Async parity ──


@pytest.mark.asyncio
async def test_async_record_context_lifecycle():
    """AsyncRecordContext: __aenter__ / __aexit__ posts the buffered receipt."""
    posts: List[Dict[str, Any]] = []

    async def fake_async_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append({"capsule_id": capsule_id, "body": body})
        return _record_response(0)

    ctx = AsyncRecordContext(
        capsule_id="cap_test001",
        record_id="pa_a",
        pattern_tag="pa",
        post_callback=fake_async_post,
    )
    async with ctx as r:
        r.bind_output({"decision": "approved"})

    assert len(posts) == 1
    assert posts[0]["body"]["record_id"] == "pa_a"


@pytest.mark.asyncio
async def test_async_record_context_no_emit_on_exception():
    posts: List[Dict[str, Any]] = []

    async def fake_async_post(capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        posts.append(body)
        return _record_response(0)

    with pytest.raises(RuntimeError):
        async with AsyncRecordContext(
            capsule_id="cap_test001",
            record_id="pa_b",
            pattern_tag="pa",
            post_callback=fake_async_post,
        ) as r:
            r.bind_output({"x": 1})
            raise RuntimeError("boom")

    assert posts == []


# ── 12. Parent link body builder ──


def test_build_parent_link_body_omits_none_fields():
    body = build_parent_link_body(
        parent_chain_hash="sha512:" + "a" * 128,
        parent_key_id="cust-auth-x",
        parent_signature="base64:" + "b" * 88,
    )
    assert body == {
        "parent_chain_hash": "sha512:" + "a" * 128,
        "parent_key_id": "cust-auth-x",
        "parent_signature": "base64:" + "b" * 88,
    }


def test_build_parent_link_body_includes_optional_fields():
    body = build_parent_link_body(
        parent_chain_hash="sha512:" + "a" * 128,
        parent_key_id="cust-auth-x",
        parent_signature="base64:" + "b" * 88,
        parent_role="rag-retrieval",
        parent_jurisdiction="EU",
        parent_organization_tag="vendor:llamaindex-cloud",
    )
    assert body["parent_role"] == "rag-retrieval"
    assert body["parent_jurisdiction"] == "EU"
    assert body["parent_organization_tag"] == "vendor:llamaindex-cloud"
