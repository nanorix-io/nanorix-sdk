"""GDPR (Regulation (EU) 2016/679) context for capsule workloads."""
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

_FRAMEWORK_VERSION = "2016-679"
"""Regulation (EU) 2016/679 — applicable from 25 May 2018."""

_VALID_LEGAL_BASIS = frozenset(
    {
        "consent",
        "contract",
        "legal_obligation",
        "vital_interests",
        "public_task",
        "legitimate_interests",
    }
)


@dataclass(frozen=True)
class GdprContext:
    """GDPR context for a capsule workload.

    Declares context for tagging this workload under GDPR. Does NOT
    certify, attest to satisfaction of, or claim compliance with GDPR.
    Compliance determination remains the customer's data protection
    officer + their supervisory authority's responsibility.

    Required fields encode information needed to identify the workload
    under GDPR's controller / processor / lawful-basis framework.
    """

    data_controller: str
    """The data controller (Article 4(7)) for the personal data
    processed in this workload. Required."""

    legal_basis: str
    """Article 6(1) lawful basis. Must be one of: ``consent``,
    ``contract``, ``legal_obligation``, ``vital_interests``,
    ``public_task``, ``legitimate_interests``. Required."""

    processing_purpose: str
    """Why the personal data is being processed. Required for the
    purpose-limitation principle at Article 5(1)(b)."""

    data_classification: str = "personal_data"
    """Default ``'personal_data'``; customer can narrow (e.g.,
    ``'special_category'`` for Article 9 categories)."""

    data_processor: Optional[str] = None
    """Data processor identifier when the controller uses a processor
    (Article 28). ``None`` when controller processes the data itself."""

    data_subject_categories: Optional[str] = None
    """Comma-separated category tags (e.g., ``'employees,customers'``).
    Free-form string; customer-defined vocabulary."""

    dpo_contact: Optional[str] = None
    """Data Protection Officer contact reference. Helper does NOT verify
    a DPO exists or is registered — pure context tag."""

    cross_border_transfer_basis: Optional[str] = None
    """When personal data crosses borders (Articles 44-50): one of
    ``'adequacy'``, ``'sccs'``, ``'bcrs'``, ``'derogation'``, etc. Free-
    form; customer-defined."""

    retention_period: Optional[str] = None
    """Storage-limitation tag (Article 5(1)(e)): e.g., ``'30_days'``,
    ``'session_only'``, ``'ephemeral'``. Free-form."""

    def __post_init__(self) -> None:
        if not self.data_controller:
            raise ValueError("data_controller is required and must be non-empty")
        if not self.legal_basis:
            raise ValueError("legal_basis is required and must be non-empty")
        if self.legal_basis not in _VALID_LEGAL_BASIS:
            raise ValueError(
                f"legal_basis={self.legal_basis!r} not in "
                f"{sorted(_VALID_LEGAL_BASIS)} (Article 6(1) closed enum)"
            )
        if not self.processing_purpose:
            raise ValueError(
                "processing_purpose is required and must be non-empty"
            )
        if not self.data_classification:
            raise ValueError("data_classification must be non-empty")

        validate_value_length("data_controller", self.data_controller)
        validate_value_length("legal_basis", self.legal_basis)
        validate_value_length("processing_purpose", self.processing_purpose)
        validate_value_length("data_classification", self.data_classification)
        if self.data_processor is not None:
            validate_value_length("data_processor", self.data_processor)
        if self.data_subject_categories is not None:
            validate_value_length(
                "data_subject_categories", self.data_subject_categories
            )
        if self.dpo_contact is not None:
            validate_value_length("dpo_contact", self.dpo_contact)
        if self.cross_border_transfer_basis is not None:
            validate_value_length(
                "cross_border_transfer_basis", self.cross_border_transfer_basis
            )
        if self.retention_period is not None:
            validate_value_length("retention_period", self.retention_period)

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``."""
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "GDPR",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "data_controller": self.data_controller,
            "legal_basis": self.legal_basis,
            "processing_purpose": self.processing_purpose,
            "data_classification": self.data_classification,
        }
        if self.data_processor is not None:
            meta["data_processor"] = self.data_processor
        if self.data_subject_categories is not None:
            meta["data_subject_categories"] = self.data_subject_categories
        if self.dpo_contact is not None:
            meta["dpo_contact"] = self.dpo_contact
        if self.cross_border_transfer_basis is not None:
            meta["cross_border_transfer_basis"] = self.cross_border_transfer_basis
        if self.retention_period is not None:
            meta["retention_period"] = self.retention_period

        if len(meta) > MAX_METADATA_KEYS:
            raise ValueError(
                f"to_metadata produced {len(meta)} keys, max {MAX_METADATA_KEYS}"
            )
        size = estimate_serialized_size(meta)
        if size > 4096:
            raise ValueError(
                f"to_metadata serializes to {size} bytes, max 4096"
            )
        return meta
