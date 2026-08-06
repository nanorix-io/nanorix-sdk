"""Capsules resource — sync and async implementations."""

from __future__ import annotations
import uuid as _uuid
from typing import Any, BinaryIO, Callable, Dict, Iterable, List, Optional, Tuple, Union, cast

from nanorix._batch import (
    BatchRecord,
    BatchResult,
    compute_batch_merkle_root,
    parse_results_jsonl,
    serialize_records_to_jsonl,
)
from nanorix._constants import DEFAULT_EXECUTE_TIMEOUT, DEFAULT_UPLOAD_TIMEOUT
from nanorix._exceptions import BadRequestError, ExecutionError
from nanorix._http import AsyncHttpTransport, HttpTransport
from nanorix._models import (
    Capsule,
    CapsuleList,
    CdpAccess,
    ExecutionResult,
    FileInfo,
)
from nanorix._session import (
    SessionResult,
    build_session_metadata,
    hash_state_bytes,
)
from nanorix.capsule_record import (
    AsyncRecordContext,
    RecordContext,
    build_parent_link_body,
    validate_parent_link_fields,
)


class LiveCapsule:
    """
    A running capsule with workspace methods.
    Returned by client.capsules.create().
    """

    def __init__(self, capsule: Capsule, http: HttpTransport) -> None:
        self._capsule = capsule
        self._http = http

    @property
    def id(self) -> str:
        return self._capsule.id

    @property
    def status(self) -> str:
        return self._capsule.status

    def upload(
        self,
        filename: str,
        data: Union[bytes, BinaryIO],
    ) -> FileInfo:
        """Upload a file to the capsule's input directory."""
        content = data if isinstance(data, bytes) else data.read()
        result = self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/upload",
            data=content,
            headers={"Content-Type": "application/octet-stream"},
            params={"filename": filename},
            timeout=DEFAULT_UPLOAD_TIMEOUT,
        )
        return FileInfo.model_validate(result)

    def execute(
        self,
        command: Optional[str] = None,
        *,
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """
        Execute a command in the capsule.
        If no command provided, uses the capsulefile entrypoint.
        """
        body: Dict[str, Any] = {}
        if command is not None:
            body["command"] = command
        if timeout is not None:
            body["timeout"] = timeout

        result = self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/exec",
            json=body if body else None,
            timeout=DEFAULT_EXECUTE_TIMEOUT,
        )
        exec_result = ExecutionResult.model_validate(result)

        if exec_result.exit_code != 0:
            raise ExecutionError(
                f"Command exited with code {exec_result.exit_code}",
                exit_code=exec_result.exit_code,
                stdout=exec_result.stdout,
                stderr=exec_result.stderr,
            )

        return exec_result

    def download(self, filename: str) -> bytes:
        """Download an output file from the capsule."""
        return cast(bytes, self._http.request(
                        "GET",
                        f"/v1/capsules/{self.id}/output/{filename}",
                        raw_response=True,
                    ))

    def list_outputs(self) -> List[FileInfo]:
        """List all output files in the capsule."""
        result = self._http.request("GET", f"/v1/capsules/{self.id}/output")
        files_data = result.get("files", result) if isinstance(result, dict) else result
        return [FileInfo.model_validate(f) for f in files_data]

    def destroy(self) -> CdpAccess:
        """
        Destroy the capsule and return the CDP.
        After this call, the capsule is gone. Only the proof remains.
        """
        result = self._http.request("DELETE", f"/v1/capsules/{self.id}")
        return CdpAccess(
            full_data=result.get("cdp", result),
            verification_data=result.get("verification_cdp", {}),
            capsule_id=self.id,
        )

    def refresh(self) -> "LiveCapsule":
        """Refresh capsule status from API."""
        result = self._http.request("GET", f"/v1/capsules/{self.id}")
        self._capsule = Capsule.model_validate(result)
        return self

    # ── Wave A consumers ───────────────────────────

    def record(
        self,
        record_id: str,
        pattern_tag: Optional[str] = None,
    ) -> RecordContext:
        """Open a per-record receipt context.

        Per-record receipts buffer server-side until destroy and bind into
        the AuditProof's outer 8-step chain via Merkle root incorporation
        at Step 8 (the canonical verifier).

        The receipt carries `pattern_tag` (signed primitive). **Control IDs
        are NEVER stamped into the signed receipt.** Adapters apply the
         published mapping artifact (`nanorix.control_map`) at
        ingestion time to derive `(jurisdiction × classification ×
        pattern_tag) -> list[control_id]`. Three-layer separation: receipt
        (immutable) / mapping (versioned) / adapter (vendor pace).

        Args:
            record_id: Customer-declared opaque identifier (record-domain
                key — PA ID, claim ID, study UID, molecule ID). Field
                length cap: 256 chars (
                the Nanorix API service).
            pattern_tag: Optional closed-enum workflow pattern tag. Must
                be one of the 15 canonical variants in
                `nanorix.capsule_record.PATTERN_TAGS`
                §"pattern_tag rule" (case-sensitive snake_case wire form
                matching the Rust serde rename_all="snake_case" output).

        Returns:
            A `RecordContext` context manager. On `__exit__` (without
            exception), the buffered receipt is POSTed to
            `/v1/capsules/:id/record`. Access `.record_chain_hash` +
            `.record_index` after the context exits for the server-
            computed values.

        Example:
            >>> with capsule.record(record_id="pa_123", pattern_tag="pa") as r:
            ...     result = process_pa(pa)
            ...     r.bind_output(result)
            ...     r.event({"type": "ReasoningStepCompleted",
            ...              "step_id": "ddx",
            ...              "summary": "..."})
        """
        return RecordContext(
            capsule_id=self.id,
            record_id=record_id,
            pattern_tag=pattern_tag,
            post_callback=self._post_record,
        )

    def _post_record(self, capsule_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Internal — POST to `/v1/capsules/:id/record`. Used by RecordContext."""
        result = self._http.request(
            "POST",
            f"/v1/capsules/{capsule_id}/record",
            json=body,
        )
        return result if isinstance(result, dict) else {}

    def add_parent_proof(
        self,
        parent_chain_hash: str,
        parent_key_id: str,
        parent_signature: str,
        parent_role: Optional[str] = None,
        parent_jurisdiction: Optional[str] = None,
        parent_organization_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Declare a cross-org parent-proof link.

        Parent links buffer server-side until destroy and bind into the
        AuditProof's outer 8-step chain via Merkle root incorporation at
        Step 8 alongside record receipts. Parent signatures verify
        independently of Nanorix accounts via Portable Pubkey Bundles
        .

        Each link attests to lineage, NOT to compliance —
        three-role liability split. The auditor adjudicates substantive
        satisfaction; receipts carry verified-lineage only.

        Args:
            parent_chain_hash: SHA-512 chain hash of the upstream
                AuditProof's Step 8. Prefix format: `sha512:{hex}`. Field
                length cap: 144 chars.
            parent_key_id: Authority key ID of the parent's signer.
                Canonical formats: `nrx-verify-{terminated_at}-{cdp_id[..8]}`
                (Nanorix-managed) OR `cust-auth-{authority_id}` (customer-
                rooted). Length cap: 256 chars.
            parent_signature: Ed25519 signature of parent's signer over
                `parent_chain_hash`. Prefix format: `base64:{b64}`. Length
                cap: 256 chars.
            parent_role: Optional opaque role tag (e.g.,
                `"input-data-source"`, `"rag-retrieval"`,
                `"safety-review"`). Length cap: 128 chars.
            parent_jurisdiction: Optional ISO-3166 / region tag for the
                parent's signing authority (e.g., `"US"`, `"EU"`). Length
                cap: 16 chars.
            parent_organization_tag: Optional opaque organization
                identifier (e.g., `"vendor:banjo-health"`,
                `"customer:anterior"`). Length cap: 256 chars.

        Returns:
            The server response dict, containing `capsule_id`,
            `parent_chain_hash`, `buffered_count`, and a `disclaimer`.

        Raises:
            BadRequestError: If any field-length cap is violated or a
                required field is empty.
        """
        validate_parent_link_fields(
            parent_chain_hash=parent_chain_hash,
            parent_key_id=parent_key_id,
            parent_signature=parent_signature,
            parent_role=parent_role,
            parent_jurisdiction=parent_jurisdiction,
            parent_organization_tag=parent_organization_tag,
        )
        body = build_parent_link_body(
            parent_chain_hash=parent_chain_hash,
            parent_key_id=parent_key_id,
            parent_signature=parent_signature,
            parent_role=parent_role,
            parent_jurisdiction=parent_jurisdiction,
            parent_organization_tag=parent_organization_tag,
        )
        result = self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/parent",
            json=body,
        )
        return result if isinstance(result, dict) else {}


class Capsules:
    """Synchronous capsule operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def create(
        self,
        *,
        capsulefile: Optional[str] = None,
        max_lifetime_seconds: Optional[int] = None,
        egress_profile: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        cdp_kind: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> LiveCapsule:
        """Create a new capsule. Returns a LiveCapsule with workspace methods.

        ``metadata`` binds compliance-binding context into the AuditProof's
        ``metadata_hash``. Use:mod:`nanorix.compliance` to
        construct typed framework context (HIPAA, GDPR, SOC 2) or pass a
        plain ``Dict[str, str]``. Server constraints: 16 keys max, 64-char
        keys, 256-char values, 4 KB total. ``nanorix:*`` keys are reserved
        for helper-emitted reserved keys; customer-supplied
        ``nanorix:*`` keys are rejected by the API.

        ``cdp_kind`` is the reserved-slot scope discriminator
        (Wave 16-A landed; Wave 18-11 ungated). Accepted snake_case
        values: ``"workload"`` (capsule-lifecycle default — pre-Wave-18-11
        back-compat None), ``"call"`` (sealed-proxy per-LLM-call scope),
        ``"request"`` (sealed-middleware per-HTTP-request scope),
        ``"batch"`` (high-volume per-record scope). Customer-sole-declarer
         — Nanorix records verbatim. Populating cdp_kind
        SHIFTS the AuditProof's canonical_hash (correct Forever-Standard
        semantics: declaring a scope binds it into the signature).

        ``idempotency_key`` is optional. When omitted the SDK auto-generates a
        UUIDv4 ``Idempotency-Key`` header so a transient network error +
        SDK-level retry never silently double-creates a capsule (per Wave
        18-7 / CTI-T7-004; Stripe-pattern 24h dedup per Migration 058).
        Pass an explicit value to scope idempotency under your own dedup
        contract (e.g. one Idempotency-Key per business workflow id).
        """
        body: Dict[str, Any] = {}
        if capsulefile is not None:
            body["capsulefile_id"] = capsulefile
        if max_lifetime_seconds is not None:
            body["max_lifetime_seconds"] = max_lifetime_seconds
        if egress_profile is not None:
            body["egress_profile"] = egress_profile
        if metadata is not None:
            body["metadata"] = metadata
        if cdp_kind is not None:
            body["cdp_kind"] = cdp_kind

        key = idempotency_key if idempotency_key is not None else str(_uuid.uuid4())
        result = self._http.request(
            "POST",
            "/v1/capsules",
            json=body,
            headers={"Idempotency-Key": key},
        )
        capsule = Capsule.model_validate(result)
        return LiveCapsule(capsule, self._http)

    def run(
        self,
        *,
        capsulefile: str,
        inputs: Optional[Dict[str, Union[bytes, BinaryIO]]] = None,
        command: Optional[str] = None,
        timeout: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, bytes], CdpAccess]:
        """
        One-shot processing: create -> upload -> execute -> download -> destroy.

        Returns (outputs, cdp) where:
        - outputs: Dict[str, bytes] mapping filename -> content
        - cdp: CdpAccess with .full() and .verification()

        ``metadata`` flows through to the underlying ``create()`` call; see
        that method for binding semantics.
        """
        capsule = self.create(
            capsulefile=capsulefile,
            max_lifetime_seconds=max_lifetime_seconds,
            metadata=metadata,
        )

        try:
            if inputs:
                for filename, data in inputs.items():
                    capsule.upload(filename, data)

            capsule.execute(command=command, timeout=timeout)

            output_list = capsule.list_outputs()
            outputs: Dict[str, bytes] = {}
            for f in output_list:
                outputs[f.filename] = capsule.download(f.filename)

            cdp = capsule.destroy()
            return outputs, cdp

        except ExecutionError as e:
            try:
                cdp = capsule.destroy()
                e.cdp = cdp.full()
            except Exception:
                pass
            raise

        except Exception:
            try:
                capsule.destroy()
            except Exception:
                pass
            raise

    def batch(
        self,
        *,
        capsulefile: str,
        records: Iterable[Dict[str, Any]],
        on_record: Optional[Callable[[BatchRecord], None]] = None,
        record_id_field: str = "custom_id",
        on_record_error: str = "fail_isolated",
        max_lifetime_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> BatchResult:
        """Batch processing — Anthropic-Batches inline shape (Gap 1).

        Per Agent A research (2026-05-05): adopted shape mirrors
        Anthropic Message Batches `requests[]` inline format and SQS
        `ReportBatchItemFailures` per-record outcome semantics. Each
        record MUST carry a unique `custom_id` for inclusion-proof
        stability.

        Args:
            capsulefile: ID of a Capsulefile that has `batch_config`
                declared (per Migration 041). The customer's entrypoint
                is responsible for reading `/data/input/records.jsonl`
                and writing `/data/output/results.jsonl`.
            records: Iterable of dicts; each must have a non-empty
                `record_id_field` value (default ``"custom_id"``).
                Duplicates raise BadRequestError.
            on_record: Optional callback invoked once per processed
                record after capsule destroy (parses out of
                results.jsonl). Useful for progress tracking on large
                batches.
            record_id_field: Name of the per-record identifier key.
                Default ``"custom_id"`` matches Anthropic Batches +
                OpenAI Batch convention.
            on_record_error: ``"fail_isolated"`` (default) — record
                errored outcome and continue; ``"abort_batch"`` — raise
                ExecutionError on first error. Mirrors the Capsulefile
                ``batch.on_record_error`` config (Migration 041
                validator domain).
            max_lifetime_seconds: Capsule lifetime ceiling. Capped by
                Capsulefile.max_duration_seconds (Migration 040 / Gap 4).
            metadata: Compliance-binding metadata.
            timeout: Per-execute timeout (forwarded to /v1/capsules/{id}/exec).

        Returns:
            BatchResult with merkle_root + audit_proof + per-record
            outcomes. `merkle_root` is computed CLIENT-SIDE per the
            same RFC 6962 formula as the sealed runtime —
            cross-implementation byte-identical so verifiers can
            replay independently.

        Raises:
            BadRequestError: Records missing `custom_id`, duplicate IDs,
                or empty iterable.
            ExecutionError: Customer entrypoint exited non-zero, OR
                ``on_record_error="abort_batch"`` and any record errored.

        Customer entrypoint contract (V1 SDK-driven; runtime auto-iterate
        ships in a follow-up batch):

            # records.jsonl input — one JSON per line, each has custom_id
            # results.jsonl output — one JSON per line, each has:
            #   {"custom_id": "...", "status": "succeeded"|"errored",
            #    "output": ...               # if succeeded
            #    "error_code": "...",        # if errored
            #    "error_message": "..."}     # if errored
        """
        if on_record_error not in ("fail_isolated", "abort_batch"):
            raise BadRequestError(
                f"on_record_error must be 'fail_isolated' or 'abort_batch'; "
                f"got {on_record_error!r}",
                status_code=400,
            )

        records_list = list(records)
        if not records_list:
            raise BadRequestError(
                "Capsule.batch() requires at least one record",
                status_code=400,
            )

        records_jsonl = serialize_records_to_jsonl(records_list, record_id_field)

        capsule = self.create(
            capsulefile=capsulefile,
            max_lifetime_seconds=max_lifetime_seconds,
            metadata=metadata,
        )

        try:
            capsule.upload("records.jsonl", records_jsonl)
            capsule.execute(timeout=timeout)

            # Customer's entrypoint must have written results.jsonl.
            try:
                results_data = capsule.download("results.jsonl")
            except Exception as e:
                raise ExecutionError(
                    f"Customer entrypoint did not produce results.jsonl: {e}",
                    exit_code=0,
                    stdout="",
                    stderr="",
                ) from e

            batch_records = parse_results_jsonl(
                results_data, records_jsonl, record_id_field=record_id_field
            )

            success_count = sum(1 for r in batch_records if r.ok)
            error_count = sum(1 for r in batch_records if not r.ok)

            if on_record_error == "abort_batch" and error_count > 0:
                first_failure = next(r for r in batch_records if not r.ok)
                # Still destroy and surface the CDP via the exception.
                cdp = capsule.destroy()
                err = ExecutionError(
                    f"Batch aborted on first record error "
                    f"(on_record_error='abort_batch'): "
                    f"{first_failure.custom_id} → {first_failure.error_code}",
                    exit_code=0,
                    stdout="",
                    stderr=first_failure.error_message or "",
                )
                err.cdp = cdp.full()
                raise err

            if on_record is not None:
                for r in batch_records:
                    on_record(r)

            cdp = capsule.destroy()
            merkle_root = compute_batch_merkle_root(batch_records)

            return BatchResult(
                merkle_root=merkle_root,
                audit_proof=cdp,
                success_count=success_count,
                error_count=error_count,
                total_count=len(batch_records),
                _results=batch_records,
            )

        except ExecutionError:
            try:
                capsule.destroy()
            except Exception:
                pass
            raise
        except Exception:
            try:
                capsule.destroy()
            except Exception:
                pass
            raise

    def session(
        self,
        *,
        capsulefile: str,
        session_id: str,
        parent_cdp_id: Optional[str] = None,
        initial_state: Optional[bytes] = None,
        max_lifetime_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> SessionResult:
        """Session-mode processing — LangGraph-shape memory artifact (Gap 2).

        Per Agent A research (2026-05-05): adopted shape mirrors LangGraph
        CheckpointSaver (`thread_id` semantics, `parent_config` chain) +
        AWS Bedrock AgentCore CMK-encrypted artifact posture. Continuity
        invariant enforced via parent_cdp_id chain + artifact hash binding
        in metadata.

        V1 SDK-driven flow (server-side runtime auto-mount in subsequent
        batches):
            1. Customer pre-fetches state.bin from their artifact_uri +
               decrypts via their KMS BEFORE passing bytes here.
            2. SDK uploads state.bin → /data/input/state.bin.
            3. SDK creates capsule with metadata bindings (session_id +
               parent_cdp_id + artifact_input_hash).
            4. Customer entrypoint reads /data/input/state.bin + writes
               /data/output/state.bin.
            5. SDK downloads new state.bin, destroys → ONE CdpAccess.
            6. Customer re-encrypts new state with their KMS + persists
               to next-session artifact_uri.

        Args:
            capsulefile: ID of a Capsulefile that has `memory_config`
                declared (per Migration 042).
            session_id: Customer-supplied session ID. Mirrors LangGraph
                `thread_id`; multiple capsules in a chain share this.
            parent_cdp_id: Previous session's CDP ID. None = first session.
                Verifier replays the chain by fetching each parent CDP.
            initial_state: Bytes of the prior session's state output
                (decrypted by customer). None = empty initial state
                (first session in chain).
            max_lifetime_seconds: Capsule lifetime ceiling.
            metadata: Compliance-binding metadata; merged underneath the
                reserved `nanorix:` session bindings.
            timeout: Per-execute timeout.

        Returns:
            SessionResult with state_out + artifact_input_hash +
            artifact_output_hash + audit_proof. Customer should persist
            artifact_output_hash for the next session in the chain.

        Raises:
            ExecutionError: Customer entrypoint exited non-zero.
            BadRequestError: Invalid session_id (empty).
        """
        if not session_id:
            raise BadRequestError(
                "Capsule.session() requires non-empty session_id "
                "(matches LangGraph thread_id convention)",
                status_code=400,
            )

        state_bytes = initial_state if initial_state is not None else b""
        artifact_input_hash = hash_state_bytes(state_bytes)

        session_metadata = build_session_metadata(
            session_id=session_id,
            parent_cdp_id=parent_cdp_id,
            artifact_input_hash=artifact_input_hash,
            extra=metadata,
        )

        capsule = self.create(
            capsulefile=capsulefile,
            max_lifetime_seconds=max_lifetime_seconds,
            metadata=session_metadata,
        )

        try:
            capsule.upload("state.bin", state_bytes)
            capsule.execute(timeout=timeout)

            try:
                state_out = capsule.download("state.bin")
            except Exception as e:
                raise ExecutionError(
                    f"Customer entrypoint did not produce state.bin: {e}",
                    exit_code=0,
                    stdout="",
                    stderr="",
                ) from e

            artifact_output_hash = hash_state_bytes(state_out)
            cdp = capsule.destroy()

            return SessionResult(
                state_out=state_out,
                artifact_input_hash=artifact_input_hash,
                artifact_output_hash=artifact_output_hash,
                audit_proof=cdp,
                session_id=session_id,
            )

        except ExecutionError:
            try:
                capsule.destroy()
            except Exception:
                pass
            raise
        except Exception:
            try:
                capsule.destroy()
            except Exception:
                pass
            raise

    def list(self, **kwargs: Any) -> CapsuleList:
        """List capsules. Supports ?status= query param."""
        result = self._http.request("GET", "/v1/capsules", params=kwargs or None)
        return CapsuleList.model_validate(result)

    def get(self, capsule_id: str) -> Capsule:
        """Get a capsule by ID."""
        result = self._http.request("GET", f"/v1/capsules/{capsule_id}")
        return Capsule.model_validate(result)


class AsyncLiveCapsule:
    """Async running capsule with workspace methods."""

    def __init__(self, capsule: Capsule, http: AsyncHttpTransport) -> None:
        self._capsule = capsule
        self._http = http

    @property
    def id(self) -> str:
        return self._capsule.id

    @property
    def status(self) -> str:
        return self._capsule.status

    async def upload(
        self,
        filename: str,
        data: Union[bytes, BinaryIO],
    ) -> FileInfo:
        """Upload a file to the capsule's input directory (async)."""
        content = data if isinstance(data, bytes) else data.read()
        result = await self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/upload",
            data=content,
            headers={"Content-Type": "application/octet-stream"},
            params={"filename": filename},
            timeout=DEFAULT_UPLOAD_TIMEOUT,
        )
        return FileInfo.model_validate(result)

    async def execute(
        self,
        command: Optional[str] = None,
        *,
        timeout: Optional[int] = None,
    ) -> ExecutionResult:
        """Execute a command in the capsule (async)."""
        body: Dict[str, Any] = {}
        if command is not None:
            body["command"] = command
        if timeout is not None:
            body["timeout"] = timeout

        result = await self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/exec",
            json=body if body else None,
            timeout=DEFAULT_EXECUTE_TIMEOUT,
        )
        exec_result = ExecutionResult.model_validate(result)

        if exec_result.exit_code != 0:
            raise ExecutionError(
                f"Command exited with code {exec_result.exit_code}",
                exit_code=exec_result.exit_code,
                stdout=exec_result.stdout,
                stderr=exec_result.stderr,
            )

        return exec_result

    async def download(self, filename: str) -> bytes:
        """Download an output file from the capsule (async)."""
        return cast(bytes, await self._http.request(
                        "GET",
                        f"/v1/capsules/{self.id}/output/{filename}",
                        raw_response=True,
                    ))

    async def list_outputs(self) -> List[FileInfo]:
        """List all output files in the capsule (async)."""
        result = await self._http.request("GET", f"/v1/capsules/{self.id}/output")
        files_data = result.get("files", result) if isinstance(result, dict) else result
        return [FileInfo.model_validate(f) for f in files_data]

    async def destroy(self) -> CdpAccess:
        """Destroy the capsule and return the CDP (async)."""
        result = await self._http.request("DELETE", f"/v1/capsules/{self.id}")
        return CdpAccess(
            full_data=result.get("cdp", result),
            verification_data=result.get("verification_cdp", {}),
            capsule_id=self.id,
        )

    async def refresh(self) -> "AsyncLiveCapsule":
        """Refresh capsule status from API (async)."""
        result = await self._http.request("GET", f"/v1/capsules/{self.id}")
        self._capsule = Capsule.model_validate(result)
        return self

    # ── Wave A consumers (async parity) ────────────

    def record(
        self,
        record_id: str,
        pattern_tag: Optional[str] = None,
    ) -> AsyncRecordContext:
        """Open a per-record receipt context (async —).

        Async mirror of `LiveCapsule.record(...)`. Returns an
        `AsyncRecordContext` usable in `async with`. See the synchronous
        docstring for full semantics + the three-layer separation
        discipline (signed receipt / published mapping / adapter).
        """
        return AsyncRecordContext(
            capsule_id=self.id,
            record_id=record_id,
            pattern_tag=pattern_tag,
            post_callback=self._post_record_async,
        )

    async def _post_record_async(
        self, capsule_id: str, body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Internal — async POST to `/v1/capsules/:id/record`."""
        result = await self._http.request(
            "POST",
            f"/v1/capsules/{capsule_id}/record",
            json=body,
        )
        return result if isinstance(result, dict) else {}

    async def add_parent_proof(
        self,
        parent_chain_hash: str,
        parent_key_id: str,
        parent_signature: str,
        parent_role: Optional[str] = None,
        parent_jurisdiction: Optional[str] = None,
        parent_organization_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Declare a cross-org parent-proof link (async —).

        Async mirror of `LiveCapsule.add_parent_proof(...)`. Same field-
        length validation + endpoint contract; see the synchronous
        docstring for full semantics + three-role liability
        discipline (parent_proof_hashes attest to lineage, not to
        compliance).
        """
        validate_parent_link_fields(
            parent_chain_hash=parent_chain_hash,
            parent_key_id=parent_key_id,
            parent_signature=parent_signature,
            parent_role=parent_role,
            parent_jurisdiction=parent_jurisdiction,
            parent_organization_tag=parent_organization_tag,
        )
        body = build_parent_link_body(
            parent_chain_hash=parent_chain_hash,
            parent_key_id=parent_key_id,
            parent_signature=parent_signature,
            parent_role=parent_role,
            parent_jurisdiction=parent_jurisdiction,
            parent_organization_tag=parent_organization_tag,
        )
        result = await self._http.request(
            "POST",
            f"/v1/capsules/{self.id}/parent",
            json=body,
        )
        return result if isinstance(result, dict) else {}


class AsyncCapsules:
    """Asynchronous capsule operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def create(
        self,
        *,
        capsulefile: Optional[str] = None,
        max_lifetime_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        cdp_kind: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> AsyncLiveCapsule:
        """Create a new capsule (async). Returns an AsyncLiveCapsule.

        ``metadata`` and ``cdp_kind`` semantics match the synchronous
        ``Capsules.create()`` method; see that method's docstring for
        binding details and the reserved-slot wire-form
        invariants.

        ``idempotency_key`` matches the synchronous create() default —
        auto-generated UUIDv4 when omitted (Wave 18-7 / CTI-T7-004).
        """
        body: Dict[str, Any] = {}
        if capsulefile is not None:
            body["capsulefile_id"] = capsulefile
        if max_lifetime_seconds is not None:
            body["max_lifetime_seconds"] = max_lifetime_seconds
        if metadata is not None:
            body["metadata"] = metadata
        if cdp_kind is not None:
            body["cdp_kind"] = cdp_kind

        key = idempotency_key if idempotency_key is not None else str(_uuid.uuid4())
        result = await self._http.request(
            "POST",
            "/v1/capsules",
            json=body,
            headers={"Idempotency-Key": key},
        )
        capsule = Capsule.model_validate(result)
        return AsyncLiveCapsule(capsule, self._http)

    async def run(
        self,
        *,
        capsulefile: str,
        inputs: Optional[Dict[str, Union[bytes, BinaryIO]]] = None,
        command: Optional[str] = None,
        timeout: Optional[int] = None,
        max_lifetime_seconds: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> Tuple[Dict[str, bytes], CdpAccess]:
        """One-shot processing (async): create -> upload -> execute -> download -> destroy."""
        capsule = await self.create(
            capsulefile=capsulefile,
            max_lifetime_seconds=max_lifetime_seconds,
            metadata=metadata,
        )

        try:
            if inputs:
                for filename, data in inputs.items():
                    await capsule.upload(filename, data)

            await capsule.execute(command=command, timeout=timeout)

            output_list = await capsule.list_outputs()
            outputs: Dict[str, bytes] = {}
            for f in output_list:
                outputs[f.filename] = await capsule.download(f.filename)

            cdp = await capsule.destroy()
            return outputs, cdp

        except ExecutionError as e:
            try:
                cdp = await capsule.destroy()
                e.cdp = cdp.full()
            except Exception:
                pass
            raise

        except Exception:
            try:
                await capsule.destroy()
            except Exception:
                pass
            raise

    async def list(self, **kwargs: Any) -> CapsuleList:
        """List capsules (async)."""
        result = await self._http.request("GET", "/v1/capsules", params=kwargs or None)
        return CapsuleList.model_validate(result)

    async def get(self, capsule_id: str) -> Capsule:
        """Get a capsule by ID (async)."""
        result = await self._http.request("GET", f"/v1/capsules/{capsule_id}")
        return Capsule.model_validate(result)
