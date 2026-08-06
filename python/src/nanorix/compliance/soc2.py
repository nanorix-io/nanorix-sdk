"""SOC 2 / Trust Services Criteria 2017 context for capsule workloads."""
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

_FRAMEWORK_VERSION = "TSC-2017"

_VALID_TRUST_SERVICE_CRITERIA = frozenset(
    {
        "security",
        "availability",
        "processing_integrity",
        "confidentiality",
        "privacy",
    }
)


@dataclass(frozen=True)
class Soc2Context:
    """SOC 2 Trust Services Criteria context for a capsule workload.

    Declares context for tagging this workload under SOC 2. Does NOT
    certify, attest to satisfaction of, or claim compliance with SOC 2.
    Compliance determination remains the customer's auditor's
    responsibility — only the auditor (typically a CPA firm operating
    under AICPA standards) issues the SOC 2 report and verdict.
    """

    service_organization: str
    """The service organization receiving the SOC 2 audit. Required."""

    trust_service_criteria: List[str] = field(default_factory=list)
    """Subset of: ``security``, ``availability``, ``processing_integrity``,
    ``confidentiality``, ``privacy``. Stored comma-joined in the metadata
    string value. Helper rejects values outside this set; helper rejects
    individual items containing ``','`` (the join separator)."""

    data_classification: str = ""
    """Customer's classification (e.g., ``'tenant_data'``,
    ``'production'``). Required (empty value rejected at construction)."""

    customer_id: Optional[str] = None
    """Per-customer tag when the workload is dedicated to one customer."""

    subservice_organizations: Optional[str] = None
    """Comma-separated names of subservice organizations relevant to this
    workload (carve-out vs inclusive subservice declarations live in the
    SOC 2 report itself; this is just a tag)."""

    audit_period: Optional[str] = None
    """Optional period tag (e.g., ``'2026-Q2'``, ``'2026-01-01..2026-06-30'``)."""

    def __post_init__(self) -> None:
        if not self.service_organization:
            raise ValueError(
                "service_organization is required and must be non-empty"
            )
        if not self.trust_service_criteria:
            raise ValueError(
                "trust_service_criteria is required and must be non-empty "
                "(pick at least one of: security, availability, "
                "processing_integrity, confidentiality, privacy)"
            )
        # Validate item set
        for idx, item in enumerate(self.trust_service_criteria):
            if not isinstance(item, str):
                raise ValueError(
                    f"trust_service_criteria[{idx}] must be a string "
                    f"(got {type(item).__name__})"
                )
            if item not in _VALID_TRUST_SERVICE_CRITERIA:
                raise ValueError(
                    f"trust_service_criteria[{idx}]={item!r} not in "
                    f"{sorted(_VALID_TRUST_SERVICE_CRITERIA)}"
                )
        # No commas in items (defense in depth — set values are commaless,
        # but a future expansion of the criteria list could miss this).
        validate_no_comma_in_list(
            "trust_service_criteria", list(self.trust_service_criteria)
        )
        if not self.data_classification:
            raise ValueError("data_classification must be non-empty")

        validate_value_length("service_organization", self.service_organization)
        validate_value_length("data_classification", self.data_classification)
        # Joined value length check — comma-separated string fits 256-char wire limit.
        joined = ",".join(self.trust_service_criteria)
        validate_value_length("trust_service_criteria(joined)", joined)
        if self.customer_id is not None:
            validate_value_length("customer_id", self.customer_id)
        if self.subservice_organizations is not None:
            validate_value_length(
                "subservice_organizations", self.subservice_organizations
            )
        if self.audit_period is not None:
            validate_value_length("audit_period", self.audit_period)

    def to_metadata(self) -> Dict[str, str]:
        """Convert to the metadata dict format for ``capsules.create()``.

        ``trust_service_criteria`` is comma-joined into a single string
        value (per the metadata schema's ``Dict[str, str]`` shape).
        """
        meta: Dict[str, str] = {
            RESERVED_FRAMEWORK_KEY: "SOC 2",
            RESERVED_FRAMEWORK_VERSION_KEY: _FRAMEWORK_VERSION,
            "service_organization": self.service_organization,
            "trust_service_criteria": ",".join(self.trust_service_criteria),
            "data_classification": self.data_classification,
        }
        if self.customer_id is not None:
            meta["customer_id"] = self.customer_id
        if self.subservice_organizations is not None:
            meta["subservice_organizations"] = self.subservice_organizations
        if self.audit_period is not None:
            meta["audit_period"] = self.audit_period

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
