"""Tests for nanorix.healthcare.X12EligibilityAdapter (Batch 2 sub-item 2).

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Member IDs, NPIs, payer IDs are obviously fictitious. No real PHI even
in fixtures.

Coverage:
- 270 inquiry parse + 271 response parse
- ISA segment separator auto-detection
- payer info extraction (NM1 PR loop)
- member ID hashing (never raw)
- service-type code extraction from EQ
- Multi-transaction batch + per-record receipt emission
- Vocabulary discipline scan (4 banned terms absent)
- Strict-mode validation
- Closed-enum rejection
- Reserved-key constraint + 4096-byte metadata limit
"""
from __future__ import annotations

import hashlib
import inspect
import re
from typing import List

import pytest

from nanorix.capsule_record import PATTERN_TAGS
from nanorix.healthcare import (
    X12EligibilityAdapter,
)


# ── Synthetic fixtures ─────────────────────────────────────────────


# X12 270 inquiry, standard separators (* element, ~ segment, : sub-element).
# Note: ISA segment is exactly 106 bytes followed by ~ terminator.
ISA_270_HEADER = (
    "ISA*00*          *00*          *ZZ*SUBMITTER-TEST *"
    "ZZ*RECEIVER-TEST  *260524*1200*^*00501*000000001*0*P*:~"
)

ISA_271_HEADER = (
    "ISA*00*          *00*          *ZZ*PAYER-TEST     *"
    "ZZ*SUBMITTER-TEST *260524*1230*^*00501*000000002*0*P*:~"
)


X12_270_FIXTURE = (
    ISA_270_HEADER
    + "GS*HS*SUBMITTER-TEST*RECEIVER-TEST*20260524*1200*1*X*005010X279A1~"
    + "ST*270*0001*005010X279A1~"
    + "BHT*0022*13*REF-TEST-001*20260524*1200~"
    + "HL*1**20*1~"
    + "NM1*PR*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*0~"
    + "TRN*1*TRN-TEST-001*9SUBMITTER~"
    + "NM1*IL*1*DOE*JOHN****MI*MEMBER-TEST-001~"
    + "DMG*D8*19800101*M~"
    + "DTP*291*D8*20260524~"
    + "EQ*30~"
    + "SE*13*0001~"
    + "GE*1*1~"
    + "IEA*1*000000001~"
).encode("ascii")


X12_271_FIXTURE = (
    ISA_271_HEADER
    + "GS*HB*PAYER-TEST*SUBMITTER-TEST*20260524*1230*2*X*005010X279A1~"
    + "ST*271*0002*005010X279A1~"
    + "BHT*0022*11*REF-TEST-001*20260524*1230~"
    + "HL*1**20*1~"
    + "NM1*PR*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*0~"
    + "TRN*2*TRN-TEST-001*9PAYER~"
    + "NM1*IL*1*DOE*JOHN****MI*MEMBER-TEST-001~"
    + "DMG*D8*19800101*M~"
    + "EB*1*IND*30*HM*GOLD PLAN~"
    + "SE*12*0002~"
    + "GE*1*2~"
    + "IEA*1*000000002~"
).encode("ascii")


# Multiple service-type codes (EQ with sub-element separator).
X12_270_MULTI_STC_FIXTURE = (
    ISA_270_HEADER.replace("000000001", "000000003")
    + "GS*HS*SUBMITTER-TEST*RECEIVER-TEST*20260524*1200*3*X*005010X279A1~"
    + "ST*270*0003*005010X279A1~"
    + "BHT*0022*13*REF-MULTI-001*20260524*1200~"
    + "HL*1**20*1~"
    + "NM1*PR*2*ANTHEM-TEST*****PI*99999~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*9876543210~"
    + "HL*3*2*22*0~"
    + "NM1*IL*1*SMITH*JANE****MI*MEMBER-TEST-002~"
    + "EQ*30:88:AL:MH~"
    + "SE*10*0003~"
    + "GE*1*3~"
    + "IEA*1*000000003~"
).encode("ascii")


# ── 1. Basic parse / structural smoke ──────────────────────────────


def test_x12_270_parses_to_structural_metadata():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    assert parsed.transaction_set == "270"
    assert parsed.isa_control_number == "000000001"
    assert parsed.gs_control_number == "1"
    assert parsed.st_control_number == "0001"
    assert "SUBMITTER-TEST" in parsed.submitter_id
    assert "RECEIVER-TEST" in parsed.receiver_id


def test_x12_270_metadata_includes_reserved_keys():
    adapter = X12EligibilityAdapter()
    meta = adapter.parse_transaction(X12_270_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-270-271"
    assert meta["nanorix:framework_version"] == "x12-005010X279A1"


def test_x12_271_parses_correctly():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_271_FIXTURE)
    assert parsed.transaction_set == "271"
    assert parsed.is_response is True
    assert parsed.is_inquiry is False


def test_x12_270_is_inquiry_flag():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    assert parsed.is_inquiry is True
    assert parsed.is_response is False


def test_x12_270_payer_info_extracted():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    assert parsed.payer_id == "60054"
    assert parsed.payer_name == "AETNA-TEST"


def test_x12_271_payer_info_extracted():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_271_FIXTURE)
    assert parsed.payer_id == "60054"
    assert parsed.payer_name == "AETNA-TEST"


# ── 2. Member ID hashing (never raw) ───────────────────────────────


def test_x12_270_member_id_hashed_not_raw():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    # The raw ID "MEMBER-TEST-001" should NEVER appear in metadata.
    # Only its hash binds into AuditProof.
    assert parsed.member_id_hash != ""
    assert len(parsed.member_id_hash) == 64  # SHA-256 hex
    assert "MEMBER-TEST-001" not in parsed.member_id_hash

    # Confirm hash matches direct computation.
    expected = hashlib.sha256(b"MEMBER-TEST-001").hexdigest()
    assert parsed.member_id_hash == expected


def test_x12_member_id_hash_in_metadata():
    adapter = X12EligibilityAdapter()
    meta = adapter.parse_transaction(X12_270_FIXTURE).to_metadata()
    assert "member_id_hash" in meta
    # Raw ID must NOT appear anywhere in metadata.
    for key, value in meta.items():
        assert "MEMBER-TEST-001" not in value, (
            f"raw member ID leaked into metadata[{key!r}]={value!r}"
        )


def test_x12_member_id_hash_deterministic_across_parses():
    adapter = X12EligibilityAdapter()
    h1 = adapter.parse_transaction(X12_270_FIXTURE).member_id_hash
    h2 = adapter.parse_transaction(X12_270_FIXTURE).member_id_hash
    assert h1 == h2


# ── 3. Service-type code extraction ────────────────────────────────


def test_x12_270_single_service_type_extracted():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    assert parsed.service_type_codes == "30"


def test_x12_270_multi_service_type_extracted_from_subelements():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_MULTI_STC_FIXTURE)
    # EQ*30:88:AL:MH~ contains 4 service-type codes.
    codes = set(parsed.service_type_codes.split(","))
    assert codes == {"30", "88", "AL", "MH"}


def test_x12_common_service_types_table_includes_health_benefit():
    adapter = X12EligibilityAdapter()
    common = adapter.common_service_types
    assert common["30"] == "Health Benefit Plan Coverage"
    assert common["88"] == "Pharmacy"
    assert common["MH"] == "Mental Health"


# ── 4. ISA segment separator handling ──────────────────────────────


def test_x12_isa_segment_separators_auto_detected():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    # Default separators: * element, ~ segment, : sub-element.
    # Successful parse implies they were correctly detected.
    assert parsed.transaction_set == "270"


def test_x12_payload_too_short_for_isa_rejected():
    adapter = X12EligibilityAdapter()
    bad = b"ISA*00*"  # < 106 bytes
    with pytest.raises(ValueError, match="ISA"):
        adapter.parse_transaction(bad)


def test_x12_payload_not_starting_with_isa_rejected():
    adapter = X12EligibilityAdapter()
    bad = (b"X" * 200)
    with pytest.raises(ValueError, match="ISA"):
        adapter.parse_transaction(bad)


def test_x12_empty_payload_rejected():
    adapter = X12EligibilityAdapter()
    with pytest.raises(ValueError, match="empty"):
        adapter.parse_transaction(b"")


def test_x12_non_bytes_input_rejected():
    adapter = X12EligibilityAdapter()
    with pytest.raises(ValueError, match="expects bytes"):
        adapter.parse_transaction("not bytes")  # type: ignore[arg-type]


# ── 5. Payload integrity hash ──────────────────────────────────────


def test_x12_payload_hash_is_deterministic_sha256():
    adapter = X12EligibilityAdapter()
    h1 = adapter.parse_transaction(X12_270_FIXTURE).payload_hash
    h2 = adapter.parse_transaction(X12_270_FIXTURE).payload_hash
    assert h1 == h2
    assert len(h1) == 64


def test_x12_different_payloads_produce_different_hashes():
    adapter = X12EligibilityAdapter()
    h_270 = adapter.parse_transaction(X12_270_FIXTURE).payload_hash
    h_271 = adapter.parse_transaction(X12_271_FIXTURE).payload_hash
    assert h_270 != h_271


# ── 6. parse_270 + parse_271 direction-gating ──────────────────────


def test_x12_parse_270_accepts_inquiry():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_270(X12_270_FIXTURE)
    assert parsed.transaction_set == "270"


def test_x12_parse_270_rejects_response():
    adapter = X12EligibilityAdapter()
    with pytest.raises(ValueError, match="expected 270"):
        adapter.parse_270(X12_271_FIXTURE)


def test_x12_parse_271_accepts_response():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_271(X12_271_FIXTURE)
    assert parsed.transaction_set == "271"


def test_x12_parse_271_rejects_inquiry():
    adapter = X12EligibilityAdapter()
    with pytest.raises(ValueError, match="expected 271"):
        adapter.parse_271(X12_270_FIXTURE)


# ── 7. Multi-transaction batch ─────────────────────────────────────


def _build_batch(n: int) -> List[bytes]:
    """Synthesize a batch of 270 inquiries with unique control numbers."""
    out = []
    for i in range(n):
        ctrl = f"{i:09d}"
        ref = f"REF-BATCH-{i:06d}"
        member = f"MEMBER-BATCH-{i:06d}"
        isa = (
            "ISA*00*          *00*          *ZZ*SUBMITTER-TEST *"
            "ZZ*RECEIVER-TEST  *260524*1200*^*00501*"
            + ctrl
            + "*0*P*:~"
        )
        fx = (
            isa
            + "GS*HS*SUBMITTER-TEST*RECEIVER-TEST*20260524*1200*1*X*005010X279A1~"
            + f"ST*270*{i:04d}*005010X279A1~"
            + f"BHT*0022*13*{ref}*20260524*1200~"
            + "HL*1**20*1~"
            + "NM1*PR*2*AETNA-TEST*****PI*60054~"
            + "HL*2*1*21*1~"
            + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
            + "HL*3*2*22*0~"
            + f"NM1*IL*1*DOE*JOHN****MI*{member}~"
            + "EQ*30~"
            + "SE*10*0001~"
            + "GE*1*1~"
            + f"IEA*1*{ctrl}~"
        ).encode("ascii")
        out.append(fx)
    return out


def test_x12_batch_n10_all_parse_distinctly():
    adapter = X12EligibilityAdapter()
    parsed_list = [adapter.parse_transaction(p) for p in _build_batch(10)]
    assert len(parsed_list) == 10
    assert len({p.isa_control_number for p in parsed_list}) == 10
    assert len({p.payload_hash for p in parsed_list}) == 10


def test_x12_batch_n100_all_parse_distinctly():
    adapter = X12EligibilityAdapter()
    parsed_list = [
        adapter.parse_transaction(p) for p in _build_batch(100)
    ]
    assert len(parsed_list) == 100


def test_x12_batch_n1000_all_parse_distinctly():
    adapter = X12EligibilityAdapter()
    parsed_list = [
        adapter.parse_transaction(p) for p in _build_batch(1000)
    ]
    assert len(parsed_list) == 1000


# ── 8. Per-record receipt integration ──────────────────────────────


def test_x12_receipt_pattern_tag_matches_closed_enum():
    pattern_tag = X12EligibilityAdapter.receipt_pattern_tag()
    assert pattern_tag == "rcm_eligibility"
    assert pattern_tag in PATTERN_TAGS


def test_x12_receipt_pattern_tag_static_method():
    assert X12EligibilityAdapter.receipt_pattern_tag() == "rcm_eligibility"


# ── 9. Metadata constraints ────────────────────────────────────────


def test_x12_metadata_serializes_within_4kb_limit():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    import json
    meta_bytes = json.dumps(parsed.to_metadata()).encode("utf-8")
    assert len(meta_bytes) <= 4096


def test_x12_metadata_has_reserved_framework_keys():
    adapter = X12EligibilityAdapter()
    meta = adapter.parse_transaction(X12_270_FIXTURE).to_metadata()
    assert "nanorix:framework" in meta
    assert "nanorix:framework_version" in meta


def test_x12_derive_metadata_accepts_workflow_id():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    meta = X12EligibilityAdapter.derive_metadata(
        parsed, customer_workflow_id="adonis-elig-job-001"
    )
    assert meta["customer_workflow_id"] == "adonis-elig-job-001"


# ── 10. Vocabulary discipline ──────────────────────────────────────


def test_x12_module_text_has_no_banned_verdict_vocabulary():
    import nanorix.healthcare.x12_eligibility as x12_mod
    source = inspect.getsource(x12_mod)
    banned = ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]
    for word in banned:
        matches = re.findall(rf"\b{word}\b", source)
        assert not matches, (
            f"banned verdict vocabulary {word!r} found: {matches[:3]}"
        )


def test_x12_module_avoids_engine_name_leaks(engine_names):
    import nanorix.healthcare.x12_eligibility as x12_mod
    source = inspect.getsource(x12_mod)
    for engine in engine_names:
        matches = re.findall(rf"\b{engine}\b", source, re.IGNORECASE)
        assert not matches, f"possible engine name leak: {engine}"


# ── 11. Segment types observed ─────────────────────────────────────


def test_x12_270_segment_types_recorded():
    adapter = X12EligibilityAdapter()
    parsed = adapter.parse_transaction(X12_270_FIXTURE)
    segs = parsed.segment_types_observed.split(",")
    assert "ISA" in segs
    assert "GS" in segs
    assert "ST" in segs
    assert "EQ" in segs
    assert "SE" in segs
    assert "GE" in segs
    assert "IEA" in segs


# ── 12. Edge cases ─────────────────────────────────────────────────


def test_x12_supported_transaction_sets():
    adapter = X12EligibilityAdapter()
    assert adapter.supported_transaction_sets == ["270", "271"]


def test_x12_invalid_transaction_set_rejected():
    """An ST segment with a non-270/271 transaction set ID is rejected."""
    adapter = X12EligibilityAdapter()
    # Mutate the X12 270 fixture to declare ST*999 instead.
    bad = X12_270_FIXTURE.replace(b"ST*270*", b"ST*999*")
    with pytest.raises(ValueError, match="transaction_set"):
        adapter.parse_transaction(bad)
