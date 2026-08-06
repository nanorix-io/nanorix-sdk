"""Apache Airflow operator — run a sealed Nanorix capsule as a DAG step.

Gap-closing arc. Airflow is dominant in
healthcare data-engineering, so a first-class operator lets a data
engineer make a sealed capsule a node in their DAG instead of hand-
wrapping the SDK in a ``PythonOperator``.

All real logic lives in the framework-agnostic core
(:mod:`nanorix.integrations._orchestrator_core`). This module is a thin
shell: it subclasses ``airflow.models.BaseOperator`` and translates the
operator arguments into a :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`,
then calls :func:`~nanorix.integrations._orchestrator_core.run_capsule_job`.

## Optional dependency

A real Airflow provider depends on apache-airflow, so this operator
genuinely needs the framework at runtime:

```bash
pip install 'nanorix[airflow]'
```

The ``airflow`` import is guarded: importing THIS module without Airflow
installed does not raise. The clear, actionable error is raised only when
the operator is actually instantiated. That keeps ``import
nanorix.integrations`` cheap and the test suite framework-free.

## DAG wiring example

```python
from datetime import datetime

from airflow import DAG
from nanorix import Client
from nanorix.integrations.airflow import NanorixCapsuleOperator

client = Client(api_key="nrx_live_...")

with DAG(
    dag_id="rcm_prior_auth",
    start_date=datetime(2026, 5, 1),
    schedule="@daily",
    catchup=False,
) as dag:
    decide = NanorixCapsuleOperator(
        task_id="pa_decision",
        client=client,
        capsulefile="cf_pa_decider",
        command="python /app/decide.py",
        # Customer declares the classification + binding metadata
        #; the operator never infers them.
        data_classification="PHI",
        metadata={"workflow": "prior-auth", "payer": "acme"},
        output_files=["decision.json"],
    )
```

After the task runs, the AuditProof and the downloaded outputs are pushed
to XCom (keys ``audit_proof_capsule_id``, ``audit_proof_verification``,
and ``outputs``) so downstream tasks can correlate or hand the
verification view to an auditor.

## Narrow signed claim

The operator returns the AuditProof the capsule produced. It does NOT
assert the DAG, the pipeline, or any regulatory framework is satisfied —
the claim is structural ("this capsule ran and was destroyed, here is the
proof"). Substantive determinations are the customer's auditor's, not
Nanorix's.

## Deferrable variant (note)

For long-running capsule jobs you can avoid pinning an Airflow worker slot
for the whole run by pairing a deferrable operator with the async
202-accepted capsule lifecycle (poll-until-complete / webhook completion;
see the Nanorix API service). The
deferrable shell would: submit the job (202), ``self.defer(...)`` onto a
``TimeDeltaTrigger``/poll trigger, and on resume call the SAME core to
download-before-destroy and return the AuditProof. The synchronous
operator below is the V1 ship; the deferrable variant reuses
``run_capsule_job`` verbatim and is a wiring event, not a new engine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    CapsuleJobResult,
    run_capsule_job,
)

# Guard the framework import. Importing this module without Airflow must
# NOT raise — the actionable error is deferred to operator instantiation.
try:  # pragma: no cover - exercised only when apache-airflow is installed
    from airflow.models import BaseOperator as _BaseOperator

    _AIRFLOW_AVAILABLE = True
    _AIRFLOW_IMPORT_ERROR: Optional[Exception] = None
except Exception as _exc:  # ImportError or a transitive provider error
    _BaseOperator = object
    _AIRFLOW_AVAILABLE = False
    _AIRFLOW_IMPORT_ERROR = _exc

# Single source of the remediation message so the operator and any test
# assertion agree on the wording.
AIRFLOW_IMPORT_ERROR_MESSAGE = (
    "NanorixCapsuleOperator requires Apache Airflow, which is not "
    "installed. Install it with:\n\n    pip install 'nanorix[airflow]'\n\n"
    "(installs apache-airflow>=2.7). The framework-agnostic core "
    "(nanorix.integrations._orchestrator_core.run_capsule_job) needs no "
    "framework if you prefer to wire the capsule lifecycle yourself."
)

__all__ = ["NanorixCapsuleOperator", "AIRFLOW_IMPORT_ERROR_MESSAGE"]


class NanorixCapsuleOperator(_BaseOperator):
    """Airflow operator that runs one sealed Nanorix capsule as a DAG step.

    Thin shell over
    :func:`~nanorix.integrations._orchestrator_core.run_capsule_job`. The
    operator's ``execute`` builds a
    :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams` from
    its constructor arguments and returns the resulting AuditProof's
    verification view.

    Args:
        client: A configured ``nanorix.Client``.
        capsulefile: Finalized Capsulefile id (``cf_...``). Required.
        command: Optional command; ``None`` uses the Capsulefile
            entrypoint.
        data_classification: Optional customer-declared classification
            (one of PHI, PII, financial, credentials, general). Never
            inferred.
        metadata: Optional customer-declared compliance-binding metadata.
        input_files: Optional ``filename -> bytes`` uploaded before exec.
        output_files: Optional list of output filenames downloaded BEFORE
            destroy (no post-destroy cache).
        destroy_on_complete: Destroy + return AuditProof when ``True``
            (default).
        max_lifetime_seconds: Optional capsule lifetime ceiling.
        capsule_timeout: Optional per-execute timeout (seconds).
        idempotency_key: Optional idempotency key for capsule create.

    Raises:
        ImportError: at instantiation, when apache-airflow is not
            installed.
    """

    # Templated fields so DAG authors can use Jinja for the command +
    # metadata values, matching Airflow operator conventions.
    template_fields = ("command", "capsulefile")

    def __init__(
        self,
        *,
        client: Any,
        capsulefile: str,
        command: Optional[str] = None,
        data_classification: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        input_files: Optional[Dict[str, bytes]] = None,
        output_files: Optional[List[str]] = None,
        destroy_on_complete: bool = True,
        max_lifetime_seconds: Optional[int] = None,
        capsule_timeout: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if not _AIRFLOW_AVAILABLE:
            raise ImportError(AIRFLOW_IMPORT_ERROR_MESSAGE) from _AIRFLOW_IMPORT_ERROR

        super().__init__(**kwargs)
        self.client = client
        self.capsulefile = capsulefile
        self.command = command
        self.data_classification = data_classification
        self.metadata_ = metadata
        self.input_files = input_files
        self.output_files = output_files
        self.destroy_on_complete = destroy_on_complete
        self.max_lifetime_seconds = max_lifetime_seconds
        self.capsule_timeout = capsule_timeout
        self.idempotency_key = idempotency_key

    def _build_params(self) -> CapsuleJobParams:
        """Translate operator arguments into the framework-agnostic params."""
        return CapsuleJobParams(
            capsulefile=self.capsulefile,
            command=self.command,
            data_classification=self.data_classification,
            metadata=self.metadata_,
            input_files=self.input_files,
            output_files=self.output_files,
            destroy_on_complete=self.destroy_on_complete,
            max_lifetime_seconds=self.max_lifetime_seconds,
            timeout=self.capsule_timeout,
            idempotency_key=self.idempotency_key,
        )

    def execute(self, context: Any) -> Dict[str, Any]:
        """Run the capsule job and return a JSON-serializable summary for XCom.

        The summary carries the capsule id, the shareable verification
        view of the AuditProof, and the narrow-claim disclaimer. The raw
        output bytes are returned under ``outputs`` (base-name keyed) so
        downstream tasks can read them; persist them deliberately, as the
        capsule is destroyed and there is no post-destroy cache.
        """
        result: CapsuleJobResult = run_capsule_job(self.client, self._build_params())

        verification = result.verification
        verification_dict: Any = None
        if verification is not None:
            to_dict = getattr(verification, "model_dump", None) or getattr(
                verification, "dict", None
            )
            verification_dict = to_dict() if callable(to_dict) else verification

        return {
            "audit_proof_capsule_id": result.capsule_id,
            "audit_proof_verification": verification_dict,
            "destroyed": result.destroyed,
            "outputs": result.outputs,
            "disclaimer": result.disclaimer,
        }
