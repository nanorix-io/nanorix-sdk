"""Tests for nanorix.healthcare.X12FullRCMAdapter (Batch 2 sub-item 4).

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Member IDs, NPIs, payer IDs, claim numbers, and dollar amounts are
obviously fictitious. No real PHI even in fixtures.

Coverage:
- 837P (Professional) claim parse
- 837I (Institutional) claim parse
- 837D (Dental) claim parse
- 835 remittance parse + claim status distribution
- 834 enrollment parse + maintenance type distribution
- 837 flavor detection via GS08 implementation-guide reference
- Member ID hashing (never raw)
- Provider NPI extraction
- Claim count + amount summing (cents to avoid float drift)
- Multi-transaction batch + per-record receipt emission
- Vocabulary discipline scan
- Engine-name leak guard
- Closed-enum rejection
- Convenience properties (is_claim/is_remittance/is_enrollment/has_denied_claims)
"""
from __future__ import annotations

import hashlib
import inspect
import re
from typing import List

import pytest

from nanorix.capsule_record import PATTERN_TAGS
from nanorix.healthcare import X12FullRCMAdapter


# ── Synthetic fixtures ─────────────────────────────────────────────


# 837P (Professional) - simple single-claim
X12_837P_FIXTURE = (
    "ISA*00*          *00*          *ZZ*PROVIDER-TEST  *"
    "ZZ*PAYER-TEST     *260524*1200*^*00501*000000001*0*P*:~"
    "GS*HC*PROVIDER-TEST*PAYER-TEST*20260524*1200*1*X*005010X222A1~"
    "ST*837*0001*005010X222A1~"
    "BHT*0019*00*REF-CLAIM-001*20260524*1200*CH~"
    "NM1*41*2*PROVIDER-TEST*****46*PROVIDERID~"
    "PER*IC*CONTACT*TE*5551234~"
    "NM1*40*2*PAYER-TEST*****46*PAYERID~"
    "HL*1**20*1~"
    "NM1*85*2*PROVIDER-TEST*****XX*1234567890~"
    "HL*2*1*22*0~"
    "SBR*P*18*****CI~"
    "NM1*IL*1*DOE*JOHN****MI*MEMBER-CLAIM-001~"
    "NM1*PR*2*AETNA-TEST*****PI*60054~"
    "CLM*CLAIM-001*150.00***11:B:1*Y*A*Y*Y~"
    "DTP*431*D8*20260524~"
    "REF*D9*REF-CLAIM-001~"
    "HI*ABK:Z000~"
    "LX*1~"
    "SV1*HC:99213*150.00*UN*1***1~"
    "DTP*472*D8*20260524~"
    "SE*20*0001~"
    "GE*1*1~"
    "IEA*1*000000001~"
).encode("ascii")


# 837I (Institutional)
X12_837I_FIXTURE = (
    "ISA*00*          *00*          *ZZ*HOSPITAL-TEST  *"
    "ZZ*PAYER-TEST     *260524*1200*^*00501*000000002*0*P*:~"
    "GS*HC*HOSPITAL-TEST*PAYER-TEST*20260524*1200*2*X*005010X223A2~"
    "ST*837*0002*005010X223A2~"
    "BHT*0019*00*REF-INST-001*20260524*1200*CH~"
    "NM1*41*2*HOSPITAL-TEST*****46*HOSPID~"
    "PER*IC*CONTACT*TE*5551234~"
    "NM1*40*2*PAYER-TEST*****46*PAYERID~"
    "HL*1**20*1~"
    "NM1*85*2*HOSPITAL-TEST*****XX*2345678901~"
    "HL*2*1*22*0~"
    "SBR*P*18*****CI~"
    "NM1*IL*1*DOE*JOHN****MI*MEMBER-INST-001~"
    "NM1*PR*2*AETNA-TEST*****PI*60054~"
    "CLM*INST-CLAIM-001*5000.00***81:A:1*Y*A*Y*Y~"
    "DTP*434*RD8*20260520-20260524~"
    "HI*ABK:I10~"
    "SE*15*0002~"
    "GE*1*2~"
    "IEA*1*000000002~"
).encode("ascii")


# 837D (Dental)
X12_837D_FIXTURE = (
    "ISA*00*          *00*          *ZZ*DENTAL-TEST    *"
    "ZZ*PAYER-TEST     *260524*1200*^*00501*000000003*0*P*:~"
    "GS*HC*DENTAL-TEST*PAYER-TEST*20260524*1200*3*X*005010X224A2~"
    "ST*837*0003*005010X224A2~"
    "BHT*0019*00*REF-DENTAL-001*20260524*1200*CH~"
    "NM1*41*2*DENTAL-TEST*****46*DENTALID~"
    "PER*IC*CONTACT*TE*5551234~"
    "NM1*40*2*PAYER-TEST*****46*PAYERID~"
    "HL*1**20*1~"
    "NM1*85*2*DENTAL-TEST*****XX*3456789012~"
    "HL*2*1*22*0~"
    "SBR*P*18*****CI~"
    "NM1*IL*1*DOE*JANE****MI*MEMBER-DENTAL-001~"
    "NM1*PR*2*DENTAL-PAYER*****PI*99999~"
    "CLM*DENTAL-001*350.00***11:B:1*Y*A*Y*Y~"
    "DTP*472*D8*20260524~"
    "SE*15*0003~"
    "GE*1*3~"
    "IEA*1*000000003~"
).encode("ascii")


# 835 Remittance with mixed claim statuses (processed primary + denied)
X12_835_FIXTURE = (
    "ISA*00*          *00*          *ZZ*PAYER-TEST     *"
    "ZZ*PROVIDER-TEST  *260524*1300*^*00501*000000004*0*P*:~"
    "GS*HP*PAYER-TEST*PROVIDER-TEST*20260524*1300*4*X*005010X221A1~"
    "ST*835*0004~"
    "BPR*I*1500.00*C*ACH*CCP*01*111000025*DA*999999*1234567890**01*222000099*"
    "DA*111111*20260524~"
    "TRN*1*TRACE-001*1999999999~"
    "N1*PR*PAYER-TEST*****PAYERID~"
    "N1*PE*PROVIDER-TEST*XX*1234567890~"
    "LX*1~"
    "CLP*CLAIM-001*1*150.00*120.00*30.00*MC*PAYER-CLAIM-001*11~"
    "NM1*QC*1*DOE*JOHN****MI*MEMBER-001~"
    "LX*2~"
    "CLP*CLAIM-002*4*200.00*0.00*0.00*MC*PAYER-CLAIM-002*11~"
    "NM1*QC*1*DOE*JANE****MI*MEMBER-002~"
    "LX*3~"
    "CLP*CLAIM-003*1*1500.00*1380.00*120.00*MC*PAYER-CLAIM-003*11~"
    "NM1*QC*1*SMITH*BOB****MI*MEMBER-003~"
    "SE*15*0004~"
    "GE*1*4~"
    "IEA*1*000000004~"
).encode("ascii")


# 834 Enrollment with multiple maintenance types (addition, change, term)
X12_834_FIXTURE = (
    "ISA*00*          *00*          *ZZ*EMPLOYER-TEST  *"
    "ZZ*PAYER-TEST     *260524*1100*^*00501*000000005*0*P*:~"
    "GS*BE*EMPLOYER-TEST*PAYER-TEST*20260524*1100*5*X*005010X220A1~"
    "ST*834*0005*005010X220A1~"
    "BGN*00*REF-ENR-001*20260524*1100~"
    "N1*P5*EMPLOYER-TEST*****EMPLOYERID~"
    "N1*IN*PAYER-TEST*****PAYERID~"
    "INS*Y*18*021*20*A***FT~"
    "REF*0F*MEMBER-ENROLL-001~"
    "NM1*IL*1*DOE*JOHN****34*MEMBER-ENROLL-001~"
    "INS*Y*18*024*20*A***FT~"
    "REF*0F*MEMBER-ENROLL-002~"
    "NM1*IL*1*DOE*JANE****34*MEMBER-ENROLL-002~"
    "INS*Y*18*001*20*A***FT~"
    "REF*0F*MEMBER-ENROLL-003~"
    "NM1*IL*1*SMITH*BOB****34*MEMBER-ENROLL-003~"
    "SE*15*0005~"
    "GE*1*5~"
    "IEA*1*000000005~"
).encode("ascii")


# ── 1. 837 flavor detection ────────────────────────────────────────


def test_x12_837p_flavor_detected():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    assert parsed.transaction_set == "837"
    assert parsed.flavor == "P"
    assert parsed.framework_version == "x12-005010X222A1"


def test_x12_837i_flavor_detected():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837I_FIXTURE)
    assert parsed.transaction_set == "837"
    assert parsed.flavor == "I"
    assert parsed.framework_version == "x12-005010X223A2"


def test_x12_837d_flavor_detected():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837D_FIXTURE)
    assert parsed.transaction_set == "837"
    assert parsed.flavor == "D"
    assert parsed.framework_version == "x12-005010X224A2"


def test_x12_supported_837_flavors():
    adapter = X12FullRCMAdapter()
    assert adapter.supported_837_flavors == ["D", "I", "P"]


# ── 2. 835 remittance parse ────────────────────────────────────────


def test_x12_835_parses_to_structural_metadata():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.transaction_set == "835"
    assert parsed.flavor == ""
    assert parsed.framework_version == "x12-005010X221A1"
    assert parsed.is_remittance is True
    assert parsed.is_claim is False
    assert parsed.is_enrollment is False


def test_x12_835_claim_count_correct():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.claim_count == 3


def test_x12_835_claim_status_distribution():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    # CLP02 codes: 1 (primary), 4 (denied), 1 (primary).
    # Distinct: {"1", "4"}.
    codes = set(parsed.claim_status_distribution.split(","))
    assert codes == {"1", "4"}


def test_x12_835_has_denied_claims_flag():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.has_denied_claims is True


def test_x12_835_total_payment_amount_in_cents():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    # CLP04 = 120.00 + 0.00 + 1380.00 = 1500.00 → 150000 cents
    assert parsed.total_payment_amount_cents == 150000


def test_x12_835_no_charge_amount():
    """835 has no CLM segments → total_charge_amount_cents is 0."""
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.total_charge_amount_cents == 0


# ── 3. 834 enrollment parse ────────────────────────────────────────


def test_x12_834_parses_to_structural_metadata():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_834_FIXTURE)
    assert parsed.transaction_set == "834"
    assert parsed.is_enrollment is True
    assert parsed.is_remittance is False
    assert parsed.is_claim is False


def test_x12_834_enrollment_record_count_correct():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_834_FIXTURE)
    assert parsed.claim_count == 3  # Three INS segments


def test_x12_834_maintenance_type_distribution():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_834_FIXTURE)
    # INS03 codes: 021 (addition), 024 (term), 001 (change).
    codes = set(parsed.maintenance_type_distribution.split(","))
    assert codes == {"021", "024", "001"}


def test_x12_834_maintenance_codes_table():
    adapter = X12FullRCMAdapter()
    codes = adapter.maintenance_codes
    assert codes["001"] == "Change"
    assert codes["021"] == "Addition"
    assert codes["024"] == "Cancel/Termination"


# ── 4. 837 claim count + charge amount ─────────────────────────────


def test_x12_837p_claim_count():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    assert parsed.claim_count == 1


def test_x12_837p_total_charge_in_cents():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    # CLM02 = 150.00 → 15000 cents
    assert parsed.total_charge_amount_cents == 15000


def test_x12_837i_total_charge_in_cents():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837I_FIXTURE)
    # CLM02 = 5000.00 → 500000 cents
    assert parsed.total_charge_amount_cents == 500000


def test_x12_837d_total_charge_in_cents():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837D_FIXTURE)
    # CLM02 = 350.00 → 35000 cents
    assert parsed.total_charge_amount_cents == 35000


# ── 5. Provider + payer + member info ──────────────────────────────


def test_x12_837p_provider_npi_extracted():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    assert parsed.provider_npi == "1234567890"


def test_x12_837p_payer_info_extracted():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    assert parsed.payer_id == "60054"
    assert parsed.payer_name == "AETNA-TEST"


def test_x12_837p_member_id_hashed():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    expected = hashlib.sha256(b"MEMBER-CLAIM-001").hexdigest()
    assert parsed.member_id_hash == expected


def test_x12_837p_member_id_not_in_metadata():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_837P_FIXTURE).to_metadata()
    for key, value in meta.items():
        assert "MEMBER-CLAIM-001" not in value


def test_x12_835_payer_extracted_via_n1():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.payer_name == "PAYER-TEST"


def test_x12_835_provider_extracted_via_n1_pe():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_835_FIXTURE)
    assert parsed.provider_npi == "1234567890"


# ── 6. parse_834/835/837 transaction-set gating ────────────────────


def test_x12_parse_834_accepts_834():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_834(X12_834_FIXTURE)
    assert parsed.transaction_set == "834"


def test_x12_parse_834_rejects_other_transaction_set():
    adapter = X12FullRCMAdapter()
    with pytest.raises(ValueError, match="expected 834"):
        adapter.parse_834(X12_835_FIXTURE)


def test_x12_parse_835_accepts_835():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_835(X12_835_FIXTURE)
    assert parsed.transaction_set == "835"


def test_x12_parse_835_rejects_other_transaction_set():
    adapter = X12FullRCMAdapter()
    with pytest.raises(ValueError, match="expected 835"):
        adapter.parse_835(X12_837P_FIXTURE)


def test_x12_parse_837_accepts_837():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_837(X12_837P_FIXTURE)
    assert parsed.transaction_set == "837"


def test_x12_parse_837_rejects_other_transaction_set():
    adapter = X12FullRCMAdapter()
    with pytest.raises(ValueError, match="expected 837"):
        adapter.parse_837(X12_834_FIXTURE)


# ── 7. Receipt pattern_tag mapping ─────────────────────────────────


def test_x12_837_receipt_pattern_tag_is_rcm_claim():
    pt = X12FullRCMAdapter.receipt_pattern_tag("837")
    assert pt == "rcm_claim"
    assert pt in PATTERN_TAGS


def test_x12_835_receipt_pattern_tag_is_rcm_remit():
    pt = X12FullRCMAdapter.receipt_pattern_tag("835")
    assert pt == "rcm_remit"
    assert pt in PATTERN_TAGS


def test_x12_834_receipt_pattern_tag_is_rcm_claim():
    """834 reuses rcm_claim slot for enrollment-class records."""
    pt = X12FullRCMAdapter.receipt_pattern_tag("834")
    assert pt == "rcm_claim"
    assert pt in PATTERN_TAGS


def test_x12_unsupported_receipt_pattern_tag_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        X12FullRCMAdapter.receipt_pattern_tag("270")


# ── 8. Metadata constraints + framework labels ─────────────────────


def test_x12_837p_metadata_framework_label():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_837P_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-837P"
    assert meta["nanorix:framework_version"] == "x12-005010X222A1"


def test_x12_837i_metadata_framework_label():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_837I_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-837I"


def test_x12_837d_metadata_framework_label():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_837D_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-837D"


def test_x12_835_metadata_framework_label():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_835_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-835"
    assert meta["nanorix:framework_version"] == "x12-005010X221A1"


def test_x12_834_metadata_framework_label():
    adapter = X12FullRCMAdapter()
    meta = adapter.parse_transaction(X12_834_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "X12-834"


def test_x12_metadata_serializes_within_4kb_limit():
    adapter = X12FullRCMAdapter()
    for fixture in (X12_837P_FIXTURE, X12_835_FIXTURE, X12_834_FIXTURE):
        parsed = adapter.parse_transaction(fixture)
        import json
        meta_bytes = json.dumps(parsed.to_metadata()).encode("utf-8")
        assert len(meta_bytes) <= 4096


def test_x12_metadata_within_16_keys():
    adapter = X12FullRCMAdapter()
    for fixture in (X12_837P_FIXTURE, X12_835_FIXTURE, X12_834_FIXTURE):
        parsed = adapter.parse_transaction(fixture)
        assert len(parsed.to_metadata()) <= 16


def test_x12_derive_metadata_accepts_workflow_id():
    adapter = X12FullRCMAdapter()
    parsed = adapter.parse_transaction(X12_837P_FIXTURE)
    meta = X12FullRCMAdapter.derive_metadata(
        parsed, customer_workflow_id="nym-rcm-job-001"
    )
    assert meta["customer_workflow_id"] == "nym-rcm-job-001"


# ── 9. Multi-batch ─────────────────────────────────────────────────


def _build_837p_batch(n: int) -> List[bytes]:
    out = []
    for i in range(n):
        ctrl = f"{i:09d}"
        member = f"MEMBER-BATCH-{i:06d}"
        claim_id = f"CLAIM-BATCH-{i:06d}"
        fx = (
            "ISA*00*          *00*          *ZZ*PROVIDER-TEST  *"
            "ZZ*PAYER-TEST     *260524*1200*^*00501*"
            + ctrl
            + "*0*P*:~"
            "GS*HC*PROVIDER-TEST*PAYER-TEST*20260524*1200*1*X*005010X222A1~"
            + f"ST*837*{i:04d}*005010X222A1~"
            + f"BHT*0019*00*REF-{i:06d}*20260524*1200*CH~"
            + "HL*1**20*1~"
            + "NM1*85*2*PROVIDER-TEST*****XX*1234567890~"
            + "HL*2*1*22*0~"
            + f"NM1*IL*1*DOE*JOHN****MI*{member}~"
            + "NM1*PR*2*AETNA-TEST*****PI*60054~"
            + f"CLM*{claim_id}*100.00***11:B:1*Y*A*Y*Y~"
            + "SE*10*0001~"
            + "GE*1*1~"
            + f"IEA*1*{ctrl}~"
        ).encode("ascii")
        out.append(fx)
    return out


def test_x12_837p_batch_n10_all_parse_distinctly():
    adapter = X12FullRCMAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_837p_batch(10)]
    assert len({p.isa_control_number for p in parsed}) == 10
    assert len({p.payload_hash for p in parsed}) == 10
    assert all(p.claim_count == 1 for p in parsed)


def test_x12_837p_batch_n100():
    adapter = X12FullRCMAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_837p_batch(100)]
    assert len(parsed) == 100
    # Each claim is $100.00 → 10000 cents per record → 1,000,000 total
    total_charge = sum(p.total_charge_amount_cents for p in parsed)
    assert total_charge == 1_000_000


def test_x12_837p_batch_n1000():
    adapter = X12FullRCMAdapter()
    parsed = [adapter.parse_transaction(p) for p in _build_837p_batch(1000)]
    assert len(parsed) == 1000


# ── 10. Closed-enum + ISA + format rejection ───────────────────────


def test_x12_full_rcm_unsupported_transaction_set_rejected():
    adapter = X12FullRCMAdapter()
    bad = X12_837P_FIXTURE.replace(b"ST*837*", b"ST*999*")
    with pytest.raises(ValueError, match="transaction_set"):
        adapter.parse_transaction(bad)


def test_x12_full_rcm_empty_payload_rejected():
    adapter = X12FullRCMAdapter()
    with pytest.raises(ValueError, match="empty"):
        adapter.parse_transaction(b"")


def test_x12_full_rcm_non_isa_prefix_rejected():
    adapter = X12FullRCMAdapter()
    with pytest.raises(ValueError, match="ISA"):
        adapter.parse_transaction(b"X" * 200)


def test_x12_full_rcm_supported_transaction_sets():
    adapter = X12FullRCMAdapter()
    assert adapter.supported_transaction_sets == ["834", "835", "837"]


# ── 11. Vocabulary discipline ──────────────────────────────────────


def test_x12_full_rcm_has_no_banned_verdict_vocabulary():
    import nanorix.healthcare.x12_full_rcm as x12_mod
    source = inspect.getsource(x12_mod)
    banned = ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]
    for word in banned:
        matches = re.findall(rf"\b{word}\b", source)
        assert not matches, (
            f"banned verdict vocabulary {word!r} found: {matches[:3]}"
        )


def test_x12_full_rcm_avoids_engine_name_leaks():
    import nanorix.healthcare.x12_full_rcm as x12_mod
    source = inspect.getsource(x12_mod)
    engine_names = ["AIPPF", "FGI", "SIS", "MCP", "ACE", "ECM", "ZILCHNET"]
    for engine in engine_names:
        matches = re.findall(rf"\b{engine}\b", source, re.IGNORECASE)
        assert not matches, f"possible engine name leak: {engine}"


# ── 12. Claim status codes table ───────────────────────────────────


def test_x12_835_claim_status_codes_table():
    adapter = X12FullRCMAdapter()
    codes = adapter.claim_status_codes
    assert codes["1"] == "Processed as Primary"
    assert codes["4"] == "Denied"
    assert codes["22"] == "Reversal of Previous Payment"


# ── 13. Edge cases ─────────────────────────────────────────────────


def test_x12_837p_amount_rounding_in_cents():
    """Verify CLM02 amount handling with fractional cents."""
    adapter = X12FullRCMAdapter()
    fx = (
        "ISA*00*          *00*          *ZZ*PROVIDER-TEST  *"
        "ZZ*PAYER-TEST     *260524*1200*^*00501*000000099*0*P*:~"
        "GS*HC*PROVIDER-TEST*PAYER-TEST*20260524*1200*1*X*005010X222A1~"
        "ST*837*0099*005010X222A1~"
        "HL*1**20*1~"
        "NM1*85*2*PROVIDER-TEST*****XX*1234567890~"
        "HL*2*1*22*0~"
        "NM1*IL*1*DOE*JOHN****MI*M-001~"
        "NM1*PR*2*AETNA*****PI*60054~"
        "CLM*C-001*123.45***11:B:1*Y*A*Y*Y~"
        "SE*8*0099~"
        "GE*1*1~"
        "IEA*1*000000099~"
    ).encode("ascii")
    parsed = adapter.parse_transaction(fx)
    # 123.45 → 12345 cents
    assert parsed.total_charge_amount_cents == 12345


def test_x12_835_no_denied_claims_returns_false():
    """835 with all CLP02=1 has no denied claims."""
    adapter = X12FullRCMAdapter()
    fx = (
        "ISA*00*          *00*          *ZZ*PAYER-TEST     *"
        "ZZ*PROVIDER-TEST  *260524*1300*^*00501*000000098*0*P*:~"
        "GS*HP*PAYER-TEST*PROVIDER-TEST*20260524*1300*1*X*005010X221A1~"
        "ST*835*0098~"
        "BPR*I*100*C*ACH*CCP*01*1*DA*1*1*1*01*1*DA*1*20260524~"
        "N1*PR*PAYER-TEST*****PAYERID~"
        "N1*PE*PROVIDER-TEST*XX*1234567890~"
        "CLP*C-001*1*100.00*100.00*0.00*MC*P-001*11~"
        "SE*6*0098~"
        "GE*1*1~"
        "IEA*1*000000098~"
    ).encode("ascii")
    parsed = adapter.parse_transaction(fx)
    assert parsed.has_denied_claims is False
