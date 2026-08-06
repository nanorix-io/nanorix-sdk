import { describe, it, expect } from "vitest";
import {
  BulkFhirAdapter,
  kickoffToMetadata,
  statusToMetadata,
} from "../src/healthcare/index.js";

describe("BulkFhirAdapter", () => {
  it("parses a patient-level kickoff URL with _type filter", () => {
    const adapter = new BulkFhirAdapter();
    const k = adapter.parseKickoff(
      "https://fhir.example.com/r4/Patient/$export?_type=Patient,Observation",
    );
    expect(k.endpointType).toBe("patient");
    expect(k.baseUrl).toBe("https://fhir.example.com/r4");
    expect(k.typesRequested).toBe("Patient,Observation");
    expect(k.outputFormat).toBe("application/fhir+ndjson");
    expect(k.requestHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it("rejects a URL missing $export", () => {
    const adapter = new BulkFhirAdapter();
    expect(() =>
      adapter.parseKickoff("https://fhir.example.com/r4/Patient"),
    ).toThrowError(/does not contain '\$export'/);
  });

  it("parses a group-level kickoff and extracts group id", () => {
    const adapter = new BulkFhirAdapter();
    const k = adapter.parseKickoff(
      "https://fhir.example.com/r4/Group/grp123/$export",
    );
    expect(k.endpointType).toBe("group");
    expect(k.groupId).toBe("grp123");
    expect(k.baseUrl).toBe("https://fhir.example.com/r4");
  });

  it("kickoffToMetadata produces reserved keys + structural fields", () => {
    const adapter = new BulkFhirAdapter();
    const k = adapter.parseKickoff(
      "https://fhir.example.com/r4/$export?_since=2026-01-01",
    );
    const meta = kickoffToMetadata(k);
    expect(meta["nanorix:framework"]).toBe("FHIR-Bulk");
    expect(meta["nanorix:framework_version"]).toBe("fhir-bulk-data-2.0.0");
    expect(meta.endpoint_type).toBe("system");
    expect(meta.kind).toBe("kickoff");
    expect(meta.since).toBe("2026-01-01");
  });

  it("status response 'completed' detection + statusToMetadata", () => {
    const adapter = new BulkFhirAdapter();
    const body = JSON.stringify({
      transactionTime: "2026-01-02T00:00:00Z",
      request: "https://fhir.example.com/r4/$export",
      output: [
        { type: "Patient", url: "https://x/0.ndjson", count: 1 },
        { type: "Observation", url: "https://x/1.ndjson", count: 1 },
      ],
      error: [],
      deleted: [],
    });
    const s = adapter.parseStatus(body);
    expect(s.state).toBe("completed");
    expect(s.outputCount).toBe(2);
    const meta = statusToMetadata(s);
    expect(meta["nanorix:framework"]).toBe("FHIR-Bulk");
    expect(meta.kind).toBe("status");
    expect(meta.state).toBe("completed");
  });

  it("parses NDJSON stream + hashes patient ids structurally", () => {
    const adapter = new BulkFhirAdapter();
    const ndjson = [
      JSON.stringify({
        resourceType: "Patient",
        id: "p1",
        meta: { lastUpdated: "2026-01-01T00:00:00Z" },
      }),
      JSON.stringify({
        resourceType: "Observation",
        id: "o1",
        subject: { reference: "Patient/p1" },
      }),
    ].join("\n");
    const resources = Array.from(adapter.parseNdjsonStream(ndjson));
    expect(resources).toHaveLength(2);
    expect(resources[0].resourceType).toBe("Patient");
    expect(resources[0].patientIdHash).toMatch(/^[a-f0-9]{64}$/);
    expect(resources[1].resourceType).toBe("Observation");
    expect(resources[1].patientIdHash).toBe(resources[0].patientIdHash);
  });

  it("resource_hash is deterministic for the same input", () => {
    const adapter = new BulkFhirAdapter();
    const json = JSON.stringify({ resourceType: "Patient", id: "x" });
    const r1 = Array.from(adapter.parseNdjsonStream(json));
    const r2 = Array.from(adapter.parseNdjsonStream(json));
    expect(r1[0].resourceHash).toBe(r2[0].resourceHash);
    expect(BulkFhirAdapter.receiptPatternTag()).toBe("fhir_record");
  });
});
