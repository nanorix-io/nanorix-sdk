"""Sentry observability shell — guarded verification-failure alerter.

Forwards Nanorix AuditProof verification FAILURES to Sentry as tagged
events. Sentry is an alerting backend; only failures warrant an event —
the core returns ``None`` for a passing verification and this shell emits
nothing. All shaping lives in :mod:`nanorix.observability._core` (pure,
vendor-SDK-free, dict-tested); this module is a THIN pusher that REPLAYS the
event description onto ``sentry_sdk``.

## Transport

Capturing a Sentry event requires the ``sentry-sdk`` package at runtime, so
this shell guards that import: it is imported lazily inside the push path
and raises a clear, actionable :class:`ImportError` only when Sentry is
actually needed and absent. The ``[sentry]`` extra declares ``sentry-sdk``.
A customer who manages their own Sentry hub can pass a ``capture=`` callable
so no Sentry import happens in this module at all.

Critically, ``import nanorix.observability.sentry`` and importing the core
succeed with NO ``sentry-sdk`` installed — the guard fires only on use.

## Credential discipline

The customer supplies their Sentry DSN (constructor arg or ``SENTRY_DSN``
env). It is passed once to ``sentry_sdk.init`` and is **never** retained as
a readable attribute on this object after initialization — mirroring the
connectors' no-credential-retention discipline.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from nanorix.observability._core import (
    ObservabilityError,
    assert_structural_facts_only,
    auditproof_to_sentry_event,
)

__all__ = ["SentryObservabilityAdapter"]

_IMPORT_HINT = (
    "The Sentry adapter requires the optional 'sentry-sdk' package, which "
    "is not installed. Install it with:\n\n    pip install nanorix[sentry]\n\n"
    "Alternatively, pass capture=<callable> so this adapter does not import "
    "sentry_sdk itself."
)


def _require_sentry() -> Any:
    """Import sentry_sdk lazily; raise an actionable ImportError if absent."""
    try:
        import sentry_sdk
    except ImportError as err:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_IMPORT_HINT) from err
    return sentry_sdk


class SentryObservabilityAdapter:
    """Forward AuditProof verification failures to Sentry as tagged events.

    Args:
        dsn: The customer's Sentry DSN. Falls back to ``SENTRY_DSN``. Passed
            once to ``sentry_sdk.init``; never retained on this object after
            initialization.
        environment: The Sentry environment tag (``production`` /
            ``staging`` / ``dev``). Falls back to ``SENTRY_ENVIRONMENT``.
        capture: Optional callable ``(message, level, tags, context) -> None``
            replacing the default ``sentry_sdk`` capture path. When supplied
            no ``sentry_sdk`` import happens in this module — the customer
            owns the hub. In tests a fake capture is passed so no Sentry SDK
            is required.
    """

    def __init__(
        self,
        dsn: Optional[str] = None,
        *,
        environment: Optional[str] = None,
        capture: Optional[Callable[..., None]] = None,
    ) -> None:
        resolved_dsn = dsn or os.environ.get("SENTRY_DSN")
        if capture is None and not resolved_dsn:
            raise ObservabilityError(
                "Sentry DSN is required (pass dsn=... or set SENTRY_DSN), or "
                "supply a custom capture= callable. Nanorix never stores your "
                "Sentry DSN."
            )
        self.environment = environment or os.environ.get(
            "SENTRY_ENVIRONMENT", "production"
        )
        self._capture = capture
        # If a capture callable is supplied we import no sentry_sdk and never
        # touch the DSN. Otherwise sentry_sdk is initialized lazily on first
        # use; the DSN is consumed there and not stored as a readable
        # attribute (no-credential-retention).
        self._dsn = resolved_dsn if capture is None else None
        self._initialized = capture is not None

    def _ensure_init(self) -> Optional[Any]:
        if self._capture is not None:
            return None
        sentry_sdk = _require_sentry()
        if not self._initialized:
            sentry_sdk.init(dsn=self._dsn, environment=self.environment)
            # No-credential-retention: the DSN has been handed to sentry_sdk;
            # drop our reference so nothing reads it back off this object.
            self._dsn = None
            self._initialized = True
        return sentry_sdk

    def send_auditproof(self, proof: Any, verification: Any) -> Optional[Dict[str, Any]]:
        """Emit a Sentry event when an AuditProof verification FAILED.

        Shapes the event via the core (STRUCTURAL FACTS only). When the
        verification passed the core returns ``None`` and this emits nothing
        (Sentry only fires on failures). Returns the emitted event
        description, or ``None`` when verification passed.
        """
        event = auditproof_to_sentry_event(proof, verification)
        if event is None:
            return None
        assert_structural_facts_only(event)
        self._emit(event)
        return event

    def _emit(self, event: Dict[str, Any]) -> None:
        """Replay the event description onto the configured capture path."""
        if self._capture is not None:
            self._capture(
                event["message"],
                event["level"],
                event["tags"],
                event["context"],
            )
            return
        sentry_sdk = self._ensure_init()
        assert sentry_sdk is not None
        with sentry_sdk.new_scope() as scope:
            for key, value in event["tags"].items():
                scope.set_tag(key, value)
            scope.set_context("nanorix_verification", event["context"])
            sentry_sdk.capture_message(event["message"], level=event["level"])
