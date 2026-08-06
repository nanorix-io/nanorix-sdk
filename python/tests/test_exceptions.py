"""Exception hierarchy tests."""
from nanorix._exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ExecutionError,
    GoneError,
    NanorixError,
    NotFoundError,
    PayloadTooLargeError,
    QuotaExceededError,
    RateLimitError,
    _STATUS_MAP,
    raise_for_status,
)
import pytest


def test_status_code_mapping_complete():
    expected = {
        400: BadRequestError,
        401: AuthenticationError,
        402: QuotaExceededError,
        403: QuotaExceededError,
        404: NotFoundError,
        409: ConflictError,
        410: GoneError,
        413: PayloadTooLargeError,
        429: RateLimitError,
    }
    for code, cls in expected.items():
        assert _STATUS_MAP[code] is cls, f"Status {code} should map to {cls.__name__}"


def test_exception_preserves_body():
    body = {"error": "not found", "detail": "capsule_id=xyz"}
    with pytest.raises(NotFoundError) as exc_info:
        raise_for_status(404, body)
    assert exc_info.value.body == body
    assert exc_info.value.status_code == 404


def test_exception_repr_informative():
    err = NanorixError("test error", status_code=500, body={"error": "test"})
    r = repr(err)
    assert "NanorixError" in r
    assert "500" in r
    assert "test error" in r


def test_rate_limit_includes_retry_after():
    body = {"error": "rate limited", "retry_after": 2.5}
    with pytest.raises(RateLimitError) as exc_info:
        raise_for_status(429, body)
    assert exc_info.value.retry_after == 2.5
    assert exc_info.value.status_code == 429


def test_execution_error_includes_exit_code():
    err = ExecutionError(
        "failed",
        exit_code=137,
        stdout="output",
        stderr="killed",
    )
    assert err.exit_code == 137
    assert err.stdout == "output"
    assert err.stderr == "killed"
    assert err.cdp is None
