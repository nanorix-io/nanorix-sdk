"""Jupyter cell magic — run a sealed Nanorix capsule from a notebook cell.

Batch 16 (2026-05-28) — FINAL gap-closing batch. Data scientists live in
notebooks, so a ``%%nanorix_capsule`` cell magic lets an analyst run a
sealed-capsule lifecycle and render the AuditProof + its verification
inline, instead of hand-wiring the SDK in a code cell.

All real logic lives in the IPython-free core
(:mod:`nanorix.integrations._notebook_core`), which itself reuses the
Batch-13 orchestrator core
(:func:`nanorix.integrations._orchestrator_core.run_capsule_job`). This
module is a thin shell: it guards the ``IPython`` import, registers the
cell magic, parses the magic line into a ``CapsuleJobParams`` (via the
core), runs the capsule job (via the core), and renders the result (via
the core). A notebook cell IS one capsule job; the lifecycle is reused
verbatim, never reimplemented.

## Optional dependency

Jupyter magics need ``IPython`` at runtime, so this module's magic surface
genuinely requires the framework:

```bash
pip install 'nanorix[jupyter]'
```

The ``IPython`` import is guarded: importing THIS module without IPython
does NOT raise. The clear, actionable error is raised only when you try to
register/use the magic. That keeps ``import nanorix.integrations`` cheap
and the test suite framework-free — the pure functions in
:mod:`nanorix.integrations._notebook_core` (``render_auditproof_html``,
``render_verification_html``, ``parse_magic_args``, ``run_capsule_cell``)
import without IPython and are tested directly.

## Notebook usage

```python
%load_ext nanorix.integrations.jupyter

from nanorix import Client
nrx = Client(api_key="nrx_live_...")

# Tell the magic which client to use (once per kernel).
%nanorix_client nrx
```

```python
%%nanorix_capsule --capsulefile cf_phi_pipeline --data-classification PHI --output-file result.json
python /app/run.py
```

The cell body is the command run inside the sealed capsule. After the cell
runs, the capsule's AuditProof — the destruction chain plus an INDEPENDENT
offline verification (chain integrity + Ed25519 signature) — renders as
rich HTML right under the cell.

## Narrow signed claim

The rendered output is STRUCTURAL: chain integrity and signature validity.
It NEVER renders a regulatory-adequacy claim ("compliant", "satisfied").
Whether any framework is satisfied is the customer's auditor's
determination, not Nanorix's (ADR-017 three-role discipline). The customer
declares the Capsulefile + data-classification on the magic line (ADR-013);
the magic never infers either.
"""

from __future__ import annotations

from typing import Any, Optional

# The pure, IPython-free core. Safe to import eagerly (no IPython, no
# framework). Tests import only these.
from nanorix.integrations._notebook_core import (
    MAGIC_USAGE,
    NARROW_CLAIM_DISCLAIMER,
    CapsuleJobResult,
    MagicArgsError,
    parse_magic_args,
    render_auditproof_html,
    render_cell_result_html,
    render_verification_html,
    run_capsule_cell,
)

# Guard the framework import. Importing this module without IPython must NOT
# raise — the actionable error is deferred to magic registration/use. Same
# discipline as the Airflow operator + Temporal activity (Batch 13).
try:  # pragma: no cover - exercised only when IPython is installed
    from IPython.core.magic import Magics, line_magic, magics_class, cell_magic
    from IPython.display import HTML, display

    _IPYTHON_AVAILABLE = True
    _IPYTHON_IMPORT_ERROR: Optional[Exception] = None
except Exception as _exc:  # ImportError or a transitive IPython error
    Magics = object
    _IPYTHON_AVAILABLE = False
    _IPYTHON_IMPORT_ERROR = _exc

    def magics_class(cls: Any) -> Any:
        """No-op stand-in so the class body parses without IPython."""
        return cls

    def cell_magic(*_a: Any, **_k: Any) -> Any:
        """No-op stand-in so the class body parses without IPython."""

        def _decorator(func: Any) -> Any:
            return func

        return _decorator

    def line_magic(*_a: Any, **_k: Any) -> Any:
        """No-op stand-in so the class body parses without IPython."""

        def _decorator(func: Any) -> Any:
            return func

        return _decorator


# Single source of the remediation message so the magic and any test
# assertion agree on the wording.
IPYTHON_IMPORT_ERROR_MESSAGE = (
    "The Nanorix Jupyter magic requires IPython, which is not installed. "
    "Install it with:\n\n    pip install 'nanorix[jupyter]'\n\n"
    "(installs ipython>=8.0). The framework-agnostic helpers "
    "(nanorix.integrations._notebook_core.run_capsule_cell / "
    "render_auditproof_html) need no IPython if you prefer to drive the "
    "capsule lifecycle and render the AuditProof yourself."
)

__all__ = [
    "NanorixMagics",
    "NanorixDisplay",
    "load_ipython_extension",
    "IPYTHON_IMPORT_ERROR_MESSAGE",
    # Re-exported core helpers for callers who reach the shell first.
    "parse_magic_args",
    "run_capsule_cell",
    "render_auditproof_html",
    "render_verification_html",
    "render_cell_result_html",
    "NARROW_CLAIM_DISCLAIMER",
    "MAGIC_USAGE",
]


def _require_ipython() -> None:
    """Raise the actionable remediation error iff IPython is unavailable."""
    if not _IPYTHON_AVAILABLE:
        raise ImportError(IPYTHON_IMPORT_ERROR_MESSAGE) from _IPYTHON_IMPORT_ERROR


class NanorixDisplay:
    """Rich-display wrapper for a capsule cell's AuditProof result.

    Holds a :class:`~nanorix.integrations._orchestrator_core.CapsuleJobResult`
    (and optional offline verification result) and exposes ``_repr_html_``
    so a notebook renders the AuditProof chain + structural verification
    PASS/FAIL nicely when the object is the last expression in a cell.

    The HTML is produced by the IPython-free renderer
    (:func:`nanorix.integrations._notebook_core.render_auditproof_html`), so
    this wrapper carries no IPython dependency and is unit-testable directly.
    Structural language only — no regulatory-adequacy claim appears
    (narrow-claim rule). Every capsule-supplied value is XSS-escaped by the
    renderer.
    """

    def __init__(
        self,
        result: CapsuleJobResult,
        verify_result: Any = None,
    ) -> None:
        self.result = result
        self.verify_result = verify_result

    def _repr_html_(self) -> str:
        """Return the rich HTML representation for the notebook."""
        return render_auditproof_html(self.result, verify_result=self.verify_result)

    def __repr__(self) -> str:
        capsule_id = getattr(self.result, "capsule_id", "?")
        destroyed = getattr(self.result, "destroyed", False)
        return f"<NanorixDisplay capsule_id={capsule_id!r} destroyed={destroyed}>"


@magics_class
class NanorixMagics(Magics):
    """IPython magics for running sealed Nanorix capsules from a notebook.

    Registers two magics:

    - ``%nanorix_client <expr>`` (line magic) — bind the
      ``nanorix.Client`` the cell magic uses. ``<expr>`` is evaluated in the
      user namespace (e.g. ``%nanorix_client nrx``).
    - ``%%nanorix_capsule <args>`` (cell magic) — run the cell body as the
      command inside a sealed capsule and render the AuditProof inline.

    All work delegates to the IPython-free core; this class only bridges the
    IPython magic protocol to it.
    """

    def __init__(self, shell: Any = None, client: Any = None) -> None:
        # ``Magics.__init__`` requires the shell; tolerate a bare construct
        # under the no-IPython stand-in so importing stays cheap.
        if _IPYTHON_AVAILABLE:
            super().__init__(shell)
        self._client = client

    @line_magic("nanorix_client")
    def nanorix_client(self, line: str) -> Any:
        """Bind the ``nanorix.Client`` the ``%%nanorix_capsule`` magic uses.

        ``%nanorix_client nrx`` evaluates ``nrx`` in the user namespace and
        stores it as the client for subsequent capsule cells.
        """
        name = (line or "").strip()
        if not name:
            raise MagicArgsError(
                "Usage: %nanorix_client <client-variable>, e.g. "
                "%nanorix_client nrx (a nanorix.Client you already created)."
            )
        # Plain user-namespace VARIABLE LOOKUP only — never eval(). A client
        # binding only needs a name, so we look it up directly rather than
        # executing an arbitrary expression. Avoids any code-execution path.
        user_ns = getattr(self.shell, "user_ns", {}) if self.shell else {}
        if name not in user_ns:
            raise MagicArgsError(
                f"No variable named {name!r} in the notebook namespace. "
                "Create the client first, then bind it by name:\n\n"
                "    from nanorix import Client\n"
                "    nrx = Client(api_key='nrx_live_...')\n"
                "    %nanorix_client nrx\n"
            )
        self._client = user_ns[name]
        return self._client

    @cell_magic("nanorix_capsule")
    def nanorix_capsule(self, line: str, cell: str) -> Any:
        """Run the cell body as a sealed capsule and render the AuditProof.

        The magic line carries the customer-declared parameters
        (``--capsulefile``, ``--data-classification``, ``--destroy`` /
        ``--no-destroy``, ``--output-file``, ``--meta``, ...); the cell body
        is the command run inside the capsule. The result renders as rich
        HTML (chain + structural verification) under the cell, and a
        :class:`NanorixDisplay` is stored in the user namespace as
        ``_nanorix_last`` for programmatic access.
        """
        if self._client is None:
            raise MagicArgsError(
                "No Nanorix client is bound. Run "
                "`%nanorix_client <your-client-variable>` first, e.g.:\n\n"
                "    from nanorix import Client\n"
                "    nrx = Client(api_key='nrx_live_...')\n"
                "    %nanorix_client nrx\n"
            )

        params = parse_magic_args(line, cell)
        result = run_capsule_cell(self._client, params)
        rendered = NanorixDisplay(result)

        # Stash the result for programmatic follow-up cells.
        if self.shell is not None and hasattr(self.shell, "user_ns"):
            self.shell.user_ns["_nanorix_last"] = rendered

        # Render inline.
        display(HTML(rendered._repr_html_()))
        return rendered


def load_ipython_extension(ipython: Any) -> None:
    """Register the Nanorix magics with a running IPython kernel.

    Called automatically by ``%load_ext nanorix.integrations.jupyter``.

    Args:
        ipython: The active ``InteractiveShell`` instance IPython passes in.

    Raises:
        ImportError: when IPython is not installed (the module was imported
            but the framework surface is unavailable).
    """
    _require_ipython()
    ipython.register_magics(NanorixMagics)


def unload_ipython_extension(ipython: Any) -> None:  # pragma: no cover - cosmetic
    """Symmetric unload hook (no-op; magics are kernel-scoped)."""
    return None
