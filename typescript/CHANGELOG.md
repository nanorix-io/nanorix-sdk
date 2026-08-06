# Changelog

All notable changes to the Nanorix TypeScript SDK will be documented in this file.

## 0.5.0 — 2026-05-09

### Added

- `debug` module — customer-side debugging tools (Wave 8A):
  - `setTraceContext()` / `getTraceContext()` / `clearTraceContext()` / `buildTraceHeaders()` —
    W3C Trace Context propagation (`traceparent` + `tracestate`); reads `OTEL_TRACEPARENT`
    env var when no explicit value is set.
  - `withRetry<T>()` — async retry-with-backoff; exponential backoff with 503 `Retry-After`
    respect; configurable via `RetryOptions`; idempotent operations only.
  - `downloadAuditProof()` — downloads AuditProof JSON to a local file with 0o600
    permissions; creates parent directories as needed.
  - `verifyAuditProof()` — pure-TypeScript 4-stage AuditProof verification pipeline
    byte-equivalent with the Go reference verifier on the 100-fixture corpus. Returns
    `VerificationResult` with closed-set `FailureReason` matching Go/Rust wire types.
  - `VerifierPolicy` — customer-side policy for authority-pin (`requiredAuthorityId`).
  - `FailureReasonType` — closed-set failure reason wire discriminators (ADR-006 I0).
  - `toWireForm()` — projects `VerificationResult` to `{valid, failure_reason}` fixture form.
- `_jcs` module — JCS RFC 8785 canonical JSON serialization in pure TypeScript (~150 LOC).
  `canonicalize()` + `canonicalizeBytes()`. Cross-impl byte-equivalence with Rust `serde_jcs`
  and Python `_jcs.py`.

### Tests

- +151 new tests in `tests/debug.test.ts`:
  - 11 trace context tests
  - 7 retry-with-backoff tests
  - 15 JCS canonicalization tests
  - 17 AuditProof verification unit tests
  - 100 fixture corpus byte-equivalence tests (Go V1 parity contract)
  - 1 property-test (10k iterations, fault injection, deterministic FailureReason)

## 0.4.0 — 2026-04-22

### Added

- Webhooks resource: DLQ admin endpoints (`GET /v1/webhooks/dlq`, `POST /v1/webhooks/dlq/:id/replay`)
- Activity event emission helpers (`Cap-3` Phase 1)
- Per-event signing helpers (`Ed25519GeneratedKey`, `signActivityEvent`, `verifyEventAttestation`)
- Checkpoint saver helper
- Compliance helpers (GDPR, HIPAA, SOC 2)
- `Usage` resource + pagination

## 0.3.0 — 2026-03-26

### Breaking changes

- Renamed `NanorixClient.templates` → `capsulefiles` (ADR-013 capsulefile rename)

## 0.2.0 — 2026-03-26

### Breaking changes

- Renamed `template` to `capsulefile` across the entire SDK

## 0.1.0 — 2026-03-25

### Initial release
