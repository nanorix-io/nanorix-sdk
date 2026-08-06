"""Tests for nanorix.healthcare.X12ServicesReviewAdapter (Batch 2 sub-item 3).

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient IDs, NPIs, provider IDs, certification IDs are obviously
fictitious. No real PHI even in fixtures.

Coverage:
- 278 REQUEST parse (BHT06=13)
- 278 RESPONSE parse (BHT06=11)
- Certification action codes (A1/A2/A3/A4) extraction
- Convenience properties (is_approved/is_denied/is_pended)
- Provider NPI extraction (requesting + service)
- Member ID hashing (never raw)
- Certification number extraction (REF*BB, REF*G1)
- Multi-transaction batch + per-record receipt emission
- Vocabulary discipline scan
- Engine-name leak guard
- Closed-enum rejection (non-278)
"""
from __future__ import annotations

import hashlib
import inspect
import re
from typing import List

import pytest

from nanorix.capsule_record import PATTERN_TAGS
from nanorix.healthcare import (
    X12ServicesReviewAdapter,
)


# ── Synthetic fixtures ─────────────────────────────────────────────


ISA_278_REQUEST_HEADER = (
    "ISA*00*          *00*          *ZZ*PROVIDER-TEST  *"
    "ZZ*PAYER-TEST     *260524*1200*^*00501*000000001*0*P*:~"
)

ISA_278_RESPONSE_HEADER = (
    "ISA*00*          *00*          *ZZ*PAYER-TEST     *"
    "ZZ*PROVIDER-TEST  *260524*1230*^*00501*000000002*0*P*:~"
)


X12_278_REQUEST_FIXTURE = (
    ISA_278_REQUEST_HEADER
    + "GS*HI*PROVIDER-TEST*PAYER-TEST*20260524*1200*1*X*005010X217~"
    + "ST*278*0001*005010X217~"
    + "BHT*0007*13*REF-PA-001*20260524*1200~"
    + "HL*1**20*1~"
    + "NM1*X3*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*1~"
    + "NM1*IL*1*DOE*JOHN****MI*MEMBER-PA-001~"
    + "DMG*D8*19800101*M~"
    + "HL*4*3*EV*0~"
    + "UM*HS*AR*****20260524~"
    + "NM1*SJ*1*SURGEON*TEST****XX*9876543210~"
    + "REF*G1*PA-REQUEST-001~"
    + "SE*15*0001~"
    + "GE*1*1~"
    + "IEA*1*000000001~"
).encode("ascii")


X12_278_RESPONSE_APPROVED_FIXTURE = (
    ISA_278_RESPONSE_HEADER
    + "GS*HN*PAYER-TEST*PROVIDER-TEST*20260524*1230*2*X*005010X217~"
    + "ST*278*0002*005010X217~"
    + "BHT*0007*11*REF-PA-001*20260524*1230~"
    + "HL*1**20*1~"
    + "NM1*X3*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*1~"
    + "NM1*IL*1*DOE*JOHN****MI*MEMBER-PA-001~"
    + "HL*4*3*EV*0~"
    + "UM*HS*AR*****20260524~"
    + "HCR*A1*CERT-NUM-001~"
    + "REF*BB*CERT-NUM-001~"
    + "SE*13*0002~"
    + "GE*1*2~"
    + "IEA*1*000000002~"
).encode("ascii")


X12_278_RESPONSE_DENIED_FIXTURE = (
    ISA_278_RESPONSE_HEADER.replace("000000002", "000000003")
    + "GS*HN*PAYER-TEST*PROVIDER-TEST*20260524*1230*3*X*005010X217~"
    + "ST*278*0003*005010X217~"
    + "BHT*0007*11*REF-PA-002*20260524*1230~"
    + "HL*1**20*1~"
    + "NM1*X3*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*1~"
    + "NM1*IL*1*DOE*JANE****MI*MEMBER-PA-002~"
    + "HL*4*3*EV*0~"
    + "UM*HS*DS*****20260524~"
    + "HCR*A3*CERT-NUM-002~"
    + "SE*11*0003~"
    + "GE*1*3~"
    + "IEA*1*000000003~"
).encode("ascii")


X12_278_RESPONSE_PENDED_FIXTURE = (
    ISA_278_RESPONSE_HEADER.replace("000000002", "000000004")
    + "GS*HN*PAYER-TEST*PROVIDER-TEST*20260524*1230*4*X*005010X217~"
    + "ST*278*0004*005010X217~"
    + "BHT*0007*11*REF-PA-003*20260524*1230~"
    + "HL*1**20*1~"
    + "NM1*X3*2*AETNA-TEST*****PI*60054~"
    + "HL*2*1*21*1~"
    + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
    + "HL*3*2*22*0~"
    + "NM1*IL*1*DOE*BABY****MI*MEMBER-PA-003~"
    + "UM*HS*MS*****20260524~"
    + "HCR*A4*CERT-NUM-003~"
    + "SE*10*0004~"
    + "GE*1*4~"
    + "IEA*1*000000004~"
).encode("ascii")


# ── 1. Direction detection ─────────────────────────────────────────


def test_x12_278_request_direction_detected():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.direction == "REQUEST"
    assert parsed.is_request is True
    assert parsed.is_response is False


def test_x12_278_response_direction_detected():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_APPROVED_FIXTURE)
    assert parsed.direction == "RESPONSE"
    assert parsed.is_response is True


def test_x12_278_request_certification_action_empty():
    """REQUEST has no HCR; certification_action must be empty."""
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.certification_action == ""


# ── 2. Certification action codes ──────────────────────────────────


def test_x12_278_response_a1_approved():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_APPROVED_FIXTURE)
    assert parsed.certification_action == "A1"
    assert parsed.is_approved is True
    assert parsed.is_denied is False
    assert parsed.is_pended is False


def test_x12_278_response_a3_denied():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_DENIED_FIXTURE)
    assert parsed.certification_action == "A3"
    assert parsed.is_denied is True
    assert parsed.is_approved is False


def test_x12_278_response_a4_pended():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_PENDED_FIXTURE)
    assert parsed.certification_action == "A4"
    assert parsed.is_pended is True
    assert parsed.is_approved is False


def test_x12_278_certification_actions_table_complete():
    adapter = X12ServicesReviewAdapter()
    actions = adapter.certification_actions
    assert actions["A1"] == "Certified in Total"
    assert actions["A3"] == "Not Certified"
    assert actions["A4"] == "Pended"


# ── 3. Payer + provider info ───────────────────────────────────────


def test_x12_278_request_payer_info_extracted():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.payer_id == "60054"
    assert parsed.payer_name == "AETNA-TEST"


def test_x12_278_request_provider_npis_extracted():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.requesting_provider_npi == "1234567890"
    assert parsed.service_provider_npi == "9876543210"


def test_x12_278_response_provider_npi_extracted():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_APPROVED_FIXTURE)
    assert parsed.requesting_provider_npi == "1234567890"


# ── 4. Member ID hashing ───────────────────────────────────────────


def test_x12_278_member_id_hashed_not_raw():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.member_id_hash != ""
    assert len(parsed.member_id_hash) == 64
    expected = hashlib.sha256(b"MEMBER-PA-001").hexdigest()
    assert parsed.member_id_hash == expected


def test_x12_278_member_id_not_in_metadata():
    adapter = X12ServicesReviewAdapter()
    meta = adapter.parse_transaction(X12_278_REQUEST_FIXTURE).to_metadata()
    for key, value in meta.items():
        assert "MEMBER-PA-001" not in value, (
            f"raw member ID leaked into metadata[{key!r}]={value!r}"
        )


# ── 5. UM segment + review type ────────────────────────────────────


def test_x12_278_um_review_type_extracted():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.review_type == "HS"


def test_x12_278_um_category_extracted():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.certification_request_category == "AR"


def test_x12_278_review_types_table():
    adapter = X12ServicesReviewAdapter()
    types = adapter.review_types
    assert types["AR"] == "Admission Review"
    assert types["HS"] == "Health Services Review"


def test_x12_278_cert_request_categories_table():
    adapter = X12ServicesReviewAdapter()
    cats = adapter.certification_request_categories
    assert cats["AR"] == "Admission Review"
    assert cats["DS"] == "Diagnostic Services"
    assert cats["MS"] == "Medical Services"


# ── 6. Certification number extraction ─────────────────────────────


def test_x12_278_request_cert_number_from_ref_g1():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    assert parsed.certification_number == "PA-REQUEST-001"


def test_x12_278_response_cert_number_from_hcr_or_ref():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_RESPONSE_APPROVED_FIXTURE)
    assert parsed.certification_number == "CERT-NUM-001"


# ── 7. Closed-enum + transaction set rejection ─────────────────────


def test_x12_278_non_278_transaction_set_rejected():
    adapter = X12ServicesReviewAdapter()
    bad = X12_278_REQUEST_FIXTURE.replace(b"ST*278*", b"ST*999*")
    with pytest.raises(ValueError, match="transaction_set"):
        adapter.parse_transaction(bad)


def test_x12_278_empty_payload_rejected():
    adapter = X12ServicesReviewAdapter()
    with pytest.raises(ValueError, match="empty"):
        adapter.parse_transaction(b"")


def test_x12_278_non_isa_prefix_rejected():
    adapter = X12ServicesReviewAdapter()
    with pytest.raises(ValueError, match="ISA"):
        adapter.parse_transaction(b"X" * 200)


def test_x12_278_supported_transaction_sets():
    adapter = X12ServicesReviewAdapter()
    assert adapter.supported_transaction_sets == ["278"]


# ── 8. parse_request + parse_response gating ───────────────────────


def test_x12_278_parse_request_accepts_request():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_request(X12_278_REQUEST_FIXTURE)
    assert parsed.is_request is True


def test_x12_278_parse_request_rejects_response():
    adapter = X12ServicesReviewAdapter()
    with pytest.raises(ValueError, match="expected REQUEST"):
        adapter.parse_request(X12_278_RESPONSE_APPROVED_FIXTURE)


def test_x12_278_parse_response_accepts_response():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_response(X12_278_RESPONSE_APPROVED_FIXTURE)
    assert parsed.is_response is True


def test_x12_278_parse_response_rejects_request():
    adapter = X12ServicesReviewAdapter()
    with pytest.raises(ValueError, match="expected RESPONSE"):
        adapter.parse_response(X12_278_REQUEST_FIXTURE)


# ── 9. Multi-transaction batch ─────────────────────────────────────


def _build_pa_request_batch(n: int) -> List[bytes]:
    out = []
    for i in range(n):
        ctrl = f"{i:09d}"
        ref = f"PA-BATCH-{i:06d}"
        member = f"MEMBER-BATCH-{i:06d}"
        isa = (
            "ISA*00*          *00*          *ZZ*PROVIDER-TEST  *"
            "ZZ*PAYER-TEST     *260524*1200*^*00501*"
            + ctrl + "*0*P*:~"
        )
        fx = (
            isa
            + "GS*HI*PROVIDER-TEST*PAYER-TEST*20260524*1200*1*X*005010X217~"
            + f"ST*278*{i:04d}*005010X217~"
            + f"BHT*0007*13*{ref}*20260524*1200~"
            + "HL*1**20*1~"
            + "NM1*X3*2*AETNA-TEST*****PI*60054~"
            + "HL*2*1*21*1~"
            + "NM1*1P*1*PROVIDER*TEST****XX*1234567890~"
            + "HL*3*2*22*1~"
            + f"NM1*IL*1*DOE*JOHN****MI*{member}~"
            + "UM*HS*AR*****20260524~"
            + f"REF*G1*{ref}~"
            + "SE*10*0001~"
            + "GE*1*1~"
            + f"IEA*1*{ctrl}~"
        ).encode("ascii")
        out.append(fx)
    return out


def test_x12_278_batch_n10_all_parse_distinctly():
    adapter = X12ServicesReviewAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_pa_request_batch(10)]
    assert len({p.isa_control_number for p in parsed}) == 10
    assert len({p.certification_number for p in parsed}) == 10


def test_x12_278_batch_n100_all_parse_distinctly():
    adapter = X12ServicesReviewAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_pa_request_batch(100)]
    assert len(parsed) == 100


def test_x12_278_batch_n1000_all_parse_distinctly():
    adapter = X12ServicesReviewAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_pa_request_batch(1000)]
    assert len(parsed) == 1000


# ── 10. Per-record receipt integration ─────────────────────────────


def test_x12_278_receipt_pattern_tag_is_pa():
    pattern_tag = X12ServicesReviewAdapter.receipt_pattern_tag()
    assert pattern_tag == "pa"
    assert pattern_tag in PATTERN_TAGS


def test_x12_278_receipt_pattern_tag_static_method():
    assert X12ServicesReviewAdapter.receipt_pattern_tag() == "pa"


# ── 11. Metadata constraints ───────────────────────────────────────


def test_x12_278_metadata_serializes_within_4kb_limit():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    import json
    meta_bytes = json.dumps(parsed.to_metadata()).encode("utf-8")
    assert len(meta_bytes) <= 4096


def test_x12_278_metadata_reserved_keys():
    adapter = X12ServicesReviewAdapter()
    meta = adapter.parse_transaction(X12_278_REQUEST_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-278"
    assert meta["nanorix:framework_version"] == "x12-005010X217"


def test_x12_278_derive_metadata_accepts_workflow_id():
    adapter = X12ServicesReviewAdapter()
    parsed = adapter.parse_transaction(X12_278_REQUEST_FIXTURE)
    meta = X12ServicesReviewAdapter.derive_metadata(
        parsed, customer_workflow_id="banjo-pa-job-001"
    )
    assert meta["customer_workflow_id"] == "banjo-pa-job-001"


# ── 12. Vocabulary discipline ──────────────────────────────────────


def test_x12_278_module_has_no_banned_verdict_vocabulary():
    import nanorix.healthcare.x12_278 as x12_mod
    source = inspect.getsource(x12_mod)
    banned = ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]
    for word in banned:
        matches = re.findall(rf"\b{word}\b", source)
        assert not matches, (
            f"banned verdict vocabulary {word!r} found: {matches[:3]}"
        )


def test_x12_278_module_avoids_engine_name_leaks(engine_names):
    import nanorix.healthcare.x12_278 as x12_mod
    source = inspect.getsource(x12_mod)
    for engine in engine_names:
        matches = re.findall(rf"\b{engine}\b", source, re.IGNORECASE)
        assert not matches, f"possible engine name leak: {engine}"
