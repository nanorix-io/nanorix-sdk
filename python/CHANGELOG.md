# Changelog

All notable changes to the Nanorix Python SDK will be documented in this file.

## 0.5.0 — 2026-05-29

> Version constant set to `0.5.0` on 2026-05-09 (`fa65d4e`); content continued
> landing under that version through 2026-05-29 (`3cef1ec`). Last version on
> PyPI is 0.3.0, so this entry covers everything a customer upgrading from
> `pip install nanorix` (0.3.0) receives, together with the 0.4.0 entry below.

### Breaking changes

**None.** Verified by AST diff of the published `nanorix-0.3.0.tar.gz` sdist
against this tree: 0 modules removed, 0 names removed from the top-level
`__all__` (24 → 62), and 0 removed or narrowed methods on any class that
existed in 0.3.0. Every signature change is additive keyword-only parameters
with defaults (`Capsules.create` gained `egress_profile`, `metadata`,
`cdp_kind`, `idempotency_key`; `Capsules.run` gained `metadata`; the async
variants gained the same minus `egress_profile`).

### Fixed

- `python -m nanorix` raised `TypeError: unsupported operand type(s) for |:
  'types.GenericAlias' and 'NoneType'` on Python 3.9 before it could print
  anything. `__main__.py` annotated `main(argv: list[str] | None = None)` —
  a PEP 604 union, which CPython evaluates at function-definition time and
  which only became valid at runtime in 3.10. The module now carries
  `from __future__ import annotations`, as the other 87 modules in the package
  already did. Only `python -m nanorix` was affected; `import nanorix` never
  imports `__main__`.

### Dependencies and Python support

- **New required runtime dependency:** `cryptography>=46.0.7,<47.0.0` — needed
  by the offline verifier (`nanorix.verifier`) and the Ed25519 signing helpers.
  0.3.0 required only `httpx` and `pydantic`.
- `requires-python` stays `>=3.9`, but `cryptography` 46.x excludes Python
  3.9.0 and 3.9.1 (`!=3.9.0,!=3.9.1,>=3.8`), so the effective floor is 3.9.2.
- All 96 modules under `src/nanorix` are clear of 3.10+ grammar and of PEP 604
  unions in runtime-evaluated positions, so both `import nanorix` and
  `python -m nanorix` are safe on the full supported range. Re-checkable with
  `ruff check --target-version py39 --select FA102 src/nanorix` (0 findings).

### Added — new modules (83 modules new since 0.4.0)

#### `nanorix.integrations` — 16 modules (15 below + the package `__init__`)

Agent-framework, web-framework, and orchestrator wrappers. Each reuses the
`nanorix.helpers.activity_events` 4-variant surface (ADR-006 I0). Framework
shells are optional installs; the cores need no framework.

- `integrations.langchain` — `NanorixCallback`
- `integrations.langgraph` — `NanorixLangGraphCallback`
- `integrations.llama_index` — `NanorixLlamaIndexCallback`
- `integrations.litellm` — `NanorixLiteLLMCallback`
- `integrations.autogen` — `NanorixAutoGenCallback`, `attach_to_conversable_agent`,
  `attach_to_runtime`
- `integrations.crewai` — `NanorixCrewAICallback`, `attach_to_crew`
- `integrations.otel_genai` — `NanorixOTelGenAIExporter`,
  `OTEL_GENAI_SEMCONV_VERSION`, `REDACTED_PLACEHOLDER`
- `integrations.fastapi_middleware` — `NanorixMiddleware` (ASGI), `CDP_KIND_REQUEST`,
  `RESPONSE_HEADER_AUDIT_PROOF_ID` / `_CAPSULE_ID` / `_CDP_KIND`
- `integrations.flask_middleware` — `NanorixMiddleware` (WSGI), same constants
- `integrations.airflow` — `NanorixCapsuleOperator`
- `integrations.temporal` — `make_capsule_activity`, `register_capsule_activity`,
  `NanorixCapsuleActivityInput`, `NanorixCapsuleActivityResult`, `ACTIVITY_NAME`
- `integrations.jupyter` — `NanorixMagics` (`%%nanorix_capsule`), `NanorixDisplay`,
  `load_ipython_extension`, HTML renderers
- `integrations.c2pa_provenance` — `C2PAManifestBuilder`, `C2PAManifest`,
  `C2PAVerificationResult`, `verify_c2pa_manifest`, `AssertionType` (ADR-035)
- `integrations._orchestrator_core` — framework-free `run_capsule_job`,
  `CapsuleJobParams`, `CapsuleJobResult`, `OrchestratorError`
- `integrations._notebook_core` — IPython-free `run_capsule_cell`,
  `parse_magic_args`, `render_auditproof_html` / `_cell_result_html` / `_verification_html`

#### `nanorix.connectors` — 4 modules (ADR-036)

Customer-side streaming of bytes through volatile capsule memory. Runs in the
customer's process with the customer's cloud credentials; Nanorix never stages
the data and never takes custody of credentials.

- `connectors._base` — `Source`, `Sink`, `SourceObject`, `ConnectorError`,
  `stream_source_into_capsule`, `stream_capsule_to_sink`, `DEFAULT_CHUNK_SIZE`,
  `TRUST_BOUNDARY_NOTE`
- `connectors.s3` — `S3Source`, `S3Sink` (extra: `nanorix[s3]`)
- `connectors.gcs` — `GCSSource`, `GCSSink` (extra: `nanorix[gcs]`)
- `connectors` — re-exports the `_base` surface

#### `nanorix.observability` — 6 modules

Export AuditProof events, verification outcomes, and the billing meter to an
observability backend. The shaping core needs no vendor SDK.

- `observability._core` — `auditproof_to_datadog_event`, `auditproof_to_splunk_event`,
  `auditproof_to_honeycomb_span`, `auditproof_to_sentry_event`,
  `cost_meter_to_datadog_series`, `cost_meter_to_splunk_event`, `ProofFields`,
  `read_proof_fields`, `assert_structural_facts_only`, `FORBIDDEN_VOCAB`,
  `ObservabilityError`, `STRUCTURAL_FACTS_NOTE`
- `observability.datadog` — `DatadogObservabilityAdapter`
- `observability.splunk` — `SplunkObservabilityAdapter`
- `observability.honeycomb` — `HoneycombObservabilityAdapter`
- `observability.sentry` — `SentryObservabilityAdapter`
- `observability` — re-exports the `_core` surface

#### `nanorix.grc` — 5 modules

File the signed AuditProof into a GRC platform as evidence, landed against the
control IDs the ADR-040 mapping resolves. Evidence is a factual reference
(`applies_to` / `references` / `related_to`), never a verdict — the auditor
adjudicates (ADR-017).

- `grc._core` — `receipt_to_evidence_body`, `auditproof_to_evidence_bodies`,
  `resolve_control_ids`, `assert_reference_only`, `FORBIDDEN_VOCAB`,
  `REFERENCE_RELATIONSHIP`, `MAPPING_DISCLAIMER`, `GrcError`
- `grc.vanta` — `VantaEvidenceAdapter`
- `grc.drata` — `DrataEvidenceAdapter`
- `grc.anecdotes` — `AnecdotesEvidenceAdapter`
- `grc` — re-exports the `_core` surface

#### `nanorix.healthcare` — 14 modules

Structural adapters for customer-supplied healthcare payloads. Helpers emit
AuditProof-binding metadata only; clinical interpretation stays the customer's
(three-role discipline, ADR-017).

- `healthcare.hl7_v2` — `HL7v2Adapter`, `HL7v2ParsedMessage`
- `healthcare.ccda` — `CCDAReader`, `CCDADocumentReference`
- `healthcare.epic_smart` — `EpicLaunchContext`, `EpicLaunchContextResult`
- `healthcare.bulk_fhir` — `BulkFhirAdapter`, `BulkFhirKickoff`, `BulkFhirResource`,
  `BulkFhirStatus`
- `healthcare.dicomweb` — `DICOMwebAdapter`, `DICOMwebTransaction`
- `healthcare.dicom_sr` — `DICOMSRBinding`
- `healthcare.ihe` — `IHEAdapter`, `IHETransaction`, `IHETransactionType`, `IHEBindingError`
- `healthcare.ncpdp` — `NCPDPAdapter`, `NCPDPParsedTransaction`
- `healthcare.x12_eligibility` — `X12EligibilityAdapter`, `X12EligibilityTransaction` (270/271)
- `healthcare.x12_278` — `X12ServicesReviewAdapter`, `X12ServicesReviewTransaction`
- `healthcare.x12_full_rcm` — `X12FullRCMAdapter`, `X12RCMTransaction` (834/835/837)
- `healthcare.fda_samd` — `FDASaMDContext`
- `healthcare.ai_fairness` — `AIFairnessContext`
- `healthcare` — re-exports 27 names across the above

#### `nanorix.compliance` — 17 modules

Per-framework context builders. Factual reference language only — the surface
uses `applies_to` / `references` / `related_to` and never asserts that a
framework requirement has been discharged.

- `compliance.hipaa` — `HipaaContext`
- `compliance.gdpr` — `GdprContext`
- `compliance.soc2` — `Soc2Context`
- `compliance.hitrust` — `HITRUSTContext`
- `compliance.cfr_part_2` — `CFRPart2Context`
- `compliance.nist_ai_rmf` — `NISTAIRMFContext`
- `compliance.pci_dss` — `PCIDSSContext`
- `compliance.iso_42001` — `ISO42001Context`
- `compliance.texas_sb_14` — `TexasSB14Context`
- `compliance.ccpa` — `CCPAContext`
- `compliance.cpra` — `CPRAContext`
- `compliance.bipa` — `BIPAContext`
- `compliance.nyc_ll144` — `NYCLL144Context`
- `compliance.colorado_ai_act` — `ColoradoAIActContext`
- `compliance.utah_ai_policy_act` — `UtahAIPolicyActContext`
- `compliance._constraints` — `validate_value_length`, `validate_no_comma_in_list`,
  `estimate_serialized_size`
- `compliance` — re-exports the 15 framework context classes

#### `nanorix.verifier` — 5 modules

Offline AuditProof verification, ADR-039 / ADR-041 wave-N receipts and parent
proofs, and the portable bundle conventions.

- `verifier._verify` — `verify`, `VerifyResult`
- `verifier.wave_n` — `verify_full_audit_proof`, `verify_record_receipt`,
  `verify_merkle_inclusion_proof`, `build_merkle_inclusion_proof`,
  `compute_record_chain_hash`, `compute_record_receipts_merkle_root`,
  `compute_parent_proofs_merkle_root`, `compute_activity_root`,
  `compute_step_8_base`, `compute_step_8_amended`, `merkle_pair_hash`,
  `merkle_root_sha512_null_separated`, `detect_parent_proof_cycle`,
  `enforce_depth_cap`, `RecordReceipt`, `ParentProofLink`, `WaveNVerifyResult`,
  `WaveNVerifyError`, `GENESIS_SHA512_HEX`, `PARENT_PROOF_MAX_DEPTH`, `PATTERN_TAGS_WIRE`
- `verifier.bundle` — `PortableReceiptBundle`, `extract_receipt_bundle`,
  `verify_receipt_bundle`, `AuditProofAnchors`, `BundleError` + 8 `BUNDLE_ERR_*` codes
- `verifier.pubkey_bundle` — `PortablePubkeyBundle`, `build_pubkey_bundle`,
  `verify_pubkey_bundle`, `resolve_parent_key`, `resolve_parent_key_forever`,
  `PubKeyEntry`, `BundleSignature`, `PubkeyBundleError` + 8 `PUBKEY_BUNDLE_ERR_*` codes
- `verifier` — re-exports 38 names across the four modules

#### `nanorix.helpers` — 4 modules

- `helpers.activity_events` — `emit_tool_call_attempted`, `emit_tool_call_completed`,
  `emit_tool_call_failed`, `emit_reasoning_step_completed`, `REASONING_STEP_KINDS`,
  `TOOL_CALL_FAILURE_KINDS`, `DEFAULT_BUFFER_PATH`
- `helpers.signer` — `Ed25519FileKey`, `EventSigner`, `EventAttestation`,
  `sign_activity_event`, `verify_event_attestation`, `canonicalize_event`
- `helpers.checkpoint_saver` — `NanorixCheckpointSaver`, `CheckpointEntry`,
  `ArtifactStorage`, `InMemoryArtifactStorage`, `hash_state_dict`
- `helpers` — re-exports the above

#### Top-level modules — 12 new

- `nanorix.debug` — customer-side debugging tools (Wave 8A):
  - `set_trace_context()` / `get_trace_context()` / `clear_trace_context()` — W3C Trace
    Context propagation (`traceparent` + `tracestate`) bound to all SDK calls;
    reads `OTEL_TRACEPARENT` env var when no explicit value is set.
  - `with_retry()` / `async_with_retry()` — exponential backoff with 503 `Retry-After`
    respect; configurable via `RetryOptions`; idempotent operations only.
  - `download_auditproof()` — downloads AuditProof JSON to a local file with 0600
    permissions; creates parent directories as needed.
  - `verify_auditproof()` — pure-Python 4-stage AuditProof verification pipeline
    byte-equivalent with the Go reference verifier (`tools/auditproof-verifier-go/`)
    on the 100-fixture corpus. Returns structured `VerificationResult` with closed-set
    `FailureReason` enum mirroring `governance/rzl/src/verify_types.rs`.
  - `VerifierPolicy` — customer-side policy for authority-pin (`required_authority_id`).
  - `FailureReasonType` — closed-set failure reason wire discriminators (ADR-006 I0).
- `nanorix._jcs` — JCS RFC 8785 canonical JSON serialization in pure Python
  (~150 LOC). Cross-impl byte-equivalence with Rust `serde_jcs` and TypeScript `_jcs.ts`.
- `nanorix.capsule_record` — `RecordContext`, `AsyncRecordContext`, `canonical_hash`,
  `sha512_hex`, `validate_pattern_tag`, `PATTERN_TAGS`, and the `MAX_*_LEN` bounds
  (ADR-039 per-record receipts)
- `nanorix.control_map` — `load_mapping`, `resolve`, `available_framework_versions`,
  `Mapping`, `MAPPING_2026_02`, `SCHEMA_VERSION`, `FRAMEWORK_VERSION`, `DISCLAIMER`
  (ADR-040 published mapping artifact)
- `nanorix.decorators` — `@workload` / `WorkloadDecorator` and the
  `FRAMEWORK_*` / `ALLOWED_FRAMEWORKS` / `WRAPPER_KIND` constants
- `nanorix.billing` — `Billing`, `AsyncBilling`, `BillingMeterUsage`, `CostForecast`,
  `CapsulefileUsage`, `PatternUsage`, `ShapeUsage`
- `nanorix.benchmark` — `load_benchmark`, `parse_artifact`, `get_p95`,
  `BenchmarkArtifact`, `MeasurementRun`, `Percentiles` (+ `benchmark_data/2026-05.json`)
- `nanorix._batch` — `BatchRecord`, `BatchResult`, `compute_batch_merkle_root`,
  `serialize_records_to_jsonl`, `parse_results_jsonl`
- `nanorix._session` — `SessionResult`, `build_session_metadata`, `hash_state_bytes`
- `nanorix._merkle` — `merkle_root_from_leaves`, `hash_record_bytes`, `combined_record_leaf`
- `nanorix._pagination` — `SyncPage`, `AsyncPage`
- `nanorix.resources.usage` — `Usage`, `AsyncUsage`, `UsageItem`

### Added — surface changes to modules that existed in 0.3.0

- `Client` / `AsyncClient` gained four attributes: `usage`, `billing`,
  `audit_proofs` (ADR-014 brand alias for `cdp`; same interface).
- `Capsules` gained `batch()` (inline batch processing with RFC 6962 Merkle root)
  and `session()` (LangGraph-shape memory artifact).
- `LiveCapsule` / `AsyncLiveCapsule` gained `record()` and `add_parent_proof()`
  (ADR-039 + ADR-041 ergonomic surface).
- `_exceptions` gained `UnprocessableEntityError` (422), `InputProvenanceRejectedError`,
  and `SecretsInjectionRejectedError`.
- `_models` gained `RecordReceipt` and `ParentProofLink`; `CdpDocument` and
  `VerificationCdp` gained `cdp_kind`, `record_receipts`,
  `record_receipts_merkle_root`, `parent_proof_hashes`, `parent_proofs_merkle_root`.
- `python -m nanorix` gained a `verify` subcommand — offline AuditProof
  verification with `--json` / `--quiet` and exit codes 0 (pass) / 1 (fail) /
  2 (usage error). 0.3.0 only printed the version.

### Optional-dependency extras

28 extras added since 0.3.0 (0.3.0 shipped only `dev`): `langchain`, `langgraph`,
`llama-index`, `litellm`, `otel-genai`, `autogen`, `crewai`, `fastapi`, `flask`,
`hl7-v2`, `ccda`, `epic-smart`, `dicomweb`, `dicom-sr`, `airflow`, `temporal`,
`jupyter`, `s3`, `gcs`, `datadog`, `splunk`, `honeycomb`, `sentry`, `vanta`,
`drata`, `anecdotes`, plus the grouped `observability` and `grc` extras.

### Tests

- 2315 tests collected (`python -m pytest --collect-only -q` in `sdk/python/`),
  up from the 45 that shipped with 0.1.0.
- The Wave 8A batch alone added 152 tests in `tests/test_debug.py`:
  10 trace context · 9 retry-with-backoff · 4 download AuditProof ·
  16 JCS canonicalization · 20 AuditProof verification unit ·
  101 fixture corpus byte-equivalence (Go V1 parity contract) ·
  1 property-test (10k iterations, fault injection, deterministic FailureReason).

### Commit provenance

50 commits touch `sdk/python` between the 0.4.0 bump (`4bd2404`) and this entry
(`git log --no-merges 4bd2404..HEAD -- sdk/python`). The named batches of the
2026-05 gap-closing arc map onto the module list above as follows.

| Commit | Date | Landed |
|---|---|---|
| `6f252e2` | 2026-05-03 | `metadata` kwarg + `nanorix.compliance` (HIPAA, GDPR, SOC 2) |
| `506138a` `b395f0f` | 2026-05-05 | Pagination + `Usage` + `AuditProofs` alias; offline verifier + `python -m nanorix verify` |
| `0ae5b49` `01cd342` `ff15447` | 2026-05-05/06 | `Capsule.batch()`, `Capsule.session()`, `Ed25519FileKey` signer helpers |
| `bda0876` | 2026-05-06 | 422 mapping + `InputProvenanceRejected` / `SecretsInjectionRejected` |
| `fa65d4e` | 2026-05-09 | Wave 8A — `nanorix.debug`, `nanorix._jcs`; version constant → 0.5.0 |
| `c504cf2` … `cd31624` | 2026-05-10+ | Wave 14 — `integrations.langchain` / `langgraph` / `llama_index` / `litellm` / `otel_genai`, `@workload` |
| `59f71d8` … `73b7a37` | 2026-05-1x | Wave 17 — the 12 further `compliance.*` framework contexts |
| `6352eff` `87cdd07` `88ebdcd` | 2026-05-1x | `healthcare.hl7_v2` / `ccda` / `epic_smart` / `fda_samd` / `ai_fairness` |
| `f43ad06` | 2026-05-2x | DICOM stack — `healthcare.dicomweb`, `healthcare.dicom_sr` |
| `478e06e` `71eb110` `9e5dc33` `6d5abac` `e9108f9` | 2026-05-27 | **Batch 2** — `healthcare.ncpdp`, `x12_eligibility`, `x12_278`, `x12_full_rcm`, `bulk_fhir` |
| `86dba40` | 2026-05-27 | **Batch 5** — `healthcare.ihe` (IHE XDS-I.b + XDR profile mapping) |
| `c999d41` | 2026-05-28 | **Batch 9** — `integrations.c2pa_provenance` (ADR-035) |
| `4c86cb0` | 2026-05-28 | **Batch 13** — `integrations.airflow`, `integrations.temporal`, `integrations._orchestrator_core` |
| `a6eec8d` | 2026-05-28 | **Batch 14** — `nanorix.connectors` (`_base`, `s3`, `gcs`; ADR-036) |
| `7d7f8c0` | 2026-05-28 | **Batch 16** — `integrations.jupyter`, `integrations._notebook_core` |
| `3cef1ec` | 2026-05-29 | **Batch 17** — `nanorix.observability` (4 adapters), `nanorix.grc` (3 adapters) |
| `f46d0c2` | 2026-05-29 | `cryptography` floor raised to 46.0.7 (PYSEC-2026-35/36, CVE-2026-26007) |

## 0.4.0 — 2026-04-11

> Bumped in `4bd2404` but never published to PyPI — the last version on PyPI is
> 0.3.0. Recorded here so the 0.3.0 → 0.5.0 upgrade path is complete.

### Breaking changes

**None.**

### Added

- `nanorix.resources.egress_profiles` — `EgressProfiles` / `AsyncEgressProfiles`
  with `create`, `list`, `get`, `update`, `delete`, `add_rule`, `list_rules`,
  `remove_rule` (Business+ egress allowlisting).
- `nanorix.resources.webhooks` — `Webhooks` / `AsyncWebhooks` with `create`,
  `list`, `get`, `update`, `delete`, `test`, `deliveries`; plus `Webhook` with
  `verify_signature()` for HMAC-SHA256 delivery verification across 15 event types.
- `Client` / `AsyncClient` gained the `egress_profiles` and `webhooks` attributes.
- `Capsules.create()` gained the keyword-only `egress_profile` parameter.

## 0.3.0 — 2026-03-26

### Breaking changes

- Renamed `capsules.start()` to `capsules.create()` to align with REST API convention

## 0.2.0 — 2026-03-26

### Breaking changes

- Renamed "template" to "capsulefile" across the entire SDK (zero customers, clean cut)
- `client.templates` -> `client.capsulefiles`
- `Template` model -> `Capsulefile`
- `TemplateFile` model -> `CapsulefileFile`
- `TemplateList` model -> `CapsulefileList`
- `template_id` param -> `capsulefile_id`
- `template` param in `capsules.create()`/`capsules.run()` -> `capsulefile`
- `wait_until_ready()` -> `wait_until_finalized()` (polls for "finalized" status)
- Default status changed from "building" to "draft"
- API endpoints changed from `/v1/templates` to `/v1/capsulefiles`

## 0.1.0 — 2026-03-25

### Initial release

- Sync and async clients (`Client`, `AsyncClient`)
- Capsulefile management: create (three-step), list, get, delete, wait_until_finalized
- Capsule lifecycle: create, upload, execute, download, destroy
- One-shot processing: `client.capsules.run()` wraps full lifecycle
- Workspace mode: `LiveCapsule` object with step-by-step methods
- CDP access: `CdpAccess` with `.full()`, `.verification()`, `.verify_url`
- Typed exception hierarchy mapping HTTP status codes
- Retry with exponential backoff on 429 and 5xx
- Pydantic v2 models with forward compatibility (`extra="allow"`)
- PEP 561 type stubs (`py.typed`)
- 45 tests with respx mocks
