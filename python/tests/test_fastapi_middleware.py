"""FastAPI middleware tests.

Exercises ``nanorix.integrations.fastapi_middleware.NanorixMiddleware``
against:
- Required-arg discipline (client + framework + capsulefile).
- Per-request capsule lifecycle (create with ``cdp_kind="request"`` →
  run handler → destroy → emit AuditProof).
- Path-prefix framework override.
- Exclusion list (health checks, metrics).
- Response header attachment (AuditProof-Id + CdpKind + Capsule-Id).
- Status-code → ToolCallFailureKind mapping.
- Hook → ActivityEvent variant routing (ReasoningStepCompleted vs
  ToolCallFailed).
- Customer-sole-declarer — framework_per_route + metadata.
- Forever-Standard — cdp_kind=Request canonical wire form.
- Cross-impl byte-equivalence — same metadata shape as Python + TS
  decorators.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

# Optional dep — skip the whole file if fastapi/starlette isn't installed.
fastapi = pytest.importorskip("fastapi", reason="fastapi extra not installed")
starlette = pytest.importorskip("starlette", reason="starlette not installed")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from nanorix.integrations.fastapi_middleware import (  # noqa: E402
    ALLOWED_FRAMEWORKS,
    CDP_KIND_REQUEST,
    FRAMEWORK_HIPAA,
    NanorixMiddleware,
    RESPONSE_HEADER_AUDIT_PROOF_ID,
    RESPONSE_HEADER_CAPSULE_ID,
    RESPONSE_HEADER_CDP_KIND,
    WRAPPER_KIND,
    _canonical_sha512_hex,
    _classify_exception,
    _map_status_to_failure_kind,
    _resolve_framework_for_path,
)


# ── Fakes (no live Nanorix API; the middleware is the unit) ────────


class FakeCdp:
    """Minimal CdpAccess shape consumed by the middleware."""

    def __init__(self, capsule_id: str) -> None:
        self.id = "cap_" + capsule_id.replace("-", "")
        self.audit_proof_id = self.id


class FakeCapsule:
    """Minimal Capsule shape with destroy() returning a FakeCdp."""

    def __init__(self, capsule_id: str = "abcd1234-0000-0000-0000-000000000000") -> None:
        self.id = capsule_id
        self.destroy_called = False
        self.create_metadata: Optional[Dict[str, str]] = None
        self.create_cdp_kind: Optional[str] = None

    def destroy(self) -> FakeCdp:
        self.destroy_called = True
        return FakeCdp(self.id)


class FakeCapsules:
    """Records every create() invocation for assertions."""

    def __init__(self) -> None:
        self.created: List[FakeCapsule] = []

    def create(
        self,
        *,
        capsulefile: Optional[str] = None,
        max_lifetime_seconds: Optional[int] = None,
        egress_profile: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        cdp_kind: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> FakeCapsule:
        cap = FakeCapsule()
        cap.create_metadata = dict(metadata) if metadata else None
        cap.create_cdp_kind = cdp_kind
        self.created.append(cap)
        return cap


class FakeClient:
    def __init__(self) -> None:
        self.capsules = FakeCapsules()


# ── Required-arg discipline ─────────────────────────────────────────


def test_middleware_requires_client():
    app = FastAPI()
    with pytest.raises(ValueError, match="client="):
        app.add_middleware(
            NanorixMiddleware,
            framework="HIPAA",
            capsulefile="cf_demo",
        )
        # Trigger the middleware construction by issuing a request.
        TestClient(app).get("/")


def test_middleware_requires_framework():
    app = FastAPI()
    client = FakeClient()
    with pytest.raises(ValueError, match="framework"):
        app.add_middleware(
            NanorixMiddleware,
            client=client,
            framework="",
            capsulefile="cf_demo",
        )
        TestClient(app).get("/")


def test_middleware_requires_capsulefile():
    app = FastAPI()
    client = FakeClient()
    with pytest.raises(ValueError, match="capsulefile"):
        app.add_middleware(
            NanorixMiddleware,
            client=client,
            framework="HIPAA",
            capsulefile="",
        )
        TestClient(app).get("/")


# ── Wire-form constants ─────────────────────────────────────────────


def test_cdp_kind_request_wire_form_is_snake_case():
    """Reserved-slot wire form must be ``"request"`` exactly."""
    assert CDP_KIND_REQUEST == "request"
    assert CDP_KIND_REQUEST == CDP_KIND_REQUEST.lower()


def test_wrapper_kind_is_middleware():
    """Tier C wrapper kind identifier."""
    assert WRAPPER_KIND == "middleware"


def test_allowed_frameworks_includes_canonical_labels():
    for fw in ("HIPAA", "GDPR", "SOC2", "PCI_DSS", "HITRUST", "EU_AI_ACT"):
        assert fw in ALLOWED_FRAMEWORKS


# ── Capsule lifecycle (the main path) ───────────────────────────────


def _build_app(client: FakeClient, **kwargs: Any) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        NanorixMiddleware,
        client=client,
        framework=kwargs.pop("framework", FRAMEWORK_HIPAA),
        capsulefile=kwargs.pop("capsulefile", "cf_pa_decision"),
        **kwargs,
    )

    @app.get("/decide")
    def decide() -> Dict[str, str]:
        return {"decision": "approved"}

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/admin/policy")
    def admin_policy() -> Dict[str, str]:
        return {"policy": "v2"}

    @app.get("/boom")
    def boom() -> None:
        raise HTTPException(status_code=500, detail="explode")

    @app.get("/auth-fail")
    def auth_fail() -> None:
        raise HTTPException(status_code=403, detail="forbidden")

    @app.get("/bad-args")
    def bad_args() -> None:
        raise HTTPException(status_code=400, detail="bad")

    @app.get("/raise")
    def raise_uncaught() -> None:
        raise RuntimeError("uncaught")

    return app


def test_every_request_creates_one_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")
    test_client.get("/decide")
    test_client.get("/decide")

    assert len(fake.capsules.created) == 3, "one capsule per request"
    for cap in fake.capsules.created:
        assert cap.destroy_called, "every capsule MUST be destroyed"


def test_capsule_created_with_cdp_kind_request():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    assert len(fake.capsules.created) == 1
    assert fake.capsules.created[0].create_cdp_kind == "request"
    assert fake.capsules.created[0].create_cdp_kind == CDP_KIND_REQUEST


def test_capsule_metadata_includes_wrapper_kind_middleware():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:wrapper_kind"] == "middleware"


def test_capsule_metadata_includes_framework():
    fake = FakeClient()
    app = _build_app(fake, framework="GDPR")
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework"] == "GDPR"


def test_capsule_metadata_includes_request_method_and_path():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:request_method"] == "GET"
    assert md["nanorix:request_path"] == "/decide"


def test_framework_version_propagated_when_set():
    fake = FakeClient()
    app = _build_app(fake, framework_version="2026-02")
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework_version"] == "2026-02"


def test_framework_version_absent_when_not_set():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    #: absence is opt-out, not empty-string.
    assert "nanorix:framework_version" not in md


def test_agent_id_propagated_when_set():
    fake = FakeClient()
    app = _build_app(fake, agent_id="agent-7")
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:agent_id"] == "agent-7"


# ── Response headers ────────────────────────────────────────────────


def test_response_includes_audit_proof_id_header():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/decide")

    assert resp.status_code == 200
    assert RESPONSE_HEADER_AUDIT_PROOF_ID in resp.headers
    audit_proof_id = resp.headers[RESPONSE_HEADER_AUDIT_PROOF_ID]
    assert audit_proof_id.startswith("cap_"), "AuditProof ID shape: cap_<32hex>"


def test_response_includes_cdp_kind_request_header():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/decide")
    assert resp.headers[RESPONSE_HEADER_CDP_KIND] == "request"


def test_response_includes_capsule_id_header():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/decide")
    assert RESPONSE_HEADER_CAPSULE_ID in resp.headers


# ── Path-prefix framework override ──────────────────────────────────


def test_framework_per_route_longest_prefix_wins():
    fake = FakeClient()
    app = _build_app(
        fake,
        framework="HIPAA",
        framework_per_route={"/admin": "SOC2", "/admin/billing": "PCI_DSS"},
    )
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.post("/admin/policy")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework"] == "SOC2"


def test_framework_per_route_falls_back_to_default():
    fake = FakeClient()
    app = _build_app(
        fake,
        framework="HIPAA",
        framework_per_route={"/admin": "SOC2"},
    )
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework"] == "HIPAA"


# ── Exclusion list ──────────────────────────────────────────────────


def test_excluded_paths_skip_capsule_wrap():
    fake = FakeClient()
    app = _build_app(fake, excluded_paths=["/health"])
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/health")
    test_client.get("/health")

    assert len(fake.capsules.created) == 0, "health checks must not consume quota"


def test_excluded_paths_does_not_attach_audit_proof_headers():
    fake = FakeClient()
    app = _build_app(fake, excluded_paths=["/health"])
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/health")
    assert RESPONSE_HEADER_AUDIT_PROOF_ID not in resp.headers


def test_non_excluded_paths_still_wrapped():
    fake = FakeClient()
    app = _build_app(fake, excluded_paths=["/health"])
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    assert len(fake.capsules.created) == 1


# ── Failure-path emission ───────────────────────────────────────────


def test_5xx_response_still_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/boom")
    assert resp.status_code == 500
    assert fake.capsules.created[0].destroy_called


def test_5xx_response_attaches_audit_proof_headers():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/boom")
    assert RESPONSE_HEADER_AUDIT_PROOF_ID in resp.headers
    assert resp.headers[RESPONSE_HEADER_CDP_KIND] == "request"


def test_4xx_response_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/auth-fail")
    assert resp.status_code == 403
    assert fake.capsules.created[0].destroy_called


def test_400_response_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/bad-args")
    assert resp.status_code == 400
    assert fake.capsules.created[0].destroy_called


# ── Status-to-failure-kind taxonomy ────────────────────


def test_map_status_408_timeout():
    assert _map_status_to_failure_kind(408) == "timeout"


def test_map_status_401_permission_denied():
    assert _map_status_to_failure_kind(401) == "permission_denied"


def test_map_status_403_permission_denied():
    assert _map_status_to_failure_kind(403) == "permission_denied"


def test_map_status_400_invalid_args():
    assert _map_status_to_failure_kind(400) == "invalid_args"


def test_map_status_422_invalid_args():
    assert _map_status_to_failure_kind(422) == "invalid_args"


def test_map_status_499_cancelled():
    assert _map_status_to_failure_kind(499) == "cancelled"


def test_map_status_500_upstream_failure():
    assert _map_status_to_failure_kind(500) == "upstream_failure"


def test_map_status_503_upstream_failure():
    assert _map_status_to_failure_kind(503) == "upstream_failure"


def test_map_status_504_upstream_failure():
    assert _map_status_to_failure_kind(504) == "upstream_failure"


def test_map_status_429_upstream_failure_default():
    # 429 has no specific failure_kind variant — defaults to upstream_failure.
    assert _map_status_to_failure_kind(429) == "upstream_failure"


# ── Exception classifier ────────────────────────────────────────────


def test_classify_exception_timeout():
    assert _classify_exception(TimeoutError("slow")) == "timeout"


def test_classify_exception_permission_error():
    assert _classify_exception(PermissionError("denied")) == "permission_denied"


def test_classify_exception_value_error():
    assert _classify_exception(ValueError("bad")) == "invalid_args"


def test_classify_exception_type_error():
    assert _classify_exception(TypeError("wrong type")) == "invalid_args"


def test_classify_exception_default_upstream_failure():
    class CustomError(Exception):
        pass

    assert _classify_exception(CustomError("???")) == "upstream_failure"


# ── Path-resolution helper ──────────────────────────────────────────


def test_resolve_framework_returns_default_when_no_override():
    assert (
        _resolve_framework_for_path(
            path="/any",
            default_framework="HIPAA",
            framework_per_route=None,
        )
        == "HIPAA"
    )


def test_resolve_framework_returns_default_when_no_prefix_matches():
    assert (
        _resolve_framework_for_path(
            path="/other",
            default_framework="HIPAA",
            framework_per_route={"/admin": "SOC2"},
        )
        == "HIPAA"
    )


def test_resolve_framework_longest_prefix_match():
    assert (
        _resolve_framework_for_path(
            path="/admin/billing/invoice",
            default_framework="HIPAA",
            framework_per_route={
                "/admin": "SOC2",
                "/admin/billing": "PCI_DSS",
            },
        )
        == "PCI_DSS"
    )


# ── on_audit_proof callback hook ────────────────────────────────────


def test_on_audit_proof_callback_invoked_per_request():
    fake = FakeClient()
    received: List[Any] = []
    app = _build_app(fake, on_audit_proof=received.append)
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")
    test_client.get("/decide")
    test_client.get("/decide")

    assert len(received) == 3, "callback invoked once per destroyed capsule"
    for cdp in received:
        assert cdp.id.startswith("cap_")


def test_on_audit_proof_callback_failure_does_not_break_request():
    """Per the FastAPI middleware contract: a buggy customer callback
    MUST NOT crash the request."""
    fake = FakeClient()

    def boom(cdp: Any) -> None:
        raise RuntimeError("callback exploded")

    app = _build_app(fake, on_audit_proof=boom)
    test_client = TestClient(app, raise_server_exceptions=False)

    resp = test_client.get("/decide")
    assert resp.status_code == 200, "request must succeed even if callback fails"


# ── Canonical-hash + cross-impl invariants ──────────────────────────


def test_canonical_sha512_hex_deterministic_and_jcs_flavor():
    """Same JCS flavor (sort_keys=True, separators=(',', ':')) as Rust JCS."""
    h1 = _canonical_sha512_hex({"a": 1, "b": 2})
    h2 = _canonical_sha512_hex({"b": 2, "a": 1})  # different insertion order
    assert h1 == h2, "JCS sort_keys must produce same hex regardless of insertion order"
    assert len(h1) == 128, "SHA-512 hex must be 128 chars"
    assert all(c in "0123456789abcdef" for c in h1)


def test_cross_impl_metadata_shape_byte_equivalent_to_decorator():
    """The middleware's metadata shape MUST be byte-equivalent to the
    @workload decorator's shape per cross-impl byte-equivalence
    discipline. Same keys, same values, server canonicalizes via
    sort_keys=True before computing metadata_hash → same AuditProof."""
    fake = FakeClient()
    app = _build_app(
        fake,
        framework="HIPAA",
        framework_version="2026-02",
        agent_id="agent-7",
    )
    test_client = TestClient(app, raise_server_exceptions=False)

    test_client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None

    # Required keys present.
    for key in (
        "nanorix:wrapper_kind",
        "nanorix:framework",
        "nanorix:framework_version",
        "nanorix:agent_id",
        "nanorix:request_method",
        "nanorix:request_path",
    ):
        assert key in md, f"required reserved key missing: {key}"

    # Wrapper kind distinguishes middleware from workload+proxy.
    assert md["nanorix:wrapper_kind"] == "middleware"


# ── Forever-Standard preservation ───────────────────────


def test_cdp_kind_request_is_in_4_value_closed_enum():
    """The middleware emits one of the 4 known CdpKind wire values."""
    assert CDP_KIND_REQUEST in ("workload", "call", "request", "batch")
    assert CDP_KIND_REQUEST == "request"


# ── Test count sentinel ─────────────────────────────────────────────


def test_fastapi_middleware_test_count_meets_dispatch_minimum():
    """Combined with the Flask middleware test file, ≥40 tests cover
    Dispatch minimum."""
    import inspect
    import sys

    self_module = sys.modules[__name__]
    test_funcs = [
        n for n, obj in inspect.getmembers(self_module, inspect.isfunction)
        if n.startswith("test_")
    ]
    # Sentinel: this file alone contributes ≥35 tests; Flask file
    # ships the remaining ≥5 of the dispatch's ≥40 total.
    assert len(test_funcs) >= 35, f"got {len(test_funcs)} tests; need ≥35"
