"""AI fairness evaluation context for capsule workloads.

The AI fairness evaluation helper records customer-declared methodology
+ scope context for fairness evaluation of AI systems. Fairness
evaluation is an emerging regulatory expectation across multiple
jurisdictions and frameworks, including:

- **NIST AI Risk Management Framework 1.0** (NIST AI 100-1, January
  2023): voluntary US federal framework; the MEASURE function (one of
  four core functions GOVERN / MAP / MEASURE / MANAGE) includes
  fairness evaluation under Measure-2.11 + trustworthy AI
  characteristic ``fairness_bias`` (§3 of the framework).
- **FDA Artificial Intelligence-Enabled Device Software Functions**
  draft guidance (89 FR 1543, January 7 2025;
  <https://www.federalregister.gov/documents/2025/01/07/2024-31543>):
  recommends representativeness across demographic groups (race,
  ethnicity, sex, age) for both data collection AND performance
  evaluation. As of 2026-05-10, the draft has NOT been finalized —
  subgroup performance disclosure is an **emerging FDA expectation**
  tied to the draft, NOT a finalized regulatory requirement. The
  helper records the customer-declared evaluation context without
  asserting that the evaluation is statutorily required.
- **NYC Local Law 144 of 2021** (AEDT bias audit): mandates annual
  bias audits for Automated Employment Decision Tools used in NYC
  hiring / promotion decisions. Composes with :class:`NYCLL144Context`.
- **Colorado AI Act / SB24-205** (effective February 1, 2026): requires
  algorithmic discrimination risk-management for High-Risk AI Systems
  making Consequential Decisions. Composes with
  :class:`ColoradoAIActContext`.
- **ISO/IEC 42001:2023** AI Management System: includes fairness as
  one of the AI-system characteristics that the AIMS addresses.

Peer-reviewed prior art on healthcare-AI fairness methodology: the
Anterior team's *Anterior's Approach to Fairness Evaluation of
Automated Prior Authorization System* (arXiv 2603.14631, Sai P.
Selvaraj + Khadija Mahmoud + Anuj Iravane, submitted March 15 2026;
<https://arxiv.org/abs/2603.14631>) validates an **error-rate parity**
methodology rather than approval-outcome parity, evaluating 7,166
medical-necessity-determination cases across 27 guidelines with a
±5 percentage-point tolerance band on subgroup error-rate differences.
The paper aligns with NIST AI RMF MEASURE-function fairness evaluation
+ FDA emerging subgroup-performance-disclosure expectation.

Cross-vertical framing:
    AI fairness evaluation is **not specific to healthcare AI**. The
    methodology applies across all AI deployments — hiring, housing,
    financial services, criminal justice, education, healthcare. The
    helper ships under :mod:`nanorix.healthcare` because healthcare AI
    is the lead beachhead, but is intentionally framework-agnostic
    with respect to vertical. Composition with vertical-specific
    helpers (FDA SaMD for healthcare, NYC LL 144 for hiring, Colorado
    AI Act for high-risk decisions) is the customer's design choice.

Three-role discipline (ADR-017):
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for an AI workload's fairness-evaluation
    posture. It does NOT certify that the customer's evaluation
    satisfies any specific regulatory or technical standard, does NOT
    validate the statistical sufficiency of the evaluation, does NOT
    verify documentation against the customer's evaluation artifacts,
    and does NOT substitute for the customer's AI ethics + bias
    governance program. Methodology selection, demographic-group
    scoping, sample-size determination, tolerance-band selection, and
    pre-registration discipline remain the customer's AI ethics +
    governance team's responsibility.

Composition with other helpers:

The fairness-evaluation context composes naturally with:

- :class:`FDASaMDContext` for FDA-regulated medical device software
  (cross-references the FDA SaMD ``subgroup_performance_disclosed``
  field; same semantic — emitted under both context tags)
- :class:`NISTAIRMFContext` for NIST AI RMF posture (MEASURE function
  + ``fairness_bias`` trustworthy AI characteristic alignment)
- :class:`NYCLL144Context` for AEDT bias audit (mandatory annual
  audits for NYC AEDT)
- :class:`ColoradoAIActContext` for High-Risk AI Systems making
  Consequential Decisions (algorithmic discrimination risk
  management)
- :class:`ISO42001Context` for AI Management System fairness coverage

Companion posture document: ``docs/healthcare/ai-fairness-evaluation.md``.

Note: AI fairness regulatory positions evolve. The ``framework_version``
field is a non-calendar adapter-baseline identifier; bump when the
underlying methodology or regulatory framework changes materially.
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
    validate_no_comma_in_list,
    validate_value_length,
)

_FRAMEWORK_VERSION = "2026-02"
"""AI fairness evaluation framework-baseline identifier. Non-calendar
adapter-baseline identifier; bump when the underlying methodology or
regulatory expectation framework changes materially. As of 2026-05 the
FDA AI/ML 2025 draft guidance is in public comment and has NOT been
finalized."""

_VALID_EVALUATION_METHODOLOGIES = frozenset(
    {
        "error_rate_parity",
        "outcome_parity",
        "calibration_parity",
        "equalized_odds",
        "demographic_parity",
        "custom",
    }
)
"""Fairness-evaluation methodology taxonomy.

- ``error_rate_parity``: subgroup error-rate differences within a
  tolerance band (per Anterior arXiv 2603.14631; ±5pp tolerance band
  validated on 7,166 medical-necessity-determination cases).
- ``outcome_parity``: equal positive-outcome rates across subgroups
  (legacy demographic parity framing).
- ``calibration_parity``: subgroup calibration / reliability-diagram
  parity (predicted-probability vs observed-rate).
- ``equalized_odds``: equal true-positive and false-positive rates
  across subgroups (Hardt et al. 2016).
- ``demographic_parity``: predicted-positive rate independent of
  protected attribute.
- ``custom``: customer-specified methodology not in the canonical set."""

_VALID_RE_EVALUATION_CADENCES = frozenset(
    {
        "quarterly",
        "biannually",
        "annually",
        "on_significant_change",
        "ad_hoc",
    }
)
"""Re-evaluation cadence taxonomy. NYC LL 144 mandates annual cadence
for AEDT bias audits; the FDA AI/ML 2025 draft addresses continuous
performance monitoring via Predetermined Change Control Plans.
``on_significant_change`` aligns with the FDA PCCP framing."""

# ISO-8601 calendar-date validation (YYYY-MM-DD). Helper validates
# structural format only; does not interpret the date.
_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class AIFairnessContext:
    """AI fairness evaluation context for a capsule workload.

    Declares customer-supplied context for tagging this workload's AI
    fairness-evaluation posture. Records the customer-declared
    evaluation methodology, demographic-group scope, sample size,
    tolerance band (where applicable), evaluation status, and
    documentation reference. Does NOT certify that the evaluation
    satisfies any specific regulatory or technical standard, does NOT
    validate the statistical sufficiency of the evaluation, and does
    NOT substitute for the customer's AI ethics + bias governance
    program.

    .. warning::

        Fairness-evaluation determination is the customer's AI ethics
        + governance team's responsibility. The helper records the
        customer-declared context only. Downstream evidence consumers
        (auditors, GRC platforms, FDA evidence reviewers, state AI-law
        regulators) verify substantive evaluation artifacts via the
        customer's documentation reference; the helper does NOT verify
        or assess the artifacts.

    The helper records:

    - **Evaluation methodology** (error-rate parity / outcome parity /
      calibration parity / equalized odds / demographic parity /
      custom)
    - **Demographic groups evaluated** (e.g., race, ethnicity, sex,
      age, disability status, language)
    - **Tolerance band** (percentage points; Anterior used ±5pp)
    - **Sample size** (Anterior used 7,166 cases)
    - **Evaluation completed** (boolean flag)
    - **Documentation URL** (reference to evaluation report)
    - **Subgroup performance disclosed** (composes with FDA SaMD)
    - **Methodology pre-registered** (pre-registration discipline
      flag)
    - **Evaluation date** (ISO-8601 date)
    - **Re-evaluation cadence** (NYC LL 144 mandates annual; FDA PCCP
      addresses continuous monitoring)

    Cross-vertical: AI fairness evaluation applies to AI deployments
    across hiring (NYC LL 144 / EEOC), housing (HUD), financial
    services (CFPB), criminal justice, education, healthcare (FDA
    SaMD). Common compositions:

    - **Healthcare AI SaMD** (clinical decision support, voice
      biomarker diagnostics, radiology AI): pair with
      :class:`FDASaMDContext` (cross-references
      ``subgroup_performance_disclosed``) +
      :class:`NISTAIRMFContext` (MEASURE function fairness evaluation
      alignment) + :class:`HipaaContext`
    - **NYC-jurisdictional AEDT** (hiring / promotion AI):
      :class:`NYCLL144Context` (mandatory annual bias audit)
    - **Colorado-jurisdictional High-Risk AI System**:
      :class:`ColoradoAIActContext` (algorithmic discrimination risk
      management)
    - **AIMS-aligned organization**: :class:`ISO42001Context`

    The helper is intentionally cross-vertical — healthcare AI is the
    lead beachhead but the methodology applies universally. See
    companion posture document:
    ``docs/healthcare/ai-fairness-evaluation.md``.

    Peer-reviewed prior art: the Anterior team's *Anterior's Approach
    to Fairness Evaluation of Automated Prior Authorization System*
    (arXiv 2603.14631; Selvaraj + Mahmoud + Iravane, submitted March
    15 2026) validates an error-rate-parity methodology across 7,166
    cases × 27 medical-necessity guidelines with a ±5pp tolerance
    band. The methodology aligns with the NIST AI RMF MEASURE
    function and the emerging FDA pre-market subgroup-performance
    expectation.
    """

    evaluation_methodology: str
    """Fairness-evaluation methodology. Must be one of the values in
    :attr:`evaluation_methodology_set`. Required.

    Methodology selection is the customer's AI ethics + governance
    team's responsibility; helper records the customer-declared
    methodology only. Helper does NOT verify the methodology is
    appropriate for the workload's protected-attribute structure or
    decision-outcome shape. Per Anterior arXiv 2603.14631,
    ``error_rate_parity`` is a peer-reviewed methodology validated on
    healthcare-AI prior-authorization decisions; the canonical set
    also covers established academic frameworks (equalized odds,
    demographic parity, calibration parity)."""

    demographic_groups_evaluated: List[str]
    """Customer-declared demographic categories evaluated for parity
    (e.g., ``["race", "ethnicity", "sex", "age",
    "disability_status", "language"]``). Required non-empty list.
    Stored comma-joined.

    Scope selection is the customer's responsibility; helper does NOT
    verify the scope is statutorily required for the customer's
    jurisdiction or that the scope is sufficient for the workload's
    risk profile. The FDA AI/ML 2025 draft (89 FR 1543) recommends
    representativeness across race, ethnicity, sex, and age; state AI
    laws (e.g., Colorado AI Act, NYC LL 144) may impose additional
    demographic-group expectations."""

    sample_size: int
    """Number of evaluation cases in the fairness evaluation. Required
    positive integer.

    Sample-size determination is the customer's responsibility; helper
    does NOT verify the size is statistically sufficient for the
    workload's effect-size hypothesis or the customer's tolerance band.
    Per Anterior arXiv 2603.14631, 7,166 cases were evaluated across
    27 medical-necessity guidelines; appropriate sample size varies by
    methodology, protected-attribute prevalence, and tolerance band."""

    evaluation_completed: bool
    """Whether the fairness evaluation has been performed. Required.

    Status determination is the customer's responsibility. A ``True``
    declaration records the customer's declared status only;
    downstream evidence consumers verify completion via the
    customer's documentation reference."""

    tolerance_band_pp: Optional[float] = None
    """Percentage-point tolerance band for parity. Optional; only
    meaningful for methodologies admitting tolerance bands
    (error-rate parity, calibration parity, equalized odds). Per
    Anterior arXiv 2603.14631, ±5.0pp was used. Must be in the range
    ``[0.0, 100.0]`` when provided.

    Tolerance-band selection is the customer's responsibility; helper
    does NOT verify the band is appropriate for the workload's risk
    profile. Looser bands may underestimate disparity; tighter bands
    may overestimate sample-size requirements."""

    documentation_url: Optional[str] = None
    """Reference URL pointing to the fairness evaluation report (e.g.,
    arXiv preprint, internal eval doc, GRC-platform artifact). Optional.

    Documentation existence is the customer's responsibility; helper
    does NOT verify the URL is reachable, the document exists, or the
    document contains substantive evaluation artifacts."""

    subgroup_performance_disclosed: Optional[bool] = None
    """Whether subgroup-stratified performance metrics have been
    disclosed for this workload. Optional boolean — emitted as the
    string ``"true"`` or ``"false"`` in the metadata dict when set.

    .. note::

        This field semantically mirrors :class:`FDASaMDContext`'s
        ``subgroup_performance_disclosed`` field — when both contexts
        are used together, the customer declares the same value on
        both. Subgroup performance disclosure is an **emerging FDA
        expectation** tied to the AI-Enabled Device Software
        Functions 2025 draft guidance (89 FR 1543, Jan 7 2025) and
        NIST AI RMF Measure-2.11 fairness evaluation — **not a
        finalized regulatory requirement** as of 2026-05. The helper
        records the customer-declared disclosure status without
        asserting that disclosure is statutorily required.

    The disclosure determination is the customer's responsibility;
    helper does NOT verify disclosure artifacts exist or meet emerging
    FDA / NIST expectations."""

    methodology_pre_registered: Optional[bool] = None
    """Whether the evaluation methodology was pre-registered before
    deployment (e.g., on OSF, AsPredicted, or an internal registry).
    Optional boolean.

    Pre-registration discipline is a methodological-rigor signal valued
    in some regulatory contexts (e.g., FDA pre-market submissions).
    Helper records the customer-declared status only; does NOT verify
    pre-registration artifacts exist."""

    evaluation_date: Optional[str] = None
    """ISO-8601 calendar date the evaluation was completed (format
    ``YYYY-MM-DD``). Optional. Helper validates structural format only;
    does NOT interpret the date or verify recency.

    Useful when paired with :attr:`re_evaluation_cadence` for
    downstream consumers to assess whether the evaluation is within
    cadence."""

    re_evaluation_cadence: Optional[str] = None
    """Cadence for re-evaluation. Must be one of the values in
    :attr:`re_evaluation_cadence_set` when provided. Optional.

    NYC LL 144 mandates annual cadence for AEDT bias audits. The FDA
    AI/ML 2025 draft addresses continuous performance monitoring via
    Predetermined Change Control Plans (PCCP); ``on_significant_change``
    aligns with the PCCP framing. Cadence determination is the
    customer's responsibility; helper does NOT verify cadence
    obligations against the customer's jurisdiction."""

    evaluation_owner_role: Optional[str] = None
    """Who owns this fairness evaluation (e.g.,
    ``"ai_ethics_lead"``, ``"model_governance_engineer"``,
    ``"chief_data_scientist"``). Useful for the AuditProof activity
    trail and accountable-deployment evidence."""

    affected_decision_categories: List[str] = field(default_factory=list)
    """Customer-declared decision-category descriptors (e.g.,
    ``"clinical_decision_support"``, ``"prior_authorization"``,
    ``"resume_screening"``, ``"credit_underwriting"``,
    ``"diagnostic_imaging"``). Free-form. Stored comma-joined.

    Useful for routing AuditProof evidence to category-specific
    downstream consumers (FDA inspectors for healthcare-AI, EEOC for
    hiring-AI, CFPB for credit-AI, etc.)."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier
        ``"AI_Fairness_Evaluation"``.
        """
        return "AI_Fairness_Evaluation"

    @property
    def framework_version(self) -> str:
        """The framework-baseline version emitted in metadata.

        Returns ``"2026-02"`` — non-calendar adapter-baseline
        identifier.
        """
        return _FRAMEWORK_VERSION

    @property
    def evaluation_methodology_set(self) -> List[str]:
        """The canonical fairness-evaluation methodology taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_EVALUATION_METHODOLOGIES)

    @property
    def re_evaluation_cadence_set(self) -> List[str]:
        """The canonical re-evaluation cadence taxonomy.

        Returns a sorted copy.
        """
        return sorted(_VALID_RE_EVALUATION_CADENCES)

    @property
    def uses_tolerance_band(self) -> bool:
        """Whether the configured methodology admits a tolerance band.

        Returns True for ``error_rate_parity``, ``calibration_parity``,
        ``equalized_odds`` — methodologies that naturally express a
        ±band on subgroup metric differences. Returns False for
        ``demographic_parity`` / ``outcome_parity`` / ``custom``.
        """
        return self.evaluation_methodology in {
            "error_rate_parity",
            "calibration_parity",
            "equalized_odds",
        }

    def __post_init__(self) -> None:
        if not self.evaluation_methodology:
            raise ValueError(
                "evaluation_methodology is required and must be non-empty"
            )
        if (
            self.evaluation_methodology
            not in _VALID_EVALUATION_METHODOLOGIES
        ):
            raise ValueError(
                f"evaluation_methodology={self.evaluation_methodology!r} "
                f"not in {sorted(_VALID_EVALUATION_METHODOLOGIES)} "
                "(fairness-evaluation methodology taxonomy)"
            )

        if not self.demographic_groups_evaluated:
            raise ValueError(
                "demographic_groups_evaluated is required and must be "
                "non-empty (declare at least one demographic category "
                "evaluated, e.g., 'race', 'ethnicity', 'sex', 'age')"
            )
        for idx, item in enumerate(self.demographic_groups_evaluated):
            if not isinstance(item, str):
                raise ValueError(
                    f"demographic_groups_evaluated[{idx}] must be a "
                    f"string (got {type(item).__name__})"
                )
            if not item:
                raise ValueError(
                    f"demographic_groups_evaluated[{idx}] must be "
                    "non-empty"
                )
        validate_no_comma_in_list(
            "demographic_groups_evaluated",
            list(self.demographic_groups_evaluated),
        )

        if not isinstance(self.sample_size, bool) and not isinstance(
            self.sample_size, int
        ):
            raise ValueError(
                f"sample_size must be an int "
                f"(got {type(self.sample_size).__name__})"
            )
        if isinstance(self.sample_size, bool):
            # bool is subclass of int; reject explicit booleans.
            raise ValueError(
                "sample_size must be an int, not a bool"
            )
        if self.sample_size <= 0:
            raise ValueError(
                f"sample_size={self.sample_size} must be a positive "
                "integer (number of evaluation cases)"
            )

        if not isinstance(self.evaluation_completed, bool):
            raise ValueError(
                "evaluation_completed must be a bool (got "
                f"{type(self.evaluation_completed).__name__}); "
                "pass True or False, not the string 'true'"
            )

        if self.tolerance_band_pp is not None:
            if isinstance(self.tolerance_band_pp, bool):
                raise ValueError(
                    "tolerance_band_pp must be a float / int, not a bool"
                )
            if not isinstance(
                self.tolerance_band_pp, (int, float)
            ):
                raise ValueError(
                    "tolerance_band_pp must be a number "
                    f"(got {type(self.tolerance_band_pp).__name__})"
                )
            if not (0.0 <= float(self.tolerance_band_pp) <= 100.0):
                raise ValueError(
                    f"tolerance_band_pp={self.tolerance_band_pp} must be "
                    "in [0.0, 100.0] (percentage-point band)"
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

        if self.methodology_pre_registered is not None and not (
            isinstance(self.methodology_pre_registered, bool)
        ):
            raise ValueError(
                "methodology_pre_registered must be a bool when "
                "provided (got "
                f"{type(self.methodology_pre_registered).__name__})"
            )

        if self.evaluation_date is not None:
            if not self.evaluation_date:
                raise ValueError(
                    "evaluation_date if provided must be non-empty"
                )
            if not _ISO_DATE_PATTERN.fullmatch(self.evaluation_date):
                raise ValueError(
                    f"evaluation_date={self.evaluation_date!r} must match "
                    "ISO-8601 format 'YYYY-MM-DD' (e.g., '2026-05-10'); "
                    "helper validates structural format only"
                )

        if self.re_evaluation_cadence is not None:
            if not self.re_evaluation_cadence:
                raise ValueError(
                    "re_evaluation_cadence if provided must be non-empty"
                )
            if (
                self.re_evaluation_cadence
                not in _VALID_RE_EVALUATION_CADENCES
            ):
                raise ValueError(
                    f"re_evaluation_cadence={self.re_evaluation_cadence!r} "
                    f"not in {sorted(_VALID_RE_EVALUATION_CADENCES)} "
                    "(re-evaluation cadence taxonomy)"
                )

        if self.affected_decision_categories:
            for idx, item in enumerate(self.affected_decision_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"affected_decision_categories[{idx}] must be a "
                        f"string (got {type(item).__name__})"
                    )
                if not item:
                    raise ValueError(
                        f"affected_decision_categories[{idx}] must be "
                        "non-empty"
                    )
                if "," in item:
                    raise ValueError(
                        f"affected_decision_categories[{idx}]={item!r} "
                        "contains ',' which is the comma-join "
                        "separator; remove or escape before passing"
                    )

        validate_value_length(
            "evaluation_methodology", self.evaluation_methodology
        )
        joined_groups = ",".join(self.demographic_groups_evaluated)
        validate_value_length(
            "demographic_groups_evaluated(joined)", joined_groups
        )
        if self.documentation_url is not None:
            validate_value_length(
                "documentation_url", self.documentation_url
            )
        if self.evaluation_date is not None:
            validate_value_length(
                "evaluation_date", self.evaluation_date
            )
        if self.re_evaluation_cadence is not None:
            validate_value_length(
                "re_evaluation_cadence", self.re_evaluation_cadence
            )
        if self.evaluation_owner_role is not None:
            validate_value_length(
                "evaluation_owner_role", self.evaluation_owner_role
            )
        if self.affected_decision_categories:
            joined_cats = ",".join(self.affected_decision_categories)
            validate_value_length(
                "affected_decision_categories(joined)", joined_cats
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared AI fairness evaluation context fields.
        Suitable for binding into the AuditProof's ``metadata_hash``
        per ADR-010 A4.

        Auditor + GRC platform + FDA-evidence consumers read this from
        the AuditProof's metadata field. The helper records the
        customer-declared evaluation context; downstream consumers
        verify substantive evaluation artifacts via the customer's
        :attr:`documentation_url` reference.
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "evaluation_methodology": self.evaluation_methodology,
            "demographic_groups_evaluated": ",".join(
                self.demographic_groups_evaluated
            ),
            "sample_size": str(self.sample_size),
            "evaluation_completed": (
                "true" if self.evaluation_completed else "false"
            ),
        }
        if self.tolerance_band_pp is not None:
            # Emit with up to two decimal places; strip trailing zeros.
            tb = float(self.tolerance_band_pp)
            formatted = f"{tb:.2f}".rstrip("0").rstrip(".")
            if not formatted:
                formatted = "0"
            meta["tolerance_band_pp"] = formatted
        if self.documentation_url is not None:
            meta["documentation_url"] = self.documentation_url
        if self.subgroup_performance_disclosed is not None:
            meta["subgroup_performance_disclosed"] = (
                "true"
                if self.subgroup_performance_disclosed
                else "false"
            )
        if self.methodology_pre_registered is not None:
            meta["methodology_pre_registered"] = (
                "true"
                if self.methodology_pre_registered
                else "false"
            )
        if self.evaluation_date is not None:
            meta["evaluation_date"] = self.evaluation_date
        if self.re_evaluation_cadence is not None:
            meta["re_evaluation_cadence"] = self.re_evaluation_cadence
        if self.evaluation_owner_role is not None:
            meta["evaluation_owner_role"] = self.evaluation_owner_role
        if self.affected_decision_categories:
            meta["affected_decision_categories"] = ",".join(
                self.affected_decision_categories
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
