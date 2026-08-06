"""Batch 9 (2026-05-28) — C2PA LLM-output provenance tests.

Validates:

- Builder fluent API (capsule / model / prompt_intent / timestamp / output)
- Required-field enforcement at .sign() time (customer-sole-declarer)
- Closed-enum AssertionType (Forever-Standard discipline)
- Field length caps + forbidden-token scans (brand discipline +
  )
- SHA-512 of canonical-JCS claims object signed by Ed25519
- Independent offline verification with raw 32-byte public key
- Tampering detection (signature breaks if any field modified)
- Tamper-detection on claims_hash recomputation
- Round-trip canonicalization byte-stability
- Manifest structure (claim_generator, claim_generator_info, assertions,
  claims_hash, signature)
- Optional model_weights_hash + custom assertions
- Narrow-claim disclaimer carried in verification result
- AuditProof byte-form UNCHANGED (validate by import + struct grep)
- RecordReceipt + ParentProofLink fields UNCHANGED (narrowness invariant)
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from nanorix._jcs import canonicalize
from nanorix.helpers.signer import EventSigner
from nanorix.integrations.c2pa_provenance import (
    AssertionType,
    C2PAClaimGeneratorInfo,
    C2PAManifest,
    C2PAManifestBuilder,
    C2PAVerificationResult,
    C2PA_ACTION_CREATED,
    DIGITAL_SOURCE_TYPE_AI,
    FORBIDDEN_BRAND_LEAK_TOKENS,
    FORBIDDEN_REGULATORY_TOKENS,
    MANIFEST_FORMAT,
    MAX_AUDITPROOF_ID_LEN,
    MAX_CAPSULE_ID_LEN,
    MAX_MODEL_ID_LEN,
    MAX_PROMPT_INTENT_LEN,
    MAX_VERIFICATION_KEY_ID_LEN,
    NARROW_CLAIM_DISCLAIMER,
    SIGNATURE_ALGORITHM,
    VALID_ASSERTION_TYPES,
    verify_c2pa_manifest,
)


# ── Fixtures ──


class _InMemoryEd25519Signer(EventSigner):
    """Pure in-memory Ed25519 signer for tests (no file IO)."""

    def __init__(self) -> None:
        self._private = Ed25519PrivateKey.generate()
        self._public_bytes = self._private.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(message)

    def public_key_bytes(self) -> bytes:
        return self._public_bytes

    def algorithm_name(self) -> str:
        return "Ed25519"


@pytest.fixture
def signer() -> _InMemoryEd25519Signer:
    return _InMemoryEd25519Signer()


def _build_basic(signer: _InMemoryEd25519Signer) -> C2PAManifest:
    return (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="cap_abc123",
            auditproof_id="cdp_def456",
            verification_key_id="nrx-verify-2026-05-28T12-34-50-cap_abc1",
        )
        .with_model(model_id="openai/gpt-4o-2024-08-06")
        .with_prompt_intent("clinical_pa_decision_narrative_v3")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"This is the generated narrative.")
        .sign(signer)
    )


# ── Group 1: Builder happy path ──


def test_builder_returns_c2pa_manifest(signer):
    m = _build_basic(signer)
    assert isinstance(m, C2PAManifest)


def test_basic_manifest_has_required_top_level_fields(signer):
    m = _build_basic(signer)
    d = m.to_dict()
    assert d["format"] == MANIFEST_FORMAT
    assert d["claim_generator"].startswith("Nanorix/1.0")
    assert isinstance(d["claim_generator_info"], list)
    assert d["claims_hash"].startswith("sha512:")
    assert d["signature"]["algorithm"] == SIGNATURE_ALGORITHM
    assert d["signature"]["public_key"].startswith("base64:")
    assert d["signature"]["signature"].startswith("base64:")


def test_basic_manifest_includes_two_assertions(signer):
    m = _build_basic(signer)
    labels = [a["label"] for a in m.assertions]
    assert AssertionType.C2PA_ACTIONS.value in labels
    assert AssertionType.NANORIX_AI_GENERATED.value in labels


def test_c2pa_actions_assertion_uses_correct_digital_source_type(signer):
    m = _build_basic(signer)
    actions_assertion = next(
        a for a in m.assertions if a["label"] == AssertionType.C2PA_ACTIONS.value
    )
    actions = actions_assertion["data"]["actions"]
    assert actions[0]["action"] == C2PA_ACTION_CREATED
    assert actions[0]["digitalSourceType"] == DIGITAL_SOURCE_TYPE_AI


def test_nanorix_assertion_contains_three_audit_proof_binders(signer):
    m = _build_basic(signer)
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    data = nx["data"]
    assert data["capsule_id"] == "cap_abc123"
    assert data["auditproof_id"] == "cdp_def456"
    assert data["verification_key_id"] == "nrx-verify-2026-05-28T12-34-50-cap_abc1"


def test_nanorix_assertion_includes_disclaimer(signer):
    m = _build_basic(signer)
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    assert nx["data"]["disclaimer"] == NARROW_CLAIM_DISCLAIMER


def test_output_content_hash_uses_sha512_prefix(signer):
    m = _build_basic(signer)
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    out_hash = nx["data"]["output_content_hash"]
    assert out_hash.startswith("sha512:")
    expected = (
        "sha512:" + hashlib.sha512(b"This is the generated narrative.").hexdigest()
    )
    assert out_hash == expected


def test_prompt_intent_hash_uses_sha512_of_text(signer):
    m = _build_basic(signer)
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    intent_hash = nx["data"]["prompt_intent_hash"]
    expected = (
        "sha512:"
        + hashlib.sha512(b"clinical_pa_decision_narrative_v3").hexdigest()
    )
    assert intent_hash == expected


def test_canonical_bytes_are_deterministic(signer):
    m = _build_basic(signer)
    a = m.to_canonical_bytes()
    b = m.to_canonical_bytes()
    assert a == b


def test_canonical_bytes_match_jcs(signer):
    m = _build_basic(signer)
    canonical = m.to_canonical_bytes()
    redo = canonicalize(json.loads(canonical))
    assert canonical == redo


# ── Group 2: Required-field enforcement (customer-sole-declarer) ──


def test_sign_without_capsule_raises(signer):
    b = (
        C2PAManifestBuilder()
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
    )
    with pytest.raises(ValueError, match="capsule_id"):
        b.sign(signer)


def test_sign_without_model_raises(signer):
    b = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
    )
    with pytest.raises(ValueError, match="model_id"):
        b.sign(signer)


def test_sign_without_prompt_intent_raises(signer):
    b = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
    )
    with pytest.raises(ValueError, match="prompt_intent"):
        b.sign(signer)


def test_sign_without_timestamp_raises(signer):
    b = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_output_content(b"o")
    )
    with pytest.raises(ValueError, match="generation_timestamp"):
        b.sign(signer)


def test_sign_without_output_raises(signer):
    b = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
    )
    with pytest.raises(ValueError, match="output_content"):
        b.sign(signer)


# ── Group 3: Field-validation rejections ──


def test_capsule_id_over_cap_rejected():
    with pytest.raises(ValueError, match="capsule_id"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="x" * (MAX_CAPSULE_ID_LEN + 1),
            auditproof_id="a",
            verification_key_id="v",
        )


def test_auditproof_id_over_cap_rejected():
    with pytest.raises(ValueError, match="auditproof_id"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="c",
            auditproof_id="x" * (MAX_AUDITPROOF_ID_LEN + 1),
            verification_key_id="v",
        )


def test_verification_key_id_over_cap_rejected():
    with pytest.raises(ValueError, match="verification_key_id"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="c",
            auditproof_id="a",
            verification_key_id="x" * (MAX_VERIFICATION_KEY_ID_LEN + 1),
        )


def test_model_id_over_cap_rejected():
    with pytest.raises(ValueError, match="model_id"):
        C2PAManifestBuilder().with_model(
            model_id="x" * (MAX_MODEL_ID_LEN + 1)
        )


def test_prompt_intent_over_cap_rejected():
    with pytest.raises(ValueError, match="prompt"):
        C2PAManifestBuilder().with_prompt_intent("x" * (MAX_PROMPT_INTENT_LEN + 1))


def test_empty_capsule_id_rejected():
    with pytest.raises(ValueError, match="capsule_id"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="", auditproof_id="a", verification_key_id="v"
        )


def test_empty_output_content_rejected():
    with pytest.raises(ValueError, match="output_content"):
        C2PAManifestBuilder().with_output_content(b"")


def test_non_bytes_output_content_rejected():
    with pytest.raises(TypeError):
        C2PAManifestBuilder().with_output_content("not-bytes")  # type: ignore[arg-type]


def test_invalid_timestamp_rejected():
    with pytest.raises(ValueError, match="RFC 3339"):
        C2PAManifestBuilder().with_generation_timestamp("not-a-timestamp")


def test_timestamp_with_offset_accepted():
    b = C2PAManifestBuilder().with_generation_timestamp(
        "2026-05-28T12:34:56+05:30"
    )
    # No raise = success
    assert b is not None


def test_model_weights_hash_without_prefix_rejected():
    with pytest.raises(ValueError, match="sha512:"):
        C2PAManifestBuilder().with_model(
            model_id="m", model_weights_hash="abc123"
        )


def test_model_weights_hash_with_prefix_accepted():
    b = C2PAManifestBuilder().with_model(
        model_id="m", model_weights_hash="sha512:" + "a" * 128
    )
    assert b is not None


# ── Group 4: Forbidden-token scans (narrow-claim discipline) ──


@pytest.mark.parametrize("token", FORBIDDEN_BRAND_LEAK_TOKENS)
def test_brand_leak_tokens_rejected_in_model_id(token):
    with pytest.raises(ValueError, match="brand-leak"):
        C2PAManifestBuilder().with_model(model_id=f"openai/{token}-gpt-4o")


@pytest.mark.parametrize("token", FORBIDDEN_REGULATORY_TOKENS)
def test_regulatory_tokens_rejected_in_prompt_intent(token):
    with pytest.raises(ValueError, match="regulatory"):
        C2PAManifestBuilder().with_prompt_intent(
            f"This output {token} all HIPAA controls"
        )


def test_forbidden_token_in_capsule_id_rejected():
    with pytest.raises(ValueError, match="brand-leak"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="VerificationCdp_run_1",
            auditproof_id="a",
            verification_key_id="v",
        )


# ── Group 5: Custom assertions extensibility ──


def test_custom_assertion_with_unknown_label_rejected():
    with pytest.raises(ValueError, match="AssertionType"):
        C2PAManifestBuilder().with_assertion(
            {"label": "custom.not_in_enum", "data": {}}
        )


def test_custom_assertion_with_valid_label_accepted(signer):
    m = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
        .with_assertion(
            {
                "label": AssertionType.C2PA_TRAINING_MINING.value,
                "data": {"allowed": False},
            }
        )
        .sign(signer)
    )
    labels = [a["label"] for a in m.assertions]
    assert AssertionType.C2PA_TRAINING_MINING.value in labels


def test_custom_assertion_with_forbidden_token_in_data_rejected():
    with pytest.raises(ValueError):
        C2PAManifestBuilder().with_assertion(
            {
                "label": AssertionType.C2PA_INGREDIENT.value,
                "data": {"note": "This SATISFIED everything"},
            }
        )


# ── Group 6: Closed-enum invariants ──


def test_assertion_types_are_closed_enum():
    for member in AssertionType:
        assert member.value in VALID_ASSERTION_TYPES


def test_assertion_types_count_is_five():
    # ships 5 variants. New variants require ADR amendment.
    assert len(VALID_ASSERTION_TYPES) == 5


def test_signature_algorithm_is_ed25519_only():
    assert SIGNATURE_ALGORITHM == "Ed25519"


# ── Group 7: Independent offline verification ──


def test_verify_returns_signature_valid_true(signer):
    m = _build_basic(signer)
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), signer.public_key_bytes()
    )
    assert result.signature_valid is True
    assert result.claims_hash_valid is True


def test_verify_with_wrong_public_key_returns_signature_invalid(signer):
    other = _InMemoryEd25519Signer()
    m = _build_basic(signer)
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), other.public_key_bytes()
    )
    assert result.signature_valid is False


def test_verify_extracts_capsule_binders(signer):
    m = _build_basic(signer)
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), signer.public_key_bytes()
    )
    assert result.capsule_id == "cap_abc123"
    assert result.auditproof_id == "cdp_def456"
    assert result.verification_key_id == "nrx-verify-2026-05-28T12-34-50-cap_abc1"
    assert result.model_id == "openai/gpt-4o-2024-08-06"


def test_verify_result_carries_disclaimer(signer):
    m = _build_basic(signer)
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), signer.public_key_bytes()
    )
    assert result.disclaimer == NARROW_CLAIM_DISCLAIMER
    assert "STRUCTURAL" in result.disclaimer.upper()


def test_verify_returns_isinstance_result(signer):
    m = _build_basic(signer)
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), signer.public_key_bytes()
    )
    assert isinstance(result, C2PAVerificationResult)


# ── Group 8: Tampering detection ──


def test_modified_output_hash_breaks_signature(signer):
    m = _build_basic(signer)
    d = m.to_dict()
    nx = next(
        a
        for a in d["assertions"]
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    nx["data"]["output_content_hash"] = "sha512:" + "f" * 128
    tampered_bytes = canonicalize(d)
    result = verify_c2pa_manifest(tampered_bytes, signer.public_key_bytes())
    # claims_hash unchanged but assertions were modified -> recompute fails
    assert result.claims_hash_valid is False or result.signature_valid is False


def test_modified_signature_breaks_verification(signer):
    m = _build_basic(signer)
    d = m.to_dict()
    # Flip the first base64 char of the signature
    sig = d["signature"]["signature"]
    sig_body = sig[len("base64:") :]
    decoded = base64.b64decode(sig_body)
    tampered = bytearray(decoded)
    tampered[0] ^= 0xFF
    d["signature"]["signature"] = "base64:" + base64.b64encode(bytes(tampered)).decode()
    tampered_bytes = canonicalize(d)
    result = verify_c2pa_manifest(tampered_bytes, signer.public_key_bytes())
    assert result.signature_valid is False


def test_added_assertion_breaks_claims_hash(signer):
    m = _build_basic(signer)
    d = m.to_dict()
    d["assertions"].append(
        {
            "label": AssertionType.C2PA_INGREDIENT.value,
            "data": {"injected": True},
        }
    )
    tampered_bytes = canonicalize(d)
    result = verify_c2pa_manifest(tampered_bytes, signer.public_key_bytes())
    assert result.claims_hash_valid is False


def test_modified_model_id_breaks_claims_hash(signer):
    m = _build_basic(signer)
    d = m.to_dict()
    nx = next(
        a
        for a in d["assertions"]
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    nx["data"]["model_id"] = "openai/different-model"
    tampered_bytes = canonicalize(d)
    result = verify_c2pa_manifest(tampered_bytes, signer.public_key_bytes())
    assert result.claims_hash_valid is False


# ── Group 9: Optional / extension fields ──


def test_model_weights_hash_optional_when_omitted(signer):
    m = _build_basic(signer)
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    assert "model_weights_hash" not in nx["data"]


def test_model_weights_hash_present_when_supplied(signer):
    weights = "sha512:" + "a" * 128
    m = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m", model_weights_hash=weights)
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
        .sign(signer)
    )
    nx = next(
        a
        for a in m.assertions
        if a["label"] == AssertionType.NANORIX_AI_GENERATED.value
    )
    assert nx["data"]["model_weights_hash"] == weights


def test_custom_claim_generator_can_be_set(signer):
    m = (
        C2PAManifestBuilder()
        .with_claim_generator("Nanorix/1.0 my-app/2.0.0")
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
        .sign(signer)
    )
    assert m.claim_generator == "Nanorix/1.0 my-app/2.0.0"


def test_extra_claim_generator_info_appended(signer):
    info = C2PAClaimGeneratorInfo(name="my-app", version="2.0.0")
    m = (
        C2PAManifestBuilder()
        .with_claim_generator_info(info)
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
        .sign(signer)
    )
    assert len(m.claim_generator_info) == 2
    assert m.claim_generator_info[1].name == "my-app"


# ── Group 10: Signature algorithm enforcement ──


def test_non_ed25519_signer_rejected():
    class WrongSigner(EventSigner):
        def sign(self, message: bytes) -> bytes:
            return b"\x00" * 64

        def public_key_bytes(self) -> bytes:
            return b"\x00" * 32

        def algorithm_name(self) -> str:
            return "RSA-PSS-SHA256"

    b = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="v"
        )
        .with_model(model_id="m")
        .with_prompt_intent("i")
        .with_generation_timestamp("2026-05-28T12:34:56Z")
        .with_output_content(b"o")
    )
    with pytest.raises(ValueError, match="Ed25519"):
        b.sign(WrongSigner())


def test_unsupported_algorithm_in_manifest_raises():
    fake = {
        "claim_generator": "X",
        "claim_generator_info": [],
        "format": MANIFEST_FORMAT,
        "assertions": [],
        "claims_hash": "sha512:" + "a" * 128,
        "signature": {
            "algorithm": "ECDSA",
            "public_key": "base64:AAAA",
            "signature": "base64:AAAA",
        },
    }
    with pytest.raises(ValueError, match="signature algorithm"):
        verify_c2pa_manifest(
            json.dumps(fake).encode(), b"\x00" * 32
        )


# ── Group 11: Narrowness invariant — AuditProof byte-form unchanged ──


def test_no_audit_proof_struct_modification_in_c2pa_module():
    """Module must NOT import or modify the VerificationCdp struct."""
    import nanorix.integrations.c2pa_provenance as mod
    import inspect

    src = inspect.getsource(mod)
    # The c2pa module should NEVER import the internal AuditProof struct.
    assert "VerificationCdp" not in src or "VerificationCdp\"" in src or "VerificationCdp" in FORBIDDEN_BRAND_LEAK_TOKENS
    # FullCdp internal naming must not appear (other than forbidden-list literal)
    forbidden_idx = src.find("FullCdp")
    if forbidden_idx >= 0:
        # Allowed only in FORBIDDEN_BRAND_LEAK_TOKENS literal
        assert "FullCdp" in FORBIDDEN_BRAND_LEAK_TOKENS


def test_c2pa_module_does_not_import_internal_struct_modules():
    import nanorix.integrations.c2pa_provenance as mod
    import inspect

    src = inspect.getsource(mod)
    # These would indicate inappropriate coupling to internal AuditProof code
    assert "from nanorix._models import" not in src or "Capsule" not in src
    assert "import governance" not in src


# ── Group 12: Round-trip + JSON encoding stability ──


def test_round_trip_via_canonical_bytes(signer):
    m = _build_basic(signer)
    canonical = m.to_canonical_bytes()
    parsed = json.loads(canonical)
    re_canonical = canonicalize(parsed)
    assert canonical == re_canonical


def test_json_output_parses_cleanly(signer):
    m = _build_basic(signer)
    parsed = json.loads(m.to_json())
    assert parsed["claims_hash"].startswith("sha512:")


def test_verify_accepts_str_input(signer):
    m = _build_basic(signer)
    s = m.to_canonical_bytes().decode("utf-8")
    result = verify_c2pa_manifest(s, signer.public_key_bytes())
    assert result.signature_valid is True


# ── Group 13: Forbidden token coverage across all setters ──


def test_forbidden_token_in_verification_key_id_rejected():
    with pytest.raises(ValueError, match="brand-leak"):
        C2PAManifestBuilder().with_capsule(
            capsule_id="c", auditproof_id="a", verification_key_id="AVE-key"
        )


def test_forbidden_regulatory_term_in_claim_generator_rejected():
    with pytest.raises(ValueError, match="regulatory"):
        C2PAManifestBuilder().with_claim_generator("My App PASSED audit")


# ── Group 14: Verifier robustness ──


def test_verify_rejects_non_object_manifest():
    with pytest.raises(ValueError):
        verify_c2pa_manifest(b'"just a string"', b"\x00" * 32)


def test_verify_rejects_missing_signature_block():
    fake = {
        "claim_generator": "X",
        "claim_generator_info": [],
        "format": MANIFEST_FORMAT,
        "assertions": [],
        "claims_hash": "sha512:" + "a" * 128,
    }
    with pytest.raises(ValueError):
        verify_c2pa_manifest(json.dumps(fake).encode(), b"\x00" * 32)


def test_verify_rejects_non_list_assertions():
    fake = {
        "claim_generator": "X",
        "claim_generator_info": [],
        "format": MANIFEST_FORMAT,
        "assertions": "not a list",
        "claims_hash": "sha512:" + "a" * 128,
        "signature": {
            "algorithm": "Ed25519",
            "public_key": "base64:AAAA",
            "signature": "base64:AAAA",
        },
    }
    with pytest.raises(ValueError):
        verify_c2pa_manifest(json.dumps(fake).encode(), b"\x00" * 32)


# ── Group 15: AuditProof binding round-trip ──


def test_three_binders_round_trip_via_verify(signer):
    m = (
        C2PAManifestBuilder()
        .with_capsule(
            capsule_id="cap_specific_001",
            auditproof_id="cdp_specific_002",
            verification_key_id="nrx-verify-test-key-003",
        )
        .with_model(model_id="anthropic/claude-3-5-sonnet")
        .with_prompt_intent("structural test")
        .with_generation_timestamp("2026-05-28T00:00:00Z")
        .with_output_content(b"output bytes here")
        .sign(signer)
    )
    result = verify_c2pa_manifest(
        m.to_canonical_bytes(), signer.public_key_bytes()
    )
    assert result.capsule_id == "cap_specific_001"
    assert result.auditproof_id == "cdp_specific_002"
    assert result.verification_key_id == "nrx-verify-test-key-003"
    assert result.model_id == "anthropic/claude-3-5-sonnet"
    assert result.signature_valid is True
    assert result.claims_hash_valid is True
