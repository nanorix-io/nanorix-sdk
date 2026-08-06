"""ISO/IEC 42001:2023 context for capsule workloads.

ISO/IEC 42001:2023 (Information technology — Artificial intelligence —
Management system) is the international AI Management System (AIMS)
standard published by ISO/IEC in December 2023. It is the first
certifiable management-system standard for organizations that develop,
provide, or use AI systems, structured around a Plan-Do-Check-Act (PDCA)
cycle with 23 controls in Annex A spanning AI policies, organizational
structure, AI resources, AI impact assessment, AI lifecycle, data for AI
systems, and information for interested parties.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with
    ISO/IEC 42001:2023. AIMS scope determination, control applicability,
    and certification audits remain the customer's accredited
    certification body's responsibility (e.g., A-LIGN, BSI, Schellman,
    or another ISO/IEC 17021-1-accredited certification body).

The cohort verification (2026-05-10) confirms Eleos as a verified
ISO/IEC 42001:2023-certified healthcare-AI vendor (per StackAware blog
November 2025). Customers in similar-shape AI cohorts may pursue or
maintain ISO/IEC 42001 certification and want capsule workloads tagged
with the AIMS scope they belong to.

Companion posture document: ``docs/compliance/iso-42001.md``.

Composes with :class:`NISTAIRMFContext` — the two frameworks overlap
substantially (both structure AI risk management around lifecycle
phases) but ISO/IEC 42001 is certifiable while NIST AI RMF is voluntary.
A workload can declare both contexts; the customer's certification body
reviews both.
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

_FRAMEWORK_VERSION = "2023"
"""ISO/IEC 42001:2023 (published 2023-12-18). The numeric year suffix
identifies the published standard generation."""

_VALID_AI_SYSTEM_ROLES = frozenset(
    {
        "ai_provider",
        "ai_developer",
        "ai_user",
        "ai_deployer",
    }
)
"""ISO/IEC 42001:2023 §3 AI system role taxonomy:

- ``ai_provider`` — develops + makes AI system available to others
- ``ai_developer`` — develops the AI system (may overlap with provider)
- ``ai_user`` — uses the AI system in own operations
- ``ai_deployer`` — deploys the AI system into operational use
"""

_VALID_PDCA_PHASES = frozenset(
    {
        "plan",
        "do",
        "check",
        "act",
    }
)
"""ISO/IEC 42001:2023 §6-§10 management-system PDCA cycle phases:

- ``plan`` — Clauses 4-6 (context, leadership, planning)
- ``do`` — Clauses 7-8 (support, operation)
- ``check`` — Clause 9 (performance evaluation)
- ``act`` — Clause 10 (improvement)
"""

# 23 ISO/IEC 42001:2023 Annex A control identifiers, in canonical
# A.2.x..A.10.x order. Names are short forms aligned with the Annex A
# control titles.
_ISO_42001_ANNEX_A_CONTROLS: List[str] = [
    "A_2_2_ai_policy",
    "A_2_3_alignment_with_other_organizational_policies",
    "A_2_4_review_of_ai_policy",
    "A_3_2_ai_roles_and_responsibilities",
    "A_3_3_reporting_of_concerns",
    "A_4_2_resources_for_ai_systems",
    "A_4_3_tooling_resources",
    "A_4_4_system_and_computing_resources",
    "A_4_5_human_resources",
    "A_4_6_data_resources",
    "A_5_2_ai_system_impact_assessment_process",
    "A_5_3_documentation_of_ai_system_impact_assessment",
    "A_5_4_assessment_of_ai_system_impact_on_individuals_or_groups",
    "A_5_5_assessment_of_societal_impacts_of_ai_systems",
    "A_6_1_2_objectives_for_responsible_ai_development",
    "A_6_2_2_ai_system_lifecycle_processes",
    "A_6_2_3_documentation_of_ai_system_design_and_development",
    "A_6_2_4_data_for_development_and_enhancement",
    "A_6_2_5_acquisition_of_ai_systems",
    "A_6_2_6_ai_system_release_management",
    "A_7_2_information_for_interested_parties_of_ai_systems",
    "A_7_3_external_reporting",
    "A_7_4_communication_of_incidents",
]


@dataclass(frozen=True)
class ISO42001Context:
    """ISO/IEC 42001:2023 context for a capsule workload.

    Declares context for tagging an AI workload under the ISO/IEC
    42001:2023 AI Management System standard. Does NOT certify, attest
    to satisfaction of, or claim compliance with ISO/IEC 42001:2023.
    AIMS scope determination, control applicability, and certification
    audits remain the customer's accredited certification body's
    responsibility.

    Common usage in healthcare-AI cohorts pursuing or maintaining
    ISO/IEC 42001 certification (e.g., the Eleos cohort verified
    2026-05-10 per StackAware blog November 2025). Pairs with
    :class:`NISTAIRMFContext` when both frameworks apply.

    See companion posture document: ``docs/compliance/iso-42001.md``.
    """

    organization: str
    """The certified organization (or organization pursuing
    certification) owning the AIMS. Required."""

    ai_system_role: str
    """ISO/IEC 42001:2023 §3 role of the organization with respect to
    this AI system. Must be one of: ``ai_provider``, ``ai_developer``,
    ``ai_user``, ``ai_deployer``. Required.

    A single organization may play multiple roles across different AI
    systems; this field declares the role for THIS specific
    workload."""

    ai_system_purpose: str
    """High-level statement of the AI system's purpose (e.g.,
    ``'clinical-decision-support'``, ``'documentation-automation'``,
    ``'patient-engagement'``). Required. Free-form."""

    pdca_phases_in_scope: List[str] = field(default_factory=list)
    """Subset of the four PDCA cycle phases in scope for this
    workload's evidence mapping. Must be a non-empty subset of
    ``plan``, ``do``, ``check``, ``act``. Stored comma-joined in
    the metadata string value."""

    annex_a_controls_in_scope: List[str] = field(default_factory=list)
    """Subset of the 23 ISO/IEC 42001:2023 Annex A controls in scope
    for this workload's evidence mapping. Must be a subset of the
    canonical control identifiers from :attr:`annex_a_controls`. May
    be empty if the workload's control-mapping has not yet been
    declared. Stored comma-joined."""

    certification_body: Optional[str] = None
    """ISO/IEC 17021-1-accredited certification body identifier (e.g.,
    ``'A-LIGN'``, ``'BSI'``, ``'Schellman'``). Helper does NOT verify
    accreditation status — pure context tag."""

    certification_status: Optional[str] = None
    """Optional certification status tag (e.g., ``'pursuing'``,
    ``'in_audit'``, ``'certified'``, ``'recertification'``). Free-form
    context tag — helper does NOT verify the status against the
    certification body."""

    cert_reference: Optional[str] = None
    """Customer-side ISO/IEC 42001 certificate reference. Helper does
    NOT verify a certificate exists — pure context tag."""

    aims_scope_statement_reference: Optional[str] = None
    """Reference to the customer-side AIMS scope statement (Clause 4.3
    of ISO/IEC 42001:2023). Helper does NOT verify the scope statement
    — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'ai_engineer'``,
    ``'data_scientist'``). Useful for the AuditProof activity trail
    and Annex A.3.2 (AI roles + responsibilities) evidence
    conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier
        ``'ISO_IEC_42001_2023'``.
        """
        return "ISO_IEC_42001_2023"

    @property
    def pdca_phases(self) -> List[str]:
        """The four canonical ISO/IEC 42001:2023 PDCA cycle phases.

        Returns a copy. Order matches the management-system Clauses
        4-10 sequence.
        """
        return ["plan", "do", "check", "act"]

    @property
    def annex_a_controls(self) -> List[str]:
        """The 23 canonical ISO/IEC 42001:2023 Annex A control
        identifiers.

        Returns a copy so customer code cannot mutate the module-level
        list. Order matches the canonical Annex A.2.x..A.10.x sequence.
        """
        return list(_ISO_42001_ANNEX_A_CONTROLS)

    def __post_init__(self) -> None:
        if not self.organization:
            raise ValueError(
                "organization is required and must be non-empty"
            )
        if not self.ai_system_role:
            raise ValueError(
                "ai_system_role is required and must be non-empty"
            )
        if self.ai_system_role not in _VALID_AI_SYSTEM_ROLES:
            raise ValueError(
                f"ai_system_role={self.ai_system_role!r} not in "
                f"{sorted(_VALID_AI_SYSTEM_ROLES)} "
                "(ISO/IEC 42001:2023 §3 AI system role taxonomy)"
            )
        if not self.ai_system_purpose:
            raise ValueError(
                "ai_system_purpose is required and must be non-empty"
            )

        if not self.pdca_phases_in_scope:
            raise ValueError(
                "pdca_phases_in_scope is required and must be non-empty "
                "(pick at least one of: plan, do, check, act)"
            )
        for idx, item in enumerate(self.pdca_phases_in_scope):
            if not isinstance(item, str):
                raise ValueError(
                    f"pdca_phases_in_scope[{idx}] must be a string "
                    f"(got {type(item).__name__})"
                )
            if item not in _VALID_PDCA_PHASES:
                raise ValueError(
                    f"pdca_phases_in_scope[{idx}]={item!r} not in "
                    f"{sorted(_VALID_PDCA_PHASES)}"
                )
        validate_no_comma_in_list(
            "pdca_phases_in_scope",
            list(self.pdca_phases_in_scope),
        )

        if self.annex_a_controls_in_scope:
            valid_control_set = set(_ISO_42001_ANNEX_A_CONTROLS)
            for idx, item in enumerate(self.annex_a_controls_in_scope):
                if not isinstance(item, str):
                    raise ValueError(
                        f"annex_a_controls_in_scope[{idx}] must be a "
                        f"string (got {type(item).__name__})"
                    )
                if item not in valid_control_set:
                    raise ValueError(
                        f"annex_a_controls_in_scope[{idx}]={item!r} "
                        "not in the 23 canonical ISO/IEC 42001:2023 "
                        "Annex A controls (introspect via "
                        "ISO42001Context.annex_a_controls)"
                    )
            validate_no_comma_in_list(
                "annex_a_controls_in_scope",
                list(self.annex_a_controls_in_scope),
            )

        validate_value_length("organization", self.organization)
        validate_value_length("ai_system_role", self.ai_system_role)
        validate_value_length(
            "ai_system_purpose", self.ai_system_purpose
        )
        joined_phases = ",".join(self.pdca_phases_in_scope)
        validate_value_length(
            "pdca_phases_in_scope(joined)", joined_phases
        )
        # annex_a_controls_in_scope full-set joined would be ~1500
        # chars — the customer should pick the subset relevant to this
        # workload, not emit the full set per capsule.
        if self.annex_a_controls_in_scope:
            joined_controls = ",".join(self.annex_a_controls_in_scope)
            validate_value_length(
                "annex_a_controls_in_scope(joined)", joined_controls
            )
        if self.certification_body is not None:
            validate_value_length(
                "certification_body", self.certification_body
            )
        if self.certification_status is not None:
            validate_value_length(
                "certification_status", self.certification_status
            )
        if self.cert_reference is not None:
            validate_value_length(
                "cert_reference", self.cert_reference
            )
        if self.aims_scope_statement_reference is not None:
            validate_value_length(
                "aims_scope_statement_reference",
                self.aims_scope_statement_reference,
            )
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. ``pdca_phases_in_scope`` and (when
        non-empty) ``annex_a_controls_in_scope`` are comma-joined into
        single string values.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "organization": self.organization,
            "ai_system_role": self.ai_system_role,
            "ai_system_purpose": self.ai_system_purpose,
            "pdca_phases_in_scope": ",".join(
                self.pdca_phases_in_scope
            ),
        }
        if self.annex_a_controls_in_scope:
            meta["annex_a_controls_in_scope"] = ",".join(
                self.annex_a_controls_in_scope
            )
        if self.certification_body is not None:
            meta["certification_body"] = self.certification_body
        if self.certification_status is not None:
            meta["certification_status"] = self.certification_status
        if self.cert_reference is not None:
            meta["cert_reference"] = self.cert_reference
        if self.aims_scope_statement_reference is not None:
            meta["aims_scope_statement_reference"] = (
                self.aims_scope_statement_reference
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
