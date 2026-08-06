"""CPRA (California Privacy Rights Act) context for capsule workloads.

The California Privacy Rights Act (CPRA), enacted as Proposition 24 in
November 2020 and operative for most provisions on January 1, 2023, is
an expansion of the CCPA. It amends and extends the California
Consumer Privacy Act (Cal. Civ. Code §§1798.100-1798.199.100) by:

- Creating the **sensitive personal information (SPI)** category at
  §1798.140(ae) with a right to limit use and disclosure of SPI
  (§1798.121)
- Adding the right to **correction** of inaccurate PI (§1798.106)
- Expanding the right-to-know **lookback** beyond 12 months for PI
  collected on or after January 1, 2022 (§1798.130)
- Imposing **data-minimization** and **purpose-limitation**
  obligations (§1798.100(c))
- Imposing **storage-limitation** obligations on retention periods
  (§1798.100(a)(3))
- Establishing the **California Privacy Protection Agency (CPPA)** as
  a dedicated enforcement authority (§1798.199.10)
- Authorizing future regulations on **automated decision-making
  technology (ADMT)** opt-out rights (§1798.185(a)(16))

This helper records CPRA-specific context — particularly the SPI
category and ADMT opt-out designation — that the CCPA helper does NOT
capture. Customers in California-jurisdictional cohorts post-2023
commonly wrap with both :class:`CCPAContext` (base statute) and
:class:`CPRAContext` (expansion); the two helpers compose and the
right-hand framework wins the ``nanorix:framework`` key.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with the
    CPRA. Threshold-applicability determination, SPI categorization,
    ADMT opt-out implementation, and enforcement matters remain the
    customer's regulatory counsel's responsibility (the California
    Privacy Protection Agency is the primary statutory enforcement
    authority; the California Attorney General retains parallel
    authority).

Relationship to CCPA: the CPRA does NOT replace the CCPA — it amends
and expands it. Many practitioners describe the post-2023 California
consumer-privacy regime as "CCPA as amended by CPRA". This helper
intentionally emits ``nanorix:framework = "CPRA"`` rather than
``"CCPA"`` so cohort identification distinguishes pre-2023 from post-
2023 evidence. Customers may emit both — see the integration doc for
the merge pattern.

Companion posture document: ``docs/compliance/cpra.md``.

Note: the CPPA continues to issue implementing regulations. As of the
helper publication, the framework_version field is a non-calendar
operative-version identifier; bump when statutory amendments or
material CPPA regulations take effect.
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

_FRAMEWORK_VERSION = "2023-operative"
"""CPRA operative version identifier. The CPRA was enacted in
November 2020 as Proposition 24 and most operative provisions took
effect on January 1, 2023 (with enforcement commencing July 1, 2023).
The version string is a non-calendar operative-version identifier;
bump when statutory amendments or material CPPA regulations take
effect."""

# CPRA is by construction a California-jurisdictional helper.
_JURISDICTION_STATE = "CA"

_VALID_BUSINESS_ROLES = frozenset(
    {
        "business",
        "service_provider",
        "contractor",
        "third_party",
    }
)
"""CPRA-aligned actor roles — identical to CCPA §1798.140 definitions."""

# Sensitive personal information categories per §1798.140(ae). These
# are CPRA-introduced; they were not present in the original CCPA.
_VALID_SPI_CATEGORIES = frozenset(
    {
        "ssn",
        "drivers_license",
        "state_id",
        "passport_number",
        "financial_account",
        "debit_credit_card_with_credentials",
        "precise_geolocation",
        "racial_or_ethnic_origin",
        "religious_beliefs",
        "philosophical_beliefs",
        "union_membership",
        "mail_email_text_content",
        "genetic_data",
        "biometric_for_identification",
        "health",
        "sex_life_or_sexual_orientation",
        "no_spi_processed",
        "other",
    }
)
"""§1798.140(ae) sensitive personal information categories. ``no_spi_
processed`` covers workloads whose customer has determined no SPI is
in scope; ``other`` is an escape hatch for SPI categorizations that do
not map cleanly to the statutory enumeration."""

_VALID_ADMT_DESIGNATIONS = frozenset(
    {
        "uses_admt",
        "uses_admt_consequential_only",
        "does_not_use_admt",
        "admt_determination_pending",
    }
)
"""Automated decision-making technology designation per CPPA proposed
regulations under §1798.185(a)(16). The CPPA rulemaking on ADMT is
ongoing as of helper publication; customers may declare:

- ``uses_admt`` — workload uses automated decision-making technology
  in any form
- ``uses_admt_consequential_only`` — workload uses ADMT for
  significant decisions only (employment, housing, insurance, etc.)
- ``does_not_use_admt`` — customer-declared determination that no
  ADMT is in use
- ``admt_determination_pending`` — customer's regulatory counsel has
  not yet made a determination
"""


@dataclass(frozen=True)
class CPRAContext:
    """CPRA context for a capsule workload.

    Declares context for tagging this workload under the California
    Privacy Rights Act amendments to the CCPA. Does NOT certify,
    attest to satisfaction of, or claim compliance with the CPRA.
    Threshold-applicability determination, SPI categorization,
    correction-request handling, ADMT opt-out implementation, and
    enforcement matters remain the customer's regulatory counsel's
    responsibility.

    Common usage for California-jurisdictional workloads post-2023.
    Most customers in the cohort wrap with both :class:`CCPAContext`
    (base statute) and :class:`CPRAContext` (expansion) and merge the
    metadata dicts. Pairs additionally with :class:`HipaaContext` for
    California healthcare AI, :class:`GdprContext` for cross-
    jurisdictional California + EU workloads, and
    :class:`NISTAIRMFContext` for ADMT workloads where the customer
    aligns with the NIST AI RMF MEASURE function.

    The helper emits a structural ``jurisdiction_state="CA"`` flag,
    a ``sensitive_pi_in_scope`` boolean for the §1798.140(ae) SPI
    distinction, and an ``admt_designation`` enum for the ADMT
    designation.

    See companion posture document: ``docs/compliance/cpra.md``.
    """

    business_organization: str
    """The organization that determines the purpose and means of
    processing California-resident PI. Required."""

    business_role: str
    """The organization's CPRA-aligned role with respect to this
    workload. Must be one of: ``business``, ``service_provider``,
    ``contractor``, ``third_party``. Required."""

    sensitive_pi_in_scope: bool
    """Whether the workload processes California-resident sensitive
    personal information (SPI) per §1798.140(ae). Required boolean —
    emitted as the string ``"true"`` or ``"false"`` in the metadata
    dict.

    The SPI determination is the customer's; the helper records the
    customer's declared scope. Helper does NOT verify the workload's
    actual SPI handling."""

    admt_designation: str
    """The customer's declared automated decision-making technology
    designation per §1798.185(a)(16). Must be one of:
    ``uses_admt``, ``uses_admt_consequential_only``,
    ``does_not_use_admt``, ``admt_determination_pending``. Required.

    The ADMT designation is the customer's; CPPA rulemaking on ADMT
    is ongoing and the customer's regulatory counsel makes the
    determination."""

    spi_categories: List[str] = field(default_factory=list)
    """Categories of California-resident SPI processed by this
    workload, when ``sensitive_pi_in_scope=True``. Each item must be
    one of the values in :attr:`spi_category_set`. Stored comma-
    joined."""

    retention_period: Optional[str] = None
    """Storage-limitation tag per §1798.100(a)(3) (e.g.,
    ``'30_days'``, ``'session_only'``, ``'ephemeral'``,
    ``'7_years_after_treatment'``). Free-form. Helper does NOT verify
    the retention period is documented or enforced — pure context
    tag."""

    purpose_of_processing: Optional[str] = None
    """The customer's declared purpose for processing California-
    resident PI per §1798.100(c) purpose-limitation. Free-form."""

    privacy_notice_reference: Optional[str] = None
    """Customer-side reference to the consumer-facing privacy notice
    required by §1798.100(b) (as amended). Helper does NOT verify the
    notice exists, is published, is current, or covers SPI categories
    — pure context tag."""

    right_to_correct_workflow_reference: Optional[str] = None
    """Customer-side reference to the §1798.106 right-to-correct
    request handling workflow (CPRA-introduced). Helper does NOT
    verify the workflow exists — pure context tag."""

    right_to_limit_spi_workflow_reference: Optional[str] = None
    """Customer-side reference to the §1798.121 right-to-limit-use-
    and-disclosure-of-SPI workflow (CPRA-introduced). Helper does NOT
    verify the workflow exists — pure context tag."""

    admt_opt_out_workflow_reference: Optional[str] = None
    """Customer-side reference to the §1798.185(a)(16) ADMT opt-out
    workflow when ``admt_designation`` indicates ADMT use. CPPA
    rulemaking on ADMT is ongoing as of helper publication. Helper
    does NOT verify the workflow exists — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload. Useful for the AuditProof activity
    trail and accountable-deployment evidence."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'CPRA'``.
        """
        return "CPRA"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'CA'`` — the CPRA is by construction a California-
        jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def spi_category_set(self) -> List[str]:
        """The canonical §1798.140(ae) SPI-category taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        SPI-category enumeration.
        """
        return sorted(_VALID_SPI_CATEGORIES)

    @property
    def admt_designation_set(self) -> List[str]:
        """The canonical ADMT-designation taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        ADMT-designation enumeration.
        """
        return sorted(_VALID_ADMT_DESIGNATIONS)

    def __post_init__(self) -> None:
        if not self.business_organization:
            raise ValueError(
                "business_organization is required and must be non-empty"
            )
        if not self.business_role:
            raise ValueError(
                "business_role is required and must be non-empty"
            )
        if self.business_role not in _VALID_BUSINESS_ROLES:
            raise ValueError(
                f"business_role={self.business_role!r} not in "
                f"{sorted(_VALID_BUSINESS_ROLES)} "
                "(CPRA §1798.140 actor roles)"
            )

        if not isinstance(self.sensitive_pi_in_scope, bool):
            raise ValueError(
                "sensitive_pi_in_scope must be a bool (got "
                f"{type(self.sensitive_pi_in_scope).__name__}); pass "
                "True or False, not the string 'true'"
            )

        if not self.admt_designation:
            raise ValueError(
                "admt_designation is required and must be non-empty"
            )
        if self.admt_designation not in _VALID_ADMT_DESIGNATIONS:
            raise ValueError(
                f"admt_designation={self.admt_designation!r} not in "
                f"{sorted(_VALID_ADMT_DESIGNATIONS)} "
                "(CPPA ADMT-designation taxonomy)"
            )

        if self.spi_categories:
            valid_spi_set = set(_VALID_SPI_CATEGORIES)
            for idx, item in enumerate(self.spi_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"spi_categories[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if item not in valid_spi_set:
                    raise ValueError(
                        f"spi_categories[{idx}]={item!r} not in the "
                        "canonical CPRA §1798.140(ae) SPI categories "
                        "(introspect via CPRAContext.spi_category_set)"
                    )
            validate_no_comma_in_list(
                "spi_categories", list(self.spi_categories)
            )

        validate_value_length(
            "business_organization", self.business_organization
        )
        validate_value_length("business_role", self.business_role)
        validate_value_length(
            "admt_designation", self.admt_designation
        )
        if self.spi_categories:
            joined = ",".join(self.spi_categories)
            validate_value_length("spi_categories(joined)", joined)
        if self.retention_period is not None:
            validate_value_length(
                "retention_period", self.retention_period
            )
        if self.purpose_of_processing is not None:
            validate_value_length(
                "purpose_of_processing", self.purpose_of_processing
            )
        if self.privacy_notice_reference is not None:
            validate_value_length(
                "privacy_notice_reference",
                self.privacy_notice_reference,
            )
        if self.right_to_correct_workflow_reference is not None:
            validate_value_length(
                "right_to_correct_workflow_reference",
                self.right_to_correct_workflow_reference,
            )
        if self.right_to_limit_spi_workflow_reference is not None:
            validate_value_length(
                "right_to_limit_spi_workflow_reference",
                self.right_to_limit_spi_workflow_reference,
            )
        if self.admt_opt_out_workflow_reference is not None:
            validate_value_length(
                "admt_opt_out_workflow_reference",
                self.admt_opt_out_workflow_reference,
            )
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. Always emits the structural
        ``jurisdiction_state="CA"`` flag, a ``sensitive_pi_in_scope``
        boolean (lowercase string ``"true"`` / ``"false"``), and the
        ``admt_designation`` enum.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "business_organization": self.business_organization,
            "business_role": self.business_role,
            "sensitive_pi_in_scope": (
                "true" if self.sensitive_pi_in_scope else "false"
            ),
            "admt_designation": self.admt_designation,
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.spi_categories:
            meta["spi_categories"] = ",".join(self.spi_categories)
        if self.retention_period is not None:
            meta["retention_period"] = self.retention_period
        if self.purpose_of_processing is not None:
            meta["purpose_of_processing"] = self.purpose_of_processing
        if self.privacy_notice_reference is not None:
            meta["privacy_notice_reference"] = (
                self.privacy_notice_reference
            )
        if self.right_to_correct_workflow_reference is not None:
            meta["right_to_correct_workflow_reference"] = (
                self.right_to_correct_workflow_reference
            )
        if self.right_to_limit_spi_workflow_reference is not None:
            meta["right_to_limit_spi_workflow_reference"] = (
                self.right_to_limit_spi_workflow_reference
            )
        if self.admt_opt_out_workflow_reference is not None:
            meta["admt_opt_out_workflow_reference"] = (
                self.admt_opt_out_workflow_reference
            )
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
