"""HITRUST CSF v11 context for capsule workloads.

HITRUST CSF v11 is a certifiable security and privacy framework that
harmonizes HIPAA, NIST SP 800-53, ISO 27001, and PCI-DSS requirements into
19 control domains. A HITRUST r2 (validated) / e1 / i1 assessment is
issued by a HITRUST-authorized external assessor after evidence review.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with HITRUST
    CSF v11. Compliance determination remains the customer's HITRUST-
    authorized external assessor's responsibility.

Companion posture document: ``docs/compliance/hitrust-csf-v11.md``.
The posture document maps Nanorix substrate properties to HITRUST CSF v11
control domains using factual ``related_to`` language only.
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

_FRAMEWORK_VERSION = "v11"
"""HITRUST CSF v11 (current generally-available version)."""

_VALID_ASSESSMENT_TYPES = frozenset(
    {
        "e1",
        "i1",
        "r2",
    }
)
"""HITRUST assessment tiers per HITRUST Alliance:

- ``e1`` — essentials (entry-level, ~44 controls)
- ``i1`` — implemented (mid-tier, ~182 controls)
- ``r2`` — risk-based (validated, full scope)
"""

# 19 HITRUST CSF v11 control domains, in canonical order. Exposed as a
# class-level property so customers can introspect the framework's domain
# inventory without re-reading the spec. Names mirror the posture document
# at ``docs/compliance/hitrust-csf-v11.md``.
_HITRUST_DOMAINS: List[str] = [
    "01_information_security_management_program",
    "02_endpoint_protection",
    "03_portable_media_security",
    "04_mobile_device_security",
    "05_wireless_security",
    "06_configuration_management",
    "07_vulnerability_management",
    "08_network_protection",
    "09_password_management",
    "10_access_control",
    "11_audit_logging_and_monitoring",
    "12_education_training_and_awareness",
    "13_third_party_assurance",
    "14_incident_management",
    "15_business_continuity_and_disaster_recovery",
    "16_risk_management",
    "17_physical_and_environmental_security",
    "18_data_protection_and_privacy",
    "19_transmission_protection",
]


@dataclass(frozen=True)
class HITRUSTContext:
    """HITRUST CSF v11 context for a capsule workload.

    Declares context for tagging this workload under HITRUST CSF v11.
    Does NOT certify, attest to satisfaction of, or claim compliance
    with HITRUST CSF v11. Compliance determination remains the
    customer's HITRUST-authorized external assessor's responsibility.

    Common usage in high-risk healthcare AI cohort (e.g., the Eleos /
    SmarterDx / Nym / Fathom / MDClone customer set), where customers
    are pursuing or maintaining HITRUST r2 certification and want
    capsule workloads tagged with the assessment context they belong
    to. Pairs with :class:`HipaaContext` when both frameworks apply.

    See companion posture document: ``docs/compliance/hitrust-csf-v11.md``.
    """

    covered_entity: str
    """The HITRUST-assessed entity owning the data (typically the
    HIPAA-covered entity in healthcare contexts). Required."""

    assessment_type: str
    """HITRUST assessment tier the workload's evidence is scoped to.
    Must be one of: ``e1``, ``i1``, ``r2``. Required.

    Pick the tier that matches the customer's current HITRUST
    assessment scope (essentials, implemented, or validated)."""

    data_classification: str
    """Customer's classification of the data the capsule processes
    (e.g., ``'PHI'``, ``'PHI-radiology'``, ``'tenant_data'``).
    Required."""

    in_scope_domains: List[str] = field(default_factory=list)
    """Subset of the 19 HITRUST CSF v11 control domains in scope for
    this workload's evidence mapping. Stored comma-joined in the
    metadata string value. Each item must be one of the canonical
    domain names from :attr:`domains`. Helper rejects values outside
    that set; helper rejects individual items containing ``','``."""

    assessor: Optional[str] = None
    """HITRUST-authorized external assessor identifier (e.g.,
    ``'Coalfire'``, ``'BDO'``). Helper does NOT verify assessor
    authorization — pure context tag."""

    assessment_period: Optional[str] = None
    """Optional period tag (e.g., ``'2026-Q2'``,
    ``'2026-01-01..2026-12-31'``)."""

    cert_reference: Optional[str] = None
    """Customer-side HITRUST certificate or report reference. Helper
    does NOT verify a certificate exists — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g.,
    ``'attending_radiologist'``). Useful for the AuditProof activity
    trail and HITRUST Domain 10 (Access Control) evidence
    conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier
        ``'HITRUST_CSF_v11'``.
        """
        return "HITRUST_CSF_v11"

    @property
    def domains(self) -> List[str]:
        """The 19 canonical HITRUST CSF v11 control domains.

        Returns a copy so customer code cannot mutate the module-level
        list. Order matches the posture document at
        ``docs/compliance/hitrust-csf-v11.md``.
        """
        return list(_HITRUST_DOMAINS)

    def __post_init__(self) -> None:
        if not self.covered_entity:
            raise ValueError(
                "covered_entity is required and must be non-empty"
            )
        if not self.assessment_type:
            raise ValueError(
                "assessment_type is required and must be non-empty "
                "(pick one of: e1, i1, r2)"
            )
        if self.assessment_type not in _VALID_ASSESSMENT_TYPES:
            raise ValueError(
                f"assessment_type={self.assessment_type!r} not in "
                f"{sorted(_VALID_ASSESSMENT_TYPES)} "
                "(HITRUST Alliance assessment tiers)"
            )
        if not self.data_classification:
            raise ValueError("data_classification must be non-empty")

        # in_scope_domains may be empty (workload may not be evidence-
        # mapped to a specific HITRUST domain), but if provided, all
        # items must be in the canonical set.
        if self.in_scope_domains:
            valid_domain_set = set(_HITRUST_DOMAINS)
            for idx, item in enumerate(self.in_scope_domains):
                if not isinstance(item, str):
                    raise ValueError(
                        f"in_scope_domains[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if item not in valid_domain_set:
                    raise ValueError(
                        f"in_scope_domains[{idx}]={item!r} not in the "
                        "19 canonical HITRUST CSF v11 control domains "
                        "(introspect via HITRUSTContext.domains)"
                    )
            validate_no_comma_in_list(
                "in_scope_domains", list(self.in_scope_domains)
            )

        validate_value_length("covered_entity", self.covered_entity)
        validate_value_length("assessment_type", self.assessment_type)
        validate_value_length(
            "data_classification", self.data_classification
        )
        # Joined value length check — comma-separated string fits
        # 256-char wire limit. Worst case: 19 domains × ~52 chars +
        # 18 commas ≈ 1006 chars — would fail. Customer should pick
        # the subset directly relevant to this workload (typically
        # 1-5 domains).
        if self.in_scope_domains:
            joined = ",".join(self.in_scope_domains)
            validate_value_length("in_scope_domains(joined)", joined)
        if self.assessor is not None:
            validate_value_length("assessor", self.assessor)
        if self.assessment_period is not None:
            validate_value_length(
                "assessment_period", self.assessment_period
            )
        if self.cert_reference is not None:
            validate_value_length("cert_reference", self.cert_reference)
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. ``in_scope_domains`` (when non-empty)
        is comma-joined into a single string value (per the metadata
        schema's ``Dict[str, str]`` shape).

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "covered_entity": self.covered_entity,
            "assessment_type": self.assessment_type,
            "data_classification": self.data_classification,
        }
        if self.in_scope_domains:
            meta["in_scope_domains"] = ",".join(self.in_scope_domains)
        if self.assessor is not None:
            meta["assessor"] = self.assessor
        if self.assessment_period is not None:
            meta["assessment_period"] = self.assessment_period
        if self.cert_reference is not None:
            meta["cert_reference"] = self.cert_reference
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
