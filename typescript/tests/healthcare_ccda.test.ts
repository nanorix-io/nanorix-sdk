import { describe, it, expect } from "vitest";
import { CCDADocumentReference, CCDAReader } from "../src/healthcare/index.js";

const SAMPLE_BYTES = new TextEncoder().encode("<ClinicalDocument/>");

const SAMPLE_FIELDS = {
  documentTypeCode: "34133-9",
  documentTypeDisplay: "Continuity of Care Document",
  documentId: "doc-001",
  documentDatetime: "20260528120000",
  patientIdReference: "pat-001",
  authorOrganization: "Acme Hospital",
  custodianOrganization: "Acme Hospital",
};

describe("CCDADocumentReference", () => {
  it("toMetadata emits reserved keys + structural fields", () => {
    const reader = new CCDAReader();
    const doc = reader.readFromFields(SAMPLE_BYTES, SAMPLE_FIELDS);
    const meta = doc.toMetadata();
    expect(meta["nanorix:framework"]).toBe("CCDA");
    expect(meta["nanorix:framework_version"]).toBe("ccda-r2.1");
    expect(meta.document_type_code).toBe("34133-9");
    expect(meta.document_id).toBe("doc-001");
    expect(meta.patient_id_reference).toBe("pat-001");
    expect(meta.document_sha256).toMatch(/^[a-f0-9]{64}$/);
  });

  it("document type predicates work for canonical LOINC codes", () => {
    const reader = new CCDAReader();
    const ccd = reader.readFromFields(SAMPLE_BYTES, SAMPLE_FIELDS);
    expect(ccd.isContinuityOfCare).toBe(true);
    expect(ccd.isDischargeSummary).toBe(false);

    const disch = reader.readFromFields(SAMPLE_BYTES, {
      ...SAMPLE_FIELDS,
      documentTypeCode: "18842-5",
      documentTypeDisplay: "Discharge Summary",
    });
    expect(disch.isDischargeSummary).toBe(true);
    expect(disch.isContinuityOfCare).toBe(false);

    const prog = reader.readFromFields(SAMPLE_BYTES, {
      ...SAMPLE_FIELDS,
      documentTypeCode: "11506-3",
    });
    expect(prog.isProgressNote).toBe(true);
  });

  it("rejects empty document bytes", () => {
    const reader = new CCDAReader();
    expect(() =>
      reader.readFromFields(new Uint8Array(0), SAMPLE_FIELDS),
    ).toThrowError(/empty document bytes/);
  });

  it("strict mode rejects missing documentTypeCode", () => {
    const reader = new CCDAReader(true);
    expect(() =>
      reader.readFromFields(SAMPLE_BYTES, {
        ...SAMPLE_FIELDS,
        documentTypeCode: "",
      }),
    ).toThrowError(/documentTypeCode is required/);
  });

  it("toFhirBundle produces DocumentReference + Binary entries", () => {
    const reader = new CCDAReader();
    const doc = reader.readFromFields(SAMPLE_BYTES, SAMPLE_FIELDS);
    const bundle = doc.toFhirBundle(SAMPLE_BYTES);
    expect(bundle.resourceType).toBe("Bundle");
    expect(bundle.type).toBe("collection");
    const entry = (bundle.entry as Array<{ resource: { resourceType: string } }>)
      .map((e) => e.resource.resourceType);
    expect(entry).toEqual(["DocumentReference", "Binary"]);
  });

  it("readToBundle returns both parsed doc + bundle", () => {
    const reader = new CCDAReader();
    const { doc, bundle } = reader.readToBundle(SAMPLE_BYTES, SAMPLE_FIELDS);
    expect(doc.documentTypeCode).toBe("34133-9");
    expect((bundle.entry as unknown[]).length).toBe(2);
  });

  it("supportedDocumentTypes returns canonical LOINC pairs", () => {
    const types = CCDAReader.supportedDocumentTypes();
    expect(types).toContainEqual(["34133-9", "Continuity of Care Document"]);
    expect(types.length).toBeGreaterThanOrEqual(6);
  });
});
