"""Tests for GRC evidence-filing adapters.

These tests import ONLY the pure core (``nanorix.grc._core``) +
:mod:`nanorix.control_map` + the guarded shells with a dependency-injected
``post=`` transport. They do NOT import any GRC vendor SDK (vanta-api /
requests) — verifying the architecture goal that the GRC core + shells are
exercisable without any vendor SDK installed.

Invariants asserted:

- the mapping is applied at adapter time (specific cell + baseline
  fallback + customer-namespace control IDs);
- every evidence body carries ``relationship: "references"`` + the mapping
  disclaimer (narrow-claim discipline) and NO verdict vocabulary;
- the receipt's signed fields + outer envelope land in the body unchanged
  (no new signed primitive; the AuditProof is consumed verbatim);
- the shells POST one body per (receipt × control_id) to the injected
  transport;
- adapter objects retain NO vendor credential after a call
  (no-credential-retention).
"""

from __future__ import annotations

import pytest

from nanorix.control_map import DISCLAIMER as MAPPING_DISCLAIMER
from nanorix.control_map import load_mapping
from nanorix.grc import (
    FORBIDDEN_VOCAB,
    REFERENCE_RELATIONSHIP,
    GrcError,
    assert_reference_only,
    auditproof_to_evidence_bodies,
    receipt_to_evidence_body,
    resolve_control_ids,
)
from nanorix.grc.anecdotes import AnecdotesEvidenceAdapter
from nanorix.grc.drata import DrataEvidenceAdapter
from nanorix.grc.vanta import VantaEvidenceAdapter


# ── Fixtures ──────────────────────────────────────────────────────────


def _receipt(pattern_tag="rcm_claim", index=0):
    return {
        "record_index": index,
        "record_id": f"rec_{index}",
        "record_input_hash": "sha512:aaaa",
        "record_output_hash": "sha512:bbbb",
        "record_chain_hash": "sha512:cccc",
        "pattern_tag": pattern_tag,
        "merkle_inclusion_proof": ["sha512:dddd", "sha512:eeee"],
    }


def _proof(receipts=None):
    return {
        "capsule_id": "cap_a1b2c3d4",
        "key_id": "nrx-verify-2026-05-29-a1b2c3d4",
        "verification_key": "base64:PUBKEY",
        "record_receipts_merkle_root": "sha512:rootroot",
        "terminated_at": "2026-05-29T12:00:00Z",
        "chain": [
            {"subsystem": "eee_namespace", "chain_hash": "sha512:s1"},
            {"subsystem": "capsule_destroy", "chain_hash": "sha512:s8"},
        ],
        "attestation": {"algorithm": "Ed25519", "signature": "base64:SIG"},
        "record_receipts": receipts if receipts is not None else [_receipt()],
    }


MAPPING = load_mapping(framework_version="2026-02")


# ── Core: control-ID resolution at adapter time ─────────────


def test_resolve_specific_cell():
    ids = resolve_control_ids(
        MAPPING, jurisdiction="US", classification="PHI", pattern_tag="rcm_claim"
    )
    assert "HIPAA-164.312(b)" in ids
    assert "SOC2-CC7.2" in ids


def test_resolve_baseline_fallback_when_pattern_none():
    ids = resolve_control_ids(
        MAPPING, jurisdiction="EU", classification="PII", pattern_tag=None
    )
    assert "GDPR-Art-30" in ids


def test_resolve_baseline_fallback_when_cell_empty():
    # A jurisdiction/classification with no seeded pattern cell falls back.
    ids = resolve_control_ids(
        MAPPING,
        jurisdiction="EU",
        classification="PII",
        pattern_tag="not_a_seeded_pattern",
    )
    assert "GDPR-Art-30" in ids


def test_resolve_appends_customer_namespace_ids():
    ids = resolve_control_ids(
        MAPPING,
        jurisdiction="US",
        classification="PHI",
        pattern_tag="rcm_claim",
        additional_control_ids=["MAYO-CS-POLICY-1.7"],
    )
    assert ids[-1] == "MAYO-CS-POLICY-1.7"


def test_resolve_dedupes_preserving_order():
    ids = resolve_control_ids(
        MAPPING,
        jurisdiction="US",
        classification="PHI",
        pattern_tag="rcm_claim",
        additional_control_ids=["HIPAA-164.312(b)"],  # already in the cell
    )
    assert ids.count("HIPAA-164.312(b)") == 1


# ── Core: forbidden-vocabulary gate (reference-only) ──────────────────


@pytest.mark.parametrize("word", FORBIDDEN_VOCAB)
def test_gate_rejects_each_verdict_word(word):
    with pytest.raises(GrcError):
        assert_reference_only({"note": f"this control is {word}"})


def test_gate_rejects_lowercase_verdict():
    with pytest.raises(GrcError):
        assert_reference_only("evidence satisfied the control")


def test_gate_allows_reference_relationship_body():
    body = receipt_to_evidence_body(
        _receipt(),
        {"capsule_id": "cap_x"},
        "HIPAA-164.312(b)",
        framework_version="2026-02",
    )
    assert_reference_only(body)  # no exception
    assert body["relationship"] == "references"


# ── Core: evidence-body shaping ───────────────────────────────────────


def test_evidence_body_carries_reference_and_disclaimer():
    body = receipt_to_evidence_body(
        _receipt(),
        {"capsule_id": "cap_x", "key_id": "kid", "signature": "base64:SIG"},
        "HIPAA-164.312(b)",
        framework_version="2026-02",
    )
    assert body["relationship"] == REFERENCE_RELATIONSHIP
    assert body["relationship"] == "references"
    assert body["disclaimer"] == MAPPING_DISCLAIMER
    assert body["framework_version"] == "2026-02"
    assert body["evidence_type"] == "auditproof_receipt"
    assert body["control_id"] == "HIPAA-164.312(b)"


def test_evidence_body_preserves_signed_receipt_fields():
    receipt = _receipt(index=3)
    body = receipt_to_evidence_body(
        receipt, {"capsule_id": "cap_x"}, "SOC2-CC7.2", framework_version="2026-02"
    )
    r = body["receipt"]
    assert r["record_index"] == 3
    assert r["record_id"] == "rec_3"
    assert r["record_chain_hash"] == "sha512:cccc"
    assert r["record_input_hash"] == "sha512:aaaa"
    assert r["record_output_hash"] == "sha512:bbbb"
    assert r["pattern_tag"] == "rcm_claim"
    assert r["merkle_inclusion_proof"] == ["sha512:dddd", "sha512:eeee"]


def test_evidence_body_extra_fields_merged():
    body = receipt_to_evidence_body(
        _receipt(),
        {"capsule_id": "cap_x"},
        "SOC2-CC7.2",
        framework_version="2026-02",
        extra_fields={"tenant_id": "tnt_42"},
    )
    assert body["tenant_id"] == "tnt_42"


def test_receipt_missing_required_field_raises():
    bad = _receipt()
    del bad["record_chain_hash"]
    with pytest.raises(GrcError):
        receipt_to_evidence_body(
            bad, {"capsule_id": "cap_x"}, "SOC2-CC7.2", framework_version="2026-02"
        )


# ── Core: full AuditProof → evidence bodies ───────────────────────────


def test_auditproof_to_bodies_one_per_receipt_times_control():
    proof = _proof(receipts=[_receipt(index=0), _receipt(index=1)])
    bodies = auditproof_to_evidence_bodies(
        proof, jurisdiction="US", classification="PHI", mapping=MAPPING
    )
    cell_ids = MAPPING.resolve("US", "PHI", "rcm_claim")
    assert len(bodies) == 2 * len(cell_ids)
    # Every body references one of the cell's control IDs.
    for body in bodies:
        assert body["control_id"] in cell_ids


def test_auditproof_envelope_extracted_from_chain_and_attestation():
    bodies = auditproof_to_evidence_bodies(
        _proof(), jurisdiction="US", classification="PHI", mapping=MAPPING
    )
    env = bodies[0]["audit_proof_envelope"]
    assert env["capsule_id"] == "cap_a1b2c3d4"
    assert env["key_id"] == "nrx-verify-2026-05-29-a1b2c3d4"
    assert env["record_receipts_merkle_root"] == "sha512:rootroot"
    assert env["step_8_chain_hash"] == "sha512:s8"  # last chain entry
    assert env["signature"] == "base64:SIG"
    assert env["terminated_at"] == "2026-05-29T12:00:00Z"


def test_auditproof_with_no_receipts_yields_empty_list():
    bodies = auditproof_to_evidence_bodies(
        _proof(receipts=[]), jurisdiction="US", classification="PHI", mapping=MAPPING
    )
    assert bodies == []


def test_auditproof_untagged_receipt_uses_baseline():
    proof = _proof(receipts=[_receipt(pattern_tag=None, index=0)])
    # Force a deletion so pattern_tag is genuinely absent.
    del proof["record_receipts"][0]["pattern_tag"]
    bodies = auditproof_to_evidence_bodies(
        proof, jurisdiction="EU", classification="PII", mapping=MAPPING
    )
    baseline = MAPPING.resolve_baseline("EU", "PII")
    assert {b["control_id"] for b in bodies} == set(baseline)


def test_auditproof_default_framework_version_when_no_mapping():
    bodies = auditproof_to_evidence_bodies(
        _proof(), jurisdiction="US", classification="PHI"
    )
    assert all(b["framework_version"] == "2026-02" for b in bodies)


# ── Invariant: no new signed primitive (AuditProof consumed verbatim) ─


def test_bodies_add_no_signed_field_to_receipt():
    proof = _proof()
    bodies = auditproof_to_evidence_bodies(
        proof, jurisdiction="US", classification="PHI", mapping=MAPPING
    )
    # The receipt sub-document has only the signed fields — no
    # control_tags / no synthesized signature / no mapping stamped in.
    receipt_keys = set(bodies[0]["receipt"].keys())
    assert "control_tags" not in receipt_keys
    assert "signature" not in receipt_keys
    assert receipt_keys == {
        "record_index",
        "record_id",
        "record_chain_hash",
        "record_input_hash",
        "record_output_hash",
        "pattern_tag",
        "merkle_inclusion_proof",
    }


# ── Invariant: narrow-claim discipline across every emitted body ──────


def test_every_body_is_reference_only_and_has_disclaimer():
    proof = _proof(receipts=[_receipt(index=0), _receipt(index=1)])
    bodies = auditproof_to_evidence_bodies(
        proof, jurisdiction="US", classification="PHI", mapping=MAPPING
    )
    for body in bodies:
        assert body["relationship"] == "references"
        assert body["disclaimer"] == MAPPING_DISCLAIMER
        # No verdict vocabulary anywhere in the body.
        assert_reference_only(body)


# ── Shells: Vanta / Drata / Anecdotes pushers (injected post=) ────────


def test_vanta_adapter_files_one_post_per_body():
    sent = []
    adapter = VantaEvidenceAdapter(
        token="vt", workspace_id="ws_1", mapping=MAPPING,
        post=lambda path, body: sent.append((path, body)) or {"ok": True},
    )
    confirmations = adapter.file_auditproof(
        _proof(), jurisdiction="US", classification="PHI"
    )
    cell_ids = MAPPING.resolve("US", "PHI", "rcm_claim")
    assert len(sent) == len(cell_ids)
    assert all(p == "/workspaces/ws_1/evidence" for p, _ in sent)
    assert all(b["relationship"] == "references" for _, b in sent)
    assert all(b["workspace_id"] == "ws_1" for _, b in sent)
    assert len(confirmations) == len(cell_ids)


def test_drata_adapter_path_and_reference():
    sent = []
    adapter = DrataEvidenceAdapter(
        token="dt", workspace_id="ws_2", mapping=MAPPING,
        post=lambda path, body: sent.append((path, body)) or {},
    )
    adapter.file_auditproof(_proof(), jurisdiction="US", classification="PHI")
    assert all(p == "/workspaces/ws_2/evidence" for p, _ in sent)
    assert all(b["disclaimer"] == MAPPING_DISCLAIMER for _, b in sent)


def test_anecdotes_adapter_tenant_path():
    sent = []
    adapter = AnecdotesEvidenceAdapter(
        token="at", tenant_id="tnt_9", mapping=MAPPING,
        post=lambda path, body: sent.append((path, body)) or {},
    )
    adapter.file_auditproof(_proof(), jurisdiction="US", classification="PHI")
    assert all(p == "/tenants/tnt_9/evidence" for p, _ in sent)
    assert all(b["tenant_id"] == "tnt_9" for _, b in sent)


def test_vanta_adapter_customer_namespace_ids_filed():
    sent = []
    adapter = VantaEvidenceAdapter(
        token="vt", workspace_id="ws_1", mapping=MAPPING,
        post=lambda path, body: sent.append(body) or {},
    )
    adapter.file_auditproof(
        _proof(),
        jurisdiction="US",
        classification="PHI",
        additional_control_ids=["MAYO-CS-POLICY-1.7"],
    )
    filed_ids = {b["control_id"] for b in sent}
    assert "MAYO-CS-POLICY-1.7" in filed_ids


def test_vanta_adapter_requires_token_workspace_or_post():
    with pytest.raises(GrcError):
        VantaEvidenceAdapter(token=None, workspace_id=None)


def test_drata_adapter_requires_token_workspace_or_post():
    with pytest.raises(GrcError):
        DrataEvidenceAdapter(token=None, workspace_id=None)


def test_anecdotes_adapter_requires_token_tenant_or_post():
    with pytest.raises(GrcError):
        AnecdotesEvidenceAdapter(token=None, tenant_id=None)


# ── No-credential-retention (mirrors connectors' discipline) ──────────


def test_vanta_adapter_retains_no_token_attribute():
    secret = "vanta_super_secret_token"
    adapter = VantaEvidenceAdapter(token=secret, workspace_id="ws_1", mapping=MAPPING)
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"


def test_drata_adapter_retains_no_token_attribute():
    secret = "drata_super_secret_token"
    adapter = DrataEvidenceAdapter(token=secret, workspace_id="ws_2", mapping=MAPPING)
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"


def test_anecdotes_adapter_retains_no_token_attribute():
    secret = "anecdotes_super_secret_token"
    adapter = AnecdotesEvidenceAdapter(token=secret, tenant_id="tnt_9", mapping=MAPPING)
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"


def test_grc_adapter_credential_not_in_filed_body():
    sent = []
    secret = "vanta_secret_token_xyz"
    adapter = VantaEvidenceAdapter(
        token=secret, workspace_id="ws_1", mapping=MAPPING,
        post=lambda path, body: sent.append(body) or {},
    )
    adapter.file_auditproof(_proof(), jurisdiction="US", classification="PHI")
    for body in sent:
        # The token never appears in any filed evidence body.
        assert secret not in str(body)
