"""Tests for the Jupyter integration (Batch 16 — FINAL gap-closing batch).

These tests import ONLY the IPython-free core
(``nanorix.integrations._notebook_core``), the IPython-free pieces of the
shell (``NanorixDisplay``, ``load_ipython_extension``, the import-guard
sentinel), the orchestrator core, and a hand-rolled fake client. They do
NOT import ``IPython`` — verifying the architecture goal that the test
suite stays framework-free, exactly like the Batch-13 orchestrator tests.

Coverage:

- ``parse_magic_args`` — valid args, defaults, ``--destroy`` /
  ``--no-destroy``, repeatable ``--output-file`` / ``--meta``, numeric
  flags, missing ``--capsulefile``, unknown flag, missing value, mutually-
  exclusive destroy flags, malformed ``--meta``.
- ``run_capsule_cell`` — reuses ``run_capsule_job`` (mocked client),
  download-before-destroy ordering, data_classification passthrough,
  ``nanorix:*`` reserved-prefix rejection.
- ``render_auditproof_html`` / ``render_verification_html`` — AuditProof
  brand present, NO internal engine names, NO regulatory-adequacy claims,
  PASS + FAIL structural renders.
- XSS — a malicious ``<script>`` / quote in capsule stdout, a filename, and
  a failure message is html-escaped (raw tag absent, escaped form present).
- Import guard — ``load_ipython_extension`` raises a clear actionable error
  without IPython installed.
"""

from __future__ import annotations

import importlib.util

import pytest

from nanorix.integrations._notebook_core import (
    MAGIC_USAGE,
    NARROW_CLAIM_DISCLAIMER,
    MagicArgsError,
    parse_magic_args,
    render_auditproof_html,
    render_cell_result_html,
    render_verification_html,
    run_capsule_cell,
)
from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    CapsuleJobResult,
    OrchestratorError,
)

# Reuse the established Batch-13 fake-client shape (a notebook cell is one
# capsule job, so the same fakes drive both paths).
from tests.test_orchestrator_core import (  # type: ignore[import-not-found]
    FakeAuditProof,
    FakeClient,
    FakeExecResult,
    FakeVerification,
)


# ── Guard: confirm IPython is genuinely absent for this suite ──────────


def test_ipython_is_not_installed_in_test_env() -> None:
    """The whole point: the pure surface is tested with NO IPython present."""
    assert importlib.util.find_spec("IPython") is None


def test_pure_core_imports_without_ipython() -> None:
    """Importing the core module must not pull IPython."""
    import nanorix.integrations._notebook_core as nc  # noqa: WPS433

    assert hasattr(nc, "render_auditproof_html")
    assert hasattr(nc, "parse_magic_args")
    assert hasattr(nc, "run_capsule_cell")


# ── parse_magic_args ───────────────────────────────────────────────────


def test_parse_valid_full_args() -> None:
    params = parse_magic_args(
        "--capsulefile cf_phi --data-classification PHI "
        "--output-file result.json --timeout 60 --max-lifetime 300 "
        "--idempotency-key key-1",
        "python /app/run.py",
    )
    assert isinstance(params, CapsuleJobParams)
    assert params.capsulefile == "cf_phi"
    assert params.data_classification == "PHI"
    assert params.output_files == ["result.json"]
    assert params.command == "python /app/run.py"
    assert params.destroy_on_complete is True  # default
    assert params.timeout == 60
    assert params.max_lifetime_seconds == 300
    assert params.idempotency_key == "key-1"


def test_parse_destroy_flag() -> None:
    params = parse_magic_args("--capsulefile cf_x --destroy", "echo hi")
    assert params.destroy_on_complete is True


def test_parse_no_destroy_flag() -> None:
    params = parse_magic_args("--capsulefile cf_x --no-destroy", "echo hi")
    assert params.destroy_on_complete is False


def test_parse_empty_cell_means_entrypoint() -> None:
    """An empty/whitespace cell body means 'use the Capsulefile entrypoint'."""
    params = parse_magic_args("--capsulefile cf_x", "   \n  ")
    assert params.command is None
    params2 = parse_magic_args("--capsulefile cf_x", None)
    assert params2.command is None


def test_parse_repeatable_output_files_and_meta() -> None:
    params = parse_magic_args(
        "--capsulefile cf_x --output-file a.json --output-file b.csv "
        "--meta workflow=prior-auth --meta payer=acme",
        "run",
    )
    assert params.output_files == ["a.json", "b.csv"]
    assert params.metadata == {"workflow": "prior-auth", "payer": "acme"}


def test_parse_missing_capsulefile_raises_clear_error() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--data-classification PHI", "run")
    assert "--capsulefile" in str(exc.value)
    assert "ADR-013" in str(exc.value)


def test_parse_unknown_flag_raises() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--capsulefile cf_x --bogus value", "run")
    assert "Unknown flag" in str(exc.value)
    assert "--bogus" in str(exc.value)


def test_parse_flag_missing_value_raises() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--capsulefile", "run")
    assert "requires a value" in str(exc.value)


def test_parse_destroy_and_no_destroy_mutually_exclusive() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--capsulefile cf_x --destroy --no-destroy", "run")
    assert "mutually exclusive" in str(exc.value)


def test_parse_malformed_meta_raises() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--capsulefile cf_x --meta novalue", "run")
    assert "--meta expects KEY=VALUE" in str(exc.value)


def test_parse_non_integer_timeout_raises() -> None:
    with pytest.raises(MagicArgsError) as exc:
        parse_magic_args("--capsulefile cf_x --timeout abc", "run")
    assert "integer" in str(exc.value)


def test_parse_usage_string_present() -> None:
    """The usage string is surfaced on errors so the analyst self-corrects."""
    assert "%%nanorix_capsule" in MAGIC_USAGE
    assert "--capsulefile" in MAGIC_USAGE


# ── run_capsule_cell (reuses orchestrator core; mocked client) ─────────


def test_run_capsule_cell_happy_path_reuses_core() -> None:
    client = FakeClient()
    params = parse_magic_args(
        "--capsulefile cf_x --output-file decision.json", "python run.py"
    )
    result = run_capsule_cell(client, params)
    assert isinstance(result, CapsuleJobResult)
    assert result.capsule_id == "cap_abc123"
    assert result.destroyed is True
    assert result.outputs == {"decision.json": b"{}"}
    assert result.disclaimer == NARROW_CLAIM_DISCLAIMER


def test_run_capsule_cell_download_before_destroy_ordering() -> None:
    """Inherited from the core: download happens BEFORE destroy."""
    client = FakeClient()
    params = parse_magic_args(
        "--capsulefile cf_x --output-file decision.json", "run"
    )
    run_capsule_cell(client, params)
    ops = [op for op, _ in client.log]
    assert ops.index("download") < ops.index("destroy")


def test_run_capsule_cell_data_classification_passthrough() -> None:
    client = FakeClient()
    params = parse_magic_args(
        "--capsulefile cf_x --data-classification financial", "run"
    )
    run_capsule_cell(client, params)
    assert client.capsules.last_create_kwargs["data_classification"] == "financial"


def test_run_capsule_cell_no_destroy_leaves_no_proof() -> None:
    client = FakeClient()
    params = parse_magic_args("--capsulefile cf_x --no-destroy", "run")
    result = run_capsule_cell(client, params)
    assert result.destroyed is False
    assert result.audit_proof is None
    assert result.verification is None


def test_run_capsule_cell_reserved_metadata_prefix_rejected() -> None:
    """nanorix:* metadata keys are rejected by the core (fail fast)."""
    client = FakeClient()
    params = parse_magic_args(
        "--capsulefile cf_x --meta nanorix:owner=me", "run"
    )
    with pytest.raises(OrchestratorError) as exc:
        run_capsule_cell(client, params)
    assert "nanorix:" in str(exc.value)


def test_run_capsule_cell_invalid_classification_rejected() -> None:
    client = FakeClient()
    params = parse_magic_args(
        "--capsulefile cf_x --data-classification SECRET", "run"
    )
    with pytest.raises(OrchestratorError):
        run_capsule_cell(client, params)


# ── render_verification_html ───────────────────────────────────────────


class _StubVerify:
    def __init__(self, ok, chain_valid, signature_valid, failure_reason="", failed_step=None, chain_hash="abc123"):
        self.ok = ok
        self.chain_valid = chain_valid
        self.signature_valid = signature_valid
        self.subsystems_attested = ["s"] * (8 if chain_valid else 3)
        self.failure_reason = failure_reason
        self.failed_step = failed_step
        self.chain_hash = chain_hash


def test_render_verification_pass() -> None:
    html = render_verification_html(_StubVerify(True, True, True))
    assert "Chain integrity: PASSED" in html
    assert "Signature: PASSED" in html
    assert "Verification: PASSED" in html


def test_render_verification_fail_structural_language() -> None:
    html = render_verification_html(
        _StubVerify(False, False, False, failure_reason="hash mismatch at step 3", failed_step=3)
    )
    assert "Chain integrity: FAILED" in html
    assert "hash mismatch at step 3" in html
    assert "step 3" in html
    # Structural FAILED, never a regulatory claim.
    assert "COMPLIANT" not in html
    assert "SATISFIED" not in html


def test_render_verification_none_is_neutral() -> None:
    html = render_verification_html(None)
    assert "No AuditProof verification available" in html


def test_render_verification_no_engine_names() -> None:
    """Even a partial-fail render must not leak internal subsystem names."""
    html = render_verification_html(
        _StubVerify(False, True, False, failure_reason="signature did not verify")
    )
    for engine in ("eee_namespace", "dire_keys", "rzl_audit", "fgx_forensic", "EEE", "DIRE", "RZL"):
        assert engine not in html


# ── render_auditproof_html ─────────────────────────────────────────────


def _result_with_outputs(outputs=None, stdout="ok", capsule_id="cap_1"):
    exec_result = FakeExecResult(exit_code=0, stdout=stdout, stderr="")
    return CapsuleJobResult(
        capsule_id=capsule_id,
        exec_result=exec_result,
        outputs=outputs or {},
        audit_proof=FakeAuditProof(capsule_id),
        verification=FakeVerification(capsule_id),
        destroyed=True,
    )


def test_render_auditproof_has_brand_and_no_engine_names() -> None:
    html = render_auditproof_html(_result_with_outputs({"r.json": b"{}"}))
    assert "AuditProof" in html
    assert "cap_1" in html
    # Internal engine names must NEVER appear (ADR-014).
    for engine in (
        "EEE", "DIRE", "RZL", "FGX", "AIPPF", "ACE", "MCP", "FGI",
        "SIS", "ZilchNet", "SLMM", "ZPSS", "ECM", "Memproof", "AVE",
        "eee_namespace", "dire_keys", "rzl_audit",
    ):
        assert engine not in html


def test_render_auditproof_no_regulatory_claims() -> None:
    html = render_auditproof_html(_result_with_outputs({"r.json": b"{}"}))
    # Structural language only; never a regulatory-adequacy assertion.
    assert "COMPLIANT" not in html
    assert "SATISFIED" not in html
    assert "MEETS" not in html


def test_render_auditproof_carries_disclaimer() -> None:
    html = render_auditproof_html(_result_with_outputs())
    # The narrow-claim disclaimer is rendered (escaped) verbatim's substance.
    assert "auditor" in html
    assert "structurally" in html


def test_render_cell_result_html_alias_matches() -> None:
    r = _result_with_outputs({"r.json": b"{}"})
    assert render_cell_result_html(r) == render_auditproof_html(r)


def test_render_auditproof_renders_chain_steps() -> None:
    """The destruction chain renders as neutral ordinals (no subsystem names)."""

    class _Destruction:
        steps = [{"status": "verified"}, {"status": "verified"}]

    class _Verification:
        destruction = _Destruction()

    r = CapsuleJobResult(
        capsule_id="cap_1",
        exec_result=FakeExecResult(),
        verification=_Verification(),
        destroyed=True,
    )
    html = render_auditproof_html(r)
    assert "Destruction chain" in html
    assert "Step 1" in html
    assert "Step 2" in html


# ── XSS escaping (NON-NEGOTIABLE) ──────────────────────────────────────

_XSS = '<script>alert("x")</script>'


def test_xss_escaped_in_stdout() -> None:
    r = _result_with_outputs(stdout=_XSS)
    html = render_auditproof_html(r)
    # Raw executable tag MUST NOT appear; escaped form MUST.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_escaped_in_filename() -> None:
    r = _result_with_outputs(outputs={_XSS: b"data"})
    html = render_auditproof_html(r)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_escaped_in_capsule_id() -> None:
    r = _result_with_outputs(capsule_id='cap"><script>alert(1)</script>')
    html = render_auditproof_html(r)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_escaped_in_verification_failure_message() -> None:
    """A failure message echoing capsule-derived text is escaped."""
    html = render_verification_html(
        _StubVerify(False, False, False, failure_reason=_XSS, failed_step=1)
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_xss_double_quote_escaped_in_chain_hash() -> None:
    html = render_verification_html(
        _StubVerify(True, True, True, chain_hash='"><img src=x onerror=alert(1)>')
    )
    assert "<img" not in html
    assert "&lt;img" in html


def test_xss_escaped_in_metadata_via_filename_bytes_value() -> None:
    """Output bytes value containing a quote does not break out (size only shown)."""
    # Filenames are the customer-controlled key; assert a quote in a name
    # is neutralised.
    r = _result_with_outputs(outputs={'a".json': b"{}"})
    html = render_auditproof_html(r)
    assert 'a".json' not in html  # raw unescaped name must not appear
    assert "a&quot;.json" in html


# ── Import guard ───────────────────────────────────────────────────────


def test_load_ipython_extension_raises_clear_error_without_ipython() -> None:
    """Importing the shell is fine; using the magic without IPython errors."""
    from nanorix.integrations import jupyter  # noqa: WPS433

    assert jupyter._IPYTHON_AVAILABLE is False
    with pytest.raises(ImportError) as exc:
        jupyter.load_ipython_extension(object())
    msg = str(exc.value)
    assert "pip install 'nanorix[jupyter]'" in msg
    assert "ipython>=8.0" in msg


def test_jupyter_shell_module_imports_without_ipython() -> None:
    """The shell module must import even when IPython is absent (guarded)."""
    from nanorix.integrations import jupyter  # noqa: WPS433

    assert hasattr(jupyter, "NanorixMagics")
    assert hasattr(jupyter, "NanorixDisplay")
    assert hasattr(jupyter, "load_ipython_extension")


def test_nanorix_display_repr_html_is_xss_safe() -> None:
    """NanorixDisplay._repr_html_ (IPython-free) renders + escapes."""
    from nanorix.integrations.jupyter import NanorixDisplay  # noqa: WPS433

    r = _result_with_outputs(stdout=_XSS, capsule_id="cap_disp")
    disp = NanorixDisplay(r)
    html = disp._repr_html_()
    assert "AuditProof" in html
    assert "cap_disp" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "NanorixDisplay" in repr(disp)
