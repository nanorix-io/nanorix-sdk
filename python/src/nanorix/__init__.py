"""
Nanorix Python SDK — Sealed processing with a verifiable AuditProof for every capsule.

Usage:
    import nanorix

    client = nanorix.Client(api_key="nrx_live_...")

    # One-shot
    result, cdp = client.capsules.run(
        capsulefile="cf_...",
        inputs={"patient.csv": data},
    )

    # Workspace
    capsule = client.capsules.create(capsulefile="cf_...")
    capsule.upload("data.csv", data)
    capsule.execute()
    output = capsule.download("result.json")
    cdp = capsule.destroy()
"""
from nanorix._client import AsyncClient, Client
from nanorix._constants import VERSION
from nanorix._exceptions import (
    AuthenticationError,
    BadRequestError,
    CeilingError,
    ConflictError,
    ExecutionError,
    GoneError,
    InputProvenanceRejectedError,
    NanorixError,
    NotFoundError,
    PayloadTooLargeError,
    QuotaExceededError,
    RateLimitError,
    SecretsInjectionRejectedError,
    TimeoutError,
    UnprocessableEntityError,
)
from nanorix._models import (
    Capsule,
    CapsuleList,
    Capsulefile,
    CapsulefileFile,
    CapsulefileList,
    CdpAccess,
    CdpDocument,
    ExecutionResult,
    FileInfo,
    ParentProofLink,
    RecordReceipt,
    VerificationCdp,
)
# customer-facing ergonomic surface — per-record receipt
# emission (RecordContext) + cross-org parent-proof composition. Three-layer
# separation: receipt (immutable signed primitive carrying pattern_tag) /
# mapping artifact (published, versioned) / adapter (vendor pace).
from nanorix.capsule_record import (
    PATTERN_TAGS,
    AsyncRecordContext,
    RecordContext,
    validate_pattern_tag,
)
from nanorix._batch import BatchRecord, BatchResult
from nanorix._pagination import AsyncPage, SyncPage
from nanorix._session import SessionResult
from nanorix.decorators import WorkloadDecorator, workload
from nanorix.healthcare import (
    CCDADocumentReference,
    CCDAReader,
    EpicLaunchContext,
    EpicLaunchContextResult,
    HL7v2Adapter,
    HL7v2ParsedMessage,
)
from nanorix.resources.usage import UsageItem

# Batch 3 sub-item 1 (CR-002, 2026-05-27) — Billing meter readout surface.
# Programmatic mirror of Stripe metered billing data. Per-pattern,
# per-shape, per-capsulefile breakdowns + cost forecast. Counts are
# STRUCTURAL FACTS per feedback_narrow_signed_claim_auditor_certifies.md.
from nanorix.billing import (
    AsyncBilling,
    Billing,
    BillingMeterUsage,
    CapsulefileUsage,
    CostForecast,
    PatternUsage,
    ShapeUsage,
)

# control-map (Wave B 2026-05-26) — published mapping artifact
# `(jurisdiction × data_classification × pattern_tag) → list[control_id]`.
# Consumed by GRC adapters (Vanta / Drata / Anecdotes / internal) at
# ingestion time given a receipt's signed `pattern_tag` +
# customer's declared `jurisdiction` + capsule's `data_classification`.
# Three-layer separation: receipt (immutable) / mapping (versioned) /
# adapter (vendor pace). The mapping is NEVER stamped into the receipt.
from nanorix.control_map import (
    DISCLAIMER as CONTROL_MAP_DISCLAIMER,
    FRAMEWORK_VERSION as CONTROL_MAP_FRAMEWORK_VERSION,
    MAPPING_2026_02,
    Mapping as ControlMapping,
    SCHEMA_VERSION as CONTROL_MAP_SCHEMA_VERSION,
    available_framework_versions,
    load_mapping,
    resolve as resolve_controls,
)

__version__ = VERSION
__all__ = [
    "Client",
    "AsyncClient",
    # Exceptions
    "NanorixError",
    "AuthenticationError",
    "NotFoundError",
    "ConflictError",
    "RateLimitError",
    "PayloadTooLargeError",
    "GoneError",
    "QuotaExceededError",
    "BadRequestError",
    "ExecutionError",
    "CeilingError",
    "TimeoutError",
    "UnprocessableEntityError",
    "InputProvenanceRejectedError",
    "SecretsInjectionRejectedError",
    # Models
    "Capsulefile",
    "CapsulefileFile",
    "CapsulefileList",
    "Capsule",
    "CapsuleList",
    "ExecutionResult",
    "FileInfo",
    "CdpDocument",
    "VerificationCdp",
    "CdpAccess",
    "UsageItem",
    # Pagination
    "SyncPage",
    "AsyncPage",
    # Batch (Order 2 / Gap 1)
    "BatchRecord",
    "BatchResult",
    # Session (Order 3 / Gap 2)
    "SessionResult",
    # Tier B `@workload` decorator (Wave 14, item 3 of 5)
    "workload",
    "WorkloadDecorator",
    # Healthcare data-format adapters (Wave 17 batch 6, Items 79-81)
    "HL7v2Adapter",
    "HL7v2ParsedMessage",
    "CCDAReader",
    "CCDADocumentReference",
    "EpicLaunchContext",
    "EpicLaunchContextResult",
    # control-map (Wave B 2026-05-26)
    "CONTROL_MAP_DISCLAIMER",
    "CONTROL_MAP_FRAMEWORK_VERSION",
    "CONTROL_MAP_SCHEMA_VERSION",
    "MAPPING_2026_02",
    "ControlMapping",
    "available_framework_versions",
    "load_mapping",
    "resolve_controls",
    # per-record receipt + cross-org parent surface
    "RecordReceipt",
    "ParentProofLink",
    "RecordContext",
    "AsyncRecordContext",
    "PATTERN_TAGS",
    "validate_pattern_tag",
    # Batch 3 sub-item 1 (CR-002, 2026-05-27) — billing meter readout
    "Billing",
    "AsyncBilling",
    "BillingMeterUsage",
    "CostForecast",
    "PatternUsage",
    "ShapeUsage",
    "CapsulefileUsage",
]
