import { describe, it, expect } from "vitest";
import {
  DICOMSRBinding,
  SR_TEMPLATE_MEASUREMENT_REPORT,
  SR_TEMPLATE_AI_ANALYSIS_REPORT,
  isValidDicomUid,
} from "../src/healthcare/index.js";

const SAMPLE_DOC = new TextEncoder().encode("FAKE DICOM SR BYTES");
const SAMPLE_UID = "1.2.840.10008.5.1.4.1.1.88.11.999.1";

describe("DICOMSRBinding", () => {
  it("HUMAN-creator binding emits structural metadata", () => {
    const b = DICOMSRBinding.bind({
      sopInstanceUid: SAMPLE_UID,
      documentBytes: SAMPLE_DOC,
      creator: "HUMAN",
      contentTemplateId: SR_TEMPLATE_MEASUREMENT_REPORT,
    });
    const meta = b.toMetadata();
    expect(meta["nanorix:framework"]).toBe("DICOM_SR");
    expect(meta["nanorix:framework_version"]).toBe("dicom-sr-ps3.3-2024");
    expect(meta.sop_instance_uid).toBe(SAMPLE_UID);
    expect(meta.creator).toBe("HUMAN");
    expect(meta.content_template_id).toBe("TID 1500");
    expect(meta.document_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(meta.document_size_bytes).toBe(String(SAMPLE_DOC.length));
  });

  it("AI-creator without algorithmIdentification is rejected", () => {
    expect(() =>
      DICOMSRBinding.bind({
        sopInstanceUid: SAMPLE_UID,
        documentBytes: SAMPLE_DOC,
        creator: "AI",
        contentTemplateId: SR_TEMPLATE_AI_ANALYSIS_REPORT,
      }),
    ).toThrowError(/algorithmIdentification required when creator='AI'/);
  });

  it("AI-creator with algorithmIdentification emits it", () => {
    const b = DICOMSRBinding.bind({
      sopInstanceUid: SAMPLE_UID,
      documentBytes: SAMPLE_DOC,
      creator: "AI",
      contentTemplateId: SR_TEMPLATE_AI_ANALYSIS_REPORT,
      algorithmIdentification: "VoiceBio@1.2.3",
    });
    expect(b.toMetadata().algorithm_identification).toBe("VoiceBio@1.2.3");
  });

  it("rejects malformed SOP Instance UID", () => {
    expect(() =>
      DICOMSRBinding.bind({
        sopInstanceUid: "not.a.uid!",
        documentBytes: SAMPLE_DOC,
        creator: "HUMAN",
        contentTemplateId: SR_TEMPLATE_MEASUREMENT_REPORT,
      }),
    ).toThrowError(/does not match DICOM/);
  });

  it("rejects empty document bytes", () => {
    expect(() =>
      DICOMSRBinding.bind({
        sopInstanceUid: SAMPLE_UID,
        documentBytes: new Uint8Array(0),
        creator: "HUMAN",
        contentTemplateId: SR_TEMPLATE_MEASUREMENT_REPORT,
      }),
    ).toThrowError(/non-empty/);
  });

  it("rejects malformed content template ID", () => {
    expect(() =>
      DICOMSRBinding.bind({
        sopInstanceUid: SAMPLE_UID,
        documentBytes: SAMPLE_DOC,
        creator: "HUMAN",
        contentTemplateId: "Template 1500",
      }),
    ).toThrowError(/must match 'TID NNNN' pattern/);
  });

  it("document hash is deterministic for the same bytes", () => {
    const b1 = DICOMSRBinding.bind({
      sopInstanceUid: SAMPLE_UID,
      documentBytes: SAMPLE_DOC,
      creator: "HUMAN",
      contentTemplateId: SR_TEMPLATE_MEASUREMENT_REPORT,
    });
    const b2 = DICOMSRBinding.bind({
      sopInstanceUid: SAMPLE_UID,
      documentBytes: SAMPLE_DOC,
      creator: "HUMAN",
      contentTemplateId: SR_TEMPLATE_MEASUREMENT_REPORT,
    });
    expect(b1.documentSha256).toBe(b2.documentSha256);
    expect(isValidDicomUid(SAMPLE_UID)).toBe(true);
    expect(isValidDicomUid("not.uid!")).toBe(false);
  });
});
