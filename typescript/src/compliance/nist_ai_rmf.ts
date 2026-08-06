/** NIST AI Risk Management Framework 1.0 context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "1.0";

/** NIST AI RMF 1.0 four core functions. */
export type NistAiRmfCoreFunction = "govern" | "map" | "measure" | "manage";

const VALID_CORE_FUNCTIONS: ReadonlySet<NistAiRmfCoreFunction> =
  new Set<NistAiRmfCoreFunction>(["govern", "map", "measure", "manage"]);

/** NIST AI RMF 1.0 trustworthy AI characteristics. */
export type NistAiRmfRiskCategory =
  | "validity_reliability"
  | "safety"
  | "security_resilience"
  | "accountability_transparency"
  | "explainability_interpretability"
  | "privacy"
  | "fairness_bias";

const VALID_RISK_CATEGORIES: ReadonlySet<NistAiRmfRiskCategory> =
  new Set<NistAiRmfRiskCategory>([
    "validity_reliability",
    "safety",
    "security_resilience",
    "accountability_transparency",
    "explainability_interpretability",
    "privacy",
    "fairness_bias",
  ]);

/** NIST AI RMF 1.0 AI lifecycle stages (§4 Map function). */
export type NistAiRmfLifecycleStage =
  | "plan_design"
  | "data_collection"
  | "build_model"
  | "verify_validate"
  | "deploy_use"
  | "operate_monitor";

const VALID_LIFECYCLE_STAGES: ReadonlySet<NistAiRmfLifecycleStage> =
  new Set<NistAiRmfLifecycleStage>([
    "plan_design",
    "data_collection",
    "build_model",
    "verify_validate",
    "deploy_use",
    "operate_monitor",
  ]);

/**
 * NIST AI Risk Management Framework 1.0 context for a capsule workload.
 *
 * Declares context for tagging an AI workload under the voluntary
 * NIST AI RMF 1.0. Does NOT certify, attest to satisfaction of, or
 * claim compliance with the framework. Substantive risk-management
 * determinations remain the customer's responsibility.
 */
export interface NistAiRmfContextOptions {
  /** Organization owning the AI system whose risk management is declared. Required. */
  aiSystemOwner: string;
  /** Use-case category (e.g., `'clinical-decision-support'`). Required. */
  useCaseCategory: string;
  /** Subset of core functions in scope — must be non-empty. */
  coreFunctionsInScope: NistAiRmfCoreFunction[];
  /** Optional subset of trustworthy AI characteristics. */
  riskCategories?: NistAiRmfRiskCategory[];
  /** Optional AI lifecycle stage. */
  lifecycleStage?: NistAiRmfLifecycleStage;
  /** Customer-side risk assessment record reference. */
  riskAssessmentReference?: string;
  /** Customer-side model-card document reference. */
  modelCardReference?: string;
  /** Reference to subgroup-performance evaluation results. */
  subgroupEvaluationReference?: string;
}

export class NistAiRmfContext {
  readonly aiSystemOwner: string;
  readonly useCaseCategory: string;
  readonly coreFunctionsInScope: ReadonlyArray<NistAiRmfCoreFunction>;
  readonly riskCategories: ReadonlyArray<NistAiRmfRiskCategory>;
  readonly lifecycleStage?: NistAiRmfLifecycleStage;
  readonly riskAssessmentReference?: string;
  readonly modelCardReference?: string;
  readonly subgroupEvaluationReference?: string;

  /** Canonical framework identifier. */
  get frameworkId(): string {
    return "NIST_AI_RMF_1.0";
  }

  /** The four canonical NIST AI RMF 1.0 core functions (defensive copy). */
  get coreFunctions(): NistAiRmfCoreFunction[] {
    return ["govern", "map", "measure", "manage"];
  }

  /** The seven canonical trustworthy AI characteristics (defensive copy). */
  get trustworthyAiCharacteristics(): NistAiRmfRiskCategory[] {
    return [
      "validity_reliability",
      "safety",
      "security_resilience",
      "accountability_transparency",
      "explainability_interpretability",
      "privacy",
      "fairness_bias",
    ];
  }

  constructor(opts: NistAiRmfContextOptions) {
    if (!opts.aiSystemOwner) {
      throw new Error("aiSystemOwner is required and must be non-empty");
    }
    if (!opts.useCaseCategory) {
      throw new Error("useCaseCategory is required and must be non-empty");
    }
    if (!opts.coreFunctionsInScope || opts.coreFunctionsInScope.length === 0) {
      throw new Error(
        "coreFunctionsInScope is required and must be non-empty " +
          "(pick at least one of: govern, map, measure, manage)",
      );
    }
    opts.coreFunctionsInScope.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `coreFunctionsInScope[${idx}] must be a string (got ${typeof item})`,
        );
      }
      if (!VALID_CORE_FUNCTIONS.has(item)) {
        throw new Error(
          `coreFunctionsInScope[${idx}]=${JSON.stringify(item)} not in ` +
            `${JSON.stringify(Array.from(VALID_CORE_FUNCTIONS))}`,
        );
      }
    });
    validateNoCommaInList(
      "coreFunctionsInScope",
      [...opts.coreFunctionsInScope],
    );

    const riskCategories = opts.riskCategories ?? [];
    if (riskCategories.length > 0) {
      riskCategories.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `riskCategories[${idx}] must be a string (got ${typeof item})`,
          );
        }
        if (!VALID_RISK_CATEGORIES.has(item)) {
          throw new Error(
            `riskCategories[${idx}]=${JSON.stringify(item)} not in ` +
              `${JSON.stringify(Array.from(VALID_RISK_CATEGORIES))} ` +
              `(seven trustworthy AI characteristics)`,
          );
        }
      });
      validateNoCommaInList("riskCategories", [...riskCategories]);
    }

    if (opts.lifecycleStage !== undefined) {
      if (!VALID_LIFECYCLE_STAGES.has(opts.lifecycleStage)) {
        throw new Error(
          `lifecycleStage=${JSON.stringify(opts.lifecycleStage)} not in ` +
            `${JSON.stringify(Array.from(VALID_LIFECYCLE_STAGES))}`,
        );
      }
    }

    validateValueLength("aiSystemOwner", opts.aiSystemOwner);
    validateValueLength("useCaseCategory", opts.useCaseCategory);
    validateValueLength(
      "coreFunctionsInScope(joined)",
      opts.coreFunctionsInScope.join(","),
    );
    if (riskCategories.length > 0) {
      validateValueLength("riskCategories(joined)", riskCategories.join(","));
    }
    if (opts.lifecycleStage !== undefined) {
      validateValueLength("lifecycleStage", opts.lifecycleStage);
    }
    if (opts.riskAssessmentReference !== undefined) {
      validateValueLength(
        "riskAssessmentReference",
        opts.riskAssessmentReference,
      );
    }
    if (opts.modelCardReference !== undefined) {
      validateValueLength("modelCardReference", opts.modelCardReference);
    }
    if (opts.subgroupEvaluationReference !== undefined) {
      validateValueLength(
        "subgroupEvaluationReference",
        opts.subgroupEvaluationReference,
      );
    }

    this.aiSystemOwner = opts.aiSystemOwner;
    this.useCaseCategory = opts.useCaseCategory;
    this.coreFunctionsInScope = Object.freeze([...opts.coreFunctionsInScope]);
    this.riskCategories = Object.freeze([...riskCategories]);
    this.lifecycleStage = opts.lifecycleStage;
    this.riskAssessmentReference = opts.riskAssessmentReference;
    this.modelCardReference = opts.modelCardReference;
    this.subgroupEvaluationReference = opts.subgroupEvaluationReference;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      ai_system_owner: this.aiSystemOwner,
      use_case_category: this.useCaseCategory,
      core_functions_in_scope: this.coreFunctionsInScope.join(","),
    };
    if (this.riskCategories.length > 0) {
      meta.risk_categories = this.riskCategories.join(",");
    }
    if (this.lifecycleStage !== undefined) {
      meta.lifecycle_stage = this.lifecycleStage;
    }
    if (this.riskAssessmentReference !== undefined) {
      meta.risk_assessment_reference = this.riskAssessmentReference;
    }
    if (this.modelCardReference !== undefined) {
      meta.model_card_reference = this.modelCardReference;
    }
    if (this.subgroupEvaluationReference !== undefined) {
      meta.subgroup_evaluation_reference = this.subgroupEvaluationReference;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
