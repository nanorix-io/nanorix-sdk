/**
 * Customer-side SDK helpers — utilities customers run in their own
 * infrastructure (NOT inside Nanorix capsules) AND in-capsule emission
 * helpers (run inside the customer's capsule entrypoint code).
 *
 * Mirrors Python `nanorix.helpers` byte-identically.
 */

// Customer-side signer (Order 6 / Gap 3) — runs in customer's KMS-
// backed signing endpoint infrastructure.
export {
  Ed25519GeneratedKey,
  attestationToWireDict,
  canonicalizeEvent,
  signActivityEvent,
  verifyEventAttestation,
} from "./signer.js";
export type { EventAttestation, EventSigner } from "./signer.js";

// Cap-3 (Phase 1) — in-capsule ActivityEvent emission helpers. Activates
// ActivityEvent variants (ToolCallAttempted/Completed/Failed +
// ReasoningStepCompleted) for customer use.
export {
  DEFAULT_BUFFER_PATH,
  REASONING_STEP_KINDS,
  TOOL_CALL_FAILURE_KINDS,
  canonicalHash,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
  emitToolCallCompleted,
  emitToolCallFailed,
} from "./activity_events.js";
export type {
  EmitReasoningStepCompletedArgs,
  EmitToolCallAttemptedArgs,
  EmitToolCallCompletedArgs,
  EmitToolCallFailedArgs,
  ReasoningStepKind,
  ToolCallFailureKind,
} from "./activity_events.js";
