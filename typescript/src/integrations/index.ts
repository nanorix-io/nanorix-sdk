/**
 * LLM integration adapter module — Tier A structural callbacks.
 *
 * Each adapter provides a structural callback class that customers wire
 * INTO their LLM framework code (LangChain.js / LangGraph.js /
 * LlamaIndex.ts / LiteLLM proxy / @opentelemetry/api / AutoGen-shape /
 * CrewAI-shape). The Nanorix integration does NOT depend on the
 * framework's runtime — it provides the callback shape that emits
 * AuditProof metadata when wired in.
 *
 * Adapters shipped:
 *
 * - `NanorixLangChainCallback`     — LangChain.js callback handler
 * - `NanorixLangGraphCallback`     — LangGraph.js callback handler (node-aware)
 * - `NanorixLlamaIndexCallback`    — LlamaIndex.ts event-bus subscriber
 * - `NanorixLiteLLMCallback`       — LiteLLM HTTP-API request/response wrap
 * - `NanorixOtelGenaiTracer`       — OpenTelemetry GenAI semconv exporter
 * - `NanorixAutoGenCallback`       — AutoGen-shape structural callback
 * - `NanorixCrewAICallback`        — CrewAI-shape structural callback
 *
 * Each adapter is byte-equivalent (event JSON shape) to its Python SDK
 * counterpart under `nanorix.integrations.*`. The 4 ActivityEvent
 * variants shipped by cover the entire surface — no new
 * variants — Forever-Standard preserved.
 *
 * Customer wraps once at agent setup time:
 *
 * ```ts
 * import { NanorixLangChainCallback } from "@nanorix/sdk";
 *
 * const callback = new NanorixLangChainCallback({
 *   framework: "HIPAA",
 *   capsulefile: "agent",
 * });
 * await chain.invoke(
 *   { input: "..." },
 *   { callbacks: [callback.asLangChainCallback()] },
 * );
 * ```
 *
 * Narrow signed claim discipline: every emitted ActivityEvent records
 * structural facts (event happened at time T, with structural
 * identifiers). NEVER asserts framework correctness, LLM-output safety,
 * hallucination-freedom, or regulatory-framework conformance (per
 *).
 */

export { NanorixLangChainCallback } from "./langchain.js";
export type { NanorixLangChainCallbackOptions } from "./langchain.js";

export { NanorixLangGraphCallback } from "./langgraph.js";
export type { NanorixLangGraphCallbackOptions } from "./langgraph.js";

export {
  NanorixLlamaIndexCallback,
  VALID_LLAMAINDEX_EVENTS,
} from "./llama_index.js";
export type {
  NanorixLlamaIndexCallbackOptions,
  LlamaIndexEventName,
} from "./llama_index.js";

export {
  NanorixLiteLLMCallback,
  VALID_LITELLM_HOOKS,
} from "./litellm.js";
export type {
  NanorixLiteLLMCallbackOptions,
  LiteLLMHookName,
  LiteLLMBeforeRequest,
  LiteLLMAfterSuccess,
  LiteLLMAfterFailure,
} from "./litellm.js";

export {
  NanorixOtelGenaiTracer,
  ALLOWED_OPERATIONS as OTEL_GENAI_ALLOWED_OPERATIONS,
  PROVIDER_PREFIX_MAP as OTEL_GENAI_PROVIDER_PREFIX_MAP,
  REDACTED_PLACEHOLDER as OTEL_GENAI_REDACTED_PLACEHOLDER,
  OTEL_GENAI_SEMCONV_VERSION,
  SPAN_KIND_INTERNAL,
  SPAN_KIND_CLIENT,
  STATUS_CODE_UNSET,
  STATUS_CODE_OK,
  STATUS_CODE_ERROR,
  inferProviderFromModel,
  stripProviderPrefix,
  coerceAttrValue,
} from "./otel_genai.js";
export type {
  NanorixOtelGenaiTracerOptions,
  OtelGenaiOperation,
  OtelTracerLike,
  OtelSpanLike,
  LlmCallSpanOptions,
  RecordResponseOptions,
  EmitLlmCallOptions,
  EmitToolCallOptions,
} from "./otel_genai.js";

export {
  NanorixAutoGenCallback,
  extractToolCalls as autogenExtractToolCalls,
  extractToolCallId as autogenExtractToolCallId,
  extractAutogenToolName,
  extractAutogenToolArgs,
  renderMessageForReasoning as autogenRenderMessageForReasoning,
} from "./autogen.js";
export type {
  NanorixAutoGenCallbackOptions,
  OnAgentMessageReceivedOptions,
  OnAgentMessageSentOptions,
  OnToolCallCompletedOptions,
  OnAgentErrorOptions,
} from "./autogen.js";

export {
  NanorixCrewAICallback,
  extractTaskId as crewaiExtractTaskId,
  extractTaskName as crewaiExtractTaskName,
  extractTaskDurationMs as crewaiExtractTaskDurationMs,
  renderTaskArgs as crewaiRenderTaskArgs,
  renderTaskResult as crewaiRenderTaskResult,
  renderStep as crewaiRenderStep,
} from "./crewai.js";
export type {
  NanorixCrewAICallbackOptions,
  OnTaskFailedOptions,
  OnStepErrorOptions,
} from "./crewai.js";

// Shared framework label constants — re-exported once from langchain.ts
// only (Python convention: framework labels are stable across all
// integrations, so we expose a single canonical set).
export {
  FRAMEWORK_HIPAA,
  FRAMEWORK_GDPR,
  FRAMEWORK_SOC2,
  FRAMEWORK_PCI_DSS,
  FRAMEWORK_HITRUST,
  FRAMEWORK_EU_AI_ACT,
  ALLOWED_FRAMEWORKS,
} from "./langchain.js";

// Tier C native-framework middleware.
// Express.js (Node WSGI-shape; Flask analog) + Next.js App Router
// (Web-API shape; FastAPI analog). Each wraps an inbound HTTP request
// in a Nanorix capsule lifecycle and emits an AuditProof with
// `cdp_kind: "request"`.
export {
  nanorixMiddleware,
  nanorixErrorHandler,
  toMetadata as expressToMetadata,
  CDP_KIND_REQUEST as EXPRESS_CDP_KIND_REQUEST,
  WRAPPER_KIND as EXPRESS_WRAPPER_KIND,
  RESPONSE_HEADER_AUDIT_PROOF_ID as EXPRESS_RESPONSE_HEADER_AUDIT_PROOF_ID,
  RESPONSE_HEADER_CDP_KIND as EXPRESS_RESPONSE_HEADER_CDP_KIND,
  RESPONSE_HEADER_CAPSULE_ID as EXPRESS_RESPONSE_HEADER_CAPSULE_ID,
} from "./express_middleware.js";
export type {
  NanorixMiddlewareOptions,
  NanorixErrorHandlerOptions,
  NanorixRequestContext,
  ExpressRequestLike,
  ExpressResponseLike,
  ExpressNextFunction,
  ExpressRequestHandler,
  ExpressErrorRequestHandler,
  NanorixClientLike as ExpressNanorixClientLike,
  NanorixCapsuleLike as ExpressNanorixCapsuleLike,
  NanorixCapsuleResourceLike as ExpressNanorixCapsuleResourceLike,
  NanorixLogger as ExpressNanorixLogger,
} from "./express_middleware.js";

export {
  withNanorix,
  wrapApiRoute,
  toMetadata as nextjsToMetadata,
  CDP_KIND_REQUEST as NEXTJS_CDP_KIND_REQUEST,
  WRAPPER_KIND as NEXTJS_WRAPPER_KIND,
  RESPONSE_HEADER_AUDIT_PROOF_ID as NEXTJS_RESPONSE_HEADER_AUDIT_PROOF_ID,
  RESPONSE_HEADER_CDP_KIND as NEXTJS_RESPONSE_HEADER_CDP_KIND,
  RESPONSE_HEADER_CAPSULE_ID as NEXTJS_RESPONSE_HEADER_CAPSULE_ID,
} from "./nextjs_middleware.js";
export type {
  NanorixNextOptions,
  NanorixRouteContext,
  NextRequestLike,
  NextResponseLike,
  NextRouteContext,
  NextRequestWithNanorix,
  NextMiddlewareHandler,
  NextRouteHandler,
} from "./nextjs_middleware.js";

// C2PA LLM-output provenance.
// Sibling primitive next to AuditProof; AuditProof byte-form unchanged.
// EU AI Act Article 50 (effective August 2026) interchange format.
export {
  AssertionType as C2PA_AssertionType,
  C2PAManifest,
  C2PAManifestBuilder,
  CLAIM_GENERATOR_PREFIX as C2PA_CLAIM_GENERATOR_PREFIX,
  MANIFEST_FORMAT as C2PA_MANIFEST_FORMAT,
  SIGNATURE_ALGORITHM as C2PA_SIGNATURE_ALGORITHM,
  DIGITAL_SOURCE_TYPE_AI as C2PA_DIGITAL_SOURCE_TYPE_AI,
  C2PA_ACTION_CREATED,
  VALID_ASSERTION_TYPES as C2PA_VALID_ASSERTION_TYPES,
  FORBIDDEN_BRAND_LEAK_TOKENS as C2PA_FORBIDDEN_BRAND_LEAK_TOKENS,
  FORBIDDEN_REGULATORY_TOKENS as C2PA_FORBIDDEN_REGULATORY_TOKENS,
  NARROW_CLAIM_DISCLAIMER as C2PA_NARROW_CLAIM_DISCLAIMER,
  MAX_MODEL_ID_LEN as C2PA_MAX_MODEL_ID_LEN,
  MAX_PROMPT_INTENT_LEN as C2PA_MAX_PROMPT_INTENT_LEN,
  MAX_CAPSULE_ID_LEN as C2PA_MAX_CAPSULE_ID_LEN,
  MAX_AUDITPROOF_ID_LEN as C2PA_MAX_AUDITPROOF_ID_LEN,
  MAX_VERIFICATION_KEY_ID_LEN as C2PA_MAX_VERIFICATION_KEY_ID_LEN,
  MAX_TIMESTAMP_LEN as C2PA_MAX_TIMESTAMP_LEN,
  verifyC2PAManifest,
} from "./c2pa_provenance.js";
export type {
  AssertionTypeValue as C2PA_AssertionTypeValue,
  C2PAClaimGeneratorInfo,
  C2PASignatureInfo,
  C2PAManifestDict,
  C2PAVerificationResult,
  WithCapsuleOptions as C2PA_WithCapsuleOptions,
  WithModelOptions as C2PA_WithModelOptions,
} from "./c2pa_provenance.js";
