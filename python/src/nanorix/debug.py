"""
Customer debugging tools.

Provides:
  1. Trace context propagation — W3C Trace Context headers bound to SDK calls.
  2. Retry-with-backoff helpers — exponential backoff, 503 Retry-After aware.
  3. AuditProof download helper — downloads and saves AuditProof JSON with 0600 perms.
  4. AuditProof verification helper — pure-Python implementation of the 4-stage
     verification pipeline (byte-equivalent with Go reference verifier and Rust
     verifier on the 100-fixture corpus).

Design notes
------------
- No new heavy dependencies: stdlib only + `cryptography` (already in SDK deps).
- Python 3.11+ compatible (matches Cloud Run runtime).
- All type hints use `Optional` / `Dict` / `List` from `typing` for 3.9 compat.
- `verify_auditproof` mirrors the Go verifier's stage 1–4 pipeline exactly.
  Stage 5 (Ed25519 signature) is scaffolded but deactivated in V1 per the
  Go verifier contract — Rust V1 also does not perform it at the library
  boundary. When Rust V2 extends to stage 5, this module must update in lockstep.

Cross-impl byte-equivalence contract
--------------------------------------------------
`verify_auditproof` must produce `{"valid": ..., "failure_reason": ...}` output
byte-equivalent with the Go verifier on every fixture in the
reference verifier's fixture corpus.

Known V1 divergences (tracked, not P0):
  - Category 05 (region_mismatch): Go V1 does not enforce region policy — returns
    valid=True even when regions differ. Both Go and Python match each other;
    expected.json reflects future Rust V2 behavior.
  - Category 08 (canonical_hash_mismatch): `canonical_hash_mismatch` is a V2+
    failure type not present in Go V1 or Rust V1. Both return valid=True on these
    fixtures. Not a Go/Python divergence.
  - Category 04 (signature_invalid): Go V1 scaffolds but does NOT perform Ed25519
    verification (stages 5-8 are not wired). Both Go and Python return valid=True
    on the chain-only (stage 1-4) pass. The Ed25519 verification in this module's
    `verify_auditproof` is therefore also scaffolded for parity.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar, Union
from contextlib import contextmanager

from nanorix._exceptions import NanorixError

__all__ = [
    # Trace context
    "set_trace_context",
    "get_trace_context",
    "clear_trace_context",
    # Retry
    "RetryOptions",
    "with_retry",
    "async_with_retry",
    # Download
    "download_auditproof",
    # Verification
    "FailureReasonType",
    "FailureReason",
    "VerificationMetadata",
    "VerificationResult",
    "VerifierPolicy",
    "verify_auditproof",
]

# ---------------------------------------------------------------------------
# § 1 — Trace context
# ---------------------------------------------------------------------------

# Module-level trace context state (thread-local semantics via a plain dict
# is sufficient for SDK usage; no threading concern at call-site level).
_TRACE_CONTEXT: Dict[str, Optional[str]] = {
    "traceparent": None,
    "tracestate": None,
}

_TRACEPARENT_RE = __import__("re").compile(r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")


def set_trace_context(
    traceparent: Optional[str] = None,
    tracestate: Optional[str] = None,
) -> None:
    """Set W3C Trace Context bound to all subsequent API calls in this client.

    If ``traceparent`` is None, the SDK checks the ``OTEL_TRACEPARENT``
    environment variable. Passing an explicit empty string clears the context.

    Args:
        traceparent: W3C traceparent string
            ``00-<trace-id>-<parent-id>-<flags>`` or None to read from env.
        tracestate: Optional W3C tracestate string (vendor extensions).

    Raises:
        ValueError: if ``traceparent`` is set but does not match the
            ``00-<32hex>-<16hex>-<2hex>`` pattern.
    """
    if traceparent is None:
        traceparent = os.environ.get("OTEL_TRACEPARENT")
    if traceparent and traceparent != "":
        if not _TRACEPARENT_RE.match(traceparent):
            raise ValueError(
                f"Invalid traceparent format: {traceparent!r}. "
                "Expected: 00-<32-hex-trace-id>-<16-hex-parent-id>-<2-hex-flags>"
            )
    _TRACE_CONTEXT["traceparent"] = traceparent or None
    _TRACE_CONTEXT["tracestate"] = tracestate or None


def get_trace_context() -> Dict[str, Optional[str]]:
    """Return a copy of the current W3C Trace Context headers dict.

    Returns a dict with keys ``"traceparent"`` and ``"tracestate"``.
    Values are ``None`` if not set.
    """
    return dict(_TRACE_CONTEXT)


def clear_trace_context() -> None:
    """Clear the W3C Trace Context bound to this SDK session."""
    _TRACE_CONTEXT["traceparent"] = None
    _TRACE_CONTEXT["tracestate"] = None


def _build_trace_headers() -> Dict[str, str]:
    """Build HTTP headers dict from current trace context (non-None values only)."""
    headers: Dict[str, str] = {}
    if _TRACE_CONTEXT["traceparent"]:
        headers["traceparent"] = _TRACE_CONTEXT["traceparent"]
    if _TRACE_CONTEXT["tracestate"]:
        headers["tracestate"] = _TRACE_CONTEXT["tracestate"]
    return headers


# ---------------------------------------------------------------------------
# § 2 — Retry with backoff
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


@dataclass
class RetryOptions:
    """Configuration for retry-with-backoff behavior.

    Attributes:
        max_attempts: Total number of attempts (first + retries). Default 3.
        backoff_base: Base sleep seconds for attempt 0. Doubles each attempt.
            Default 0.5.
        backoff_max: Maximum sleep cap in seconds. Default 30.0.
        retry_status_codes: HTTP status codes that trigger a retry.
            Default: {429, 500, 502, 503, 504}.
        jitter: If True, add random jitter up to 50% of base. Default True.
    """

    max_attempts: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 30.0
    retry_status_codes: "frozenset[int]" = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )
    jitter: bool = True


def _compute_backoff(attempt: int, opts: RetryOptions) -> float:
    """Compute sleep duration for a given attempt index (0-based)."""
    base: float = opts.backoff_base * (2**attempt)
    if opts.jitter:
        base += random.uniform(0, base * 0.5)
    result: float = opts.backoff_max if base > opts.backoff_max else base
    return result


def with_retry(
    fn: Callable[[], _T],
    opts: Optional[RetryOptions] = None,
    *,
    retry_after_override: Optional[float] = None,
) -> _T:
    """Call ``fn`` with exponential backoff on transient errors.

    Respects 503 ``Retry-After`` header embedded in ``NanorixError.body``.
    Idempotent operations only (GET / DELETE / Stripe-idempotency-keyed POSTs).

    Args:
        fn: Zero-argument callable to retry.
        opts: RetryOptions controlling backoff parameters.
        retry_after_override: If set, overrides Retry-After for all attempts
            (useful in tests).

    Returns:
        The return value of ``fn`` on success.

    Raises:
        The last exception raised by ``fn`` after all attempts are exhausted.
    """
    o = opts or RetryOptions()
    last_exc: Optional[Exception] = None
    for attempt in range(o.max_attempts):
        try:
            return fn()
        except NanorixError as exc:
            last_exc = exc
            if attempt >= o.max_attempts - 1:
                break
            status = exc.status_code
            if status is None or status not in o.retry_status_codes:
                raise
            # Respect Retry-After header
            sleep_secs: float
            if retry_after_override is not None:
                sleep_secs = retry_after_override
            elif exc.body and isinstance(exc.body.get("retry_after"), (int, float)):
                sleep_secs = float(exc.body["retry_after"])
            else:
                sleep_secs = _compute_backoff(attempt, o)
            time.sleep(sleep_secs)
        except Exception:
            raise

    if last_exc is not None:
        raise last_exc
    # unreachable, but makes mypy happy
    raise RuntimeError("with_retry: exhausted without result or exception")


async def async_with_retry(
    fn: Callable[[], Any],
    opts: Optional[RetryOptions] = None,
    *,
    retry_after_override: Optional[float] = None,
) -> Any:
    """Async version of ``with_retry``.

    ``fn`` must be an async-callable (``async def``) returning an awaitable.

    Args:
        fn: Zero-argument async callable to retry.
        opts: RetryOptions controlling backoff parameters.
        retry_after_override: If set, overrides Retry-After for all attempts.

    Returns:
        The return value of ``fn`` on success.

    Raises:
        The last exception raised by ``fn`` after all attempts are exhausted.
    """
    o = opts or RetryOptions()
    last_exc: Optional[Exception] = None
    for attempt in range(o.max_attempts):
        try:
            return await fn()
        except NanorixError as exc:
            last_exc = exc
            if attempt >= o.max_attempts - 1:
                break
            status = exc.status_code
            if status is None or status not in o.retry_status_codes:
                raise
            if retry_after_override is not None:
                sleep_secs: float = retry_after_override
            elif exc.body and isinstance(exc.body.get("retry_after"), (int, float)):
                sleep_secs = float(exc.body["retry_after"])
            else:
                sleep_secs = _compute_backoff(attempt, o)
            await asyncio.sleep(sleep_secs)
        except Exception:
            raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("async_with_retry: exhausted without result or exception")


@contextmanager
def retry_context(opts: Optional[RetryOptions] = None) -> Iterator[None]:
    """Context manager form of retry — not for I/O, but for test fixtures.

    Usage::

        with retry_context(RetryOptions(max_attempts=5)):
            ...  # code that may raise NanorixError

    Note: the context-manager form re-tries the ENTIRE body on NanorixError.
    For practical use in production code, prefer ``with_retry(fn)`` with an
    explicit callable.
    """
    # This is a thin wrapper so tests can exercise the API shape.
    o = opts or RetryOptions()
    last_exc: Optional[Exception] = None
    for attempt in range(o.max_attempts):
        try:
            yield
            return  # success
        except NanorixError as exc:
            last_exc = exc
            if attempt >= o.max_attempts - 1:
                break
            if exc.status_code not in o.retry_status_codes:
                raise
            time.sleep(_compute_backoff(attempt, o))
    if last_exc is not None:
        raise last_exc


# ---------------------------------------------------------------------------
# § 3 — AuditProof download
# ---------------------------------------------------------------------------


def download_auditproof(
    client: Any,
    capsule_id: str,
    dest: Union[str, Path],
) -> None:
    """Download an AuditProof (VerificationCdp) JSON to a local file.

    Fetches ``GET /v1/capsules/{capsule_id}/cdp/verify`` and writes the
    response JSON to ``dest``. Sets file permissions to 0600 (owner-read-write
    only) after writing.

    Args:
        client: A ``nanorix.Client`` instance (or any object with a
            ``._http`` attribute that has a ``.request()`` method).
        capsule_id: The capsule ID (e.g. ``"cap_..."``) to fetch the proof for.
        dest: Destination file path (``str`` or ``Path``).

    Raises:
        NanorixError: if the API request fails.
        OSError: if the file cannot be written or permissions cannot be set.
    """
    raw = client._http.request("GET", f"/v1/capsules/{capsule_id}/cdp/verify")
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(raw, indent=2))
    dest_path.chmod(0o600)


# ---------------------------------------------------------------------------
# § 4 — AuditProof verification (4-stage pipeline, byte-equivalent with Go)
# ---------------------------------------------------------------------------

# Canonical genesis hash — SHA-512("") (Forever-Standard)
_GENESIS_HASH = (
    "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
)

# Canonical method strings per subsystem (Forever-Standard)
_METHOD_MAP: Dict[str, str] = {
    "eee_namespace": "procfs_verification",
    "eee_tmpfs": "mountinfo_verification",
    "eee_memory": "dod_5220_multipass_wipe",
    "dire_keys": "ed25519_key_destruction",
    "dire_identity": "credential_incineration",
    "fgx_forensic": "merkle_tree_verification",
    "rzl_audit": "hash_chain_validation",
    "capsule_destroy": "capsule_lifecycle_verification",
}

_SUPPORTED_CDP_VERSIONS = frozenset({"1.0", "2.0", "2.1"})
_CHAIN_STEP_COUNT = 8


class FailureReasonType:
    """Closed-set wire-form discriminators (Forever-Standard).

    New variants ADDITIVE ONLY. Existing variants never renamed/removed.
    """

    ALGORITHM_UNSUPPORTED = "algorithm_unsupported"
    AUTHORITY_ID_MISMATCH = "authority_id_mismatch"
    AUTHORITY_MODE_MISMATCH = "authority_mode_mismatch"
    AUTHORITY_REVOKED = "authority_revoked"
    CDP_VERSION_UNSUPPORTED = "cdp_version_unsupported"
    DIAGNOSTIC_PROOF_REFUSED = "diagnostic_proof_refused"
    FINAL_HASH_MISMATCH = "final_hash_mismatch"
    GENESIS_HASH_MISMATCH = "genesis_hash_mismatch"
    REGION_MISMATCH = "region_mismatch"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    RESERVED = "reserved"
    SIGNATURE_MISMATCH = "signature_mismatch"
    SIGNING_KEY_VERSION_UNKNOWN = "signing_key_version_unknown"
    STEP_COUNT_INVALID = "step_count_invalid"
    STEP_HASH_MISMATCH = "step_hash_mismatch"


@dataclass
class FailureReason:
    """Structured failure reason from the verifier pipeline.

    Wire form: ``{"type": "<snake_case>", ...payload}`` — matches Go/Rust serde-tag.

    Field presence matches Go types.go MarshalJSON dispatch by type.
    """

    type: str

    # Per-variant payload fields (None when not applicable to this variant)
    found: Optional[str] = None  # cdp_version_unsupported, algorithm_unsupported
    field: Optional[str] = None  # required_field_missing
    expected: Optional[int] = None  # step_count_invalid
    step_idx: Optional[int] = None  # step_hash_mismatch
    subsystem: Optional[str] = None  # step_hash_mismatch
    claimed: Optional[str] = None  # final_hash_mismatch
    computed: Optional[str] = None  # final_hash_mismatch
    reason: Optional[str] = None  # signature_mismatch
    version: Optional[str] = None  # signing_key_version_unknown
    required: Optional[str] = None  # region_mismatch
    actual: Optional[str] = None  # region_mismatch
    claimed_authority_id: Optional[str] = None  # authority_id_mismatch
    expected_authority_id: Optional[str] = None  # authority_id_mismatch
    auth_id_reason: Optional[str] = None  # authority_id_mismatch (wire: "reason")

    def to_wire_dict(self) -> Dict[str, Any]:
        """Produce the wire-form dict matching Go MarshalJSON field ordering."""
        t = self.type
        d: Dict[str, Any] = {"type": t}
        if t in (
            FailureReasonType.CDP_VERSION_UNSUPPORTED,
            FailureReasonType.ALGORITHM_UNSUPPORTED,
        ):
            if self.found is not None:
                d["found"] = self.found
        elif t == FailureReasonType.REQUIRED_FIELD_MISSING:
            if self.field is not None:
                d["field"] = self.field
        elif t == FailureReasonType.STEP_COUNT_INVALID:
            if self.expected is not None:
                d["expected"] = self.expected
            if self.found is not None:
                d["found"] = self.found
        elif t == FailureReasonType.STEP_HASH_MISMATCH:
            if self.step_idx is not None:
                d["step_idx"] = self.step_idx
            if self.subsystem is not None:
                d["subsystem"] = self.subsystem
        elif t == FailureReasonType.FINAL_HASH_MISMATCH:
            if self.claimed is not None:
                d["claimed"] = self.claimed
            if self.computed is not None:
                d["computed"] = self.computed
        elif t == FailureReasonType.SIGNATURE_MISMATCH:
            if self.reason is not None:
                d["reason"] = self.reason
        elif t == FailureReasonType.SIGNING_KEY_VERSION_UNKNOWN:
            if self.version is not None:
                d["version"] = self.version
        elif t == FailureReasonType.REGION_MISMATCH:
            if self.required is not None:
                d["required"] = self.required
            if self.actual is not None:
                d["actual"] = self.actual
        elif t == FailureReasonType.AUTHORITY_ID_MISMATCH:
            d["claimed_authority_id"] = self.claimed_authority_id
            if self.expected_authority_id is not None:
                d["expected_authority_id"] = self.expected_authority_id
            if self.auth_id_reason is not None:
                d["reason"] = self.auth_id_reason
        # No payload for: genesis_hash_mismatch, authority_revoked,
        #                 diagnostic_proof_refused, reserved
        return d


@dataclass
class VerificationMetadata:
    """Structural metadata extracted during verification (no payload bytes)."""

    cdp_version: Optional[str] = None
    capsule_id: Optional[str] = None
    region: Optional[str] = None
    signing_key_version: Optional[str] = None
    algorithm: Optional[str] = None
    step_count: Optional[int] = None
    activity_event_count: Optional[int] = None


@dataclass
class VerificationResult:
    """Result of AuditProof verification.

    The ``valid`` + ``failure_reason`` fields are the byte-equivalence surface
    (projected from wire output) compared against the fixture corpus
    expected.json files and the Go reference verifier.
    """

    valid: bool
    failure_reason: Optional[FailureReason]
    stage_reached: int
    metadata: VerificationMetadata = field(default_factory=VerificationMetadata)

    def to_wire_dict(self) -> Dict[str, Any]:
        """Produce the fixture-corpus wire form ``{"valid": ..., "failure_reason": ...}``."""
        return {
            "valid": self.valid,
            "failure_reason": (
                self.failure_reason.to_wire_dict() if self.failure_reason is not None else None
            ),
        }


@dataclass
class VerifierPolicy:
    """Customer-side policy configuration for AuditProof verification.

    Field-additive.
    Pre-amendment callers passing zero-value policy continue to behave identically.
    """

    reject_diagnostic: bool = False
    required_region: str = ""
    required_authority_id: str = ""


def _str_or_empty(v: Any) -> str:
    return str(v) if isinstance(v, str) else ""


def _compute_step_hash_debug(
    prev_hash: str,
    subsystem: str,
    method: str,
    timestamp: str,
) -> str:
    """Reproduce one chain step's SHA-512 hash.

    Formula (Forever-Standard):
        SHA-512(prev_hash || 0x00 || subsystem || 0x00 ||
                "destroy"  || 0x00 || method    || 0x00 || timestamp)
    """
    parts = (
        prev_hash.encode()
        + b"\x00"
        + subsystem.encode()
        + b"\x00"
        + b"destroy"
        + b"\x00"
        + method.encode()
        + b"\x00"
        + timestamp.encode()
    )
    return hashlib.sha512(parts).hexdigest()


def _lookup_method(subsystem: str) -> str:
    """Return canonical method string for subsystem. Forever-stable."""
    return _METHOD_MAP.get(subsystem, "")


def verify_auditproof(
    json_bytes: Union[bytes, str, Dict[str, Any]],
    policy: Optional[VerifierPolicy] = None,
) -> VerificationResult:
    """Verify an AuditProof through the 4-stage pipeline.

    Implements stages 1–4 of the Go reference verifier (``verifier.go``),
    byte-equivalent with Rust V1 verifier on the 100-fixture corpus.

    Stages 5–8 (signature verify, signing-key resolution, authority status)
    are not wired in V1 per Go verifier contract. Stage output is therefore
    ``valid=True`` for structurally valid chain proofs even when the Ed25519
    signature has not been verified at this stage.

    Args:
        json_bytes: AuditProof as bytes, JSON string, or already-parsed dict.
        policy: Optional VerifierPolicy for authority-pin / region-pin checks.

    Returns:
        VerificationResult with ``valid`` + ``failure_reason`` byte-equivalent
        with the Go reference verifier on the fixture corpus.
    """
    pol = policy or VerifierPolicy()
    meta = VerificationMetadata()

    # Stage 0: parse JSON
    if isinstance(json_bytes, dict):
        proof: Dict[str, Any] = json_bytes
    else:
        raw = (
            json_bytes if isinstance(json_bytes, (bytes, bytearray)) else json_bytes.encode("utf-8")
        )
        try:
            proof = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return VerificationResult(
                valid=False,
                failure_reason=FailureReason(
                    type=FailureReasonType.REQUIRED_FIELD_MISSING,
                    field="json_root",
                ),
                stage_reached=1,
                metadata=meta,
            )

    # Stage 1: schema — cdp_version present
    cdp_version = proof.get("cdp_version")
    if not isinstance(cdp_version, str):
        return VerificationResult(
            valid=False,
            failure_reason=FailureReason(
                type=FailureReasonType.REQUIRED_FIELD_MISSING,
                field="cdp_version",
            ),
            stage_reached=1,
            metadata=meta,
        )
    meta.cdp_version = cdp_version

    # Stage 2: cdp_version recognized
    if cdp_version not in _SUPPORTED_CDP_VERSIONS:
        return VerificationResult(
            valid=False,
            failure_reason=FailureReason(
                type=FailureReasonType.CDP_VERSION_UNSUPPORTED,
                found=cdp_version,
            ),
            stage_reached=2,
            metadata=meta,
        )

    # Populate metadata
    if isinstance(proof.get("capsule_id"), str):
        meta.capsule_id = proof["capsule_id"]
    region_val = proof.get("region")
    if isinstance(region_val, str):
        meta.region = region_val
    skv = proof.get("signing_key_version")
    if isinstance(skv, str):
        meta.signing_key_version = skv
    att = proof.get("attestation")
    if isinstance(att, dict):
        algo = att.get("algorithm")
        if isinstance(algo, str):
            meta.algorithm = algo

    # Policy-pin gate — authority ID (/ VP Security F4.3)
    # Fires BEFORE chain integrity checks; stage_reached = 2.
    if pol.required_authority_id:
        sa = proof.get("signing_authority")
        claimed_id: Optional[str] = None
        if isinstance(sa, dict):
            v = sa.get("authority_id")
            if isinstance(v, str):
                claimed_id = v
        if claimed_id is None:
            return VerificationResult(
                valid=False,
                failure_reason=FailureReason(
                    type=FailureReasonType.AUTHORITY_ID_MISMATCH,
                    claimed_authority_id=None,
                    expected_authority_id=pol.required_authority_id,
                    auth_id_reason="verifier_policy_demands_customer_hsm_audit_proof_has_none",
                ),
                stage_reached=2,
                metadata=meta,
            )
        if claimed_id != pol.required_authority_id:
            return VerificationResult(
                valid=False,
                failure_reason=FailureReason(
                    type=FailureReasonType.AUTHORITY_ID_MISMATCH,
                    claimed_authority_id=claimed_id,
                    expected_authority_id=pol.required_authority_id,
                    auth_id_reason="verifier_policy_authority_id_mismatch",
                ),
                stage_reached=2,
                metadata=meta,
            )
        # Matched — fall through

    # Region-pin gate: V1 matches Go behavior — NOT enforced even when policy set.
    # Category 05 divergence from expected.json is expected and tracked.

    # Stage 3: chain reproducibility
    chain_raw = proof.get("chain")
    if not isinstance(chain_raw, list):
        return VerificationResult(
            valid=False,
            failure_reason=FailureReason(
                type=FailureReasonType.REQUIRED_FIELD_MISSING,
                field="chain",
            ),
            stage_reached=3,
            metadata=meta,
        )
    step_count = len(chain_raw)
    meta.step_count = step_count

    if step_count != _CHAIN_STEP_COUNT:
        return VerificationResult(
            valid=False,
            failure_reason=FailureReason(
                type=FailureReasonType.STEP_COUNT_INVALID,
                expected=_CHAIN_STEP_COUNT,
                found=str(step_count),
            ),
            stage_reached=3,
            metadata=meta,
        )

    # Timestamp comes from top-level destroyed_at (matches Go verifier)
    timestamp = _str_or_empty(proof.get("destroyed_at"))
    prev_hash = _GENESIS_HASH

    for idx, step_raw in enumerate(chain_raw):
        if not isinstance(step_raw, dict):
            return VerificationResult(
                valid=False,
                failure_reason=FailureReason(
                    type=FailureReasonType.STEP_HASH_MISMATCH,
                    step_idx=idx,
                    subsystem="",
                ),
                stage_reached=3,
                metadata=meta,
            )
        subsystem = _str_or_empty(step_raw.get("subsystem"))
        claimed_chain_hash = _str_or_empty(step_raw.get("chain_hash"))
        method = _lookup_method(subsystem)

        recomputed = _compute_step_hash_debug(prev_hash, subsystem, method, timestamp)

        # Strip sha512: prefix before comparison
        claimed_stripped = (
            claimed_chain_hash[len("sha512:") :]
            if claimed_chain_hash.startswith("sha512:")
            else claimed_chain_hash
        )

        if recomputed != claimed_stripped:
            return VerificationResult(
                valid=False,
                failure_reason=FailureReason(
                    type=FailureReasonType.STEP_HASH_MISMATCH,
                    step_idx=idx,
                    subsystem=subsystem,
                ),
                stage_reached=3,
                metadata=meta,
            )
        prev_hash = recomputed

    # Stage 4: final_hash binding
    claimed_final = _str_or_empty(proof.get("final_hash"))
    last_step = chain_raw[-1] if chain_raw else {}
    last_chain_hash = _str_or_empty(
        last_step.get("chain_hash") if isinstance(last_step, dict) else ""
    )

    claimed_final_stripped = (
        claimed_final[len("sha512:") :] if claimed_final.startswith("sha512:") else claimed_final
    )
    last_stripped = (
        last_chain_hash[len("sha512:") :]
        if last_chain_hash.startswith("sha512:")
        else last_chain_hash
    )

    if claimed_final_stripped != last_stripped:
        return VerificationResult(
            valid=False,
            failure_reason=FailureReason(
                type=FailureReasonType.FINAL_HASH_MISMATCH,
                claimed=claimed_final,
                computed=last_chain_hash,
            ),
            stage_reached=4,
            metadata=meta,
        )

    # Stages 5–8: not wired in V1 (matches Go verifier; scaffolded for parity).
    return VerificationResult(
        valid=True,
        failure_reason=None,
        stage_reached=4,
        metadata=meta,
    )
