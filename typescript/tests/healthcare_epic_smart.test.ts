import { describe, it, expect } from "vitest";
import {
  EpicLaunchContext,
  hasPatientContext,
  hasEncounterContext,
  hasUserContext,
  isProduction,
  epicLaunchContextToMetadata,
} from "../src/healthcare/index.js";

describe("EpicLaunchContext", () => {
  it("fromTokenResponse extracts launch-context fields", () => {
    const binder = new EpicLaunchContext({
      epicClientId: "acme-app-prod",
      deploymentEnvironment: "epic_production",
    });
    const result = binder.fromTokenResponse({
      patient: "p1",
      encounter: "e1",
      fhirUser: "Practitioner/u1",
      scope: "patient/Patient.read patient/Observation.read launch",
    });
    expect(result.epicClientId).toBe("acme-app-prod");
    expect(result.deploymentEnvironment).toBe("epic_production");
    expect(result.patientFhirId).toBe("p1");
    expect(result.encounterFhirId).toBe("e1");
    expect(result.fhirUser).toBe("Practitioner/u1");
    expect(result.scopesGranted).toEqual([
      "patient/Patient.read",
      "patient/Observation.read",
      "launch",
    ]);
    expect(hasPatientContext(result)).toBe(true);
    expect(hasEncounterContext(result)).toBe(true);
    expect(hasUserContext(result)).toBe(true);
    expect(isProduction(result)).toBe(true);
  });

  it("rejects unknown deploymentEnvironment", () => {
    expect(
      () =>
        new EpicLaunchContext({
          epicClientId: "x",
          // @ts-expect-error — intentional bad enum
          deploymentEnvironment: "epic_super_secret",
        }),
    ).toThrowError(/deploymentEnvironment=/);
  });

  it("rejects missing epicClientId", () => {
    expect(
      () =>
        new EpicLaunchContext({
          epicClientId: "",
          deploymentEnvironment: "epic_sandbox",
        }),
    ).toThrowError(/epicClientId is required/);
  });

  it("fromComponents builds result without OAuth response", () => {
    const binder = new EpicLaunchContext({
      epicClientId: "acme-app-test",
      deploymentEnvironment: "epic_vendor_test",
    });
    const r = binder.fromComponents({
      patientFhirId: "p1",
      scopesGranted: ["launch"],
    });
    expect(r.patientFhirId).toBe("p1");
    expect(hasPatientContext(r)).toBe(true);
    expect(hasEncounterContext(r)).toBe(false);
    expect(isProduction(r)).toBe(false);
  });

  it("toMetadata emits reserved keys + scopes_granted as space-joined", () => {
    const binder = new EpicLaunchContext({
      epicClientId: "acme-app-prod",
      deploymentEnvironment: "epic_production",
    });
    const r = binder.fromComponents({
      patientFhirId: "p1",
      scopesGranted: ["patient/Patient.read", "launch"],
    });
    const meta = epicLaunchContextToMetadata(r);
    expect(meta["nanorix:framework"]).toBe("EpicSMART");
    expect(meta["nanorix:framework_version"]).toBe("smart-app-launch-v2");
    expect(meta.epic_client_id).toBe("acme-app-prod");
    expect(meta.deployment_environment).toBe("epic_production");
    expect(meta.patient_fhir_id).toBe("p1");
    expect(meta.scopes_granted).toBe("patient/Patient.read launch");
  });

  it("rejects non-string token-response launch context values", () => {
    const binder = new EpicLaunchContext({
      epicClientId: "x",
      deploymentEnvironment: "epic_sandbox",
    });
    expect(() => binder.fromTokenResponse({ patient: 123 as unknown as string })).toThrowError(
      /must be a string/,
    );
  });

  it("deploymentEnvironmentTaxonomy returns canonical pairs + 5 entries", () => {
    const taxonomy = EpicLaunchContext.deploymentEnvironmentTaxonomy();
    expect(taxonomy).toHaveLength(5);
    expect(taxonomy[0][0]).toBe("epic_sandbox");
  });
});
