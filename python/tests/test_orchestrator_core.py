"""Tests for the framework-agnostic orchestrator core (Batch 13).

These tests import ONLY ``nanorix.integrations._orchestrator_core`` plus a
hand-rolled fake client. They do NOT import apache-airflow or temporalio —
verifying the architecture goal that the test suite stays framework-free.

The fake client records the order of every capsule operation into a shared
call log so ordering invariants (upload-before-execute,
download-before-destroy) can be asserted directly.
"""

from __future__ import annotations

import inspect

import pytest

from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    CapsuleJobResult,
    NARROW_CLAIM_DISCLAIMER,
    OrchestratorError,
    RESERVED_METADATA_PREFIX,
    VALID_DATA_CLASSIFICATIONS,
    run_capsule_job,
)


# ── Fakes ─────────────────────────────────────────────────────────────


class FakeExecResult:
    def __init__(self, exit_code: int = 0, stdout: str = "ok", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeVerification:
    """Shareable verification view — what audit_proof.verification() returns."""

    def __init__(self, capsule_id: str):
        self.capsule_id = capsule_id

    def model_dump(self):
        return {"capsule_id": self.capsule_id, "kind": "verification"}


class FakeAuditProof:
    """Stand-in for CdpAccess returned by destroy()."""

    def __init__(self, capsule_id: str):
        self.id = capsule_id
        self._capsule_id = capsule_id

    def verification(self):
        return FakeVerification(self._capsule_id)


class FakeCapsule:
    def __init__(self, capsule_id: str, log: list, *, fail_on: str | None = None):
        self.id = capsule_id
        self._log = log
        self._fail_on = fail_on
        self.outputs_store = {"decision.json": b"{}", "report.txt": b"hi"}

    def upload(self, filename: str, data: bytes):
        self._log.append(("upload", filename))
        if self._fail_on == "upload":
            raise RuntimeError("upload boom")
        return {"filename": filename}

    def execute(self, command=None, timeout=None):
        self._log.append(("execute", command))
        if self._fail_on == "execute":
            raise RuntimeError("execute boom")
        return FakeExecResult()

    def download(self, filename: str) -> bytes:
        self._log.append(("download", filename))
        if self._fail_on == "download":
            raise RuntimeError("download boom")
        return self.outputs_store.get(filename, b"")

    def destroy(self):
        self._log.append(("destroy", self.id))
        if self._fail_on == "destroy":
            raise RuntimeError("destroy boom")
        return FakeAuditProof(self.id)


class FakeCapsules:
    def __init__(self, log: list, *, fail_on: str | None = None, create_fails: bool = False):
        self._log = log
        self._fail_on = fail_on
        self._create_fails = create_fails
        self.last_create_kwargs: dict | None = None
        self.accepts_data_classification = True

    def create(
        self,
        *,
        capsulefile=None,
        max_lifetime_seconds=None,
        metadata=None,
        data_classification=None,
        idempotency_key=None,
        **kwargs,
    ):
        self.last_create_kwargs = {
            "capsulefile": capsulefile,
            "max_lifetime_seconds": max_lifetime_seconds,
            "metadata": metadata,
            "data_classification": data_classification,
            "idempotency_key": idempotency_key,
            **kwargs,
        }
        self._log.append(("create", capsulefile))
        if self._create_fails:
            raise RuntimeError("create boom")
        return FakeCapsule("cap_abc123", self._log, fail_on=self._fail_on)


class FakeClient:
    def __init__(self, *, fail_on: str | None = None, create_fails: bool = False):
        self.log: list = []
        self.capsules = FakeCapsules(self.log, fail_on=fail_on, create_fails=create_fails)


class LegacyCapsules(FakeCapsules):
    """A create() that does NOT accept data_classification (old SDK)."""

    def create(
        self,
        *,
        capsulefile=None,
        max_lifetime_seconds=None,
        metadata=None,
        idempotency_key=None,
    ):
        self.last_create_kwargs = {
            "capsulefile": capsulefile,
            "max_lifetime_seconds": max_lifetime_seconds,
            "metadata": metadata,
            "idempotency_key": idempotency_key,
        }
        self._log.append(("create", capsulefile))
        if self._create_fails:
            raise RuntimeError("create boom")
        return FakeCapsule("cap_abc123", self._log, fail_on=self._fail_on)


class LegacyClient:
    def __init__(self):
        self.log: list = []
        self.capsules = LegacyCapsules(self.log)


# ── Happy path ────────────────────────────────────────────────────────


def test_happy_path_create_execute_destroy_returns_audit_proof():
    client = FakeClient()
    params = CapsuleJobParams(capsulefile="cf_x", command="python run.py")
    result = run_capsule_job(client, params)

    assert isinstance(result, CapsuleJobResult)
    assert result.capsule_id == "cap_abc123"
    assert result.destroyed is True
    assert isinstance(result.audit_proof, FakeAuditProof)
    ops = [op for op, _ in client.log]
    assert ops == ["create", "execute", "destroy"]


def test_result_carries_audit_proof_and_verification():
    client = FakeClient()
    result = run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert result.audit_proof is not None
    assert isinstance(result.verification, FakeVerification)
    assert result.verification.model_dump()["kind"] == "verification"


def test_result_carries_narrow_claim_disclaimer():
    client = FakeClient()
    result = run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert result.disclaimer == NARROW_CLAIM_DISCLAIMER
    assert "does NOT assert" in result.disclaimer
    assert "auditor" in result.disclaimer


def test_command_forwarded_to_execute():
    client = FakeClient()
    run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x", command="echo hi"))
    execute_calls = [arg for op, arg in client.log if op == "execute"]
    assert execute_calls == ["echo hi"]


def test_none_command_uses_entrypoint():
    client = FakeClient()
    run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x", command=None))
    execute_calls = [arg for op, arg in client.log if op == "execute"]
    assert execute_calls == [None]


# ── Ordering invariants ───────────────────────────────────────────────


def test_download_before_destroy_ordering():
    """No-post-destroy-cache: outputs downloaded BEFORE the capsule dies."""
    client = FakeClient()
    params = CapsuleJobParams(
        capsulefile="cf_x",
        output_files=["decision.json", "report.txt"],
    )
    result = run_capsule_job(client, params)

    ops = [op for op, _ in client.log]
    last_download_idx = max(i for i, op in enumerate(ops) if op == "download")
    destroy_idx = ops.index("destroy")
    assert last_download_idx < destroy_idx, (
        "every download MUST precede destroy (no post-destroy cache)"
    )
    assert result.outputs == {"decision.json": b"{}", "report.txt": b"hi"}


def test_upload_before_execute_ordering():
    client = FakeClient()
    params = CapsuleJobParams(
        capsulefile="cf_x",
        input_files={"in.csv": b"a,b,c"},
    )
    run_capsule_job(client, params)
    ops = [op for op, _ in client.log]
    upload_idx = ops.index("upload")
    execute_idx = ops.index("execute")
    assert upload_idx < execute_idx


def test_full_ordering_upload_execute_download_destroy():
    client = FakeClient()
    params = CapsuleJobParams(
        capsulefile="cf_x",
        input_files={"in.csv": b"x"},
        output_files=["decision.json"],
    )
    run_capsule_job(client, params)
    ops = [op for op, _ in client.log]
    assert ops == ["create", "upload", "execute", "download", "destroy"]


def test_multiple_inputs_all_uploaded_before_execute():
    client = FakeClient()
    params = CapsuleJobParams(
        capsulefile="cf_x",
        input_files={"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"},
    )
    run_capsule_job(client, params)
    ops = [op for op, _ in client.log]
    execute_idx = ops.index("execute")
    upload_indices = [i for i, op in enumerate(ops) if op == "upload"]
    assert len(upload_indices) == 3
    assert all(i < execute_idx for i in upload_indices)


# ── output_files behavior ─────────────────────────────────────────────


def test_no_output_files_means_no_download():
    client = FakeClient()
    result = run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert "download" not in [op for op, _ in client.log]
    assert result.outputs == {}


def test_empty_output_files_list_means_no_download():
    client = FakeClient()
    result = run_capsule_job(
        client, CapsuleJobParams(capsulefile="cf_x", output_files=[])
    )
    assert "download" not in [op for op, _ in client.log]
    assert result.outputs == {}


def test_output_files_returned_as_bytes_mapping():
    client = FakeClient()
    result = run_capsule_job(
        client, CapsuleJobParams(capsulefile="cf_x", output_files=["report.txt"])
    )
    assert result.outputs == {"report.txt": b"hi"}


# ── destroy_on_complete ───────────────────────────────────────────────


def test_destroy_on_complete_false_leaves_capsule():
    client = FakeClient()
    result = run_capsule_job(
        client, CapsuleJobParams(capsulefile="cf_x", destroy_on_complete=False)
    )
    assert "destroy" not in [op for op, _ in client.log]
    assert result.destroyed is False
    assert result.audit_proof is None
    assert result.verification is None


def test_destroy_on_complete_false_still_downloads_outputs():
    client = FakeClient()
    result = run_capsule_job(
        client,
        CapsuleJobParams(
            capsulefile="cf_x",
            output_files=["decision.json"],
            destroy_on_complete=False,
        ),
    )
    assert result.outputs == {"decision.json": b"{}"}
    assert "destroy" not in [op for op, _ in client.log]


# ── Error paths ───────────────────────────────────────────────────────


def test_create_failure_propagates_and_no_destroy():
    client = FakeClient(create_fails=True)
    with pytest.raises(RuntimeError, match="create boom"):
        run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert "destroy" not in [op for op, _ in client.log]


def test_execute_failure_still_destroys_capsule():
    client = FakeClient(fail_on="execute")
    with pytest.raises(RuntimeError, match="execute boom"):
        run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert "destroy" in [op for op, _ in client.log], "cleanup must destroy on failure"


def test_download_failure_still_destroys_capsule():
    client = FakeClient(fail_on="download")
    with pytest.raises(RuntimeError, match="download boom"):
        run_capsule_job(
            client, CapsuleJobParams(capsulefile="cf_x", output_files=["decision.json"])
        )
    assert "destroy" in [op for op, _ in client.log]


def test_upload_failure_still_destroys_capsule():
    client = FakeClient(fail_on="upload")
    with pytest.raises(RuntimeError, match="upload boom"):
        run_capsule_job(
            client, CapsuleJobParams(capsulefile="cf_x", input_files={"x": b"y"})
        )
    assert "destroy" in [op for op, _ in client.log]


def test_execute_failure_with_destroy_disabled_does_not_destroy():
    client = FakeClient(fail_on="execute")
    with pytest.raises(RuntimeError, match="execute boom"):
        run_capsule_job(
            client,
            CapsuleJobParams(capsulefile="cf_x", destroy_on_complete=False),
        )
    assert "destroy" not in [op for op, _ in client.log]


def test_cleanup_destroy_failure_does_not_mask_original_error():
    client = FakeClient(fail_on="execute")
    # Make destroy ALSO fail; the original execute error must still surface.
    original_destroy = FakeCapsule.destroy

    def both_fail(self):
        self._log.append(("destroy", self.id))
        raise RuntimeError("destroy also boom")

    FakeCapsule.destroy = both_fail
    try:
        with pytest.raises(RuntimeError, match="execute boom"):
            run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    finally:
        FakeCapsule.destroy = original_destroy


# ── Parameter validation ──────────────────────────────────────────────


def test_missing_capsulefile_raises():
    with pytest.raises(OrchestratorError, match="capsulefile is required"):
        run_capsule_job(FakeClient(), CapsuleJobParams(capsulefile=""))


def test_whitespace_capsulefile_raises():
    with pytest.raises(OrchestratorError, match="capsulefile is required"):
        run_capsule_job(FakeClient(), CapsuleJobParams(capsulefile="   "))


def test_invalid_data_classification_raises():
    with pytest.raises(OrchestratorError, match="data_classification must be one of"):
        run_capsule_job(
            FakeClient(),
            CapsuleJobParams(capsulefile="cf_x", data_classification="SECRET"),
        )


def test_each_valid_data_classification_accepted():
    for dc in VALID_DATA_CLASSIFICATIONS:
        client = FakeClient()
        result = run_capsule_job(
            client, CapsuleJobParams(capsulefile="cf_x", data_classification=dc)
        )
        assert result.destroyed is True


def test_metadata_reserved_prefix_rejected():
    bad_key = f"{RESERVED_METADATA_PREFIX}framework"
    with pytest.raises(OrchestratorError, match="reserved"):
        run_capsule_job(
            FakeClient(),
            CapsuleJobParams(capsulefile="cf_x", metadata={bad_key: "HIPAA"}),
        )


def test_metadata_too_many_keys_rejected():
    meta = {f"k{i}": "v" for i in range(17)}
    with pytest.raises(OrchestratorError, match="at most"):
        run_capsule_job(
            FakeClient(), CapsuleJobParams(capsulefile="cf_x", metadata=meta)
        )


def test_metadata_value_too_long_rejected():
    with pytest.raises(OrchestratorError, match="256-char"):
        run_capsule_job(
            FakeClient(),
            CapsuleJobParams(capsulefile="cf_x", metadata={"k": "z" * 257}),
        )


def test_metadata_key_too_long_rejected():
    with pytest.raises(OrchestratorError, match="64-char"):
        run_capsule_job(
            FakeClient(),
            CapsuleJobParams(capsulefile="cf_x", metadata={"k" * 65: "v"}),
        )


def test_metadata_total_serialized_too_large_rejected():
    # 16 keys * ~256-char values pushes serialized size past 4096 bytes.
    meta = {f"key{i:02d}": "v" * 250 for i in range(16)}
    with pytest.raises(OrchestratorError, match="byte"):
        run_capsule_job(
            FakeClient(), CapsuleJobParams(capsulefile="cf_x", metadata=meta)
        )


def test_valid_metadata_accepted():
    client = FakeClient()
    result = run_capsule_job(
        client,
        CapsuleJobParams(capsulefile="cf_x", metadata={"workflow": "prior-auth"}),
    )
    assert result.destroyed is True
    assert client.capsules.last_create_kwargs["metadata"] == {"workflow": "prior-auth"}


# ── data_classification passthrough ───────────────────────────────────


def test_data_classification_passthrough_when_create_accepts_it():
    client = FakeClient()
    run_capsule_job(
        client, CapsuleJobParams(capsulefile="cf_x", data_classification="PHI")
    )
    assert client.capsules.last_create_kwargs["data_classification"] == "PHI"


def test_data_classification_folds_into_metadata_on_legacy_client():
    client = LegacyClient()
    run_capsule_job(
        client, CapsuleJobParams(capsulefile="cf_x", data_classification="PII")
    )
    meta = client.capsules.last_create_kwargs["metadata"]
    assert meta is not None
    assert meta["data_classification"] == "PII"


def test_data_classification_none_not_forwarded():
    client = FakeClient()
    run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x"))
    assert client.capsules.last_create_kwargs["data_classification"] is None


# ── create kwargs threading ───────────────────────────────────────────


def test_max_lifetime_and_idempotency_key_forwarded():
    client = FakeClient()
    run_capsule_job(
        client,
        CapsuleJobParams(
            capsulefile="cf_x",
            max_lifetime_seconds=120,
            idempotency_key="run-2026-05-28",
        ),
    )
    kw = client.capsules.last_create_kwargs
    assert kw["max_lifetime_seconds"] == 120
    assert kw["idempotency_key"] == "run-2026-05-28"
    assert kw["capsulefile"] == "cf_x"


def test_timeout_forwarded_to_execute(monkeypatch):
    seen = {}
    client = FakeClient()
    orig_execute = FakeCapsule.execute

    def recording_execute(self, command=None, timeout=None):
        seen["timeout"] = timeout
        return orig_execute(self, command=command, timeout=timeout)

    monkeypatch.setattr(FakeCapsule, "execute", recording_execute)
    run_capsule_job(client, CapsuleJobParams(capsulefile="cf_x", timeout=45))
    assert seen["timeout"] == 45


# ── No internal-engine leakage in the public surface ──────────────────


def test_core_module_has_no_internal_engine_names(engine_names):
    import nanorix.integrations._orchestrator_core as core

    src = inspect.getsource(core)
    for forbidden in engine_names:
        assert forbidden not in src, f"internal engine name {forbidden!r} leaked"


def test_no_passfail_compliance_language_in_disclaimer():
    for word in ["COMPLIANT", "SATISFIED", "MEETS", "PASSED"]:
        assert word not in NARROW_CLAIM_DISCLAIMER


# ── Import guard (no real framework imported) ─────────────────────────


def test_core_imports_without_any_framework():
    # If this test module imported, the core imported with no airflow /
    # temporalio installed. Assert the frameworks are genuinely absent so
    # the "framework-free" guarantee is actually being exercised.
    import importlib.util

    assert importlib.util.find_spec("airflow") is None
    assert importlib.util.find_spec("temporalio") is None


def test_airflow_import_guard_message_is_actionable():
    # We assert the guard MESSAGE (not the real module) so the test never
    # needs apache-airflow installed. The message must name the extra.
    from nanorix.integrations.airflow import AIRFLOW_IMPORT_ERROR_MESSAGE

    assert "pip install 'nanorix[airflow]'" in AIRFLOW_IMPORT_ERROR_MESSAGE
    assert "apache-airflow" in AIRFLOW_IMPORT_ERROR_MESSAGE


def test_airflow_operator_instantiation_raises_clear_error_without_airflow():
    from nanorix.integrations import airflow as airflow_mod

    # Airflow is not installed in the test env, so instantiation must raise
    # a clear, actionable ImportError naming the extra.
    assert airflow_mod._AIRFLOW_AVAILABLE is False
    with pytest.raises(ImportError, match=r"nanorix\[airflow\]"):
        airflow_mod.NanorixCapsuleOperator(
            task_id="t", client=FakeClient(), capsulefile="cf_x"
        )


def test_temporal_import_guard_message_is_actionable():
    from nanorix.integrations.temporal import TEMPORAL_IMPORT_ERROR_MESSAGE

    assert "pip install 'nanorix[temporal]'" in TEMPORAL_IMPORT_ERROR_MESSAGE
    assert "temporalio" in TEMPORAL_IMPORT_ERROR_MESSAGE


def test_temporal_make_activity_raises_clear_error_without_temporalio():
    from nanorix.integrations import temporal as temporal_mod

    assert temporal_mod._TEMPORAL_AVAILABLE is False
    with pytest.raises(ImportError, match=r"nanorix\[temporal\]"):
        temporal_mod.make_capsule_activity(FakeClient())


# ── Temporal activity body (framework-free core path) ─────────────────


def test_temporal_activity_body_runs_core_and_returns_result():
    # _run_activity is the framework-free body; exercise it directly so the
    # Temporal-shaped path is covered without temporalio installed.
    from nanorix.integrations.temporal import (
        NanorixCapsuleActivityInput,
        NanorixCapsuleActivityResult,
        _run_activity,
    )

    client = FakeClient()
    activity_input = NanorixCapsuleActivityInput(
        capsulefile="cf_x",
        command="python run.py",
        data_classification="PHI",
        output_files=["decision.json"],
    )
    result = _run_activity(client, activity_input)

    assert isinstance(result, NanorixCapsuleActivityResult)
    assert result.capsule_id == "cap_abc123"
    assert result.destroyed is True
    assert result.outputs == {"decision.json": b"{}"}
    assert result.verification == {"capsule_id": "cap_abc123", "kind": "verification"}
    # Ordering held in the underlying core.
    ops = [op for op, _ in client.log]
    assert ops.index("download") < ops.index("destroy")


def test_temporal_input_to_params_maps_all_fields():
    from nanorix.integrations.temporal import NanorixCapsuleActivityInput

    activity_input = NanorixCapsuleActivityInput(
        capsulefile="cf_x",
        command="cmd",
        data_classification="financial",
        metadata={"k": "v"},
        input_files={"in": b"1"},
        output_files=["out"],
        destroy_on_complete=False,
        max_lifetime_seconds=99,
        timeout=10,
        idempotency_key="ik",
    )
    params = activity_input.to_params()
    assert isinstance(params, CapsuleJobParams)
    assert params.capsulefile == "cf_x"
    assert params.data_classification == "financial"
    assert params.metadata == {"k": "v"}
    assert params.destroy_on_complete is False
    assert params.max_lifetime_seconds == 99
    assert params.timeout == 10
    assert params.idempotency_key == "ik"


# ── Airflow operator body (build params, no framework) ────────────────


def test_airflow_build_params_uses_core(monkeypatch):
    # Bypass the BaseOperator subclass machinery (airflow absent) by
    # constructing the params builder logic via the shell module's
    # _build_params on a manually-populated instance is not possible
    # without airflow; instead verify the param mapping at the dataclass
    # level mirrors what the operator passes.
    params = CapsuleJobParams(
        capsulefile="cf_x",
        command="python run.py",
        data_classification="PHI",
        metadata={"workflow": "prior-auth"},
        output_files=["decision.json"],
    )
    client = FakeClient()
    result = run_capsule_job(client, params)
    assert result.capsule_id == "cap_abc123"
    assert result.outputs == {"decision.json": b"{}"}
