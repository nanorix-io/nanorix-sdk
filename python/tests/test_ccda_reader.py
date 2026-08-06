"""Tests for nanorix.healthcare.CCDAReader.

PHI/PII discipline: all test fixtures use SYNTHETIC FAKE DATA.
Patient identifiers ("Patient-001"), DOB ("2000-01-01"),
DX ("Z00.00 General Adult Examination") are obviously fictitious;
no real PHI per Wave 17 batch 6 directive.
"""
from __future__ import annotations

import base64
import inspect
import re

import pytest

from nanorix.healthcare import CCDAReader


# Synthetic fixture builders — never real PHI.

CCD_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <id extension="DOC-001" root="2.16.840.1.113883.19.5"/>
  <code code="34133-9" displayName="Continuity of Care Document"/>
  <effectiveTime value="20260510120000"/>
  <recordTarget>
    <patientRole>
      <id extension="Patient-001"/>
    </patientRole>
  </recordTarget>
  <author>
    <assignedAuthor>
      <representedOrganization>
        <name>Acme Hospital</name>
      </representedOrganization>
    </assignedAuthor>
  </author>
  <custodian>
    <assignedCustodian>
      <representedCustodianOrganization>
        <name>Acme Hospital Records</name>
      </representedCustodianOrganization>
    </assignedCustodian>
  </custodian>
  <component>
    <structuredBody>
      <component>
        <section>
          <templateId root="2.16.840.1.113883.10.20.22.2.1.1"/>
          <code code="10160-0"/>
        </section>
      </component>
      <component>
        <section>
          <templateId root="2.16.840.1.113883.10.20.22.2.5.1"/>
          <code code="11450-4"/>
        </section>
      </component>
    </structuredBody>
  </component>
</ClinicalDocument>"""

DISCHARGE_SUMMARY_FIXTURE = b"""<?xml version="1.0"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <id extension="DOC-002"/>
  <code code="18842-5" displayName="Discharge Summary"/>
  <effectiveTime value="20260510130000"/>
  <recordTarget>
    <patientRole>
      <id extension="Patient-002"/>
    </patientRole>
  </recordTarget>
  <author>
    <assignedAuthor>
      <representedOrganization>
        <name>Acme Hospital</name>
      </representedOrganization>
    </assignedAuthor>
  </author>
  <custodian>
    <assignedCustodian>
      <representedCustodianOrganization>
        <name>Acme Records</name>
      </representedCustodianOrganization>
    </assignedCustodian>
  </custodian>
</ClinicalDocument>"""

PROGRESS_NOTE_FIXTURE = b"""<?xml version="1.0"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <id extension="DOC-003"/>
  <code code="11506-3" displayName="Progress Note"/>
  <effectiveTime value="20260510140000"/>
  <recordTarget>
    <patientRole>
      <id extension="Patient-003"/>
    </patientRole>
  </recordTarget>
  <author>
    <assignedAuthor>
      <representedOrganization>
        <name>Clinic-Org</name>
      </representedOrganization>
    </assignedAuthor>
  </author>
  <custodian>
    <assignedCustodian>
      <representedCustodianOrganization>
        <name>Clinic-Org</name>
      </representedCustodianOrganization>
    </assignedCustodian>
  </custodian>
</ClinicalDocument>"""


# ── Required-field / smoke ─────────────────────────────────────


def test_ccda_ccd_parses_header_fields():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    assert doc.document_type_code == "34133-9"
    assert doc.document_type_display == "Continuity of Care Document"
    assert doc.document_id == "DOC-001"
    assert doc.document_datetime == "20260510120000"
    assert doc.patient_id_reference == "Patient-001"
    assert doc.author_organization == "Acme Hospital"
    assert doc.custodian_organization == "Acme Hospital Records"


def test_ccda_ccd_metadata_includes_reserved_keys():
    reader = CCDAReader()
    meta = reader.read(CCD_FIXTURE).to_metadata()
    assert meta["nanorix:framework"] == "CCDA"
    assert meta["nanorix:framework_version"] == "ccda-r2.1"


def test_ccda_discharge_summary_parses_correctly():
    reader = CCDAReader()
    doc = reader.read(DISCHARGE_SUMMARY_FIXTURE)
    assert doc.document_type_code == "18842-5"
    assert doc.is_discharge_summary is True
    assert doc.is_continuity_of_care is False


def test_ccda_progress_note_parses_correctly():
    reader = CCDAReader()
    doc = reader.read(PROGRESS_NOTE_FIXTURE)
    assert doc.is_progress_note is True
    assert doc.is_continuity_of_care is False


# ── Document-type predicates ───────────────────────────────────


def test_ccda_ccd_predicate_is_continuity_of_care_true():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    assert doc.is_continuity_of_care is True


def test_ccda_ccd_predicate_is_discharge_summary_false():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    assert doc.is_discharge_summary is False


# ── SHA-256 anchoring ──────────────────────────────────────────


def test_ccda_document_sha256_is_deterministic():
    reader = CCDAReader()
    doc1 = reader.read(CCD_FIXTURE)
    doc2 = reader.read(CCD_FIXTURE)
    assert doc1.document_sha256 == doc2.document_sha256
    assert len(doc1.document_sha256) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", doc1.document_sha256)


def test_ccda_distinct_documents_produce_distinct_hashes():
    reader = CCDAReader()
    d1 = reader.read(CCD_FIXTURE)
    d2 = reader.read(DISCHARGE_SUMMARY_FIXTURE)
    assert d1.document_sha256 != d2.document_sha256


# ── Section structure observation ──────────────────────────────


def test_ccda_section_template_ids_observed():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    assert "2.16.840.1.113883.10.20.22.2.1.1" in doc.section_template_ids
    assert "2.16.840.1.113883.10.20.22.2.5.1" in doc.section_template_ids


def test_ccda_section_codes_observed():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    assert "10160-0" in doc.section_codes
    assert "11450-4" in doc.section_codes


def test_ccda_section_codes_emitted_in_metadata():
    reader = CCDAReader()
    meta = reader.read(CCD_FIXTURE).to_metadata()
    assert "section_codes" in meta
    parts = meta["section_codes"].split(",")
    assert "10160-0" in parts


# ── FHIR Bundle emission ───────────────────────────────────────


def test_ccda_to_fhir_bundle_produces_bundle_resource():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"


def test_ccda_to_fhir_bundle_includes_document_reference():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "DocumentReference" in types


def test_ccda_to_fhir_bundle_includes_binary_resource():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "Binary" in types


def test_ccda_to_fhir_bundle_binary_data_is_base64_of_raw():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    binary = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Binary"
    )
    decoded = base64.standard_b64decode(binary["data"])
    assert decoded == CCD_FIXTURE


def test_ccda_to_fhir_bundle_document_reference_has_subject():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    doc_ref = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DocumentReference"
    )
    assert doc_ref["subject"]["identifier"]["value"] == "Patient-001"


def test_ccda_to_fhir_bundle_document_reference_has_loinc_type():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    doc_ref = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DocumentReference"
    )
    coding = doc_ref["type"]["coding"][0]
    assert coding["system"] == "http://loinc.org"
    assert coding["code"] == "34133-9"


def test_ccda_to_fhir_bundle_attachment_includes_sha256_hash():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    bundle = doc.to_fhir_bundle(raw_bytes=CCD_FIXTURE)
    doc_ref = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DocumentReference"
    )
    attachment = doc_ref["content"][0]["attachment"]
    assert attachment["hash"] == doc.document_sha256


def test_ccda_read_to_bundle_convenience_wrapper():
    reader = CCDAReader()
    doc, bundle = reader.read_to_bundle(CCD_FIXTURE)
    assert doc.document_type_code == "34133-9"
    assert bundle["resourceType"] == "Bundle"
    assert len(bundle["entry"]) == 2


# ── Error handling ─────────────────────────────────────────────


def test_ccda_read_rejects_non_bytes():
    reader = CCDAReader()
    with pytest.raises(ValueError, match="expects bytes"):
        reader.read("not bytes")  # type: ignore


def test_ccda_read_rejects_empty_bytes():
    reader = CCDAReader()
    with pytest.raises(ValueError, match="empty"):
        reader.read(b"")


def test_ccda_read_rejects_malformed_xml():
    reader = CCDAReader()
    with pytest.raises(ValueError, match="XML parse error"):
        reader.read(b"<not-closed")


def test_ccda_read_rejects_non_cda_root():
    reader = CCDAReader()
    bad = b'<?xml version="1.0"?><Bundle xmlns="urn:hl7-org:fhir"/>'
    with pytest.raises(ValueError, match=r"root element"):
        reader.read(bad)


def test_ccda_strict_mode_rejects_missing_code():
    reader = CCDAReader(strict=True)
    bad = b"""<?xml version="1.0"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <id extension="DOC"/>
</ClinicalDocument>"""
    with pytest.raises(
        ValueError, match=r"strict.*code element missing"
    ):
        reader.read(bad)


def test_ccda_to_fhir_bundle_rejects_non_bytes_raw():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    with pytest.raises(ValueError, match="raw_bytes must be bytes"):
        doc.to_fhir_bundle(raw_bytes="not bytes")  # type: ignore


# ── Reader introspection ───────────────────────────────────────


def test_ccda_supported_document_types_returns_canonical_list():
    types = CCDAReader.supported_document_types()
    codes = [code for code, _ in types]
    assert "34133-9" in codes
    assert "18842-5" in codes
    assert "11506-3" in codes


def test_ccda_strict_mode_property_reflects_constructor():
    assert CCDAReader(strict=True).strict_mode is True
    assert CCDAReader(strict=False).strict_mode is False


# ── derive_metadata convenience ────────────────────────────────


def test_ccda_derive_metadata_with_workflow_id():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    meta = CCDAReader.derive_metadata(
        doc, customer_workflow_id="workflow-9999"
    )
    assert meta["customer_workflow_id"] == "workflow-9999"
    assert meta["document_type_code"] == "34133-9"


# ── Wire-constraint enforcement ────────────────────────────────


def test_ccda_metadata_keys_under_16_max():
    reader = CCDAReader()
    meta = reader.read(CCD_FIXTURE).to_metadata()
    assert len(meta) <= 16


def test_ccda_metadata_serialized_size_under_4kb():
    import json
    reader = CCDAReader()
    meta = reader.read(CCD_FIXTURE).to_metadata()
    assert len(json.dumps(meta)) <= 4096


# ── Three-role / vocabulary discipline ─────────────────────────


def test_ccda_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.healthcare import ccda as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )


def test_ccda_class_docstring_surfaces_three_role_discipline():
    doc = CCDAReader.__doc__ or ""
    assert "structural" in doc.lower()
    assert "customer" in doc.lower()


def test_ccda_module_docstring_surfaces_reader_only_scope():
    from nanorix.healthcare import ccda as mod
    doc = mod.__doc__ or ""
    assert "reader" in doc.lower()


# ── Round-trip stability ───────────────────────────────────────


def test_ccda_round_trip_preserves_header_identifiers():
    reader = CCDAReader()
    doc = reader.read(CCD_FIXTURE)
    meta = doc.to_metadata()
    assert meta["document_id"] == "DOC-001"
    assert meta["patient_id_reference"] == "Patient-001"
    assert meta["author_organization"] == "Acme Hospital"
