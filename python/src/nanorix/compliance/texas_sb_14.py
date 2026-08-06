"""Texas SB 14 (Texas Responsible AI Governance Act) context for capsule workloads.

Texas Senate Bill 14 (TRAIGA — Texas Responsible AI Governance Act) is a
state-level AI governance statute affecting AI systems deployed for use
by Texas residents or operating in the state of Texas. The statute
addresses high-risk AI systems used in consequential decisions affecting
individuals (including healthcare AI use cases such as clinical decision
support, treatment recommendations, and benefits determinations).

This helper is a state-jurisdictional context tag — applicable to any AI
workload deployed in Texas regardless of vertical, with healthcare-AI
deployments being particularly relevant given the cohort overlap between
Texas residency, Texas healthcare providers, and AI-enabled clinical
decision support tools.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with
    Texas SB 14 / TRAIGA. Statutory applicability determination,
    consequential-decision scope analysis, impact assessments, and
    enforcement matters remain the customer's regulatory counsel's
    responsibility (Texas Attorney General is the statutory enforcement
    authority).

State-jurisdictional helpers cross-vertical: a single AI workload may
fall under both Texas SB 14 (jurisdiction) and a vertical-specific
framework (e.g., HIPAA for healthcare AI). Customers wrap with both
helpers and merge the metadata dicts when both apply.

Companion posture document: ``docs/compliance/texas-sb-14.md``.

Note: state AI laws are a fast-evolving space. As of the helper
publication, Texas SB 14 is the operative Texas statute; Colorado SB
21-169 / Colorado AI Act and other state-level frameworks are addressed
in separate helpers as they ship. The framework_version field will be
updated when Texas SB 14 is amended; treat the version string as the
operative-version identifier rather than a calendar year.
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

_FRAMEWORK_VERSION = "2025-enacted"
"""Texas SB 14 — TRAIGA (Texas Responsible AI Governance Act),
operative version as enacted. The version string is a non-calendar
operative-version identifier; bump when the statute is amended."""

# Texas SB 14 jurisdiction value — emitted as a structural state tag.
_JURISDICTION_STATE = "TX"

_VALID_DEPLOYER_ROLES = frozenset(
    {
        "developer",
        "deployer",
        "distributor",
    }
)
"""TRAIGA-aligned actor roles. Roles with respect to high-risk AI
systems map to differing statutory obligations:

- ``developer`` — designs / develops the AI system
- ``deployer`` — operates / uses the AI system to make consequential
  decisions
- ``distributor`` — places the AI system on the market

A single organization may play multiple roles across different AI
systems; this field declares the role for THIS specific workload."""

_VALID_HIGH_RISK_USE_CASES = frozenset(
    {
        "healthcare_clinical_decision_support",
        "healthcare_treatment_recommendation",
        "healthcare_benefits_determination",
        "employment_decision",
        "education_decision",
        "financial_services_decision",
        "housing_decision",
        "government_services_decision",
        "criminal_justice_decision",
        "insurance_decision",
        "not_high_risk",
        "other_high_risk",
    }
)
"""High-risk AI use-case categories aligned with the consequential-
decision scope of state AI governance frameworks. ``not_high_risk``
is a customer-declared determination that the workload does not meet
the high-risk threshold; ``other_high_risk`` is an escape hatch for
high-risk uses not enumerated in this set."""


@dataclass(frozen=True)
class TexasSB14Context:
    """Texas SB 14 (TRAIGA) context for a capsule workload.

    Declares context for tagging this workload under Texas SB 14
    (Texas Responsible AI Governance Act). Does NOT certify, attest to
    satisfaction of, or claim compliance with TRAIGA. Statutory
    applicability determination, consequential-decision scope
    analysis, and impact assessments remain the customer's regulatory
    counsel's responsibility.

    Common usage for AI workloads deployed in Texas — particularly
    healthcare-AI deployments serving Texas residents (e.g., a
    clinical decision support tool used in a Texas hospital, or a
    treatment recommendation system serving Texas patients). The
    helper emits a structural ``jurisdiction_state="TX"`` flag that
    the customer's GRC program uses to identify Texas-jurisdictional
    workloads in cross-state portfolios.

    Pairs with :class:`HipaaContext` for Texas-jurisdictional
    healthcare AI, with :class:`NISTAIRMFContext` for AI risk
    management framework alignment, and with :class:`ISO42001Context`
    for ISO/IEC 42001-certified Texas-deployed AI systems.

    See companion posture document: ``docs/compliance/texas-sb-14.md``.
    """

    deployer_organization: str
    """The organization deploying / developing / distributing the AI
    system in Texas. Required."""

    deployer_role: str
    """The organization's TRAIGA-aligned role with respect to this AI
    system. Must be one of: ``developer``, ``deployer``,
    ``distributor``. Required."""

    high_risk_use_case: str
    """Customer's classification of the AI system's use case under
    TRAIGA's high-risk consequential-decision taxonomy. Must be one
    of the values in :attr:`high_risk_use_case_set`. Required.

    Use ``not_high_risk`` for workloads the customer has determined do
    not meet the high-risk threshold (the customer's regulatory
    counsel makes this determination; the helper records it). Use
    ``other_high_risk`` for high-risk uses not in the enumerated set."""

    impact_assessment_completed: bool
    """Whether the customer has completed a TRAIGA-aligned algorithmic
    impact assessment for this AI system. Required boolean — emitted
    as the string ``"true"`` or ``"false"`` in the metadata dict.

    The impact-assessment determination is the customer's; the helper
    records the customer's declared status. Helper does NOT verify the
    assessment exists or is current."""

    affected_categories: List[str] = field(default_factory=list)
    """Categories of individuals or groups affected by the AI system
    (e.g., ``'texas_residents'``, ``'texas_medicaid_beneficiaries'``,
    ``'texas_state_employees'``). Free-form. Stored comma-joined."""

    impact_assessment_reference: Optional[str] = None
    """Customer-side algorithmic impact assessment record reference.
    Helper does NOT verify the assessment exists — pure context tag."""

    notice_to_consumers_reference: Optional[str] = None
    """Customer-side reference to the consumer notice describing the
    AI system's use in consequential decisions. Helper does NOT verify
    the notice exists — pure context tag."""

    bias_audit_reference: Optional[str] = None
    """Customer-side bias / disparate-impact audit record reference,
    when the AI system has been audited for differential performance
    across demographic subgroups. Helper does NOT verify the audit —
    pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'attending_physician'``,
    ``'claims_adjudicator'``). Useful for the AuditProof activity
    trail and accountable-deployment evidence conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'TX_SB_14'``.
        """
        return "TX_SB_14"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'TX'`` — Texas SB 14 is by construction a Texas-
        jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def high_risk_use_case_set(self) -> List[str]:
        """The canonical high-risk use-case taxonomy.

        Returns a copy. Useful for customer introspection of the
        valid use-case enumeration.
        """
        return sorted(_VALID_HIGH_RISK_USE_CASES)

    def __post_init__(self) -> None:
        if not self.deployer_organization:
            raise ValueError(
                "deployer_organization is required and must be "
                "non-empty"
            )
        if not self.deployer_role:
            raise ValueError(
                "deployer_role is required and must be non-empty"
            )
        if self.deployer_role not in _VALID_DEPLOYER_ROLES:
            raise ValueError(
                f"deployer_role={self.deployer_role!r} not in "
                f"{sorted(_VALID_DEPLOYER_ROLES)} "
                "(TRAIGA-aligned actor roles)"
            )
        if not self.high_risk_use_case:
            raise ValueError(
                "high_risk_use_case is required and must be non-empty"
            )
        if self.high_risk_use_case not in _VALID_HIGH_RISK_USE_CASES:
            raise ValueError(
                f"high_risk_use_case={self.high_risk_use_case!r} not "
                f"in {sorted(_VALID_HIGH_RISK_USE_CASES)} "
                "(high-risk consequential-decision taxonomy)"
            )

        # impact_assessment_completed is bool-typed; reject non-bool
        # truthy values that customers may pass accidentally.
        if not isinstance(self.impact_assessment_completed, bool):
            raise ValueError(
                "impact_assessment_completed must be a bool (got "
                f"{type(self.impact_assessment_completed).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if self.affected_categories:
            for idx, item in enumerate(self.affected_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_categories[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_categories[{idx}] must be non-empty"
                    )
            validate_no_comma_in_list(
                "affected_categories", list(self.affected_categories)
            )

        validate_value_length(
            "deployer_organization", self.deployer_organization
        )
        validate_value_length("deployer_role", self.deployer_role)
        validate_value_length(
            "high_risk_use_case", self.high_risk_use_case
        )
        if self.affected_categories:
            joined = ",".join(self.affected_categories)
            validate_value_length(
                "affected_categories(joined)", joined
            )
        if self.impact_assessment_reference is not None:
            validate_value_length(
                "impact_assessment_reference",
                self.impact_assessment_reference,
            )
        if self.notice_to_consumers_reference is not None:
            validate_value_length(
                "notice_to_consumers_reference",
                self.notice_to_consumers_reference,
            )
        if self.bias_audit_reference is not None:
            validate_value_length(
                "bias_audit_reference", self.bias_audit_reference
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
        ``jurisdiction_state="TX"`` flag. The
        ``impact_assessment_completed`` bool is emitted as the
        lowercase string ``"true"`` or ``"false"``.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "deployer_organization": self.deployer_organization,
            "deployer_role": self.deployer_role,
            "high_risk_use_case": self.high_risk_use_case,
            "impact_assessment_completed": (
                "true" if self.impact_assessment_completed else "false"
            ),
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.affected_categories:
            meta["affected_categories"] = ",".join(
                self.affected_categories
            )
        if self.impact_assessment_reference is not None:
            meta["impact_assessment_reference"] = (
                self.impact_assessment_reference
            )
        if self.notice_to_consumers_reference is not None:
            meta["notice_to_consumers_reference"] = (
                self.notice_to_consumers_reference
            )
        if self.bias_audit_reference is not None:
            meta["bias_audit_reference"] = self.bias_audit_reference
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
