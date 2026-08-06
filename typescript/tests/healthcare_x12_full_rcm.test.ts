import { describe, it, expect } from "vitest";
import {
  X12FullRCMAdapter,
  isClaim,
  isRemittance,
  isEnrollment,
  hasDeniedClaims,
  x12RcmToMetadata,
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

// 837P (Professional) sample.
const CLAIM_837P =
  buildIsa("PROVIDER", "PAYER", "000000006") +
  "~" +
  "GS*HC*PROVIDER*PAYER*20260528*1200*6*X*005010X222A1~" +
  "ST*837*0006~" +
  "BHT*0019*00*CLAIM-001*20260528*1200*CH~" +
  "NM1*85*2*Provider*****XX*9876543210~" +
  "NM1*PR*2*Acme Payer*****PI*PAYER-001~" +
  "NM1*IL*1*Doe*Jane****MI*MEMBER-001~" +
  "CLM*CLM-001*1500.50~" +
  "SE*8*0006~" +
  "GE*1*6~" +
  "IEA*1*000000006~";

// 835 (Remittance) sample with denied claim.
const REMIT_835 =
  buildIsa("PAYER", "PROVIDER", "000000007") +
  "~" +
  "GS*HP*PAYER*PROVIDER*20260528*1200*7*X*005010X221A1~" +
  "ST*835*0007~" +
  "BPR*I*0*C*ACH~" +
  "N1*PR*Acme Payer*XV*PAYER-001~" +
  "N1*PE*Provider*XX*9876543210~" +
  "CLP*CLAIM-001*4*1500.50*0.00~" +
  "SE*7*0007~" +
  "GE*1*7~" +
  "IEA*1*000000007~";

// 834 (Enrollment) sample.
const ENROLL_834 =
  buildIsa("EMPLOYER", "PAYER", "000000008") +
  "~" +
  "GS*BE*EMPLOYER*PAYER*20260528*1200*8*X*005010X220A1~" +
  "ST*834*0008~" +
  "BGN*00*ENROLL-001*20260528*1200~" +
  "N1*P5*Employer*FI*98-7654321~" +
  "INS*Y*18*021*20260528*A***FT~" +
  "NM1*IL*1*Doe*Jane****34*123-45-6789~" +
  "SE*7*0008~" +
  "GE*1*8~" +
  "IEA*1*000000008~";

describe("X12FullRCMAdapter", () => {
  it("parses an 837P claim with charge total + provider NPI", () => {
    const adapter = new X12FullRCMAdapter();
    const tx = adapter.parseTransaction(CLAIM_837P);
    expect(tx.transactionSet).toBe("837");
    expect(tx.flavor).toBe("P");
    expect(tx.frameworkVersion).toBe("x12-005010X222A1");
    expect(tx.providerNpi).toBe("9876543210");
    expect(tx.payerId).toBe("PAYER-001");
    expect(tx.claimCount).toBe(1);
    expect(tx.totalChargeAmountCents).toBe(150050);
    expect(isClaim(tx)).toBe(true);
    expect(tx.memberIdHash).toMatch(/^[a-f0-9]{64}$/);
  });

  it("parses an 835 remittance + flags denied claims", () => {
    const adapter = new X12FullRCMAdapter();
    const tx = adapter.parseTransaction(REMIT_835);
    expect(tx.transactionSet).toBe("835");
    expect(isRemittance(tx)).toBe(true);
    expect(tx.claimCount).toBe(1);
    expect(tx.claimStatusDistribution).toContain("4");
    expect(hasDeniedClaims(tx)).toBe(true);
  });

  it("parses an 834 enrollment with maintenance code", () => {
    const adapter = new X12FullRCMAdapter();
    const tx = adapter.parseTransaction(ENROLL_834);
    expect(tx.transactionSet).toBe("834");
    expect(isEnrollment(tx)).toBe(true);
    expect(tx.claimCount).toBe(1);
    expect(tx.maintenanceTypeDistribution).toContain("021");
  });

  it("rejects empty payload + non-ISA-starting payload", () => {
    const adapter = new X12FullRCMAdapter();
    expect(() => adapter.parseTransaction("")).toThrowError(/empty payload/);
    expect(() => adapter.parseTransaction("GS*HC~")).toThrowError(
      /does not begin with ISA segment/,
    );
  });

  it("parse834/835/837 reject mismatched transaction sets", () => {
    const adapter = new X12FullRCMAdapter();
    expect(() => adapter.parse834(CLAIM_837P)).toThrowError(/expected 834/);
    expect(() => adapter.parse835(CLAIM_837P)).toThrowError(/expected 835/);
    expect(() => adapter.parse837(REMIT_835)).toThrowError(/expected 837/);
  });

  it("x12RcmToMetadata emits reserved keys + flavor-specific framework label", () => {
    const adapter = new X12FullRCMAdapter();
    const tx = adapter.parseTransaction(CLAIM_837P);
    const meta = x12RcmToMetadata(tx);
    expect(meta["nanorix:framework"]).toBe("X12-837P");
    expect(meta["nanorix:framework_version"]).toBe("x12-005010X222A1");
    expect(meta.transaction_set).toBe("837");
    expect(meta.flavor).toBe("P");
    expect(meta.claim_count).toBe("1");
    expect(meta.total_charge_amount_cents).toBe("150050");
  });

  it("receiptPatternTag maps 837/835/834 correctly", () => {
    expect(X12FullRCMAdapter.receiptPatternTag("837")).toBe("rcm_claim");
    expect(X12FullRCMAdapter.receiptPatternTag("835")).toBe("rcm_remit");
    expect(X12FullRCMAdapter.receiptPatternTag("834")).toBe("rcm_claim");
    expect(() =>
      X12FullRCMAdapter.receiptPatternTag("999"),
    ).toThrowError(/unsupported transaction_set/);
  });
});
