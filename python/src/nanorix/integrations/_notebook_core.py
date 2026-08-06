"""Framework-agnostic core for the Jupyter integration.

Batch 16 (2026-05-28) — FINAL gap-closing batch. This module is the pure,
IPython-free engine behind the ``%%nanorix_capsule`` cell magic
(:mod:`nanorix.integrations.jupyter`). It has NO dependency on ``IPython``
and is fully unit-testable with a mocked client — exactly the discipline
the Airflow operator / Temporal activity (Batch 13) and the data
source/sink connectors (Batch 14) follow.

The split is deliberate:

- **This module** holds the cell-execution wrapper plus all HTML rendering
  and magic-line argument parsing. None of it imports ``IPython``. Tests
  import only from here.
- **The shell** (:mod:`nanorix.integrations.jupyter`) registers the cell
  magic with IPython, guards the ``IPython`` import, and delegates every
  line of real work to this module.

## What the core does

- :func:`run_capsule_cell` — a thin wrapper over the Batch-13 orchestrator
  core :func:`nanorix.integrations._orchestrator_core.run_capsule_job`. A
  notebook cell IS one capsule job: create → upload? → execute → download?
  (BEFORE destroy) → destroy → AuditProof. The lifecycle is NOT
  reimplemented here; it is reused verbatim.
- :func:`parse_magic_args` — parses the ``%%nanorix_capsule`` magic-line
  arguments (``--capsulefile``, ``--data-classification``, ``--destroy`` /
  ``--no-destroy``, ``--output-file``, ``--meta``, ...) into a
  :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`. The
  cell body becomes the command run inside the capsule.
- :func:`render_auditproof_html` / :func:`render_verification_html` —
  render the AuditProof chain + the offline verification result as an
  HTML fragment for inline notebook display.

## Trust-boundary discipline (NON-NEGOTIABLE)

- **Reuse the orchestrator core.** A cell runs :func:`run_capsule_job`;
  the download-before-destroy ordering and the cleanup-on-failure
  behaviour are inherited, not re-derived. There is NO cell-side cache of
  capsule data after destroy (``feedback_no_post_destroy_cache.md``).
- **Customer-sole-declarer.** ``--capsulefile`` and
  ``--data-classification`` come from the magic-line args the analyst
  typed; the core never infers either.
- **AuditProof external brand.** Rendered output says
  "AuditProof". Internal subsystem names never appear in this surface.
- **Narrow signed claim.** The HTML renders STRUCTURAL verification —
  chain integrity and signature validity. It NEVER renders a regulatory
  adequacy claim. "Chain integrity: PASSED" is a structural label; whether
  any framework is satisfied is the customer's auditor's determination,
  never Nanorix's.
- **No new signed primitive.** This consumes the AuditProof the capsule
  already produced and the existing offline verifier; it signs nothing.
- **XSS-safe rendering.** Every capsule-supplied / customer-supplied value
  embedded in the HTML (capsule output, capsule_id, filenames, error
  messages, metadata, the command) is escaped via :func:`html.escape`
  before it reaches the HTML string. A notebook renders HTML output as
  live DOM; an unescaped ``<script>`` in a customer filename or a capsule's
  stdout would otherwise execute in the analyst's browser.
"""

from __future__ import annotations

import html
import shlex
from typing import Any, Dict, List, Mapping, Optional, Tuple

from nanorix.integrations._orchestrator_core import (
    NARROW_CLAIM_DISCLAIMER,
    CapsuleJobParams,
    CapsuleJobResult,
    run_capsule_job,
)

__all__ = [
    "CapsuleJobResult",
    "MagicArgsError",
    "parse_magic_args",
    "run_capsule_cell",
    "render_auditproof_html",
    "render_verification_html",
    "render_cell_result_html",
    "NARROW_CLAIM_DISCLAIMER",
    "MAGIC_USAGE",
]


# Usage string surfaced on a parse error so the analyst sees exactly what
# the magic accepts without leaving the notebook.
MAGIC_USAGE = (
    "Usage:\n"
    "    %%nanorix_capsule --capsulefile cf_... "
    "[--data-classification PHI|PII|financial|credentials|general]\n"
    "                      [--destroy | --no-destroy] "
    "[--output-file NAME ...] [--meta KEY=VALUE ...]\n"
    "                      [--timeout SECONDS] [--max-lifetime SECONDS] "
    "[--idempotency-key KEY]\n"
    "    <cell body = the command/script to run inside the sealed capsule>\n\n"
    "The capsulefile and data-classification are customer-declared; "
    "they are never inferred."
)


class MagicArgsError(Exception):
    """Raised when the ``%%nanorix_capsule`` magic-line arguments are invalid.

    Distinct from :class:`~nanorix.integrations._orchestrator_core.OrchestratorError`
    (which covers job-parameter / wire-constraint violations) so the magic
    can surface an argument-parsing problem with the usage string while
    letting deeper validation errors propagate from the core.
    """


# ── Magic-line argument parsing ─────────────────────────────────────────


def parse_magic_args(
    line: str,
    cell: Optional[str] = None,
) -> CapsuleJobParams:
    """Parse the ``%%nanorix_capsule`` magic line into ``CapsuleJobParams``.

    The cell body (``cell``) becomes the command executed inside the
    capsule. The magic line carries the customer-declared parameters.

    Accepted flags:

    - ``--capsulefile cf_...`` (REQUIRED) — finalized Capsulefile id.
    - ``--data-classification {PHI|PII|financial|credentials|general}`` —
      customer-declared classification. Validated against the accepted set
      by the core (never inferred).
    - ``--destroy`` / ``--no-destroy`` — destroy the capsule and return the
      AuditProof (default) vs. leave it running (no AuditProof). Mutually
      exclusive.
    - ``--output-file NAME`` (repeatable) — output filename downloaded
      BEFORE destroy (no post-destroy cache).
    - ``--meta KEY=VALUE`` (repeatable) — customer-declared
      compliance-binding metadata. ``nanorix:*`` keys are rejected by the
      core.
    - ``--timeout SECONDS`` — per-execute timeout.
    - ``--max-lifetime SECONDS`` — per-capsule lifetime ceiling.
    - ``--idempotency-key KEY`` — idempotency key for capsule create.

    Args:
        line: The magic line (everything after ``%%nanorix_capsule``).
        cell: The cell body — used as the capsule command. ``None`` or an
            empty/whitespace-only body means "use the Capsulefile
            entrypoint" (``command=None``).

    Returns:
        A :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`
        ready to hand to :func:`run_capsule_cell`.

    Raises:
        MagicArgsError: unparseable line, missing ``--capsulefile``, an
            unknown flag, a flag missing its value, both ``--destroy`` and
            ``--no-destroy``, a malformed ``--meta`` token, or a
            non-integer numeric flag.
    """
    try:
        tokens = shlex.split(line or "")
    except ValueError as exc:
        raise MagicArgsError(
            f"Could not parse the %%nanorix_capsule magic line ({exc}).\n\n"
            f"{MAGIC_USAGE}"
        ) from exc

    capsulefile: Optional[str] = None
    data_classification: Optional[str] = None
    output_files: List[str] = []
    metadata: Dict[str, str] = {}
    timeout: Optional[int] = None
    max_lifetime: Optional[int] = None
    idempotency_key: Optional[str] = None
    destroy: Optional[bool] = None  # None = use default (True)

    def _next_value(flag: str, idx: int) -> Tuple[str, int]:
        if idx + 1 >= len(tokens):
            raise MagicArgsError(
                f"{flag} requires a value.\n\n{MAGIC_USAGE}"
            )
        return tokens[idx + 1], idx + 1

    def _int_value(flag: str, raw: str) -> int:
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise MagicArgsError(
                f"{flag} expects an integer; got {raw!r}.\n\n{MAGIC_USAGE}"
            ) from exc

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("--capsulefile", "--cf"):
            capsulefile, i = _next_value(tok, i)
        elif tok in ("--data-classification", "--classification"):
            data_classification, i = _next_value(tok, i)
        elif tok in ("--output-file", "--output", "-o"):
            value, i = _next_value(tok, i)
            output_files.append(value)
        elif tok == "--meta":
            value, i = _next_value(tok, i)
            if "=" not in value:
                raise MagicArgsError(
                    f"--meta expects KEY=VALUE; got {value!r}.\n\n{MAGIC_USAGE}"
                )
            key, _, val = value.partition("=")
            key = key.strip()
            if not key:
                raise MagicArgsError(
                    f"--meta key is empty in {value!r}.\n\n{MAGIC_USAGE}"
                )
            metadata[key] = val
        elif tok == "--timeout":
            value, i = _next_value(tok, i)
            timeout = _int_value(tok, value)
        elif tok in ("--max-lifetime", "--max-lifetime-seconds"):
            value, i = _next_value(tok, i)
            max_lifetime = _int_value(tok, value)
        elif tok in ("--idempotency-key", "--idempotency"):
            idempotency_key, i = _next_value(tok, i)
        elif tok == "--destroy":
            if destroy is False:
                raise MagicArgsError(
                    "--destroy and --no-destroy are mutually exclusive.\n\n"
                    f"{MAGIC_USAGE}"
                )
            destroy = True
        elif tok == "--no-destroy":
            if destroy is True:
                raise MagicArgsError(
                    "--destroy and --no-destroy are mutually exclusive.\n\n"
                    f"{MAGIC_USAGE}"
                )
            destroy = False
        else:
            raise MagicArgsError(
                f"Unknown flag {tok!r} on the %%nanorix_capsule magic line.\n\n"
                f"{MAGIC_USAGE}"
            )
        i += 1

    if not capsulefile or not capsulefile.strip():
        raise MagicArgsError(
            "%%nanorix_capsule requires --capsulefile (the finalized "
            "Capsulefile id, e.g. --capsulefile cf_...). The analyst "
            "declares it explicitly; it is never inferred.\n\n"
            f"{MAGIC_USAGE}"
        )

    command = cell.strip() if cell and cell.strip() else None

    return CapsuleJobParams(
        capsulefile=capsulefile,
        command=command,
        data_classification=data_classification,
        metadata=metadata or None,
        output_files=output_files or None,
        destroy_on_complete=True if destroy is None else destroy,
        timeout=timeout,
        max_lifetime_seconds=max_lifetime,
        idempotency_key=idempotency_key,
    )


# ── Cell execution (thin wrapper over the orchestrator core) ────────────


def run_capsule_cell(client: Any, params: CapsuleJobParams) -> CapsuleJobResult:
    """Run one notebook cell as a sealed capsule job.

    A thin wrapper over
    :func:`nanorix.integrations._orchestrator_core.run_capsule_job`. The
    Jupyter integration adds NO lifecycle logic of its own — a cell is one
    capsule job, and all of the create → upload → execute → download
    (before destroy) → destroy ordering, the cleanup-on-failure behaviour,
    and the no-post-destroy-cache guarantee are inherited from the core.

    Args:
        client: A configured ``nanorix.Client`` (or any object exposing the
            same ``capsules.create(...) -> LiveCapsule`` surface). In tests
            this is a mock; no IPython or framework is required.
        params: The customer-declared
            :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`
            (typically built by :func:`parse_magic_args`).

    Returns:
        The :class:`~nanorix.integrations._orchestrator_core.CapsuleJobResult`
        carrying the exec result, any downloaded outputs, and (when the
        capsule was destroyed) the AuditProof + shareable verification view.

    Raises:
        OrchestratorError: invalid job parameters (validated by the core).
        Exception: any native client/transport/execution exception is
            propagated unchanged after best-effort capsule cleanup.
    """
    return run_capsule_job(client, params)


# ── HTML rendering (XSS-safe; structural language only) ─────────────────


# Inline CSS kept scoped under a single wrapper class so it cannot leak into
# the surrounding notebook DOM. All values interpolated into the HTML below
# are either trusted literals (defined in this module) or run through
# ``html.escape`` first.
_WRAPPER_OPEN = '<div class="nrx-auditproof" style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:12px 16px;max-width:760px;">'
_WRAPPER_CLOSE = "</div>"


def _esc(value: Any) -> str:
    """Escape any value for safe embedding in an HTML text/attribute context.

    Coerces to ``str`` first (capsule outputs may be bytes, ids may be
    non-string in a malformed proof) so a non-string never slips past the
    escaper. ``quote=True`` so embedded double quotes can't break out of an
    attribute value either.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    return html.escape(str(value), quote=True)


def _badge(label: str, ok: bool) -> str:
    """Render a structural PASS/FAIL badge.

    ``label`` is a trusted literal supplied by this module. The PASS/FAIL
    word is a STRUCTURAL status (chain integrity / signature validity),
    never a regulatory-adequacy claim.
    """
    color = "#1a7f37" if ok else "#cf222e"
    word = "PASSED" if ok else "FAILED"
    return (
        f'<span style="display:inline-block;padding:1px 8px;border-radius:'
        f'12px;background:{color};color:#fff;font-size:12px;font-weight:600;">'
        f"{_esc(label)}: {word}</span>"
    )


def render_verification_html(verify_result: Any) -> str:
    """Render an offline-verification result as an XSS-safe HTML fragment.

    Accepts a :class:`nanorix.verifier.VerifyResult` (or any object exposing
    ``ok`` / ``chain_valid`` / ``signature_valid`` / ``subsystems_attested``
    / ``failed_step`` / ``failure_reason`` / ``chain_hash``). The render is
    STRUCTURAL only — chain integrity and Ed25519 signature validity. It
    never asserts regulatory adequacy; that is the customer's auditor's
    determination (three-role discipline; narrow-claim rule).

    Args:
        verify_result: The verification result to render. ``None`` renders a
            neutral "no verification available" note (e.g. when the capsule
            was not destroyed).

    Returns:
        An HTML fragment string. All dynamic values are ``html.escape``-d.
    """
    if verify_result is None:
        return (
            '<div class="nrx-verify" style="margin-top:8px;color:#57606a;'
            'font-size:13px;">No AuditProof verification available '
            "(capsule was not destroyed).</div>"
        )

    ok = bool(getattr(verify_result, "ok", False))
    chain_valid = bool(getattr(verify_result, "chain_valid", False))
    signature_valid = bool(getattr(verify_result, "signature_valid", False))
    subsystems = getattr(verify_result, "subsystems_attested", None) or []
    failed_step = getattr(verify_result, "failed_step", None)
    failure_reason = getattr(verify_result, "failure_reason", "") or ""
    chain_hash = getattr(verify_result, "chain_hash", "") or ""

    parts: List[str] = [
        '<div class="nrx-verify" style="margin-top:8px;">',
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px;">',
        # "Chain integrity" / "Signature" are STRUCTURAL labels — fine.
        _badge("Chain integrity", chain_valid),
        _badge("Signature", signature_valid),
        _badge("Verification", ok),
        "</div>",
    ]

    parts.append(
        '<div style="font-size:12px;color:#57606a;">'
        f"Subsystems attested: {len(subsystems)} / 8"
        "</div>"
    )

    if chain_hash:
        parts.append(
            '<div style="font-size:11px;color:#57606a;word-break:break-all;'
            'font-family:ui-monospace,SFMono-Regular,monospace;margin-top:4px;">'
            f"chain_hash: {_esc(chain_hash)}"
            "</div>"
        )

    if not ok and failure_reason:
        # failure_reason can echo capsule/proof-derived strings — escape it.
        step_note = f" (step {_esc(failed_step)})" if failed_step is not None else ""
        parts.append(
            '<div style="font-size:12px;color:#cf222e;margin-top:6px;">'
            f"Failure{step_note}: {_esc(failure_reason)}"
            "</div>"
        )

    parts.append("</div>")
    return "".join(parts)


def render_auditproof_html(
    result: CapsuleJobResult,
    verify_result: Any = None,
) -> str:
    """Render a capsule cell's AuditProof + verification as an HTML fragment.

    Produces the rich inline notebook output: the capsule id, the
    destruction chain (8 structural steps), the exec exit code, and the
    offline verification badges. Every capsule-supplied value (capsule_id,
    output filenames, stdout/stderr, error text, metadata) is escaped before
    embedding (``feedback_no_silly_mistakes`` + XSS discipline).

    Args:
        result: The
            :class:`~nanorix.integrations._orchestrator_core.CapsuleJobResult`
            from :func:`run_capsule_cell`.
        verify_result: Optional offline verification result
            (:class:`nanorix.verifier.VerifyResult`) to render alongside.
            When ``None`` the verification block is rendered from the
            result's shareable verification view if available, otherwise a
            neutral note.

    Returns:
        An HTML fragment string (safe to hand to IPython ``HTML(...)`` or a
        ``_repr_html_``). Structural language only — no regulatory-adequacy
        claim appears.
    """
    capsule_id = getattr(result, "capsule_id", "") or ""
    destroyed = bool(getattr(result, "destroyed", False))
    exec_result = getattr(result, "exec_result", None)
    outputs = getattr(result, "outputs", None) or {}
    disclaimer = getattr(result, "disclaimer", "") or NARROW_CLAIM_DISCLAIMER

    parts: List[str] = [_WRAPPER_OPEN]

    # Header — AuditProof brand, never an internal engine name.
    parts.append(
        '<div style="font-weight:700;font-size:15px;margin-bottom:8px;">'
        "Nanorix AuditProof"
        "</div>"
    )

    # Capsule id + destroyed status.
    parts.append(
        '<div style="font-size:13px;margin-bottom:6px;">'
        f"Capsule: <code>{_esc(capsule_id)}</code>"
        "</div>"
    )
    parts.append(
        '<div style="font-size:13px;margin-bottom:6px;">'
        f"Destroyed: <strong>{'yes' if destroyed else 'no'}</strong>"
        "</div>"
    )

    # Exit code (best-effort; exec_result shape is client-defined).
    exit_code = getattr(exec_result, "exit_code", None)
    if exit_code is not None:
        parts.append(
            '<div style="font-size:13px;margin-bottom:6px;">'
            f"Exit code: <code>{_esc(exit_code)}</code>"
            "</div>"
        )

    # Captured stdout / stderr — capsule-supplied, MUST be escaped.
    stdout = getattr(exec_result, "stdout", None)
    stderr = getattr(exec_result, "stderr", None)
    if stdout:
        parts.append(_render_stream("stdout", stdout))
    if stderr:
        parts.append(_render_stream("stderr", stderr))

    # Downloaded outputs — filenames are customer/capsule-supplied; escape.
    if outputs:
        parts.append(
            '<div style="font-size:13px;margin-top:6px;margin-bottom:2px;">'
            "Outputs downloaded before destroy:"
            "</div><ul style=\"margin:2px 0 6px 0;font-size:12px;\">"
        )
        for filename, data in outputs.items():
            size = len(data) if isinstance(data, (bytes, bytearray, str)) else 0
            parts.append(
                f"<li><code>{_esc(filename)}</code> "
                f"({_esc(size)} bytes)</li>"
            )
        parts.append("</ul>")

    # Destruction chain (the structural 8 steps), rendered from the
    # shareable verification view if present.
    parts.append(_render_chain(result))

    # Offline verification badges (structural).
    if verify_result is not None:
        parts.append(render_verification_html(verify_result))
    else:
        parts.append(
            render_verification_html(_verify_from_result(result))
        )

    # Narrow-claim disclaimer — carried verbatim so a reader can never read
    # a regulatory assertion into a structural proof.
    parts.append(
        '<div style="font-size:11px;color:#57606a;margin-top:10px;'
        'border-top:1px solid #eaeef2;padding-top:6px;">'
        f"{_esc(disclaimer)}"
        "</div>"
    )

    parts.append(_WRAPPER_CLOSE)
    return "".join(parts)


def render_cell_result_html(
    result: CapsuleJobResult,
    verify_result: Any = None,
) -> str:
    """Alias of :func:`render_auditproof_html` (cell-output entrypoint).

    Named for the call site (the magic renders the cell's result); kept as a
    distinct public symbol so the magic and tests have a stable name even if
    the AuditProof renderer is later split.
    """
    return render_auditproof_html(result, verify_result=verify_result)


# ── Rendering internals ─────────────────────────────────────────────────


def _render_stream(label: str, content: Any) -> str:
    """Render captured stdout/stderr in an escaped, scrollable block.

    ``label`` is a trusted literal; ``content`` is capsule-supplied and is
    escaped. Capped so a runaway stream can't bloat the notebook DOM.
    """
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)
    if len(text) > 4000:
        text = text[:4000] + "\n... [truncated]"
    return (
        f'<div style="font-size:12px;color:#57606a;margin-top:4px;">{_esc(label)}:</div>'
        '<pre style="background:#f6f8fa;border-radius:6px;padding:6px 8px;'
        "font-size:11px;max-height:160px;overflow:auto;white-space:pre-wrap;"
        f'word-break:break-word;margin:2px 0;">{_esc(text)}</pre>'
    )


def _render_chain(result: CapsuleJobResult) -> str:
    """Render the structural destruction chain from the verification view.

    Reads the shareable verification view (``result.verification`` — a
    :class:`nanorix.verifier`... no, the SDK ``VerificationCdp``) if present.
    The chain steps' ``subsystem`` names are NOT rendered — they are
    internal engine identifiers. Instead each step renders as a
    neutral ordinal "Step N" with its structural ``status`` if present.
    """
    verification = getattr(result, "verification", None)
    steps = _extract_chain_steps(verification)
    if not steps:
        return ""

    parts: List[str] = [
        '<div style="font-size:13px;margin-top:8px;margin-bottom:2px;">'
        "Destruction chain:"
        "</div><ol style=\"margin:2px 0 6px 0;font-size:12px;\">"
    ]
    for idx, step in enumerate(steps, start=1):
        # Render an ordinal + structural status ONLY. Never the subsystem
        # name (internal engine identifier). ``status`` is a
        # neutral structural field if present; escape it regardless.
        status = ""
        if isinstance(step, Mapping):
            raw_status = step.get("status") or step.get("verified")
            if raw_status is not None:
                status = f" — {_esc(raw_status)}"
        parts.append(f"<li>Step {idx}{status}</li>")
    parts.append("</ol>")
    return "".join(parts)


def _extract_chain_steps(verification: Any) -> List[Any]:
    """Pull the destruction-chain step list off a shareable verification view.

    Tolerates the SDK ``VerificationCdp`` (``.destruction.steps``), a plain
    dict, or anything without a chain (returns ``[]``). Never raises.
    """
    if verification is None:
        return []
    destruction = getattr(verification, "destruction", None)
    if destruction is None and isinstance(verification, Mapping):
        destruction = verification.get("destruction")
    if destruction is None:
        return []
    steps = getattr(destruction, "steps", None)
    if steps is None and isinstance(destruction, Mapping):
        steps = destruction.get("steps")
    if isinstance(steps, list):
        return steps
    return []


def _verify_from_result(result: CapsuleJobResult) -> Any:
    """Best-effort offline verification of the AuditProof on a cell result.

    Runs the offline verifier (:func:`nanorix.verifier.verify`) over the
    full AuditProof document when one is present, so the rendered badges
    reflect an INDEPENDENT recomputation rather than trusting the API's
    say-so. Returns ``None`` when the capsule wasn't destroyed or no full
    proof is reachable (the renderer then shows a neutral note).

    Imported lazily so this module imports without the verifier package
    being loaded eagerly, mirroring the orchestrator core's connector
    imports.
    """
    audit_proof = getattr(result, "audit_proof", None)
    if audit_proof is None:
        return None
    proof_dict = _full_proof_dict(audit_proof)
    if proof_dict is None:
        return None
    try:
        from nanorix.verifier import verify

        return verify(proof_dict)
    except Exception:  # noqa: BLE001 - rendering must never crash the cell
        return None


def _full_proof_dict(audit_proof: Any) -> Optional[Dict[str, Any]]:
    """Extract the full proof dict from an AuditProof-like object.

    Accepts the SDK ``CdpAccess`` (``.to_dict()``), a plain dict, or a
    ``full()``-returning shape. Returns ``None`` when no usable full proof
    is reachable.
    """
    if isinstance(audit_proof, dict):
        return audit_proof
    to_dict = getattr(audit_proof, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
            if isinstance(value, dict):
                return value
        except Exception:  # noqa: BLE001
            return None
    return None
