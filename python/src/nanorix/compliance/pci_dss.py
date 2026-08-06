"""PCI DSS v4.0 context for capsule workloads.

PCI DSS v4.0 (Payment Card Industry Data Security Standard) is the
operative card-data security standard published by the PCI Security
Standards Council. v4.0 was finalized in 2022 with a transition period
through 2025-Q1; v4.0.1 (an errata-style refresh) was published in 2024.
This helper targets the v4.0 generation as the operative customer-facing
version.

PCI DSS structures controls under 12 top-level requirements grouped into
six goals: Build and Maintain a Secure Network and Systems · Protect
Account Data · Maintain a Vulnerability Management Program · Implement
Strong Access Control Measures · Regularly Monitor and Test Networks ·
Maintain an Information Security Policy.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with PCI
    DSS. Cardholder-data-environment scoping, Self-Assessment
    Questionnaire (SAQ) selection, and Report on Compliance (ROC)
    determinations remain the customer's Qualified Security Assessor
    (QSA) or Internal Security Assessor (ISA) responsibility.

Common usage in the PBM / RCM / claims-processing cohort (Banjo Health
PBM-shape, Adonis-shape RCM, claims-routing platforms) where regulated
data crosses both PHI and cardholder-data scope.

Companion posture document: ``docs/compliance/pci-dss.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_no_comma_in_list,
    validate_value_length,
)

_FRAMEWORK_VERSION = "4.0"
"""PCI DSS v4.0 (finalized 2022, transition complete 2025-Q1). v4.0.1
errata refresh published 2024; v4.0 remains the canonical generation
identifier for assessment purposes."""

_VALID_MERCHANT_LEVELS = frozenset(
    {
        "level_1",
        "level_2",
        "level_3",
        "level_4",
        "service_provider_level_1",
        "service_provider_level_2",
    }
)
"""PCI SSC merchant + service-provider levels:

- ``level_1`` — >6M Visa/MC transactions/year (annual ROC by QSA)
- ``level_2`` — 1M-6M transactions/year
- ``level_3`` — 20K-1M e-commerce transactions/year
- ``level_4`` — <20K e-commerce / <1M total transactions
- ``service_provider_level_1`` — >300K transactions/year (annual ROC)
- ``service_provider_level_2`` — <300K transactions/year (annual SAQ-D)
"""

_VALID_ASSESSMENT_TYPES = frozenset(
    {
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
    }
)
"""PCI DSS v4.0 assessment types — Self-Assessment Questionnaire variants
(SAQ A/A-EP/B/B-IP/C/C-VT/D-Merchant/D-Service Provider/P2PE) plus the
full Report on Compliance (ROC) by a QSA."""

# 12 PCI DSS top-level requirements, in canonical order. Names are the
# colloquial short forms used in PCI SSC reference materials.
_PCI_DSS_REQUIREMENTS: List[str] = [
    "01_install_maintain_network_security_controls",
    "02_apply_secure_configurations",
    "03_protect_stored_account_data",
    "04_protect_cardholder_data_with_strong_cryptography_during_transmission",
    "05_protect_systems_from_malicious_software",
    "06_develop_maintain_secure_systems_software",
    "07_restrict_access_by_business_need_to_know",
    "08_identify_users_authenticate_access",
    "09_restrict_physical_access",
    "10_log_monitor_access",
    "11_test_security_of_systems_networks",
    "12_support_information_security_with_organizational_policies",
]


@dataclass(frozen=True)
class PCIDSSContext:
    """PCI DSS v4.0 context for a capsule workload.

    Declares context for tagging this workload under PCI DSS v4.0.
    Does NOT certify, attest to satisfaction of, or claim compliance
    with PCI DSS. CDE scoping, SAQ selection, and ROC determinations
    remain the customer's QSA/ISA's responsibility.

    Common usage in PBM, RCM, and claims-processing cohorts where
    regulated data crosses both PHI and cardholder-data scope. Pairs
    with :class:`HipaaContext` when both frameworks apply (e.g.,
    pharmacy claim routing where a partial PAN appears alongside PHI).

    The helper emits a structural ``cardholder_data_in_scope`` flag
    (string ``"true"`` or ``"false"``) that the customer's QSA reviews
    to identify capsules participating in the Cardholder Data
    Environment (CDE). The CDE-scoping decision is the customer's;
    the helper records the customer's declared decision.

    See companion posture document: ``docs/compliance/pci-dss.md``.
    """

    merchant_or_service_provider: str
    """The PCI-assessed entity owning the cardholder data context
    (e.g., the merchant, service provider, or processor name).
    Required."""

    merchant_level: str
    """PCI SSC merchant or service-provider level. Must be one of:
    ``level_1``, ``level_2``, ``level_3``, ``level_4``,
    ``service_provider_level_1``, ``service_provider_level_2``.
    Required."""

    assessment_type: str
    """PCI DSS v4.0 assessment type. Must be one of: ``saq_a``,
    ``saq_a_ep``, ``saq_b``, ``saq_b_ip``, ``saq_c``, ``saq_c_vt``,
    ``saq_d_merchant``, ``saq_d_service_provider``, ``saq_p2pe``,
    ``roc``. Required.

    Pick the SAQ tier or ROC scope that matches the customer's current
    PCI DSS assessment scope."""

    cardholder_data_in_scope: bool
    """Whether this capsule is in the customer's Cardholder Data
    Environment (CDE) scope. Required boolean — the customer must
    explicitly declare CDE participation. Emitted as the string
    ``"true"`` or ``"false"`` in the metadata dict.

    The CDE-scoping decision is fundamental to PCI DSS — it
    determines which controls apply. The helper records the
    customer's declared decision; it does NOT verify whether the
    capsule actually processes, stores, or transmits cardholder
    data."""

    in_scope_requirements: List[str] = field(default_factory=list)
    """Subset of the 12 PCI DSS v4.0 top-level requirements in scope
    for this workload's evidence mapping. Stored comma-joined in the
    metadata string value. Each item must be one of the canonical
    requirement identifiers from :attr:`requirements`. May be empty
    if the workload's requirement-mapping has not yet been declared."""

    qsa_firm: Optional[str] = None
    """PCI-authorized Qualified Security Assessor (QSA) firm
    identifier (e.g., ``'Coalfire'``, ``'NCC Group'``, ``'Schellman'``).
    Helper does NOT verify QSA authorization — pure context tag."""

    assessment_period: Optional[str] = None
    """Optional period tag (e.g., ``'2026-Q2'``,
    ``'2026-01-01..2026-12-31'``)."""

    aoc_reference: Optional[str] = None
    """Customer-side Attestation of Compliance (AoC) reference. Helper
    does NOT verify an AoC exists — pure context tag."""

    cde_segmentation_method: Optional[str] = None
    """Customer's declared method of segmenting the CDE from the rest
    of the customer's environment (e.g.,
    ``'network_segmentation'``, ``'application_layer_isolation'``,
    ``'cde_capsules_only'``). Free-form context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'pharmacy_tech'``,
    ``'claims_router'``). Useful for the AuditProof activity trail
    and PCI DSS Requirement 7 (need-to-know) + Requirement 8
    (identification) evidence conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'PCI_DSS'``.
        """
        return "PCI_DSS"

    @property
    def requirements(self) -> List[str]:
        """The 12 canonical PCI DSS v4.0 top-level requirements.

        Returns a copy so customer code cannot mutate the module-level
        list. Order matches the canonical sequence in PCI SSC v4.0
        reference materials.
        """
        return list(_PCI_DSS_REQUIREMENTS)

    def __post_init__(self) -> None:
        if not self.merchant_or_service_provider:
            raise ValueError(
                "merchant_or_service_provider is required and must be "
                "non-empty"
            )
        if not self.merchant_level:
            raise ValueError(
                "merchant_level is required and must be non-empty"
            )
        if self.merchant_level not in _VALID_MERCHANT_LEVELS:
            raise ValueError(
                f"merchant_level={self.merchant_level!r} not in "
                f"{sorted(_VALID_MERCHANT_LEVELS)} "
                "(PCI SSC merchant + service-provider levels)"
            )
        if not self.assessment_type:
            raise ValueError(
                "assessment_type is required and must be non-empty"
            )
        if self.assessment_type not in _VALID_ASSESSMENT_TYPES:
            raise ValueError(
                f"assessment_type={self.assessment_type!r} not in "
                f"{sorted(_VALID_ASSESSMENT_TYPES)} "
                "(PCI DSS v4.0 SAQ + ROC types)"
            )

        # cardholder_data_in_scope is bool-typed by dataclass; reject
        # non-bool truthy values that customers may pass accidentally
        # (e.g., the string "true").
        if not isinstance(self.cardholder_data_in_scope, bool):
            raise ValueError(
                "cardholder_data_in_scope must be a bool (got "
                f"{type(self.cardholder_data_in_scope).__name__}); pass "
                "True or False, not the string 'true'"
            )

        if self.in_scope_requirements:
            valid_req_set = set(_PCI_DSS_REQUIREMENTS)
            for idx, item in enumerate(self.in_scope_requirements):
                if not isinstance(item, str):
                    raise ValueError(
                        f"in_scope_requirements[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if item not in valid_req_set:
                    raise ValueError(
                        f"in_scope_requirements[{idx}]={item!r} not in "
                        "the 12 canonical PCI DSS v4.0 requirements "
                        "(introspect via PCIDSSContext.requirements)"
                    )
            validate_no_comma_in_list(
                "in_scope_requirements",
                list(self.in_scope_requirements),
            )

        validate_value_length(
            "merchant_or_service_provider",
            self.merchant_or_service_provider,
        )
        validate_value_length("merchant_level", self.merchant_level)
        validate_value_length("assessment_type", self.assessment_type)
        # in_scope_requirements joined string length check — full set
        # would be ~700 chars; customer should pick the subset directly
        # relevant to this workload.
        if self.in_scope_requirements:
            joined = ",".join(self.in_scope_requirements)
            validate_value_length(
                "in_scope_requirements(joined)", joined
            )
        if self.qsa_firm is not None:
            validate_value_length("qsa_firm", self.qsa_firm)
        if self.assessment_period is not None:
            validate_value_length(
                "assessment_period", self.assessment_period
            )
        if self.aoc_reference is not None:
            validate_value_length("aoc_reference", self.aoc_reference)
        if self.cde_segmentation_method is not None:
            validate_value_length(
                "cde_segmentation_method", self.cde_segmentation_method
            )
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. The ``cardholder_data_in_scope``
        bool is emitted as the lowercase string ``"true"`` or
        ``"false"`` (the metadata schema is ``Dict[str, str]``).

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash`` per ADR-010 A4).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "merchant_or_service_provider": (
                self.merchant_or_service_provider
            ),
            "merchant_level": self.merchant_level,
            "assessment_type": self.assessment_type,
            "cardholder_data_in_scope": (
                "true" if self.cardholder_data_in_scope else "false"
            ),
        }
        if self.in_scope_requirements:
            meta["in_scope_requirements"] = ",".join(
                self.in_scope_requirements
            )
        if self.qsa_firm is not None:
            meta["qsa_firm"] = self.qsa_firm
        if self.assessment_period is not None:
            meta["assessment_period"] = self.assessment_period
        if self.aoc_reference is not None:
            meta["aoc_reference"] = self.aoc_reference
        if self.cde_segmentation_method is not None:
            meta["cde_segmentation_method"] = self.cde_segmentation_method
        if self.workforce_member_role is not None:
            meta["workforce_member_role"] = self.workforce_member_role

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, "
                f"max {MAX_METADATA_KEYS}"
            )
        size = estimate_serialized_size(meta)
        if size > 4096:
            raise ValueError(
                f"to_metadata serializes to {size} bytes, max 4096"
            )
        return meta
