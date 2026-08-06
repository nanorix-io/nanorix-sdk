"""Framework-agnostic core for data-pipeline orchestrator operators.

Batch 13 (2026-05-28) — gap-closing arc: data-pipeline orchestrator
operators. This module is the pure, framework-free engine that the
Airflow operator (``nanorix.integrations.airflow``) and the Temporal
activity (``nanorix.integrations.temporal``) both call. It has NO
dependency on apache-airflow or temporalio and is fully unit-testable
with a mocked client.

The contract is a single function:

    run_capsule_job(client, params: CapsuleJobParams) -> CapsuleJobResult

which drives the existing Nanorix client surface
(``client.capsules.create`` → ``upload`` → ``execute`` → ``download`` →
``destroy``). It introduces NO new signed primitive — it consumes the
AuditProof the capsule already produces.

## Trust-boundary discipline (NON-NEGOTIABLE)

- **Customer-sole-declarer.** ``capsulefile``,
  ``data_classification`` and ``metadata`` are declared by the pipeline
  author. This core NEVER infers any of them. There are no silent
  defaults for the compliance-binding fields.
- **AuditProof external brand.** The result exposes
  ``audit_proof`` / an AuditProof. Internal subsystem names never appear
  in this surface.
- **Narrow signed claim.** ``run_capsule_job`` returns the AuditProof the
  capsule produced. It does NOT assert the pipeline (or the DAG, or the
  workflow) is correct, complete, or that any framework is satisfied. The
  claim is structural — "this capsule ran and was destroyed, here is the
  proof" — never substantive.
- **No post-destroy data exposure.** When ``output_files`` are requested,
  the core downloads them BEFORE the capsule is destroyed. Once the
  AuditProof says the capsule is destroyed, no capsule data is read. The
  download-then-destroy ordering is the operator-level enforcement of the
  capsule's zero-retention guarantee.
- **Cleanup on failure.** If ``destroy_on_complete`` is true and any step
  after ``create`` raises, the core still attempts to destroy the capsule
  so a sealed workspace is never left running because a DAG step errored.

## Why a framework-agnostic core

Airflow operators subclass ``airflow.models.BaseOperator`` and Temporal
activities are decorated with ``@temporalio.activity.defn`` — those shells
genuinely require the framework at runtime. Keeping all logic here means:

- the shells stay thin (parse the operator/activity inputs → build
  ``CapsuleJobParams`` → call ``run_capsule_job`` → return the result);
- the test suite imports only this module + a mocked client and never
  needs apache-airflow or temporalio installed;
- Dagster, Prefect, Argo, or any future orchestrator can be wired with
  the SAME core — they are documentation, not new engines.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

# Reuse the exact wire constraints + reserved-prefix discipline the
# compliance helpers already enforce. Do not re-derive these limits here.
from nanorix.compliance._constraints import (
    MAX_KEY_LENGTH,
    MAX_METADATA_KEYS,
    MAX_TOTAL_SERIALIZED,
    MAX_VALUE_LENGTH,
    estimate_serialized_size,
)

__all__ = [
    "CapsuleJobParams",
    "CapsuleJobResult",
    "OrchestratorError",
    "run_capsule_job",
    "NARROW_CLAIM_DISCLAIMER",
    "RESERVED_METADATA_PREFIX",
    "VALID_DATA_CLASSIFICATIONS",
]

# Customer-supplied keys with this prefix are rejected by the API
# (validation.rs). Only Nanorix helpers may emit reserved keys. The
# orchestrator core emits NO reserved keys — it forwards the customer's
# metadata verbatim — but it fails fast here so the rejection surfaces in
# the DAG author's stack trace rather than as an opaque HTTP 400.
RESERVED_METADATA_PREFIX = "nanorix:"

# The accepted data_classification values, mirroring the API
# (services/api/src/routes/capsules.rs). The customer declares one of
# these; Nanorix records it verbatim and never overrides it.
VALID_DATA_CLASSIFICATIONS = (
    "PHI",
    "PII",
    "financial",
    "credentials",
    "general",
)

# Structural-claim disclaimer carried on every result so a downstream
# consumer that serializes the result cannot accidentally read a
# regulatory assertion into it. The claim is "what happened", never
# "X satisfies Y".
NARROW_CLAIM_DISCLAIMER = (
    "This AuditProof attests structurally to the capsule lifecycle "
    "(create, execute, destroy) and the destruction of the sealed "
    "workspace. It does NOT assert that the pipeline, DAG, or workflow "
    "is correct, complete, or that any regulatory framework is "
    "satisfied. Substantive determinations are made by the customer's "
    "auditor, not by Nanorix."
)


class OrchestratorError(Exception):
    """Raised for invalid orchestrator job parameters.

    Job-parameter validation errors (missing capsulefile, bad
    data_classification, reserved metadata key, oversized metadata) raise
    this class. Execution-time failures from the client surface
    (BadRequestError, ExecutionError, transport errors) propagate
    unchanged so the operator/activity can surface the native SDK
    exception to the orchestrator's retry machinery.
    """


@dataclass
class CapsuleJobParams:
    """Customer-declared parameters for one capsule job step.

    Every compliance-binding field is declared by the pipeline author
    (customer-sole-declarer). The core never infers
    ``capsulefile``, ``data_classification``, or ``metadata``.

    Attributes:
        capsulefile: ID of a finalized Capsulefile (``cf_...``). Required;
            an empty value raises :class:`OrchestratorError`.
        command: Optional command to execute in the capsule. ``None`` uses
            the Capsulefile entrypoint.
        data_classification: Optional customer-declared classification of
            the data this capsule handles. One of
            :data:`VALID_DATA_CLASSIFICATIONS`. Recorded verbatim into the
            AuditProof's capsule context; never inferred or overridden.
        metadata: Optional customer-declared compliance-binding metadata
            (``Dict[str, str]``). Subject to the wire constraints (16 keys,
            64-char keys, 256-char values, 4 KB serialized). Customer-
            supplied ``nanorix:*`` keys are rejected.
        input_files: Optional mapping of ``filename -> bytes`` uploaded to
            the capsule's input directory BEFORE execution.
        output_files: Optional list of output filenames to download from
            the capsule AFTER execution and BEFORE destruction. When empty
            or ``None`` no outputs are downloaded.
        sources: Optional list of :class:`nanorix.connectors.Source`
            connectors. Each is streamed INTO the capsule BEFORE
            execution — alongside / in place of ``input_files``. The
            connector runs in the customer's process with the customer's
            cloud credentials; Nanorix never stages the data. Backward-
            compatible: ``None`` means no source connectors.
        sinks: Optional list of :class:`nanorix.connectors.Sink`
            connectors. Each receives the capsule's
            ``output_files`` and streams them to the destination BEFORE
            destroy — never after (``feedback_no_post_destroy_cache.md``).
            ``None`` means no sink connectors. When ``sinks`` is given,
            ``output_files`` names the capsule outputs streamed to every
            sink.
        connector_chunk_size: Optional bounded streaming chunk size (bytes)
            for source/sink connectors. ``None`` uses the connector
            default. No path materializes a whole object beyond one chunk.
        destroy_on_complete: When ``True`` (default) the capsule is
            destroyed after a successful run and the AuditProof is
            returned. When ``False`` the capsule is left running and no
            AuditProof is produced (the caller owns destruction).
        max_lifetime_seconds: Optional per-capsule lifetime ceiling for
            orphan reclamation. Not a product-tier limit.
        timeout: Optional per-execute timeout (seconds) forwarded to the
            exec call.
        idempotency_key: Optional idempotency key forwarded to capsule
            create so an orchestrator task retry never double-creates a
            capsule. When omitted the client auto-generates one.
    """

    capsulefile: str
    command: Optional[str] = None
    data_classification: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    input_files: Optional[Dict[str, bytes]] = None
    output_files: Optional[List[str]] = None
    sources: Optional[List[Any]] = None
    sinks: Optional[List[Any]] = None
    connector_chunk_size: Optional[int] = None
    destroy_on_complete: bool = True
    max_lifetime_seconds: Optional[int] = None
    timeout: Optional[int] = None
    idempotency_key: Optional[str] = None

    def validate(self) -> None:
        """Validate the customer-declared parameters; raise on violation.

        Fails fast so a misconfigured DAG step surfaces the error in the
        author's stack trace at submit time rather than as an opaque HTTP
        error mid-pipeline.

        Raises:
            OrchestratorError: capsulefile empty, data_classification not
                in the accepted set, or metadata violates the wire
                constraints / reserved-prefix discipline.
        """
        if not self.capsulefile or not str(self.capsulefile).strip():
            raise OrchestratorError(
                "CapsuleJobParams.capsulefile is required (the finalized "
                "Capsulefile id, e.g. 'cf_...'). The pipeline author "
                "declares it explicitly; it is never inferred."
            )

        if self.data_classification is not None:
            if self.data_classification not in VALID_DATA_CLASSIFICATIONS:
                raise OrchestratorError(
                    "data_classification must be one of "
                    f"{VALID_DATA_CLASSIFICATIONS}; got "
                    f"{self.data_classification!r}. Nanorix records the "
                    "customer-declared classification verbatim and never "
                    "infers or overrides it."
                )

        if self.metadata is not None:
            _validate_metadata(self.metadata)


def _validate_metadata(metadata: Mapping[str, str]) -> None:
    """Mirror ``validate_metadata`` (validation.rs) for fail-fast errors.

    Reproduces the exact wire constraints and the reserved-prefix
    discipline. Does NOT loosen them client-side.
    """
    if len(metadata) > MAX_METADATA_KEYS:
        raise OrchestratorError(
            f"metadata has {len(metadata)} keys; the API accepts at most "
            f"{MAX_METADATA_KEYS}."
        )
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise OrchestratorError(
                "metadata must be a Dict[str, str]; got a non-string key "
                f"or value for key {key!r}."
            )
        if key.startswith(RESERVED_METADATA_PREFIX):
            raise OrchestratorError(
                f"metadata key {key!r} uses the reserved "
                f"{RESERVED_METADATA_PREFIX!r} prefix. Customer-supplied "
                "reserved keys are rejected by the API; use a customer-"
                "namespaced key instead."
            )
        if len(key) > MAX_KEY_LENGTH:
            raise OrchestratorError(
                f"metadata key {key!r} exceeds the {MAX_KEY_LENGTH}-char "
                f"limit (got {len(key)} chars)."
            )
        if len(value) > MAX_VALUE_LENGTH:
            raise OrchestratorError(
                f"metadata value for key {key!r} exceeds the "
                f"{MAX_VALUE_LENGTH}-char limit (got {len(value)} chars)."
            )
    serialized = estimate_serialized_size(dict(metadata))
    if serialized > MAX_TOTAL_SERIALIZED:
        raise OrchestratorError(
            f"metadata serializes to {serialized} bytes; the API limit is "
            f"{MAX_TOTAL_SERIALIZED} bytes."
        )


@dataclass
class CapsuleJobResult:
    """Result of one capsule job step.

    Attributes:
        capsule_id: The id of the capsule that ran.
        exec_result: The raw execution result returned by the client's
            ``execute`` call (exit_code, stdout, stderr, duration, ...).
        outputs: Mapping of ``filename -> bytes`` for every requested
            output file, downloaded BEFORE destruction. Empty when no
            outputs were requested. After this result is returned the
            capsule is destroyed (when ``destroy_on_complete``); the bytes
            here are the only copy the caller will ever see — there is no
            post-destroy cache.
        audit_proof: The AuditProof (``CdpAccess``) the capsule produced on
            destruction, or ``None`` when ``destroy_on_complete`` was
            ``False`` (the capsule is still running and owns its own
            destruction). This is the AuditProof brand surface,
            never an internal subsystem object.
        verification: The shareable verification view of the AuditProof
            (``audit_proof.verification()``), or ``None`` when the capsule
            was not destroyed. Safe to hand to an auditor.
        destroyed: ``True`` when the core destroyed the capsule.
        disclaimer: The narrow-claim disclaimer
            (:data:`NARROW_CLAIM_DISCLAIMER`). Carried so any serializer of
            this result cannot read a substantive/regulatory assertion into
            a structural proof.
    """

    capsule_id: str
    exec_result: Any
    outputs: Dict[str, bytes] = field(default_factory=dict)
    audit_proof: Any = None
    verification: Any = None
    destroyed: bool = False
    disclaimer: str = NARROW_CLAIM_DISCLAIMER


def run_capsule_job(client: Any, params: CapsuleJobParams) -> CapsuleJobResult:
    """Run one capsule job step and return its AuditProof.

    Lifecycle: ``create`` → (optional ``upload`` of ``input_files``) →
    ``execute`` → (optional ``download`` of ``output_files`` — BEFORE
    destroy) → (optional ``destroy``) → return :class:`CapsuleJobResult`.

    This is the single engine behind the Airflow operator and the Temporal
    activity. It has NO framework dependency.

    Ordering invariants (enforced and tested):

    - **Upload before execute.** Input files are written into the input
      directory before the command runs.
    - **Download before destroy.** Output files are downloaded while the
      capsule is alive. Once the AuditProof says the capsule is destroyed,
      no capsule data is read. There is no post-destroy cache.
    - **Cleanup on failure.** If anything after ``create`` raises and
      ``destroy_on_complete`` is true, the capsule is still destroyed so a
      sealed workspace is never orphaned by a failed step. The original
      exception is re-raised.

    Args:
        client: A Nanorix client (``nanorix.Client``) or any object that
            exposes the same ``capsules.create(...)`` →
            ``LiveCapsule.upload/execute/download/destroy`` surface. In
            tests this is a mock; no framework is required.
        params: The customer-declared :class:`CapsuleJobParams`.

    Returns:
        A :class:`CapsuleJobResult` carrying the exec result, any
        downloaded outputs, and (when destroyed) the AuditProof +
        shareable verification view.

    Raises:
        OrchestratorError: invalid job parameters (validated up front).
        Exception: any native client/transport/execution exception is
            propagated unchanged after best-effort capsule cleanup.
    """
    params.validate()

    create_kwargs = _resolve_create_kwargs(client, params)
    capsule = client.capsules.create(**create_kwargs)
    capsule_id = capsule.id

    try:
        # 1. Upload inputs BEFORE execution.
        if params.input_files:
            for filename, data in params.input_files.items():
                capsule.upload(filename, data)

        # 1b. Stream every SOURCE connector INTO the capsule BEFORE
        # execution. Runs in the customer's process with the
        #     customer's cloud credentials; Nanorix never stages the data.
        if params.sources:
            _stream_sources(client, capsule, params)

        # 2. Execute.
        exec_result = capsule.execute(
            command=params.command,
            timeout=params.timeout,
        )

        # 3. Download outputs BEFORE destroy — no-post-destroy-cache.
        outputs: Dict[str, bytes] = {}
        if params.output_files:
            for filename in params.output_files:
                outputs[filename] = capsule.download(filename)

        # 3b. Stream capsule OUTPUT to every SINK connector BEFORE destroy
        # (feedback_no_post_destroy_cache.md). Sinks run
        #     while the capsule is still alive; once destroyed there is no
        #     capsule data left to read.
        if params.sinks:
            _stream_sinks(client, capsule, params)

        # 4. Destroy (optional) and capture the AuditProof.
        if params.destroy_on_complete:
            audit_proof = capsule.destroy()
            return CapsuleJobResult(
                capsule_id=capsule_id,
                exec_result=exec_result,
                outputs=outputs,
                audit_proof=audit_proof,
                verification=_safe_verification(audit_proof),
                destroyed=True,
            )

        return CapsuleJobResult(
            capsule_id=capsule_id,
            exec_result=exec_result,
            outputs=outputs,
            audit_proof=None,
            verification=None,
            destroyed=False,
        )

    except Exception:
        # Cleanup: never leave a sealed workspace running because a step
        # failed. Best-effort — swallow cleanup errors, re-raise original.
        if params.destroy_on_complete:
            try:
                capsule.destroy()
            except Exception:
                pass
        raise


def _resolve_create_kwargs(client: Any, params: CapsuleJobParams) -> Dict[str, Any]:
    """Build create kwargs, resolving the ``data_classification`` channel.

    Inspects the bound ``client.capsules.create`` signature. When it
    accepts ``data_classification`` the value is forwarded as a dedicated
    create field; otherwise it is folded into a customer-namespaced
    metadata key. Either way the customer-declared value binds into the
    AuditProof and is never inferred.
    """
    create_kwargs: Dict[str, Any] = {"capsulefile": params.capsulefile}
    if params.max_lifetime_seconds is not None:
        create_kwargs["max_lifetime_seconds"] = params.max_lifetime_seconds
    if params.idempotency_key is not None:
        create_kwargs["idempotency_key"] = params.idempotency_key

    metadata: Dict[str, str] = dict(params.metadata) if params.metadata else {}

    if params.data_classification is not None:
        if _accepts_data_classification(client):
            create_kwargs["data_classification"] = params.data_classification
        else:
            metadata.setdefault("data_classification", params.data_classification)
            _validate_metadata(metadata)

    if metadata:
        create_kwargs["metadata"] = metadata
    return create_kwargs


def _accepts_data_classification(client: Any) -> bool:
    """True iff ``client.capsules.create`` declares ``data_classification``."""
    try:
        sig = inspect.signature(client.capsules.create)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "data_classification" in params:
        return True
    # A ``**kwargs`` create accepts anything.
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _safe_verification(audit_proof: Any) -> Any:
    """Return the shareable verification view, tolerating shapes without it."""
    verification = getattr(audit_proof, "verification", None)
    if callable(verification):
        try:
            return verification()
        except Exception:
            return None
    return None


def _stream_sources(client: Any, capsule: Any, params: CapsuleJobParams) -> None:
    """Stream every source connector INTO the capsule (before exec).

    Imported lazily so the orchestrator core keeps importing without the
    connectors package being loaded eagerly. The connector streaming core
    enforces the bounded-chunk / no-Nanorix-staging discipline.
    """
    from nanorix.connectors import stream_source_into_capsule

    chunk_kwargs: Dict[str, Any] = {}
    if params.connector_chunk_size is not None:
        chunk_kwargs["chunk_size"] = params.connector_chunk_size
    for source in params.sources or []:
        stream_source_into_capsule(client, capsule, source, **chunk_kwargs)


def _stream_sinks(client: Any, capsule: Any, params: CapsuleJobParams) -> None:
    """Stream capsule OUTPUT to every sink connector (BEFORE destroy).

    The capsule's ``output_files`` name the outputs streamed to each sink.
    Enforced ordering: this runs while the capsule is still alive, before
    ``destroy`` (``feedback_no_post_destroy_cache.md``).
    """
    from nanorix.connectors import stream_capsule_to_sink

    output_files = params.output_files or []
    if not output_files:
        return
    chunk_kwargs: Dict[str, Any] = {}
    if params.connector_chunk_size is not None:
        chunk_kwargs["chunk_size"] = params.connector_chunk_size
    for sink in params.sinks or []:
        stream_capsule_to_sink(client, capsule, output_files, sink, **chunk_kwargs)
