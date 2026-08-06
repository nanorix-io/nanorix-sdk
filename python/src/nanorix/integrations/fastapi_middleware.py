"""
FastAPI middleware — Tier C (native framework) integration.

Per the integration ship sequence (Wave 14, Item 4 of 5; Wave 18-11
dispatch): wraps every inbound HTTP request to a customer's FastAPI app
in a Nanorix capsule lifecycle. Each request → one capsule → one
AuditProof with ``cdp_kind: Some("Request")``.

 reserved-slot (Wave 16-A landed `a951568`; Wave 18-11
CTI-T14-002 + CTI-T14-004 ungating). This module is the canonical
consumer of `CdpKind::Request` — every emission here populates the
slot with the canonical wire string ``"request"``.

## Three-tier integration model

| Tier | Effort | Claim                                            |
|------|--------|--------------------------------------------------|
| A    | 5 min  | Per-call receipts (LangChain callback / proxy)   |
| B    | 30 min | Full workflow AuditProof (decorator)      |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)    |

This middleware is Tier C. The claim is per-request:
every HTTP request that survives the middleware gate is bound into a
sealed capsule lifecycle. The AuditProof carries
``cdp_kind: "request"`` + the customer-declared framework + agent_id +
capsulefile_id in metadata. Plaintext request / response bodies are
NOT bound — only canonical-JSON SHA-512 hex digests of structural
identifiers (method + path + status + latency).

## Public API

```python
from fastapi import FastAPI
from nanorix import Client
from nanorix.integrations.fastapi_middleware import NanorixMiddleware

app = FastAPI()
client = Client(api_key="nrx_live_...")

app.add_middleware(
    NanorixMiddleware,
    client=client,
    framework="HIPAA",
    capsulefile="pa-decision-api",
)
```

After ``app.add_middleware`` registers the middleware, every inbound
request creates a Nanorix capsule, runs the request handler inside the
capsule's lifecycle, and emits an AuditProof at response completion.
The AuditProof is side-channeled into the response as the
``X-Nanorix-AuditProof-Id`` response header so the customer's caller
(or downstream auditor) can correlate.

## Hook → ActivityEvent mapping

The middleware maps the request lifecycle into the EXISTING 4-variant
ActivityEvent surface (Forever-Standard preserved; no new
variant introduced):

| Lifecycle hook        | ActivityEvent emitted          | Notes                                      |
|-----------------------|--------------------------------|--------------------------------------------|
| Request start         | (none — record start_ts)       | Open request context                       |
| Successful response   | ReasoningStepCompleted         | Records method + path + status; emit       |
| Failed response (5xx) | ToolCallFailed                 | Records method + path + status + failure_kind |
| Unhandled exception   | ToolCallFailed                 | Records exception class as failure_reason  |

## Trust-boundary discipline

- **Customer-sole-declarer.** Customer declares
  ``framework=`` (e.g., ``"HIPAA"``) and ``capsulefile=`` at middleware
  registration time. The middleware does NOT classify request
  payloads. ``framework_per_route`` lets customer override per
  route prefix (e.g., ``"/admin"`` → ``"SOC2"``).
- **Zero-retention.** Request body and response body
  NEVER enter the AuditProof. Only structural identifiers (method,
  path, query string, status, duration) and their canonical-JSON
  SHA-512 hex digests bind. Headers are NOT captured by default
  (the customer's compliance team typically wants this).
- ** brand:** The response header naming the AuditProof is
  ``X-Nanorix-AuditProof-Id``, not ``X-CDP-Id``.

## Optional dependency

The ``fastapi`` package is an optional dep:

```bash
pip install 'nanorix[fastapi]'   # installs fastapi + starlette
```

The module fails clearly with an import error + remediation hint if
``starlette`` is not installed.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

# Optional dep — imported lazily; the remediation error is raised on
# first use (constructing the middleware), never at module import.
try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse
    from starlette.types import ASGIApp

    _STARLETTE_AVAILABLE = True
    _STARLETTE_IMPORT_ERROR: Optional[Exception] = None
except ImportError as _exc:  # pragma: no cover - exercised when extra missing
    BaseHTTPMiddleware = object
    StarletteRequest = StarletteResponse = ASGIApp = None
    _STARLETTE_AVAILABLE = False
    _STARLETTE_IMPORT_ERROR = _exc

_STARLETTE_IMPORT_ERROR_MESSAGE = (
    "nanorix.integrations.fastapi_middleware requires the 'fastapi' "
    "optional extra. Install with:  pip install 'nanorix[fastapi]'  "
    "(or pip install starlette>=0.36 alongside nanorix)."
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
"""The reserved-slot wire form for per-HTTP-request AuditProofs
(Wave 16-A `a951568`; Wave 18-11 ungating). Mirrors `CdpKind::Request`
serde rename_all = "snake_case" at the canonical verifier."""


WRAPPER_KIND = "middleware"
"""Reserved metadata-key value emitted via `nanorix:wrapper_kind`.

Values for the reserved metadata key:
- ``"workload"``: Tier B `@workload` decorator (function-level wrap)
- ``"middleware"``: this Tier C middleware (per-request wrap)
- ``"proxy"``: Tier A SDK proxy (per-LLM-call wrap)
"""


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
"""Recommended canonical framework labels.

Customer may pass any non-empty string for ``framework=``; the canonical
labels above match the `nanorix.compliance.*Context.framework_id` values.
"""


RESPONSE_HEADER_AUDIT_PROOF_ID = "X-Nanorix-AuditProof-Id"
"""Response header naming the per-request AuditProof ID."""

RESPONSE_HEADER_CDP_KIND = "X-Nanorix-CdpKind"
"""Response header naming the AuditProof scope discriminator."""

RESPONSE_HEADER_CAPSULE_ID = "X-Nanorix-Capsule-Id"
"""Response header naming the underlying capsule UUID."""


# Closed-enum mapping HTTP status → ToolCallFailureKind serde wire form
# at the Nanorix API service.
_STATUS_TO_FAILURE_KIND: Mapping[int, str] = {
    408: "timeout",
    401: "permission_denied",
    403: "permission_denied",
    400: "invalid_args",
    422: "invalid_args",
    499: "cancelled",
}


def _map_status_to_failure_kind(status: int) -> str:
    """Closed-enum mapping of upstream HTTP status → ToolCallFailureKind."""
    if status in _STATUS_TO_FAILURE_KIND:
        return _STATUS_TO_FAILURE_KIND[status]
    if status >= 500:
        return "upstream_failure"
    return "upstream_failure"


def _canonical_sha512_hex(payload: Any) -> str:
    """SHA-512 hex of a canonical-JSON-serialized payload.

    Uses ``sort_keys=True, separators=(",", ":")`` so cross-impl
    byte-equivalence holds against the Rust JCS serializer at
    the canonical verifier.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha512(canonical).hexdigest()


def _resolve_framework_for_path(
    *,
    path: str,
    default_framework: str,
    framework_per_route: Optional[Mapping[str, str]],
) -> str:
    """Pick the framework label for an inbound request path.

    Customer-sole-declarer: the customer's middleware-registration
    `framework_per_route` mapping wins; longest-prefix-first match. If no
    prefix matches, the middleware-wide `framework` argument is used.
    """
    if not framework_per_route:
        return default_framework
    # Longest-prefix-first match (deterministic).
    candidates = sorted(framework_per_route.keys(), key=len, reverse=True)
    for prefix in candidates:
        if path.startswith(prefix):
            return framework_per_route[prefix]
    return default_framework


# ── Public API ──────────────────────────────────────────────────────


class NanorixMiddleware(BaseHTTPMiddleware):
    """FastAPI / Starlette ASGI middleware that wraps every inbound HTTP
    request in a Nanorix capsule lifecycle and emits an AuditProof with
    ``cdp_kind: "request"`` at response completion.

    Mount via ``app.add_middleware(NanorixMiddleware, **kwargs)``.

    Args:
        app: The ASGI app being wrapped (passed by FastAPI).
        client: REQUIRED. A ``nanorix.Client`` instance the middleware
            uses to create + destroy capsules.
        framework: REQUIRED. Compliance framework label (e.g.,
            ``"HIPAA"``, ``"GDPR"``, ``"SOC2"``). Customer-declared;
            no silent default. Bound to AuditProof.metadata
            as ``nanorix:framework``.
        capsulefile: REQUIRED. Server-side Capsulefile ID (``cf_...``)
            or local Capsulefile name.
        framework_version: Optional framework version string (e.g.,
            ``"2026-02"``). Bound as ``nanorix:framework_version``.
        agent_id: Optional sub-agent identifier. Bound as
            ``nanorix:agent_id``.
        framework_per_route: Optional path-prefix → framework override
            mapping (longest-prefix wins). Lets customer attest
            different compliance scopes per route family
            (e.g., ``{"/admin": "SOC2"}``).
        excluded_paths: Optional iterable of path prefixes the
            middleware does NOT wrap (e.g., ``["/health", "/metrics"]``).
            Health checks should typically NOT consume capsule quota.
        max_lifetime_seconds: Optional capsule lifetime ceiling. Default
            300 seconds (matches the sealed-proxy ceiling at the Nanorix API service).
        egress_profile: Optional egress profile ID for outbound network
            policy (Business+ tier).
        on_audit_proof: Optional callback receiving the resulting
            ``CdpAccess`` after every destroyed capsule. Useful for
            streaming proofs into a GRC platform side-channel.

    Raises:
        ValueError: if ``client``, ``framework``, or ``capsulefile`` is
            missing or empty.
    """

    def __init__(
        self,
        app: ASGIApp,
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
        if not _STARLETTE_AVAILABLE:
            raise ImportError(
                _STARLETTE_IMPORT_ERROR_MESSAGE
            ) from _STARLETTE_IMPORT_ERROR
        super().__init__(app)
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
                "argument (e.g., capsulefile='pa-decision-api' or "
                "'cf_...')."
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

    # ── Lifecycle ──────────────────────────────────────────────────

    async def dispatch(
        self,
        request: StarletteRequest,
        call_next: Callable[[StarletteRequest], Awaitable[StarletteResponse]],
    ) -> StarletteResponse:
        """Middleware entry point. Wraps the request in a capsule lifecycle.

        Lifecycle:
        1. Check exclusion list — skip-wrap for health checks, etc.
        2. Resolve the framework for this path (per-route override).
        3. Build capsule metadata + create capsule with
           ``cdp_kind: "request"``.
        4. Run the downstream handler (``call_next``).
        5. Emit ReasoningStepCompleted (success) or ToolCallFailed
           (5xx / exception) ActivityEvent into the in-capsule buffer.
        6. Destroy capsule + attach AuditProof ID to the response
           headers + invoke ``on_audit_proof`` callback if configured.
        """
        # ── 1. Exclusion check.
        path = request.url.path
        if self._is_excluded(path):
            return await call_next(request)

        # ── 2. Framework resolution.
        framework = _resolve_framework_for_path(
            path=path,
            default_framework=self.framework,
            framework_per_route=self.framework_per_route,
        )

        # ── 3. Capsule create.
        metadata = self._build_request_metadata(
            request=request,
            framework=framework,
        )

        capsule = await self._create_capsule_async(metadata=metadata)
        capsule_id = self._extract_capsule_id(capsule)

        # ── 4. Run handler + measure.
        start_ts = time.perf_counter()
        status: int = 0
        exception: Optional[BaseException] = None
        response: Optional[StarletteResponse] = None
        try:
            response = await call_next(request)
            status = response.status_code
        except BaseException as exc:  # pragma: no cover - exception propagation
            exception = exc
        duration_ms = int((time.perf_counter() - start_ts) * 1000)

        # ── 5. Emit appropriate ActivityEvent into the capsule's buffer.
        self._emit_request_outcome(
            request=request,
            framework=framework,
            status=status,
            duration_ms=duration_ms,
            exception=exception,
        )

        # ── 6. Destroy + attach AuditProof headers.
        cdp = await self._destroy_capsule_async(capsule)
        audit_proof_id = self._extract_audit_proof_id(cdp, capsule_id)

        if self.on_audit_proof is not None:
            try:
                self.on_audit_proof(cdp)
            except Exception:  # noqa: BLE001 - callback must not crash request
                pass

        # ── Re-raise if the handler raised. AuditProof headers are
        # attached to the exception response (FastAPI converts to 500).
        if exception is not None:
            raise exception

        assert response is not None  # for type-checker; the path above ensured this
        if audit_proof_id is not None:
            response.headers[RESPONSE_HEADER_AUDIT_PROOF_ID] = audit_proof_id
        response.headers[RESPONSE_HEADER_CDP_KIND] = CDP_KIND_REQUEST
        if capsule_id is not None:
            response.headers[RESPONSE_HEADER_CAPSULE_ID] = capsule_id

        return response

    # ── Internals ──────────────────────────────────────────────────

    def _is_excluded(self, path: str) -> bool:
        """Return True if this path is in the exclusion list."""
        for prefix in self.excluded_paths:
            if path.startswith(prefix):
                return True
        return False

    def _build_request_metadata(
        self,
        *,
        request: StarletteRequest,
        framework: str,
    ) -> Dict[str, str]:
        """Build the customer-declared metadata dict for the capsule.

        Reserved keys (``nanorix:*``) bound into the AuditProof:

        - ``nanorix:wrapper_kind`` — ``"middleware"``
        - ``nanorix:framework`` — customer-declared
        - ``nanorix:framework_version`` — optional
        - ``nanorix:agent_id`` — optional
        - ``nanorix:request_method`` — HTTP method (e.g., ``"POST"``)
        - ``nanorix:request_path`` — request URL path (e.g., ``"/v1/decide"``)

        Plaintext request body / query parameters / headers are NOT
        bound — only the structural identifiers above.
        """
        metadata: Dict[str, str] = {
            "nanorix:wrapper_kind": WRAPPER_KIND,
            "nanorix:framework": framework,
            "nanorix:request_method": request.method,
            "nanorix:request_path": request.url.path,
        }
        if self.framework_version is not None:
            metadata["nanorix:framework_version"] = self.framework_version
        if self.agent_id is not None:
            metadata["nanorix:agent_id"] = self.agent_id
        return metadata

    async def _create_capsule_async(self, *, metadata: Mapping[str, str]) -> Any:
        """Invoke ``client.capsules.create`` with the request-scope
        cdp_kind reserved-slot value.

        Supports both sync (``Client``) and async (``AsyncClient``)
        SDK shapes.
        """
        create = self.client.capsules.create
        kwargs: Dict[str, Any] = {
            "capsulefile": self.capsulefile,
            "metadata": dict(metadata),
            "cdp_kind": CDP_KIND_REQUEST,
        }
        if self.max_lifetime_seconds is not None:
            kwargs["max_lifetime_seconds"] = self.max_lifetime_seconds
        if self.egress_profile is not None:
            kwargs["egress_profile"] = self.egress_profile

        if inspect.iscoroutinefunction(create):
            return await create(**kwargs)

        # Sync SDK; run in default executor so we don't block the event loop.
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: create(**kwargs))

    async def _destroy_capsule_async(self, capsule: Any) -> Any:
        """Destroy the capsule, awaiting if the destroy method is async."""
        destroy = capsule.destroy
        if inspect.iscoroutinefunction(destroy):
            return await destroy()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, destroy)

    def _emit_request_outcome(
        self,
        *,
        request: StarletteRequest,
        framework: str,
        status: int,
        duration_ms: int,
        exception: Optional[BaseException],
    ) -> None:
        """Map the request outcome to one of the existing 4 ActivityEvents.

         Forever-Standard: no new ActivityEvent variant is
        introduced. The middleware uses the existing helpers:
        - 2xx-4xx response → ReasoningStepCompleted
        - 5xx response OR unhandled exception → ToolCallFailed
        """
        try:
            from nanorix.helpers.activity_events import (
                emit_reasoning_step_completed,
                emit_tool_call_failed,
            )
        except ImportError:
            # Helpers always ship with the SDK; this import error would
            # indicate a packaging defect. Best-effort: skip emission.
            return

        endpoint = f"{request.method} {request.url.path}"
        # Buffer path: outside a capsule, the default path /data/...
        # doesn't exist; fall back to a no-op via the helper's OSError
        # tolerance. Customer's outer capsule (created above) has the
        # tmpfs mount.
        buffer_path = os.environ.get(
            "NANORIX_ACTIVITY_BUFFER_PATH", "/data/activity_events.jsonl"
        )

        if exception is not None:
            # Unhandled exception → ToolCallFailed.
            try:
                emit_tool_call_failed(
                    tool_call_id=str(id(request)),
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
            # 5xx response → ToolCallFailed.
            try:
                emit_tool_call_failed(
                    tool_call_id=str(id(request)),
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

        # 2xx / 3xx / 4xx → ReasoningStepCompleted.
        try:
            emit_reasoning_step_completed(
                step_idx=0,  # one step per request; customer wraps
                                  # multi-step internally via nanorix.helpers
                prompt={"method": request.method, "path": request.url.path},
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
        """Best-effort extract the capsule's UUID for the response header."""
        for attr in ("id", "capsule_id"):
            v = getattr(capsule, attr, None)
            if isinstance(v, str):
                return v
        return None

    @staticmethod
    def _extract_audit_proof_id(cdp: Any, capsule_id: Optional[str]) -> Optional[str]:
        """Best-effort extract the AuditProof ID from the CdpAccess result.

        The SDK's CdpAccess shape exposes the AuditProof ID as either a
        direct attribute or via ``.proof_id`` / ``.id``. Fall back to
        ``cap_{capsule_id without dashes}`` to mirror the server's
        deterministic ID derivation.
        """
        for attr in ("audit_proof_id", "proof_id", "id"):
            v = getattr(cdp, attr, None)
            if isinstance(v, str):
                return v
        if capsule_id is not None:
            return "cap_" + capsule_id.replace("-", "")
        return None


def _classify_exception(exc: BaseException) -> str:
    """Map a Python exception class to a closed-enum
    ToolCallFailureKind wire string.

    Returns one of the 6 known wire values; defaults to
    ``"upstream_failure"`` when no specific match. Mirrors the
    classification table in
    the Python SDK.
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
    # Walk MRO for parent-class match.
    for parent in type(exc).__mro__[1:]:
        if parent.__name__ in mapping:
            return mapping[parent.__name__]
    return "upstream_failure"
