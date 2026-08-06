"""42 CFR Part 2 (SUD records) context for capsule workloads.

42 CFR Part 2 (Confidentiality of Substance Use Disorder Patient Records)
governs the confidentiality of records of patients treated in federally-
assisted programs for substance use disorder (SUD). It is widely regarded
as the highest-sensitivity data protection requirement in US healthcare —
it carries restrictions beyond HIPAA and places strict limits on
disclosure, redisclosure, and subpoena response.

Companion to :class:`HipaaContext`. SUD treatment records typically fall
under BOTH HIPAA and 42 CFR Part 2; customers in the behavioral-health
cohort (Eleos + CalMHSA + similar) wrap capsule workloads in both
contexts and merge the metadata dicts.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with 42 CFR
    Part 2. The redisclosure prohibition (§2.32), patient consent
    requirements (§2.31), and Qualified Service Organization (QSO)
    obligations (§2.11) are entirely the customer's programmatic and
    legal responsibility.

Companion posture document: ``docs/compliance/42-cfr-part-2.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "2024-Revision"
"""42 CFR Part 2 was substantially revised by the HHS final rule
published 16 February 2024, harmonizing more closely with HIPAA. The
2024 revision is the operative regulatory text."""

_VALID_DISCLOSURE_BASES = frozenset(
    {
        "patient_consent",
        "medical_emergency",
        "research",
        "audit_evaluation",
        "court_order",
        "qso_agreement",
        "not_disclosed",
    }
)
"""Disclosure-basis tags aligning with 42 CFR Part 2 §2.31 (consent),
§2.51 (medical emergencies), §2.52 (research), §2.53 (audit/eval),
§2.61-§2.65 (court orders), §2.12(c)(4) (QSO), and ``not_disclosed``
(workload is internal-only, no disclosure occurs)."""


@dataclass(frozen=True)
class CFRPart2Context:
    """42 CFR Part 2 context for a capsule workload.

    Declares context for tagging this workload under 42 CFR Part 2.
    Does NOT certify, attest to satisfaction of, or claim compliance
    with 42 CFR Part 2. The customer's regulatory counsel determines
    Part 2 applicability and compliance posture.

    Common usage in behavioral health AI cohort (Eleos behavioral
    health + CalMHSA + substance-use treatment platforms). When a
    workload processes SUD treatment records, the customer typically
    wraps the capsule with BOTH :class:`HipaaContext` and
    :class:`CFRPart2Context` and merges the metadata dicts before
    passing to ``capsules.create()``.

    The helper emits a structural ``treatment_record_redisclosure_restriction``
    flag set to the string ``"true"`` to record that the workload is
    operating under 42 CFR Part 2 §2.32 redisclosure restrictions.
    This is a tag for downstream auditor review; the actual
    redisclosure controls live in the customer's application.

    See companion posture document: ``docs/compliance/42-cfr-part-2.md``.
    """

    part_2_program: str
    """The federally-assisted Part 2 program owning the data
    (e.g., the SUD treatment provider or program operator).
    Required. Per §2.11, a Part 2 program is the actor whose records
    are protected; the QSO (Nanorix) handles those records on the
    program's behalf."""

    minimum_necessary_purpose: str
    """Why this workload needs access to Part 2-protected records.
    Required. Part 2 §2.31 and §2.13 both impose minimum-necessary
    principles on disclosure and use."""

    data_classification: str = "PHI-SUD"
    """Default ``'PHI-SUD'`` (Protected Health Information, Substance
    Use Disorder records). Customer can narrow (e.g.,
    ``'PHI-SUD-clinical-notes'``)."""

    disclosure_basis: str = "not_disclosed"
    """The §2.31-§2.65 basis under which Part 2 records are
    being handled. Must be one of: ``patient_consent``,
    ``medical_emergency``, ``research``, ``audit_evaluation``,
    ``court_order``, ``qso_agreement``, ``not_disclosed``.
    Defaults to ``not_disclosed`` for internal capsule processing
    that does not involve external disclosure."""

    qso_agreement_reference: Optional[str] = None
    """Customer-side QSO Agreement (QSOA) contract reference between
    the Part 2 program and Nanorix as QSO. Helper does NOT verify a
    QSOA exists — pure context tag. The QSOA is the contractual
    mechanism under §2.11; legal execution is customer-owned."""

    consent_reference: Optional[str] = None
    """When ``disclosure_basis = patient_consent``, the customer-side
    consent record reference. Helper does NOT verify a consent record
    exists — pure context tag."""

    research_authorization_reference: Optional[str] = None
    """When ``disclosure_basis = research``, the §2.52 research
    authorization reference. Helper does NOT verify authorization —
    pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload within the Part 2 program (e.g.,
    ``'sud_counselor'``, ``'program_administrator'``)."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'42_CFR_Part_2'``.
        """
        return "42_CFR_Part_2"

    @property
    def redisclosure_restriction_applies(self) -> bool:
        """Whether §2.32 redisclosure restriction applies.

        Always returns ``True`` for any workload wrapped in this
        context — the entire point of CFRPart2Context is that the
        workload involves Part 2-protected records, which are subject
        to §2.32. Exposed as a property so customer downstream code
        can branch on it without re-parsing metadata.
        """
        return True

    def __post_init__(self) -> None:
        if not self.part_2_program:
            raise ValueError(
                "part_2_program is required and must be non-empty"
            )
        if not self.minimum_necessary_purpose:
            raise ValueError(
                "minimum_necessary_purpose is required and must be "
                "non-empty"
            )
        if not self.data_classification:
            raise ValueError("data_classification must be non-empty")
        if not self.disclosure_basis:
            raise ValueError(
                "disclosure_basis is required and must be non-empty"
            )
        if self.disclosure_basis not in _VALID_DISCLOSURE_BASES:
            raise ValueError(
                f"disclosure_basis={self.disclosure_basis!r} not in "
                f"{sorted(_VALID_DISCLOSURE_BASES)} "
                "(42 CFR Part 2 §2.31-§2.65 disclosure-basis set)"
            )

        # Conditional field cross-checks: when the disclosure basis
        # requires a paired reference field, surface the gap at
        # construction. Helper does NOT verify the reference's
        # validity (that's customer regulatory counsel's job); it
        # only ensures the field is set.
        if (
            self.disclosure_basis == "patient_consent"
            and self.consent_reference is None
        ):
            raise ValueError(
                "disclosure_basis='patient_consent' requires "
                "consent_reference to be set (§2.31 consent record "
                "reference; helper does NOT verify the consent)"
            )
        if (
            self.disclosure_basis == "research"
            and self.research_authorization_reference is None
        ):
            raise ValueError(
                "disclosure_basis='research' requires "
                "research_authorization_reference to be set (§2.52 "
                "research authorization reference; helper does NOT "
                "verify the authorization)"
            )

        validate_value_length("part_2_program", self.part_2_program)
        validate_value_length(
            "minimum_necessary_purpose", self.minimum_necessary_purpose
        )
        validate_value_length(
            "data_classification", self.data_classification
        )
        validate_value_length("disclosure_basis", self.disclosure_basis)
        if self.qso_agreement_reference is not None:
            validate_value_length(
                "qso_agreement_reference", self.qso_agreement_reference
            )
        if self.consent_reference is not None:
            validate_value_length(
                "consent_reference", self.consent_reference
            )
        if self.research_authorization_reference is not None:
            validate_value_length(
                "research_authorization_reference",
                self.research_authorization_reference,
            )
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. Always emits the
        ``treatment_record_redisclosure_restriction`` flag with value
        ``"true"`` to record that the workload operates under §2.32.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "part_2_program": self.part_2_program,
            "minimum_necessary_purpose": self.minimum_necessary_purpose,
            "data_classification": self.data_classification,
            "disclosure_basis": self.disclosure_basis,
            "treatment_record_redisclosure_restriction": "true",
        }
        if self.qso_agreement_reference is not None:
            meta["qso_agreement_reference"] = self.qso_agreement_reference
        if self.consent_reference is not None:
            meta["consent_reference"] = self.consent_reference
        if self.research_authorization_reference is not None:
            meta["research_authorization_reference"] = (
                self.research_authorization_reference
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
