import { describe, it, expect } from "vitest";
import {
  X12ServicesReviewAdapter,
  is278Request,
  is278Response,
  is278Approved,
  is278Denied,
  x12_278_toMetadata,
} from "../src/healthcare/index.js";

// Build an ISA segment of exactly 106 chars (X12 standard).
// ISA fields are 16 elements after ISA itself, separated by `*`.
// Element separator is `*`, subelement separator is `:`, terminator is `~`.
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

// 278 REQUEST sample (BHT06=13).
const REQ_278 =
  buildIsa("SUBMITTER", "RECEIVER", "000000001") +
  "~" +
  "GS*HI*SUBMITTER*RECEIVER*20260528*1200*1*X*005010X217~" +
  "ST*278*0001~" +
  "BHT*0007*13*REF-001*20260528*1200~" +
  "NM1*PR*2*Acme Payer*****PI*A12345~" +
  "NM1*1P*1*Smith*John****XX*1234567890~" +
  "NM1*IL*1*Doe*Jane****MI*MEMBER-001~" +
  "UM*HS*AR~" +
  "REF*G1*CERT-99~" +
  "SE*9*0001~" +
  "GE*1*1~" +
  "IEA*1*000000001~";

// 278 RESPONSE sample (BHT06=11).
const RESP_278_APPROVED =
  buildIsa("PAYER", "PROVIDER", "000000002") +
  "~" +
  "GS*HI*PAYER*PROVIDER*20260528*1200*2*X*005010X217~" +
  "ST*278*0002~" +
  "BHT*0007*11*REF-002*20260528*1200~" +
  "NM1*PR*2*Acme Payer*****PI*A12345~" +
  "NM1*1P*1*Smith*John****XX*1234567890~" +
  "HCR*A1*CERT-99~" +
  "SE*7*0002~" +
  "GE*1*2~" +
  "IEA*1*000000002~";

describe("X12ServicesReviewAdapter", () => {
  it("parses a 278 REQUEST + extracts BHT direction + payer fields", () => {
    const adapter = new X12ServicesReviewAdapter();
    const tx = adapter.parseTransaction(REQ_278);
    expect(tx.transactionSet).toBe("278");
    expect(is278Request(tx)).toBe(true);
    expect(tx.payerId).toBe("A12345");
    expect(tx.payerName).toBe("Acme Payer");
    expect(tx.requestingProviderNpi).toBe("1234567890");
    expect(tx.reviewType).toBe("HS");
    expect(tx.certificationRequestCategory).toBe("AR");
    expect(tx.certificationNumber).toBe("CERT-99");
    expect(tx.memberIdHash).toMatch(/^[a-f0-9]{64}$/);
    expect(tx.payloadHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it("parses a 278 RESPONSE + extracts HCR01 certification action", () => {
    const adapter = new X12ServicesReviewAdapter();
    const tx = adapter.parseTransaction(RESP_278_APPROVED);
    expect(is278Response(tx)).toBe(true);
    expect(tx.certificationAction).toBe("A1");
    expect(is278Approved(tx)).toBe(true);
    expect(is278Denied(tx)).toBe(false);
  });

  it("rejects empty payload", () => {
    const adapter = new X12ServicesReviewAdapter();
    expect(() => adapter.parseTransaction("")).toThrowError(/empty payload/);
  });

  it("rejects non-ISA-starting payload", () => {
    const adapter = new X12ServicesReviewAdapter();
    expect(() => adapter.parseTransaction("GS*HI*X*Y~")).toThrowError(
      /does not begin with ISA segment/,
    );
  });

  it("parseRequest rejects RESPONSE transactions", () => {
    const adapter = new X12ServicesReviewAdapter();
    expect(() => adapter.parseRequest(RESP_278_APPROVED)).toThrowError(
      /expected REQUEST/,
    );
  });

  it("parseResponse rejects REQUEST transactions", () => {
    const adapter = new X12ServicesReviewAdapter();
    expect(() => adapter.parseResponse(REQ_278)).toThrowError(
      /expected RESPONSE/,
    );
  });

  it("x12_278_toMetadata emits reserved keys + structural fields", () => {
    const adapter = new X12ServicesReviewAdapter();
    const tx = adapter.parseTransaction(RESP_278_APPROVED);
    const meta = x12_278_toMetadata(tx);
    expect(meta["nanorix:framework"]).toBe("X12-278");
    expect(meta["nanorix:framework_version"]).toBe("x12-005010X217");
    expect(meta.transaction_set).toBe("278");
    expect(meta.direction).toBe("RESPONSE");
    expect(meta.certification_action).toBe("A1");
    expect(X12ServicesReviewAdapter.receiptPatternTag()).toBe("pa");
  });
});
