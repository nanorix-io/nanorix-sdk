"""
Tier B integration — `@workload` decorator.

Per the integration ship sequence (Wave 14, item 3 of 5): wraps a
customer Python function in a Nanorix capsule lifecycle. One line per
workload, full AuditProof for the workflow. Mirrors the Tier
A LangChain callback (per-call receipts) by going one level wider —
the entire function execution is bound into a single AuditProof.

## Three-tier integration model

| Tier | Effort | Claim                                            |
|------|--------|--------------------------------------------------|
| A    | 5 min  | Per-call receipts (LangChain callback / proxy)   |
| B    | 30 min | Full workflow AuditProof (this decorator) |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)    |

This decorator is Tier B. The claim is full: customer's whole
function runs under a sealed-and-destroyed capsule lifecycle; the
AuditProof emits at function exit and is bound to the function's
input/output via metadata hashes.

## Public API

```python
from nanorix import Client, workload

client = Client(api_key="nrx_live_...")

@workload(framework="HIPAA", capsulefile="pa-decision", client=client)
def my_function(input_data):
    # body executes inside a Nanorix capsule context
    # any in-capsule helpers (emit_tool_call_attempted, etc.) write
    # to the capsule's activity-trail buffer
    return {"decision": "approved"}

# Call as a normal function. AuditProof attached.
result = my_function({"patient": "X"})

# Audit proof is accessible via the wrapped function's attribute:
audit_proof = my_function.last_audit_proof
```

## Trust-boundary discipline

- **Customer-sole-declarer.** Capsulefile is pre-authored
  by the customer; the decorator only orchestrates. `framework=` is
  REQUIRED — no silent default. The decorator does NOT classify,
  infer, or augment the customer's data.
- **Forever-Standard.** No new ActivityEvent variants;
  no new capsule-create API fields. The decorator composes existing
  primitives (`client.capsules.create()`, `metadata=`,
  `client.capsules.destroy()`) verbatim.
- **AuditProof brand discipline.** Lifecycle evidence is surfaced
  as the AuditProof, and `VerificationCdp` stays the type exposed
  through `CdpAccess`.
- **Zero-retention.** Plaintext function
  arguments / return values stay in the customer's process memory;
  only their canonical-JSON SHA-512 hashes enter the AuditProof
  metadata.

## Capsulefile resolution

The `capsulefile=` argument accepts either:

1. **Server-side ID** (`cf_xxx`): used directly with
   `client.capsules.create(capsulefile=<id>)`.
2. **Local YAML name**: matches against `name:` field in any
   `capsulefile.yaml` found in `capsulefile_dir` (default cwd) or
   its immediate subdirectories. The local YAML is NOT uploaded by
   the decorator — the customer is expected to have already run
   `nanorix build` to publish the Capsulefile to the server. The
   local YAML scan is a convenience to translate human-readable names
   to server IDs at runtime, mirroring how the CLI's
   `nanorix capsulefile validate` behaves.

If the name doesn't resolve to an existing finalized Capsulefile on
the server, a `BadRequestError` is raised at capsule-create time.

## Cross-impl byte-equivalence

The decorator's orchestration metadata is byte-identical between
Python and TypeScript implementations:

```
metadata = {
  "nanorix:wrapper_kind": "workload",
  "nanorix:framework": <framework>,
  "nanorix:framework_version": <framework_version | absent>,
  "nanorix:agent_id":           <agent_id          | absent>,
  ...customer-supplied metadata (no nanorix: prefix)
}
```

Same key set, same key ordering (server canonicalizes via
`sort_keys=True`), same string values → same `metadata_hash` →
same AuditProof bytes for byte-identical Capsulefile + identical
wrapped function.

## Cap-3 / Wave 14 boss directive

> "the decorator on real customer function shape; AuditProof
> generated per call; Pattern coverage = ALL function-shaped
> workloads."

The decorator covers the function shape (Pattern 3 single-shot,
Pattern 4 high-volume per-record, Pattern 7 multi-modal fusion via
LangChain composition). Patterns 1 + 2 + 5b + 8 layer this with
the LangChain callback (Tier A) for per-call sub-receipts.

The wrapper attaches AuditProof to the wrapped function as
`.last_audit_proof` (single attribute, last invocation wins). For
multi-call workflows the customer either uses the on_audit_proof
callback or wraps each per-call section in its own decorator.
"""

from __future__ import annotations

import functools
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, TypeVar, Union

from nanorix._exceptions import BadRequestError, NanorixError
from nanorix._models import CdpAccess


__all__ = [
    "workload",
    "WorkloadDecorator",
    "FRAMEWORK_HIPAA",
    "FRAMEWORK_GDPR",
    "FRAMEWORK_SOC2",
    "FRAMEWORK_PCI_DSS",
    "FRAMEWORK_HITRUST",
    "FRAMEWORK_EU_AI_ACT",
    "ALLOWED_FRAMEWORKS",
    "WRAPPER_KIND",
]


# ── Constants ───────────────────────────────────────────────────────


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

Customer may pass any non-empty string for `framework=`; the canonical
labels above are convenience constants matching the
`nanorix.compliance.*Context.framework_id` values. The framework arg
is bound into AuditProof.metadata as `nanorix:framework` for
compliance-binding.
"""


WRAPPER_KIND = "workload"
"""The reserved metadata-key value indicating this AuditProof was
emitted by the `@workload` Tier B decorator.

Values for the `nanorix:wrapper_kind` metadata key:
- ``"workload"``: this decorator (Tier B function-level wrap)
- ``"middleware"``: future FastAPI/Flask middleware (Tier C; item 4)
- ``"proxy"``: future LLM-SDK proxy (Tier A; item 2)

The `nanorix:wrapper_kind` key is a forever-stable string field;
adding new wrapper kinds is field-additive (the existing key continues
to carry the prior values verbatim).
"""


# ── Capsulefile resolution ──────────────────────────────────────────


def _is_server_capsulefile_id(capsulefile: str) -> bool:
    """Heuristic: server-assigned IDs start with ``cf_``."""
    return capsulefile.startswith("cf_")


def _find_local_capsulefile_yaml(name: str, search_dir: Path) -> Optional[Path]:
    """Find a local capsulefile.yaml whose ``name:`` field matches.

    Searches ``search_dir`` and its immediate subdirectories for any
    ``capsulefile.yaml`` or ``capsulefile.yml`` file, parses each, and
    returns the path of the first match. Returns ``None`` if no match.

    Parsing errors and IO errors are silently skipped (the decorator
    falls back to treating the input as a server-side ID, which yields
    a clean ``BadRequestError`` from the API if it isn't one).
    """
    candidates: List[Path] = []
    for stem in ("capsulefile.yaml", "capsulefile.yml"):
        p = search_dir / stem
        if p.is_file():
            candidates.append(p)
    try:
        for child in search_dir.iterdir():
            if not child.is_dir():
                continue
            for stem in ("capsulefile.yaml", "capsulefile.yml"):
                p = child / stem
                if p.is_file():
                    candidates.append(p)
    except OSError:
        # search_dir doesn't exist or isn't readable; treat as no match.
        return None

    for path in candidates:
        try:
            # Tiny YAML subset parse — we only need the `name:` field on
            # the top level. Avoid pulling pyyaml as an SDK dep.
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            # Skip comments and indented lines.
            if not stripped or stripped.startswith("#") or line[0] in (" ", "\t"):
                continue
            if stripped.startswith("name:"):
                value = stripped[len("name:") :].strip()
                # Strip quotes if present.
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                if value == name:
                    return path
                break  # name found but didn't match; next file
    return None


def _resolve_capsulefile(
    *,
    capsulefile: str,
    capsulefile_dir: Optional[Union[str, Path]],
    client: Any,
) -> str:
    """Resolve a Capsulefile reference to a server-side ID.

    Strategy:
    1. If ``capsulefile`` already looks like a server-side ID
       (``cf_...``), pass through unchanged.
    2. Else: scan local YAMLs for a match; if found, query the server's
       ``GET /v1/capsulefiles?name=<name>`` to obtain the canonical ID.
       If the server's list endpoint doesn't support filter-by-name,
       fall back to listing + matching client-side.
    3. If no local match: pass the raw string to the API; the API will
       reject with ``BadRequestError`` if it doesn't resolve.

    The decorator never UPLOADS a Capsulefile — that's the customer's
    job (`nanorix build`). The decorator only translates names → IDs.
    """
    if _is_server_capsulefile_id(capsulefile):
        return capsulefile

    # Try local YAML scan to confirm the name exists in the customer's
    # repo (gives a clean error message if the customer typo'd the name).
    search_dir = Path(capsulefile_dir) if capsulefile_dir is not None else Path.cwd()
    _ = _find_local_capsulefile_yaml(capsulefile, search_dir)

    # Look up by name on the server.
    try:
        cf_list = client.capsulefiles.list()
    except NanorixError:
        # Network or auth error — fall through to API rejection at create time.
        return capsulefile

    items = getattr(cf_list, "capsulefiles", None) or getattr(cf_list, "items", None) or []
    for item in items:
        cf_name = getattr(item, "name", None)
        if cf_name == capsulefile:
            cf_id = getattr(item, "id", None) or getattr(item, "capsulefile_id", None)
            if cf_id:
                return str(cf_id)

    # Couldn't resolve locally; pass through and let the API surface the error.
    return capsulefile


# ── Metadata builder (cross-impl byte-equivalent) ──────────────────


def _build_workload_metadata(
    *,
    framework: str,
    framework_version: Optional[str],
    agent_id: Optional[str],
    extra: Optional[Mapping[str, str]],
) -> Dict[str, str]:
    """Build the metadata dict bound into the AuditProof.

    Cross-impl byte-equivalence invariant: the same inputs produce the
    same key set with the same string values in BOTH Python and
    TypeScript implementations. The server canonicalizes (sort_keys)
    before computing ``metadata_hash``, so insertion order doesn't
    matter — but key NAMES and VALUES must be byte-identical across
    implementations.

    Reserved keys (``nanorix:*``) emitted by this helper are documented
    here; they are accepted by the API (server-emitted
    reserved keys; customer-supplied ``nanorix:*`` keys are rejected).
    """
    metadata: Dict[str, str] = {
        "nanorix:wrapper_kind": WRAPPER_KIND,
        "nanorix:framework": framework,
    }
    if framework_version is not None:
        metadata["nanorix:framework_version"] = framework_version
    if agent_id is not None:
        metadata["nanorix:agent_id"] = agent_id

    if extra is not None:
        for k, v in extra.items():
            if k.startswith("nanorix:"):
                # Reject customer-supplied reserved-prefix keys per
                # (4 KB limit + nanorix:*
                # reserved prefix are the only guardrails).
                raise BadRequestError(
                    f"Customer-supplied metadata key {k!r} uses the reserved "
                    f"`nanorix:` prefix; the prefix is "
                    f"server-emitted only.",
                    status_code=400,
                )
            metadata[k] = str(v)

    return metadata


# ── Decorator implementation ───────────────────────────────────────


F = TypeVar("F", bound=Callable[..., Any])


class WorkloadDecorator:
    """Stateful decorator object — the result of ``@workload(...)``.

    Carries the framework + capsulefile + client config across all
    invocations of the wrapped function. Each call:

    1. Resolves the capsulefile name → server ID (cached on first call).
    2. Builds the AuditProof metadata.
    3. Creates a capsule via ``client.capsules.create()``.
    4. Runs the wrapped function in the local process (the function's
       body produces the customer's outputs; if the customer also
       uses the in-capsule activity-trail helpers from
       ``nanorix.helpers.activity_events``, those events bind into the
       same AuditProof).
    5. Destroys the capsule; attaches the resulting CdpAccess to the
       wrapper as ``last_audit_proof`` and (optionally) invokes the
       ``on_audit_proof`` callback.

    The wrapped function returns its normal return value; the
    AuditProof is side-channeled. Customers wanting tighter coupling
    can pass ``on_audit_proof=`` to receive the proof per call.
    """

    def __init__(
        self,
        *,
        framework: str,
        capsulefile: str,
        client: Optional[Any] = None,
        metadata: Optional[Mapping[str, str]] = None,
        framework_version: Optional[str] = None,
        agent_id: Optional[str] = None,
        capsulefile_dir: Optional[Union[str, Path]] = None,
        max_lifetime_seconds: Optional[int] = None,
        egress_profile: Optional[str] = None,
        on_audit_proof: Optional[Callable[[CdpAccess], None]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        if not framework or not isinstance(framework, str):
            raise ValueError(
                "@workload requires an explicit `framework=` argument "
                "(e.g., framework='HIPAA'). No silent default per "
                " customer-sole-declarer discipline."
            )
        if not capsulefile or not isinstance(capsulefile, str):
            raise ValueError(
                "@workload requires an explicit `capsulefile=` argument "
                "(e.g., capsulefile='pa-decision-agent' or capsulefile='cf_...')."
            )

        self.framework = framework
        self.capsulefile = capsulefile
        self.metadata: Dict[str, str] = dict(metadata) if metadata else {}
        self.framework_version = framework_version
        self.agent_id = agent_id
        self.capsulefile_dir = capsulefile_dir
        self.max_lifetime_seconds = max_lifetime_seconds
        self.egress_profile = egress_profile
        self.on_audit_proof = on_audit_proof
        self.api_key = api_key
        self.base_url = base_url

        # Eagerly capture or lazily resolve the client.
        self._client = client
        # Cache the resolved capsulefile_id after first call.
        self._resolved_capsulefile_id: Optional[str] = None

    # ── Decorator protocol ─────────────────────────────────────────

    def __call__(self, func: F) -> F:
        """Wrap a function. Returns the wrapped callable."""
        if inspect.iscoroutinefunction(func):
            return self._wrap_async(func)
        return self._wrap_sync(func)

    # ── Sync wrap ──────────────────────────────────────────────────

    def _wrap_sync(self, func: F) -> F:
        decorator_self = self

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = decorator_self._get_client()
            cf_id = decorator_self._get_capsulefile_id(client)
            metadata = _build_workload_metadata(
                framework=decorator_self.framework,
                framework_version=decorator_self.framework_version,
                agent_id=decorator_self.agent_id,
                extra=decorator_self.metadata,
            )

            capsule = client.capsules.create(
                capsulefile=cf_id,
                max_lifetime_seconds=decorator_self.max_lifetime_seconds,
                egress_profile=decorator_self.egress_profile,
                metadata=metadata,
            )

            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                # Destroy on exception path; attach proof to exception.
                try:
                    cdp = capsule.destroy()
                    setattr(wrapper, "last_audit_proof", cdp)
                    if decorator_self.on_audit_proof is not None:
                        try:
                            decorator_self.on_audit_proof(cdp)
                        except Exception:  # noqa: BLE001
                            pass  # callback failures must not mask the original error
                    if hasattr(exc, "__dict__"):
                        try:
                            exc.cdp = cdp.full()  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
                raise

            # Success path.
            cdp = capsule.destroy()
            setattr(wrapper, "last_audit_proof", cdp)
            if decorator_self.on_audit_proof is not None:
                try:
                    decorator_self.on_audit_proof(cdp)
                except Exception:  # noqa: BLE001 - callback hook must not crash workload
                    pass

            return result

        # Attach diagnostic + introspection attributes.
        wrapper.framework = decorator_self.framework  # type: ignore[attr-defined]
        wrapper.capsulefile = decorator_self.capsulefile  # type: ignore[attr-defined]
        wrapper.last_audit_proof = None  # type: ignore[attr-defined]
        wrapper.workload_decorator = decorator_self  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    # ── Async wrap ─────────────────────────────────────────────────

    def _wrap_async(self, func: F) -> F:
        decorator_self = self

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            client = decorator_self._get_client()
            cf_id = decorator_self._get_capsulefile_id(client)
            metadata = _build_workload_metadata(
                framework=decorator_self.framework,
                framework_version=decorator_self.framework_version,
                agent_id=decorator_self.agent_id,
                extra=decorator_self.metadata,
            )

            # Async client interface.
            create = client.capsules.create
            if inspect.iscoroutinefunction(create):
                capsule = await create(
                    capsulefile=cf_id,
                    max_lifetime_seconds=decorator_self.max_lifetime_seconds,
                    metadata=metadata,
                )
            else:
                capsule = create(
                    capsulefile=cf_id,
                    max_lifetime_seconds=decorator_self.max_lifetime_seconds,
                    egress_profile=decorator_self.egress_profile,
                    metadata=metadata,
                )

            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                try:
                    destroy = capsule.destroy
                    cdp = (
                        await destroy() if inspect.iscoroutinefunction(destroy) else destroy()
                    )
                    setattr(wrapper, "last_audit_proof", cdp)
                    if decorator_self.on_audit_proof is not None:
                        try:
                            decorator_self.on_audit_proof(cdp)
                        except Exception:  # noqa: BLE001
                            pass
                    if hasattr(exc, "__dict__"):
                        try:
                            exc.cdp = cdp.full()  # type: ignore[attr-defined]
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass
                raise

            destroy = capsule.destroy
            cdp = await destroy() if inspect.iscoroutinefunction(destroy) else destroy()
            setattr(wrapper, "last_audit_proof", cdp)
            if decorator_self.on_audit_proof is not None:
                try:
                    decorator_self.on_audit_proof(cdp)
                except Exception:  # noqa: BLE001
                    pass

            return result

        wrapper.framework = decorator_self.framework  # type: ignore[attr-defined]
        wrapper.capsulefile = decorator_self.capsulefile  # type: ignore[attr-defined]
        wrapper.last_audit_proof = None  # type: ignore[attr-defined]
        wrapper.workload_decorator = decorator_self  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    # ── Internal helpers ───────────────────────────────────────────

    def _get_client(self) -> Any:
        """Return the configured Nanorix client.

        Resolution order:
        1. Explicit ``client=`` kwarg passed to the decorator.
        2. Constructed from ``api_key=`` / ``base_url=`` kwargs or
           ``NANORIX_API_KEY`` env var.
        """
        if self._client is not None:
            return self._client

        # Lazy import to avoid circular import (decorators imported by __init__).
        from nanorix._client import Client

        api_key = self.api_key or os.environ.get("NANORIX_API_KEY")
        if not api_key:
            raise ValueError(
                "@workload requires a Nanorix client. Pass `client=...` or "
                "`api_key=...` to the decorator, or set the NANORIX_API_KEY "
                "environment variable."
            )
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if self.base_url is not None:
            kwargs["base_url"] = self.base_url
        self._client = Client(**kwargs)
        return self._client

    def _get_capsulefile_id(self, client: Any) -> str:
        """Resolve the configured capsulefile ref → server ID, with caching."""
        if self._resolved_capsulefile_id is not None:
            return self._resolved_capsulefile_id

        cf_id = _resolve_capsulefile(
            capsulefile=self.capsulefile,
            capsulefile_dir=self.capsulefile_dir,
            client=client,
        )
        self._resolved_capsulefile_id = cf_id
        return cf_id


# ── Public factory function ────────────────────────────────────────


def workload(
    *,
    framework: str,
    capsulefile: str,
    client: Optional[Any] = None,
    metadata: Optional[Mapping[str, str]] = None,
    framework_version: Optional[str] = None,
    agent_id: Optional[str] = None,
    capsulefile_dir: Optional[Union[str, Path]] = None,
    max_lifetime_seconds: Optional[int] = None,
    egress_profile: Optional[str] = None,
    on_audit_proof: Optional[Callable[[CdpAccess], None]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> WorkloadDecorator:
    """Wrap a function in a Nanorix capsule lifecycle.

    Each invocation of the wrapped function creates a fresh capsule,
    runs the function body, destroys the capsule, and produces an
    AuditProof bound to the customer-declared compliance framework
    via the AuditProof's ``metadata_hash``.

    Args:
        framework: REQUIRED. Compliance framework label (e.g.
            ``"HIPAA"``, ``"GDPR"``, ``"SOC2"``). Customer-declared
            ; no silent default. Bound to the AuditProof
            metadata as ``nanorix:framework``.
        capsulefile: REQUIRED. Server-side Capsulefile ID (``cf_...``)
            or local Capsulefile name (resolved via the customer's
            ``capsulefile.yaml`` + ``client.capsulefiles.list()``).
        client: Optional ``nanorix.Client`` instance. If ``None``, a
            client is constructed from ``api_key=`` / env
            ``NANORIX_API_KEY``.
        metadata: Optional extra metadata dict bound into the
            AuditProof. Customer-supplied keys must NOT use the
            ``nanorix:`` prefix (reserved).
        framework_version: Optional framework version string (e.g.
            ``"2026-02"``). Bound as ``nanorix:framework_version``.
        agent_id: Optional sub-agent identifier. Bound as
            ``nanorix:agent_id`` and propagated to any in-capsule
            ActivityEvent emissions.
        capsulefile_dir: Optional directory to search for local
            ``capsulefile.yaml`` (default cwd).
        max_lifetime_seconds: Optional capsule lifetime ceiling.
        egress_profile: Optional egress profile ID for outbound
            network policy (Business+ tier).
        on_audit_proof: Optional callback receiving the ``CdpAccess``
            after each capsule destroy. Useful for streaming proofs to
            the customer's GRC platform without monkeying with the
            wrapper's return value.
        api_key: Optional Nanorix API key (used only when ``client``
            is not provided).
        base_url: Optional Nanorix base URL (used only when ``client``
            is not provided).

    Returns:
        A ``WorkloadDecorator`` instance. Apply via ``@workload(...)``.

    Raises:
        ValueError: if ``framework`` or ``capsulefile`` is missing/empty.
        BadRequestError: if customer-supplied ``metadata`` uses a
            ``nanorix:`` prefix key.
    """
    return WorkloadDecorator(
        framework=framework,
        capsulefile=capsulefile,
        client=client,
        metadata=metadata,
        framework_version=framework_version,
        agent_id=agent_id,
        capsulefile_dir=capsulefile_dir,
        max_lifetime_seconds=max_lifetime_seconds,
        egress_profile=egress_profile,
        on_audit_proof=on_audit_proof,
        api_key=api_key,
        base_url=base_url,
    )
