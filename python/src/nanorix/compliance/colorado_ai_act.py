"""Colorado AI Act (SB24-205) context for capsule workloads.

The Colorado Artificial Intelligence Act (SB24-205) was enacted in
2024 and is operative February 1, 2026. The Act establishes
requirements for developers and deployers of **High-Risk Artificial
Intelligence Systems** — defined as any AI system that, when deployed,
makes or is a substantial factor in making a **Consequential Decision**
affecting a Colorado consumer. The Act is codified at Colorado Revised
Statutes Title 6, Article 1, Part 17.

A **Consequential Decision** (Colorado Rev. Stat. § 6-1-1701(3)) is a
decision that has a material legal or similarly significant effect on
the provision or denial to a consumer (or the cost or terms thereof)
of:

- Education enrollment or an educational opportunity
- Employment or an employment opportunity
- A financial or lending service
- An essential government service
- Healthcare services
- Housing
- Insurance
- A legal service

Developer obligations (§ 6-1-1702) — applicable to entities that
develop or substantially modify a High-Risk AI System:

- Use reasonable care to protect consumers from any known or
  reasonably foreseeable risks of algorithmic discrimination
- Provide deployers with a statement disclosing intended uses, known
  harmful or inappropriate uses, summary of training data, foreseeable
  risks, evaluation methodology, data governance measures, and
  mitigation measures
- Publish on the developer's website a statement summarizing the types
  of High-Risk AI Systems the developer has developed or substantially
  modified
- Notify the Colorado Attorney General within 90 days of any
  algorithmic discrimination caused or contributed to by the High-Risk
  AI System

Deployer obligations (§ 6-1-1703) — applicable to entities that
deploy a High-Risk AI System:

- Use reasonable care to protect consumers from any known or
  reasonably foreseeable risks of algorithmic discrimination
- Implement a risk-management policy and program
- Complete an impact assessment annually and within 90 days of any
  intentional or substantial modification, addressing categories
  enumerated in § 6-1-1703(3) (purpose, intended use, deployer
  benefits, summary of data used, transparency measures, post-
  deployment monitoring, and others)
- Provide consumer notice of High-Risk AI System use before the
  consequential decision is made
- Provide an explanation of the principal reason(s) and degree to
  which the AI System contributed to an adverse Consequential
  Decision, with an opportunity to correct any inaccurate personal
  data the AI System used
- Provide consumers with an opportunity to appeal an adverse
  Consequential Decision (with limited exceptions)
- Publish on the deployer's website a statement summarizing the types
  of High-Risk AI Systems the deployer currently deploys and how the
  deployer manages known or reasonably foreseeable risks of
  algorithmic discrimination
- Annually review the deployment of each High-Risk AI System
- Notify the Colorado Attorney General within 90 days of any
  algorithmic discrimination

Enforcement: The Colorado Attorney General is the exclusive
enforcement authority (§ 6-1-1706). There is no private right of
action. Violations are deemed unfair or deceptive trade practices
under the Colorado Consumer Protection Act, with civil penalties of
up to $20,000 per violation. A rebuttable presumption of reasonable
care attaches when a developer or deployer complies with the
disclosure and assessment provisions (§ 6-1-1707).

Healthcare AI relevance: Healthcare services is a consequential-
decision category under § 6-1-1701(3)(e). Healthcare AI systems
making or substantially contributing to consequential decisions about
healthcare service provision (including clinical decision support that
substantially assists treatment / benefits-determination decisions) are
within scope. Healthcare-AI developers and deployers serving Colorado
consumers have material Colorado AI Act exposure post-February 1, 2026.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with the
    Colorado AI Act. High-Risk-AI-System determination, Consequential
    Decision scope analysis, impact-assessment adequacy, reasonable-
    care determination, and Attorney-General enforcement matters
    remain the customer's regulatory counsel's responsibility. The
    Colorado Attorney General is the exclusive enforcement authority.

State-jurisdictional helpers cross-vertical: a single workload may
fall under both the Colorado AI Act (state-jurisdictional, high-risk-
AI-specific) and a vertical-specific framework. Colorado-jurisdictional
healthcare AI workloads commonly pair :class:`ColoradoAIActContext`
with :class:`HipaaContext` (federal vertical) and
:class:`NISTAIRMFContext` (voluntary AI risk management framework);
Colorado-jurisdictional employment AI workloads commonly pair with
employer-context frameworks.

Companion posture document: ``docs/compliance/colorado-ai-act.md``.

Note: state AI laws are a fast-evolving regulatory space. The Colorado
AI Act effective date is February 1, 2026; implementing rulemaking by
the Colorado Attorney General is ongoing. The framework_version field
is a non-calendar operative-version identifier; bump when the Act or
implementing rules are amended.
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

_FRAMEWORK_VERSION = "2026-02"
"""Colorado AI Act operative version identifier. SB24-205 effective
February 1, 2026; Colorado Attorney General implementing rulemaking
in progress. Version string is a non-calendar operative-version
identifier; bump when the Act or implementing rules are amended."""

# Colorado AI Act is by construction a Colorado-jurisdictional helper.
_JURISDICTION_STATE = "CO"

_VALID_ACTOR_ROLES = frozenset(
    {
        "developer",
        "deployer",
        "developer_and_deployer",
    }
)
"""Colorado AI Act-aligned actor roles. Roles with respect to a
High-Risk AI System map to differing statutory obligations:

- ``developer`` — entity that develops or substantially modifies the
  AI System (§ 6-1-1702 obligations)
- ``deployer`` — entity that uses or deploys the AI System to make or
  substantially contribute to a Consequential Decision (§ 6-1-1703
  obligations)
- ``developer_and_deployer`` — entity that plays both roles for the
  same AI System (both sets of obligations attach)

A single organization may play differing roles across different AI
Systems; this field declares the role for THIS specific workload."""

_VALID_CONSEQUENTIAL_DECISION_CATEGORIES = frozenset(
    {
        "education",
        "employment",
        "financial_or_lending",
        "essential_government_service",
        "healthcare",
        "housing",
        "insurance",
        "legal_service",
        "not_consequential_decision",
        "other",
    }
)
"""§ 6-1-1701(3) Consequential Decision categories. ``healthcare`` is
the dominant healthcare-AI category. ``not_consequential_decision`` is
a customer-declared determination that the workload does not make or
substantially contribute to a consequential decision; ``other`` is an
escape hatch for edge-case fact patterns the customer's counsel
evaluates."""

_VALID_CONSUMER_NOTICE_TIMINGS = frozenset(
    {
        "before_consequential_decision",
        "concurrent_with_decision",
        "post_decision_adverse_explanation",
        "no_notice_provided",
        "not_required",
        "other",
    }
)
"""§ 6-1-1703(4) consumer-notice timing taxonomy. ``before_
consequential_decision`` is the dominant compliant timing under
§ 6-1-1703(4)(a). ``post_decision_adverse_explanation`` captures the
§ 6-1-1703(4)(b) adverse-decision explanation obligation, which is
separate from the pre-decision notice. ``not_required`` is a customer-
declared determination that no notice is required (typically paired
with ``high_risk_ai_system=False``)."""


@dataclass(frozen=True)
class ColoradoAIActContext:
    """Colorado AI Act (SB24-205) context for a capsule workload.

    Declares context for tagging this workload under the Colorado AI
    Act. Does NOT certify, attest to satisfaction of, or claim
    compliance with the Act. High-Risk-AI-System determination,
    Consequential Decision scope analysis, impact-assessment
    adequacy, reasonable-care determination, and Attorney-General
    enforcement matters remain the customer's regulatory counsel's
    responsibility.

    Common usage for AI workloads deployed in Colorado or serving
    Colorado consumers — particularly healthcare AI clinical decision
    support, employment screening AEDTs, insurance underwriting AI,
    and lending decision AI. The helper emits a structural
    ``jurisdiction_state="CO"`` flag plus three load-bearing booleans
    (``high_risk_ai_system``, ``impact_assessment_completed``,
    ``consumer_notice_provided``) tied directly to §§ 6-1-1702 +
    6-1-1703 statutory obligations.

    Pairs with :class:`HipaaContext` for Colorado-jurisdictional
    healthcare AI, with :class:`NISTAIRMFContext` for AI risk
    management framework alignment (the Act's risk-management-program
    requirement at § 6-1-1703(2) commonly aligns with NIST AI RMF's
    MAP / MEASURE / MANAGE functions), and with
    :class:`ISO42001Context` for ISO/IEC 42001-certified Colorado-
    deployed AI systems.

    See companion posture document: ``docs/compliance/colorado-ai-act.md``.
    """

    organization: str
    """The organization developing / deploying the High-Risk AI System
    affecting Colorado consumers. Required."""

    actor_role: str
    """The organization's Colorado AI Act-aligned role with respect to
    this AI System. Must be one of: ``developer``, ``deployer``,
    ``developer_and_deployer``. Required."""

    high_risk_ai_system: bool
    """Whether this workload is a High-Risk AI System (i.e., when
    deployed, makes or is a substantial factor in making a
    Consequential Decision affecting a Colorado consumer) per
    § 6-1-1701(9). Required boolean — emitted as the string ``"true"``
    or ``"false"`` in the metadata dict.

    The High-Risk-AI-System determination is the customer's; the
    helper records the customer's declared determination. Helper does
    NOT verify the workload's actual function or its substantial-
    factor role. Where ``high_risk_ai_system=False``, the helper
    still emits the framework tag — useful for recording cohort-
    affirmative non-applicability determinations for audit-trail
    purposes."""

    impact_assessment_completed: bool
    """Whether the customer has completed a § 6-1-1703(3) impact
    assessment within the past 12 months (or within 90 days of any
    intentional or substantial modification). Required boolean —
    emitted as the string ``"true"`` or ``"false"``.

    The assessment-completion determination is the customer's; the
    helper records the customer's declared status. Helper does NOT
    verify the assessment exists, addresses the § 6-1-1703(3)(a)
    through (k) enumerated categories, or is current under the annual
    review obligation."""

    consumer_notice_provided: bool
    """Whether the customer has provided § 6-1-1703(4)(a) consumer
    notice before a Consequential Decision is made (or, when
    applicable, the § 6-1-1703(4)(b) adverse-decision explanation).
    Required boolean — emitted as the string ``"true"`` or
    ``"false"``.

    The notice-provision determination is the customer's; the helper
    records the customer's declared status. Helper does NOT verify
    that notice was delivered with the required content, timing,
    or to the correct Colorado consumers."""

    consequential_decision_category: str
    """The § 6-1-1701(3) Consequential Decision category that this
    AI System makes or substantially contributes to. Must be one of
    the values in :attr:`consequential_decision_category_set`.
    Required.

    Use ``not_consequential_decision`` when the customer has
    determined the workload does not make or substantially contribute
    to a consequential decision (typically paired with
    ``high_risk_ai_system=False``). Use ``other`` as an escape hatch
    for edge-case fact patterns the customer's counsel evaluates."""

    consumer_notice_timing: Optional[str] = None
    """The customer-declared § 6-1-1703(4) consumer-notice timing.
    Must be one of the values in :attr:`consumer_notice_timing_set`
    when provided. Optional; customers commonly declare
    ``before_consequential_decision`` paired with
    ``consumer_notice_provided=True``."""

    affected_consumer_categories: List[str] = field(default_factory=list)
    """Categories of Colorado consumers affected by the AI System
    (e.g., ``'colorado_residents'``, ``'colorado_medicaid_
    beneficiaries'``, ``'colorado_health_insurance_enrollees'``,
    ``'colorado_consumer_credit_applicants'``). Free-form. Stored
    comma-joined."""

    impact_assessment_reference: Optional[str] = None
    """Customer-side reference to the § 6-1-1703(3) impact-assessment
    record. Helper does NOT verify the assessment exists or addresses
    all enumerated categories — pure context tag."""

    risk_management_policy_reference: Optional[str] = None
    """Customer-side reference to the § 6-1-1703(2) risk-management
    policy and program record. Helper does NOT verify the policy
    exists or is current — pure context tag."""

    developer_disclosure_reference: Optional[str] = None
    """Customer-side reference to the § 6-1-1702(2) developer
    disclosure statement provided to deployers. Helper does NOT verify
    the disclosure addresses all § 6-1-1702(2)(a) through (h)
    enumerated categories — pure context tag. Applicable when
    ``actor_role`` includes ``developer``."""

    adverse_decision_explanation_reference: Optional[str] = None
    """Customer-side reference to the § 6-1-1703(4)(b) adverse-
    decision explanation workflow record. Helper does NOT verify the
    explanation workflow exists or addresses the principal-reason
    requirement — pure context tag."""

    appeal_workflow_reference: Optional[str] = None
    """Customer-side reference to the § 6-1-1703(4)(c) consumer-appeal
    workflow record. Helper does NOT verify the appeal workflow
    exists or meets the statutory exception scope — pure context
    tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'attending_physician'``,
    ``'underwriter'``, ``'loan_officer'``,
    ``'admissions_officer'``). Useful for the AuditProof activity
    trail and accountable-deployment evidence in Colorado Attorney
    General enforcement conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'CO_SB24_205'``.
        """
        return "CO_SB24_205"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'CO'`` — the Colorado AI Act is by construction a
        Colorado-jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def consequential_decision_category_set(self) -> List[str]:
        """The canonical § 6-1-1701(3) Consequential Decision taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        consequential-decision-category enumeration.
        """
        return sorted(_VALID_CONSEQUENTIAL_DECISION_CATEGORIES)

    @property
    def consumer_notice_timing_set(self) -> List[str]:
        """The canonical § 6-1-1703(4) consumer-notice-timing taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        notice-timing enumeration.
        """
        return sorted(_VALID_CONSUMER_NOTICE_TIMINGS)

    @property
    def actor_role_set(self) -> List[str]:
        """The canonical actor-role taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        actor-role enumeration.
        """
        return sorted(_VALID_ACTOR_ROLES)

    def __post_init__(self) -> None:
        if not self.organization:
            raise ValueError(
                "organization is required and must be non-empty"
            )
        if not self.actor_role:
            raise ValueError(
                "actor_role is required and must be non-empty"
            )
        if self.actor_role not in _VALID_ACTOR_ROLES:
            raise ValueError(
                f"actor_role={self.actor_role!r} not in "
                f"{sorted(_VALID_ACTOR_ROLES)} "
                "(Colorado AI Act-aligned actor roles)"
            )

        # Required booleans — reject non-bool truthy values customers
        # may pass accidentally (e.g., the string 'true' or int 1).
        if not isinstance(self.high_risk_ai_system, bool):
            raise ValueError(
                "high_risk_ai_system must be a bool (got "
                f"{type(self.high_risk_ai_system).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.impact_assessment_completed, bool):
            raise ValueError(
                "impact_assessment_completed must be a bool (got "
                f"{type(self.impact_assessment_completed).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.consumer_notice_provided, bool):
            raise ValueError(
                "consumer_notice_provided must be a bool (got "
                f"{type(self.consumer_notice_provided).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if not self.consequential_decision_category:
            raise ValueError(
                "consequential_decision_category is required and must "
                "be non-empty"
            )
        if (
            self.consequential_decision_category
            not in _VALID_CONSEQUENTIAL_DECISION_CATEGORIES
        ):
            raise ValueError(
                "consequential_decision_category="
                f"{self.consequential_decision_category!r} not in "
                f"{sorted(_VALID_CONSEQUENTIAL_DECISION_CATEGORIES)} "
                "(§ 6-1-1701(3) Consequential Decision taxonomy)"
            )

        if self.consumer_notice_timing is not None:
            if not self.consumer_notice_timing:
                raise ValueError(
                    "consumer_notice_timing if provided must be non-empty"
                )
            if (
                self.consumer_notice_timing
                not in _VALID_CONSUMER_NOTICE_TIMINGS
            ):
                raise ValueError(
                    f"consumer_notice_timing={self.consumer_notice_timing!r} "
                    f"not in {sorted(_VALID_CONSUMER_NOTICE_TIMINGS)} "
                    "(§ 6-1-1703(4) consumer-notice-timing taxonomy)"
                )

        if self.affected_consumer_categories:
            for idx, item in enumerate(
                self.affected_consumer_categories
            ):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_consumer_categories[{idx}] must be "
                        f"a string (got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_consumer_categories[{idx}] must "
                        "be non-empty"
                    )
            validate_no_comma_in_list(
                "affected_consumer_categories",
                list(self.affected_consumer_categories),
            )

        validate_value_length("organization", self.organization)
        validate_value_length("actor_role", self.actor_role)
        validate_value_length(
            "consequential_decision_category",
            self.consequential_decision_category,
        )
        if self.consumer_notice_timing is not None:
            validate_value_length(
                "consumer_notice_timing", self.consumer_notice_timing
            )
        if self.affected_consumer_categories:
            joined = ",".join(self.affected_consumer_categories)
            validate_value_length(
                "affected_consumer_categories(joined)", joined
            )
        if self.impact_assessment_reference is not None:
            validate_value_length(
                "impact_assessment_reference",
                self.impact_assessment_reference,
            )
        if self.risk_management_policy_reference is not None:
            validate_value_length(
                "risk_management_policy_reference",
                self.risk_management_policy_reference,
            )
        if self.developer_disclosure_reference is not None:
            validate_value_length(
                "developer_disclosure_reference",
                self.developer_disclosure_reference,
            )
        if self.adverse_decision_explanation_reference is not None:
            validate_value_length(
                "adverse_decision_explanation_reference",
                self.adverse_decision_explanation_reference,
            )
        if self.appeal_workflow_reference is not None:
            validate_value_length(
                "appeal_workflow_reference",
                self.appeal_workflow_reference,
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
        ``jurisdiction_state="CO"`` flag plus three load-bearing
        booleans (``high_risk_ai_system``,
        ``impact_assessment_completed``, ``consumer_notice_provided``)
        as lowercase string ``"true"`` / ``"false"`` values.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash`` per ADR-010 A4).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "organization": self.organization,
            "actor_role": self.actor_role,
            "high_risk_ai_system": (
                "true" if self.high_risk_ai_system else "false"
            ),
            "impact_assessment_completed": (
                "true" if self.impact_assessment_completed else "false"
            ),
            "consumer_notice_provided": (
                "true" if self.consumer_notice_provided else "false"
            ),
            "consequential_decision_category": (
                self.consequential_decision_category
            ),
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.consumer_notice_timing is not None:
            meta["consumer_notice_timing"] = self.consumer_notice_timing
        if self.affected_consumer_categories:
            meta["affected_consumer_categories"] = ",".join(
                self.affected_consumer_categories
            )
        if self.impact_assessment_reference is not None:
            meta["impact_assessment_reference"] = (
                self.impact_assessment_reference
            )
        if self.risk_management_policy_reference is not None:
            meta["risk_management_policy_reference"] = (
                self.risk_management_policy_reference
            )
        if self.developer_disclosure_reference is not None:
            meta["developer_disclosure_reference"] = (
                self.developer_disclosure_reference
            )
        if self.adverse_decision_explanation_reference is not None:
            meta["adverse_decision_explanation_reference"] = (
                self.adverse_decision_explanation_reference
            )
        if self.appeal_workflow_reference is not None:
            meta["appeal_workflow_reference"] = (
                self.appeal_workflow_reference
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
