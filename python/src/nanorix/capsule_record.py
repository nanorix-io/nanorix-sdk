"""Per-record receipt + parent-proof SDK ergonomics (ADR-039 + ADR-041).

This module exposes the customer-facing surface for emitting per-record
receipts (ADR-039) and parent-proof links (ADR-041) against a live
capsule. Receipts and parent links buffer server-side per Wave A
(`services/api/src/routes/records.rs`) and bind into the AuditProof's
outer 8-step chain at capsule-destroy time via Merkle root incorporation
into Step 8 (`governance/rzl/src/wave_n.rs::compute_step_8_amended`).

## Three-layer separation discipline

| Layer | Carries | Evolves at |
|---|---|---|
| Receipt (ADR-039) | `pattern_tag` (signed primitive) | ADR-006 I0 Forever-Standard pace |
| Mapping artifact (ADR-040) | `(j × c × p) -> list[control_id]` | Framework revision pace |
| Adapter (Vanta/Drata/internal) | Reads signed receipt + applies mapping | Vendor pace |

**Control IDs are NEVER stamped into the signed receipt** — they are
derived at adapter time by applying the ADR-040 published mapping
artifact (`nanorix.control_map`) to `(jurisdiction × classification ×
pattern_tag)`. Stamping mapping output inside the cryptographic receipt
would freeze historical receipts with stale control IDs forever when
frameworks revise.

The signed receipt carries `pattern_tag` only; the adapter applies the
mapping freshly at ingestion time using whichever `framework_version` is
current at the moment of GRC evidence-filing.

## Vocabulary discipline (CCO policy)

- Required relationship vocabulary: `applies_to` / `references` / `related_to`.
- The receipt attests to evidence; the auditor adjudicates substantive
  acceptability (ADR-017 three-role liability split).
- See `nanorix.control_map.DISCLAIMER` for the canonical disclaimer surface
  that every adapter consumer surfaces alongside the resolved control IDs.
- Verdict vocabulary (the four banned terms) is enforced by the pre-publish
  6-scan gate in `tests/test_capsule_record.py` — kept out of every
  customer-facing SDK surface.

## Usage — per-record emission

```python
from nanorix import Client

client = Client(api_key="nrx_live_...")

with client.capsules.create(capsulefile="cf_pa") as capsule:
    for pa in batch:
        with capsule.record(record_id=pa.id, pattern_tag="pa") as r:
            result = process_pa(pa)
            r.bind_output(result)
            r.event({"type": "ReasoningStepCompleted", "step_id": "ddx"})
    # capsule destroy returns the AuditProof with receipts[] populated
```

## Usage — cross-org parent proof

```python
with client.capsules.create(capsulefile="cf_pa") as capsule:
    capsule.add_parent_proof(
        parent_chain_hash="sha512:def...",
        parent_key_id="cust-auth-banjo-prod",
        parent_signature="base64:...",
        parent_role="input-data-source",
        parent_organization_tag="vendor:banjo-health",
        parent_jurisdiction="US",
    )
    # ... per-record emission ...
```
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Union

from nanorix._exceptions import BadRequestError
from nanorix._jcs import canonicalize

__all__ = [
    "PATTERN_TAGS",
    "RecordContext",
    "AsyncRecordContext",
    "validate_pattern_tag",
    "sha512_hex",
    "canonical_hash",
    "MAX_RECORD_ID_LEN",
    "MAX_HASH_LEN",
    "MAX_PARENT_KEY_ID_LEN",
    "MAX_PARENT_SIG_LEN",
    "MAX_PARENT_ROLE_LEN",
    "MAX_PARENT_JURISDICTION_LEN",
    "MAX_PARENT_ORG_TAG_LEN",
]


# ── Closed-enum PatternTag mirror of Rust `nanorix_rzl::types::PatternTag` ──
#
# Per ADR-039 §"pattern_tag rule" and `governance/rzl/src/types.rs::PatternTag`,
# the closed enum is append-only at Forever-Standard pace. The wire form is
# snake_case (matches the Rust serde rename_all="snake_case" output).
#
# Per ADR-006 I0: this tuple is APPEND-ONLY. New variants land via ADR
# amendment; existing variants are NEVER renamed or removed.
PATTERN_TAGS: tuple[str, ...] = (
    # Pattern 1 — extraction / decision
    "pa",
    "extraction",
    "annotation",
    # Pattern 2 — multi-step agent
    "agent_step",
    "agent_turn",
    # Pattern 4 — high-volume per-record
    "rcm_claim",
    "rcm_eligibility",
    "rcm_remit",
    "ncpdp_script",
    # Pattern 6 — imaging
    "dicom_study",
    "dicom_sr",
    # Pattern 8 — long-horizon
    "screening_hit",
    "fhir_record",
    # Generic
    "ehr_document",
    "custom",
)


# ── Field length limits (mirror `services/api/src/routes/records.rs`) ──

MAX_RECORD_ID_LEN: int = 256
MAX_HASH_LEN: int = 144  # "sha512:" + 128 hex
MAX_PARENT_KEY_ID_LEN: int = 256
MAX_PARENT_SIG_LEN: int = 256
MAX_PARENT_ROLE_LEN: int = 128
MAX_PARENT_JURISDICTION_LEN: int = 16
MAX_PARENT_ORG_TAG_LEN: int = 256
MAX_ACTIVITY_TRAIL_LEN: int = 1024


def validate_pattern_tag(pattern_tag: Optional[str]) -> Optional[str]:
    """Validate that `pattern_tag` matches the closed enum.

    Per ADR-039 §"pattern_tag rule" + ADR-013 customer-sole-declarer:
    Nanorix records the customer-declared pattern verbatim; this validator
    only enforces that the declared value is one of the canonical closed-
    enum variants. Case-sensitive snake_case matching the Rust serde
    rename_all="snake_case" wire form.

    Args:
        pattern_tag: Customer-declared pattern tag string, or None.

    Returns:
        The pattern_tag unchanged when valid (or None when None passed in).

    Raises:
        BadRequestError: If `pattern_tag` is not a recognized variant.
    """
    if pattern_tag is None:
        return None
    if pattern_tag not in PATTERN_TAGS:
        raise BadRequestError(
            f"pattern_tag {pattern_tag!r} is not a recognized variant. "
            f"Valid variants per ADR-039 §'pattern_tag rule': "
            f"{', '.join(PATTERN_TAGS)}",
            status_code=400,
        )
    return pattern_tag


def sha512_hex(data: bytes) -> str:
    """SHA-512 of raw bytes -> `sha512:{hex}` wire form.

    Matches the Rust API's `record_input_hash` / `record_output_hash` prefix
    format per ADR-039 §"Architecture".
    """
    return "sha512:" + hashlib.sha512(data).hexdigest()


def canonical_hash(value: Any) -> str:
    """JCS-canonicalize `value` per RFC 8785 + SHA-512 -> `sha512:{hex}`.

    Mirrors the Rust `serde_jcs::to_vec(value)` -> `SHA-512` path used by
    `compute_activity_root` and other cryptographic-boundary primitives.
    Cross-impl byte-equivalent to the Rust serializer (see
    `sdk/python/src/nanorix/_jcs.py` for the RFC 8785 implementation).

    Args:
        value: JSON-serializable Python object (dict, list, str, int, float,
            bool, None, or nested combinations). Bytes inputs are not
            JSON-serializable; pass `sha512_hex(bytes)` instead.

    Returns:
        `sha512:{128 hex chars}` matching the Rust `RecordReceipt`
        `record_input_hash` / `record_output_hash` wire form.
    """
    canonical_bytes = canonicalize(value)
    return sha512_hex(canonical_bytes)


def _hash_payload(data: Union[bytes, str, Dict[str, Any], List[Any]]) -> str:
    """Hash an arbitrary payload to `sha512:{hex}` wire form.

    Dispatch table:
        - bytes -> SHA-512 of raw bytes
        - str   -> SHA-512 of UTF-8 encoded bytes (per JCS string convention)
        - dict / list -> JCS canonical-bytes -> SHA-512
    """
    if isinstance(data, bytes):
        return sha512_hex(data)
    if isinstance(data, str):
        return sha512_hex(data.encode("utf-8"))
    if isinstance(data, (dict, list)):
        return canonical_hash(data)
    raise BadRequestError(
        f"bind_output / bind_input value must be bytes, str, dict, or list; "
        f"got {type(data).__name__}",
        status_code=400,
    )


# ── RecordContext ────────────────────────────────────────────────────


class _RecordContextBase:
    """Shared state for the synchronous + async `RecordContext`.

    Both subclasses delegate the buffer logic to this base; only the HTTP
    POST step diverges (sync vs await). Keeping the state machine in one
    place ensures cross-mode semantic parity.
    """

    def __init__(
        self,
        capsule_id: str,
        record_id: str,
        pattern_tag: Optional[str],
    ) -> None:
        if not record_id or not isinstance(record_id, str):
            raise BadRequestError(
                "record_id must be a non-empty string per ADR-039 §'Architecture' "
                "(customer-sole-declarer per ADR-013)",
                status_code=400,
            )
        if len(record_id) > MAX_RECORD_ID_LEN:
            raise BadRequestError(
                f"record_id length must be <= {MAX_RECORD_ID_LEN} chars "
                f"(got {len(record_id)})",
                status_code=400,
            )

        self.capsule_id = capsule_id
        self.record_id = record_id
        self.pattern_tag: Optional[str] = validate_pattern_tag(pattern_tag)
        self._record_input_hash: Optional[str] = None
        self._record_output_hash: Optional[str] = None
        self._activity_trail: List[Dict[str, Any]] = []
        self._closed = False

    def bind_input(self, data: Union[bytes, str, Dict[str, Any], List[Any]]) -> None:
        """Bind the record's input payload as a SHA-512 hash on the receipt.

        Args:
            data: Raw bytes, a UTF-8 string, or a JSON-serializable object.
                bytes -> SHA-512 of the bytes. str -> SHA-512 of UTF-8 bytes.
                dict/list -> JCS canonical-bytes -> SHA-512 (RFC 8785).

        The resulting `sha512:{hex}` value is sent to the server in the
        `record_input_hash` field of the create-record request.
        """
        self._record_input_hash = _hash_payload(data)

    def bind_output(self, data: Union[bytes, str, Dict[str, Any], List[Any]]) -> None:
        """Bind the record's output payload as a SHA-512 hash on the receipt.

        Same hashing semantics as `bind_input`. The resulting `sha512:{hex}`
        value is sent in the `record_output_hash` field.
        """
        self._record_output_hash = _hash_payload(data)

    def event(self, activity_event: Dict[str, Any]) -> None:
        """Append a per-record activity event to the receipt's activity trail.

        Args:
            activity_event: A JSON-serializable dict describing the event.
                The Rust API consumes the trail as `Vec<serde_json::Value>`
                per `services/api/src/routes/records.rs::CreateRecordRequest`;
                arbitrary JSON structure is accepted but client-side payload
                limit is `MAX_ACTIVITY_TRAIL_LEN` events.

        Per ADR-028 + Wave 18-11 cdp_kind discipline: customer-supplied
        events flow through Nanorix verbatim. The activity trail is
        canonicalized via JCS + chained via `compute_activity_root` at
        chain-hash computation time.
        """
        if not isinstance(activity_event, dict):
            raise BadRequestError(
                "event must be a dict per the ActivityEvent wire form",
                status_code=400,
            )
        if len(self._activity_trail) >= MAX_ACTIVITY_TRAIL_LEN:
            raise BadRequestError(
                f"activity_trail length must be <= {MAX_ACTIVITY_TRAIL_LEN}",
                status_code=400,
            )
        self._activity_trail.append(activity_event)

    def _build_request_body(self) -> Dict[str, Any]:
        """Construct the JSON body for `POST /v1/capsules/:id/record`.

        Only fields with declared values are emitted; the Rust handler
        treats absent fields as the documented default (genesis hash for
        unset input/output; None for unset pattern_tag).
        """
        body: Dict[str, Any] = {"record_id": self.record_id}
        if self.pattern_tag is not None:
            body["pattern_tag"] = self.pattern_tag
        if self._record_input_hash is not None:
            body["record_input_hash"] = self._record_input_hash
        if self._record_output_hash is not None:
            body["record_output_hash"] = self._record_output_hash
        if self._activity_trail:
            body["activity_trail"] = list(self._activity_trail)
        return body

    def _mark_closed(self) -> None:
        self._closed = True


class RecordContext(_RecordContextBase):
    """Synchronous per-record receipt context manager.

    Returned by `LiveCapsule.record(record_id, pattern_tag)`. On context
    exit, POSTs the buffered receipt to `/v1/capsules/:id/record`. The
    Wave A endpoint computes the `record_chain_hash` server-side and
    appends the receipt to the in-memory wave-N buffer; the receipt is
    bound into the AuditProof's outer chain via Merkle root at capsule
    destroy time.

    Customers do NOT need to compute Merkle inclusion proofs client-side
    — the server emits them in the response and re-binds them at destroy
    time as the final receipt set is known.

    The receipt carries `pattern_tag` only. **Control IDs are NEVER
    stamped into the signed receipt** — adapters apply the ADR-040
    published mapping at ingestion time via `nanorix.control_map`.
    """

    def __init__(
        self,
        capsule_id: str,
        record_id: str,
        pattern_tag: Optional[str],
        post_callback: Any,
    ) -> None:
        super().__init__(capsule_id, record_id, pattern_tag)
        # `post_callback` is the bound HTTP-POST function provided by the
        # parent `LiveCapsule`. Decoupling avoids a circular import with
        # `resources.capsules` and lets tests inject a stub.
        self._post = post_callback
        self.response: Optional[Dict[str, Any]] = None

    def __enter__(self) -> "RecordContext":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # Surface caller exceptions verbatim — never swallow.
        # On error path, do NOT emit the receipt (the customer's intent
        # is to abort this record's emission). Caller can re-enter a new
        # context if they want a partial-failure record.
        if exc_type is not None:
            self._mark_closed()
            return None
        body = self._build_request_body()
        self.response = self._post(self.capsule_id, body)
        self._mark_closed()
        return None

    @property
    def record_chain_hash(self) -> Optional[str]:
        """Return the `record_chain_hash` from the server response (post-exit)."""
        if self.response is None:
            return None
        return self.response.get("record_chain_hash")

    @property
    def record_index(self) -> Optional[int]:
        """Return the zero-indexed `record_index` from the server response."""
        if self.response is None:
            return None
        return self.response.get("record_index")


class AsyncRecordContext(_RecordContextBase):
    """Asynchronous per-record receipt context manager.

    Mirror of `RecordContext` for the `AsyncLiveCapsule` surface. The same
    state machine + validation; only the HTTP POST is awaited.
    """

    def __init__(
        self,
        capsule_id: str,
        record_id: str,
        pattern_tag: Optional[str],
        post_callback: Any,
    ) -> None:
        super().__init__(capsule_id, record_id, pattern_tag)
        self._post = post_callback
        self.response: Optional[Dict[str, Any]] = None

    async def __aenter__(self) -> "AsyncRecordContext":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self._mark_closed()
            return None
        body = self._build_request_body()
        self.response = await self._post(self.capsule_id, body)
        self._mark_closed()
        return None

    @property
    def record_chain_hash(self) -> Optional[str]:
        if self.response is None:
            return None
        return self.response.get("record_chain_hash")

    @property
    def record_index(self) -> Optional[int]:
        if self.response is None:
            return None
        return self.response.get("record_index")


# ── Parent proof validation helpers ──────────────────────────────────


def validate_parent_link_fields(
    parent_chain_hash: str,
    parent_key_id: str,
    parent_signature: str,
    parent_role: Optional[str] = None,
    parent_jurisdiction: Optional[str] = None,
    parent_organization_tag: Optional[str] = None,
) -> None:
    """Validate parent-proof link fields against ADR-041 + API length caps.

    Raises BadRequestError on any field-length violation. Field constraints
    mirror `services/api/src/routes/records.rs::create_parent`.
    """
    if not parent_chain_hash or not isinstance(parent_chain_hash, str):
        raise BadRequestError(
            "parent_chain_hash must be a non-empty string per ADR-041",
            status_code=400,
        )
    if len(parent_chain_hash) > MAX_HASH_LEN:
        raise BadRequestError(
            f"parent_chain_hash length must be <= {MAX_HASH_LEN} chars "
            f"(got {len(parent_chain_hash)})",
            status_code=400,
        )
    if not parent_key_id or not isinstance(parent_key_id, str):
        raise BadRequestError(
            "parent_key_id must be a non-empty string per ADR-041",
            status_code=400,
        )
    if len(parent_key_id) > MAX_PARENT_KEY_ID_LEN:
        raise BadRequestError(
            f"parent_key_id length must be <= {MAX_PARENT_KEY_ID_LEN} chars",
            status_code=400,
        )
    if not parent_signature or not isinstance(parent_signature, str):
        raise BadRequestError(
            "parent_signature must be a non-empty string per ADR-041",
            status_code=400,
        )
    if len(parent_signature) > MAX_PARENT_SIG_LEN:
        raise BadRequestError(
            f"parent_signature length must be <= {MAX_PARENT_SIG_LEN} chars",
            status_code=400,
        )
    if parent_role is not None and len(parent_role) > MAX_PARENT_ROLE_LEN:
        raise BadRequestError(
            f"parent_role length must be <= {MAX_PARENT_ROLE_LEN} chars",
            status_code=400,
        )
    if (
        parent_jurisdiction is not None
        and len(parent_jurisdiction) > MAX_PARENT_JURISDICTION_LEN
    ):
        raise BadRequestError(
            f"parent_jurisdiction length must be <= {MAX_PARENT_JURISDICTION_LEN} chars",
            status_code=400,
        )
    if (
        parent_organization_tag is not None
        and len(parent_organization_tag) > MAX_PARENT_ORG_TAG_LEN
    ):
        raise BadRequestError(
            f"parent_organization_tag length must be <= {MAX_PARENT_ORG_TAG_LEN} chars",
            status_code=400,
        )


def build_parent_link_body(
    parent_chain_hash: str,
    parent_key_id: str,
    parent_signature: str,
    parent_role: Optional[str] = None,
    parent_jurisdiction: Optional[str] = None,
    parent_organization_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the JSON body for `POST /v1/capsules/:id/parent`.

    Only fields with declared values are emitted; the Rust handler treats
    absent optional fields as `None` per ADR-041 §"Architecture".
    """
    body: Dict[str, Any] = {
        "parent_chain_hash": parent_chain_hash,
        "parent_key_id": parent_key_id,
        "parent_signature": parent_signature,
    }
    if parent_role is not None:
        body["parent_role"] = parent_role
    if parent_jurisdiction is not None:
        body["parent_jurisdiction"] = parent_jurisdiction
    if parent_organization_tag is not None:
        body["parent_organization_tag"] = parent_organization_tag
    return body
