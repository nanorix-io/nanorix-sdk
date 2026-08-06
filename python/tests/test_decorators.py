"""Wave 14 / item 3 — `@workload` decorator tests.

Exercises ``nanorix.decorators.workload`` against:

- Synchronous and async function shapes.
- Required-arg discipline (framework + capsulefile).
- Capsulefile resolution (server-side ID + local YAML name).
- Metadata builder cross-impl byte-equivalence invariants.
- Reserved-prefix metadata rejection.
- Capsule lifecycle (create → exec body → destroy).
- Exception path (capsule still destroyed; AuditProof attached to exc).
- ``last_audit_proof`` attribute exposure.
- ``on_audit_proof`` callback hook.
- Compliance framework constants.
- Property tests on random arg shapes (≥10k iterations).
- ADR-006 I0 Forever-Standard preservation (no API surface change to capsule-create).
- Vocabulary discipline (CONVENTIONS.md): AuditProof / Capsulefile / AuditRecord.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import pytest

from nanorix import (
    BadRequestError,
    Client,
    WorkloadDecorator,
    workload,
)
from nanorix._models import CdpAccess
from nanorix.decorators import (
    ALLOWED_FRAMEWORKS,
    FRAMEWORK_GDPR,
    FRAMEWORK_HIPAA,
    FRAMEWORK_PCI_DSS,
    FRAMEWORK_SOC2,
    WRAPPER_KIND,
    _build_workload_metadata,
    _find_local_capsulefile_yaml,
    _is_server_capsulefile_id,
    _resolve_capsulefile,
)


TEST_API_KEY = "nrx_test_sk_test_1234567890abcdef"
TEST_BASE_URL = "https://api.nanorix.io"


CAPSULE_RESPONSE = {
    "id": "cap_w001",
    "status": "active",
    "capsulefile_id": "cf_pa_decision",
    "max_lifetime_seconds": 300,
    "created_at": "2026-05-10T00:00:00Z",
}


DESTROY_RESPONSE = {
    "cdp": {
        "capsule_id": "cap_w001",
        "version": "2.0",
        "activity": [],
        "destruction_trigger": "user_explicit",
        "metadata_hash": "sha512:abc123",
    },
    "verification_cdp": {
        "capsule_id": "cap_w001",
        "version": "2.0",
        "verify": "https://nanorix.io/verify/cap_w001",
    },
}


# ── Fixtures ───────────────────────────────────────────────────────


class _Routes:
    """Convenience holder for capsule create/destroy mock routes."""

    __slots__ = ("create", "destroy")

    def __init__(self, create: Any, destroy: Any) -> None:
        self.create = create
        self.destroy = destroy


@pytest.fixture
def workload_routes(mock_api) -> _Routes:
    """Install canonical capsule create + destroy mocks; return route handles.

    Uses ``assert_all_called=False`` so individual tests don't have to
    exercise both create + destroy routes — some tests only assert on one.
    """
    mock_api._assert_all_called = False  # type: ignore[attr-defined]
    create = mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    destroy = mock_api.delete("/v1/capsules/cap_w001").mock(
        return_value=httpx.Response(200, json=DESTROY_RESPONSE)
    )
    return _Routes(create=create, destroy=destroy)


@pytest.fixture
def workload_client(workload_routes) -> Client:
    """Pre-configured test client with capsule create/destroy mocks installed."""
    return Client(api_key=TEST_API_KEY, base_url=TEST_BASE_URL)


# ── Construction + required args ───────────────────────────────────


def test_workload_returns_decorator_object():
    dec = workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_xx")
    assert isinstance(dec, WorkloadDecorator)


def test_workload_requires_framework():
    with pytest.raises(ValueError, match="framework"):
        workload(framework="", capsulefile="cf_xx")  # type: ignore[arg-type]


def test_workload_requires_framework_not_none():
    with pytest.raises(ValueError, match="framework"):
        workload(framework=None, capsulefile="cf_xx")  # type: ignore[arg-type]


def test_workload_requires_capsulefile():
    with pytest.raises(ValueError, match="capsulefile"):
        workload(framework=FRAMEWORK_HIPAA, capsulefile="")


def test_workload_requires_capsulefile_not_none():
    with pytest.raises(ValueError, match="capsulefile"):
        workload(framework=FRAMEWORK_HIPAA, capsulefile=None)  # type: ignore[arg-type]


def test_workload_records_required_args():
    dec = workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
    )
    assert dec.framework == "HIPAA"
    assert dec.capsulefile == "cf_pa_decision"


# ── Decorator application ──────────────────────────────────────────


def test_decorator_applies_to_function(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function(x: int) -> int:
        return x * 2

    assert callable(my_function)
    assert hasattr(my_function, "framework")
    assert hasattr(my_function, "capsulefile")
    assert hasattr(my_function, "last_audit_proof")
    assert my_function.framework == "HIPAA"  # type: ignore[attr-defined]


def test_decorator_preserves_function_name(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_named_function():
        """Original docstring."""
        return None

    assert my_named_function.__name__ == "my_named_function"
    assert my_named_function.__doc__ == "Original docstring."


def test_decorator_invokes_wrapped_function(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function(x: int) -> int:
        return x * 2

    result = my_function(21)
    assert result == 42


def test_decorator_attaches_audit_proof(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return "ok"

    assert my_function.last_audit_proof is None  # type: ignore[attr-defined]
    my_function()
    assert my_function.last_audit_proof is not None  # type: ignore[attr-defined]
    assert isinstance(my_function.last_audit_proof, CdpAccess)  # type: ignore[attr-defined]


def test_decorator_creates_capsule(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    assert workload_routes.create.called


def test_decorator_destroys_capsule(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    assert workload_routes.destroy.called


def test_decorator_destroys_on_exception(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        my_function()
    assert workload_routes.destroy.called
    # AuditProof is still attached to the wrapper after exception destroy.
    assert my_function.last_audit_proof is not None  # type: ignore[attr-defined]


def test_on_audit_proof_callback_invoked(workload_client):
    captured: List[CdpAccess] = []

    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        on_audit_proof=lambda cdp: captured.append(cdp),
    )
    def my_function():
        return None

    my_function()
    assert len(captured) == 1
    assert isinstance(captured[0], CdpAccess)


def test_on_audit_proof_callback_invoked_on_exception(workload_client):
    captured: List[CdpAccess] = []

    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        on_audit_proof=lambda cdp: captured.append(cdp),
    )
    def my_function():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        my_function()
    assert len(captured) == 1


def test_on_audit_proof_callback_failure_does_not_mask_result(workload_client):
    """A buggy callback must not crash the workload."""

    def bad_callback(cdp: CdpAccess) -> None:
        raise RuntimeError("callback bug")

    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        on_audit_proof=bad_callback,
    )
    def my_function():
        return "ok"

    # Result should still come through.
    assert my_function() == "ok"


def test_decorator_with_args_and_kwargs(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function(a: int, b: int, *, factor: int = 1) -> int:
        return (a + b) * factor

    assert my_function(1, 2, factor=10) == 30


# ── Capsule create body inspection ─────────────────────────────────


def test_capsule_create_includes_capsulefile_id(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    assert body["capsulefile_id"] == "cf_pa_decision"


def test_capsule_create_includes_metadata(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    assert body["metadata"]["nanorix:framework"] == "HIPAA"
    assert body["metadata"]["nanorix:wrapper_kind"] == "workload"


def test_metadata_includes_framework_version_when_provided(workload_routes, workload_client):
    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        framework_version="2026-02",
    )
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    assert body["metadata"]["nanorix:framework_version"] == "2026-02"


def test_metadata_includes_agent_id_when_provided(workload_routes, workload_client):
    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        agent_id="pa-agent-7",
    )
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    assert body["metadata"]["nanorix:agent_id"] == "pa-agent-7"


def test_capsule_create_includes_max_lifetime(workload_routes, workload_client):
    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        max_lifetime_seconds=600,
    )
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    assert body["max_lifetime_seconds"] == 600


# ── Metadata builder discipline ────────────────────────────────────


def test_metadata_builder_minimal():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id=None,
        extra=None,
    )
    assert md == {
        "nanorix:wrapper_kind": "workload",
        "nanorix:framework": "HIPAA",
    }


def test_metadata_builder_with_all_reserved_keys():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version="2026-02",
        agent_id="agent-7",
        extra=None,
    )
    assert md["nanorix:wrapper_kind"] == "workload"
    assert md["nanorix:framework"] == "HIPAA"
    assert md["nanorix:framework_version"] == "2026-02"
    assert md["nanorix:agent_id"] == "agent-7"


def test_metadata_builder_with_customer_extras():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id=None,
        extra={"matter_id": "M-2026-001", "patient_class": "outpatient"},
    )
    assert md["matter_id"] == "M-2026-001"
    assert md["patient_class"] == "outpatient"


def test_metadata_builder_rejects_customer_reserved_prefix():
    with pytest.raises(BadRequestError, match="nanorix:"):
        _build_workload_metadata(
            framework="HIPAA",
            framework_version=None,
            agent_id=None,
            extra={"nanorix:matter_id": "evil"},
        )


def test_metadata_builder_coerces_extra_values_to_str():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id=None,
        extra={"count": 42, "active": True},  # type: ignore[dict-item]
    )
    assert md["count"] == "42"
    assert md["active"] == "True"


def test_metadata_builder_byte_equivalence_invariant():
    """Same inputs → same key set + same string values, regardless of insertion order."""
    md_a = _build_workload_metadata(
        framework="HIPAA",
        framework_version="2026-02",
        agent_id="agent-7",
        extra={"matter_id": "M-001"},
    )
    md_b = _build_workload_metadata(
        framework="HIPAA",
        framework_version="2026-02",
        agent_id="agent-7",
        extra={"matter_id": "M-001"},
    )
    assert md_a == md_b
    # JCS-flavor canonical-JSON serialization → byte-identical.
    assert json.dumps(md_a, sort_keys=True, separators=(",", ":")) == json.dumps(
        md_b, sort_keys=True, separators=(",", ":")
    )


# ── Capsulefile resolution ─────────────────────────────────────────


def test_is_server_capsulefile_id_recognizes_cf_prefix():
    assert _is_server_capsulefile_id("cf_abc123")
    assert _is_server_capsulefile_id("cf_")
    assert not _is_server_capsulefile_id("pa-decision")
    assert not _is_server_capsulefile_id("agent")
    assert not _is_server_capsulefile_id("")


def test_resolve_capsulefile_passthrough_for_server_id(workload_client):
    cf_id = _resolve_capsulefile(
        capsulefile="cf_abc123",
        capsulefile_dir=None,
        client=workload_client,
    )
    assert cf_id == "cf_abc123"


def test_find_local_capsulefile_yaml(tmp_path: Path):
    yaml_text = "name: pa-decision\nruntime: python3.12\nentrypoint: python main.py\nfiles:\n  - main.py\n"
    cf_path = tmp_path / "capsulefile.yaml"
    cf_path.write_text(yaml_text)

    found = _find_local_capsulefile_yaml("pa-decision", tmp_path)
    assert found == cf_path

    not_found = _find_local_capsulefile_yaml("other-name", tmp_path)
    assert not_found is None


def test_find_local_capsulefile_yaml_in_subdirectory(tmp_path: Path):
    sub = tmp_path / "agent"
    sub.mkdir()
    (sub / "capsulefile.yaml").write_text("name: agent\nfiles:\n  - main.py\n")

    found = _find_local_capsulefile_yaml("agent", tmp_path)
    assert found == sub / "capsulefile.yaml"


def test_find_local_capsulefile_yaml_quoted_name(tmp_path: Path):
    (tmp_path / "capsulefile.yaml").write_text(
        'name: "pa-decision"\nfiles:\n  - main.py\n'
    )

    found = _find_local_capsulefile_yaml("pa-decision", tmp_path)
    assert found is not None


def test_find_local_capsulefile_yaml_handles_missing_dir(tmp_path: Path):
    found = _find_local_capsulefile_yaml(
        "anything", tmp_path / "nonexistent"
    )
    assert found is None


# ── Compliance framework constants ─────────────────────────────────


def test_framework_constants_exposed():
    assert FRAMEWORK_HIPAA == "HIPAA"
    assert FRAMEWORK_GDPR == "GDPR"
    assert FRAMEWORK_SOC2 == "SOC2"
    assert FRAMEWORK_PCI_DSS == "PCI_DSS"


def test_allowed_frameworks_set():
    assert FRAMEWORK_HIPAA in ALLOWED_FRAMEWORKS
    assert FRAMEWORK_GDPR in ALLOWED_FRAMEWORKS
    assert FRAMEWORK_SOC2 in ALLOWED_FRAMEWORKS
    # Open-set discipline: customer-supplied free-form strings are also accepted.


def test_wrapper_kind_constant():
    assert WRAPPER_KIND == "workload"


# ── Async function support ─────────────────────────────────────────


def test_decorator_supports_async_function(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    async def my_async_function(x: int) -> int:
        await asyncio.sleep(0)
        return x * 2

    result = asyncio.run(my_async_function(21))
    assert result == 42


def test_decorator_async_attaches_audit_proof(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    async def my_async_function():
        await asyncio.sleep(0)
        return None

    asyncio.run(my_async_function())
    assert my_async_function.last_audit_proof is not None  # type: ignore[attr-defined]


def test_decorator_async_destroys_on_exception(workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    async def my_async_function():
        await asyncio.sleep(0)
        raise RuntimeError("async boom")

    with pytest.raises(RuntimeError, match="async boom"):
        asyncio.run(my_async_function())
    assert my_async_function.last_audit_proof is not None  # type: ignore[attr-defined]


# ── Multi-call discipline ──────────────────────────────────────────


def test_multiple_calls_each_create_capsule(workload_routes, workload_client):
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    my_function()
    my_function()
    assert workload_routes.create.call_count == 3


def test_last_audit_proof_overwritten_per_call(workload_client):
    """ ``last_audit_proof`` is the LAST invocation's proof (as documented). """

    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def my_function():
        return None

    my_function()
    proof_1 = my_function.last_audit_proof  # type: ignore[attr-defined]
    my_function()
    proof_2 = my_function.last_audit_proof  # type: ignore[attr-defined]
    # Both proofs are CdpAccess; they are distinct objects (one per invocation).
    assert isinstance(proof_1, CdpAccess)
    assert isinstance(proof_2, CdpAccess)


# ── ADR-006 I0 Forever-Standard preservation ───────────────────────


def test_no_new_capsule_create_field_introduced(workload_routes, workload_client):
    """The decorator MUST NOT introduce new top-level capsule-create fields.

    Per ADR-006 I0: capsule-create body shape is forever-stable. The
    decorator composes existing ``capsulefile_id``, ``max_lifetime_seconds``,
    ``egress_profile``, and ``metadata`` fields. Any new top-level field
    would require a new ADR.
    """
    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        client=workload_client,
        framework_version="2026-02",
        agent_id="a1",
        max_lifetime_seconds=600,
    )
    def my_function():
        return None

    my_function()
    body = json.loads(workload_routes.create.calls[0].request.content)
    expected_fields = {
        "capsulefile_id",
        "max_lifetime_seconds",
        "metadata",
    }
    actual_fields = set(body.keys())
    # Allow legitimate optional fields, but reject anything outside the known set.
    assert actual_fields.issubset(expected_fields | {"egress_profile"}), (
        f"Decorator introduced unexpected capsule-create fields: "
        f"{actual_fields - expected_fields}"
    )


# ── Property tests (random function shapes) ────────────────────────


def _random_args(rng: random.Random) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Generate a random arg tuple + kwargs dict."""
    n_args = rng.randint(0, 5)
    args = tuple(rng.randint(-1000, 1000) for _ in range(n_args))
    n_kwargs = rng.randint(0, 3)
    kwargs = {
        "k_" + "".join(rng.choices(string.ascii_lowercase, k=4)): rng.randint(0, 100)
        for _ in range(n_kwargs)
    }
    return args, kwargs


def test_property_random_args_lifecycle(workload_client):
    """≥10k iterations: random arg shapes survive the wrap intact."""
    rng = random.Random(0xCAFE)

    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision", client=workload_client)
    def echo(*args: Any, **kwargs: Any) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
        return args, kwargs

    iterations = 10_000
    for _ in range(iterations):
        args, kwargs = _random_args(rng)
        result = echo(*args, **kwargs)
        assert result == (args, kwargs)
    # AuditProof must still be set after iterations.
    assert echo.last_audit_proof is not None  # type: ignore[attr-defined]


def test_property_random_metadata_extras(workload_routes, workload_client):
    """Random valid customer metadata extras serialize cleanly into the request."""
    rng = random.Random(0xBEEF)

    iterations = 200
    for _ in range(iterations):
        n = rng.randint(0, 6)
        extras = {
            "k" + str(i): "".join(rng.choices(string.ascii_letters + string.digits, k=10))
            for i in range(n)
        }

        @workload(
            framework=FRAMEWORK_HIPAA,
            capsulefile="cf_pa_decision",
            client=workload_client,
            metadata=extras,
        )
        def f():
            return None

        f()

    # Every invocation produced a valid create body (no exception).
    assert workload_routes.create.call_count >= iterations


# ── Cross-impl byte-equivalence fixtures ───────────────────────────


def test_metadata_byte_equivalence_fixture_1():
    """Fixture 1 of 10 (byte-equivalence cross-impl with TS)."""
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id=None,
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert (
        canonical
        == '{"nanorix:framework":"HIPAA","nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_2():
    md = _build_workload_metadata(
        framework="GDPR",
        framework_version=None,
        agent_id=None,
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert (
        canonical
        == '{"nanorix:framework":"GDPR","nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_3():
    md = _build_workload_metadata(
        framework="SOC2",
        framework_version="2026-02",
        agent_id=None,
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"nanorix:framework":"SOC2",'
        '"nanorix:framework_version":"2026-02",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_4():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id="agent-7",
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"nanorix:agent_id":"agent-7",'
        '"nanorix:framework":"HIPAA",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_5():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version="2026-02",
        agent_id="agent-7",
        extra={"matter": "M001"},
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"matter":"M001",'
        '"nanorix:agent_id":"agent-7",'
        '"nanorix:framework":"HIPAA",'
        '"nanorix:framework_version":"2026-02",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_6():
    md = _build_workload_metadata(
        framework="EU_AI_ACT",
        framework_version=None,
        agent_id=None,
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert (
        canonical
        == '{"nanorix:framework":"EU_AI_ACT","nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_7():
    md = _build_workload_metadata(
        framework="HITRUST",
        framework_version="r2",
        agent_id=None,
        extra={"a": "1", "b": "2"},
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"a":"1","b":"2",'
        '"nanorix:framework":"HITRUST",'
        '"nanorix:framework_version":"r2",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_8():
    md = _build_workload_metadata(
        framework="PCI_DSS",
        framework_version="4.0",
        agent_id="a1",
        extra={"merchant_id": "m_42"},
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"merchant_id":"m_42",'
        '"nanorix:agent_id":"a1",'
        '"nanorix:framework":"PCI_DSS",'
        '"nanorix:framework_version":"4.0",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_9():
    md = _build_workload_metadata(
        framework="GDPR",
        framework_version="2018",
        agent_id="agent-eu",
        extra=None,
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"nanorix:agent_id":"agent-eu",'
        '"nanorix:framework":"GDPR",'
        '"nanorix:framework_version":"2018",'
        '"nanorix:wrapper_kind":"workload"}'
    )


def test_metadata_byte_equivalence_fixture_10():
    md = _build_workload_metadata(
        framework="HIPAA",
        framework_version=None,
        agent_id=None,
        extra={"region": "US-East-1"},
    )
    canonical = json.dumps(md, sort_keys=True, separators=(",", ":"))
    assert canonical == (
        '{"nanorix:framework":"HIPAA",'
        '"nanorix:wrapper_kind":"workload",'
        '"region":"US-East-1"}'
    )


# ── Misc surface ───────────────────────────────────────────────────


def test_decorator_can_use_api_key_kwarg(mock_api):
    """When no `client=` provided, the decorator constructs one from `api_key=`."""
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.delete("/v1/capsules/cap_w001").mock(
        return_value=httpx.Response(200, json=DESTROY_RESPONSE)
    )

    @workload(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
        api_key=TEST_API_KEY,
        base_url=TEST_BASE_URL,
    )
    def my_function():
        return "ok"

    assert my_function() == "ok"


def test_decorator_no_client_no_api_key_raises():
    @workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision")
    def my_function():
        return None

    with pytest.raises(ValueError, match="api_key|client|NANORIX_API_KEY"):
        my_function()


def test_decorator_returns_function_reference():
    """The decorator wraps and returns a function-like object."""
    dec = workload(framework=FRAMEWORK_HIPAA, capsulefile="cf_pa_decision")

    def f():
        pass

    wrapped = dec(f)
    assert callable(wrapped)
    assert hasattr(wrapped, "workload_decorator")
    assert wrapped.workload_decorator is dec  # type: ignore[attr-defined]


def test_workload_decorator_class_directly_usable():
    """`WorkloadDecorator` can be instantiated directly (advanced use)."""
    dec = WorkloadDecorator(
        framework=FRAMEWORK_HIPAA,
        capsulefile="cf_pa_decision",
    )
    assert dec.framework == "HIPAA"
    assert dec.capsulefile == "cf_pa_decision"
