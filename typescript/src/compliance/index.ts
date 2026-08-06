/**
 * Compliance helper module — three-role discipline.
 *
 * Per ADR-014 + ADR-017: declares framework context for a workload's
 * metadata. Does NOT certify, attest to satisfaction of, or claim
 * compliance with any framework. Compliance determination remains the
 * customer's auditor's responsibility.
 *
 * Frameworks shipped:
 *   - HipaaContext             (US healthcare, 45 CFR Parts 160-164)
 *   - GdprContext              (EU data protection, Regulation (EU) 2016/679)
 *   - Soc2Context              (SOC 2 / Trust Services Criteria 2017)
 *   - BipaContext              (Illinois Biometric Information Privacy Act, 740 ILCS 14)
 *   - CcpaContext              (California Consumer Privacy Act)
 *   - CFRPart2Context          (42 CFR Part 2 — Substance Use Disorder records)
 *   - ColoradoAiActContext     (Colorado AI Act, SB24-205)
 *   - CpraContext              (California Privacy Rights Act — CCPA amendment)
 *   - HitrustContext           (HITRUST CSF v11)
 *   - Iso42001Context          (ISO/IEC 42001:2023 AI Management System)
 *   - NistAiRmfContext         (NIST AI Risk Management Framework 1.0)
 *   - NycLl144Context          (NYC Local Law 144 of 2021 — AEDT hiring law)
 *   - PciDssContext            (PCI DSS v4.0)
 *   - TexasSb14Context         (Texas SB 14 — TRAIGA)
 *   - UtahAiPolicyActContext   (Utah AI Policy Act, SB 149)
 *
 * Customer wraps once at capsule create:
 *
 * ```ts
 * import { NanorixClient } from "@nanorix/sdk";
 * import { HipaaContext } from "@nanorix/sdk/compliance";
 *
 * const client = new NanorixClient({ apiKey: "nrx_live_..." });
 *
 * const ctx = new HipaaContext({
 *   coveredEntity: "Acme Hospital",
 *   minimumNecessaryPurpose: "Inference for radiology workflow",
 * });
 * const capsule = await client.capsules.create({
 *   capsulefile: "cf_...",
 *   metadata: ctx.toMetadata(),
 * });
 * ```
 *
 * The metadata field binds into the AuditProof's `metadata_hash` per
 * ADR-010 A4. The customer's GRC platform (Vanta, Drata, Secureframe)
 * consumes the AuditProof's metadata field; the customer's auditor
 * evaluates against framework provisions; the related-to mappings
 * ship in the destruction-response `regulatory_context` field per
 * ADR-017.
 */

export { GdprContext } from "./gdpr.js";
export type { GdprContextOptions, GdprLegalBasis } from "./gdpr.js";
export { HipaaContext } from "./hipaa.js";
export type { HipaaContextOptions } from "./hipaa.js";
export { Soc2Context } from "./soc2.js";
export type { Soc2ContextOptions, Soc2TrustServiceCriterion } from "./soc2.js";

// Batch 7 sub-item 1 (2026-05-28) — 12 additional framework helpers.
export { BipaContext } from "./bipa.js";
export type {
  BipaContextOptions,
  BipaBiometricIdentifierType,
  BipaConsentBasis,
} from "./bipa.js";
export { CcpaContext } from "./ccpa.js";
export type {
  CcpaContextOptions,
  CcpaBusinessRole,
  CcpaThresholdBasis,
  CcpaPiCategory,
} from "./ccpa.js";
export { CFRPart2Context } from "./cfr_part_2.js";
export type {
  CFRPart2ContextOptions,
  CFRPart2DisclosureBasis,
} from "./cfr_part_2.js";
export { ColoradoAiActContext } from "./colorado_ai_act.js";
export type {
  ColoradoAiActContextOptions,
  ColoradoAiActActorRole,
  ColoradoAiActConsequentialDecisionCategory,
  ColoradoAiActConsumerNoticeTiming,
} from "./colorado_ai_act.js";
export { CpraContext } from "./cpra.js";
export type {
  CpraContextOptions,
  CpraBusinessRole,
  CpraSpiCategory,
  CpraAdmtDesignation,
} from "./cpra.js";
export { HitrustContext } from "./hitrust.js";
export type {
  HitrustContextOptions,
  HitrustAssessmentType,
} from "./hitrust.js";
export { Iso42001Context } from "./iso_42001.js";
export type {
  Iso42001ContextOptions,
  Iso42001AiSystemRole,
  Iso42001PdcaPhase,
} from "./iso_42001.js";
export { NistAiRmfContext } from "./nist_ai_rmf.js";
export type {
  NistAiRmfContextOptions,
  NistAiRmfCoreFunction,
  NistAiRmfRiskCategory,
  NistAiRmfLifecycleStage,
} from "./nist_ai_rmf.js";
export { NycLl144Context } from "./nyc_ll144.js";
export type {
  NycLl144ContextOptions,
  NycLl144EmployerRole,
  NycLl144DecisionType,
} from "./nyc_ll144.js";
export { PciDssContext } from "./pci_dss.js";
export type {
  PciDssContextOptions,
  PciDssMerchantLevel,
  PciDssAssessmentType,
} from "./pci_dss.js";
export { TexasSb14Context } from "./texas_sb_14.js";
export type {
  TexasSb14ContextOptions,
  TexasSb14DeployerRole,
  TexasSb14HighRiskUseCase,
} from "./texas_sb_14.js";
export { UtahAiPolicyActContext } from "./utah_ai_policy_act.js";
export type {
  UtahAiPolicyActContextOptions,
  UtahAiPolicyActActorRole,
  UtahAiPolicyActGenerativeAiUseCase,
} from "./utah_ai_policy_act.js";
