"""Compliance helper module — three-role discipline.

Declares framework context for a workload's metadata.
Does NOT certify, attest to satisfaction of, or claim compliance with any
framework. Compliance determination remains the customer's auditor's
responsibility.

Fifteen frameworks shipped:

- :class:`HipaaContext`             — US healthcare, 45 CFR Parts 160-164.
- :class:`GdprContext`              — EU data protection, Regulation (EU) 2016/679.
- :class:`Soc2Context`              — SOC 2 / Trust Services Criteria 2017.
- :class:`HITRUSTContext`           — HITRUST CSF v11 (19 control domains).
- :class:`CFRPart2Context`          — 42 CFR Part 2 (SUD records, 2024 revision).
- :class:`NISTAIRMFContext`         — NIST AI Risk Management Framework 1.0.
- :class:`PCIDSSContext`            — PCI DSS v4.0 (12 top-level requirements).
- :class:`ISO42001Context`          — ISO/IEC 42001:2023 AI Management System.
- :class:`TexasSB14Context`         — Texas SB 14 / TRAIGA (state AI governance).
- :class:`CCPAContext`              — California Consumer Privacy Act (state privacy).
- :class:`CPRAContext`              — California Privacy Rights Act (CCPA expansion).
- :class:`BIPAContext`              — Illinois Biometric Information Privacy Act (state biometric).
- :class:`NYCLL144Context`          — NYC Local Law 144 of 2021 (AEDT hiring law, municipal).
- :class:`ColoradoAIActContext`     — Colorado AI Act / SB24-205 (state AI law, effective 2026).
- :class:`UtahAIPolicyActContext`   — Utah AI Policy Act / SB 149 (state AI law, operative 2024).

Customer wraps once at capsule create::

    from nanorix import Client
    from nanorix.compliance import HipaaContext

    client = Client(api_key="nrx_live_...")

    ctx = HipaaContext(
        covered_entity="Acme Hospital",
        minimum_necessary_purpose="Inference for radiology workflow",
    )
    capsule = client.capsules.create(
        capsulefile="cf_...",
        metadata=ctx.to_metadata(),
    )

For SUD treatment workflows that fall under both HIPAA and 42 CFR Part 2,
wrap with both and merge::

    from nanorix.compliance import HipaaContext, CFRPart2Context

    hipaa = HipaaContext(covered_entity="Acme Treatment Center",
                        minimum_necessary_purpose="Clinical workflow")
    part2 = CFRPart2Context(part_2_program="Acme SUD Program",
                           minimum_necessary_purpose="Clinical workflow")
    # Customer chooses how to merge; CFRPart2Context's nanorix:framework
    # key wins by default. For paired-framework workloads, the customer's
    # auditor reviews both context tags in the AuditProof metadata.
    metadata = {**hipaa.to_metadata(), **part2.to_metadata()}

For Texas-jurisdictional healthcare AI, wrap HIPAA + NIST AI RMF + Texas
SB 14 and merge — the helpers compose without conflict (different
substantive field sets; the right-hand framework wins the
``nanorix:framework`` key).

For California-jurisdictional workloads post-2023, wrap CCPA + CPRA (the
CPRA does not replace the CCPA — it amends and expands it). Most
California-jurisdictional cohorts emit both context tags::

    from nanorix.compliance import CCPAContext, CPRAContext

    ccpa = CCPAContext(
        business_organization="Acme Health SaaS",
        business_role="business",
        threshold_basis="annual_revenue_25m",
        consumer_data_in_scope=True,
    )
    cpra = CPRAContext(
        business_organization="Acme Health SaaS",
        business_role="business",
        sensitive_pi_in_scope=True,
        admt_designation="uses_admt_consequential_only",
    )
    metadata = {**ccpa.to_metadata(), **cpra.to_metadata()}

For healthcare AI biometric workloads serving Illinois residents
(voice biomarkers, facial recognition diagnostics, iris-based patient
identification), wrap HIPAA + BIPA::

    from nanorix.compliance import HipaaContext, BIPAContext

    hipaa = HipaaContext(
        covered_entity="Acme Voice Biomarker Co",
        minimum_necessary_purpose="Voice-based clinical screening",
    )
    bipa = BIPAContext(
        collecting_entity="Acme Voice Biomarker Co",
        biometric_data_in_scope=True,
        consent_obtained=True,
        retention_policy_documented=True,
        biometric_identifier_type="voiceprint",
    )
    metadata = {**hipaa.to_metadata(), **bipa.to_metadata()}

BIPA carries a private right of action (740 ILCS 14/20) — see the
:class:`BIPAContext` docstring for the material risk consideration.

For NYC-jurisdictional AI-enabled hiring / promotion workloads, wrap
with :class:`NYCLL144Context`::

    from nanorix.compliance import NYCLL144Context

    aedt = NYCLL144Context(
        employer_organization="Acme NYC Hospital",
        employer_role="employer",
        aedt_in_use=True,
        bias_audit_completed=True,
        candidate_notice_provided=True,
        decision_type="screening_for_employment",
    )
    metadata = aedt.to_metadata()

For Colorado-jurisdictional High-Risk AI Systems making or substantially
contributing to Consequential Decisions (post-February 1, 2026), wrap
HIPAA + Colorado AI Act + NIST AI RMF for healthcare cohorts::

    from nanorix.compliance import (
        HipaaContext,
        ColoradoAIActContext,
        NISTAIRMFContext,
    )

    hipaa = HipaaContext(
        covered_entity="Acme Colorado Hospital",
        minimum_necessary_purpose="Clinical decision support",
    )
    co = ColoradoAIActContext(
        organization="Acme Colorado Hospital",
        actor_role="deployer",
        high_risk_ai_system=True,
        impact_assessment_completed=True,
        consumer_notice_provided=True,
        consequential_decision_category="healthcare",
    )
    nist = NISTAIRMFContext(
        ai_system_owner="Acme Colorado Hospital",
        use_case_category="clinical-decision-support",
        core_functions_in_scope=["map", "measure", "manage"],
    )
    metadata = {
        **hipaa.to_metadata(),
        **co.to_metadata(),
        **nist.to_metadata(),
    }

For Utah-jurisdictional generative-AI healthcare workloads (regulated
occupation under § 13-72-203), wrap HIPAA + Utah AI Policy Act::

    from nanorix.compliance import HipaaContext, UtahAIPolicyActContext

    hipaa = HipaaContext(
        covered_entity="Acme Utah Health AI",
        minimum_necessary_purpose="Patient-facing chatbot",
    )
    ut = UtahAIPolicyActContext(
        organization="Acme Utah Health AI",
        actor_role="regulated_occupation_provider",
        generative_ai_in_use=True,
        consumer_disclosure_provided=True,
        regulated_occupation_disclosure_provided=True,
        generative_ai_use_case="patient_engagement",
        regulated_occupation="healthcare_physician",
    )
    metadata = {**hipaa.to_metadata(), **ut.to_metadata()}

The metadata field binds into the AuditProof's ``metadata_hash``.
The customer's GRC platform (Vanta, Drata, Secureframe)
consumes the AuditProof's metadata field; the customer's auditor evaluates
against framework provisions; the related-to mappings ship in the
destruction-response ``regulatory_context`` field.
"""
from nanorix.compliance.bipa import BIPAContext
from nanorix.compliance.ccpa import CCPAContext
from nanorix.compliance.cfr_part_2 import CFRPart2Context
from nanorix.compliance.colorado_ai_act import ColoradoAIActContext
from nanorix.compliance.cpra import CPRAContext
from nanorix.compliance.gdpr import GdprContext
from nanorix.compliance.hipaa import HipaaContext
from nanorix.compliance.hitrust import HITRUSTContext
from nanorix.compliance.iso_42001 import ISO42001Context
from nanorix.compliance.nist_ai_rmf import NISTAIRMFContext
from nanorix.compliance.nyc_ll144 import NYCLL144Context
from nanorix.compliance.pci_dss import PCIDSSContext
from nanorix.compliance.soc2 import Soc2Context
from nanorix.compliance.texas_sb_14 import TexasSB14Context
from nanorix.compliance.utah_ai_policy_act import UtahAIPolicyActContext

__all__ = [
    "BIPAContext",
    "CCPAContext",
    "CFRPart2Context",
    "ColoradoAIActContext",
    "CPRAContext",
    "GdprContext",
    "HITRUSTContext",
    "HipaaContext",
    "ISO42001Context",
    "NISTAIRMFContext",
    "NYCLL144Context",
    "PCIDSSContext",
    "Soc2Context",
    "TexasSB14Context",
    "UtahAIPolicyActContext",
]
