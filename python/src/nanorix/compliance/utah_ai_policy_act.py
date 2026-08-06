"""Utah Artificial Intelligence Policy Act (SB 149) context for capsule workloads.

The Utah Artificial Intelligence Policy Act (SB 149), enacted by the
Utah Legislature in March 2024 and operative May 1, 2024, was the
first comprehensive state AI law to take effect in the United States.
The Act amends Utah Code Title 13, Chapter 2 (Consumer Sales Practices
Act enforcement provisions), adds Title 13, Chapter 72 (Artificial
Intelligence Consumer Protection Provisions), and establishes the
Office of Artificial Intelligence Policy and the AI Learning Laboratory
Program at Utah Code Title 13, Chapter 73.

Substantive obligations (Utah Code § 13-72-202 through § 13-72-204):

- **Generative AI consumer disclosure (§ 13-72-202)** — a person who
  uses, prompts, or otherwise causes generative artificial intelligence
  to interact with a person in connection with a consumer transaction
  must clearly and conspicuously disclose that the person is
  interacting with generative AI and not a human, if the consumer
  asks or inquires whether they are interacting with a human or
  generative AI.
- **Regulated occupation disclosure (§ 13-72-203)** — a person
  providing services in a regulated occupation must clearly and
  conspicuously disclose at the start of any oral exchange or
  conversation, or in any written exchange, that the person is
  interacting with generative AI and not a human, when generative AI
  is used to provide services in a regulated occupation. Healthcare
  is a regulated occupation.
- **Liability allocation (§ 13-2-12)** — under the Consumer Sales
  Practices Act, the person who uses generative AI is liable for
  consumer protection violations to the same extent as if they had
  performed the violative act personally. The "the AI did it" defense
  is unavailable.

Enforcement: The Utah Division of Consumer Protection (within the
Department of Commerce) is the statutory enforcement authority for
the consumer-disclosure provisions. Penalties under the underlying
Consumer Sales Practices Act apply (civil penalties up to $2,500 per
violation; injunctive relief; restitution). The Utah Attorney General
has parallel enforcement authority for unfair or deceptive trade
practices. The AI Learning Laboratory Program (Utah Code Title 13,
Chapter 73) operates the Utah regulatory mitigation framework — a
regulatory-sandbox-style program for AI developers and deployers.

Healthcare AI relevance: Healthcare is a regulated occupation under
Utah law. Healthcare-AI vendors using generative AI to provide or
substantially assist healthcare services to Utah patients fall within
§ 13-72-203's regulated-occupation disclosure obligation. Voice-AI
chatbots interacting with patients, generative-AI clinical decision
support presenting recommendations to clinicians, and patient-facing
generative-AI applications operating in healthcare contexts are within
the disclosure scope.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with the
    Utah AI Policy Act. Generative-AI determination, regulated-
    occupation determination, disclosure adequacy, AI Learning
    Laboratory Program eligibility, and Division-of-Consumer-
    Protection enforcement matters remain the customer's regulatory
    counsel's responsibility. The Utah Division of Consumer
    Protection and the Utah Attorney General are the statutory
    enforcement authorities.

State-jurisdictional helpers cross-vertical: a single workload may
fall under both the Utah AI Policy Act (state-jurisdictional,
generative-AI-disclosure-specific) and a vertical-specific framework.
Utah-jurisdictional healthcare AI workloads commonly pair
:class:`UtahAIPolicyActContext` with :class:`HipaaContext` (federal
vertical) and :class:`NISTAIRMFContext` (voluntary AI risk management).

Companion posture document: ``docs/compliance/utah-ai-policy-act.md``.

Note: Utah AI Policy Act is one of the first enacted state AI laws
and is being actively refined through amendments. The Utah Office of
Artificial Intelligence Policy was established by the Act and may
issue additional implementing guidance. The framework_version field
is a non-calendar operative-version identifier; bump when the Act or
implementing rules are amended.

This helper was shipped in lieu of the federal AI Civil Rights Act
helper because the AI Civil Rights Act has not been enacted at the
time of helper publication (it was last introduced as S.5152 in the
118th Congress and has not advanced); shipping an enacted-law helper
is preferred over a proposed-framework helper per Wave 17 batch 5
discipline.
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

_FRAMEWORK_VERSION = "2024-operative"
"""Utah AI Policy Act operative version identifier. SB 149 operative
May 1, 2024; amendments and implementing guidance from the Utah
Office of AI Policy may follow. Version string is a non-calendar
operative-version identifier; bump when the Act or implementing rules
are amended."""

# Utah AI Policy Act is by construction a Utah-jurisdictional helper.
_JURISDICTION_STATE = "UT"

_VALID_ACTOR_ROLES = frozenset(
    {
        "generative_ai_provider",
        "generative_ai_user",
        "regulated_occupation_provider",
        "ai_learning_lab_participant",
    }
)
"""Utah AI Policy Act-aligned actor roles. Roles map to differing
statutory positioning:

- ``generative_ai_provider`` — entity that develops or provides the
  generative AI system used in consumer interactions
- ``generative_ai_user`` — entity that uses, prompts, or otherwise
  causes generative AI to interact with consumers (§ 13-72-202
  obligation attaches)
- ``regulated_occupation_provider`` — entity providing services in a
  regulated occupation using generative AI (§ 13-72-203 obligation
  attaches; includes healthcare providers)
- ``ai_learning_lab_participant`` — entity participating in the AI
  Learning Laboratory Program (Title 13, Chapter 73), which provides
  regulatory mitigation for qualifying AI applications

A single organization may play multiple roles across different AI
applications; this field declares the role for THIS specific workload."""

_VALID_GENERATIVE_AI_USE_CASES = frozenset(
    {
        "consumer_facing_chatbot",
        "clinical_decision_support",
        "patient_engagement",
        "healthcare_administrative",
        "customer_service",
        "content_generation",
        "code_generation",
        "internal_analytics",
        "not_generative_ai",
        "other",
    }
)
"""Utah AI Policy Act generative-AI use-case categories. ``consumer_
facing_chatbot`` is the dominant § 13-72-202 use case. ``clinical_
decision_support`` and ``patient_engagement`` are dominant
§ 13-72-203 healthcare use cases. ``not_generative_ai`` is a customer-
declared determination that the workload does not use generative AI
(typically paired with ``generative_ai_in_use=False``). ``other`` is
an escape hatch for edge-case use cases."""


@dataclass(frozen=True)
class UtahAIPolicyActContext:
    """Utah AI Policy Act (SB 149) context for a capsule workload.

    Declares context for tagging this workload under the Utah AI
    Policy Act. Does NOT certify, attest to satisfaction of, or claim
    compliance with the Act. Generative-AI determination, regulated-
    occupation determination, disclosure adequacy, AI Learning
    Laboratory Program eligibility, and Division-of-Consumer-
    Protection enforcement matters remain the customer's regulatory
    counsel's responsibility.

    Common usage for AI workloads deployed in Utah or serving Utah
    consumers — particularly generative-AI consumer-facing chatbots,
    generative-AI clinical decision support presenting recommendations
    to Utah clinicians or patients, generative-AI patient-engagement
    workflows, and generative-AI customer service applications. The
    helper emits a structural ``jurisdiction_state="UT"`` flag plus
    three load-bearing booleans (``generative_ai_in_use``,
    ``consumer_disclosure_provided``,
    ``regulated_occupation_disclosure_provided``) tied directly to
    §§ 13-72-202 + 13-72-203 statutory obligations.

    Pairs with :class:`HipaaContext` for Utah-jurisdictional
    healthcare generative AI (the dominant healthcare cohort under
    § 13-72-203's regulated-occupation disclosure obligation), with
    :class:`NISTAIRMFContext` for AI risk management framework
    alignment, and with :class:`ISO42001Context` for ISO/IEC 42001-
    certified Utah-deployed AI systems.

    .. note::

        Utah is one of the first US states with an enacted comprehensive
        AI law and was selected for Wave 17 batch 5 over the federal
        AI Civil Rights Act because the federal Act has not been
        enacted as of helper publication. Shipping an enacted-law
        helper is preferred over a proposed-framework helper. The Utah
        Office of AI Policy publishes implementing guidance; check
        for current rulemaking when relying on the helper for
        evidence purposes.

    See companion posture document: ``docs/compliance/utah-ai-policy-act.md``.
    """

    organization: str
    """The organization providing / using the generative AI system in
    Utah. Required."""

    actor_role: str
    """The organization's Utah AI Policy Act-aligned role with respect
    to this generative AI system. Must be one of:
    ``generative_ai_provider``, ``generative_ai_user``,
    ``regulated_occupation_provider``, ``ai_learning_lab_participant``.
    Required."""

    generative_ai_in_use: bool
    """Whether the workload uses generative artificial intelligence as
    defined under § 13-72-101 — i.e., AI that can produce content
    including text, images, audio, video, or code. Required boolean
    — emitted as the string ``"true"`` or ``"false"`` in the
    metadata dict.

    The generative-AI determination is the customer's; the helper
    records the customer's declared determination. Helper does NOT
    verify the workload's actual computational characteristics. Where
    ``generative_ai_in_use=False``, the helper still emits the
    framework tag — useful for recording cohort-affirmative non-
    applicability determinations for audit-trail purposes."""

    consumer_disclosure_provided: bool
    """Whether the customer provides § 13-72-202 generative-AI
    disclosure to Utah consumers when the consumer asks or inquires
    whether they are interacting with a human. Required boolean —
    emitted as the string ``"true"`` or ``"false"``.

    The disclosure-provision determination is the customer's; the
    helper records the customer's declared status. Helper does NOT
    verify that the disclosure was delivered with the required
    content, clarity, or conspicuousness, or that the customer's
    response-to-inquiry workflow is operational."""

    regulated_occupation_disclosure_provided: bool
    """Whether the customer provides § 13-72-203 regulated-occupation
    disclosure at the start of oral/written exchanges when generative
    AI is used to provide services in a regulated occupation
    (healthcare is a regulated occupation under Utah law). Required
    boolean — emitted as the string ``"true"`` or ``"false"``.

    The disclosure-provision determination is the customer's; the
    helper records the customer's declared status. Helper does NOT
    verify that the disclosure was delivered with the required
    content, timing (at the start of the exchange), or that the
    workload's occupation is in fact a regulated occupation under
    Utah law."""

    generative_ai_use_case: str
    """The Utah AI Policy Act-aligned generative-AI use-case category
    that this workload supports. Must be one of the values in
    :attr:`generative_ai_use_case_set`. Required.

    Use ``not_generative_ai`` when the customer has determined the
    workload does not use generative AI (typically paired with
    ``generative_ai_in_use=False``). Use ``other`` as an escape
    hatch for edge-case use cases the customer's counsel
    evaluates."""

    regulated_occupation: Optional[str] = None
    """Customer-declared regulated occupation under Utah law in which
    the generative AI is used (e.g.,
    ``'healthcare_physician'``, ``'healthcare_nurse'``,
    ``'mental_health_professional'``, ``'legal_services'``,
    ``'real_estate'``). Free-form. Customer's regulatory counsel
    determines whether the occupation is regulated under Utah law."""

    affected_consumer_categories: List[str] = field(default_factory=list)
    """Categories of Utah consumers affected by the generative AI
    (e.g., ``'utah_residents'``, ``'utah_patients'``,
    ``'utah_clinical_decision_subjects'``,
    ``'utah_chat_interaction_participants'``). Free-form. Stored
    comma-joined."""

    consumer_disclosure_reference: Optional[str] = None
    """Customer-side reference to the § 13-72-202 consumer-disclosure
    record / template / workflow. Helper does NOT verify the
    disclosure exists or meets the statutory clarity-and-
    conspicuousness requirement — pure context tag."""

    regulated_occupation_disclosure_reference: Optional[str] = None
    """Customer-side reference to the § 13-72-203 regulated-occupation
    disclosure record / template / workflow. Helper does NOT verify
    the disclosure exists, that it is delivered at the start of the
    exchange, or that it meets the statutory clarity-and-
    conspicuousness requirement — pure context tag."""

    ai_learning_lab_reference: Optional[str] = None
    """Customer-side reference to the AI Learning Laboratory Program
    participation record (Utah Code Title 13, Chapter 73), when
    applicable. The AI Learning Laboratory provides regulatory
    mitigation for qualifying AI applications. Helper does NOT verify
    program participation or eligibility — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g.,
    ``'attending_physician'``, ``'patient_engagement_engineer'``,
    ``'chatbot_engineer'``, ``'clinical_ai_engineer'``). Useful for
    the AuditProof activity trail and accountable-deployment
    evidence in Utah Division of Consumer Protection enforcement
    conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'UT_SB_149'``.
        """
        return "UT_SB_149"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'UT'`` — the Utah AI Policy Act is by construction a
        Utah-jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def generative_ai_use_case_set(self) -> List[str]:
        """The canonical generative-AI use-case taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        use-case enumeration.
        """
        return sorted(_VALID_GENERATIVE_AI_USE_CASES)

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
                "(Utah AI Policy Act-aligned actor roles)"
            )

        # Required booleans — reject non-bool truthy values customers
        # may pass accidentally (e.g., the string 'true' or int 1).
        if not isinstance(self.generative_ai_in_use, bool):
            raise ValueError(
                "generative_ai_in_use must be a bool (got "
                f"{type(self.generative_ai_in_use).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.consumer_disclosure_provided, bool):
            raise ValueError(
                "consumer_disclosure_provided must be a bool (got "
                f"{type(self.consumer_disclosure_provided).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(
            self.regulated_occupation_disclosure_provided, bool
        ):
            raise ValueError(
                "regulated_occupation_disclosure_provided must be a "
                "bool (got "
                f"{type(self.regulated_occupation_disclosure_provided).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if not self.generative_ai_use_case:
            raise ValueError(
                "generative_ai_use_case is required and must be non-empty"
            )
        if (
            self.generative_ai_use_case
            not in _VALID_GENERATIVE_AI_USE_CASES
        ):
            raise ValueError(
                f"generative_ai_use_case={self.generative_ai_use_case!r} "
                f"not in {sorted(_VALID_GENERATIVE_AI_USE_CASES)} "
                "(Utah AI Policy Act use-case taxonomy)"
            )

        if self.regulated_occupation is not None:
            if not self.regulated_occupation:
                raise ValueError(
                    "regulated_occupation if provided must be non-empty"
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
            "generative_ai_use_case", self.generative_ai_use_case
        )
        if self.regulated_occupation is not None:
            validate_value_length(
                "regulated_occupation", self.regulated_occupation
            )
        if self.affected_consumer_categories:
            joined = ",".join(self.affected_consumer_categories)
            validate_value_length(
                "affected_consumer_categories(joined)", joined
            )
        if self.consumer_disclosure_reference is not None:
            validate_value_length(
                "consumer_disclosure_reference",
                self.consumer_disclosure_reference,
            )
        if self.regulated_occupation_disclosure_reference is not None:
            validate_value_length(
                "regulated_occupation_disclosure_reference",
                self.regulated_occupation_disclosure_reference,
            )
        if self.ai_learning_lab_reference is not None:
            validate_value_length(
                "ai_learning_lab_reference",
                self.ai_learning_lab_reference,
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
        ``jurisdiction_state="UT"`` flag plus three load-bearing
        booleans (``generative_ai_in_use``,
        ``consumer_disclosure_provided``,
        ``regulated_occupation_disclosure_provided``) as lowercase
        string ``"true"`` / ``"false"`` values.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash`` per ADR-010 A4).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "organization": self.organization,
            "actor_role": self.actor_role,
            "generative_ai_in_use": (
                "true" if self.generative_ai_in_use else "false"
            ),
            "consumer_disclosure_provided": (
                "true"
                if self.consumer_disclosure_provided
                else "false"
            ),
            "regulated_occupation_disclosure_provided": (
                "true"
                if self.regulated_occupation_disclosure_provided
                else "false"
            ),
            "generative_ai_use_case": self.generative_ai_use_case,
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.regulated_occupation is not None:
            meta["regulated_occupation"] = self.regulated_occupation
        if self.affected_consumer_categories:
            meta["affected_consumer_categories"] = ",".join(
                self.affected_consumer_categories
            )
        if self.consumer_disclosure_reference is not None:
            meta["consumer_disclosure_reference"] = (
                self.consumer_disclosure_reference
            )
        if self.regulated_occupation_disclosure_reference is not None:
            meta["regulated_occupation_disclosure_reference"] = (
                self.regulated_occupation_disclosure_reference
            )
        if self.ai_learning_lab_reference is not None:
            meta["ai_learning_lab_reference"] = (
                self.ai_learning_lab_reference
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
