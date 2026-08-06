/**
 * Batch 9 (2026-05-28) — C2PA LLM-output provenance tests (TS port).
 *
 * Mirrors the Python SDK. Validates:
 *
 * - Builder fluent API (withCapsule / withModel / withPromptIntent /
 *   withGenerationTimestamp / withOutputContent / sign)
 * - Required-field enforcement at .sign() time (customer-sole-declarer)
 * - Closed-enum AssertionType (Forever-Standard discipline)
 * - Field length caps + forbidden-token scans (brand discipline +
 *   feedback_narrow_signed_claim_auditor_certifies.md)
 * - SHA-512 of canonical-JCS claims object signed by Ed25519
 * - Independent offline verification with raw 32-byte public key
 * - Tampering detection (signature breaks if any field modified)
 * - Tamper-detection on claims_hash recomputation
 * - Round-trip canonicalization byte-stability
 * - Manifest structure (claim_generator, claim_generator_info, assertions,
 *   claims_hash, signature)
 * - Optional model_weights_hash + custom assertions
 * - Narrow-claim disclaimer carried in verification result
 * - AuditProof byte-form UNCHANGED (validate by reading source)
 */
import { describe, expect, it, beforeEach } from "vitest";
import { webcrypto } from "node:crypto";

import { canonicalizeBytes } from "../src/_jcs.js";
import { Ed25519GeneratedKey, type EventSigner } from "../src/helpers/signer.js";
import {
  C2PA_AssertionType,
  C2PAManifestBuilder,
  C2PAManifest,
  C2PA_ACTION_CREATED,
  C2PA_CLAIM_GENERATOR_PREFIX,
  C2PA_DIGITAL_SOURCE_TYPE_AI,
  C2PA_FORBIDDEN_BRAND_LEAK_TOKENS,
  C2PA_FORBIDDEN_REGULATORY_TOKENS,
  C2PA_MANIFEST_FORMAT,
  C2PA_MAX_AUDITPROOF_ID_LEN,
  C2PA_MAX_CAPSULE_ID_LEN,
  C2PA_MAX_MODEL_ID_LEN,
  C2PA_MAX_PROMPT_INTENT_LEN,
  C2PA_MAX_VERIFICATION_KEY_ID_LEN,
  C2PA_NARROW_CLAIM_DISCLAIMER,
  C2PA_SIGNATURE_ALGORITHM,
  C2PA_VALID_ASSERTION_TYPES,
  verifyC2PAManifest,
} from "../src/integrations/index.js";

// Node 18 ships globalThis.crypto with subtle; for older Node fallback ensures it
// eslint-disable-next-line @typescript-eslint/no-explicit-any
if (!(globalThis as any).crypto) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (globalThis as any).crypto = webcrypto;
}

// ── Fixtures ──

let signer: EventSigner;
let signerPublicKey: Uint8Array;

beforeEach(async () => {
  signer = await Ed25519GeneratedKey.generate();
  signerPublicKey = await signer.publicKeyBytes();
});

async function buildBasic(): Promise<C2PAManifest> {
  return new C2PAManifestBuilder()
    .withCapsule({
      capsuleId: "cap_abc123",
      auditproofId: "cdp_def456",
      verificationKeyId: "nrx-verify-2026-05-28T12-34-50-cap_abc1",
    })
    .withModel({ modelId: "openai/gpt-4o-2024-08-06" })
    .withPromptIntent("clinical_pa_decision_narrative_v3")
    .withGenerationTimestamp("2026-05-28T12:34:56Z")
    .withOutputContent(new TextEncoder().encode("This is the generated narrative."))
    .sign(signer);
}

// ── Group 1: Builder happy path ──

describe("Builder happy path", () => {
  it("returns a C2PAManifest", async () => {
    const m = await buildBasic();
    expect(m).toBeInstanceOf(C2PAManifest);
  });

  it("has required top-level wire fields", async () => {
    const m = await buildBasic();
    const d = m.toDict();
    expect(d.format).toBe(C2PA_MANIFEST_FORMAT);
    expect(d.claim_generator.startsWith("Nanorix/1.0")).toBe(true);
    expect(Array.isArray(d.claim_generator_info)).toBe(true);
    expect(d.claims_hash.startsWith("sha512:")).toBe(true);
    expect(d.signature.algorithm).toBe(C2PA_SIGNATURE_ALGORITHM);
    expect(d.signature.public_key.startsWith("base64:")).toBe(true);
    expect(d.signature.signature.startsWith("base64:")).toBe(true);
  });

  it("includes c2pa.actions + nanorix.ai_generated assertions", async () => {
    const m = await buildBasic();
    const labels = m.assertions.map((a) => a["label"]);
    expect(labels).toContain(C2PA_AssertionType.C2PA_ACTIONS);
    expect(labels).toContain(C2PA_AssertionType.NANORIX_AI_GENERATED);
  });

  it("c2pa.actions assertion uses trainedAlgorithmicMedia digitalSourceType", async () => {
    const m = await buildBasic();
    const actions = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.C2PA_ACTIONS,
    );
    const data = actions?.["data"] as { actions: Array<Record<string, unknown>> };
    expect(data.actions[0]["action"]).toBe(C2PA_ACTION_CREATED);
    expect(data.actions[0]["digitalSourceType"]).toBe(
      C2PA_DIGITAL_SOURCE_TYPE_AI,
    );
  });

  it("nanorix.ai_generated includes the three audit-proof binders", async () => {
    const m = await buildBasic();
    const nx = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    );
    const data = nx?.["data"] as Record<string, string>;
    expect(data["capsule_id"]).toBe("cap_abc123");
    expect(data["auditproof_id"]).toBe("cdp_def456");
    expect(data["verification_key_id"]).toBe(
      "nrx-verify-2026-05-28T12-34-50-cap_abc1",
    );
  });

  it("nanorix.ai_generated includes disclaimer", async () => {
    const m = await buildBasic();
    const nx = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    );
    const data = nx?.["data"] as Record<string, string>;
    expect(data["disclaimer"]).toBe(C2PA_NARROW_CLAIM_DISCLAIMER);
  });

  it("output_content_hash uses sha512: prefix and matches input", async () => {
    const m = await buildBasic();
    const nx = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    );
    const data = nx?.["data"] as Record<string, string>;
    expect(data["output_content_hash"].startsWith("sha512:")).toBe(true);
    const expected = await webcrypto.subtle.digest(
      "SHA-512",
      new TextEncoder().encode("This is the generated narrative.").buffer as ArrayBuffer,
    );
    const expectedHex = Array.from(new Uint8Array(expected))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
    expect(data["output_content_hash"]).toBe("sha512:" + expectedHex);
  });

  it("canonical bytes are deterministic", async () => {
    const m = await buildBasic();
    const a = m.toCanonicalBytes();
    const b = m.toCanonicalBytes();
    expect(Buffer.from(a).equals(Buffer.from(b))).toBe(true);
  });

  it("canonical bytes match JCS round-trip", async () => {
    const m = await buildBasic();
    const canonical = m.toCanonicalBytes();
    const parsed = JSON.parse(new TextDecoder().decode(canonical));
    const redo = canonicalizeBytes(parsed);
    expect(Buffer.from(canonical).equals(Buffer.from(redo))).toBe(true);
  });
});

// ── Group 2: Required-field enforcement ──

describe("Required-field enforcement (customer-sole-declarer)", () => {
  it("sign without capsule binders rejects", async () => {
    const b = new C2PAManifestBuilder()
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"));
    await expect(b.sign(signer)).rejects.toThrow(/capsuleId/);
  });

  it("sign without model rejects", async () => {
    const b = new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"));
    await expect(b.sign(signer)).rejects.toThrow(/modelId/);
  });

  it("sign without prompt intent rejects", async () => {
    const b = new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"));
    await expect(b.sign(signer)).rejects.toThrow(/promptIntent/);
  });

  it("sign without generation timestamp rejects", async () => {
    const b = new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withOutputContent(new TextEncoder().encode("o"));
    await expect(b.sign(signer)).rejects.toThrow(/generationTimestamp/);
  });

  it("sign without output content rejects", async () => {
    const b = new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z");
    await expect(b.sign(signer)).rejects.toThrow(/outputContent/);
  });
});

// ── Group 3: Field-validation rejections ──

describe("Field-validation rejections", () => {
  it("rejects oversized capsule_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "x".repeat(C2PA_MAX_CAPSULE_ID_LEN + 1),
        auditproofId: "a",
        verificationKeyId: "v",
      }),
    ).toThrow(/capsule_id/);
  });

  it("rejects oversized auditproof_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "c",
        auditproofId: "x".repeat(C2PA_MAX_AUDITPROOF_ID_LEN + 1),
        verificationKeyId: "v",
      }),
    ).toThrow(/auditproof_id/);
  });

  it("rejects oversized verification_key_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "x".repeat(C2PA_MAX_VERIFICATION_KEY_ID_LEN + 1),
      }),
    ).toThrow(/verification_key_id/);
  });

  it("rejects oversized model_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withModel({
        modelId: "x".repeat(C2PA_MAX_MODEL_ID_LEN + 1),
      }),
    ).toThrow(/model_id/);
  });

  it("rejects oversized prompt intent", () => {
    expect(() =>
      new C2PAManifestBuilder().withPromptIntent(
        "x".repeat(C2PA_MAX_PROMPT_INTENT_LEN + 1),
      ),
    ).toThrow(/prompt/);
  });

  it("rejects empty capsule_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "",
        auditproofId: "a",
        verificationKeyId: "v",
      }),
    ).toThrow();
  });

  it("rejects empty output content", () => {
    expect(() =>
      new C2PAManifestBuilder().withOutputContent(new Uint8Array(0)),
    ).toThrow(/output_content/);
  });

  it("rejects non-Uint8Array output content", () => {
    expect(() =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      new C2PAManifestBuilder().withOutputContent("not-bytes" as any),
    ).toThrow(TypeError);
  });

  it("rejects invalid RFC 3339 timestamp", () => {
    expect(() =>
      new C2PAManifestBuilder().withGenerationTimestamp("not-a-timestamp"),
    ).toThrow(/RFC 3339/);
  });

  it("accepts RFC 3339 timestamp with offset", () => {
    expect(() =>
      new C2PAManifestBuilder().withGenerationTimestamp(
        "2026-05-28T12:34:56+05:30",
      ),
    ).not.toThrow();
  });

  it("rejects model_weights_hash without sha512: prefix", () => {
    expect(() =>
      new C2PAManifestBuilder().withModel({
        modelId: "m",
        modelWeightsHash: "abc123",
      }),
    ).toThrow(/sha512:/);
  });

  it("accepts model_weights_hash with sha512: prefix", () => {
    expect(() =>
      new C2PAManifestBuilder().withModel({
        modelId: "m",
        modelWeightsHash: "sha512:" + "a".repeat(128),
      }),
    ).not.toThrow();
  });
});

// ── Group 4: Forbidden-token scans ──

describe("Forbidden-token scans (narrow-claim discipline)", () => {
  for (const token of C2PA_FORBIDDEN_BRAND_LEAK_TOKENS) {
    it(`rejects brand-leak token "${token}" in model_id`, () => {
      expect(() =>
        new C2PAManifestBuilder().withModel({
          modelId: `openai/${token}-gpt-4o`,
        }),
      ).toThrow(/brand-leak/);
    });
  }

  for (const token of C2PA_FORBIDDEN_REGULATORY_TOKENS) {
    it(`rejects regulatory token "${token}" in prompt_intent`, () => {
      expect(() =>
        new C2PAManifestBuilder().withPromptIntent(
          `This output ${token} all HIPAA controls`,
        ),
      ).toThrow(/regulatory/);
    });
  }

  it("rejects brand-leak token in capsule_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "VerificationCdp_run_1",
        auditproofId: "a",
        verificationKeyId: "v",
      }),
    ).toThrow(/brand-leak/);
  });

  it("rejects brand-leak token in verification_key_id", () => {
    expect(() =>
      new C2PAManifestBuilder().withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "AVE-key",
      }),
    ).toThrow(/brand-leak/);
  });

  it("rejects regulatory token in claim_generator", () => {
    expect(() =>
      new C2PAManifestBuilder().withClaimGenerator("My App PASSED audit"),
    ).toThrow(/regulatory/);
  });
});

// ── Group 5: Custom assertions extensibility ──

describe("Custom assertions", () => {
  it("rejects unknown assertion label", () => {
    expect(() =>
      new C2PAManifestBuilder().withAssertion({
        label: "custom.not_in_enum",
        data: {},
      }),
    ).toThrow(/AssertionType/);
  });

  it("accepts valid closed-enum assertion label", async () => {
    const m = await new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"))
      .withAssertion({
        label: C2PA_AssertionType.C2PA_TRAINING_MINING,
        data: { allowed: false },
      })
      .sign(signer);
    const labels = m.assertions.map((a) => a["label"]);
    expect(labels).toContain(C2PA_AssertionType.C2PA_TRAINING_MINING);
  });

  it("rejects forbidden token inside assertion data", () => {
    expect(() =>
      new C2PAManifestBuilder().withAssertion({
        label: C2PA_AssertionType.C2PA_INGREDIENT,
        data: { note: "This SATISFIED everything" },
      }),
    ).toThrow();
  });
});

// ── Group 6: Closed-enum invariants ──

describe("Closed-enum invariants", () => {
  it("assertion types are members of closed enum", () => {
    for (const value of Object.values(C2PA_AssertionType)) {
      expect(C2PA_VALID_ASSERTION_TYPES.has(value)).toBe(true);
    }
  });

  it("there are exactly 5 assertion types", () => {
    expect(C2PA_VALID_ASSERTION_TYPES.size).toBe(5);
  });

  it("signature algorithm is Ed25519 only", () => {
    expect(C2PA_SIGNATURE_ALGORITHM).toBe("Ed25519");
  });

  it("claim_generator prefix is Nanorix/1.0", () => {
    expect(C2PA_CLAIM_GENERATOR_PREFIX).toBe("Nanorix/1.0");
  });
});

// ── Group 7: Independent offline verification ──

describe("Offline verification", () => {
  it("returns signatureValid=true for a freshly-signed manifest", async () => {
    const m = await buildBasic();
    const result = await verifyC2PAManifest(
      m.toCanonicalBytes(),
      signerPublicKey,
    );
    expect(result.signatureValid).toBe(true);
    expect(result.claimsHashValid).toBe(true);
  });

  it("returns signatureValid=false for wrong public key", async () => {
    const other = await Ed25519GeneratedKey.generate();
    const otherKey = await other.publicKeyBytes();
    const m = await buildBasic();
    const result = await verifyC2PAManifest(m.toCanonicalBytes(), otherKey);
    expect(result.signatureValid).toBe(false);
  });

  it("extracts capsule binders into result", async () => {
    const m = await buildBasic();
    const result = await verifyC2PAManifest(
      m.toCanonicalBytes(),
      signerPublicKey,
    );
    expect(result.capsuleId).toBe("cap_abc123");
    expect(result.auditproofId).toBe("cdp_def456");
    expect(result.verificationKeyId).toBe(
      "nrx-verify-2026-05-28T12-34-50-cap_abc1",
    );
    expect(result.modelId).toBe("openai/gpt-4o-2024-08-06");
  });

  it("verification result carries disclaimer", async () => {
    const m = await buildBasic();
    const result = await verifyC2PAManifest(
      m.toCanonicalBytes(),
      signerPublicKey,
    );
    expect(result.disclaimer).toBe(C2PA_NARROW_CLAIM_DISCLAIMER);
    expect(result.disclaimer.toUpperCase()).toContain("STRUCTURAL");
  });
});

// ── Group 8: Tampering detection ──

describe("Tampering detection", () => {
  it("modified output_content_hash breaks verification", async () => {
    const m = await buildBasic();
    const d = m.toDict();
    const nx = d.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    ) as Record<string, unknown>;
    (nx["data"] as Record<string, string>)["output_content_hash"] =
      "sha512:" + "f".repeat(128);
    const tamperedBytes = canonicalizeBytes(d);
    const result = await verifyC2PAManifest(tamperedBytes, signerPublicKey);
    expect(result.claimsHashValid === false || result.signatureValid === false).toBe(
      true,
    );
  });

  it("modified signature breaks verification", async () => {
    const m = await buildBasic();
    const d = m.toDict();
    const sig = d.signature.signature;
    const sigBody = sig.slice("base64:".length);
    const decoded = Buffer.from(sigBody, "base64");
    decoded[0] ^= 0xff;
    d.signature.signature = "base64:" + decoded.toString("base64");
    const tamperedBytes = canonicalizeBytes(d);
    const result = await verifyC2PAManifest(tamperedBytes, signerPublicKey);
    expect(result.signatureValid).toBe(false);
  });

  it("adding an assertion breaks claims_hash", async () => {
    const m = await buildBasic();
    const d = m.toDict();
    const newAssertions: ReadonlyArray<Record<string, unknown>> = [
      ...d.assertions,
      {
        label: C2PA_AssertionType.C2PA_INGREDIENT,
        data: { injected: true },
      },
    ];
    const tampered = { ...d, assertions: newAssertions };
    const tamperedBytes = canonicalizeBytes(tampered);
    const result = await verifyC2PAManifest(tamperedBytes, signerPublicKey);
    expect(result.claimsHashValid).toBe(false);
  });

  it("modified model_id breaks claims_hash", async () => {
    const m = await buildBasic();
    const d = m.toDict();
    const nx = d.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    ) as Record<string, unknown>;
    (nx["data"] as Record<string, string>)["model_id"] =
      "openai/different-model";
    const tamperedBytes = canonicalizeBytes(d);
    const result = await verifyC2PAManifest(tamperedBytes, signerPublicKey);
    expect(result.claimsHashValid).toBe(false);
  });
});

// ── Group 9: Optional / extension fields ──

describe("Optional fields", () => {
  it("model_weights_hash omitted when not supplied", async () => {
    const m = await buildBasic();
    const nx = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    ) as Record<string, unknown>;
    expect("model_weights_hash" in (nx["data"] as Record<string, unknown>)).toBe(
      false,
    );
  });

  it("model_weights_hash present when supplied", async () => {
    const weights = "sha512:" + "a".repeat(128);
    const m = await new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m", modelWeightsHash: weights })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"))
      .sign(signer);
    const nx = m.assertions.find(
      (a) => a["label"] === C2PA_AssertionType.NANORIX_AI_GENERATED,
    ) as Record<string, unknown>;
    expect((nx["data"] as Record<string, string>)["model_weights_hash"]).toBe(
      weights,
    );
  });

  it("custom claim_generator can be set", async () => {
    const m = await new C2PAManifestBuilder()
      .withClaimGenerator("Nanorix/1.0 my-app/2.0.0")
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"))
      .sign(signer);
    expect(m.claimGenerator).toBe("Nanorix/1.0 my-app/2.0.0");
  });

  it("appended claim_generator_info", async () => {
    const m = await new C2PAManifestBuilder()
      .withClaimGeneratorInfo({ name: "my-app", version: "2.0.0" })
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"))
      .sign(signer);
    expect(m.claimGeneratorInfo.length).toBe(2);
    expect(m.claimGeneratorInfo[1].name).toBe("my-app");
  });
});

// ── Group 10: Signature algorithm enforcement ──

describe("Signature algorithm enforcement", () => {
  it("rejects non-Ed25519 signer", async () => {
    class WrongSigner implements EventSigner {
      async sign(): Promise<Uint8Array> {
        return new Uint8Array(64);
      }
      async publicKeyBytes(): Promise<Uint8Array> {
        return new Uint8Array(32);
      }
      algorithmName(): string {
        return "RSA-PSS-SHA256";
      }
    }
    const b = new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "c",
        auditproofId: "a",
        verificationKeyId: "v",
      })
      .withModel({ modelId: "m" })
      .withPromptIntent("i")
      .withGenerationTimestamp("2026-05-28T12:34:56Z")
      .withOutputContent(new TextEncoder().encode("o"));
    await expect(b.sign(new WrongSigner())).rejects.toThrow(/Ed25519/);
  });

  it("verifyC2PAManifest rejects unsupported algorithm", async () => {
    const fake = {
      claim_generator: "X",
      claim_generator_info: [],
      format: C2PA_MANIFEST_FORMAT,
      assertions: [],
      claims_hash: "sha512:" + "a".repeat(128),
      signature: {
        algorithm: "ECDSA",
        public_key: "base64:AAAA",
        signature: "base64:AAAA",
      },
    };
    await expect(
      verifyC2PAManifest(
        new TextEncoder().encode(JSON.stringify(fake)),
        new Uint8Array(32),
      ),
    ).rejects.toThrow(/signature algorithm/);
  });
});

// ── Group 11: Round-trip + accept string input ──

describe("Round-trip + str input", () => {
  it("verifies when passed a string", async () => {
    const m = await buildBasic();
    const s = new TextDecoder().decode(m.toCanonicalBytes());
    const result = await verifyC2PAManifest(s, signerPublicKey);
    expect(result.signatureValid).toBe(true);
  });

  it("round-trips via canonical bytes", async () => {
    const m = await buildBasic();
    const canonical = m.toCanonicalBytes();
    const parsed = JSON.parse(new TextDecoder().decode(canonical));
    const reCanonical = canonicalizeBytes(parsed);
    expect(Buffer.from(canonical).equals(Buffer.from(reCanonical))).toBe(true);
  });

  it("toJSON parses cleanly", async () => {
    const m = await buildBasic();
    const parsed = JSON.parse(m.toJSON());
    expect(parsed.claims_hash.startsWith("sha512:")).toBe(true);
  });
});

// ── Group 12: AuditProof byte-form unchanged (narrowness invariant) ──

describe("Narrowness invariant (receipt narrowness)", () => {
  it("manifest module does not import internal struct names", async () => {
    // Read the c2pa_provenance.ts source and verify no IP leakage
    const fs = await import("node:fs/promises");
    const path = new URL("../src/integrations/c2pa_provenance.ts", import.meta.url).pathname;
    const src = await fs.readFile(path, "utf-8");
    // Internal struct names must NEVER appear outside the forbidden-list literal
    const offendingPattern = /\b(VerificationCdp|FullCdp|AVE)\b/g;
    const matches = src.match(offendingPattern) ?? [];
    // The only legal occurrence is inside FORBIDDEN_BRAND_LEAK_TOKENS literal
    // (4 forbidden tokens occur once each in the array)
    expect(matches.length).toBeLessThanOrEqual(3);
  });

  it("c2pa module does not import the AuditProof verifier surface", async () => {
    const fs = await import("node:fs/promises");
    const path = new URL("../src/integrations/c2pa_provenance.ts", import.meta.url).pathname;
    const src = await fs.readFile(path, "utf-8");
    expect(src).not.toContain('from "../verifier/');
    expect(src).not.toContain('from "../models');
  });
});

// ── Group 13: Three binders round-trip ──

describe("AuditProof binding round-trip", () => {
  it("three binders survive sign + verify roundtrip", async () => {
    const m = await new C2PAManifestBuilder()
      .withCapsule({
        capsuleId: "cap_specific_001",
        auditproofId: "cdp_specific_002",
        verificationKeyId: "nrx-verify-test-key-003",
      })
      .withModel({ modelId: "anthropic/claude-3-5-sonnet" })
      .withPromptIntent("structural test")
      .withGenerationTimestamp("2026-05-28T00:00:00Z")
      .withOutputContent(new TextEncoder().encode("output bytes here"))
      .sign(signer);
    const result = await verifyC2PAManifest(
      m.toCanonicalBytes(),
      signerPublicKey,
    );
    expect(result.capsuleId).toBe("cap_specific_001");
    expect(result.auditproofId).toBe("cdp_specific_002");
    expect(result.verificationKeyId).toBe("nrx-verify-test-key-003");
    expect(result.modelId).toBe("anthropic/claude-3-5-sonnet");
    expect(result.signatureValid).toBe(true);
    expect(result.claimsHashValid).toBe(true);
  });
});
