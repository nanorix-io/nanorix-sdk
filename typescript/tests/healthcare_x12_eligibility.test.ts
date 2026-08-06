import { describe, it, expect } from "vitest";
import {
  X12EligibilityAdapter,
  is270Inquiry,
  is271Response,
  x12EligibilityToMetadata,
} from "../src/healthcare/index.js";

function buildIsa(submitter: string, receiver: string, ctrl: string): string {
  const pad = (s: string, n: number) => s.padEnd(n).slice(0, n);
  const elem = [
    "ISA",
    "00",
    pad("", 10),
    "00",
    pad("", 10),
    "ZZ",
    pad(submitter, 15),
    "ZZ",
    pad(receiver, 15),
    "260528",
    "1200",
    "U",
    "00501",
    pad(ctrl, 9),
    "0",
    "P",
    ":",
  ];
  return elem.join("*");
}

const INQ_270 =
  buildIsa("PROVIDER", "PAYER", "000000003") +
  "~" +
  "GS*HS*PROVIDER*PAYER*20260528*1200*3*X*005010X279A1~" +
  "ST*270*0003~" +
  "BHT*0022*13*ELIG-001*20260528*1200~" +
  "NM1*PR*2*Acme Payer*****PI*PAYER-001~" +
  "NM1*IL*1*Doe*Jane****MI*MEMBER-001~" +
  "EQ*30~" +
  "SE*7*0003~" +
  "GE*1*3~" +
  "IEA*1*000000003~";

const RESP_271 =
  buildIsa("PAYER", "PROVIDER", "000000004") +
  "~" +
  "GS*HB*PAYER*PROVIDER*20260528*1200*4*X*005010X279A1~" +
  "ST*271*0004~" +
  "BHT*0022*11*ELIG-002*20260528*1200~" +
  "NM1*PR*2*Acme Payer*****PI*PAYER-001~" +
  "NM1*IL*1*Doe*Jane****MI*MEMBER-001~" +
  "EB*1**30**HEALTH PLAN COVERAGE~" +
  "SE*7*0004~" +
  "GE*1*4~" +
  "IEA*1*000000004~";

describe("X12EligibilityAdapter", () => {
  it("parses a 270 inquiry + extracts payer + member hash + service type", () => {
    const adapter = new X12EligibilityAdapter();
    const tx = adapter.parseTransaction(INQ_270);
    expect(tx.transactionSet).toBe("270");
    expect(is270Inquiry(tx)).toBe(true);
    expect(is271Response(tx)).toBe(false);
    expect(tx.payerName).toBe("Acme Payer");
    expect(tx.payerId).toBe("PAYER-001");
    expect(tx.memberIdHash).toMatch(/^[a-f0-9]{64}$/);
    expect(tx.serviceTypeCodes).toContain("30");
  });

  it("parses a 271 response", () => {
    const adapter = new X12EligibilityAdapter();
    const tx = adapter.parseTransaction(RESP_271);
    expect(tx.transactionSet).toBe("271");
    expect(is271Response(tx)).toBe(true);
  });

  it("rejects empty payload", () => {
    const adapter = new X12EligibilityAdapter();
    expect(() => adapter.parseTransaction("")).toThrowError(/empty payload/);
  });

  it("rejects non-ISA-starting payload", () => {
    const adapter = new X12EligibilityAdapter();
    expect(() => adapter.parseTransaction("GS*HS*X*Y~")).toThrowError(
      /does not begin with ISA segment/,
    );
  });

  it("rejects non-270/271 transaction sets", () => {
    const adapter = new X12EligibilityAdapter();
    const malformed =
      buildIsa("SUBMITTER", "RECEIVER", "000000005") +
      "~" +
      "GS*HI*SUBMITTER*RECEIVER*20260528*1200*5*X*005010X217~" +
      "ST*278*0005~" +
      "IEA*1*000000005~";
    expect(() => adapter.parseTransaction(malformed)).toThrowError(
      /expected 270 or 271/,
    );
  });

  it("parse270 rejects 271 responses", () => {
    const adapter = new X12EligibilityAdapter();
    expect(() => adapter.parse270(RESP_271)).toThrowError(/expected 270/);
  });

  it("x12EligibilityToMetadata emits reserved keys + receiptPatternTag", () => {
    const adapter = new X12EligibilityAdapter();
    const tx = adapter.parseTransaction(INQ_270);
    const meta = x12EligibilityToMetadata(tx);
    expect(meta["nanorix:framework"]).toBe("X12-270-271");
    expect(meta["nanorix:framework_version"]).toBe("x12-005010X279A1");
    expect(meta.transaction_set).toBe("270");
    expect(meta.payer_name).toBe("Acme Payer");
    expect(meta.member_id_hash).toMatch(/^[a-f0-9]{64}$/);
    expect(meta.service_type_codes).toBe("30");
    expect(X12EligibilityAdapter.receiptPatternTag()).toBe("rcm_eligibility");
  });
});
