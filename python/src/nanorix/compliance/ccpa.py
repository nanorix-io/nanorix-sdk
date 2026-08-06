"""CCPA (California Consumer Privacy Act) context for capsule workloads.

The California Consumer Privacy Act (CCPA), codified at California
Civil Code §§1798.100-1798.199.100, is a state-level consumer-privacy
statute that grants California residents a defined set of rights over
their personal information and imposes corresponding obligations on
businesses meeting one or more threshold criteria. The statute became
operative on January 1, 2020, and was substantially expanded by the
California Privacy Rights Act (CPRA, see :class:`CPRAContext`) on
January 1, 2023; many customers in California-jurisdictional cohorts
cite both statutes together when describing the operative regime.

This helper is a state-jurisdictional context tag — applicable to any
workload that processes California-resident personal information,
regardless of vertical. Healthcare-AI workloads serving California
patients commonly cite CCPA alongside HIPAA; software-as-a-service
workloads serving California-resident end users cite CCPA alongside
the vertical framework (PCI DSS, SOC 2, GDPR, etc.).

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with the
    CCPA. Threshold-applicability determination (the business-criteria
    test at §1798.140(d)), consumer-rights request handling, and
    enforcement matters remain the customer's regulatory counsel's
    responsibility (the California Privacy Protection Agency is the
    statutory enforcement authority; the California Attorney General
    retains parallel enforcement authority).

State-jurisdictional helpers cross-vertical: a single workload may
fall under both the CCPA (state-jurisdictional) and a vertical-specific
framework (e.g., HIPAA for healthcare, PCI DSS for cardholder data,
GDPR for EU residents). Customers wrap with both helpers and merge the
metadata dicts when both apply. The CCPA + CPRA pairing is the most
common — see :class:`CPRAContext` for the post-2023 expansion.

Companion posture document: ``docs/compliance/ccpa.md``.

Note: California consumer-privacy law is a fast-evolving space and the
CPPA continues to issue implementing regulations. As of the helper
publication, the framework_version field tags the operative-year
identifier; bump when statutory amendments take effect.
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

_FRAMEWORK_VERSION = "2020-operative"
"""CCPA operative version identifier. The CCPA became operative on
January 1, 2020 (Cal. Civ. Code §§1798.100-1798.199.100); subsequent
CPRA amendments (effective January 1, 2023) are tagged separately by
:class:`CPRAContext`. The version string is a non-calendar operative-
version identifier; bump when the CCPA is statutorily amended."""

# CCPA is by construction a California-jurisdictional helper. Emitted as
# a structural state tag for cross-state portfolio identification.
_JURISDICTION_STATE = "CA"

_VALID_BUSINESS_ROLES = frozenset(
    {
        "business",
        "service_provider",
        "contractor",
        "third_party",
    }
)
"""CCPA-aligned actor roles per §1798.140 definitions:

- ``business`` — the entity that determines purpose / means of
  processing of California-resident PI and falls within one or more
  statutory thresholds (§1798.140(d))
- ``service_provider`` — entity that processes PI on behalf of a
  business under a written contract (§1798.140(ag))
- ``contractor`` — entity that receives PI from a business for a
  business purpose under written contract (§1798.140(j))
- ``third_party`` — entity that is not a service provider, contractor,
  or the consumer (§1798.140(ai))

A single organization may play multiple roles across different
workloads; this field declares the role for THIS specific workload."""

_VALID_THRESHOLD_BASES = frozenset(
    {
        "annual_revenue_25m",
        "consumer_records_100k",
        "revenue_share_50_pct_from_selling_pi",
        "not_subject",
        "other",
    }
)
"""CCPA §1798.140(d) business-threshold bases. A business is subject to
the CCPA if it falls within one or more of:

- ``annual_revenue_25m`` — annual gross revenue exceeds $25M
- ``consumer_records_100k`` — annually buys/sells/shares PI of 100K+
  California consumers or households (lowered from 50K by CPRA)
- ``revenue_share_50_pct_from_selling_pi`` — derives 50%+ of annual
  revenue from selling/sharing consumers' PI
- ``not_subject`` — customer-declared determination that the workload
  is not subject to the CCPA (counsel-determined)
- ``other`` — escape hatch for affiliate-control or other applicability
  routes
"""

_VALID_PI_CATEGORIES = frozenset(
    {
        "identifiers",
        "personal_records",
        "protected_classifications",
        "commercial_information",
        "biometric_information",
        "internet_activity",
        "geolocation_data",
        "sensory_data",
        "professional_employment_information",
        "education_information",
        "inferences",
        "no_pi_processed",
        "other",
    }
)
"""CCPA §1798.140(v)(1) personal-information categories. Customer
declares one or more of these as the PI categories processed by this
workload. The taxonomy mirrors §1798.140(v)(1)(A)-(K) statutory
categories; ``no_pi_processed`` covers workloads whose customer has
determined no California-resident PI is in scope; ``other`` is an
escape hatch."""


@dataclass(frozen=True)
class CCPAContext:
    """CCPA context for a capsule workload.

    Declares context for tagging this workload under the California
    Consumer Privacy Act (Cal. Civ. Code §§1798.100-1798.199.100).
    Does NOT certify, attest to satisfaction of, or claim compliance
    with the CCPA. Threshold-applicability determination, consumer-
    rights request handling, and enforcement matters remain the
    customer's regulatory counsel's responsibility.

    Common usage for any workload processing California-resident
    personal information — particularly healthcare-AI workloads
    serving California patients (pair with :class:`HipaaContext`),
    SaaS workloads serving California end-users (pair with
    :class:`Soc2Context` or :class:`PCIDSSContext`), and any
    California + EU cross-jurisdictional workload (pair with
    :class:`GdprContext`). Most California-jurisdictional workloads
    post-2023 also fall under the CPRA — pair with
    :class:`CPRAContext` for the post-2023 expansion.

    The helper emits a structural ``jurisdiction_state="CA"`` flag and
    a ``consumer_data_in_scope`` boolean — the customer's GRC program
    uses these to identify California-jurisdictional workloads in
    cross-state portfolios.

    See companion posture document: ``docs/compliance/ccpa.md``.
    """

    business_organization: str
    """The organization that determines the purpose and means of
    processing California-resident personal information. Required."""

    business_role: str
    """The organization's CCPA-aligned role with respect to this
    workload. Must be one of: ``business``, ``service_provider``,
    ``contractor``, ``third_party``. Required."""

    threshold_basis: str
    """The §1798.140(d) basis under which the customer's organization
    is subject to the CCPA. Must be one of the values in
    :attr:`threshold_basis_set`. Required.

    Use ``not_subject`` for workloads the customer has determined are
    not subject to the CCPA (the customer's regulatory counsel makes
    this determination; the helper records it)."""

    consumer_data_in_scope: bool
    """Whether the workload processes California-resident personal
    information. Required boolean — emitted as the string ``"true"``
    or ``"false"`` in the metadata dict.

    The scope determination is the customer's; the helper records the
    customer's declared scope. Helper does NOT verify the workload's
    actual California-resident PI handling."""

    pi_categories: List[str] = field(default_factory=list)
    """Categories of California-resident personal information processed
    by this workload. Each item must be one of the values in
    :attr:`pi_category_set`. Stored comma-joined. Free-form ordering."""

    purpose_of_processing: Optional[str] = None
    """The customer's declared purpose for processing California-
    resident PI in this workload. Free-form (e.g.,
    ``'service_delivery'``, ``'clinical_workflow'``,
    ``'fraud_prevention'``). Helper does NOT verify the purpose is
    permitted under §1798.100(c) (purpose-specification)."""

    privacy_notice_reference: Optional[str] = None
    """Customer-side reference to the consumer-facing privacy notice
    required by §1798.100(b). Helper does NOT verify the notice
    exists, is published, or is current — pure context tag."""

    consumer_request_workflow_reference: Optional[str] = None
    """Customer-side reference to the consumer-rights request
    handling workflow (know, delete, correct, opt-out per
    §1798.100-§1798.130). Helper does NOT verify the workflow exists
    — pure context tag."""

    service_provider_contract_reference: Optional[str] = None
    """When ``business_role == 'service_provider'`` or
    ``'contractor'``, customer-side reference to the §1798.140(ag)/
    §1798.140(j) written contract. Helper does NOT verify the contract
    exists — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g.,
    ``'customer_success_engineer'``,
    ``'consumer_rights_request_handler'``). Useful for the AuditProof
    activity trail and accountable-deployment evidence."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'CCPA'``.
        """
        return "CCPA"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'CA'`` — the CCPA is by construction a California-
        jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def threshold_basis_set(self) -> List[str]:
        """The canonical §1798.140(d) threshold-basis taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        threshold-basis enumeration.
        """
        return sorted(_VALID_THRESHOLD_BASES)

    @property
    def pi_category_set(self) -> List[str]:
        """The canonical §1798.140(v)(1) PI-category taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        PI-category enumeration.
        """
        return sorted(_VALID_PI_CATEGORIES)

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
                "(CCPA §1798.140 actor roles)"
            )
        if not self.threshold_basis:
            raise ValueError(
                "threshold_basis is required and must be non-empty"
            )
        if self.threshold_basis not in _VALID_THRESHOLD_BASES:
            raise ValueError(
                f"threshold_basis={self.threshold_basis!r} not in "
                f"{sorted(_VALID_THRESHOLD_BASES)} "
                "(CCPA §1798.140(d) threshold bases)"
            )

        if not isinstance(self.consumer_data_in_scope, bool):
            raise ValueError(
                "consumer_data_in_scope must be a bool (got "
                f"{type(self.consumer_data_in_scope).__name__}); pass "
                "True or False, not the string 'true'"
            )

        if self.pi_categories:
            valid_pi_set = set(_VALID_PI_CATEGORIES)
            for idx, item in enumerate(self.pi_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"pi_categories[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if item not in valid_pi_set:
                    raise ValueError(
                        f"pi_categories[{idx}]={item!r} not in the "
                        "canonical CCPA §1798.140(v)(1) categories "
                        "(introspect via CCPAContext.pi_category_set)"
                    )
            validate_no_comma_in_list(
                "pi_categories", list(self.pi_categories)
            )

        validate_value_length(
            "business_organization", self.business_organization
        )
        validate_value_length("business_role", self.business_role)
        validate_value_length("threshold_basis", self.threshold_basis)
        if self.pi_categories:
            joined = ",".join(self.pi_categories)
            validate_value_length("pi_categories(joined)", joined)
        if self.purpose_of_processing is not None:
            validate_value_length(
                "purpose_of_processing", self.purpose_of_processing
            )
        if self.privacy_notice_reference is not None:
            validate_value_length(
                "privacy_notice_reference",
                self.privacy_notice_reference,
            )
        if self.consumer_request_workflow_reference is not None:
            validate_value_length(
                "consumer_request_workflow_reference",
                self.consumer_request_workflow_reference,
            )
        if self.service_provider_contract_reference is not None:
            validate_value_length(
                "service_provider_contract_reference",
                self.service_provider_contract_reference,
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
        ``jurisdiction_state="CA"`` flag and a
        ``consumer_data_in_scope`` boolean (lowercase string
        ``"true"`` / ``"false"``).

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "business_organization": self.business_organization,
            "business_role": self.business_role,
            "threshold_basis": self.threshold_basis,
            "consumer_data_in_scope": (
                "true" if self.consumer_data_in_scope else "false"
            ),
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.pi_categories:
            meta["pi_categories"] = ",".join(self.pi_categories)
        if self.purpose_of_processing is not None:
            meta["purpose_of_processing"] = self.purpose_of_processing
        if self.privacy_notice_reference is not None:
            meta["privacy_notice_reference"] = (
                self.privacy_notice_reference
            )
        if self.consumer_request_workflow_reference is not None:
            meta["consumer_request_workflow_reference"] = (
                self.consumer_request_workflow_reference
            )
        if self.service_provider_contract_reference is not None:
            meta["service_provider_contract_reference"] = (
                self.service_provider_contract_reference
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
