"""
Flask middleware — Tier C (native framework) integration.

Per the integration ship sequence (
dispatch): wraps every inbound HTTP request to a customer's Flask app
in a Nanorix capsule lifecycle. Each request → one capsule → one
AuditProof with ``cdp_kind: Some("Request")``.

This is the WSGI sibling of ``nanorix.integrations.fastapi_middleware``
(ASGI). The semantic surface is identical; the binding shape adapts to
Flask's request-context lifecycle (``before_request`` / ``after_request``
/ ``teardown_request``).

 reserved-slot ( landed
Ungating). Every AuditProof emitted by this
module populates the slot with the canonical wire string ``"request"``.

## Public API

```python
from flask import Flask
from nanorix import Client
from nanorix.integrations.flask_middleware import NanorixMiddleware

app = Flask(__name__)
client = Client(api_key="nrx_live_...")

NanorixMiddleware(
    app,
    client=client,
    framework="HIPAA",
    capsulefile="pa-decision-api",
)
```

Unlike FastAPI's ``add_middleware`` mechanism, Flask wires extensions
by instantiating them with the ``app`` argument; the middleware
constructor registers the ``before_request`` / ``after_request`` /
``teardown_request`` hooks on the Flask instance.

After registration, every inbound request creates a Nanorix capsule
and emits an AuditProof bound to the request lifecycle. The
``X-Nanorix-AuditProof-Id`` response header propagates the
AuditProof ID downstream.

## Hook → ActivityEvent mapping

Mirrors the FastAPI middleware exactly (Tier-C parity invariant):

| Lifecycle hook        | ActivityEvent emitted          |
|-----------------------|--------------------------------|
| before_request        | (none — record start_ts)       |
| after_request (2xx-4xx)| ReasoningStepCompleted        |
| after_request (5xx)   | ToolCallFailed                 |
| teardown_request (err)| ToolCallFailed                 |

## Optional dependency

```bash
pip install 'nanorix[flask]'   # installs flask>=2.0
```
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from typing import Any, Callable, Dict, Mapping, Optional

# Optional dep — imported lazily; the remediation error is raised on
# first use (constructing the middleware), never at module import.
try:
    from flask import Flask, g, request

    _FLASK_AVAILABLE = True
    _FLASK_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    Flask = g = request = None
    _FLASK_AVAILABLE = False
    _FLASK_IMPORT_ERROR = _exc

_FLASK_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.flask_middleware requires the 'flask' "
    "optional extra. Install with:  pip install 'nanorix[flask]'  "
    "(or pip install flask>=2.0 alongside nanorix)."
)


__all__ = [
    "NanorixMiddleware",
    "CDP_KIND_REQUEST",
    "WRAPPER_KIND",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
    "RESPONSE_HEADER_AUDIT_PROOF_ID",
    "RESPONSE_HEADER_CDP_KIND",
    "RESPONSE_HEADER_CAPSULE_ID",
]


# ── Constants ───────────────────────────────────────────────────────


CDP_KIND_REQUEST = "request"
"""Reserved-slot wire form for per-HTTP-request AuditProofs."""

WRAPPER_KIND = "middleware"
"""Reserved metadata-key value emitted via ``nanorix:wrapper_kind``."""


FRAMEWORK_HIPAA = "HIPAA"
FRAMEWORK_GDPR = "GDPR"
FRAMEWORK_SOC2 = "SOC2"
FRAMEWORK_PCI_DSS = "PCI_DSS"
FRAMEWORK_HITRUST = "HITRUST"
FRAMEWORK_EU_AI_ACT = "EU_AI_ACT"

ALLOWED_FRAMEWORKS = frozenset(
    {
        FRAMEWORK_HIPAA,
        FRAMEWORK_GDPR,
        FRAMEWORK_SOC2,
        FRAMEWORK_PCI_DSS,
        FRAMEWORK_HITRUST,
        FRAMEWORK_EU_AI_ACT,
    }
)


RESPONSE_HEADER_AUDIT_PROOF_ID = "X-Nanorix-AuditProof-Id"
RESPONSE_HEADER_CDP_KIND = "X-Nanorix-CdpKind"
RESPONSE_HEADER_CAPSULE_ID = "X-Nanorix-Capsule-Id"


_STATUS_TO_FAILURE_KIND: Mapping[int, str] = {
    408: "timeout",
    401: "permission_denied",
    403: "permission_denied",
    400: "invalid_args",
    422: "invalid_args",
    499: "cancelled",
}


def _map_status_to_failure_kind(status: int) -> str:
    if status in _STATUS_TO_FAILURE_KIND:
        return _STATUS_TO_FAILURE_KIND[status]
    if status >= 500:
        return "upstream_failure"
    return "upstream_failure"


def _canonical_sha512_hex(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha512(canonical).hexdigest()


def _resolve_framework_for_path(
    *,
    path: str,
    default_framework: str,
    framework_per_route: Optional[Mapping[str, str]],
) -> str:
    """Same longest-prefix-first match as the FastAPI middleware."""
    if not framework_per_route:
        return default_framework
    candidates = sorted(framework_per_route.keys(), key=len, reverse=True)
    for prefix in candidates:
        if path.startswith(prefix):
            return framework_per_route[prefix]
    return default_framework


def _classify_exception(exc: BaseException) -> str:
    """Closed-enum mapping of Python exception class → ToolCallFailureKind.

    Mirrors the FastAPI middleware's ``_classify_exception`` verbatim
    (Tier-C parity invariant).
    """
    cls = type(exc).__name__
    mapping = {
        "TimeoutError": "timeout",
        "AsyncTimeoutError": "timeout",
        "FuturesTimeoutError": "timeout",
        "PermissionError": "permission_denied",
        "PermissionDenied": "permission_denied",
        "ValueError": "invalid_args",
        "ValidationError": "invalid_args",
        "TypeError": "invalid_args",
        "HTTPException": "upstream_failure",
        "RequestException": "upstream_failure",
        "ConnectionError": "upstream_failure",
        "CancelledError": "cancelled",
        "KeyboardInterrupt": "cancelled",
    }
    if cls in mapping:
        return mapping[cls]
    for parent in type(exc).__mro__[1:]:
        if parent.__name__ in mapping:
            return mapping[parent.__name__]
    return "upstream_failure"


# ── Public API ──────────────────────────────────────────────────────


class NanorixMiddleware:
    """Flask middleware that wraps every inbound HTTP request in a
    Nanorix capsule lifecycle and emits an AuditProof with
    ``cdp_kind: "request"`` at response completion.

    Wire via:

    ```python
    NanorixMiddleware(app, client=..., framework="HIPAA",
                     capsulefile="pa-decision-api")
    ```

    The constructor registers the request-lifecycle hooks
    (``before_request`` / ``after_request`` / ``teardown_request``) on
    the Flask app.

    Args:
        app: REQUIRED. The Flask app being wrapped.
        client: REQUIRED. A ``nanorix.Client`` instance the middleware
            uses to create + destroy capsules.
        framework: REQUIRED. Compliance framework label
            (customer-declared).
        capsulefile: REQUIRED. Server-side Capsulefile ID or local name.
        framework_version: Optional framework version.
        agent_id: Optional sub-agent identifier.
        framework_per_route: Optional path-prefix → framework override.
        excluded_paths: Optional iterable of path prefixes to skip.
        max_lifetime_seconds: Optional capsule lifetime (default 300).
        egress_profile: Optional egress profile ID.
        on_audit_proof: Optional callback receiving the CdpAccess after
            every destroyed capsule.

    Raises:
        ValueError: if ``client``, ``framework``, or ``capsulefile`` is
            missing or empty.
    """

    def __init__(
        self,
        app: Optional[Flask] = None,
        *,
        client: Optional[Any] = None,
        framework: str = "",
        capsulefile: str = "",
        framework_version: Optional[str] = None,
        agent_id: Optional[str] = None,
        framework_per_route: Optional[Mapping[str, str]] = None,
        excluded_paths: Optional[Any] = None,
        max_lifetime_seconds: Optional[int] = 300,
        egress_profile: Optional[str] = None,
        on_audit_proof: Optional[Callable[[Any], None]] = None,
    ) -> None:
        if not _FLASK_AVAILABLE:
            raise ImportError(
                _FLASK_IMPORT_ERROR_MESSAGE
            ) from _FLASK_IMPORT_ERROR
        if client is None:
            raise ValueError(
                "NanorixMiddleware requires an explicit `client=` "
                "argument — a nanorix.Client instance."
            )
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "NanorixMiddleware requires an explicit `framework=` "
                "argument (e.g., framework='HIPAA'). No silent default "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "NanorixMiddleware requires an explicit `capsulefile=` "
                "argument."
            )

        self.client = client
        self.framework = framework
        self.capsulefile = capsulefile
        self.framework_version = framework_version
        self.agent_id = agent_id
        self.framework_per_route: Optional[Mapping[str, str]] = (
            dict(framework_per_route) if framework_per_route else None
        )
        self.excluded_paths = tuple(excluded_paths) if excluded_paths else ()
        self.max_lifetime_seconds = max_lifetime_seconds
        self.egress_profile = egress_profile
        self.on_audit_proof = on_audit_proof

        # Support deferred initialization via init_app() (Flask
        # extension pattern). The two paths are otherwise identical.
        self.app: Optional[Flask] = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Wire the middleware hooks onto the Flask app.

        Registers ``before_request`` / ``after_request`` /
        ``teardown_request`` callbacks. Idempotent — calling twice on
        the same app is a no-op (subsequent registrations are skipped).
        """
        if self.app is app:
            return
        self.app = app
        app.before_request(self._before_request)
        app.after_request(self._after_request)
        app.teardown_request(self._teardown_request)

    # ── Lifecycle hooks ────────────────────────────────────────────

    def _before_request(self) -> None:
        """Create the per-request Nanorix capsule.

        Stores the capsule + start timestamp on Flask's ``g`` object so
        the after-request and teardown hooks can finalize the lifecycle.
        Capsule creation is best-effort: a failure here is logged + the
        request proceeds unwrapped (no Nanorix AuditProof — surface
        for the customer's compliance team to investigate).
        """
        path = request.path
        if self._is_excluded(path):
            g.nanorix_capsule = None  # sentinel: not wrapped
            return

        framework = _resolve_framework_for_path(
            path=path,
            default_framework=self.framework,
            framework_per_route=self.framework_per_route,
        )

        metadata = self._build_request_metadata(
            method=request.method,
            path=path,
            framework=framework,
        )

        try:
            capsule = self._create_capsule(metadata=metadata)
        except Exception:  # noqa: BLE001 - never block the customer's request
            g.nanorix_capsule = None
            g.nanorix_capsule_id = None
            g.nanorix_framework = framework
            return

        g.nanorix_capsule = capsule
        g.nanorix_capsule_id = self._extract_capsule_id(capsule)
        g.nanorix_framework = framework
        g.nanorix_start_ts = time.perf_counter()

    def _after_request(self, response: Any) -> Any:
        """Emit the success-path ActivityEvent.

        Called by Flask AFTER the view function returns. The response
        object is mutable here — we add AuditProof response headers.
        The capsule is NOT destroyed yet (the teardown hook handles
        destroy so 5xx + exception paths share the same finalization
        gate).
        """
        capsule = getattr(g, "nanorix_capsule", None)
        if capsule is None:
            return response

        status = getattr(response, "status_code", 0)
        duration_ms = self._duration_ms()
        framework = getattr(g, "nanorix_framework", self.framework)

        # Emit ActivityEvent based on status code.
        self._emit_request_outcome(
            method=request.method,
            path=request.path,
            framework=framework,
            status=status,
            duration_ms=duration_ms,
            exception=None,
        )

        # Attach AuditProof headers preemptively (capsule ID known
        # already; AuditProof ID gets attached after destroy in
        # teardown via a deferred header-write strategy). Since
        # Flask doesn't expose a "after-teardown response mutation"
        # hook, we attach the predicted AuditProof ID NOW based on
        # the capsule_id; the server uses a deterministic
        # cap_<32hex> derivation from the capsule UUID.
        capsule_id = getattr(g, "nanorix_capsule_id", None)
        if capsule_id is not None:
            response.headers[RESPONSE_HEADER_CAPSULE_ID] = capsule_id
            response.headers[RESPONSE_HEADER_AUDIT_PROOF_ID] = (
                "cap_" + capsule_id.replace("-", "")
            )
        response.headers[RESPONSE_HEADER_CDP_KIND] = CDP_KIND_REQUEST

        return response

    def _teardown_request(self, exception: Optional[BaseException]) -> None:
        """Destroy the capsule + emit failure-path ActivityEvent if needed.

        Called by Flask AFTER every request completes, including
        exception paths. The response object is no longer accessible
        here, but the capsule lifecycle MUST finalize regardless of
        success/failure.
        """
        capsule = getattr(g, "nanorix_capsule", None)
        if capsule is None:
            return

        # If the request failed with an unhandled exception, emit the
        # failure ActivityEvent here (after_request didn't run).
        if exception is not None:
            duration_ms = self._duration_ms()
            framework = getattr(g, "nanorix_framework", self.framework)
            self._emit_request_outcome(
                method=request.method if request else "UNKNOWN",
                path=request.path if request else "/",
                framework=framework,
                status=500,
                duration_ms=duration_ms,
                exception=exception,
            )

        # Destroy capsule + side-channel the AuditProof to on_audit_proof.
        try:
            cdp = self._destroy_capsule(capsule)
        except Exception:  # noqa: BLE001 - never let teardown crash teardown
            cdp = None

        if cdp is not None and self.on_audit_proof is not None:
            try:
                self.on_audit_proof(cdp)
            except Exception:  # noqa: BLE001
                pass

    # ── Helpers ────────────────────────────────────────────────────

    def _is_excluded(self, path: str) -> bool:
        for prefix in self.excluded_paths:
            if path.startswith(prefix):
                return True
        return False

    def _build_request_metadata(
        self,
        *,
        method: str,
        path: str,
        framework: str,
    ) -> Dict[str, str]:
        metadata: Dict[str, str] = {
            "nanorix:wrapper_kind": WRAPPER_KIND,
            "nanorix:framework": framework,
            "nanorix:request_method": method,
            "nanorix:request_path": path,
        }
        if self.framework_version is not None:
            metadata["nanorix:framework_version"] = self.framework_version
        if self.agent_id is not None:
            metadata["nanorix:agent_id"] = self.agent_id
        return metadata

    def _create_capsule(self, *, metadata: Mapping[str, str]) -> Any:
        """Synchronous capsule create — Flask is WSGI."""
        kwargs: Dict[str, Any] = {
            "capsulefile": self.capsulefile,
            "metadata": dict(metadata),
            "cdp_kind": CDP_KIND_REQUEST,
        }
        if self.max_lifetime_seconds is not None:
            kwargs["max_lifetime_seconds"] = self.max_lifetime_seconds
        if self.egress_profile is not None:
            kwargs["egress_profile"] = self.egress_profile
        return self.client.capsules.create(**kwargs)

    def _destroy_capsule(self, capsule: Any) -> Any:
        destroy = capsule.destroy
        if inspect.iscoroutinefunction(destroy):
            # Async destroy in a sync handler — best-effort sync
            # bridge. The customer should use the sync Client with
            # Flask, but we hedge.
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(destroy())
            finally:
                loop.close()
        return destroy()

    def _duration_ms(self) -> int:
        start = getattr(g, "nanorix_start_ts", None)
        if start is None:
            return 0
        return int((time.perf_counter() - start) * 1000)

    def _emit_request_outcome(
        self,
        *,
        method: str,
        path: str,
        framework: str,
        status: int,
        duration_ms: int,
        exception: Optional[BaseException],
    ) -> None:
        """Mirror of the FastAPI middleware's outcome emission."""
        try:
            from nanorix.helpers.activity_events import (
                emit_reasoning_step_completed,
                emit_tool_call_failed,
            )
        except ImportError:
            return

        endpoint = f"{method} {path}"
        buffer_path = os.environ.get(
            "NANORIX_ACTIVITY_BUFFER_PATH", "/data/activity_events.jsonl"
        )

        if exception is not None:
            try:
                emit_tool_call_failed(
                    tool_call_id=str(id(exception)),
                    tool_name=endpoint,
                    failure_reason=f"{type(exception).__name__}: {exception}",
                    duration_ms=duration_ms,
                    failure_kind=_classify_exception(exception),
                    agent_id=self.agent_id,
                    buffer_path=buffer_path,
                )
            except OSError:
                pass
            return

        if status >= 500:
            try:
                emit_tool_call_failed(
                    tool_call_id=f"http_{status}_{int(time.time() * 1000)}",
                    tool_name=endpoint,
                    failure_reason=f"http_status_{status}",
                    duration_ms=duration_ms,
                    failure_kind=_map_status_to_failure_kind(status),
                    agent_id=self.agent_id,
                    buffer_path=buffer_path,
                )
            except OSError:
                pass
            return

        try:
            emit_reasoning_step_completed(
                step_idx=0,
                prompt={"method": method, "path": path},
                response={"status": status},
                duration_ms=duration_ms,
                model=None,
                agent_id=self.agent_id,
                buffer_path=buffer_path,
            )
        except OSError:
            pass

    @staticmethod
    def _extract_capsule_id(capsule: Any) -> Optional[str]:
        for attr in ("id", "capsule_id"):
            v = getattr(capsule, attr, None)
            if isinstance(v, str):
                return v
        return None
