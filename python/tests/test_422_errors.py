"""
P0 #4 / Pre-SDK-1.0-publish — 422 status code mapping tests.

These tests pin the wire contract:
- HTTP 422 status code → UnprocessableEntityError (or subclass)
- error.code = "input_provenance_rejected" → InputProvenanceRejectedError
- error.code = "secrets_injection_rejected" → SecretsInjectionRejectedError
- error.reason / error.input_hash / error.secret_id surface on the
  typed exception for customer self-resolution

Cross-SDK parity: TypeScript test suite asserts the SAME wire-shape
inputs produce the SAME exception types. If either SDK drifts, both
test suites must be updated together.
"""

from __future__ import annotations

import pytest

from nanorix import (
    InputProvenanceRejectedError,
    NanorixError,
    SecretsInjectionRejectedError,
    UnprocessableEntityError,
)
from nanorix._exceptions import raise_for_status


# ── 422 dispatch ─────────────────────────────────────────────────


def test_422_with_input_provenance_rejected_throws_typed_subclass():
    body = {
        "error": {
            "code": "input_provenance_rejected",
            "message": "Manifest input_hash mismatch",
            "reason": "hash_mismatch",
            "input_hash": "sha512:abcd1234",
        }
    }
    with pytest.raises(InputProvenanceRejectedError) as exc_info:
        raise_for_status(422, body)
    e = exc_info.value
    assert e.status_code == 422
    assert e.reason == "hash_mismatch"
    assert e.input_hash == "sha512:abcd1234"
    assert "Manifest input_hash mismatch" in str(e)
    # MUST be catchable as parent type for legacy code
    assert isinstance(e, UnprocessableEntityError)
    assert isinstance(e, NanorixError)


def test_422_with_secrets_injection_rejected_throws_typed_subclass():
    body = {
        "error": {
            "code": "secrets_injection_rejected",
            "message": "Secret 's_db_pwd' could not be injected",
            "reason": "secret_not_found",
            "secret_id": "s_db_pwd",
        }
    }
    with pytest.raises(SecretsInjectionRejectedError) as exc_info:
        raise_for_status(422, body)
    e = exc_info.value
    assert e.status_code == 422
    assert e.reason == "secret_not_found"
    assert e.secret_id == "s_db_pwd"
    assert isinstance(e, UnprocessableEntityError)
    assert isinstance(e, NanorixError)


def test_422_with_unknown_code_throws_unprocessable_entity_error():
    """422 with an error.code we don't have a typed subclass for falls
    back to the base UnprocessableEntityError. Forever-Standard:
    customers writing `except UnprocessableEntityError` continue working
    even if the API adds new 422 codes.
    """
    body = {
        "error": {
            "code": "future_unknown_422_code",
            "message": "Some future 422",
            "reason": "future_reason",
        }
    }
    with pytest.raises(UnprocessableEntityError) as exc_info:
        raise_for_status(422, body)
    e = exc_info.value
    # Must be exactly UnprocessableEntityError, NOT a subclass
    assert type(e) is UnprocessableEntityError
    assert e.reason == "future_reason"


def test_422_with_no_error_code_throws_base_unprocessable_entity():
    """422 without error.code at all (older API surface) still raises
    UnprocessableEntityError so customers can catch the status class.
    """
    body = {"error": "Just a string"}
    with pytest.raises(UnprocessableEntityError) as exc_info:
        raise_for_status(422, body)
    e = exc_info.value
    assert e.status_code == 422
    assert e.reason is None


# ── Subclass assertions ─────────────────────────────────────────


def test_input_provenance_rejected_carries_input_hash_attribute():
    e = InputProvenanceRejectedError("test", reason="hash_mismatch", input_hash="sha512:xyz")
    assert e.input_hash == "sha512:xyz"
    assert e.reason == "hash_mismatch"


def test_secrets_injection_rejected_carries_secret_id_attribute():
    e = SecretsInjectionRejectedError("test", reason="decryption_failed", secret_id="s_api_key")
    assert e.secret_id == "s_api_key"
    assert e.reason == "decryption_failed"


def test_unprocessable_entity_default_status_code_422():
    """Constructed without status_code, defaults to 422."""
    e = UnprocessableEntityError("test")
    assert e.status_code == 422


# ── Status map regression: 422 must not break other status codes ───


def test_400_still_maps_to_bad_request_error():
    from nanorix import BadRequestError

    body = {"error": "bad request"}
    with pytest.raises(BadRequestError):
        raise_for_status(400, body)


def test_429_still_maps_to_rate_limit_error_with_retry_after():
    from nanorix import RateLimitError

    body = {"error": "rate limited", "retry_after": 30}
    with pytest.raises(RateLimitError) as exc_info:
        raise_for_status(429, body)
    assert exc_info.value.retry_after == 30


# ── Customer-pattern test (per design pre-pilot acceptance bar) ──


def test_customer_self_resolution_pattern():
    """The whole point of P0 #4: customer code can dispatch on
    e.reason for self-resolution paths.
    """
    body = {
        "error": {
            "code": "secrets_injection_rejected",
            "message": "Secret missing",
            "reason": "secret_not_found",
            "secret_id": "s_stripe_key",
        }
    }
    rotated = []

    def rotate_secret(sid: str) -> None:
        rotated.append(sid)

    try:
        raise_for_status(422, body)
    except SecretsInjectionRejectedError as e:
        if e.reason == "secret_not_found":
            rotate_secret(e.secret_id)
        elif e.reason == "decryption_failed":
            pytest.fail("should have hit secret_not_found branch")

    assert rotated == ["s_stripe_key"]
