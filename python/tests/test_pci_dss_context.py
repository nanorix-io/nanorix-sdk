"""Tests for nanorix.compliance.PCIDSSContext."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from nanorix.compliance import PCIDSSContext


# ── Required fields + framework identity ───────────────────────


def test_pci_dss_minimal_context_emits_required_keys():
    ctx = PCIDSSContext(
        merchant_or_service_provider="Acme Pharmacy",
        merchant_level="level_2",
        assessment_type="saq_d_merchant",
        cardholder_data_in_scope=True,
    )
    meta = ctx.to_metadata()
    assert meta["nanorix:framework"] == "PCI_DSS"
    assert meta["nanorix:framework_version"] == "4.0"
    assert meta["merchant_or_service_provider"] == "Acme Pharmacy"
    assert meta["merchant_level"] == "level_2"
    assert meta["assessment_type"] == "saq_d_merchant"
    assert meta["cardholder_data_in_scope"] == "true"
    # Optional fields absent in minimal construction.
    assert "in_scope_requirements" not in meta
    assert "qsa_firm" not in meta
    assert "assessment_period" not in meta
    assert "aoc_reference" not in meta
    assert "cde_segmentation_method" not in meta
    assert "workforce_member_role" not in meta


def test_pci_dss_framework_id_property_returns_canonical_identifier():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
    )
    assert ctx.framework_id == "PCI_DSS"
    assert ctx.framework_id == ctx.to_metadata()["nanorix:framework"]


def test_pci_dss_requirements_property_returns_12_canonical_requirements():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=False,
    )
    reqs = ctx.requirements
    assert len(reqs) == 12
    assert reqs[0].startswith("01_")
    assert reqs[-1].startswith("12_")
    # Specific known requirements present.
    assert "03_protect_stored_account_data" in reqs
    assert "07_restrict_access_by_business_need_to_know" in reqs
    assert "10_log_monitor_access" in reqs


def test_pci_dss_requirements_property_returns_copy_not_module_state():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=False,
    )
    reqs_a = ctx.requirements
    reqs_a.append("99_corrupted")
    reqs_b = ctx.requirements
    assert "99_corrupted" not in reqs_b
    assert len(reqs_b) == 12


# ── Cardholder-data-in-scope flag ──────────────────────────────


def test_pci_dss_cardholder_data_true_emits_string_true():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_4",
        assessment_type="saq_a",
        cardholder_data_in_scope=True,
    )
    assert ctx.to_metadata()["cardholder_data_in_scope"] == "true"


def test_pci_dss_cardholder_data_false_emits_string_false():
    """Customer may declare a capsule out of CDE scope (e.g., a
    capsule that processes operational data adjacent to but separate
    from the cardholder data environment). The structural flag still
    emits 'false'."""
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_4",
        assessment_type="saq_a",
        cardholder_data_in_scope=False,
    )
    assert ctx.to_metadata()["cardholder_data_in_scope"] == "false"


def test_pci_dss_cardholder_data_non_bool_rejected():
    """Customer accidentally passing the string 'true' instead of the
    bool True must surface a clear error rather than silently coerce."""
    with pytest.raises(ValueError, match="cardholder_data_in_scope"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="level_4",
            assessment_type="saq_a",
            cardholder_data_in_scope="true",  # type: ignore[arg-type]
        )


# ── Merchant-level validation ──────────────────────────────────


@pytest.mark.parametrize(
    "level",
    [
        "level_1",
        "level_2",
        "level_3",
        "level_4",
        "service_provider_level_1",
        "service_provider_level_2",
    ],
)
def test_pci_dss_all_six_merchant_levels_accepted(level):
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level=level,
        assessment_type="saq_d_merchant",
        cardholder_data_in_scope=True,
    )
    assert ctx.to_metadata()["merchant_level"] == level


def test_pci_dss_invalid_merchant_level_rejected():
    with pytest.raises(ValueError, match="merchant_level"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="level_5",  # not in canonical set
            assessment_type="saq_d_merchant",
            cardholder_data_in_scope=True,
        )


# ── Assessment-type validation ─────────────────────────────────


@pytest.mark.parametrize(
    "atype",
    [
        "saq_a",
        "saq_a_ep",
        "saq_b",
        "saq_b_ip",
        "saq_c",
        "saq_c_vt",
        "saq_d_merchant",
        "saq_d_service_provider",
        "saq_p2pe",
        "roc",
    ],
)
def test_pci_dss_all_ten_assessment_types_accepted(atype):
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type=atype,
        cardholder_data_in_scope=True,
    )
    assert ctx.to_metadata()["assessment_type"] == atype


def test_pci_dss_invalid_assessment_type_rejected():
    with pytest.raises(ValueError, match="assessment_type"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="level_1",
            assessment_type="saq_z",  # not in canonical set
            cardholder_data_in_scope=True,
        )


# ── Empty required-field rejection ─────────────────────────────


def test_pci_dss_empty_required_fields_rejected():
    with pytest.raises(ValueError, match="merchant_or_service_provider"):
        PCIDSSContext(
            merchant_or_service_provider="",
            merchant_level="level_1",
            assessment_type="roc",
            cardholder_data_in_scope=True,
        )
    with pytest.raises(ValueError, match="merchant_level"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="",
            assessment_type="roc",
            cardholder_data_in_scope=True,
        )
    with pytest.raises(ValueError, match="assessment_type"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="level_1",
            assessment_type="",
            cardholder_data_in_scope=True,
        )


# ── In-scope requirements ──────────────────────────────────────


def test_pci_dss_in_scope_requirements_optional_when_empty():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
        in_scope_requirements=[],
    )
    assert "in_scope_requirements" not in ctx.to_metadata()


def test_pci_dss_in_scope_requirements_comma_joined_when_present():
    ctx = PCIDSSContext(
        merchant_or_service_provider="X",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
        in_scope_requirements=[
            "03_protect_stored_account_data",
            "10_log_monitor_access",
        ],
    )
    meta = ctx.to_metadata()
    assert meta["in_scope_requirements"] == (
        "03_protect_stored_account_data,10_log_monitor_access"
    )


def test_pci_dss_invalid_in_scope_requirement_rejected():
    with pytest.raises(ValueError, match="in_scope_requirements"):
        PCIDSSContext(
            merchant_or_service_provider="X",
            merchant_level="level_1",
            assessment_type="roc",
            cardholder_data_in_scope=True,
            in_scope_requirements=[
                "03_protect_stored_account_data",
                "13_invented_requirement",  # not canonical
            ],
        )


# ── Optional fields ────────────────────────────────────────────


def test_pci_dss_optional_fields_when_set_appear_in_metadata():
    ctx = PCIDSSContext(
        merchant_or_service_provider="Acme Pharmacy Network",
        merchant_level="service_provider_level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
        qsa_firm="Coalfire",
        assessment_period="2026-Q2",
        aoc_reference="PCI-AoC-2026-001",
        cde_segmentation_method="cde_capsules_only",
        workforce_member_role="pharmacy_tech",
    )
    meta = ctx.to_metadata()
    assert meta["qsa_firm"] == "Coalfire"
    assert meta["assessment_period"] == "2026-Q2"
    assert meta["aoc_reference"] == "PCI-AoC-2026-001"
    assert meta["cde_segmentation_method"] == "cde_capsules_only"
    assert meta["workforce_member_role"] == "pharmacy_tech"


# ── Wire-constraint enforcement ────────────────────────────────


def test_pci_dss_oversized_value_rejected_at_construction():
    with pytest.raises(ValueError, match="256-char limit"):
        PCIDSSContext(
            merchant_or_service_provider="A" * 257,
            merchant_level="level_1",
            assessment_type="roc",
            cardholder_data_in_scope=True,
        )


def test_pci_dss_frozen_dataclass_mutation_raises():
    ctx = PCIDSSContext(
        merchant_or_service_provider="A",
        merchant_level="level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
    )
    with pytest.raises(Exception):
        ctx.merchant_or_service_provider = "B"  # type: ignore[misc]


def test_pci_dss_metadata_json_round_trip_preserves_dict():
    ctx = PCIDSSContext(
        merchant_or_service_provider="Acme",
        merchant_level="level_2",
        assessment_type="saq_d_merchant",
        cardholder_data_in_scope=True,
        in_scope_requirements=["03_protect_stored_account_data"],
        qsa_firm="BDO",
    )
    meta = ctx.to_metadata()
    round_tripped = json.loads(json.dumps(meta))
    assert round_tripped == meta


# ── HIPAA + PCI DSS paired workflow (PBM cohort) ───────────────


def test_pci_dss_paired_with_hipaa_context_merge_works():
    """PBM / pharmacy-claims cohort: workloads cross both PHI scope
    (HIPAA) and cardholder-data scope (PCI DSS). Customer wraps with
    both and merges."""
    from nanorix.compliance import HipaaContext

    hipaa = HipaaContext(
        covered_entity="Acme Pharmacy Network",
        minimum_necessary_purpose="Pharmacy claim adjudication",
    )
    pci = PCIDSSContext(
        merchant_or_service_provider="Acme Pharmacy Network",
        merchant_level="service_provider_level_1",
        assessment_type="roc",
        cardholder_data_in_scope=True,
    )
    merged = {**hipaa.to_metadata(), **pci.to_metadata()}
    # PCI wins by default (right-hand spread).
    assert merged["nanorix:framework"] == "PCI_DSS"
    # Both frameworks' substantive fields are present.
    assert merged["covered_entity"] == "Acme Pharmacy Network"
    assert merged["merchant_or_service_provider"] == (
        "Acme Pharmacy Network"
    )
    assert merged["cardholder_data_in_scope"] == "true"


# ── Three-role + factual-language discipline ───────────────────


def test_pci_dss_module_docstring_contains_disclaimer_language():
    """Module docstring must include the three-role disclaimer
    framing per ADR-017."""
    from nanorix.compliance import pci_dss as mod

    doc = mod.__doc__ or ""
    assert "does NOT" in doc
    assert "evidence vendor" in doc or "role 1" in doc
    # PCI-specific QSA / ROC disclaimer language.
    assert "QSA" in doc or "ISA" in doc


def test_pci_dss_class_docstring_contains_disclaimer_language():
    doc = PCIDSSContext.__doc__ or ""
    assert "does NOT" in doc.lower() or "Does NOT" in doc


def test_pci_dss_source_has_no_forbidden_compliance_vocabulary():
    """Per CLAUDE.md regulatory_context rules and the 6-scan pre-publish
    gate: helper source must not assert COMPLIANT / SATISFIED / PASSED /
    MEETS / CERTIFIED in Nanorix-voice contexts."""
    from nanorix.compliance import pci_dss as mod

    src = inspect.getsource(mod)
    forbidden_assertive = re.compile(
        r"\b(?:COMPLIANT|SATISFIED|PASSED|MEETS|CERTIFIED)\b"
    )
    matches = forbidden_assertive.findall(src)
    assert len(matches) == 0, (
        f"Forbidden assertive vocabulary in helper source: {matches}"
    )
