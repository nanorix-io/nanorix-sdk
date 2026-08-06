export { NanorixClient } from "./client.js";
export type { NanorixClientOptions } from "./client.js";

// Models
export type {
  Capsulefile,
  CapsulefileFile,
  CapsulefileList,
  Capsule,
  CapsuleList,
  ExecutionResult,
  FileInfo,
  ActivityEvent,
  ResidencyInfo,
  DestructionInfo,
  AttestationInfo,
  DataSummary,
  CdpDocument,
  VerificationCdp,
  RecordReceipt,
  ParentProofLink,
} from "./models.js";
export { CdpAccess } from "./models.js";

// Resources
export { LiveCapsule } from "./resources/capsules.js";
export { EgressProfiles } from "./resources/egress-profiles.js";
export { Webhooks } from "./resources/webhooks.js";
export type { WebhookEndpoint, WebhookDelivery } from "./resources/webhooks.js";
export type {
  EgressProfile,
  EgressProfileDetail,
  EgressRule,
} from "./resources/egress-profiles.js";

// Errors
export {
  NanorixError,
  AuthenticationError,
  NotFoundError,
  ConflictError,
  RateLimitError,
  PayloadTooLargeError,
  GoneError,
  QuotaExceededError,
  BadRequestError,
  ExecutionError,
  CeilingError,
  TimeoutError,
  UnprocessableEntityError,
  InputProvenanceRejectedError,
  SecretsInjectionRejectedError,
} from "./errors.js";

// Pagination + Usage (Batch 33-foundation)
export { Page } from "./pagination.js";
export { Usage } from "./resources/usage.js";
export type {
  UsageItem,
  UsageSummary,
  UsageEstimate,
  ListItemsOptions,
} from "./resources/usage.js";

// Batch 3 sub-item 1 (CR-002, 2026-05-27) — Billing meter readout surface.
// Programmatic mirror of Stripe metered billing data. Per-pattern,
// per-shape, per-capsulefile breakdowns + cost forecast. Counts are
// STRUCTURAL FACTS per feedback_narrow_signed_claim_auditor_certifies.md.
export { Billing } from "./billing.js";
export type {
  PatternUsage,
  ShapeUsage,
  CapsulefileUsage,
  BillingMeterUsage,
  CostForecast,
  MeterDateRange,
} from "./billing.js";

// Offline AuditProof verifier (Batch 33-foundation sub-D)
export { verify, GENESIS_HASH, CANONICAL_SUBSYSTEMS } from "./verifier/index.js";
export type { VerifyResult } from "./verifier/index.js";

// Wave-N verifier surface (ADR-039 + ADR-041, Wave B Item 6 — 2026-05-27)
export {
  GENESIS_SHA512_HEX as WAVE_N_GENESIS_SHA512_HEX,
  PARENT_PROOF_MAX_DEPTH,
  PATTERN_TAGS_WIRE,
  WaveNVerifyError,
  merklePairHash,
  merkleRootSha512NullSeparated,
  computeRecordReceiptsMerkleRoot,
  computeParentProofsMerkleRoot,
  buildMerkleInclusionProof,
  verifyMerkleInclusionProof,
  computeActivityRoot,
  computeRecordChainHash,
  computeStep8Base,
  computeStep8Amended,
  detectParentProofCycle,
  enforceDepthCap,
  verifyRecordReceipt,
  verifyFullAuditProof,
} from "./verifier/index.js";
export type {
  WaveNRecordReceipt,
  WaveNParentProofLink,
  WaveNVerifyResult,
  VerifyRecordReceiptOptions,
} from "./verifier/index.js";

// Batch + Merkle (Order 2 / Gap 1)
export {
  computeBatchMerkleRoot,
  parseResultsJsonl,
  serializeRecordsToJsonl,
} from "./batch.js";
export type { BatchRecord, BatchResult } from "./batch.js";
export {
  EMPTY_SHA512_HEX,
  merkleRootFromLeaves,
  combinedRecordLeaf,
  hashRecordBytes,
} from "./merkle.js";

// Session (Order 3 / Gap 2)
export { hashStateBytes, buildSessionMetadata } from "./session.js";
export type { SessionResult } from "./session.js";

// Customer-side helpers (Order 6 / Gap 3 — per-event signing)
export {
  Ed25519GeneratedKey,
  attestationToWireDict,
  canonicalizeEvent,
  signActivityEvent,
  verifyEventAttestation,
} from "./helpers/signer.js";
export type { EventAttestation, EventSigner } from "./helpers/signer.js";

// Cap-3 (Phase 1) — in-capsule ActivityEvent emission helpers
export {
  DEFAULT_BUFFER_PATH,
  REASONING_STEP_KINDS,
  TOOL_CALL_FAILURE_KINDS,
  canonicalHash,
  emitReasoningStepCompleted,
  emitToolCallAttempted,
  emitToolCallCompleted,
  emitToolCallFailed,
} from "./helpers/activity_events.js";
export type {
  EmitReasoningStepCompletedArgs,
  EmitToolCallAttemptedArgs,
  EmitToolCallCompletedArgs,
  EmitToolCallFailedArgs,
  ReasoningStepKind,
  ToolCallFailureKind,
} from "./helpers/activity_events.js";

// Compliance helpers
export {
  GdprContext,
  HipaaContext,
  Soc2Context,
  // Batch 7 sub-item 1 (2026-05-28) — 12 additional framework helpers.
  BipaContext,
  CcpaContext,
  CFRPart2Context,
  ColoradoAiActContext,
  CpraContext,
  HitrustContext,
  Iso42001Context,
  NistAiRmfContext,
  NycLl144Context,
  PciDssContext,
  TexasSb14Context,
  UtahAiPolicyActContext,
} from "./compliance/index.js";
export type {
  GdprContextOptions,
  GdprLegalBasis,
  HipaaContextOptions,
  Soc2ContextOptions,
  Soc2TrustServiceCriterion,
  // Batch 7 sub-item 1 — option + enum types.
  BipaContextOptions,
  BipaBiometricIdentifierType,
  BipaConsentBasis,
  CcpaContextOptions,
  CcpaBusinessRole,
  CcpaThresholdBasis,
  CcpaPiCategory,
  CFRPart2ContextOptions,
  CFRPart2DisclosureBasis,
  ColoradoAiActContextOptions,
  ColoradoAiActActorRole,
  ColoradoAiActConsequentialDecisionCategory,
  ColoradoAiActConsumerNoticeTiming,
  CpraContextOptions,
  CpraBusinessRole,
  CpraSpiCategory,
  CpraAdmtDesignation,
  HitrustContextOptions,
  HitrustAssessmentType,
  Iso42001ContextOptions,
  Iso42001AiSystemRole,
  Iso42001PdcaPhase,
  NistAiRmfContextOptions,
  NistAiRmfCoreFunction,
  NistAiRmfRiskCategory,
  NistAiRmfLifecycleStage,
  NycLl144ContextOptions,
  NycLl144EmployerRole,
  NycLl144DecisionType,
  PciDssContextOptions,
  PciDssMerchantLevel,
  PciDssAssessmentType,
  TexasSb14ContextOptions,
  TexasSb14DeployerRole,
  TexasSb14HighRiskUseCase,
  UtahAiPolicyActContextOptions,
  UtahAiPolicyActActorRole,
  UtahAiPolicyActGenerativeAiUseCase,
} from "./compliance/index.js";

// Healthcare adapters (Batch 7 sub-item 2 — 2026-05-28)
// 13 healthcare modules ported from Python SDK (Tier 4 TS parity).
// Each adapter structurally parses a customer-supplied wire format and
// emits structural metadata for AuditProof binding; never interprets
// clinical content or certifies regulatory conformance.
export {
  AIFairnessContext,
  BulkFhirAdapter,
  kickoffToMetadata,
  statusToMetadata,
  CCDADocumentReference,
  CCDAReader,
  DICOMSRBinding,
  isValidDicomUid,
  SR_TEMPLATE_MEASUREMENT_REPORT,
  SR_TEMPLATE_AI_ANALYSIS_REPORT,
  SR_TEMPLATE_IMAGE_LIBRARY,
  SR_TEMPLATE_BASIC_DIAGNOSTIC_IMAGING_REPORT,
  DICOMwebAdapter,
  isRetrieve,
  isStore,
  isQuery,
  dicomwebTransactionToMetadata,
  DICOM_TAG_SOP_CLASS_UID,
  DICOM_TAG_SOP_INSTANCE_UID,
  DICOM_TAG_STUDY_INSTANCE_UID,
  DICOM_TAG_SERIES_INSTANCE_UID,
  DICOM_TAG_MODALITY,
  DICOM_TAG_TRANSFER_SYNTAX_UID,
  EpicLaunchContext,
  hasPatientContext,
  hasEncounterContext,
  hasUserContext,
  isProduction,
  epicLaunchContextToMetadata,
  FDASaMDContext,
  HL7v2Adapter,
  messageTypeWithTrigger,
  parsedMessageToMetadata,
  IheAdapter,
  IheTransactionType,
  IheBindingError,
  isIheSubmission,
  isIheRetrieve,
  isIheQuery,
  iheTransactionToMetadata,
  NCPDPAdapter,
  isControlledSubstance,
  ncpdpToMetadata,
  X12ServicesReviewAdapter,
  is278Request,
  is278Response,
  is278Approved,
  is278Denied,
  is278Pended,
  x12_278_toMetadata,
  X12_278_CERT_ACTIONS,
  X12_278_REVIEW_TYPES,
  X12_278_CERT_REQUEST_CATEGORIES,
  X12EligibilityAdapter,
  is270Inquiry,
  is271Response,
  x12EligibilityToMetadata,
  X12_270_COMMON_SERVICE_TYPES,
  X12FullRCMAdapter,
  isClaim,
  isRemittance,
  isEnrollment,
  hasDeniedClaims,
  x12RcmToMetadata,
  X12_834_MAINTENANCE_CODES,
  X12_835_CLAIM_STATUS_CODES,
} from "./healthcare/index.js";
export type {
  AIFairnessContextOptions,
  AIFairnessEvaluationMethodology,
  AIFairnessReEvaluationCadence,
  BulkFhirKickoff,
  BulkFhirStatus,
  BulkFhirResource,
  BulkFhirEndpointType,
  BulkFhirStatusState,
  CCDADocumentFields,
  DICOMSRBindingFields,
  DICOMSRBindOptions,
  DicomSrCreator,
  DICOMwebTransaction,
  DicomwebVerbFamily,
  ParseDICOMwebResponseOptions,
  EpicLaunchContextResult,
  EpicDeploymentEnvironment,
  FDASaMDContextOptions,
  FDADeviceClass,
  FDASubmissionPathway,
  FDASubmissionStatus,
  FDAClinicalValidationStatus,
  HL7v2ParsedMessage,
  Hl7V2MessageType,
  IheTransaction,
  IheProfile,
  IheTransactionId,
  ParseIheEnvelopeOptions,
  NCPDPParsedTransaction,
  NcpdpTransactionFamily,
  DeaSchedule,
  X12ServicesReviewTransaction,
  X12_278_Direction,
  X12EligibilityTransaction,
  X12EligibilityTransactionSet,
  X12RCMTransaction,
  X12RCMTransactionSet,
} from "./healthcare/index.js";

// Native-framework middleware (Batch 7 sub-item 4 — 2026-05-28)
// Express.js + Next.js App Router middleware modules ported from Python SDK
// (Tier 4 TS parity; closes Batch 7). Tier-C native-framework integration —
// per-request capsule lifecycle bound to AuditProof with `cdp_kind: "request"`.
// Customer brings the framework; SDK provides structural middleware factories.
export {
  nanorixMiddleware,
  nanorixErrorHandler,
  expressToMetadata,
  EXPRESS_CDP_KIND_REQUEST,
  EXPRESS_WRAPPER_KIND,
  EXPRESS_RESPONSE_HEADER_AUDIT_PROOF_ID,
  EXPRESS_RESPONSE_HEADER_CDP_KIND,
  EXPRESS_RESPONSE_HEADER_CAPSULE_ID,
  withNanorix,
  wrapApiRoute,
  nextjsToMetadata,
  NEXTJS_CDP_KIND_REQUEST,
  NEXTJS_WRAPPER_KIND,
  NEXTJS_RESPONSE_HEADER_AUDIT_PROOF_ID,
  NEXTJS_RESPONSE_HEADER_CDP_KIND,
  NEXTJS_RESPONSE_HEADER_CAPSULE_ID,
} from "./integrations/index.js";
export type {
  NanorixMiddlewareOptions,
  NanorixErrorHandlerOptions,
  NanorixRequestContext,
  ExpressRequestLike,
  ExpressResponseLike,
  ExpressNextFunction,
  ExpressRequestHandler,
  ExpressErrorRequestHandler,
  ExpressNanorixClientLike,
  ExpressNanorixCapsuleLike,
  ExpressNanorixCapsuleResourceLike,
  ExpressNanorixLogger,
  NanorixNextOptions,
  NanorixRouteContext,
  NextRequestLike,
  NextResponseLike,
  NextRouteContext,
  NextRequestWithNanorix,
  NextMiddlewareHandler,
  NextRouteHandler,
} from "./integrations/index.js";

// LLM integration adapters (Batch 7 sub-item 3 — 2026-05-28)
// 7 LLM integration modules ported from Python SDK (Tier 4 TS parity).
// Each adapter is a structural callback class customers wire INTO their
// LLM framework code; no framework imports — customer brings the
// framework. Adapters emit ActivityEvents for AuditProof binding.
export {
  NanorixLangChainCallback,
  NanorixLangGraphCallback,
  NanorixLlamaIndexCallback,
  VALID_LLAMAINDEX_EVENTS,
  NanorixLiteLLMCallback,
  VALID_LITELLM_HOOKS,
  NanorixOtelGenaiTracer,
  OTEL_GENAI_ALLOWED_OPERATIONS,
  OTEL_GENAI_PROVIDER_PREFIX_MAP,
  OTEL_GENAI_REDACTED_PLACEHOLDER,
  OTEL_GENAI_SEMCONV_VERSION,
  SPAN_KIND_INTERNAL,
  SPAN_KIND_CLIENT,
  STATUS_CODE_UNSET,
  STATUS_CODE_OK,
  STATUS_CODE_ERROR,
  inferProviderFromModel,
  stripProviderPrefix,
  coerceAttrValue,
  NanorixAutoGenCallback,
  autogenExtractToolCalls,
  autogenExtractToolCallId,
  extractAutogenToolName,
  extractAutogenToolArgs,
  autogenRenderMessageForReasoning,
  NanorixCrewAICallback,
  crewaiExtractTaskId,
  crewaiExtractTaskName,
  crewaiExtractTaskDurationMs,
  crewaiRenderTaskArgs,
  crewaiRenderTaskResult,
  crewaiRenderStep,
} from "./integrations/index.js";
export type {
  NanorixLangChainCallbackOptions,
  NanorixLangGraphCallbackOptions,
  NanorixLlamaIndexCallbackOptions,
  LlamaIndexEventName,
  NanorixLiteLLMCallbackOptions,
  LiteLLMHookName,
  LiteLLMBeforeRequest,
  LiteLLMAfterSuccess,
  LiteLLMAfterFailure,
  NanorixOtelGenaiTracerOptions,
  OtelGenaiOperation,
  OtelTracerLike,
  OtelSpanLike,
  LlmCallSpanOptions,
  RecordResponseOptions,
  EmitLlmCallOptions,
  EmitToolCallOptions,
  NanorixAutoGenCallbackOptions,
  OnAgentMessageReceivedOptions,
  OnAgentMessageSentOptions,
  OnToolCallCompletedOptions,
  OnAgentErrorOptions,
  NanorixCrewAICallbackOptions,
  OnTaskFailedOptions,
  OnStepErrorOptions,
} from "./integrations/index.js";

// Constants
export { VERSION } from "./constants.js";

// Customer debugging tools (Wave 8A)
export {
  setTraceContext,
  getTraceContext,
  clearTraceContext,
  buildTraceHeaders,
  withRetry,
  downloadAuditProof,
  verifyAuditProof,
  toWireForm,
  FailureReasonType,
  GENESIS_HASH as DEBUG_GENESIS_HASH,
  METHOD_MAP,
  computeStepHash,
  lookupMethod,
} from "./debug.js";
export type {
  RetryOptions,
  FailureReason,
  VerificationMetadata,
  VerificationResult,
  VerificationWireForm,
  VerifierPolicy,
} from "./debug.js";

// JCS RFC 8785 canonicalization (Wave 8A)
export { canonicalize, canonicalizeBytes } from "./_jcs.js";

// ADR-040 control-map (Wave B 2026-05-26) — published mapping artifact
// `(jurisdiction × data_classification × pattern_tag) → string[]` consumed
// by GRC adapters at ingestion time. Three-layer separation: receipt
// (immutable) / mapping (versioned) / adapter (vendor pace).
export {
  FRAMEWORK_VERSION as CONTROL_MAP_FRAMEWORK_VERSION,
  SCHEMA_VERSION as CONTROL_MAP_SCHEMA_VERSION,
  DISCLAIMER as CONTROL_MAP_DISCLAIMER,
  MAPPING_2026_02,
  Mapping as ControlMapping,
  loadMapping,
  resolve as resolveControls,
  availableFrameworkVersions,
} from "./control_map.js";
export type {
  Jurisdiction as ControlMapJurisdiction,
  DataClassification as ControlMapDataClassification,
  PatternTag as ControlMapPatternTag,
} from "./control_map.js";

// Batch 1 Item 4 (2026-05-27) — cold-start benchmark SDK helper. Layer-2
// published artifact (sibling to ADR-040 control_map). Customer-facing
// p50/p95/p99 measurements per (substrate × pattern × Capsulefile shape)
// cell. STRUCTURAL FACTS only; NEVER a service-level commitment.
export {
  BENCHMARK_VERSION,
  SCHEMA_VERSION as BENCHMARK_SCHEMA_VERSION,
  DISCLAIMER as BENCHMARK_DISCLAIMER,
  BenchmarkArtifact,
  loadBenchmark,
  parseArtifact as parseBenchmarkArtifact,
  getP95 as getBenchmarkP95,
} from "./benchmark.js";
export type {
  Percentiles as BenchmarkPercentiles,
  CaptureContext as BenchmarkCaptureContext,
  MeasurementRun as BenchmarkMeasurementRun,
  Dimension as BenchmarkDimension,
  BenchmarkFilter,
  BenchmarkLookup,
} from "./benchmark.js";

// ADR-035 Batch 9 (2026-05-28) — C2PA LLM-output provenance.
// Sibling primitive next to AuditProof; AuditProof byte-form UNCHANGED
// (ADR-006 I0 affirmed). EU AI Act Article 50 (effective August 2026)
// interchange format. Customer-sole-declarer (ADR-013); three-role
// discipline (ADR-017); narrowness invariant preserved
// (feedback_narrowness_is_the_moat_resist_receipt_enrichment.md).
export {
  C2PAManifest,
  C2PAManifestBuilder,
  C2PA_AssertionType,
  C2PA_CLAIM_GENERATOR_PREFIX,
  C2PA_MANIFEST_FORMAT,
  C2PA_SIGNATURE_ALGORITHM,
  C2PA_DIGITAL_SOURCE_TYPE_AI,
  C2PA_ACTION_CREATED,
  C2PA_VALID_ASSERTION_TYPES,
  C2PA_FORBIDDEN_BRAND_LEAK_TOKENS,
  C2PA_FORBIDDEN_REGULATORY_TOKENS,
  C2PA_NARROW_CLAIM_DISCLAIMER,
  C2PA_MAX_MODEL_ID_LEN,
  C2PA_MAX_PROMPT_INTENT_LEN,
  C2PA_MAX_CAPSULE_ID_LEN,
  C2PA_MAX_AUDITPROOF_ID_LEN,
  C2PA_MAX_VERIFICATION_KEY_ID_LEN,
  C2PA_MAX_TIMESTAMP_LEN,
  verifyC2PAManifest,
} from "./integrations/index.js";
export type {
  C2PA_AssertionTypeValue,
  C2PAClaimGeneratorInfo,
  C2PASignatureInfo,
  C2PAManifestDict,
  C2PAVerificationResult,
  C2PA_WithCapsuleOptions,
  C2PA_WithModelOptions,
} from "./integrations/index.js";

// ADR-039 + ADR-041 Wave B Item 3 (2026-05-26) — per-record receipt + parent
// proof ergonomic surface. RecordContext + addParentProof on LiveCapsule.
// Three-layer separation enforced: receipts carry pattern_tag (signed) but
// NEVER control_tags; adapters apply control_map at ingestion time.
export {
  PATTERN_TAGS,
  RecordContext,
  MAX_RECORD_ID_LEN,
  MAX_HASH_LEN,
  MAX_PARENT_KEY_ID_LEN,
  MAX_PARENT_SIG_LEN,
  MAX_PARENT_ROLE_LEN,
  MAX_PARENT_JURISDICTION_LEN,
  MAX_PARENT_ORG_TAG_LEN,
  MAX_ACTIVITY_TRAIL_LEN,
  validatePatternTag,
  sha512Hex,
  canonicalHash as receiptCanonicalHash,
  buildParentLinkBody,
  validateParentLinkFields,
  withRecordContext,
} from "./capsule_record.js";
export type {
  PatternTag,
  RecordOptions,
  ParentProofOptions,
  ActivityEventInput,
  PostRecordCallback,
} from "./capsule_record.js";
