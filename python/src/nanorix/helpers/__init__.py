"""
Customer-side SDK helpers — utilities customers run in their own
infrastructure (NOT inside Nanorix capsules) AND in-capsule emission
helpers (run inside the customer's capsule entrypoint code).

Exports:
- Signer helpers (Order 6 / Gap 3, customer-side): `Ed25519FileKey`,
  `sign_activity_event`, `EventAttestation` — for customers
  building per-event customer attestation endpoints.
- ActivityEvent emission helpers (Cap-3, in-capsule):
  `emit_tool_call_attempted`, `emit_tool_call_completed`,
  `emit_tool_call_failed`, `emit_reasoning_step_completed` —
  activates ADR-021 ActivityEvent variants for customer use.

Future helpers (deferred to subsequent batches):
- LangGraph adapter (Order 3 sub-D): `NanorixCheckpointSaver`
- Custom KMS adapters: `AwsKmsKey`, `GcpKmsKey`, `AzureKeyVaultKey`
"""

from __future__ import annotations

from nanorix.helpers.activity_events import (
    DEFAULT_BUFFER_PATH,
    REASONING_STEP_KINDS,
    TOOL_CALL_FAILURE_KINDS,
    emit_reasoning_step_completed,
    emit_tool_call_attempted,
    emit_tool_call_completed,
    emit_tool_call_failed,
)
from nanorix.helpers.signer import (
    Ed25519FileKey,
    EventAttestation,
    EventSigner,
    sign_activity_event,
)

__all__ = [
    "DEFAULT_BUFFER_PATH",
    "Ed25519FileKey",
    "EventAttestation",
    "EventSigner",
    "REASONING_STEP_KINDS",
    "TOOL_CALL_FAILURE_KINDS",
    "emit_reasoning_step_completed",
    "emit_tool_call_attempted",
    "emit_tool_call_completed",
    "emit_tool_call_failed",
    "sign_activity_event",
]
