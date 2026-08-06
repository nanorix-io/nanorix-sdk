/**
 * Healthcare adapter module — three-role discipline.
 *
 * Each adapter structurally parses a customer-
 * supplied wire format (HL7 V2 / FHIR / DICOM / X12 / NCPDP / IHE /
 * C-CDA / Epic SMART) and emits structural metadata fields suitable
 * for AuditProof binding. Adapters NEVER interpret clinical content,
 * certify regulatory conformance, or substitute for downstream
 * adjudication.
 *
 * Adapters shipped:
 *  - AIFairnessContext    — AI fairness evaluation context (cross-vertical)
 *  - BulkFhirAdapter      — HL7 FHIR Bulk Data $export v2.0.0
 *  - CCDAReader           — Consolidated CDA R2.1 reader → FHIR Bundle
 *  - DICOMSRBinding       — DICOM SR (Structured Report) AuditProof binding
 *  - DICOMwebAdapter      — DICOMweb (WADO-RS / STOW-RS / QIDO-RS)
 *  - EpicLaunchContext    — Epic SMART-on-FHIR launch context binding
 *  - FDASaMDContext       — FDA Software as a Medical Device framework
 *  - HL7v2Adapter         — HL7 V2 ADT/ORU message structural adapter
 *  - IheAdapter           — IHE XDS-I.b / XDR profile mapping
 *  - NCPDPAdapter         — NCPDP SCRIPT + Telecom Standard
 *  - X12ServicesReviewAdapter — X12 EDI 278 services review / PA
 *  - X12EligibilityAdapter    — X12 EDI 270/271 eligibility
 *  - X12FullRCMAdapter        — X12 EDI 834/835/837 full RCM cycle
 *
 * Customer wraps once at capsule create:
 *
 * ```ts
 * import { NanorixClient } from "@nanorix/sdk";
 * import { HL7v2Adapter, parsedMessageToMetadata } from "@nanorix/sdk/healthcare";
 *
 * const adapter = new HL7v2Adapter();
 * const parsed = adapter.parseMessage(messageBytes);
 * const capsule = await client.capsules.create({
 *   capsulefile: "cf_...",
 *   metadata: parsedMessageToMetadata(parsed),
 * });
 * ```
 *
 * Narrow signed claim discipline: every metadata envelope is keyed by
 * the reserved `nanorix:framework` + `nanorix:framework_version` keys
 * plus structural identifiers only. Never asserts conformance with the
 * referenced framework (per
 * feedback_narrow_signed_claim_auditor_certifies.md).
 */

export { AIFairnessContext } from "./ai_fairness.js";
export type {
  AIFairnessContextOptions,
  AIFairnessEvaluationMethodology,
  AIFairnessReEvaluationCadence,
} from "./ai_fairness.js";

export {
  BulkFhirAdapter,
  kickoffToMetadata,
  statusToMetadata,
} from "./bulk_fhir.js";
export type {
  BulkFhirKickoff,
  BulkFhirStatus,
  BulkFhirResource,
  BulkFhirEndpointType,
  BulkFhirStatusState,
} from "./bulk_fhir.js";

export { CCDADocumentReference, CCDAReader } from "./ccda.js";
export type { CCDADocumentFields } from "./ccda.js";

export {
  DICOMSRBinding,
  isValidDicomUid,
  SR_TEMPLATE_MEASUREMENT_REPORT,
  SR_TEMPLATE_AI_ANALYSIS_REPORT,
  SR_TEMPLATE_IMAGE_LIBRARY,
  SR_TEMPLATE_BASIC_DIAGNOSTIC_IMAGING_REPORT,
} from "./dicom_sr.js";
export type {
  DICOMSRBindingFields,
  DICOMSRBindOptions,
  DicomSrCreator,
} from "./dicom_sr.js";

export {
  DICOMwebAdapter,
  isRetrieve,
  isStore,
  isQuery,
  transactionToMetadata as dicomwebTransactionToMetadata,
  DICOM_TAG_SOP_CLASS_UID,
  DICOM_TAG_SOP_INSTANCE_UID,
  DICOM_TAG_STUDY_INSTANCE_UID,
  DICOM_TAG_SERIES_INSTANCE_UID,
  DICOM_TAG_MODALITY,
  DICOM_TAG_TRANSFER_SYNTAX_UID,
} from "./dicomweb.js";
export type {
  DICOMwebTransaction,
  DicomwebVerbFamily,
  ParseDICOMwebResponseOptions,
} from "./dicomweb.js";

export {
  EpicLaunchContext,
  hasPatientContext,
  hasEncounterContext,
  hasUserContext,
  isProduction,
  resultToMetadata as epicLaunchContextToMetadata,
} from "./epic_smart.js";
export type {
  EpicLaunchContextResult,
  EpicDeploymentEnvironment,
} from "./epic_smart.js";

export { FDASaMDContext } from "./fda_samd.js";
export type {
  FDASaMDContextOptions,
  FDADeviceClass,
  FDASubmissionPathway,
  FDASubmissionStatus,
  FDAClinicalValidationStatus,
} from "./fda_samd.js";

export {
  HL7v2Adapter,
  messageTypeWithTrigger,
  parsedMessageToMetadata,
} from "./hl7_v2.js";
export type { HL7v2ParsedMessage, Hl7V2MessageType } from "./hl7_v2.js";

export {
  IheAdapter,
  IheTransactionType,
  IheBindingError,
  isIheSubmission,
  isIheRetrieve,
  isIheQuery,
  iheTransactionToMetadata,
} from "./ihe.js";
export type {
  IheTransaction,
  IheProfile,
  IheTransactionId,
  ParseIheEnvelopeOptions,
} from "./ihe.js";

export {
  NCPDPAdapter,
  isControlledSubstance,
  ncpdpToMetadata,
} from "./ncpdp.js";
export type {
  NCPDPParsedTransaction,
  NcpdpTransactionFamily,
  DeaSchedule,
} from "./ncpdp.js";

export {
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
} from "./x12_278.js";
export type {
  X12ServicesReviewTransaction,
  X12_278_Direction,
} from "./x12_278.js";

export {
  X12EligibilityAdapter,
  is270Inquiry,
  is271Response,
  x12EligibilityToMetadata,
  X12_270_COMMON_SERVICE_TYPES,
} from "./x12_eligibility.js";
export type {
  X12EligibilityTransaction,
  X12EligibilityTransactionSet,
} from "./x12_eligibility.js";

export {
  X12FullRCMAdapter,
  isClaim,
  isRemittance,
  isEnrollment,
  hasDeniedClaims,
  x12RcmToMetadata,
  X12_834_MAINTENANCE_CODES,
  X12_835_CLAIM_STATUS_CODES,
} from "./x12_full_rcm.js";
export type {
  X12RCMTransaction,
  X12RCMTransactionSet,
} from "./x12_full_rcm.js";
