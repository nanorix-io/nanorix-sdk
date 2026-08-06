/** ISO/IEC 42001:2023 (AI Management System) context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "2023";

/** ISO/IEC 42001:2023 §3 AI system role taxonomy. */
export type Iso42001AiSystemRole =
  | "ai_provider"
  | "ai_developer"
  | "ai_user"
  | "ai_deployer";

const VALID_AI_SYSTEM_ROLES: ReadonlySet<Iso42001AiSystemRole> =
  new Set<Iso42001AiSystemRole>([
    "ai_provider",
    "ai_developer",
    "ai_user",
    "ai_deployer",
  ]);

/** ISO/IEC 42001:2023 §6-§10 management-system PDCA cycle phases. */
export type Iso42001PdcaPhase = "plan" | "do" | "check" | "act";

const VALID_PDCA_PHASES: ReadonlySet<Iso42001PdcaPhase> =
  new Set<Iso42001PdcaPhase>(["plan", "do", "check", "act"]);

/** 23 ISO/IEC 42001:2023 Annex A controls in canonical A.2.x..A.10.x order. */
const ANNEX_A_CONTROLS: readonly string[] = Object.freeze([
  "A_2_2_ai_policy",
  "A_2_3_alignment_with_other_organizational_policies",
  "A_2_4_review_of_ai_policy",
  "A_3_2_ai_roles_and_responsibilities",
  "A_3_3_reporting_of_concerns",
  "A_4_2_resources_for_ai_systems",
  "A_4_3_tooling_resources",
  "A_4_4_system_and_computing_resources",
  "A_4_5_human_resources",
  "A_4_6_data_resources",
  "A_5_2_ai_system_impact_assessment_process",
  "A_5_3_documentation_of_ai_system_impact_assessment",
  "A_5_4_assessment_of_ai_system_impact_on_individuals_or_groups",
  "A_5_5_assessment_of_societal_impacts_of_ai_systems",
  "A_6_1_2_objectives_for_responsible_ai_development",
  "A_6_2_2_ai_system_lifecycle_processes",
  "A_6_2_3_documentation_of_ai_system_design_and_development",
  "A_6_2_4_data_for_development_and_enhancement",
  "A_6_2_5_acquisition_of_ai_systems",
  "A_6_2_6_ai_system_release_management",
  "A_7_2_information_for_interested_parties_of_ai_systems",
  "A_7_3_external_reporting",
  "A_7_4_communication_of_incidents",
]);
const ANNEX_A_CONTROL_SET = new Set<string>(ANNEX_A_CONTROLS);

/**
 * ISO/IEC 42001:2023 context for a capsule workload.
 *
 * Declares context for tagging an AI workload under the ISO/IEC
 * 42001:2023 AI Management System standard. Does NOT certify, attest
 * to satisfaction of, or claim compliance with ISO/IEC 42001:2023.
 * AIMS scope determination, control applicability, and certification
 * audits remain the customer's accredited certification body's
 * responsibility.
 */
export interface Iso42001ContextOptions {
  /** The certified organization owning the AIMS. Required. */
  organization: string;
  /** ISO/IEC 42001:2023 §3 role with respect to this AI system. Required. */
  aiSystemRole: Iso42001AiSystemRole;
  /** High-level AI system purpose. Required. */
  aiSystemPurpose: string;
  /** Non-empty subset of PDCA cycle phases in scope. Required. */
  pdcaPhasesInScope: Iso42001PdcaPhase[];
  /** Optional subset of Annex A controls in scope. */
  annexAControlsInScope?: string[];
  /** ISO/IEC 17021-1-accredited certification body. */
  certificationBody?: string;
  /** Status tag (e.g., `'pursuing'`, `'certified'`). */
  certificationStatus?: string;
  /** Customer-side certificate reference. */
  certReference?: string;
  /** AIMS scope statement reference (Clause 4.3). */
  aimsScopeStatementReference?: string;
  /** Who initiated this workload. */
  workforceMemberRole?: string;
}

export class Iso42001Context {
  readonly organization: string;
  readonly aiSystemRole: Iso42001AiSystemRole;
  readonly aiSystemPurpose: string;
  readonly pdcaPhasesInScope: ReadonlyArray<Iso42001PdcaPhase>;
  readonly annexAControlsInScope: ReadonlyArray<string>;
  readonly certificationBody?: string;
  readonly certificationStatus?: string;
  readonly certReference?: string;
  readonly aimsScopeStatementReference?: string;
  readonly workforceMemberRole?: string;

  /** Canonical framework identifier. */
  get frameworkId(): string {
    return "ISO_IEC_42001_2023";
  }

  /** The four canonical PDCA cycle phases (defensive copy). */
  get pdcaPhases(): Iso42001PdcaPhase[] {
    return ["plan", "do", "check", "act"];
  }

  /** The 23 canonical Annex A control identifiers (defensive copy). */
  get annexAControls(): string[] {
    return [...ANNEX_A_CONTROLS];
  }

  constructor(opts: Iso42001ContextOptions) {
    if (!opts.organization) {
      throw new Error("organization is required and must be non-empty");
    }
    if (!opts.aiSystemRole) {
      throw new Error("aiSystemRole is required and must be non-empty");
    }
    if (!VALID_AI_SYSTEM_ROLES.has(opts.aiSystemRole)) {
      throw new Error(
        `aiSystemRole=${JSON.stringify(opts.aiSystemRole)} not in ` +
          `${JSON.stringify(Array.from(VALID_AI_SYSTEM_ROLES))} ` +
          `(ISO/IEC 42001:2023 §3 AI system role taxonomy)`,
      );
    }
    if (!opts.aiSystemPurpose) {
      throw new Error("aiSystemPurpose is required and must be non-empty");
    }
    if (!opts.pdcaPhasesInScope || opts.pdcaPhasesInScope.length === 0) {
      throw new Error(
        "pdcaPhasesInScope is required and must be non-empty " +
          "(pick at least one of: plan, do, check, act)",
      );
    }
    opts.pdcaPhasesInScope.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `pdcaPhasesInScope[${idx}] must be a string (got ${typeof item})`,
        );
      }
      if (!VALID_PDCA_PHASES.has(item)) {
        throw new Error(
          `pdcaPhasesInScope[${idx}]=${JSON.stringify(item)} not in ` +
            `${JSON.stringify(Array.from(VALID_PDCA_PHASES))}`,
        );
      }
    });
    validateNoCommaInList("pdcaPhasesInScope", [...opts.pdcaPhasesInScope]);

    const annexAControls = opts.annexAControlsInScope ?? [];
    if (annexAControls.length > 0) {
      annexAControls.forEach((item, idx) => {
        if (typeof item !== "string") {
          throw new Error(
            `annexAControlsInScope[${idx}] must be a string ` +
              `(got ${typeof item})`,
          );
        }
        if (!ANNEX_A_CONTROL_SET.has(item)) {
          throw new Error(
            `annexAControlsInScope[${idx}]=${JSON.stringify(item)} not in ` +
              `the 23 canonical ISO/IEC 42001:2023 Annex A controls ` +
              `(introspect via Iso42001Context.annexAControls)`,
          );
        }
      });
      validateNoCommaInList("annexAControlsInScope", annexAControls);
    }

    validateValueLength("organization", opts.organization);
    validateValueLength("aiSystemRole", opts.aiSystemRole);
    validateValueLength("aiSystemPurpose", opts.aiSystemPurpose);
    validateValueLength(
      "pdcaPhasesInScope(joined)",
      opts.pdcaPhasesInScope.join(","),
    );
    if (annexAControls.length > 0) {
      validateValueLength(
        "annexAControlsInScope(joined)",
        annexAControls.join(","),
      );
    }
    if (opts.certificationBody !== undefined) {
      validateValueLength("certificationBody", opts.certificationBody);
    }
    if (opts.certificationStatus !== undefined) {
      validateValueLength("certificationStatus", opts.certificationStatus);
    }
    if (opts.certReference !== undefined) {
      validateValueLength("certReference", opts.certReference);
    }
    if (opts.aimsScopeStatementReference !== undefined) {
      validateValueLength(
        "aimsScopeStatementReference",
        opts.aimsScopeStatementReference,
      );
    }
    if (opts.workforceMemberRole !== undefined) {
      validateValueLength("workforceMemberRole", opts.workforceMemberRole);
    }

    this.organization = opts.organization;
    this.aiSystemRole = opts.aiSystemRole;
    this.aiSystemPurpose = opts.aiSystemPurpose;
    this.pdcaPhasesInScope = Object.freeze([...opts.pdcaPhasesInScope]);
    this.annexAControlsInScope = Object.freeze([...annexAControls]);
    this.certificationBody = opts.certificationBody;
    this.certificationStatus = opts.certificationStatus;
    this.certReference = opts.certReference;
    this.aimsScopeStatementReference = opts.aimsScopeStatementReference;
    this.workforceMemberRole = opts.workforceMemberRole;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: this.frameworkId,
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      organization: this.organization,
      ai_system_role: this.aiSystemRole,
      ai_system_purpose: this.aiSystemPurpose,
      pdca_phases_in_scope: this.pdcaPhasesInScope.join(","),
    };
    if (this.annexAControlsInScope.length > 0) {
      meta.annex_a_controls_in_scope = this.annexAControlsInScope.join(",");
    }
    if (this.certificationBody !== undefined) {
      meta.certification_body = this.certificationBody;
    }
    if (this.certificationStatus !== undefined) {
      meta.certification_status = this.certificationStatus;
    }
    if (this.certReference !== undefined) {
      meta.cert_reference = this.certReference;
    }
    if (this.aimsScopeStatementReference !== undefined) {
      meta.aims_scope_statement_reference = this.aimsScopeStatementReference;
    }
    if (this.workforceMemberRole !== undefined) {
      meta.workforce_member_role = this.workforceMemberRole;
    }
    validateMetadataEnvelope(meta);
    return meta;
  }
}
