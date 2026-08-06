import { describe, it, expect } from "vitest";
import {
  IheAdapter,
  IheTransactionType,
  IheBindingError,
  isIheSubmission,
  isIheRetrieve,
  iheTransactionToMetadata,
} from "../src/healthcare/index.js";

const SAMPLE_ENVELOPE = new TextEncoder().encode(
  "<soap:Envelope><soap:Body><ihe:Submission/></soap:Body></soap:Envelope>",
);

describe("IheAdapter", () => {
  it("parses an XDS-I.b RAD-68 submission with all structural fields", () => {
    const adapter = new IheAdapter();
    const tx = adapter.parseSubmission({
      profile: "XDS-I.b",
      transactionId: "RAD-68",
      soapEnvelopeBytes: SAMPLE_ENVELOPE,
      repositoryUri: "https://repo.example.org/ihe",
      submissionSetUid: "1.2.840.113619.2.1.1.999.1",
      documentUids: ["1.2.840.113619.2.1.1.999.1.1"],
      studyInstanceUids: ["1.2.840.113619.2.1.1.123"],
      homeCommunityId: "1.3.6.1.4.1.21367.13.20.1000",
    });
    expect(tx.transactionId).toBe("RAD-68");
    expect(tx.profile).toBe("XDS-I.b");
    expect(tx.payloadSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(tx.instanceCount).toBe(1);
    expect(isIheSubmission(tx)).toBe(true);
  });

  it("rejects transaction_id not defined under the declared profile", () => {
    const adapter = new IheAdapter();
    expect(() =>
      adapter.parseSubmission({
        profile: "XDR",
        transactionId: "RAD-68",
        soapEnvelopeBytes: SAMPLE_ENVELOPE,
      }),
    ).toThrowError(IheBindingError);
  });

  it("rejects empty soapEnvelopeBytes", () => {
    const adapter = new IheAdapter();
    expect(() =>
      adapter.parseSubmission({
        profile: "XDS-I.b",
        transactionId: "ITI-41",
        soapEnvelopeBytes: new Uint8Array(0),
      }),
    ).toThrowError(/must be non-empty/);
  });

  it("strict mode requires submission_set_uid + document_uids for ITI-41", () => {
    const adapter = new IheAdapter(true);
    expect(() =>
      adapter.parseSubmission({
        profile: "XDS-I.b",
        transactionId: "ITI-41",
        soapEnvelopeBytes: SAMPLE_ENVELOPE,
        repositoryUri: "https://x.example.org",
      }),
    ).toThrowError(/submission_set_uid required in strict mode/);
  });

  it("IheTransactionType.fromIdentifier validates closed enum", () => {
    expect(() =>
      IheTransactionType.fromIdentifier("RAD-999"),
    ).toThrowError(/unsupported transaction_id/);
    const t = IheTransactionType.fromIdentifier("ITI-41");
    expect(t.appliesToProfile("XDS-I.b")).toBe(true);
    expect(t.appliesToProfile("XDR")).toBe(true);
  });

  it("iheTransactionToMetadata emits reserved keys + payload_sha256", () => {
    const adapter = new IheAdapter();
    const tx = adapter.parseQueryResponse({
      profile: "XDR",
      transactionId: "ITI-38",
      soapEnvelopeBytes: SAMPLE_ENVELOPE,
      repositoryUri: "https://xca.example.org",
      homeCommunityId: "1.3.6.1.4.1.21367.13.20.1000",
    });
    const meta = iheTransactionToMetadata(tx);
    expect(meta["nanorix:framework"]).toBe("IHE");
    expect(meta["nanorix:framework_version"]).toBe("ihe-iti-rad-2024");
    expect(meta.transaction_id).toBe("ITI-38");
    expect(meta.profile).toBe("XDR");
    expect(meta.payload_sha256).toMatch(/^[a-f0-9]{64}$/);
    expect(isIheRetrieve(tx)).toBe(false);
  });

  it("envelope hash is deterministic for the same bytes", () => {
    const adapter = new IheAdapter();
    const tx1 = adapter.parseSubmission({
      profile: "XDS-I.b",
      transactionId: "RAD-68",
      soapEnvelopeBytes: SAMPLE_ENVELOPE,
      repositoryUri: "https://x.example.org",
    });
    const tx2 = adapter.parseSubmission({
      profile: "XDS-I.b",
      transactionId: "RAD-68",
      soapEnvelopeBytes: SAMPLE_ENVELOPE,
      repositoryUri: "https://x.example.org",
    });
    expect(tx1.payloadSha256).toBe(tx2.payloadSha256);
    expect(IheAdapter.isValidUid("1.2.3.4")).toBe(true);
    expect(IheAdapter.isValidUid("1.A.3")).toBe(false);
  });
});
