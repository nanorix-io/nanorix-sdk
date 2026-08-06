"""Tests for nanorix.healthcare.HL7v2Adapter.

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient identifiers are obviously fictitious ("Patient-001", etc.);
no real PHI per Wave 17 batch 6 directive.
"""
from __future__ import annotations

import inspect
import re

import pytest

from nanorix.healthcare import HL7v2Adapter


# Synthetic fixture builders — never real PHI.

ADT_A01_FIXTURE = (
    b"MSH|^~\\&|EpicCare|AcmeHospital|Receiver|ReceivingFacility|"
    b"20260510120000||ADT^A01|MSG00001|P|2.5\r"
    b"EVN|A01|20260510120000\r"
    b"PID|1||Patient-001\r"
    b"PV1|1|I|Ward-A"
)

ADT_A03_FIXTURE = (
    b"MSH|^~\\&|EpicCare|AcmeHospital|Receiver|ReceivingFacility|"
    b"20260510130000||ADT^A03|MSG00002|P|2.5\r"
    b"EVN|A03|20260510130000\r"
    b"PID|1||Patient-002\r"
    b"PV1|1|I|Ward-B"
)

ADT_A08_FIXTURE = (
    b"MSH|^~\\&|EpicCare|AcmeHospital|Receiver|ReceivingFacility|"
    b"20260510140000||ADT^A08|MSG00003|P|2.5\r"
    b"EVN|A08|20260510140000\r"
    b"PID|1||Patient-003"
)

ORU_R01_FIXTURE = (
    b"MSH|^~\\&|LabSystem|AcmeLab|Receiver|ReceivingFacility|"
    b"20260510150000||ORU^R01|MSG00004|P|2.5\r"
    b"PID|1||Patient-004\r"
    b"OBR|1||LAB-001|Z00.00^General Adult Examination\r"
    b"OBX|1|NM|GLUC^Glucose|1|95|mg/dL"
)


# ── Required-field / smoke ─────────────────────────────────────


def test_hl7_adt_a01_parses_to_structural_metadata():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    assert parsed.message_type == "ADT"
    assert parsed.trigger_event == "A01"
    assert parsed.message_control_id == "MSG00001"
    assert parsed.sending_application == "EpicCare"
    assert parsed.receiving_application == "Receiver"
    assert parsed.sending_facility == "AcmeHospital"
    assert parsed.receiving_facility == "ReceivingFacility"
    assert parsed.message_datetime == "20260510120000"
    assert parsed.processing_id == "P"
    assert parsed.version_id == "2.5"


def test_hl7_adt_a01_metadata_includes_reserved_keys():
    adapter = HL7v2Adapter()
    meta = adapter.parse_message(ADT_A01_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "HL7V2"
    assert meta["nanorix:framework_version"] == "hl7-v2.5"


def test_hl7_adt_a03_discharge_parses_correctly():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A03_FIXTURE)
    assert parsed.message_type == "ADT"
    assert parsed.trigger_event == "A03"


def test_hl7_adt_a08_update_parses_correctly():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A08_FIXTURE)
    assert parsed.message_type == "ADT"
    assert parsed.trigger_event == "A08"


def test_hl7_oru_r01_parses_correctly():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ORU_R01_FIXTURE)
    assert parsed.message_type == "ORU"
    assert parsed.trigger_event == "R01"
    assert parsed.sending_application == "LabSystem"


def test_hl7_msh_segment_hash_is_deterministic_sha256():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    # SHA-256 hex = 64 chars.
    assert len(parsed.msh_segment_hash) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", parsed.msh_segment_hash)
    # Repeat parse → same hash.
    parsed2 = adapter.parse_message(ADT_A01_FIXTURE)
    assert parsed.msh_segment_hash == parsed2.msh_segment_hash


def test_hl7_distinct_messages_produce_distinct_hashes():
    adapter = HL7v2Adapter()
    p1 = adapter.parse_message(ADT_A01_FIXTURE)
    p2 = adapter.parse_message(ADT_A03_FIXTURE)
    assert p1.msh_segment_hash != p2.msh_segment_hash


# ── Segment-types observation ──────────────────────────────────


def test_hl7_segment_types_observed_for_adt_a01():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    assert "MSH" in parsed.segment_types_observed
    assert "EVN" in parsed.segment_types_observed
    assert "PID" in parsed.segment_types_observed
    assert "PV1" in parsed.segment_types_observed


def test_hl7_segment_types_observed_for_oru():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ORU_R01_FIXTURE)
    assert "OBR" in parsed.segment_types_observed
    assert "OBX" in parsed.segment_types_observed


def test_hl7_segment_types_observed_serializes_in_metadata():
    adapter = HL7v2Adapter()
    meta = adapter.parse_message(ADT_A01_FIXTURE).to_metadata()
    assert "segment_types_observed" in meta
    parts = meta["segment_types_observed"].split(",")
    assert "MSH" in parts
    assert "PID" in parts


# ── Message-type-with-trigger composite identifier ─────────────


def test_hl7_message_type_with_trigger_property():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    assert parsed.message_type_with_trigger == "ADT^A01"


# ── Convenience parse methods (parse_adt / parse_oru) ──────────


def test_hl7_parse_adt_accepts_adt_message():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_adt(ADT_A01_FIXTURE)
    assert parsed.message_type == "ADT"


def test_hl7_parse_adt_rejects_oru_message():
    adapter = HL7v2Adapter()
    with pytest.raises(ValueError, match="expected ADT"):
        adapter.parse_adt(ORU_R01_FIXTURE)


def test_hl7_parse_oru_accepts_oru_message():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_oru(ORU_R01_FIXTURE)
    assert parsed.message_type == "ORU"


def test_hl7_parse_oru_rejects_adt_message():
    adapter = HL7v2Adapter()
    with pytest.raises(ValueError, match="expected ORU"):
        adapter.parse_oru(ADT_A01_FIXTURE)


# ── Error handling ─────────────────────────────────────────────


def test_hl7_parse_message_rejects_non_bytes():
    adapter = HL7v2Adapter()
    with pytest.raises(ValueError, match="expects bytes"):
        adapter.parse_message("not bytes")  # type: ignore


def test_hl7_parse_message_rejects_empty_bytes():
    adapter = HL7v2Adapter()
    with pytest.raises(ValueError, match="empty"):
        adapter.parse_message(b"")


def test_hl7_parse_message_rejects_missing_msh():
    adapter = HL7v2Adapter()
    with pytest.raises(ValueError, match="first segment is not MSH"):
        adapter.parse_message(b"PID|1||X")


def test_hl7_parse_message_rejects_unknown_message_type():
    adapter = HL7v2Adapter()
    bad = (
        b"MSH|^~\\&|App|Fac|Rec|RecFac|20260510120000||"
        b"SIU^S12|MSG|P|2.5"
    )
    with pytest.raises(
        ValueError, match=r"message_type=.*not in"
    ):
        adapter.parse_message(bad)


def test_hl7_parse_message_rejects_empty_msh9():
    adapter = HL7v2Adapter()
    bad = (
        b"MSH|^~\\&|App|Fac|Rec|RecFac|20260510120000|||"
        b"MSG|P|2.5"
    )
    with pytest.raises(
        ValueError, match=r"MSH-9.*message type.*is empty"
    ):
        adapter.parse_message(bad)


def test_hl7_strict_mode_rejects_unknown_adt_trigger():
    adapter = HL7v2Adapter(strict=True)
    bad = (
        b"MSH|^~\\&|App|Fac|Rec|RecFac|20260510120000||"
        b"ADT^A99|MSG|P|2.5"
    )
    with pytest.raises(ValueError, match=r"strict.*trigger_event"):
        adapter.parse_message(bad)


def test_hl7_strict_mode_rejects_unknown_oru_trigger():
    adapter = HL7v2Adapter(strict=True)
    bad = (
        b"MSH|^~\\&|App|Fac|Rec|RecFac|20260510120000||"
        b"ORU^R99|MSG|P|2.5"
    )
    with pytest.raises(ValueError, match=r"strict.*trigger_event"):
        adapter.parse_message(bad)


def test_hl7_non_strict_accepts_unknown_adt_trigger():
    """Non-strict mode is best-effort — accepts unknown triggers."""
    adapter = HL7v2Adapter(strict=False)
    bad = (
        b"MSH|^~\\&|App|Fac|Rec|RecFac|20260510120000||"
        b"ADT^A99|MSG|P|2.5"
    )
    parsed = adapter.parse_message(bad)
    assert parsed.message_type == "ADT"
    assert parsed.trigger_event == "A99"


# ── Line-ending normalization ──────────────────────────────────


def test_hl7_accepts_crlf_line_endings():
    adapter = HL7v2Adapter()
    msg = ADT_A01_FIXTURE.replace(b"\r", b"\r\n")
    parsed = adapter.parse_message(msg)
    assert parsed.message_type == "ADT"


def test_hl7_accepts_lf_line_endings():
    adapter = HL7v2Adapter()
    msg = ADT_A01_FIXTURE.replace(b"\r", b"\n")
    parsed = adapter.parse_message(msg)
    assert parsed.message_type == "ADT"


# ── Adapter introspection properties ───────────────────────────


def test_hl7_supported_message_types_returns_canonical_list():
    adapter = HL7v2Adapter()
    types = adapter.supported_message_types
    assert "ADT" in types
    assert "ORU" in types


def test_hl7_supported_adt_triggers_contains_a01_a03_a08():
    adapter = HL7v2Adapter()
    triggers = adapter.supported_adt_triggers
    assert "A01" in triggers
    assert "A03" in triggers
    assert "A08" in triggers


def test_hl7_supported_oru_triggers_contains_r01():
    adapter = HL7v2Adapter()
    assert "R01" in adapter.supported_oru_triggers


def test_hl7_strict_mode_property_reflects_constructor():
    assert HL7v2Adapter(strict=True).strict_mode is True
    assert HL7v2Adapter(strict=False).strict_mode is False


# ── derive_metadata convenience ────────────────────────────────


def test_hl7_derive_metadata_with_workflow_id():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    meta = HL7v2Adapter.derive_metadata(
        parsed, customer_workflow_id="workflow-12345"
    )
    assert meta["customer_workflow_id"] == "workflow-12345"
    assert meta["message_type"] == "ADT"


def test_hl7_derive_metadata_without_workflow_id_matches_to_metadata():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    meta_a = HL7v2Adapter.derive_metadata(parsed)
    meta_b = parsed.to_metadata()
    assert meta_a == meta_b


# ── Wire-constraint enforcement ────────────────────────────────


def test_hl7_metadata_keys_under_16_max():
    """ADR-010 A4 wire constraint: max 16 metadata keys."""
    adapter = HL7v2Adapter()
    meta = adapter.parse_message(ADT_A01_FIXTURE).to_metadata()
    assert len(meta) <= 16


def test_hl7_metadata_serialized_size_under_4kb():
    """ADR-010 A4 wire constraint: max 4096 bytes serialized."""
    import json
    adapter = HL7v2Adapter()
    meta = adapter.parse_message(ADT_A01_FIXTURE).to_metadata()
    assert len(json.dumps(meta)) <= 4096


# ── Three-role discipline / vocabulary discipline ──────────────


def test_hl7_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.healthcare import hl7_v2 as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


def test_hl7_docstring_surfaces_three_role_discipline():
    """Class-level docstring must surface ADR-017 discipline."""
    doc = HL7v2Adapter.__doc__ or ""
    assert "structural" in doc.lower()
    assert "customer" in doc.lower()


def test_hl7_module_docstring_surfaces_clinical_non_interpretation():
    """Module-level docstring must clarify the adapter does NOT
    interpret clinical content."""
    from nanorix.healthcare import hl7_v2 as mod
    doc = mod.__doc__ or ""
    assert "does not" in doc.lower() or "does NOT" in doc
    assert "clinical" in doc.lower()


# ── Round-trip stability ───────────────────────────────────────


def test_hl7_round_trip_preserves_routing_identifiers():
    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(ADT_A01_FIXTURE)
    meta = parsed.to_metadata()
    # Routing identifiers preserved verbatim.
    assert meta["sending_application"] == "EpicCare"
    assert meta["sending_facility"] == "AcmeHospital"
    assert meta["receiving_application"] == "Receiver"
    assert meta["message_control_id"] == "MSG00001"
