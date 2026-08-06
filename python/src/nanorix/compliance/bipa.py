"""BIPA (Illinois Biometric Information Privacy Act) context for capsule workloads.

The Illinois Biometric Information Privacy Act (BIPA), codified at
740 ILCS 14 and operative since October 3, 2008, is the strictest US
state-level biometric privacy statute. BIPA imposes affirmative
obligations on any private entity that collects, captures, purchases,
receives, or otherwise obtains an individual's biometric identifier or
biometric information.

BIPA is materially differentiated from other US privacy statutes by
its **private right of action** at 740 ILCS 14/20. Individuals can sue
directly for statutory damages of $1,000 per negligent violation or
$5,000 per intentional or reckless violation, plus attorneys' fees and
injunctive relief. The Illinois Supreme Court has held that a "person
aggrieved" need not allege actual injury beyond the statutory
violation itself (Rosenbach v. Six Flags Entertainment Corp., 2019
IL 123186). This makes BIPA the most litigated US biometric privacy
statute and a material risk consideration for any workload processing
Illinois-resident biometric data.

The statute defines:

- **Biometric identifier** (§14/10): retina or iris scan, fingerprint,
  voiceprint, or scan of hand or face geometry. The statute explicitly
  excludes writing samples, written signatures, photographs,
  demographic data, tattoos, physical descriptions, and certain
  healthcare-context biometrics gathered in connection with covered
  treatment.
- **Biometric information** (§14/10): any information based on a
  biometric identifier used to identify an individual.

The statute imposes (§14/15):

- Written informed consent BEFORE biometric collection (§14/15(b))
- Publicly available written retention and destruction policy
  (§14/15(a))
- Biometric data destruction within 3 years of the individual's last
  interaction with the entity OR purpose-completion (whichever first)
- Prohibition on selling / leasing / trading biometric data (§14/15(c))
- Disclosure restrictions absent consent (§14/15(d))
- Reasonable care + industry-standard protection (§14/15(e))

Healthcare AI workloads with biometric components — particularly voice
biomarker AI (Canary Speech and competitor cohorts), facial recognition
(diagnostic imaging cohorts), and iris-based patient identification —
have material BIPA exposure when serving Illinois patients. The
statute's healthcare exemption applies only to biometric identifiers
collected in connection with covered Healthcare Information privacy
regulations, and the scope of that exemption is litigated.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to satisfaction of, or claim compliance with BIPA.
    Consent-acquisition, retention-policy authoring + publication,
    destruction-cadence enforcement, and private-right-of-action
    litigation defense remain the customer's regulatory counsel's
    responsibility. The Illinois Attorney General has parallel
    enforcement authority but the dominant enforcement mechanism is
    private litigation.

State-jurisdictional helpers cross-vertical: a single workload may
fall under both BIPA (state-jurisdictional, biometric-specific) and a
vertical-specific framework. Healthcare AI biometric workloads
commonly pair :class:`BIPAContext` with :class:`HipaaContext`; SaaS
authentication workloads pair with :class:`Soc2Context`.

Companion posture document: ``docs/compliance/bipa.md``.

Note: BIPA litigation case law continues to develop; the operative
statutory provisions are stable but enforcement-position interpretations
shift. The framework_version field is a non-calendar operative-version
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

_FRAMEWORK_VERSION = "2008-operative"
"""BIPA operative version identifier. 740 ILCS 14 became operative on
October 3, 2008. The version string is a non-calendar operative-
version identifier; bump when the statute is amended."""

# BIPA is by construction an Illinois-jurisdictional helper.
_JURISDICTION_STATE = "IL"

_VALID_BIOMETRIC_IDENTIFIER_TYPES = frozenset(
    {
        "retina_iris_scan",
        "fingerprint",
        "voiceprint",
        "hand_geometry_scan",
        "face_geometry_scan",
        "biometric_information_derived",
        "no_biometrics_processed",
        "other",
    }
)
"""§14/10 biometric identifier categories. ``biometric_information_
derived`` covers information derived from a biometric identifier (the
§14/10 'biometric information' definition); ``no_biometrics_
processed`` covers workloads whose customer has determined no
biometric identifiers or biometric information are in scope; ``other``
is an escape hatch for biometric modalities not enumerated in the
statute."""

_VALID_CONSENT_BASES = frozenset(
    {
        "written_consent_obtained",
        "healthcare_exemption_claimed",
        "court_ordered",
        "no_consent_required",
        "consent_pending",
        "other",
    }
)
"""§14/15(b) consent-basis taxonomy. ``written_consent_obtained`` is
the dominant compliant basis. ``healthcare_exemption_claimed`` covers
the §14/10 healthcare-context exclusion (scope is litigated; customer
counsel determines applicability). ``no_consent_required`` covers
workloads whose customer has determined no biometric collection is
occurring (typically paired with ``biometric_identifier_type =
'no_biometrics_processed'``). ``consent_pending`` and ``other`` are
escape hatches for in-flight or non-standard fact patterns."""


@dataclass(frozen=True)
class BIPAContext:
    """BIPA context for a capsule workload.

    Declares context for tagging this workload under the Illinois
    Biometric Information Privacy Act (740 ILCS 14). Does NOT certify,
    attest to satisfaction of, or claim compliance with BIPA.
    Consent-acquisition, retention-policy authoring + publication,
    destruction-cadence enforcement, and private-right-of-action
    litigation defense remain the customer's regulatory counsel's
    responsibility.

    .. warning::

        BIPA has a private right of action (740 ILCS 14/20). Statutory
        damages are $1,000 per negligent violation or $5,000 per
        intentional or reckless violation, plus attorneys' fees. The
        Illinois Supreme Court has held that a "person aggrieved" need
        not allege actual injury beyond the statutory violation itself
        (Rosenbach v. Six Flags, 2019 IL 123186). This is materially
        different from other US privacy statutes and is the dominant
        BIPA risk consideration.

    Common usage for healthcare AI workloads with biometric components
    — particularly voice biomarker AI (Canary Speech post-Kintsugi
    cohort), facial recognition diagnostic imaging, and iris-based
    patient identification — and for authentication / identity-
    verification SaaS workloads serving Illinois residents. Pair with
    :class:`HipaaContext` for healthcare biometric workloads; the
    §14/10 healthcare exemption scope is litigated and customer
    counsel determines applicability.

    The helper emits a structural ``jurisdiction_state="IL"`` flag,
    a ``biometric_data_in_scope`` boolean, a ``consent_obtained``
    boolean for the §14/15(b) requirement, and a
    ``retention_policy_documented`` boolean for the §14/15(a)
    requirement.

    See companion posture document: ``docs/compliance/bipa.md``.
    """

    collecting_entity: str
    """The private entity collecting, capturing, purchasing, receiving,
    or otherwise obtaining biometric identifiers or biometric
    information. Required."""

    biometric_data_in_scope: bool
    """Whether the workload processes Illinois-resident biometric
    identifiers or biometric information. Required boolean — emitted
    as the string ``"true"`` or ``"false"`` in the metadata dict.

    The scope determination is the customer's; the helper records the
    customer's declared scope. Helper does NOT verify the workload's
    actual biometric handling. Where ``biometric_data_in_scope=False``,
    the helper still emits the BIPA framework tag — useful when a
    customer wants to record their cohort-affirmative non-applicability
    determination for audit-trail purposes."""

    consent_obtained: bool
    """Whether the customer has obtained §14/15(b) written informed
    consent before biometric collection in this workload. Required
    boolean — emitted as the string ``"true"`` or ``"false"`` in the
    metadata dict.

    The consent determination is the customer's; the helper records
    the customer's declared status. Helper does NOT verify consent
    records exist or are valid under §14/15(b). Where the customer
    relies on a basis other than written consent (e.g., the §14/10
    healthcare exemption), see ``consent_basis`` for the customer-
    declared alternative."""

    retention_policy_documented: bool
    """Whether the customer has a publicly available written
    biometric data retention and destruction policy per §14/15(a).
    Required boolean — emitted as the string ``"true"`` or
    ``"false"`` in the metadata dict.

    The retention-policy determination is the customer's; the helper
    records the customer's declared status. Helper does NOT verify
    the policy exists, is published, is current, or aligns with the
    §14/15(a) 3-year + purpose-completion destruction requirement."""

    biometric_identifier_type: str
    """The §14/10 biometric identifier category for biometric data
    processed by this workload. Must be one of the values in
    :attr:`biometric_identifier_type_set`. Required.

    Use ``no_biometrics_processed`` when the customer has determined
    no biometric identifiers or biometric information are in scope
    (typically paired with ``biometric_data_in_scope=False``)."""

    consent_basis: Optional[str] = None
    """The customer-declared §14/15(b) consent basis. Must be one of
    the values in :attr:`consent_basis_set` when provided. Optional;
    customers often declare ``written_consent_obtained`` paired with
    ``consent_obtained=True``. Use ``healthcare_exemption_claimed``
    when the customer relies on the §14/10 healthcare-context
    exclusion (scope litigated)."""

    affected_individual_categories: List[str] = field(
        default_factory=list
    )
    """Categories of individuals whose biometric identifiers or
    biometric information are processed (e.g.,
    ``'illinois_residents'``, ``'illinois_patients'``,
    ``'illinois_employees'``, ``'illinois_voice_biomarker_subjects'``).
    Free-form. Stored comma-joined."""

    consent_record_reference: Optional[str] = None
    """Customer-side reference to the §14/15(b) written-consent
    records. Helper does NOT verify consent records exist or satisfy
    statutory form requirements — pure context tag."""

    retention_policy_reference: Optional[str] = None
    """Customer-side reference to the publicly available §14/15(a)
    retention and destruction policy. Helper does NOT verify the
    policy is publicly available or current — pure context tag."""

    destruction_cadence_reference: Optional[str] = None
    """Customer-side reference to the §14/15(a) destruction cadence
    record (3-year-since-last-interaction OR purpose-completion,
    whichever first). Nanorix's substrate destruction chain provides
    related-to evidence for purpose-completion destruction; the
    customer's cadence-enforcement records the broader retention
    policy. Helper does NOT verify cadence is enforced — pure context
    tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'voice_biomarker_engineer'``,
    ``'biometric_authentication_engineer'``). Useful for the AuditProof
    activity trail and accountable-deployment evidence in BIPA
    litigation discovery."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``'BIPA'``.
        """
        return "BIPA"

    @property
    def jurisdiction_state(self) -> str:
        """The state-jurisdiction abbreviation emitted in metadata.

        Returns ``'IL'`` — BIPA is by construction an Illinois-
        jurisdictional helper.
        """
        return _JURISDICTION_STATE

    @property
    def biometric_identifier_type_set(self) -> List[str]:
        """The canonical §14/10 biometric-identifier-type taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        biometric-identifier-type enumeration.
        """
        return sorted(_VALID_BIOMETRIC_IDENTIFIER_TYPES)

    @property
    def consent_basis_set(self) -> List[str]:
        """The canonical §14/15(b) consent-basis taxonomy.

        Returns a copy. Useful for customer introspection of the valid
        consent-basis enumeration.
        """
        return sorted(_VALID_CONSENT_BASES)

    def __post_init__(self) -> None:
        if not self.collecting_entity:
            raise ValueError(
                "collecting_entity is required and must be non-empty"
            )

        # Required booleans — reject non-bool truthy values customers
        # may pass accidentally (e.g., the string 'true').
        if not isinstance(self.biometric_data_in_scope, bool):
            raise ValueError(
                "biometric_data_in_scope must be a bool (got "
                f"{type(self.biometric_data_in_scope).__name__}); "
                "pass True or False, not the string 'true'"
            )
        if not isinstance(self.consent_obtained, bool):
            raise ValueError(
                "consent_obtained must be a bool (got "
                f"{type(self.consent_obtained).__name__}); pass True "
                "or False, not the string 'true'"
            )
        if not isinstance(self.retention_policy_documented, bool):
            raise ValueError(
                "retention_policy_documented must be a bool (got "
                f"{type(self.retention_policy_documented).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if not self.biometric_identifier_type:
            raise ValueError(
                "biometric_identifier_type is required and must be "
                "non-empty"
            )
        if (
            self.biometric_identifier_type
            not in _VALID_BIOMETRIC_IDENTIFIER_TYPES
        ):
            raise ValueError(
                "biometric_identifier_type="
                f"{self.biometric_identifier_type!r} not in "
                f"{sorted(_VALID_BIOMETRIC_IDENTIFIER_TYPES)} "
                "(§14/10 biometric-identifier taxonomy)"
            )

        if self.consent_basis is not None:
            if not self.consent_basis:
                raise ValueError(
                    "consent_basis if provided must be non-empty"
                )
            if self.consent_basis not in _VALID_CONSENT_BASES:
                raise ValueError(
                    f"consent_basis={self.consent_basis!r} not in "
                    f"{sorted(_VALID_CONSENT_BASES)} "
                    "(§14/15(b) consent-basis taxonomy)"
                )

        if self.affected_individual_categories:
            for idx, item in enumerate(
                self.affected_individual_categories
            ):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_individual_categories[{idx}] must "
                        f"be a string (got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_individual_categories[{idx}] must "
                        "be non-empty"
                    )
            validate_no_comma_in_list(
                "affected_individual_categories",
                list(self.affected_individual_categories),
            )

        validate_value_length(
            "collecting_entity", self.collecting_entity
        )
        validate_value_length(
            "biometric_identifier_type",
            self.biometric_identifier_type,
        )
        if self.consent_basis is not None:
            validate_value_length(
                "consent_basis", self.consent_basis
            )
        if self.affected_individual_categories:
            joined = ",".join(self.affected_individual_categories)
            validate_value_length(
                "affected_individual_categories(joined)", joined
            )
        if self.consent_record_reference is not None:
            validate_value_length(
                "consent_record_reference",
                self.consent_record_reference,
            )
        if self.retention_policy_reference is not None:
            validate_value_length(
                "retention_policy_reference",
                self.retention_policy_reference,
            )
        if self.destruction_cadence_reference is not None:
            validate_value_length(
                "destruction_cadence_reference",
                self.destruction_cadence_reference,
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
        ``jurisdiction_state="IL"`` flag plus three load-bearing
        booleans (``biometric_data_in_scope``, ``consent_obtained``,
        ``retention_policy_documented``) as lowercase string
        ``"true"`` / ``"false"`` values.

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash`` per ADR-010 A4).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "collecting_entity": self.collecting_entity,
            "biometric_data_in_scope": (
                "true" if self.biometric_data_in_scope else "false"
            ),
            "consent_obtained": (
                "true" if self.consent_obtained else "false"
            ),
            "retention_policy_documented": (
                "true"
                if self.retention_policy_documented
                else "false"
            ),
            "biometric_identifier_type": self.biometric_identifier_type,
            "jurisdiction_state": _JURISDICTION_STATE,
        }
        if self.consent_basis is not None:
            meta["consent_basis"] = self.consent_basis
        if self.affected_individual_categories:
            meta["affected_individual_categories"] = ",".join(
                self.affected_individual_categories
            )
        if self.consent_record_reference is not None:
            meta["consent_record_reference"] = (
                self.consent_record_reference
            )
        if self.retention_policy_reference is not None:
            meta["retention_policy_reference"] = (
                self.retention_policy_reference
            )
        if self.destruction_cadence_reference is not None:
            meta["destruction_cadence_reference"] = (
                self.destruction_cadence_reference
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
