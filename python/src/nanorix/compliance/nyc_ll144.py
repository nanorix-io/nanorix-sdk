"""NYC Local Law 144 of 2021 (NYC AEDT hiring law) context for capsule workloads.

New York City Local Law 144 of 2021 — codified as administrative
provisions enforced by the NYC Department of Consumer and Worker
Protection (DCWP) under NYC Administrative Code §§ 20-870 through
20-874 — regulates the use of Automated Employment Decision Tools
(AEDTs) by employers and employment agencies operating in New York
City. The law was enacted in 2021 and became operative July 5, 2023.

An **Automated Employment Decision Tool (AEDT)** is a computational
process (derived from machine learning, statistical modeling, data
analytics, or artificial intelligence) that issues a simplified output
(including a score, classification, or recommendation) which is used
to substantially assist or replace discretionary decision making for
employment decisions impacting natural persons. Employment decisions
in scope include screening candidates for employment within New York
City and promotion decisions for current employees who reside in New
York City.

The law imposes three principal obligations on employers / employment
agencies that use an AEDT for an in-scope employment decision (NYC
Admin Code §§ 20-871 through 20-872 and DCWP Final Rule 6 RCNY
§§ 5-300 through 5-304):

- **Bias audit (independent, annual)** — the AEDT must have received a
  bias audit by an independent auditor within the year preceding its
  use. The audit must calculate selection rate and impact ratio for
  each category required by the law (sex; race / ethnicity;
  intersectional sex × race / ethnicity). A summary of the most recent
  bias audit must be publicly available on the employer's website.
- **Public posting** — the summary of the bias audit and the AEDT
  distribution date must be publicly available on the employer or
  employment agency's website, in a clear and conspicuous manner.
- **Candidate notice** — at least 10 business days before using an
  AEDT, the employer or employment agency must notify each candidate
  or employee who resides in NYC that an AEDT will be used, the job
  qualifications and characteristics the AEDT will assess, and the
  source / type of data collected. Candidates must be given an
  opportunity to request an alternative selection process or
  accommodation.

Enforcement: The NYC Department of Consumer and Worker Protection
(DCWP) is the statutory enforcement authority. Civil penalties for
each violation are $500 for a first violation and between $500 and
$1,500 for each subsequent violation (NYC Admin Code § 20-873). Each
day on which an AEDT is used in violation of the law constitutes a
separate violation, and each failure to provide notice constitutes a
separate violation.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with
    NYC Local Law 144 or any underlying provision. AEDT
    determination, bias-audit independence determination, audit
    methodology adequacy, public-posting placement, candidate-notice
    sufficiency, and DCWP enforcement matters remain the customer's
    regulatory counsel's responsibility. The NYC Department of
    Consumer and Worker Protection is the statutory enforcement
    authority.

State-jurisdictional helpers cross-vertical: a single workload may
fall under both NYC LL 144 (city-jurisdictional, AEDT-specific) and
a vertical-specific framework. Healthcare AI hiring / staffing
workloads that screen candidates for NYC-based roles (e.g., a hospital
or healthcare-AI-vendor employment screening AEDT serving NYC
candidates) commonly pair :class:`NYCLL144Context` with
:class:`HipaaContext` only when PHI is also processed; the dominant
pairing pattern is :class:`NYCLL144Context` standalone, since
employment screening typically does not involve PHI.

Companion posture document: ``docs/compliance/nyc-ll-144.md``.

Note: state and municipal AI laws are a fast-evolving regulatory
space. As of helper publication, NYC LL 144 is the operative NYC
ordinance; DCWP Final Rule 6 RCNY §§ 5-300 through 5-304 are the
operative implementing rules. The framework_version field is a
non-calendar operative-version identifier; bump when the ordinance
or implementing rules are amended.
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
"""NYC LL 144 operative version identifier. The 2021 ordinance
operative since July 5, 2023; DCWP Final Rule 6 RCNY §§ 5-300
through 5-304 ship the implementing rules. Version string is a
non-calendar operative-version identifier; bump when the ordinance
or implementing rules are amended."""

# NYC LL 144 is by construction a New York City municipal helper. The
# law operates within New York State, but the jurisdictional scope is
# the city (the City of New York), not the state of New York. Customers
# get both metadata flags for cross-jurisdictional portfolio filtering.
_JURISDICTION_STATE = "NY"
_JURISDICTION_CITY = "NYC"

_VALID_EMPLOYER_ROLES = frozenset(
    {
        "employer",
        "employment_agency",
        "vendor_to_employer",
    }
)
"""NYC LL 144-aligned actor roles. Roles map to differing statutory
positioning:

- ``employer`` — entity making employment decisions using the AEDT for
  its own hiring or promotion
- ``employment_agency`` — entity procuring employees on behalf of an
  employer using an AEDT
- ``vendor_to_employer`` — entity providing the AEDT as a service to
  an employer; not directly subject to LL 144 obligations but commonly
  records context for downstream evidence

A single organization may play multiple roles across different AEDT
deployments; this field declares the role for THIS specific workload."""

_VALID_DECISION_TYPES = frozenset(
    {
        "screening_for_employment",
        "promotion_decision",
        "screening_and_promotion",
        "not_in_scope",
        "other",
    }
)
"""NYC LL 144 in-scope employment decision categories. ``screening_
for_employment`` covers candidate screening for NYC-located positions;
``promotion_decision`` covers promotion decisions for NYC-resident
current employees; ``screening_and_promotion`` covers AEDTs used for
both decisions; ``not_in_scope`` is a customer-declared determination
that the workload does not constitute an in-scope employment decision
under LL 144; ``other`` is an escape hatch for edge-case fact patterns
the customer's counsel evaluates."""


@dataclass(frozen=True)
class NYCLL144Context:
    """NYC Local Law 144 of 2021 context for a capsule workload.

    Declares context for tagging this workload under New York City
    Local Law 144 of 2021 (NYC AEDT law) and the DCWP Final Rule. Does NOT
    certify, attest to satisfaction of, or claim compliance with LL
    144. AEDT determination, bias-audit independence determination,
    audit methodology adequacy, public-posting placement, candidate-
    notice sufficiency, and DCWP enforcement matters remain the
    customer's regulatory counsel's responsibility.

    Common usage for AI-enabled hiring / promotion workloads deployed
    in New York City — particularly candidate-screening AEDTs and
    promotion-recommendation AEDTs. The helper emits structural
    ``jurisdiction_state="NY"`` and ``jurisdiction_city="NYC"`` flags
    that the customer's GRC program uses to identify NYC-jurisdictional
    workloads in cross-state portfolios.

    Pairs with :class:`HipaaContext` when the AEDT processes PHI
    (uncommon for hiring AEDTs; possible for healthcare-staffing-
    specific AEDTs); pairs with :class:`NISTAIRMFContext` for AI risk
    management framework alignment, and with :class:`ISO42001Context`
    for ISO/IEC 42001-certified NYC-deployed AEDTs.

    See companion posture document: ``docs/compliance/nyc-ll-144.md``.
    """

    employer_organization: str
    """The organization deploying / using the AEDT for an in-scope
    employment decision in NYC. Required."""

    employer_role: str
    """The organization's LL 144-aligned role with respect to this
    AEDT. Must be one of: ``employer``, ``employment_agency``,
    ``vendor_to_employer``. Required."""

    aedt_in_use: bool
    """Whether the workload uses an Automated Employment Decision Tool
    in scope of NYC LL 144 — i.e., a computational process that issues
    a simplified output substantially assisting or replacing
    discretionary employment decisions for NYC candidates or NYC
    promotion decisions. Required boolean — emitted as the string
    ``"true"`` or ``"false"`` in the metadata dict.

    The AEDT-scope determination is the customer's; the helper records
    the customer's declared determination. Helper does NOT verify the
    workload's actual computational characteristics. Where
    ``aedt_in_use=False``, the helper still emits the framework tag —
    useful for recording cohort-affirmative non-applicability
    determinations for audit-trail purposes."""

    bias_audit_completed: bool
    """Whether the customer has obtained an independent bias audit of
    the AEDT within the year preceding its use, per NYC Admin Code
    § 20-871(b) and DCWP Final Rule 6 RCNY § 5-301. Required boolean
    — emitted as the string ``"true"`` or ``"false"``.

    The audit-completion determination is the customer's; the helper
    records the customer's declared status. Helper does NOT verify
    that the audit was performed, that the auditor was independent
    under § 20-870, that the audit calculated the required selection-
    rate and impact-ratio metrics, or that the audit covered the
    operative version of the AEDT."""

    candidate_notice_provided: bool
    """Whether the customer provided at-least-10-business-days notice
    to NYC-resident candidates / employees about AEDT use, per NYC
    Admin Code § 20-871(b)(2) and DCWP Final Rule 6 RCNY § 5-303.
    Required boolean — emitted as the string ``"true"`` or
    ``"false"``.

    The notice-provision determination is the customer's; the helper
    records the customer's declared status. Helper does NOT verify
    that notice was delivered with the required content, timing,
    accommodation-request mechanism, or to the correct candidates."""

    decision_type: str
    """The NYC LL 144-aligned in-scope decision category that this
    AEDT supports. Must be one of the values in
    :attr:`decision_type_set`. Required.

    Use ``not_in_scope`` when the customer has determined the workload
    does not constitute an in-scope employment decision (typically
    paired with ``aedt_in_use=False``). Use ``other`` as an escape
    hatch for edge-case fact patterns the customer's counsel
    evaluates."""

    affected_candidate_categories: List[str] = field(default_factory=list)
    """Categories of individuals affected by the AEDT (e.g.,
    ``'nyc_resident_candidates'``, ``'nyc_resident_employees'``,
    ``'healthcare_clinical_staff_candidates'``). Free-form. Stored
    comma-joined."""

    bias_audit_reference: Optional[str] = None
    """Customer-side reference to the independent bias audit record.
    Helper does NOT verify the audit exists, that the auditor was
    independent under § 20-870, or that the audit methodology is
    adequate — pure context tag."""

    bias_audit_public_url: Optional[str] = None
    """Customer-side reference to the publicly available bias-audit
    summary location, per NYC Admin Code § 20-871(b)(1)(B) and DCWP
    Final Rule 6 RCNY § 5-302. Helper does NOT verify the URL is
    publicly accessible or that the summary content satisfies
    § 5-302 — pure context tag."""

    candidate_notice_reference: Optional[str] = None
    """Customer-side reference to the candidate notice record / template
    used per § 20-871(b)(2). Helper does NOT verify the notice content,
    timing, or delivery — pure context tag."""

    aedt_distribution_date: Optional[str] = None
    """Customer-side reference to the AEDT distribution date (per DCWP
    Final Rule 6 RCNY § 5-302(a)(2)), required for the public posting.
    Helper does NOT verify the date is current — pure context tag.
    Free-form string; customer's posting publishes the canonical
    format."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'hiring_manager'``,
    ``'talent_acquisition_engineer'``, ``'people_analytics_engineer'``).
    Useful for the AuditProof activity trail and accountable-deployment
    evidence in DCWP enforcement conversations."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'NYC_LL_144'``.
        """
        return "NYC_LL_144"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'NY'`` — NYC LL 144 operates within New York State.
        """
        return _JURISDICTION_STATE

    @property
    def jurisdiction_city(self) -> str:
        """The city-jurisdiction tag emitted in metadata.

        Returns ``'NYC'`` — NYC LL 144 is by construction a New York
        City municipal helper.
        """
        return _JURISDICTION_CITY

    @property
    def decision_type_set(self) -> List[str]:
        """The canonical in-scope-decision-type taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        decision-type enumeration.
        """
        return sorted(_VALID_DECISION_TYPES)

    @property
    def employer_role_set(self) -> List[str]:
        """The canonical employer-role taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        employer-role enumeration.
        """
        return sorted(_VALID_EMPLOYER_ROLES)

    def __post_init__(self) -> None:
        if not self.employer_organization:
            raise ValueError(
                "employer_organization is required and must be non-empty"
            )
        if not self.employer_role:
            raise ValueError(
                "employer_role is required and must be non-empty"
            )
        if self.employer_role not in _VALID_EMPLOYER_ROLES:
            raise ValueError(
                f"employer_role={self.employer_role!r} not in "
                f"{sorted(_VALID_EMPLOYER_ROLES)} "
                "(NYC LL 144-aligned actor roles)"
            )

        # Required booleans — reject non-bool truthy values customers
        # may pass accidentally (e.g., the string 'true' or int 1).
        if not isinstance(self.aedt_in_use, bool):
            raise ValueError(
                "aedt_in_use must be a bool (got "
                f"{type(self.aedt_in_use).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.bias_audit_completed, bool):
            raise ValueError(
                "bias_audit_completed must be a bool (got "
                f"{type(self.bias_audit_completed).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.candidate_notice_provided, bool):
            raise ValueError(
                "candidate_notice_provided must be a bool (got "
                f"{type(self.candidate_notice_provided).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if not self.decision_type:
            raise ValueError(
                "decision_type is required and must be non-empty"
            )
        if self.decision_type not in _VALID_DECISION_TYPES:
            raise ValueError(
                f"decision_type={self.decision_type!r} not in "
                f"{sorted(_VALID_DECISION_TYPES)} "
                "(NYC LL 144 in-scope decision taxonomy)"
            )

        if self.affected_candidate_categories:
            for idx, item in enumerate(
                self.affected_candidate_categories
            ):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_candidate_categories[{idx}] must be "
                        f"a string (got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_candidate_categories[{idx}] must "
                        "be non-empty"
                    )
            validate_no_comma_in_list(
                "affected_candidate_categories",
                list(self.affected_candidate_categories),
            )

        validate_value_length(
            "employer_organization", self.employer_organization
        )
        validate_value_length("employer_role", self.employer_role)
        validate_value_length("decision_type", self.decision_type)
        if self.affected_candidate_categories:
            joined = ",".join(self.affected_candidate_categories)
            validate_value_length(
                "affected_candidate_categories(joined)", joined
            )
        if self.bias_audit_reference is not None:
            validate_value_length(
                "bias_audit_reference", self.bias_audit_reference
            )
        if self.bias_audit_public_url is not None:
            validate_value_length(
                "bias_audit_public_url", self.bias_audit_public_url
            )
        if self.candidate_notice_reference is not None:
            validate_value_length(
                "candidate_notice_reference",
                self.candidate_notice_reference,
            )
        if self.aedt_distribution_date is not None:
            validate_value_length(
                "aedt_distribution_date", self.aedt_distribution_date
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
        ``jurisdiction_state="NY"`` + ``jurisdiction_city="NYC"``
        flags plus three load-bearing booleans (``aedt_in_use``,
        ``bias_audit_completed``, ``candidate_notice_provided``) as
        lowercase string ``"true"`` / ``"false"`` values.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "employer_organization": self.employer_organization,
            "employer_role": self.employer_role,
            "aedt_in_use": (
                "true" if self.aedt_in_use else "false"
            ),
            "bias_audit_completed": (
                "true" if self.bias_audit_completed else "false"
            ),
            "candidate_notice_provided": (
                "true"
                if self.candidate_notice_provided
                else "false"
            ),
            "decision_type": self.decision_type,
            "jurisdiction_state": _JURISDICTION_STATE,
            "jurisdiction_city": _JURISDICTION_CITY,
        }
        if self.affected_candidate_categories:
            meta["affected_candidate_categories"] = ",".join(
                self.affected_candidate_categories
            )
        if self.bias_audit_reference is not None:
            meta["bias_audit_reference"] = self.bias_audit_reference
        if self.bias_audit_public_url is not None:
            meta["bias_audit_public_url"] = self.bias_audit_public_url
        if self.candidate_notice_reference is not None:
            meta["candidate_notice_reference"] = (
                self.candidate_notice_reference
            )
        if self.aedt_distribution_date is not None:
            meta["aedt_distribution_date"] = self.aedt_distribution_date
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
