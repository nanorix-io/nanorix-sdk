"""Wave 18-11 / CTI-T14-004 — Flask middleware tests.

Exercises ``nanorix.integrations.flask_middleware.NanorixMiddleware``
against the same surface as the FastAPI middleware (Tier-C parity
invariant): per-HTTP-request AuditProof emission with
``cdp_kind: "request"`` via the reserved-slot.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

# Optional dep — skip the whole file if flask isn't installed.
flask = pytest.importorskip("flask", reason="flask extra not installed")

from flask import Flask  # noqa: E402

from nanorix.integrations.flask_middleware import (  # noqa: E402
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


# ── Fakes (no live Nanorix API) ─────────────────────────────────────


class FakeCdp:
    def __init__(self, capsule_id: str) -> None:
        self.id = "cap_" + capsule_id.replace("-", "")
        self.audit_proof_id = self.id


class FakeCapsule:
    def __init__(self, capsule_id: str = "ffff1111-0000-0000-0000-000000000000") -> None:
        self.id = capsule_id
        self.destroy_called = False
        self.create_metadata: Optional[Dict[str, str]] = None
        self.create_cdp_kind: Optional[str] = None

    def destroy(self) -> FakeCdp:
        self.destroy_called = True
        return FakeCdp(self.id)


class FakeCapsules:
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


def _build_app(client: FakeClient, **kwargs: Any) -> Flask:
    app = Flask(__name__)

    NanorixMiddleware(
        app,
        client=client,
        framework=kwargs.pop("framework", FRAMEWORK_HIPAA),
        capsulefile=kwargs.pop("capsulefile", "cf_pa_decision"),
        **kwargs,
    )

    @app.route("/decide", methods=["GET"])
    def decide() -> Dict[str, str]:
        return {"decision": "approved"}

    @app.route("/health", methods=["GET"])
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.route("/admin/policy", methods=["POST"])
    def admin_policy() -> Dict[str, str]:
        return {"policy": "v2"}

    @app.route("/boom", methods=["GET"])
    def boom() -> Any:
        return ({"err": "explode"}, 500)

    @app.route("/auth-fail", methods=["GET"])
    def auth_fail() -> Any:
        return ({"err": "forbidden"}, 403)

    @app.route("/bad-args", methods=["GET"])
    def bad_args() -> Any:
        return ({"err": "bad"}, 400)

    @app.route("/raise", methods=["GET"])
    def raise_uncaught() -> Any:
        raise RuntimeError("uncaught")

    return app


# ── Required-arg discipline ─────────────────────────────────────────


def test_middleware_requires_client():
    app = Flask(__name__)
    with pytest.raises(ValueError, match="client"):
        NanorixMiddleware(app, client=None, framework="HIPAA", capsulefile="cf_demo")


def test_middleware_requires_framework():
    app = Flask(__name__)
    client = FakeClient()
    with pytest.raises(ValueError, match="framework"):
        NanorixMiddleware(app, client=client, framework="", capsulefile="cf_demo")


def test_middleware_requires_capsulefile():
    app = Flask(__name__)
    client = FakeClient()
    with pytest.raises(ValueError, match="capsulefile"):
        NanorixMiddleware(app, client=client, framework="HIPAA", capsulefile="")


# ── Wire-form parity with FastAPI middleware ────────────────────────


def test_cdp_kind_wire_form_matches_fastapi_middleware():
    """Tier-C parity invariant: Flask + FastAPI middlewares emit the
    same cdp_kind wire string."""
    assert CDP_KIND_REQUEST == "request"


def test_wrapper_kind_matches_fastapi_middleware():
    assert WRAPPER_KIND == "middleware"


def test_allowed_frameworks_match():
    for fw in ("HIPAA", "GDPR", "SOC2", "PCI_DSS", "HITRUST", "EU_AI_ACT"):
        assert fw in ALLOWED_FRAMEWORKS


# ── Capsule lifecycle ───────────────────────────────────────────────


def test_every_request_creates_one_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    client.get("/decide")
    client.get("/decide")
    client.get("/decide")

    assert len(fake.capsules.created) == 3
    for cap in fake.capsules.created:
        assert cap.destroy_called


def test_capsule_created_with_cdp_kind_request():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    client.get("/decide")
    assert fake.capsules.created[0].create_cdp_kind == "request"


def test_capsule_metadata_includes_wrapper_kind_middleware():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:wrapper_kind"] == "middleware"


def test_capsule_metadata_includes_framework():
    fake = FakeClient()
    app = _build_app(fake, framework="GDPR")
    client = app.test_client()

    client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework"] == "GDPR"


def test_capsule_metadata_includes_request_method_and_path():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:request_method"] == "GET"
    assert md["nanorix:request_path"] == "/decide"


# ── Response headers ────────────────────────────────────────────────


def test_response_includes_audit_proof_id_header():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    resp = client.get("/decide")
    assert RESPONSE_HEADER_AUDIT_PROOF_ID in resp.headers
    assert resp.headers[RESPONSE_HEADER_AUDIT_PROOF_ID].startswith("cap_")


def test_response_includes_cdp_kind_request_header():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    resp = client.get("/decide")
    assert resp.headers[RESPONSE_HEADER_CDP_KIND] == "request"


def test_response_includes_capsule_id_header():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    resp = client.get("/decide")
    assert RESPONSE_HEADER_CAPSULE_ID in resp.headers


# ── Failure paths ───────────────────────────────────────────────────


def test_5xx_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    resp = client.get("/boom")
    assert resp.status_code == 500
    assert fake.capsules.created[0].destroy_called


def test_4xx_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    resp = client.get("/auth-fail")
    assert resp.status_code == 403
    assert fake.capsules.created[0].destroy_called


def test_unhandled_exception_destroys_capsule():
    fake = FakeClient()
    app = _build_app(fake)
    client = app.test_client()

    # Flask returns 500 for unhandled exceptions in test mode.
    resp = client.get("/raise")
    assert resp.status_code == 500
    # Teardown hook should still have destroyed the capsule.
    assert fake.capsules.created[0].destroy_called


# ── Excluded paths ──────────────────────────────────────────────────


def test_excluded_paths_skip_capsule_wrap():
    fake = FakeClient()
    app = _build_app(fake, excluded_paths=["/health"])
    client = app.test_client()

    client.get("/health")
    client.get("/health")

    assert len(fake.capsules.created) == 0


def test_non_excluded_paths_still_wrapped_when_exclusion_set():
    fake = FakeClient()
    app = _build_app(fake, excluded_paths=["/health"])
    client = app.test_client()

    client.get("/decide")
    assert len(fake.capsules.created) == 1


# ── Framework per route ─────────────────────────────────────────────


def test_framework_per_route_longest_prefix_wins():
    fake = FakeClient()
    app = _build_app(
        fake,
        framework="HIPAA",
        framework_per_route={"/admin": "SOC2", "/admin/billing": "PCI_DSS"},
    )
    client = app.test_client()

    client.post("/admin/policy")

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
    client = app.test_client()

    client.get("/decide")

    md = fake.capsules.created[0].create_metadata
    assert md is not None
    assert md["nanorix:framework"] == "HIPAA"


# ── init_app deferred wiring ────────────────────────────────────────


def test_init_app_idempotent():
    """Calling NanorixMiddleware.init_app twice on the same app is a no-op."""
    app = Flask(__name__)
    fake = FakeClient()
    middleware = NanorixMiddleware(
        client=fake,
        framework="HIPAA",
        capsulefile="cf_demo",
    )
    middleware.init_app(app)
    # Second call should not re-register or raise.
    middleware.init_app(app)


def test_deferred_init_via_init_app():
    """Pattern: instantiate middleware without app, then init_app later."""
    fake = FakeClient()
    middleware = NanorixMiddleware(
        client=fake,
        framework="HIPAA",
        capsulefile="cf_demo",
    )
    app = Flask(__name__)

    @app.route("/decide", methods=["GET"])
    def decide() -> Dict[str, str]:
        return {"decision": "approved"}

    middleware.init_app(app)
    client = app.test_client()
    client.get("/decide")

    assert len(fake.capsules.created) == 1
    assert fake.capsules.created[0].create_cdp_kind == "request"


# ── on_audit_proof callback ─────────────────────────────────────────


def test_on_audit_proof_callback_invoked_per_request():
    fake = FakeClient()
    received: List[Any] = []
    app = _build_app(fake, on_audit_proof=received.append)
    client = app.test_client()

    client.get("/decide")
    client.get("/decide")

    assert len(received) == 2


def test_on_audit_proof_callback_failure_does_not_break_teardown():
    fake = FakeClient()

    def boom(cdp: Any) -> None:
        raise RuntimeError("callback exploded")

    app = _build_app(fake, on_audit_proof=boom)
    client = app.test_client()

    resp = client.get("/decide")
    assert resp.status_code == 200


# ── Status-to-failure-kind taxonomy ─────────────────────────────────


def test_map_status_408_timeout():
    assert _map_status_to_failure_kind(408) == "timeout"


def test_map_status_401_403_permission_denied():
    assert _map_status_to_failure_kind(401) == "permission_denied"
    assert _map_status_to_failure_kind(403) == "permission_denied"


def test_map_status_400_422_invalid_args():
    assert _map_status_to_failure_kind(400) == "invalid_args"
    assert _map_status_to_failure_kind(422) == "invalid_args"


def test_map_status_499_cancelled():
    assert _map_status_to_failure_kind(499) == "cancelled"


def test_map_status_5xx_upstream_failure():
    for status in (500, 502, 503, 504):
        assert _map_status_to_failure_kind(status) == "upstream_failure"


# ── Exception classifier ────────────────────────────────────────────


def test_classify_exception_timeout():
    assert _classify_exception(TimeoutError("slow")) == "timeout"


def test_classify_exception_permission_error():
    assert _classify_exception(PermissionError("denied")) == "permission_denied"


def test_classify_exception_value_error():
    assert _classify_exception(ValueError("bad")) == "invalid_args"


def test_classify_exception_default_upstream_failure():
    class CustomError(Exception):
        pass

    assert _classify_exception(CustomError("???")) == "upstream_failure"


# ── Path-resolution helper parity with FastAPI middleware ──────────


def test_resolve_framework_default_when_no_override():
    assert (
        _resolve_framework_for_path(
            path="/any",
            default_framework="HIPAA",
            framework_per_route=None,
        )
        == "HIPAA"
    )


def test_resolve_framework_longest_prefix():
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


# ── Canonical-hash parity with FastAPI middleware ──────────────────


def test_canonical_sha512_hex_deterministic():
    h1 = _canonical_sha512_hex({"a": 1, "b": 2})
    h2 = _canonical_sha512_hex({"b": 2, "a": 1})
    assert h1 == h2


def test_canonical_sha512_hex_128_chars():
    h = _canonical_sha512_hex({"x": "y"})
    assert len(h) == 128
    assert all(c in "0123456789abcdef" for c in h)
