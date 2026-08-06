"""Temporal activity — run a sealed Nanorix capsule inside a workflow.

Gap-closing arc. Temporal is rising fastest for
durable RCM / prior-authorization workflows, so a first-class activity
lets a workflow author make a sealed capsule a durable step (with
Temporal's retry + heartbeat semantics) instead of hand-wrapping the SDK.

All real logic lives in the framework-agnostic core
(:mod:`nanorix.integrations._orchestrator_core`). This module is a thin
shell: it exposes a Temporal activity (decorated with
``@temporalio.activity.defn``) plus a registration helper. The activity
builds a :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`
and calls :func:`~nanorix.integrations._orchestrator_core.run_capsule_job`.

## Optional dependency

A real Temporal worker depends on temporalio, so the activity genuinely
needs the framework at runtime:

```bash
pip install 'nanorix[temporal]'
```

The ``temporalio`` import is guarded: importing THIS module without
temporalio installed does not raise. The clear, actionable error is
raised only when you try to build/register the activity. That keeps
``import nanorix.integrations`` cheap and the test suite framework-free.

## Worker wiring example

```python
import asyncio

from temporalio.client import Client as TemporalClient
from temporalio.worker import Worker

from nanorix import Client
from nanorix.integrations.temporal import (
    NanorixCapsuleActivityInput,
    make_capsule_activity,
)

async def main() -> None:
    nanorix_client = Client(api_key="nrx_live_...")
    capsule_activity = make_capsule_activity(nanorix_client)

    temporal_client = await TemporalClient.connect("localhost:7233")
    worker = Worker(
        temporal_client,
        task_queue="rcm",
        activities=[capsule_activity],
    )
    await worker.run()

asyncio.run(main())
```

A workflow then executes the activity with a
``NanorixCapsuleActivityInput`` (all customer-declared):

```python
result = await workflow.execute_activity(
    "nanorix_capsule_activity",
    NanorixCapsuleActivityInput(
        capsulefile="cf_pa_decider",
        command="python /app/decide.py",
        data_classification="PHI",
        metadata={"workflow": "prior-auth"},
        output_files=["decision.json"],
    ),
    start_to_close_timeout=timedelta(minutes=10),
)
```

## Narrow signed claim

The activity returns the AuditProof the capsule produced. It does NOT
assert the workflow, the pipeline, or any regulatory framework is
satisfied — the claim is structural. Substantive determinations are the
customer's auditor's, not Nanorix's.

## Why a factory (``make_capsule_activity``)

Temporal activities are registered as callables on a worker. The Nanorix
client is a runtime dependency the worker owns, not workflow input, so
the activity is produced by a factory that closes over the client. The
returned callable is decorated with ``@activity.defn(name=...)`` so the
workflow refers to it by the stable name ``"nanorix_capsule_activity"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, cast

from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    CapsuleJobResult,
    run_capsule_job,
)

# Guard the framework import. Importing this module without temporalio must
# NOT raise — the actionable error is deferred to activity construction.
try:  # pragma: no cover - exercised only when temporalio is installed
    from temporalio import activity as _activity

    _TEMPORAL_AVAILABLE = True
    _TEMPORAL_IMPORT_ERROR: Optional[Exception] = None
except Exception as _exc:  # ImportError or a transitive dependency error
    _activity = None
    _TEMPORAL_AVAILABLE = False
    _TEMPORAL_IMPORT_ERROR = _exc

ACTIVITY_NAME = "nanorix_capsule_activity"

# Single source of the remediation message so the factory and any test
# assertion agree on the wording.
TEMPORAL_IMPORT_ERROR_MESSAGE = (
    "The Nanorix Temporal activity requires temporalio, which is not "
    "installed. Install it with:\n\n    pip install 'nanorix[temporal]'\n\n"
    "(installs temporalio>=1.5). The framework-agnostic core "
    "(nanorix.integrations._orchestrator_core.run_capsule_job) needs no "
    "framework if you prefer to wire the capsule lifecycle yourself."
)

__all__ = [
    "NanorixCapsuleActivityInput",
    "NanorixCapsuleActivityResult",
    "make_capsule_activity",
    "register_capsule_activity",
    "ACTIVITY_NAME",
    "TEMPORAL_IMPORT_ERROR_MESSAGE",
]


@dataclass
class NanorixCapsuleActivityInput:
    """Workflow-supplied input for the Nanorix capsule activity.

    A serializable mirror of the customer-declared fields of
    :class:`~nanorix.integrations._orchestrator_core.CapsuleJobParams`.
    Every compliance-binding field is declared by the workflow author
    ; the activity never infers any of them.
    """

    capsulefile: str
    command: Optional[str] = None
    data_classification: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    input_files: Optional[Dict[str, bytes]] = None
    output_files: Optional[List[str]] = None
    destroy_on_complete: bool = True
    max_lifetime_seconds: Optional[int] = None
    timeout: Optional[int] = None
    idempotency_key: Optional[str] = None

    def to_params(self) -> CapsuleJobParams:
        """Build the framework-agnostic params from this workflow input."""
        return CapsuleJobParams(
            capsulefile=self.capsulefile,
            command=self.command,
            data_classification=self.data_classification,
            metadata=self.metadata,
            input_files=self.input_files,
            output_files=self.output_files,
            destroy_on_complete=self.destroy_on_complete,
            max_lifetime_seconds=self.max_lifetime_seconds,
            timeout=self.timeout,
            idempotency_key=self.idempotency_key,
        )


@dataclass
class NanorixCapsuleActivityResult:
    """Serializable result returned by the Nanorix capsule activity.

    Carries the capsule id, the shareable verification view of the
    AuditProof, the downloaded outputs (downloaded BEFORE destroy — no
    post-destroy cache), and the narrow-claim disclaimer.
    """

    capsule_id: str
    verification: Any = None
    destroyed: bool = False
    outputs: Dict[str, bytes] = field(default_factory=dict)
    disclaimer: str = ""


def _run_activity(client: Any, activity_input: NanorixCapsuleActivityInput) -> NanorixCapsuleActivityResult:
    """Core activity body — framework-free so it is unit-testable directly.

    The ``@activity.defn`` wrapper in :func:`make_capsule_activity` simply
    closes over the client and calls this. Keeping the body here means the
    Temporal-shaped path is exercised by the core test suite without
    temporalio installed.
    """
    result: CapsuleJobResult = run_capsule_job(client, activity_input.to_params())

    verification = result.verification
    verification_repr: Any = None
    if verification is not None:
        to_dict = getattr(verification, "model_dump", None) or getattr(
            verification, "dict", None
        )
        verification_repr = to_dict() if callable(to_dict) else verification

    return NanorixCapsuleActivityResult(
        capsule_id=result.capsule_id,
        verification=verification_repr,
        destroyed=result.destroyed,
        outputs=result.outputs,
        disclaimer=result.disclaimer,
    )


def make_capsule_activity(client: Any) -> Callable[..., Any]:
    """Build the Nanorix capsule Temporal activity bound to ``client``.

    Returns a callable decorated with ``@temporalio.activity.defn(
    name="nanorix_capsule_activity")`` ready to pass to a Temporal
    ``Worker(activities=[...])``. The activity closes over the supplied
    Nanorix client (a worker-owned runtime dependency, not workflow
    input).

    Args:
        client: A configured ``nanorix.Client``.

    Returns:
        The decorated async activity callable.

    Raises:
        ImportError: when temporalio is not installed.
    """
    if not _TEMPORAL_AVAILABLE:
        raise ImportError(TEMPORAL_IMPORT_ERROR_MESSAGE) from _TEMPORAL_IMPORT_ERROR

    @_activity.defn(name=ACTIVITY_NAME)
    async def nanorix_capsule_activity(
        activity_input: NanorixCapsuleActivityInput,
    ) -> NanorixCapsuleActivityResult:
        """Run one sealed Nanorix capsule as a durable Temporal activity."""
        return _run_activity(client, activity_input)

    return cast(Callable[..., Any], nanorix_capsule_activity)


def register_capsule_activity(activities: List[Any], client: Any) -> List[Any]:
    """Append the Nanorix capsule activity to a worker ``activities`` list.

    Convenience for the common worker-setup pattern:

    ```python
    activities = register_capsule_activity([], nanorix_client)
    worker = Worker(temporal_client, task_queue="rcm", activities=activities)
    ```

    Args:
        activities: The list the activity is appended to (returned for
            chaining).
        client: A configured ``nanorix.Client``.

    Returns:
        The same list with the Nanorix activity appended.

    Raises:
        ImportError: when temporalio is not installed.
    """
    activities.append(make_capsule_activity(client))
    return activities
