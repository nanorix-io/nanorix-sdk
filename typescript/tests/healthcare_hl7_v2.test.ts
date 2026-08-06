import { describe, it, expect } from "vitest";
import {
  HL7v2Adapter,
  messageTypeWithTrigger,
  parsedMessageToMetadata,
} from "../src/healthcare/index.js";

const ADT_A01_SAMPLE =
  "MSH|^~\\&|EpicCare|AcmeHospital|RouteEngine|AcmeWorkflow|20260528120000||ADT^A01|MSG00001|P|2.5\r" +
  "EVN|A01|20260528120000\r" +
  "PID|1||MRN12345||DOE^JOHN||19800101|M\r" +
  "PV1|1|I|ICU^101^1";

const ORU_R01_SAMPLE =
  "MSH|^~\\&|LabApp|AcmeLab|RouteEngine|AcmeWorkflow|20260528120000||ORU^R01|MSG00002|P|2.5\r" +
  "PID|1||MRN12345||DOE^JOHN\r" +
  "OBR|1|||CBC^Complete Blood Count\r" +
  "OBX|1|NM|HGB^Hemoglobin||14.5|g/dL";

describe("HL7v2Adapter", () => {
  it("parses ADT^A01 with MSH fields + structural hash", () => {
    const adapter = new HL7v2Adapter();
    const parsed = adapter.parseMessage(ADT_A01_SAMPLE);
    expect(parsed.messageType).toBe("ADT");
    expect(parsed.triggerEvent).toBe("A01");
    expect(parsed.sendingApplication).toBe("EpicCare");
    expect(parsed.sendingFacility).toBe("AcmeHospital");
    expect(parsed.messageControlId).toBe("MSG00001");
    expect(parsed.processingId).toBe("P");
    expect(parsed.versionId).toBe("2.5");
    expect(parsed.mshSegmentHash).toMatch(/^[a-f0-9]{64}$/);
    expect(messageTypeWithTrigger(parsed)).toBe("ADT^A01");
  });

  it("parses ORU^R01 + observes segment types", () => {
    const adapter = new HL7v2Adapter();
    const parsed = adapter.parseMessage(ORU_R01_SAMPLE);
    expect(parsed.messageType).toBe("ORU");
    expect(parsed.triggerEvent).toBe("R01");
    expect(parsed.segmentTypesObserved).toContain("MSH");
    expect(parsed.segmentTypesObserved).toContain("OBX");
  });

  it("rejects non-MSH first segment", () => {
    const adapter = new HL7v2Adapter();
    expect(() =>
      adapter.parseMessage(
        "EVN|A01|20260528120000\rPID|1||MRN12345",
      ),
    ).toThrowError(/HL7 V2 messages must start with MSH/);
  });

  it("rejects unsupported message type", () => {
    const adapter = new HL7v2Adapter();
    const sample =
      "MSH|^~\\&|App|Fac|App|Fac|20260528120000||SIU^S12|MSG00099|P|2.5";
    expect(() => adapter.parseMessage(sample)).toThrowError(
      /supported families are ADT \+ ORU/,
    );
  });

  it("parseAdt rejects ORU messages", () => {
    const adapter = new HL7v2Adapter();
    expect(() => adapter.parseAdt(ORU_R01_SAMPLE)).toThrowError(
      /expected ADT/,
    );
  });

  it("parsedMessageToMetadata emits reserved keys + structural fields", () => {
    const adapter = new HL7v2Adapter();
    const parsed = adapter.parseMessage(ADT_A01_SAMPLE);
    const meta = parsedMessageToMetadata(parsed);
    expect(meta["nanorix:framework"]).toBe("HL7V2");
    expect(meta["nanorix:framework_version"]).toBe("hl7-v2.5");
    expect(meta.message_type).toBe("ADT");
    expect(meta.trigger_event).toBe("A01");
    expect(meta.sending_application).toBe("EpicCare");
  });

  it("deriveMetadata adds optional customer_workflow_id", () => {
    const adapter = new HL7v2Adapter();
    const parsed = adapter.parseMessage(ADT_A01_SAMPLE);
    const meta = HL7v2Adapter.deriveMetadata(parsed, {
      customerWorkflowId: "job-001",
    });
    expect(meta.customer_workflow_id).toBe("job-001");
  });
});
