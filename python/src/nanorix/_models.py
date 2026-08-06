"""
Pydantic models for all Nanorix API objects.

Rules:
- Every model uses model_config = ConfigDict(extra="allow") so the SDK
  doesn't break when the API adds new fields.
- All timestamps are strings (ISO 8601). Don't parse to datetime.
- Optional fields have explicit defaults.
- Use Dict, List, Optional from typing — NOT lowercase (Python 3.9).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ── Capsulefiles ──

class CapsulefileFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    filename: str
    size_bytes: int
    sha256: str  # SHA-512 hash (field name is legacy, value is sha512 hex)


class Capsulefile(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    runtime: str
    entrypoint: str
    description: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    input_path: str = "/data/input"
    output_path: str = "/data/output"
    env_vars: Dict[str, str] = Field(default_factory=dict)
    capsule_memory_mb: Optional[int] = None
    content_hash: Optional[str] = None
    status: str = "draft"
    version: int = 1
    total_size_bytes: int = 0
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    ready_at: Optional[str] = None
    files: List[CapsulefileFile] = Field(default_factory=list)


class CapsulefileList(BaseModel):
    model_config = ConfigDict(extra="allow")
    capsulefiles: List[Capsulefile]
    cursor: Optional[str] = None
    has_more: bool = False


# ── Capsules ──

class Capsule(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    customer_id: Optional[str] = None
    status: str
    capsulefile_id: Optional[str] = None
    capsulefile_version: Optional[int] = None
    max_lifetime_seconds: Optional[int] = None
    created_at: Optional[str] = None
    destroyed_at: Optional[str] = None


class CapsuleList(BaseModel):
    model_config = ConfigDict(extra="allow")
    capsules: List[Capsule]


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: int = 0
    duration_seconds: Optional[float] = None


class FileInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    file_id: Optional[str] = None
    filename: str
    size_bytes: int
    sha256: str  # SHA-512 hash (field name is legacy, value is sha512 hex)
    uploaded_at: Optional[str] = None


# ── CDP ──

class ActivityEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    event: str
    at: str
    # All other fields are event-specific and captured by extra="allow"


class ResidencyInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    started: Optional[str] = None
    destroyed: Optional[str] = None
    total_seconds: Optional[float] = None


class DestructionInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    trigger: Optional[str] = None
    chain: Optional[str] = None
    chain_hash: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None


class AttestationInfo(BaseModel):
    model_config = ConfigDict(extra="allow")
    algorithm: Optional[str] = None
    public_key: Optional[str] = None
    signature: Optional[str] = None


class DataSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    file_count: int = 0
    total_bytes: int = 0
    content_hash: Optional[str] = None


class RecordReceipt(BaseModel):
    """Per-record receipt — mirror of the canonical `RecordReceipt` wire type.

    Forever-Standard: field-additive Optional fields with
    `extra="allow"` so SDK-side parsing keeps working as the Rust struct
    grows.

    **No `control_tags` field by design.** The receipt is a direct
    evidence primitive: control-ID mapping is re-scoped, so control IDs are NEVER stamped
    into the signed receipt. Adapters derive `(jurisdiction × classification
    × pattern_tag) -> list[control_id]` at ingestion time via the
    published mapping artifact (`nanorix.control_map`). Three-layer
    separation: receipt (immutable signed primitive) / mapping (versioned
    artifact) / adapter (vendor pace).
    """

    model_config = ConfigDict(extra="allow")
    record_index: int
    record_id: str
    record_input_hash: str
    record_output_hash: str
    record_chain_hash: str
    record_activity_trail: Optional[List[Any]] = None
    pattern_tag: Optional[str] = None
    merkle_inclusion_proof: List[str] = Field(default_factory=list)


class ParentProofLink(BaseModel):
    """Cross-org parent-proof link — mirror of
    the canonical `ParentProofLink` wire type.

    Forever-Standard: optional fields skip-serialize when None
    so cross-impl byte-equivalence is preserved.
    """

    model_config = ConfigDict(extra="allow")
    parent_chain_hash: str
    parent_key_id: str
    parent_signature: str
    parent_role: Optional[str] = None
    parent_jurisdiction: Optional[str] = None
    parent_organization_tag: Optional[str] = None


class CdpDocument(BaseModel):
    """AuditRecord — the complete activity trail. Private to the data processor."""
    model_config = ConfigDict(extra="allow")
    version: Optional[str] = None
    capsule_id: str
    activity: List[ActivityEvent] = Field(default_factory=list)
    residency: Optional[ResidencyInfo] = None
    destruction: Optional[DestructionInfo] = None
    attestation: Optional[AttestationInfo] = None
    document_hash: Optional[str] = None
    destruction_trigger: Optional[str] = None
    # Reserved slot (2026-05-10): AuditProof scope
    # discriminator. V1 always None / absent (workload scope implicit).
    # Future Items 2 (sealed-proxy = Call) and 4 (sealed-middleware = Request)
    # populate this; Pattern 4 high-volume per-record AuditProofs use Batch.
    # Forever-Standard discipline: field-additive Optional;
    # pre-amendment AuditProofs deserialize via extra="allow".
    cdp_kind: Optional[str] = None  # Literal["workload", "request", "call", "batch"]
    # Wave-N per-record receipts + cross-org parent
    # composition. Field-additive Optional per Forever-Standard:
    # pre-Wave-N AuditProofs canonicalize byte-identically (both fields skip-
    # serialize when None). See the canonical verifier for the Rust
    # source struct. Receipts carry `pattern_tag` only — control IDs are
    # NEVER stamped into the signed primitive; adapters apply the
    # mapping artifact at ingestion time.
    record_receipts: Optional[List[RecordReceipt]] = None
    record_receipts_merkle_root: Optional[str] = None
    parent_proof_hashes: Optional[List[ParentProofLink]] = None
    parent_proofs_merkle_root: Optional[str] = None


class VerificationCdp(BaseModel):
    """
    AuditProof — the shareable proof. Zero operational details.
    Safe to share with auditors, regulators, and data subjects.
    """
    model_config = ConfigDict(extra="allow")
    version: Optional[str] = None
    capsule_id: str
    environment: Optional[Dict[str, Any]] = None
    data_ingested: Optional[DataSummary] = None
    data_extracted: Optional[DataSummary] = None
    residency: Optional[ResidencyInfo] = None
    destruction: Optional[DestructionInfo] = None
    attestation: Optional[AttestationInfo] = None
    document_hash: Optional[str] = None
    verify: Optional[str] = None
    # Reserved slot (2026-05-10): AuditProof scope
    # discriminator mirrored on shareable surface. See CdpDocument.cdp_kind
    # for full semantic. V1 always None.
    cdp_kind: Optional[str] = None  # Literal["workload", "request", "call", "batch"]
    # mirrored on the shareable AuditProof surface. See
    # CdpDocument for full semantics. Field-additive Optional per Forever-
    # Standard: pre-Wave-N AuditProofs canonicalize byte-
    # identically when None.
    record_receipts: Optional[List[RecordReceipt]] = None
    record_receipts_merkle_root: Optional[str] = None
    parent_proof_hashes: Optional[List[ParentProofLink]] = None
    parent_proofs_merkle_root: Optional[str] = None


class CdpAccess:
    """
    Wrapper returned after capsule destruction.
    Provides typed access to both CDP layers.
    """

    def __init__(
        self,
        full_data: Dict[str, Any],
        verification_data: Dict[str, Any],
        capsule_id: str,
    ) -> None:
        self._full_data = full_data
        self._verification_data = verification_data
        self._capsule_id = capsule_id

    @property
    def id(self) -> str:
        return self._capsule_id

    def full(self) -> CdpDocument:
        """AuditRecord with complete activity trail. Private to data processor."""
        return CdpDocument.model_validate(self._full_data)

    def verification(self) -> VerificationCdp:
        """AuditProof. Safe to share with auditors and regulators."""
        return VerificationCdp.model_validate(self._verification_data)

    @property
    def verify_url(self) -> str:
        """URL for independent CDP verification at nanorix.io/verify."""
        v = self.verification()
        if v.verify:
            return v.verify
        return f"https://nanorix.io/verify/{self._capsule_id}"

    def to_dict(self) -> Dict[str, Any]:
        """Raw full CDP as dictionary."""
        return self._full_data
