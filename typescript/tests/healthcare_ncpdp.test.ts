import { describe, it, expect } from "vitest";
import {
  NCPDPAdapter,
  isControlledSubstance,
  ncpdpToMetadata,
} from "../src/healthcare/index.js";

const SAMPLE_NEWRX_XML = `<?xml version="1.0"?>
<Message>
  <Header>
    <PrescriberOrderNumber>RX-001</PrescriberOrderNumber>
    <From>Surescripts</From>
    <To>AcmePharmacy</To>
    <Identifier>1234567890</Identifier>
    <NCPDPID>NCPDP9999</NCPDPID>
    <TimeStamp>20260528120000</TimeStamp>
  </Header>
  <NewRx>
    <DEASchedule>CII</DEASchedule>
  </NewRx>
</Message>`;

const SAMPLE_TELECOM_B1 =
  "HEADER|B1|messagecontrolid=MCI-001|sendingapplication=PharmacyA|receivingapplication=PBM-X|prescriberid=NPI-12345|pharmacyncpdpid=NCPDP9999|transactiondatetime=20260528120000";

describe("NCPDPAdapter", () => {
  it("parses NEWRX XML transaction", () => {
    const adapter = new NCPDPAdapter();
    const parsed = adapter.parseTransaction(SAMPLE_NEWRX_XML);
    expect(parsed.transactionCode).toBe("NEWRX");
    expect(parsed.transactionFamily).toBe("SCRIPT");
    expect(parsed.messageControlId).toBe("RX-001");
    expect(parsed.sendingApplication).toBe("Surescripts");
    expect(parsed.deaSchedule).toBe("CII");
    expect(parsed.payloadHash).toMatch(/^[a-f0-9]{64}$/);
    expect(isControlledSubstance(parsed)).toBe(true);
  });

  it("parses Telecom B1 transaction", () => {
    const adapter = new NCPDPAdapter();
    const parsed = adapter.parseTransaction(SAMPLE_TELECOM_B1);
    expect(parsed.transactionCode).toBe("B1");
    expect(parsed.transactionFamily).toBe("TELECOM");
    expect(parsed.messageControlId).toBe("MCI-001");
    expect(parsed.prescriberId).toBe("NPI-12345");
    expect(isControlledSubstance(parsed)).toBe(false);
  });

  it("rejects empty payload", () => {
    const adapter = new NCPDPAdapter();
    expect(() => adapter.parseTransaction("")).toThrowError(/empty payload/);
  });

  it("parseScript rejects Telecom transactions", () => {
    const adapter = new NCPDPAdapter();
    expect(() => adapter.parseScript(SAMPLE_TELECOM_B1)).toThrowError(
      /expected SCRIPT/,
    );
  });

  it("parseTelecom rejects SCRIPT transactions", () => {
    const adapter = new NCPDPAdapter();
    expect(() => adapter.parseTelecom(SAMPLE_NEWRX_XML)).toThrowError(
      /expected TELECOM/,
    );
  });

  it("ncpdpToMetadata emits reserved keys + family-specific framework version", () => {
    const adapter = new NCPDPAdapter();
    const scriptParsed = adapter.parseTransaction(SAMPLE_NEWRX_XML);
    const scriptMeta = ncpdpToMetadata(scriptParsed);
    expect(scriptMeta["nanorix:framework"]).toBe("NCPDP");
    expect(scriptMeta["nanorix:framework_version"]).toBe(
      "ncpdp-script-2017071",
    );
    expect(scriptMeta.dea_schedule).toBe("CII");

    const telecomParsed = adapter.parseTransaction(SAMPLE_TELECOM_B1);
    const telecomMeta = ncpdpToMetadata(telecomParsed);
    expect(telecomMeta["nanorix:framework_version"]).toBe("ncpdp-telecom-D.0");
    expect(telecomMeta.transaction_code).toBe("B1");
  });

  it("payload hash is deterministic + receiptPatternTag static", () => {
    const adapter = new NCPDPAdapter();
    const p1 = adapter.parseTransaction(SAMPLE_NEWRX_XML);
    const p2 = adapter.parseTransaction(SAMPLE_NEWRX_XML);
    expect(p1.payloadHash).toBe(p2.payloadHash);
    expect(NCPDPAdapter.receiptPatternTag()).toBe("ncpdp_script");
  });
});
