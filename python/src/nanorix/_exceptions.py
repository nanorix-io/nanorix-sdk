"""
Exception hierarchy for the Nanorix SDK.

Every non-2xx HTTP response raises a typed exception. The base NanorixError
includes the HTTP status code, a human-readable message, and the raw response
body for debugging.

Design rule: exceptions that involve capsule destruction (ExecutionError,
CeilingError) ALWAYS include the CDP, because destruction still happened
and the proof must be accessible.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict, Optional, Type

if TYPE_CHECKING:
    from nanorix._models import Capsule, CdpDocument


class NanorixError(Exception):
    """Base exception for all Nanorix SDK errors."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(status_code={self.status_code}, message={str(self)})"


class AuthenticationError(NanorixError):
    """401 — Invalid or missing API key."""

    pass


class NotFoundError(NanorixError):
    """404 — Resource does not exist (or you don't have access)."""

    pass


class ConflictError(NanorixError):
    """409 — Resource state conflict (e.g., duplicate capsulefile name, capsule already destroyed)."""

    pass


class RateLimitError(NanorixError):
    """429 — Too many requests. SDK auto-retries with backoff before raising."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 429,
        body: Optional[Dict[str, Any]] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.retry_after = retry_after


class PayloadTooLargeError(NanorixError):
    """413 — File or request exceeds tier size limit."""

    pass


class GoneError(NanorixError):
    """410 — Capsule has been destroyed. Only CDP remains."""

    pass


class QuotaExceededError(NanorixError):
    """402/403 — Tier quota exceeded (capsulefile count, CDP count, concurrent capsules)."""

    pass


class BadRequestError(NanorixError):
    """400 — Invalid request parameters."""

    pass


class ExecutionError(NanorixError):
    """
    Non-zero exit code from capsule execution.
    The capsule may still be alive (workspace mode) or destroyed (.run() mode).
    If destroyed, the CDP is attached — destruction still happened.
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        cdp: Optional[CdpDocument] = None,
        status_code: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.cdp = cdp


class CeilingError(NanorixError):
    """
    Safety ceiling destroyed the capsule before user-initiated destroy.
    CDP is always attached — destruction happened, proof was generated.
    """

    def __init__(
        self,
        message: str,
        *,
        cdp: Optional[CdpDocument] = None,
        status_code: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.cdp = cdp


class TimeoutError(NanorixError):
    """
    Execution exceeded the specified timeout.
    The capsule is still alive — user can retry, debug, or destroy.
    """

    def __init__(
        self,
        message: str,
        *,
        capsule: Optional[Capsule] = None,
        status_code: Optional[int] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.capsule = capsule


# ── 422 Unprocessable Entity (P0 #4 — pre-SDK-1.0-publish debuggability) ──


class UnprocessableEntityError(NanorixError):
    """422 — Request well-formed but semantically rejected.

    Base for input_provenance_rejected + secrets_injection_rejected.
    Customer self-resolution paths key off `reason` field.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 422,
        body: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body)
        self.reason = reason


class InputProvenanceRejectedError(UnprocessableEntityError):
    """422 input_provenance_rejected — Manifest's input_hash didn't match
    the runtime's observed hash. Customer cross-checks their manifest
    against runtime hash via `input_hash` field.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 422,
        body: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        input_hash: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body, reason=reason)
        self.input_hash = input_hash


class SecretsInjectionRejectedError(UnprocessableEntityError):
    """422 secrets_injection_rejected — A declared secret failed to inject.

    Customer self-resolution paths key off `reason`:
    - `secret_not_found` → rotate_or_recreate(secret_id)
    - `decryption_failed` → alert_security_team(secret_id)
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = 422,
        body: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        secret_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, body=body, reason=reason)
        self.secret_id = secret_id


# ── Status code → exception mapping ──

_STATUS_MAP: Dict[int, Type[NanorixError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: QuotaExceededError,
    403: QuotaExceededError,
    404: NotFoundError,
    409: ConflictError,
    410: GoneError,
    413: PayloadTooLargeError,
    422: UnprocessableEntityError,  # default for 422; subclasses dispatch by error.code below
    429: RateLimitError,
}


def _extract_error_field(body: Dict[str, Any], field: str) -> Optional[str]:
    """Pull a string field from body.error if it's a dict."""
    err = body.get("error")
    if isinstance(err, dict):
        v = err.get(field)
        return v if isinstance(v, str) else None
    return None


def raise_for_status(status_code: int, body: Dict[str, Any]) -> None:
    """Raise the appropriate exception for a non-2xx response."""
    if 200 <= status_code < 300:
        return

    # Extract message: body.error.message preferred (typed errors); fallback
    # to body.error if it's a string; fallback to body.message.
    err = body.get("error")
    if isinstance(err, dict):
        message = err.get("message", f"HTTP {status_code}")
        error_code = err.get("code") if isinstance(err.get("code"), str) else None
    elif isinstance(err, str):
        message = err
        error_code = None
    else:
        message = body.get("message", f"HTTP {status_code}")
        error_code = None

    # 422: disambiguate by error.code
    if status_code == 422:
        if error_code == "input_provenance_rejected":
            raise InputProvenanceRejectedError(
                message,
                status_code=status_code,
                body=body,
                reason=_extract_error_field(body, "reason"),
                input_hash=_extract_error_field(body, "input_hash"),
            )
        if error_code == "secrets_injection_rejected":
            raise SecretsInjectionRejectedError(
                message,
                status_code=status_code,
                body=body,
                reason=_extract_error_field(body, "reason"),
                secret_id=_extract_error_field(body, "secret_id"),
            )
        raise UnprocessableEntityError(
            message,
            status_code=status_code,
            body=body,
            reason=_extract_error_field(body, "reason"),
        )

    exc_class = _STATUS_MAP.get(status_code, NanorixError)

    if exc_class is RateLimitError:
        raise RateLimitError(
            message,
            status_code=status_code,
            body=body,
            retry_after=body.get("retry_after"),
        )

    raise exc_class(message, status_code=status_code, body=body)
