"""FDA Software as a Medical Device (SaMD) framework context for capsule workloads.

The US Food and Drug Administration's Software as a Medical Device
(SaMD) framework, defined at
<https://www.fda.gov/medical-devices/digital-health-center-excellence/software-medical-device-samd>
and aligned with the International Medical Device Regulators Forum
(IMDRF) SaMD risk-categorization framework, governs software intended
for one or more medical purposes that performs those purposes without
being part of a hardware medical device. The framework covers AI/ML-
enabled clinical decision support, voice biomarker diagnostics, image-
analysis software, and other software-only medical-device functions.

US device classification follows 21 CFR Part 860:

- **Class I**: lowest risk; typically 510(k)-exempt or subject to
  general controls only.
- **Class II**: moderate risk; predominantly 510(k) premarket
  notification path with special controls.
- **Class III**: highest risk; Premarket Approval (PMA) required;
  life-supporting / life-sustaining / substantial-importance devices.

Pre-market submission pathways:

- **510(k) Premarket Notification** (21 CFR Part 807): demonstration
  of substantial equivalence to a legally marketed predicate device.
  Once accepted, FDA issues a K-number (format ``K\\d{6}``).
- **De Novo Classification Request** (21 CFR Part 860 Subpart D): for
  novel low-to-moderate-risk devices without a predicate. Once
  granted, FDA issues a DEN-number (format ``DEN\\d{6}``).
- **Premarket Approval (PMA)**: high-risk Class III devices; full
  safety + effectiveness demonstration.
- **Exempt**: certain Class I (and a limited subset of Class II)
  devices exempt from 510(k) per 21 CFR Parts 862-892.

For AI-enabled device software functions, FDA issued the
*Artificial Intelligence-Enabled Device Software Functions: Lifecycle
Management and Marketing Submissions Recommendations* draft guidance
(89 FR 1543, Jan 7 2025;
<https://www.federalregister.gov/documents/2025/01/07/2024-31543>)
which addresses Predetermined Change Control Plans (PCCP),
post-market performance monitoring, and emerging expectations around
subgroup performance disclosure. As of 2026-05-10, the 2025 draft
guidance has not been finalized — subgroup performance disclosure
is an **emerging FDA expectation tied to the draft**, not a finalized
FDA mandate. The helper records the customer-declared disclosure
status without asserting that disclosure is statutorily required.

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's metadata. It does NOT
    certify, attest to, or claim FDA clearance, approval, or any
    pre-market submission status for the customer's software. K-number
    / DEN-number authentication, submission-pathway selection,
    intended-use / indications-for-use authoring, clinical validation,
    and post-market surveillance remain the customer's regulatory
    affairs counsel + clinical team's responsibility. The FDA's
    Center for Devices and Radiological Health (CDRH) is the
    regulatory authority; helper does NOT substitute for direct
    interaction with CDRH or Q-submission processes.

Composition with other helpers:

The FDA SaMD framework does NOT supersede HIPAA, 42 CFR Part 2, or
state privacy laws (CCPA/CPRA/BIPA). Healthcare-AI device software
typically requires composition with multiple helpers:

- :class:`HipaaContext` for PHI handling under 45 CFR Parts 160-164
- :class:`CFRPart2Context` for substance-use-disorder workloads
- :class:`NISTAIRMFContext` for AI risk management (subgroup
  performance disclosure tied to NIST AI RMF Measure-2.11 fairness
  evaluation; composes with FDA AI/ML 2025 draft expectations)
- :class:`BIPAContext` for Illinois-resident biometric workloads
  (voice biomarker, facial recognition diagnostic imaging)
- :class:`ColoradoAIActContext` for Colorado-jurisdictional
  Consequential Decisions in healthcare

The framework is cross-vertical: FDA SaMD applies to all medical
device software regardless of clinical specialty (radiology, voice
biomarkers, behavioral health, dental imaging, etc.).

Companion posture document: ``docs/healthcare/fda-samd-pathway.md``.

Note: FDA regulatory positions and draft guidance evolve. The
``framework_version`` field is a non-calendar adapter-baseline
identifier; bump when the underlying statutory or regulatory
framework changes materially.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from nanorix.compliance._constraints import (
    MAX_METADATA_KEYS,
    RESERVED_FRAMEWORK_KEY,
    RESERVED_FRAMEWORK_VERSION_KEY,
    estimate_serialized_size,
    validate_value_length,
)

_FRAMEWORK_VERSION = "2026-02"
"""FDA SaMD framework-baseline identifier. The framework string is a
non-calendar adapter-baseline identifier; bump when the underlying
regulatory framework or FDA guidance changes materially. As of 2026-05
the AI-Enabled Device Software Functions 2025 draft guidance is in
public comment and has NOT been finalized."""

_VALID_DEVICE_CLASSES = frozenset({"I", "II", "III"})
"""21 CFR Part 860 device classification taxonomy. Class I = lowest
risk; Class II = moderate risk (most software-only medical-device
functions); Class III = highest risk (life-supporting /
life-sustaining)."""

_VALID_SUBMISSION_PATHWAYS = frozenset(
    {"510k", "de_novo", "pma", "exempt"}
)
"""FDA pre-market submission pathway taxonomy.

- ``510k``: Premarket Notification (21 CFR Part 807); substantial
  equivalence to a legally marketed predicate device.
- ``de_novo``: De Novo Classification Request (21 CFR Part 860
  Subpart D); novel low-to-moderate-risk devices without a
  predicate.
- ``pma``: Premarket Approval (21 CFR Part 814); high-risk Class
  III devices.
- ``exempt``: 510(k)-exempt under 21 CFR Parts 862-892."""

_VALID_SUBMISSION_STATUSES = frozenset(
    {
        "pre_submission",
        "submitted",
        "in_review",
        "cleared",
        "approved",
        "denied",
        "withdrawn",
    }
)
"""FDA pre-market submission lifecycle taxonomy.

- ``pre_submission``: Customer drafting; not yet filed with FDA.
- ``submitted``: Filed with FDA; awaiting acknowledgment / review.
- ``in_review``: Active FDA review.
- ``cleared``: 510(k) cleared OR De Novo granted (issuance of
  K-number or DEN-number).
- ``approved``: PMA approved.
- ``denied``: FDA decision adverse to clearance / approval.
- ``withdrawn``: Customer withdrew submission."""

_VALID_CLINICAL_VALIDATION_STATUSES = frozenset(
    {"not_started", "in_progress", "completed"}
)
"""Clinical validation phase taxonomy. Records the customer-declared
clinical-validation status; helper does NOT verify validation
artifacts exist or are statistically sufficient."""

# FDA-assigned identifiers. K-number is 6 digits (e.g., K231234);
# DEN-number is 6 digits prefixed DEN (e.g., DEN230001).
_K_NUMBER_PATTERN = re.compile(r"^K\d{6}$")
_DEN_NUMBER_PATTERN = re.compile(r"^DEN\d{6}$")


@dataclass(frozen=True)
class FDASaMDContext:
    """FDA Software as a Medical Device (SaMD) framework context for a
    capsule workload.

    Declares customer-supplied context for tagging this workload under
    the FDA SaMD framework + 21 CFR Part 860 device classification +
    pre-market submission pathway taxonomy. Does NOT certify, attest
    to, or claim FDA clearance, approval, or any pre-market submission
    status. K-number / DEN-number authentication, submission-pathway
    selection, intended-use / indications-for-use authoring, clinical
    validation, and post-market surveillance remain the customer's
    regulatory affairs counsel + clinical team's responsibility.

    .. warning::

        FDA clearance / approval status is a regulatory determination
        made by FDA's Center for Devices and Radiological Health
        (CDRH), not by this helper or by Nanorix. A
        ``submission_status="cleared"`` declaration records the
        customer's declared status only; downstream evidence consumers
        verify clearance via FDA's public databases
        (510(k) Premarket Notification, De Novo Classification, PMA).

    The helper records:

    - **Device class** (Class I / II / III per 21 CFR Part 860)
    - **Submission pathway** (510(k) / De Novo / PMA / Exempt)
    - **Submission status** (pre-submission through cleared / approved
      / denied / withdrawn)
    - **Intended use** (customer-declared statement)
    - **Indications for use** (optional; per FDA Form 3881 conventions)
    - **Clinical validation status** (not started / in progress /
      completed)
    - **K-number** (optional; FDA-assigned 510(k) identifier)
    - **DEN-number** (optional; FDA-assigned De Novo identifier)
    - **Subgroup performance disclosed** (optional; per FDA AI/ML
      2025 draft + NIST AI RMF Measure-2.11 emerging expectation —
      NOT a finalized FDA mandate as of 2026-05)

    Cross-vertical: FDA SaMD applies to all medical device software
    regardless of clinical specialty. Common compositions:

    - **Healthcare AI** (voice biomarker, image analysis, clinical
      decision support): pair with :class:`HipaaContext` +
      :class:`NISTAIRMFContext`
    - **Illinois-resident biometric SaMD** (voice biomarker
      cohorts): add :class:`BIPAContext` for the §14/20 private
      right of action
    - **Substance-use SaMD** (behavioral health screening): add
      :class:`CFRPart2Context` for 42 CFR Part 2 SUD-records
      protections
    - **Colorado-jurisdictional Consequential Decisions**: add
      :class:`ColoradoAIActContext` for state AI-law deployer /
      developer obligations

    The helper is cross-vertical — healthcare AI is the lead beachhead
    but the framework applies to all FDA-regulated medical device
    software. See companion posture document:
    ``docs/healthcare/fda-samd-pathway.md``.
    """

    device_class: str
    """21 CFR Part 860 device class. Must be one of ``"I"``, ``"II"``,
    or ``"III"``. Required.

    Class determination is the customer's regulatory affairs counsel's
    responsibility; helper records the customer-declared class only.
    Helper does NOT verify the class against FDA's product-code
    database or the device-classification regulation under 21 CFR
    Parts 862-892."""

    submission_pathway: str
    """FDA pre-market submission pathway. Must be one of the values in
    :attr:`submission_pathway_set`. Required.

    Pathway selection is the customer's responsibility; helper records
    the customer-declared pathway only. Helper does NOT verify the
    pathway is appropriate for the device class + intended use."""

    submission_status: str
    """Current FDA submission lifecycle status. Must be one of the
    values in :attr:`submission_status_set`. Required.

    Status determination is the customer's responsibility; helper
    records the customer-declared status only. Helper does NOT verify
    the status against FDA's submission databases. A ``"cleared"`` or
    ``"approved"`` declaration must be verifiable by downstream
    evidence consumers via FDA public databases (which surface
    K-number / DEN-number / PMA-number references)."""

    intended_use: str
    """Customer-declared statement of intended use. Required non-empty
    string.

    The statement of intended use is the customer's authoring; helper
    records the declared statement only. Helper does NOT verify the
    statement aligns with the device's actual function, the cleared
    intended use (post-510(k)), or any FDA Form 3881 / 510(k) summary
    submitted to FDA."""

    indications_for_use: Optional[str] = None
    """Customer-declared indications for use per FDA Form 3881
    conventions. Optional.

    Indications for use is a more specific framing than intended use:
    it describes the disease or condition the device will diagnose,
    treat, prevent, cure, or mitigate, the target patient population,
    and (where applicable) the part of the body or type of user.
    Customer-side authoring; helper does NOT verify."""

    clinical_validation_status: Optional[str] = None
    """Clinical validation phase. Must be one of
    :attr:`clinical_validation_status_set` when provided. Optional.

    Clinical validation is the customer's responsibility; helper
    records the declared phase only. Helper does NOT verify validation
    artifacts exist, are statistically sufficient, or align with FDA
    expectations for the submission pathway."""

    k_number: Optional[str] = None
    """FDA-assigned K-number once a 510(k) is accepted. Format:
    ``K\\d{6}`` (e.g., ``K231234``). Optional; only meaningful when
    ``submission_pathway="510k"`` and ``submission_status`` is one of
    ``submitted`` / ``in_review`` / ``cleared``.

    K-number authentication is the customer's responsibility; helper
    validates only the structural format. Helper does NOT verify the
    K-number against FDA's public 510(k) database."""

    de_novo_number: Optional[str] = None
    """FDA-assigned DEN-number once a De Novo classification request
    is granted. Format: ``DEN\\d{6}`` (e.g., ``DEN230001``). Optional;
    only meaningful when ``submission_pathway="de_novo"`` and
    ``submission_status`` is one of ``submitted`` / ``in_review`` /
    ``cleared``.

    DEN-number authentication is the customer's responsibility; helper
    validates only the structural format. Helper does NOT verify the
    DEN-number against FDA's public De Novo database."""

    subgroup_performance_disclosed: Optional[bool] = None
    """Whether subgroup-stratified performance metrics (across age,
    sex, race, ethnicity, or other relevant covariates) have been
    disclosed for this device. Optional boolean — emitted as the
    string ``"true"`` or ``"false"`` in the metadata dict when set.

    .. note::

        Subgroup performance disclosure is an **emerging FDA
        expectation** tied to the AI-Enabled Device Software Functions
        2025 draft guidance (89 FR 1543, Jan 7 2025) and NIST AI RMF
        Measure-2.11 fairness evaluation — **not a finalized FDA
        mandate** as of 2026-05. The helper records the customer-
        declared disclosure status without asserting that disclosure
        is statutorily required.

    The disclosure determination is the customer's responsibility;
    helper does NOT verify disclosure artifacts exist or meet emerging
    FDA / NIST expectations."""

    predicate_device_reference: Optional[str] = None
    """Customer-side reference to the predicate device used for
    substantial-equivalence demonstration in a 510(k) submission.
    Optional; only meaningful when ``submission_pathway="510k"``.

    Predicate selection is the customer's responsibility; helper does
    NOT verify the predicate is legally marketed or appropriate for
    substantial-equivalence demonstration."""

    quality_system_reference: Optional[str] = None
    """Customer-side reference to the 21 CFR Part 820 Quality System
    Regulation (QSR) records relevant to this device. Optional.

    QSR conformance is the customer's responsibility; helper does NOT
    verify QSR records exist or are current."""

    submission_owner_role: Optional[str] = None
    """Who initiated this submission lifecycle stage (e.g.,
    ``"regulatory_affairs_lead"``, ``"clinical_validation_engineer"``,
    ``"medical_director"``). Useful for the AuditProof activity trail
    and accountable-deployment evidence in FDA inspection / 483
    response contexts."""

    affected_device_categories: List[str] = field(default_factory=list)
    """Customer-declared device category descriptors (e.g.,
    ``"voice_biomarker_diagnostic"``, ``"ai_clinical_decision_support"``,
    ``"radiology_image_analysis"``, ``"dental_caries_detection"``).
    Free-form. Stored comma-joined.

    Useful for routing AuditProof evidence to category-specific
    downstream consumers (FDA inspectors, third-party assessors,
    customer's medical director)."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier ``"FDA_SaMD"``.
        """
        return "FDA_SaMD"

    @property
    def framework_version(self) -> str:
        """The framework-baseline version emitted in metadata.

        Returns ``"2026-02"`` — non-calendar adapter-baseline
        identifier.
        """
        return _FRAMEWORK_VERSION

    @property
    def device_class_set(self) -> List[str]:
        """The canonical 21 CFR Part 860 device-class taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_DEVICE_CLASSES)

    @property
    def submission_pathway_set(self) -> List[str]:
        """The canonical FDA pre-market submission pathway taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_SUBMISSION_PATHWAYS)

    @property
    def submission_status_set(self) -> List[str]:
        """The canonical FDA submission lifecycle status taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_SUBMISSION_STATUSES)

    @property
    def clinical_validation_status_set(self) -> List[str]:
        """The canonical clinical validation phase taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_CLINICAL_VALIDATION_STATUSES)

    @property
    def is_high_risk_class(self) -> bool:
        """Whether the device is Class III (highest risk under 21 CFR
        Part 860)."""
        return self.device_class == "III"

    @property
    def is_cleared_or_approved(self) -> bool:
        """Whether the submission has reached cleared (510(k) / De
        Novo) or approved (PMA) status.

        Note: this property reflects the customer-declared
        ``submission_status`` only; helper does NOT verify the status
        against FDA's public databases."""
        return self.submission_status in {"cleared", "approved"}

    def __post_init__(self) -> None:
        if not self.device_class:
            raise ValueError(
                "device_class is required and must be non-empty"
            )
        if self.device_class not in _VALID_DEVICE_CLASSES:
            raise ValueError(
                f"device_class={self.device_class!r} not in "
                f"{sorted(_VALID_DEVICE_CLASSES)} "
                "(21 CFR Part 860 device-class taxonomy)"
            )

        if not self.submission_pathway:
            raise ValueError(
                "submission_pathway is required and must be non-empty"
            )
        if self.submission_pathway not in _VALID_SUBMISSION_PATHWAYS:
            raise ValueError(
                f"submission_pathway={self.submission_pathway!r} not in "
                f"{sorted(_VALID_SUBMISSION_PATHWAYS)} "
                "(FDA pre-market submission pathway taxonomy)"
            )

        if not self.submission_status:
            raise ValueError(
                "submission_status is required and must be non-empty"
            )
        if self.submission_status not in _VALID_SUBMISSION_STATUSES:
            raise ValueError(
                f"submission_status={self.submission_status!r} not in "
                f"{sorted(_VALID_SUBMISSION_STATUSES)} "
                "(FDA submission lifecycle taxonomy)"
            )

        if not self.intended_use:
            raise ValueError(
                "intended_use is required and must be non-empty"
            )

        if self.clinical_validation_status is not None:
            if not self.clinical_validation_status:
                raise ValueError(
                    "clinical_validation_status if provided must be "
                    "non-empty"
                )
            if (
                self.clinical_validation_status
                not in _VALID_CLINICAL_VALIDATION_STATUSES
            ):
                raise ValueError(
                    "clinical_validation_status="
                    f"{self.clinical_validation_status!r} not in "
                    f"{sorted(_VALID_CLINICAL_VALIDATION_STATUSES)}"
                )

        if self.k_number is not None:
            if not self.k_number:
                raise ValueError(
                    "k_number if provided must be non-empty"
                )
            if not _K_NUMBER_PATTERN.fullmatch(self.k_number):
                raise ValueError(
                    f"k_number={self.k_number!r} must match format "
                    r"'K\d{6}' (e.g., 'K231234'); helper validates "
                    "structural format only, does NOT verify against "
                    "FDA's 510(k) database"
                )

        if self.de_novo_number is not None:
            if not self.de_novo_number:
                raise ValueError(
                    "de_novo_number if provided must be non-empty"
                )
            if not _DEN_NUMBER_PATTERN.fullmatch(self.de_novo_number):
                raise ValueError(
                    f"de_novo_number={self.de_novo_number!r} must match "
                    r"format 'DEN\d{6}' (e.g., 'DEN230001'); helper "
                    "validates structural format only, does NOT verify "
                    "against FDA's De Novo database"
                )

        if self.subgroup_performance_disclosed is not None and not (
            isinstance(self.subgroup_performance_disclosed, bool)
        ):
            raise ValueError(
                "subgroup_performance_disclosed must be a bool when "
                "provided (got "
                f"{type(self.subgroup_performance_disclosed).__name__})"
                "; pass True or False, not the string 'true'. Note: "
                "subgroup disclosure is an emerging FDA AI/ML 2025 "
                "draft expectation, not a finalized mandate"
            )

        if self.affected_device_categories:
            for idx, item in enumerate(self.affected_device_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_device_categories[{idx}] must be a "
                        f"string (got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_device_categories[{idx}] must be "
                        "non-empty"
                    )
                if "," in item:
                    raise ValueError(
                        f"affected_device_categories[{idx}]={item!r} "
                        "contains ',' which is the comma-join "
                        "separator; remove or escape before passing"
                    )

        validate_value_length("device_class", self.device_class)
        validate_value_length(
            "submission_pathway", self.submission_pathway
        )
        validate_value_length(
            "submission_status", self.submission_status
        )
        validate_value_length("intended_use", self.intended_use)
        if self.indications_for_use is not None:
            validate_value_length(
                "indications_for_use", self.indications_for_use
            )
        if self.clinical_validation_status is not None:
            validate_value_length(
                "clinical_validation_status",
                self.clinical_validation_status,
            )
        if self.k_number is not None:
            validate_value_length("k_number", self.k_number)
        if self.de_novo_number is not None:
            validate_value_length(
                "de_novo_number", self.de_novo_number
            )
        if self.predicate_device_reference is not None:
            validate_value_length(
                "predicate_device_reference",
                self.predicate_device_reference,
            )
        if self.quality_system_reference is not None:
            validate_value_length(
                "quality_system_reference",
                self.quality_system_reference,
            )
        if self.submission_owner_role is not None:
            validate_value_length(
                "submission_owner_role", self.submission_owner_role
            )
        if self.affected_device_categories:
            joined = ",".join(self.affected_device_categories)
            validate_value_length(
                "affected_device_categories(joined)", joined
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared FDA SaMD framework fields. Suitable for
        binding into the AuditProof's ``metadata_hash``
        A4.

        Auditor + GRC platform + FDA-evidence consumers read this from
        the AuditProof's metadata field. The helper records the
        customer-declared submission status; downstream consumers
        verify clearance / approval via FDA's public databases (510(k)
        Premarket Notification, De Novo Classification, PMA).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "device_class": self.device_class,
            "submission_pathway": self.submission_pathway,
            "submission_status": self.submission_status,
            "intended_use": self.intended_use,
        }
        if self.indications_for_use is not None:
            meta["indications_for_use"] = self.indications_for_use
        if self.clinical_validation_status is not None:
            meta["clinical_validation_status"] = (
                self.clinical_validation_status
            )
        if self.k_number is not None:
            meta["k_number"] = self.k_number
        if self.de_novo_number is not None:
            meta["de_novo_number"] = self.de_novo_number
        if self.subgroup_performance_disclosed is not None:
            meta["subgroup_performance_disclosed"] = (
                "true"
                if self.subgroup_performance_disclosed
                else "false"
            )
        if self.predicate_device_reference is not None:
            meta["predicate_device_reference"] = (
                self.predicate_device_reference
            )
        if self.quality_system_reference is not None:
            meta["quality_system_reference"] = (
                self.quality_system_reference
            )
        if self.submission_owner_role is not None:
            meta["submission_owner_role"] = self.submission_owner_role
        if self.affected_device_categories:
            meta["affected_device_categories"] = ",".join(
                self.affected_device_categories
            )

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
