"""
Customer-side ActivityEvent emission helpers — Cap-3 (Phase 1).

 (Accepted 2026-05-06) shipped 4 ActivityEvent variants for AI-
agent tooling — `ToolCallAttempted`, `ToolCallCompleted`, `ToolCallFailed`,
`ReasoningStepCompleted` — implemented at
the Nanorix API service. The variants are bound
into the AuditProof's canonical_hash and surface in the activity_trail.

Until Cap-3, customers had NO SDK helper to construct + emit these events
from inside their capsule code. This module closes the gap. Customers
import these helpers in their capsule entrypoint, call them at the relevant
points in their agent loop, and the helpers write structured JSON
ActivityEvent records to the capsule's activity_trail buffer at
`/data/activity_events.jsonl`. The Nanorix daemon drains the buffer at
destroy, validates each event against the closed schema, and absorbs
matching variants into FullCdp.activity.

## Trust-boundary discipline

- **Customer-sole-declarer.** Customer's capsule code declares
  every field verbatim. Helpers do NOT classify, infer, or augment — only
  shape into the wire schema + canonical-JSON serialize + write.
- **Zero-retention.** `tool_args_hash`, `tool_result_hash`,
  `prompt_hash`, `response_hash` are all SHA-512 hex; plaintext NEVER
  enters the AuditProof. The helpers compute hashes via `_canonical_hash`
  using the same `sort_keys=True, separators=(",", ":")` JCS-flavor that
  `nanorix._merkle.hash_record_bytes` and `nanorix._session.hash_state_bytes`
  use, so cross-impl byte-equivalence holds.
- **Forever-Standard.** Field names, JSON-tag (`event`),
  and snake_case keys mirror the Rust enum's serde shape verbatim.
  Reserved Optional slots (`agent_id`, `parent_event_id`, `step_kind`,
  `summary_hash`, `failure_kind`) are sometimes-populated; pre-amendment
  AuditProofs (without these fields) verify byte-identically.

## Buffer protocol

Each helper emits a single line of JSON to the activity-trail buffer.
The buffer location is `/data/activity_events.jsonl` (line-delimited
JSON, append-only). On capsule destroy, the daemon reads the buffer
line-by-line, parses each line as ActivityEvent, validates against the
closed schema, and absorbs into FullCdp.activity. Lines that fail
schema validation are dropped (and counted in
`SchemaValidationDropped`).

For test environments where `/data/activity_events.jsonl` doesn't
exist (developer machine, CI), the helpers accept a `buffer_path`
override. Production capsule code typically uses the default path.

## Cap-3 boss directive (2026-05-08)

> "the helper code shipping inside the existing not-yet-published
> SDK package is fine. The publish event remains gated separately."

The helpers ship in the SDK source tree but the publish event (PyPI /
npm) remains gated.
Customers who consume from the source tree (private install) can use
these helpers today.

## Examples

```python
# Customer's capsule entrypoint (runs inside Nanorix sealed capsule)
from nanorix.helpers.activity_events import (
    emit_tool_call_attempted,
    emit_tool_call_completed,
    emit_tool_call_failed,
    emit_reasoning_step_completed,
)

# Agent loop emits a reasoning step
emit_reasoning_step_completed(
    step_idx=0,
    prompt={"system": "You are a PA agent...", "user": "Patient X has..."},
    response={"text": "Recommend approval pending...", "confidence": 0.87},
    duration_ms=2_400,
    model="claude-opus-4-7",
    step_kind="decide",
)

# Agent invokes a tool
emit_tool_call_attempted(
    tool_call_id="call_abc123",
    tool_name="submit_pa_request",
    tool_args={"patient_id": "X", "decision": "approved"},
)

# Tool succeeds
emit_tool_call_completed(
    tool_call_id="call_abc123",
    tool_name="submit_pa_request",
    tool_result={"submission_id": "PA-2026-001"},
    duration_ms=1_200,
)
```
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


__all__ = [
    "emit_tool_call_attempted",
    "emit_tool_call_completed",
    "emit_tool_call_failed",
    "emit_reasoning_step_completed",
    "DEFAULT_BUFFER_PATH",
    "TOOL_CALL_FAILURE_KINDS",
    "REASONING_STEP_KINDS",
]


# ── Constants ───────────────────────────────────────────────────────


DEFAULT_BUFFER_PATH = "/data/activity_events.jsonl"
"""Default activity-trail buffer path inside a Nanorix capsule.

The Nanorix daemon mounts `/data` as tmpfs (no host filesystem),
seals it, and reads `activity_events.jsonl` on capsule destroy.
"""


# Closed-enum mirrors of the Nanorix API service.
# These match the `ToolCallFailureKind` and `ReasoningStepKind` Rust
# enums verbatim (Accepted 2026-05-07).
TOOL_CALL_FAILURE_KINDS = frozenset(
    {
        "timeout",
        "permission_denied",
        "invalid_args",
        "upstream_failure",
        "cancelled",
        "reserved",
    }
)
"""Closed-enum tool-call failure kinds.

Mirrors `ToolCallFailureKind` at the Nanorix API service.
Customer's call site supplies one of these strings as `failure_kind`.
The free-form `failure_reason` field carries the customer's verbatim
narrative; both fields populate when both pieces of information are
available.
"""

REASONING_STEP_KINDS = frozenset({"plan", "reason", "synthesize", "decide", "reserved"})
"""Closed-enum reasoning step kinds.

Mirrors `ReasoningStepKind` at the Nanorix API service.
Customer-declared classification of the reasoning step. Auditor tooling
partitions the trail by step kind (e.g., "show me only the agent's
`Decide` steps").
"""


# ── Internal helpers ────────────────────────────────────────────────


def _canonical_hash(payload: Any) -> str:
    """Compute SHA-512 hex of canonical-JSON-serialized payload.

    Uses the same JCS-flavor (`sort_keys=True, separators=(",", ":")`)
    that `nanorix._merkle.hash_record_bytes`,
    `nanorix._session.hash_state_bytes`, and
    the sealed runtime use, so
    cross-impl byte-equivalence holds for the activity-trail's hash
    fields.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha512:" + hashlib.sha512(canonical).hexdigest()


def _now_iso8601() -> str:
    """Current UTC time in ISO 8601 format (matches activity-trail `at` field)."""
    return datetime.now(timezone.utc).isoformat()


def _emit(event: dict[str, Any], buffer_path: str) -> None:
    """Append one JSON-encoded ActivityEvent line to the buffer.

    Atomic append + newline-delimited so the daemon's line-by-line
    reader at destroy never sees partial events. The Path open
    mode `"a"` is documented as atomic for single-line writes <
    PIPE_BUF (4096 bytes on Linux); typical event sizes (tool-name +
    hashes + ids) are well under this ceiling.

    Failures to write (read-only filesystem, permission denied) raise
    OSError — customer's capsule code can choose to swallow or
    surface. The daemon does NOT depend on every event reaching the
    buffer; missing events are not absorbed but do not corrupt the
    AuditProof.
    """
    line = json.dumps(event, separators=(",", ":")) + "\n"
    path = Path(buffer_path)
    # Ensure parent dir exists (idempotent). For the default
    # /data/activity_events.jsonl path, /data is the capsule's tmpfs
    # root — already present.
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


# ── Public emit_* helpers ───────────────────────────────────────────


def emit_tool_call_attempted(
    *,
    tool_call_id: str,
    tool_name: str,
    tool_args: Any,
    at: Optional[str] = None,
    agent_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    buffer_path: str = DEFAULT_BUFFER_PATH,
) -> dict[str, Any]:
    """Emit a `ToolCallAttempted` ActivityEvent.

    Customer-declared. Pairs with a subsequent
    `ToolCallCompleted` (success) or `ToolCallFailed` (failure) via
    `tool_call_id`.

    Args:
        tool_call_id: Customer-provided stable identifier for this
            attempt (e.g., UUID, ULID, agent-graph node ID). NOT
            generated by Nanorix.
        tool_name: Tool name (e.g., `"submit_pa_request"`). Plaintext.
        tool_args: Tool arguments — any JSON-serializable value. The
            helper computes `tool_args_hash = sha512(canonical(tool_args))`;
            the plaintext arguments NEVER enter the AuditProof
            (zero-retention discipline).
        at: ISO 8601 timestamp; defaults to current UTC time.
        agent_id: Optional sub-agent identifier (multi-agent debugging).
        parent_event_id: Optional customer-declared causal-parent
            reference. Opaque customer-supplied identifier
            referencing the prior event in the customer's reasoning /
            tool-call graph.
        buffer_path: Override for the activity-trail buffer location
            (test-mode + custom-Capsulefile-mount edge cases).

    Returns:
        The event dict that was written to the buffer (also useful
        for unit-test assertions).

    Raises:
        OSError: if the buffer file cannot be opened or written.
    """
    event: dict[str, Any] = {
        "event": "tool_call_attempted",
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool_args_hash": _canonical_hash(tool_args),
        "at": at or _now_iso8601(),
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    if parent_event_id is not None:
        event["parent_event_id"] = parent_event_id
    _emit(event, buffer_path)
    return event


def emit_tool_call_completed(
    *,
    tool_call_id: str,
    tool_name: str,
    tool_result: Any,
    duration_ms: int,
    at: Optional[str] = None,
    agent_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    buffer_path: str = DEFAULT_BUFFER_PATH,
) -> dict[str, Any]:
    """Emit a `ToolCallCompleted` ActivityEvent (success path).

    Pairs with a prior `ToolCallAttempted` via `tool_call_id`.
    Customer-declared. Customer's agent code emits
    exactly one of `tool_call_completed` OR `tool_call_failed` per
    attempted call.

    `tool_result` is hashed (SHA-512 hex) — plaintext result NEVER
    enters AuditProof.
    """
    if duration_ms < 0:
        raise ValueError(f"duration_ms must be non-negative; got {duration_ms}")
    event: dict[str, Any] = {
        "event": "tool_call_completed",
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "tool_result_hash": _canonical_hash(tool_result),
        "duration_ms": duration_ms,
        "at": at or _now_iso8601(),
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    if parent_event_id is not None:
        event["parent_event_id"] = parent_event_id
    _emit(event, buffer_path)
    return event


def emit_tool_call_failed(
    *,
    tool_call_id: str,
    tool_name: str,
    failure_reason: str,
    duration_ms: int,
    failure_kind: Optional[str] = None,
    at: Optional[str] = None,
    agent_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    buffer_path: str = DEFAULT_BUFFER_PATH,
) -> dict[str, Any]:
    """Emit a `ToolCallFailed` ActivityEvent (failure path).

    Pairs with a prior `ToolCallAttempted` via `tool_call_id`.

    Args:
        failure_reason: Free-form customer-provided narrative
            (e.g., `"upstream returned 503 with body 'rate limited'"`).
            Nanorix records verbatim.
        failure_kind: Optional closed-enum reduction.
            One of `TOOL_CALL_FAILURE_KINDS`. Auditor query tooling
            indexes against this field; the free-form `failure_reason`
            remains the customer's narrative.
        duration_ms: Wall-clock time the call ran before failing.

    Raises:
        ValueError: if `failure_kind` is supplied and not in the
            closed-enum set.
    """
    if duration_ms < 0:
        raise ValueError(f"duration_ms must be non-negative; got {duration_ms}")
    if failure_kind is not None and failure_kind not in TOOL_CALL_FAILURE_KINDS:
        raise ValueError(
            f"failure_kind '{failure_kind}' is not in the closed-enum set; "
            f"allowed values: {sorted(TOOL_CALL_FAILURE_KINDS)}"
        )
    event: dict[str, Any] = {
        "event": "tool_call_failed",
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "failure_reason": failure_reason,
        "duration_ms": duration_ms,
        "at": at or _now_iso8601(),
    }
    if agent_id is not None:
        event["agent_id"] = agent_id
    if parent_event_id is not None:
        event["parent_event_id"] = parent_event_id
    if failure_kind is not None:
        # Closed-enum-tagged JSON shape per Rust serde
        # `#[serde(tag = "type", rename_all = "snake_case")]`.
        event["failure_kind"] = {"type": failure_kind}
    _emit(event, buffer_path)
    return event


def emit_reasoning_step_completed(
    *,
    step_idx: int,
    prompt: Any,
    response: Any,
    duration_ms: int,
    at: Optional[str] = None,
    model: Optional[str] = None,
    agent_id: Optional[str] = None,
    step_kind: Optional[str] = None,
    summary: Any = None,
    parent_event_id: Optional[str] = None,
    buffer_path: str = DEFAULT_BUFFER_PATH,
) -> dict[str, Any]:
    """Emit a `ReasoningStepCompleted` ActivityEvent.

    Customer's agent code emits this after each LLM-resolution step.
    Forms a chronological reasoning trail bound into the AuditProof's
    canonical_hash via activity_trail.

    Args:
        step_idx: 0-indexed step number within the reasoning loop.
            Customer-provided. Multiple loops in one capsule (sub-agent
            invocations) maintain distinct sequence in `agent_id`.
        prompt: Prompt payload sent to the LLM. Hashed (SHA-512 hex);
            plaintext NEVER enters AuditProof.
        response: LLM response payload. Hashed (SHA-512 hex); plaintext
            NEVER enters AuditProof.
        duration_ms: Wall-clock time the LLM call took.
        model: Optional model identifier (e.g., `"claude-opus-4-7"`,
            `"gpt-5"`). Plaintext.
        agent_id: Optional sub-agent identifier.
        step_kind: Optional closed-enum reasoning step kind.
            One of `REASONING_STEP_KINDS`.
        summary: Optional customer-derived summary artifact.
            When provided, `summary_hash = sha512(canonical(summary))`
            populates the event. Use case: PA-decision agent emits a
            structured rationale separately from the freeform LLM
            response.
        parent_event_id: Optional customer-declared causal-parent
            reference.

    Raises:
        ValueError: if `step_kind` is supplied and not in the closed-
            enum set.
    """
    if step_idx < 0:
        raise ValueError(f"step_idx must be non-negative; got {step_idx}")
    if duration_ms < 0:
        raise ValueError(f"duration_ms must be non-negative; got {duration_ms}")
    if step_kind is not None and step_kind not in REASONING_STEP_KINDS:
        raise ValueError(
            f"step_kind '{step_kind}' is not in the closed-enum set; "
            f"allowed values: {sorted(REASONING_STEP_KINDS)}"
        )
    event: dict[str, Any] = {
        "event": "reasoning_step_completed",
        "step_idx": step_idx,
        "prompt_hash": _canonical_hash(prompt),
        "response_hash": _canonical_hash(response),
        "duration_ms": duration_ms,
        "at": at or _now_iso8601(),
    }
    if model is not None:
        event["model"] = model
    if agent_id is not None:
        event["agent_id"] = agent_id
    if step_kind is not None:
        event["step_kind"] = {"type": step_kind}
    if summary is not None:
        event["summary_hash"] = _canonical_hash(summary)
    if parent_event_id is not None:
        event["parent_event_id"] = parent_event_id
    _emit(event, buffer_path)
    return event
