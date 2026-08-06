import { describe, it, expect } from "vitest";
import {
  DICOMwebAdapter,
  isRetrieve,
  isStore,
  isQuery,
  dicomwebTransactionToMetadata,
} from "../src/healthcare/index.js";

const SAMPLE_STUDY_UID = "1.2.840.113619.2.1.1.123";

describe("DICOMwebAdapter", () => {
  it("parses a WADO-RS multipart/related response", () => {
    const adapter = new DICOMwebAdapter();
    const body = new TextEncoder().encode(
      "--boundary\r\nContent-Type: application/dicom\r\n\r\nbytes\r\n--boundary--",
    );
    const tx = adapter.parseResponse({
      verbFamily: "WADO-RS",
      httpStatus: 200,
      contentType: "multipart/related; type=\"application/dicom\"; boundary=boundary",
      responseBody: body,
      studyInstanceUid: SAMPLE_STUDY_UID,
    });
    expect(tx.verbFamily).toBe("WADO-RS");
    expect(tx.httpStatus).toBe(200);
    expect(tx.studyInstanceUid).toBe(SAMPLE_STUDY_UID);
    expect(tx.responseBodySha256).toMatch(/^[a-f0-9]{64}$/);
    expect(isRetrieve(tx)).toBe(true);
    expect(isStore(tx)).toBe(false);
    expect(isQuery(tx)).toBe(false);
  });

  it("parses a QIDO-RS DICOM-JSON response with instance count", () => {
    const adapter = new DICOMwebAdapter();
    const body = new TextEncoder().encode(
      JSON.stringify([{ a: 1 }, { b: 2 }, { c: 3 }]),
    );
    const tx = adapter.parseResponse({
      verbFamily: "QIDO-RS",
      httpStatus: 200,
      contentType: "application/dicom+json",
      responseBody: body,
    });
    expect(tx.instanceCount).toBe(3);
    expect(isQuery(tx)).toBe(true);
  });

  it("rejects unsupported verbFamily", () => {
    const adapter = new DICOMwebAdapter();
    expect(() =>
      adapter.parseResponse({
        // @ts-expect-error — intentional bad enum
        verbFamily: "DIMSE-RS",
        httpStatus: 200,
        contentType: "application/dicom",
        responseBody: new Uint8Array(),
      }),
    ).toThrowError(/unsupported verbFamily/);
  });

  it("rejects malformed httpStatus", () => {
    const adapter = new DICOMwebAdapter();
    expect(() =>
      adapter.parseResponse({
        verbFamily: "WADO-RS",
        httpStatus: 999,
        contentType: "application/dicom",
        responseBody: new Uint8Array(),
      }),
    ).toThrowError(/must be int in 1\.\.599/);
  });

  it("strict mode rejects malformed UIDs", () => {
    const adapter = new DICOMwebAdapter(true);
    expect(() =>
      adapter.parseResponse({
        verbFamily: "WADO-RS",
        httpStatus: 200,
        contentType: "application/dicom",
        responseBody: new Uint8Array(0),
        studyInstanceUid: "not.a.uid!",
      }),
    ).toThrowError(/does not match DICOM UID/);
  });

  it("transaction toMetadata emits reserved keys + UIDs", () => {
    const adapter = new DICOMwebAdapter();
    const tx = adapter.parseResponse({
      verbFamily: "STOW-RS",
      httpStatus: 202,
      contentType: "application/dicom",
      responseBody: new TextEncoder().encode("STOW response"),
      studyInstanceUid: SAMPLE_STUDY_UID,
      modality: "CT",
    });
    const meta = dicomwebTransactionToMetadata(tx);
    expect(meta["nanorix:framework"]).toBe("DICOMWEB");
    expect(meta["nanorix:framework_version"]).toBe("dicomweb-ps3.18-2024");
    expect(meta.verb_family).toBe("STOW-RS");
    expect(meta.http_status).toBe("202");
    expect(meta.study_instance_uid).toBe(SAMPLE_STUDY_UID);
    expect(meta.modality).toBe("CT");
    expect(isStore(tx)).toBe(true);
  });

  it("isValidUid static + supportedVerbFamilies introspection", () => {
    const adapter = new DICOMwebAdapter();
    expect(DICOMwebAdapter.isValidUid("1.2.3.4")).toBe(true);
    expect(DICOMwebAdapter.isValidUid("1.2.3.A")).toBe(false);
    expect(adapter.supportedVerbFamilies).toEqual([
      "QIDO-RS",
      "STOW-RS",
      "WADO-RS",
    ]);
  });
});
