"""NIST AI Risk Management Framework 1.0 context for capsule workloads.

NIST AI 100-1 (Artificial Intelligence Risk Management Framework 1.0) is
a voluntary US federal framework released January 2023 by the National
Institute of Standards and Technology. The framework structures AI risk
management around four core functions: GOVERN, MAP, MEASURE, MANAGE.

While voluntary at the federal level, the framework is increasingly cited
by healthcare AI vendors, and emerging FDA pre-market expectations for
clinical AI subgroup-performance evaluation are aligned with the NIST AI
RMF MEASURE function (see Anterior arXiv 2603.14631 fairness-evaluation
methodology, which validates a MEASURE-function-aligned approach).

Three-role discipline:
    Nanorix is an evidence vendor (role 1). This helper declares
    framework context for a capsule workload's AI risk management
    posture. It does NOT certify, attest to satisfaction of, or claim
    compliance with NIST AI RMF 1.0. The voluntary nature of the
    framework does not change the three-role split — substantive
    risk-management determinations remain the customer's
    responsibility.

Companion posture document: ``docs/compliance/nist-ai-rmf.md``.

Composes with downstream AI-specific helpers as those are shipped — in
particular, when Item 82 (AI fairness sub-helper) ships, it will compose
with this context to record subgroup-performance evaluation metadata
under the MEASURE function.
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

_FRAMEWORK_VERSION = "1.0"
"""NIST AI 100-1 RMF 1.0 (released January 2023). Subsequent NIST
generative-AI profile (NIST AI 600-1) is a profile layered on RMF 1.0;
the framework version itself remains 1.0."""

_VALID_CORE_FUNCTIONS = frozenset(
    {
        "govern",
        "map",
        "measure",
        "manage",
    }
)
"""NIST AI RMF 1.0 four core functions, lowercase canonical form."""

_VALID_RISK_CATEGORIES = frozenset(
    {
        "validity_reliability",
        "safety",
        "security_resilience",
        "accountability_transparency",
        "explainability_interpretability",
        "privacy",
        "fairness_bias",
    }
)
"""NIST AI RMF 1.0 trustworthy AI characteristics (§3 of the framework,
the seven characteristics of trustworthy AI systems)."""

_VALID_LIFECYCLE_STAGES = frozenset(
    {
        "plan_design",
        "data_collection",
        "build_model",
        "verify_validate",
        "deploy_use",
        "operate_monitor",
    }
)
"""NIST AI RMF 1.0 AI lifecycle stages (§4 — Map function). Aligned
with the AI lifecycle and key dimensions in Figure 5 of NIST AI 100-1."""


@dataclass(frozen=True)
class NISTAIRMFContext:
    """NIST AI Risk Management Framework 1.0 context for a capsule workload.

    Declares context for tagging an AI workload under the voluntary
    NIST AI RMF 1.0. Does NOT certify, attest to satisfaction of, or
    claim compliance with the framework. Substantive risk-management
    determinations remain the customer's responsibility.

    Common usage in healthcare AI cohort that is preparing for or
    operating under emerging FDA pre-market subgroup-performance
    expectations — the MEASURE function in particular aligns with
    fairness-evaluation methodology validated by recent academic
    work (Anterior arXiv 2603.14631).

    Reframing note (per Item 82 industry-benchmark verification
    2026-05-10): emerging FDA pre-market expectation for AI subgroup-
    performance evaluation aligns with the NIST AI RMF MEASURE
    function. This is NOT an FDA mandate at the time of this
    framework version; the FDA's draft guidance has not finalized.
    The helper records the customer's voluntary alignment with the
    framework; it does not assert FDA-mandate compliance.

    See companion posture document: ``docs/compliance/nist-ai-rmf.md``.
    """

    ai_system_owner: str
    """The organization owning the AI system whose risk management is
    being declared. Required."""

    use_case_category: str
    """Customer's high-level use-case category (e.g.,
    ``'clinical-decision-support'``, ``'medical-imaging'``,
    ``'patient-engagement'``, ``'admin-automation'``). Required.
    Free-form; customer-defined vocabulary."""

    core_functions_in_scope: List[str] = field(default_factory=list)
    """Subset of NIST AI RMF 1.0 core functions in scope for this
    workload's evidence mapping. Must be a non-empty subset of
    ``govern``, ``map``, ``measure``, ``manage``. Stored comma-joined
    in the metadata string value."""

    risk_categories: List[str] = field(default_factory=list)
    """Subset of the seven NIST AI RMF 1.0 trustworthy AI characteristics
    relevant to this workload. Must be a subset of:
    ``validity_reliability``, ``safety``, ``security_resilience``,
    ``accountability_transparency``, ``explainability_interpretability``,
    ``privacy``, ``fairness_bias``. May be empty if the workload's
    risk categorization has not yet been declared. Stored comma-joined."""

    lifecycle_stage: Optional[str] = None
    """The AI lifecycle stage the workload corresponds to. Must be one
    of: ``plan_design``, ``data_collection``, ``build_model``,
    ``verify_validate``, ``deploy_use``, ``operate_monitor``. Optional
    — workloads spanning multiple stages may leave this unset."""

    risk_assessment_reference: Optional[str] = None
    """Customer-side risk assessment record reference (e.g., internal
    AI impact assessment ID). Helper does NOT verify the assessment
    exists — pure context tag."""

    model_card_reference: Optional[str] = None
    """Customer-side model-card document reference. Model cards are
    standard MAP / MEASURE function evidence. Helper does NOT verify
    the model card exists — pure context tag."""

    subgroup_evaluation_reference: Optional[str] = None
    """Reference to subgroup-performance evaluation results, when the
    workload has been evaluated for differential performance across
    demographic subgroups (MEASURE function alignment; see the
    posture document for Anterior arXiv 2603.14631 methodology
    citation). Helper does NOT verify the evaluation — pure context
    tag."""

    @property
    def framework_id(self) -> str:
        """The framework identifier emitted in metadata.

        Returns the canonical framework identifier
        ``'NIST_AI_RMF_1.0'``.
        """
        return "NIST_AI_RMF_1.0"

    @property
    def core_functions(self) -> List[str]:
        """The four canonical NIST AI RMF 1.0 core functions.

        Returns a copy so customer code cannot mutate the underlying
        set. Order matches the framework's canonical sequence.
        """
        return ["govern", "map", "measure", "manage"]

    @property
    def trustworthy_ai_characteristics(self) -> List[str]:
        """The seven canonical NIST AI RMF 1.0 trustworthy AI
        characteristics.

        Returns a copy. Useful for customer introspection of the full
        risk-category inventory.
        """
        return [
            "validity_reliability",
            "safety",
            "security_resilience",
            "accountability_transparency",
            "explainability_interpretability",
            "privacy",
            "fairness_bias",
        ]

    def __post_init__(self) -> None:
        if not self.ai_system_owner:
            raise ValueError(
                "ai_system_owner is required and must be non-empty"
            )
        if not self.use_case_category:
            raise ValueError(
                "use_case_category is required and must be non-empty"
            )
        if not self.core_functions_in_scope:
            raise ValueError(
                "core_functions_in_scope is required and must be "
                "non-empty (pick at least one of: govern, map, "
                "measure, manage)"
            )

        for idx, item in enumerate(self.core_functions_in_scope):
            if not isinstance(item, str):
                raise ValueError(
                    f"core_functions_in_scope[{idx}] must be a string "
                    f"(got {type(item).__name__})"
                )
            if item not in _VALID_CORE_FUNCTIONS:
                raise ValueError(
                    f"core_functions_in_scope[{idx}]={item!r} not in "
                    f"{sorted(_VALID_CORE_FUNCTIONS)}"
                )
        validate_no_comma_in_list(
            "core_functions_in_scope",
            list(self.core_functions_in_scope),
        )

        if self.risk_categories:
            for idx, item in enumerate(self.risk_categories):
                if not isinstance(item, str):
                    raise ValueError(
                        f"risk_categories[{idx}] must be a string "
                        f"(got {type(item).__name__})"
                    )
                if item not in _VALID_RISK_CATEGORIES:
                    raise ValueError(
                        f"risk_categories[{idx}]={item!r} not in "
                        f"{sorted(_VALID_RISK_CATEGORIES)} (seven "
                        "trustworthy AI characteristics)"
                    )
            validate_no_comma_in_list(
                "risk_categories", list(self.risk_categories)
            )

        if self.lifecycle_stage is not None:
            if self.lifecycle_stage not in _VALID_LIFECYCLE_STAGES:
                raise ValueError(
                    f"lifecycle_stage={self.lifecycle_stage!r} not in "
                    f"{sorted(_VALID_LIFECYCLE_STAGES)}"
                )

        validate_value_length("ai_system_owner", self.ai_system_owner)
        validate_value_length("use_case_category", self.use_case_category)
        joined_funcs = ",".join(self.core_functions_in_scope)
        validate_value_length(
            "core_functions_in_scope(joined)", joined_funcs
        )
        if self.risk_categories:
            joined_risks = ",".join(self.risk_categories)
            validate_value_length(
                "risk_categories(joined)", joined_risks
            )
        if self.lifecycle_stage is not None:
            validate_value_length("lifecycle_stage", self.lifecycle_stage)
        if self.risk_assessment_reference is not None:
            validate_value_length(
                "risk_assessment_reference",
                self.risk_assessment_reference,
            )
        if self.model_card_reference is not None:
            validate_value_length(
                "model_card_reference", self.model_card_reference
            )
        if self.subgroup_evaluation_reference is not None:
            validate_value_length(
                "subgroup_evaluation_reference",
                self.subgroup_evaluation_reference,
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys
        ``nanorix:framework`` + ``nanorix:framework_version`` plus
        customer-declared fields. ``core_functions_in_scope`` and (when
        non-empty) ``risk_categories`` are comma-joined into single
        string values (per the metadata schema's ``Dict[str, str]``
        shape).

        Auditor + GRC platform consumers read this from the AuditProof's
        metadata field (bound into ``metadata_hash``).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: self.framework_id,
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "ai_system_owner": self.ai_system_owner,
            "use_case_category": self.use_case_category,
            "core_functions_in_scope": ",".join(
                self.core_functions_in_scope
            ),
        }
        if self.risk_categories:
            meta["risk_categories"] = ",".join(self.risk_categories)
        if self.lifecycle_stage is not None:
            meta["lifecycle_stage"] = self.lifecycle_stage
        if self.risk_assessment_reference is not None:
            meta["risk_assessment_reference"] = (
                self.risk_assessment_reference
            )
        if self.model_card_reference is not None:
            meta["model_card_reference"] = self.model_card_reference
        if self.subgroup_evaluation_reference is not None:
            meta["subgroup_evaluation_reference"] = (
                self.subgroup_evaluation_reference
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
