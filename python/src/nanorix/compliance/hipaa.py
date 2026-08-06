"""HIPAA Privacy + Security Rule context for capsule workloads."""
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

_FRAMEWORK_VERSION = "2013-Omnibus"
"""Most recent major rule update (Omnibus Rule, 78 FR 5566).
Privacy Rule 2003 + Security Rule 2005 + Omnibus 2013 form the
operative regulatory text under 45 CFR Parts 160-164."""


@dataclass(frozen=True)
class HipaaContext:
    """HIPAA Privacy + Security Rule context for a capsule workload.

    Declares context for tagging this workload under HIPAA. Does NOT
    certify, attest to satisfaction of, or claim compliance with HIPAA.
    Compliance determination remains the customer's auditor's
    responsibility.

    Required fields encode information needed to identify the workload
    under HIPAA's covered-entity / business-associate framework. Optional
    fields encode additional context auditors typically request.
    """

    covered_entity: str
    """The HIPAA-covered entity owning the data. Required."""

    minimum_necessary_purpose: str
    """Why this workload needs PHI access. Required for the minimum-
    necessary standard at 45 CFR §164.502(b)."""

    data_classification: str = "PHI"
    """Default ``'PHI'``; customer can narrow (e.g.,
    ``'PHI-radiology'``, ``'PHI-imaging'``)."""

    business_associate: Optional[str] = None
    """Business-associate identifier when applicable; ``None`` when the
    covered entity processes its own data."""

    baa_reference: Optional[str] = None
    """Customer-side BAA contract reference. Helper does NOT verify a
    BAA exists — pure context tag."""

    workforce_member_role: Optional[str] = None
    """Who initiated this workload (e.g., ``'attending_radiologist'``).
    Useful for the AuditProof audit trail."""

    def __post_init__(self) -> None:
        if not self.covered_entity:
            raise ValueError("covered_entity is required and must be non-empty")
        if not self.minimum_necessary_purpose:
            raise ValueError(
                "minimum_necessary_purpose is required and must be non-empty"
            )
        if not self.data_classification:
            raise ValueError("data_classification must be non-empty")

        validate_value_length("covered_entity", self.covered_entity)
        validate_value_length(
            "minimum_necessary_purpose", self.minimum_necessary_purpose
        )
        validate_value_length("data_classification", self.data_classification)
        if self.business_associate is not None:
            validate_value_length("business_associate", self.business_associate)
        if self.baa_reference is not None:
            validate_value_length("baa_reference", self.baa_reference)
        if self.workforce_member_role is not None:
            validate_value_length(
                "workforce_member_role", self.workforce_member_role
            )

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        Returns a dict keyed by the reserved keys ``nanorix:framework`` +
        ``nanorix:framework_version`` (whitelisted server-side per
        ADR-017 + the post-G2 validation amendment) plus customer-declared
        fields. Auditor + GRC platform consumers read this from the
        AuditProof's metadata field (bound into ``metadata_hash`` per
        ADR-010 A4).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "HIPAA",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "covered_entity": self.covered_entity,
            "minimum_necessary_purpose": self.minimum_necessary_purpose,
            "data_classification": self.data_classification,
        }
        if self.business_associate is not None:
            meta["business_associate"] = self.business_associate
        if self.baa_reference is not None:
            meta["baa_reference"] = self.baa_reference
        if self.workforce_member_role is not None:
            meta["workforce_member_role"] = self.workforce_member_role

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
