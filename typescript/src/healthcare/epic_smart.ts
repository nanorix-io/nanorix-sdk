/**
 * Epic SMART-on-FHIR launch context → AuditProof metadata binding.
 *
 * SMART App Launch v2 baseline. Epic implements the standard scope
 * syntax + returns standard launch-context fields (patient, encounter,
 * fhirUser) in the token response. Helper's value-add is capturing the
 * Epic-issued launch context + deployment environment (Sandbox /
 * Vendor Test / Production) into AuditProof metadata.
 *
 * Three-role discipline (ADR-017): structural binding only; OAuth2
 * flow, token signature validation, Epic App Orchard approval, and
 * scope-grant determination remain the customer's responsibility.
 */

import {
  MAX_METADATA_KEYS,
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  estimateSerializedSize,
  validateValueLength,
} from "../compliance/_constraints.js";

const FRAMEWORK_VERSION = "smart-app-launch-v2";

/** Standard SMART App Launch v2 launch-context fields. */
const KNOWN_CONTEXT_FIELDS: ReadonlySet<string> = new Set<string>([
  "patient",
  "encounter",
  "fhirUser",
  "smart_style_url",
  "need_patient_banner",
  "intent",
  "tenant",
]);

/** Epic App Orchard deployment environments. */
export type EpicDeploymentEnvironment =
  | "epic_sandbox"
  | "epic_vendor_test"
  | "epic_production"
  | "epic_local_test"
  | "other";

const VALID_DEPLOYMENT_ENVIRONMENTS: ReadonlySet<EpicDeploymentEnvironment> =
  new Set<EpicDeploymentEnvironment>([
    "epic_sandbox",
    "epic_vendor_test",
    "epic_production",
    "epic_local_test",
    "other",
  ]);

/** Structural result for an Epic SMART-on-FHIR launch context. */
export interface EpicLaunchContextResult {
  epicClientId: string;
  deploymentEnvironment: EpicDeploymentEnvironment;
  patientFhirId?: string;
  encounterFhirId?: string;
  fhirUser?: string;
  tenant?: string;
  intent?: string;
  scopesGranted: ReadonlyArray<string>;
}

/** Whether the launch carries patient context. */
export function hasPatientContext(r: EpicLaunchContextResult): boolean {
  return r.patientFhirId !== undefined && !!r.patientFhirId;
}

/** Whether the launch carries encounter context. */
export function hasEncounterContext(r: EpicLaunchContextResult): boolean {
  return r.encounterFhirId !== undefined && !!r.encounterFhirId;
}

/** Whether the launch carries user context. */
export function hasUserContext(r: EpicLaunchContextResult): boolean {
  return r.fhirUser !== undefined && !!r.fhirUser;
}

/** Whether the launch is in Epic production environment. */
export function isProduction(r: EpicLaunchContextResult): boolean {
  return r.deploymentEnvironment === "epic_production";
}

/** Convert an EpicLaunchContextResult to capsule metadata envelope. */
export function resultToMetadata(
  r: EpicLaunchContextResult,
): Record<string, string> {
  const meta: Record<string, string> = {
    [RESERVED_FRAMEWORK_KEY]: "EpicSMART",
    [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
    epic_client_id: r.epicClientId,
    deployment_environment: r.deploymentEnvironment,
  };
  if (r.patientFhirId !== undefined && r.patientFhirId) {
    meta.patient_fhir_id = r.patientFhirId;
  }
  if (r.encounterFhirId !== undefined && r.encounterFhirId) {
    meta.encounter_fhir_id = r.encounterFhirId;
  }
  if (r.fhirUser !== undefined && r.fhirUser) {
    meta.fhir_user = r.fhirUser;
  }
  if (r.tenant !== undefined && r.tenant) {
    meta.tenant = r.tenant;
  }
  if (r.intent !== undefined && r.intent) {
    meta.intent = r.intent;
  }
  if (r.scopesGranted.length > 0) {
    meta.scopes_granted = r.scopesGranted.join(" ");
  }

  const keyCount = Object.keys(meta).length;
  if (keyCount > MAX_METADATA_KEYS) {
    throw new Error(
      `toMetadata produced ${keyCount} keys, max ${MAX_METADATA_KEYS}`,
    );
  }
  const size = estimateSerializedSize(meta);
  if (size > 4096) {
    throw new Error(`toMetadata serializes to ${size} bytes, max 4096`);
  }
  return meta;
}

/**
 * Epic SMART-on-FHIR launch context binder.
 *
 * Accepts an Epic-issued (decoded) OAuth2 token response and produces
 * an `EpicLaunchContextResult` with launch-context fields suitable for
 * AuditProof binding.
 */
export class EpicLaunchContext {
  private readonly _epicClientId: string;
  private readonly _deploymentEnvironment: EpicDeploymentEnvironment;

  constructor(opts: {
    epicClientId: string;
    deploymentEnvironment: EpicDeploymentEnvironment;
  }) {
    if (!opts.epicClientId) {
      throw new Error("epicClientId is required and must be non-empty");
    }
    if (!opts.deploymentEnvironment) {
      throw new Error(
        "deploymentEnvironment is required and must be non-empty",
      );
    }
    if (!VALID_DEPLOYMENT_ENVIRONMENTS.has(opts.deploymentEnvironment)) {
      throw new Error(
        `deploymentEnvironment=${JSON.stringify(opts.deploymentEnvironment)} not in ` +
          `${JSON.stringify(Array.from(VALID_DEPLOYMENT_ENVIRONMENTS).sort())}`,
      );
    }
    validateValueLength("epicClientId", opts.epicClientId);

    this._epicClientId = opts.epicClientId;
    this._deploymentEnvironment = opts.deploymentEnvironment;
  }

  get epicClientId(): string {
    return this._epicClientId;
  }

  get deploymentEnvironment(): EpicDeploymentEnvironment {
    return this._deploymentEnvironment;
  }

  get deploymentEnvironmentSet(): string[] {
    return Array.from(VALID_DEPLOYMENT_ENVIRONMENTS).sort();
  }

  get knownContextFields(): string[] {
    return Array.from(KNOWN_CONTEXT_FIELDS).sort();
  }

  /** Build a result from a decoded OAuth2 token response. */
  fromTokenResponse(
    tokenResponse: Record<string, unknown>,
  ): EpicLaunchContextResult {
    if (!tokenResponse || typeof tokenResponse !== "object") {
      throw new Error(
        `fromTokenResponse: tokenResponse must be an object (got ${typeof tokenResponse})`,
      );
    }
    const patient = tokenResponse.patient as string | undefined;
    const encounter = tokenResponse.encounter as string | undefined;
    const fhirUser = tokenResponse.fhirUser as string | undefined;
    const tenant = tokenResponse.tenant as string | undefined;
    const intent = tokenResponse.intent as string | undefined;
    const scope = (tokenResponse.scope as string | undefined) ?? "";

    const checks: Array<[string, unknown]> = [
      ["patient", patient],
      ["encounter", encounter],
      ["fhirUser", fhirUser],
      ["tenant", tenant],
      ["intent", intent],
    ];
    for (const [name, val] of checks) {
      if (val !== undefined && val !== null && typeof val !== "string") {
        throw new Error(
          `fromTokenResponse: tokenResponse[${JSON.stringify(name)}] must be a string ` +
            `(got ${typeof val})`,
        );
      }
    }
    if (typeof scope !== "string") {
      throw new Error(
        `fromTokenResponse: tokenResponse['scope'] must be a string ` +
          `(got ${typeof scope})`,
      );
    }

    const scopesGranted = scope ? scope.split(/\s+/).filter((s) => s) : [];

    if (patient) validateValueLength("patient_fhir_id", patient);
    if (encounter) validateValueLength("encounter_fhir_id", encounter);
    if (fhirUser) validateValueLength("fhir_user", fhirUser);
    if (tenant) validateValueLength("tenant", tenant);
    if (intent) validateValueLength("intent", intent);
    if (scopesGranted.length > 0) {
      validateValueLength(
        "scopes_granted(joined)",
        scopesGranted.join(" "),
      );
    }

    return {
      epicClientId: this._epicClientId,
      deploymentEnvironment: this._deploymentEnvironment,
      patientFhirId: patient || undefined,
      encounterFhirId: encounter || undefined,
      fhirUser: fhirUser || undefined,
      tenant: tenant || undefined,
      intent: intent || undefined,
      scopesGranted: Object.freeze([...scopesGranted]),
    };
  }

  /** Build a result from individually supplied components. */
  fromComponents(opts: {
    patientFhirId?: string;
    encounterFhirId?: string;
    fhirUser?: string;
    tenant?: string;
    intent?: string;
    scopesGranted?: string[];
  }): EpicLaunchContextResult {
    if (opts.patientFhirId !== undefined) {
      validateValueLength("patient_fhir_id", opts.patientFhirId);
    }
    if (opts.encounterFhirId !== undefined) {
      validateValueLength("encounter_fhir_id", opts.encounterFhirId);
    }
    if (opts.fhirUser !== undefined) {
      validateValueLength("fhir_user", opts.fhirUser);
    }
    if (opts.tenant !== undefined) {
      validateValueLength("tenant", opts.tenant);
    }
    if (opts.intent !== undefined) {
      validateValueLength("intent", opts.intent);
    }

    const scopesList = opts.scopesGranted ?? [];
    scopesList.forEach((sc, idx) => {
      if (typeof sc !== "string") {
        throw new Error(
          `fromComponents: scopesGranted[${idx}] must be a string ` +
            `(got ${typeof sc})`,
        );
      }
      if (!sc) {
        throw new Error(
          `fromComponents: scopesGranted[${idx}] must be non-empty`,
        );
      }
    });
    if (scopesList.length > 0) {
      validateValueLength("scopes_granted(joined)", scopesList.join(" "));
    }

    return {
      epicClientId: this._epicClientId,
      deploymentEnvironment: this._deploymentEnvironment,
      patientFhirId: opts.patientFhirId || undefined,
      encounterFhirId: opts.encounterFhirId || undefined,
      fhirUser: opts.fhirUser || undefined,
      tenant: opts.tenant || undefined,
      intent: opts.intent || undefined,
      scopesGranted: Object.freeze([...scopesList]),
    };
  }

  /** Canonical deployment-environment taxonomy with descriptions. */
  static deploymentEnvironmentTaxonomy(): Array<[string, string]> {
    return [
      ["epic_sandbox", "Epic open-developer sandbox (fhir.epic.com)"],
      ["epic_vendor_test", "Epic Vendor Test environment (pre-production)"],
      ["epic_production", "Epic production environment (post App Orchard approval)"],
      ["epic_local_test", "Customer-side hosted Epic test instance"],
      ["other", "Other deployment environment"],
    ];
  }
}
