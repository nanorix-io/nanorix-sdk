/**
 * C2PA LLM-Output Provenance — sibling primitive next to AuditProof.
 *
 * Under the 2026-05-28. Customers running LLM/AI inference inside
 * Nanorix capsules emit a C2PA-conformant signed manifest binding the
 * generated output content to the capsule run that produced it. The
 * manifest references the AuditProof by three opaque identifiers
 * (capsule_id, auditproof_id, verification_key_id) without modifying
 * the AuditProof byte-form — strict Forever-Standard
 * byte-equivalence preservation.
 *
 * ## C2PA standard
 *
 * Coalition for Content Provenance and Authenticity. Open standard
 * (ISO 22144 / TR 24337-2). Already shipping in Microsoft Designer,
 * OpenAI DALL-E, Sony cameras, Adobe Photoshop, BBC editorial.
 * Reference: https://c2pa.org/specifications/
 *
 * ## EU AI Act Article 50
 *
 * Effective August 2026. Mandates that providers of generative AI
 * systems ensure outputs are "marked in a machine-readable format"
 * and "detectable as artificially generated or manipulated" via
 * cryptographic guarantees. C2PA is the de-facto-standard
 * interchange format for satisfying these transparency obligations.
 *
 * ## Three-role discipline
 *
 * Nanorix signs STRUCTURAL FACTS only. Auditor certifies regulatory
 * adequacy. SDK refuses to claim "satisfies Art 50" / "passes" /
 * "meets" / "compliant" — those are the auditor's determinations.
 *
 * ## Narrowness invariant (sibling primitive, NOT receipt enrichment)
 *
 * Per feedback_narrowness_is_the_moat_resist_receipt_enrichment.md
 * PERMANENT: AuditProof / RecordReceipt / ParentProofLink fields are
 * UNCHANGED by this module. The C2PA manifest is a fully external
 * sibling primitive. AuditProof byte-form is explicitly affirmed
 * unchanged.
 *
 * ## Usage
 *
 * ```ts
 * import {
 *   C2PAManifestBuilder,
 *   verifyC2PAManifest,
 *   Ed25519GeneratedKey,
 * } from "@nanorix/sdk";
 *
 * const signer = await Ed25519GeneratedKey.generate();
 *
 * const manifest = await new C2PAManifestBuilder()
 *   .withCapsule({
 *     capsuleId: "cap_abc123",
 *     auditproofId: "cdp_def456",
 *     verificationKeyId: "nrx-verify-2026-05-28T12-34-50-cap_abc1",
 *   })
 *   .withModel({ modelId: "openai/gpt-4o-2024-08-06" })
 *   .withPromptIntent("clinical_pa_decision_narrative_v3")
 *   .withGenerationTimestamp("2026-05-28T12:34:56Z")
 *   .withOutputContent(outputBytes)
 *   .sign(signer);
 *
 * const manifestBytes = manifest.toCanonicalBytes();
 *
 * // Third-party offline verification
 * const result = await verifyC2PAManifest(
 *   manifestBytes,
 *   await signer.publicKeyBytes(),
 * );
 * console.log(result.signatureValid); // true
 * ```
 */

import { canonicalizeBytes } from "../_jcs.js";
import type { EventSigner } from "../helpers/signer.js";

// ── Closed enums + constants (Forever-Standard discipline) ──

export const CLAIM_GENERATOR_PREFIX = "Nanorix/1.0";
/** Wire-format claim_generator prefix; customer may append their own tooling. */

export const MANIFEST_FORMAT = "application/json";
/** JSON manifest format (media-format-specific JUMBF embedding is out of scope). */

export const SIGNATURE_ALGORITHM = "Ed25519";
/** Closed enum; matches existing per-event signing primitive. */

export const DIGITAL_SOURCE_TYPE_AI = "trainedAlgorithmicMedia";
/** C2PA standard digitalSourceType for AI-generated content. */

export const C2PA_ACTION_CREATED = "c2pa.created";
/** C2PA standard action for newly-generated content. */

/**
 * Closed enum of supported assertion labels.
 *
 * Append-only Forever-Standard; new variants arrive via
 * ADR amendment. C2PA standard labels + Nanorix-namespaced extensions
 * for capsule + AuditProof binding.
 */
export const AssertionType = {
  C2PA_ACTIONS: "c2pa.actions",
  C2PA_INGREDIENT: "c2pa.ingredient",
  C2PA_TRAINING_MINING: "c2pa.training-mining",
  NANORIX_AI_GENERATED: "nanorix.ai_generated",
  NANORIX_CAPSULE_BINDING: "nanorix.capsule_binding",
} as const;
export type AssertionTypeValue =
  (typeof AssertionType)[keyof typeof AssertionType];

export const VALID_ASSERTION_TYPES: ReadonlySet<string> = new Set(
  Object.values(AssertionType),
);

// ── Length caps + validation ──

export const MAX_MODEL_ID_LEN = 512;
export const MAX_PROMPT_INTENT_LEN = 4096;
export const MAX_CAPSULE_ID_LEN = 64;
export const MAX_AUDITPROOF_ID_LEN = 64;
export const MAX_VERIFICATION_KEY_ID_LEN = 128;
export const MAX_TIMESTAMP_LEN = 64;

/**
 * Tokens that must NEVER appear in customer-facing C2PA manifest content.
 * Internal struct/engine names are confidential brand discipline.
 */
export const FORBIDDEN_BRAND_LEAK_TOKENS: readonly string[] = [
  "VerificationCdp",
  "FullCdp",
  "AVE",
];

/**
 * Tokens that violate the narrow-signed-claim discipline.
 * Per feedback_narrow_signed_claim_auditor_certifies.md PERMANENT.
 */
export const FORBIDDEN_REGULATORY_TOKENS: readonly string[] = [
  "COMPLIANT",
  "SATISFIED",
  "PASSED",
  "MEETS",
];

/**
 * Disclaimer carried in every verification result + every manifest's
 * nanorix.ai_generated assertion data.
 *
 * Per feedback_narrow_signed_claim_auditor_certifies.md PERMANENT:
 * structural attestation NEVER asserts regulatory adequacy; uses
 * `related_to` not `satisfies/meets/passes/complies`.
 */
export const NARROW_CLAIM_DISCLAIMER =
  "Structural attestation only. This manifest records that the " +
  "named model was used in the named capsule at the named time " +
  "with the named output content hash. It is RELATED_TO EU AI Act " +
  "Article 50 transparency obligations and other content-provenance " +
  "regulatory regimes. It is NOT a determination that the output " +
  "(or the system that produced it) is in regulatory conformance " +
  "for any specific use case. That determination is the " +
  "responsibility of the auditor / certifying authority / regulator " +
  "with jurisdiction over the specific deployment.";

// ── Internal helpers ──

function checkForbiddenTokens(value: string, fieldName: string): void {
  for (const tok of FORBIDDEN_BRAND_LEAK_TOKENS) {
    if (value.includes(tok)) {
      throw new Error(
        `Field "${fieldName}": value contains forbidden brand-leak ` +
          `token "${tok}". Internal struct/engine names must not appear ` +
          `in customer-facing C2PA manifests.`,
      );
    }
  }
  for (const tok of FORBIDDEN_REGULATORY_TOKENS) {
    if (value.includes(tok)) {
      throw new Error(
        `Field "${fieldName}": value contains forbidden regulatory ` +
          `token "${tok}". C2PA manifest assertions are STRUCTURAL ` +
          `facts only; regulatory adequacy is the auditor's ` +
          `determination (` +
          `feedback_narrow_signed_claim_auditor_certifies.md).`,
      );
    }
  }
}

function bytesToHex(buf: Uint8Array): string {
  let out = "";
  for (let i = 0; i < buf.length; i++) {
    out += buf[i].toString(16).padStart(2, "0");
  }
  return out;
}

function bytesToBase64(buf: Uint8Array): string {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(buf).toString("base64");
  }
  let bin = "";
  for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
  // eslint-disable-next-line no-undef
  return btoa(bin);
}

function base64Decode(value: string): Uint8Array {
  const stripped = value.startsWith("base64:")
    ? value.slice("base64:".length)
    : value;
  if (typeof Buffer !== "undefined") {
    return new Uint8Array(Buffer.from(stripped, "base64"));
  }
  // eslint-disable-next-line no-undef
  const bin = atob(stripped);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function sha512(data: Uint8Array): Promise<Uint8Array> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const g = globalThis as any;
  if (g.crypto?.subtle) {
    const hash = await g.crypto.subtle.digest(
      "SHA-512",
      data.buffer as ArrayBuffer,
    );
    return new Uint8Array(hash);
  }
  throw new Error(
    "Web Crypto subtle.digest unavailable (need Node 18+ or modern browser)",
  );
}

async function sha512Hex(data: Uint8Array): Promise<string> {
  const digest = await sha512(data);
  return "sha512:" + bytesToHex(digest);
}

// ── Interfaces ──

/** One entry in claim_generator_info array per C2PA spec. */
export interface C2PAClaimGeneratorInfo {
  name: string;
  version: string;
  icon?: string;
}

/** Ed25519 signature material attached to a signed manifest. */
export interface C2PASignatureInfo {
  algorithm: string; // closed: "Ed25519"
  publicKey: string; // "base64:..." wire form
  signature: string; // "base64:..." wire form
}

/** Wire-format interface (snake_case) for the manifest signature block. */
interface C2PASignatureWire {
  algorithm: string;
  public_key: string;
  signature: string;
}

/** Wire-format manifest dict (snake_case) emitted by toDict(). */
export interface C2PAManifestDict {
  claim_generator: string;
  claim_generator_info: ReadonlyArray<C2PAClaimGeneratorInfo>;
  format: string;
  assertions: ReadonlyArray<Record<string, unknown>>;
  claims_hash: string;
  signature: C2PASignatureWire;
}

/**
 * Structural verification result — NEVER a regulatory verdict.
 *
 * Three-role split: this result is the Nanorix-side
 * STRUCTURAL determination. Whether the structural pass satisfies any
 * regulatory regime (e.g., EU AI Act Article 50) for a specific
 * deployment is the auditor's determination — downstream of this result.
 */
export interface C2PAVerificationResult {
  signatureValid: boolean;
  claimsHashValid: boolean;
  claimGenerator: string;
  assertions: ReadonlyArray<Record<string, unknown>>;
  outputContentHash: string | null;
  modelId: string | null;
  modelWeightsHash: string | null;
  capsuleId: string | null;
  auditproofId: string | null;
  verificationKeyId: string | null;
  promptIntentHash: string | null;
  generationTimestamp: string | null;
  disclaimer: string;
}

/** Options for withCapsule(). */
export interface WithCapsuleOptions {
  capsuleId: string;
  auditproofId: string;
  verificationKeyId: string;
}

/** Options for withModel(). */
export interface WithModelOptions {
  modelId: string;
  modelWeightsHash?: string;
}

// ── Manifest class ──

/**
 * A signed C2PA manifest. Sibling primitive next to AuditProof.
 *
 * Wire form is a JSON object whose canonical bytes (RFC 8785 JCS) are
 * SHA-512 hashed; that hash is signed via Ed25519. The signature is
 * independently verifiable offline given the public key bytes.
 *
 * Under the 2026-05-28, this manifest references the AuditProof by
 * three opaque identifiers without modifying the AuditProof byte-form.
 * Strict Forever-Standard byte-equivalence preservation.
 */
export class C2PAManifest {
  readonly claimGenerator: string;
  readonly claimGeneratorInfo: ReadonlyArray<C2PAClaimGeneratorInfo>;
  readonly format: string;
  readonly assertions: ReadonlyArray<Record<string, unknown>>;
  readonly claimsHash: string;
  readonly signatureInfo: C2PASignatureInfo;

  constructor(args: {
    claimGenerator: string;
    claimGeneratorInfo: ReadonlyArray<C2PAClaimGeneratorInfo>;
    format: string;
    assertions: ReadonlyArray<Record<string, unknown>>;
    claimsHash: string;
    signatureInfo: C2PASignatureInfo;
  }) {
    this.claimGenerator = args.claimGenerator;
    this.claimGeneratorInfo = args.claimGeneratorInfo;
    this.format = args.format;
    this.assertions = args.assertions;
    this.claimsHash = args.claimsHash;
    this.signatureInfo = args.signatureInfo;
  }

  /** Render to wire-format dict suitable for JSON encoding (snake_case). */
  toDict(): C2PAManifestDict {
    return {
      claim_generator: this.claimGenerator,
      claim_generator_info: this.claimGeneratorInfo,
      format: this.format,
      assertions: this.assertions,
      claims_hash: this.claimsHash,
      signature: {
        algorithm: this.signatureInfo.algorithm,
        public_key: this.signatureInfo.publicKey,
        signature: this.signatureInfo.signature,
      },
    };
  }

  /** JCS-canonicalized bytes (RFC 8785). Stable across languages. */
  toCanonicalBytes(): Uint8Array {
    return canonicalizeBytes(this.toDict());
  }

  /** Pretty-print JSON (non-canonical; for human inspection only). */
  toJSON(indent?: number): string {
    return JSON.stringify(this.toDict(), null, indent);
  }
}

// ── Builder ──

/**
 * Fluent builder for assembling + signing a C2PA manifest.
 *
 * Customer chains method calls; each method returns this for fluency.
 * Final `.sign(signer)` validates required fields, computes the claims
 * hash, signs it, and returns an immutable C2PAManifest.
 *
 * All inference is REFUSED customer-sole-declarer. The
 * builder will not infer modelId, promptIntent, generation timestamp,
 * output bytes — customer must declare each explicitly. Missing
 * required field at .sign() time -> throws.
 */
export class C2PAManifestBuilder {
  private capsuleId: string | null = null;
  private auditproofId: string | null = null;
  private verificationKeyId: string | null = null;
  private modelId: string | null = null;
  private modelWeightsHash: string | null = null;
  private promptIntentHash: string | null = null;
  private generationTimestamp: string | null = null;
  private outputContentHash: string | null = null;
  private extraAssertions: Record<string, unknown>[] = [];
  private claimGenerator: string = CLAIM_GENERATOR_PREFIX;
  private claimGeneratorInfo: C2PAClaimGeneratorInfo[] = [
    { name: "Nanorix", version: "1.0" },
  ];

  // ── Required setters ──

  /**
   * Bind manifest to a Nanorix AuditProof via three opaque ids.
   *
   * The three identifiers together are sufficient for any downstream
   * verifier to fetch the AuditProof (public-no-auth) and
   * independently verify both the manifest and the AuditProof.
   */
  withCapsule(opts: WithCapsuleOptions): C2PAManifestBuilder {
    const { capsuleId, auditproofId, verificationKeyId } = opts;
    if (!capsuleId || capsuleId.length > MAX_CAPSULE_ID_LEN) {
      throw new Error(
        `capsule_id must be 1..${MAX_CAPSULE_ID_LEN} chars; got ${capsuleId.length}`,
      );
    }
    if (!auditproofId || auditproofId.length > MAX_AUDITPROOF_ID_LEN) {
      throw new Error(
        `auditproof_id must be 1..${MAX_AUDITPROOF_ID_LEN} chars; got ${auditproofId.length}`,
      );
    }
    if (
      !verificationKeyId ||
      verificationKeyId.length > MAX_VERIFICATION_KEY_ID_LEN
    ) {
      throw new Error(
        `verification_key_id must be 1..${MAX_VERIFICATION_KEY_ID_LEN} chars; got ${verificationKeyId.length}`,
      );
    }
    checkForbiddenTokens(capsuleId, "capsule_id");
    checkForbiddenTokens(auditproofId, "auditproof_id");
    checkForbiddenTokens(verificationKeyId, "verification_key_id");
    this.capsuleId = capsuleId;
    this.auditproofId = auditproofId;
    this.verificationKeyId = verificationKeyId;
    return this;
  }

  /**
   * Customer-declared model attribution.
   *
   * modelId is opaque to Nanorix (not validated against any registry).
   * modelWeightsHash is optional. Both.
   */
  withModel(opts: WithModelOptions): C2PAManifestBuilder {
    const { modelId, modelWeightsHash } = opts;
    if (!modelId || modelId.length > MAX_MODEL_ID_LEN) {
      throw new Error(
        `model_id must be 1..${MAX_MODEL_ID_LEN} chars; got ${modelId.length}`,
      );
    }
    checkForbiddenTokens(modelId, "model_id");
    if (modelWeightsHash !== undefined) {
      if (!modelWeightsHash.startsWith("sha512:")) {
        throw new Error(
          "model_weights_hash must use sha512: prefix; got " +
            modelWeightsHash.slice(0, 20) +
            "...",
        );
      }
      checkForbiddenTokens(modelWeightsHash, "model_weights_hash");
    }
    this.modelId = modelId;
    this.modelWeightsHash = modelWeightsHash ?? null;
    return this;
  }

  /**
   * Record SHA-512 of customer-supplied intent text.
   *
   * Only the hash reaches the manifest; raw text never recorded
   * (privacy preserve — prompts may contain PHI/PII).
   *
   * This method computes the hash synchronously via the JCS+SHA-512
   * machinery available on the same Web Crypto event loop tick that
   * runs the canonical serializer. Implementation defers hash compute
   * to sign() time to avoid mixing sync + async fluently.
   */
  withPromptIntent(intentText: string): C2PAManifestBuilder {
    if (!intentText || intentText.length > MAX_PROMPT_INTENT_LEN) {
      throw new Error(
        `prompt intent must be 1..${MAX_PROMPT_INTENT_LEN} chars; got ${intentText.length}`,
      );
    }
    checkForbiddenTokens(intentText, "prompt_intent");
    // Store the raw text temporarily; hash at sign() time so we can keep
    // the fluent API synchronous up to .sign().
    this.promptIntentHashRawText = intentText;
    return this;
  }

  /** Set RFC 3339 timestamp of generation completion (customer's clock). */
  withGenerationTimestamp(timestamp: string): C2PAManifestBuilder {
    if (!timestamp || timestamp.length > MAX_TIMESTAMP_LEN) {
      throw new Error(
        `generation_timestamp must be 1..${MAX_TIMESTAMP_LEN} chars; got ${timestamp.length}`,
      );
    }
    const re =
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
    if (!re.test(timestamp)) {
      throw new Error(
        `generation_timestamp must be RFC 3339 format; got ${timestamp}`,
      );
    }
    this.generationTimestamp = timestamp;
    return this;
  }

  /**
   * Hash output bytes; bytes never stored.
   *
   * Customer captures the bytes at the point of attaching provenance.
   * SDK computes SHA-512 at sign() time; raw bytes never enter the
   * manifest. Zero-retention preserved.
   */
  withOutputContent(contentBytes: Uint8Array): C2PAManifestBuilder {
    if (!(contentBytes instanceof Uint8Array)) {
      throw new TypeError(
        `output_content must be Uint8Array; got ${typeof contentBytes}`,
      );
    }
    if (contentBytes.length === 0) {
      throw new Error("output_content must be non-empty");
    }
    this.outputContentRawBytes = contentBytes;
    return this;
  }

  // ── Optional setters / extensibility ──

  /** Override claim_generator string (default: "Nanorix/1.0"). */
  withClaimGenerator(claimGenerator: string): C2PAManifestBuilder {
    if (!claimGenerator || claimGenerator.length > 256) {
      throw new Error("claim_generator must be 1..256 chars");
    }
    checkForbiddenTokens(claimGenerator, "claim_generator");
    this.claimGenerator = claimGenerator;
    return this;
  }

  /** Append a claim_generator_info entry (e.g., customer's tool). */
  withClaimGeneratorInfo(info: C2PAClaimGeneratorInfo): C2PAManifestBuilder {
    checkForbiddenTokens(info.name, "claim_generator_info.name");
    checkForbiddenTokens(info.version, "claim_generator_info.version");
    this.claimGeneratorInfo.push(info);
    return this;
  }

  /**
   * Append an arbitrary assertion (closed-enum label required).
   *
   * Assertion labels come from a closed enum (AssertionType).
   * Open `data` payload accommodates forward evolution of the C2PA standard.
   */
  withAssertion(assertion: Record<string, unknown>): C2PAManifestBuilder {
    if (typeof assertion !== "object" || assertion === null) {
      throw new TypeError("assertion must be an object");
    }
    const label = assertion["label"];
    if (typeof label !== "string") {
      throw new Error("assertion missing string 'label' field");
    }
    if (!VALID_ASSERTION_TYPES.has(label)) {
      throw new Error(
        `assertion label "${label}" is not a recognized AssertionType; ` +
          `valid values: ${Array.from(VALID_ASSERTION_TYPES).sort().join(", ")}`,
      );
    }
    // Recursive forbidden-token scan
    const scan = (node: unknown, path: string): void => {
      if (typeof node === "string") {
        checkForbiddenTokens(node, path);
      } else if (Array.isArray(node)) {
        node.forEach((v, i) => scan(v, `${path}[${i}]`));
      } else if (node !== null && typeof node === "object") {
        for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
          checkForbiddenTokens(k, `${path}.${k}`);
          scan(v, `${path}.${k}`);
        }
      }
    };
    scan(assertion, "assertion");
    this.extraAssertions.push(assertion);
    return this;
  }

  // ── Sign + emit ──

  /** Assemble + sign the manifest. Required-field check enforced here. */
  async sign(signer: EventSigner): Promise<C2PAManifest> {
    // Required-field check
    const missing: string[] = [];
    if (this.capsuleId === null) missing.push("capsuleId (via withCapsule)");
    if (this.auditproofId === null)
      missing.push("auditproofId (via withCapsule)");
    if (this.verificationKeyId === null)
      missing.push("verificationKeyId (via withCapsule)");
    if (this.modelId === null) missing.push("modelId (via withModel)");
    if (this.promptIntentHashRawText === null)
      missing.push("promptIntent (via withPromptIntent)");
    if (this.generationTimestamp === null)
      missing.push("generationTimestamp (via withGenerationTimestamp)");
    if (this.outputContentRawBytes === null)
      missing.push("outputContent (via withOutputContent)");
    if (missing.length > 0) {
      throw new Error(
        "Cannot sign manifest: missing required customer-declared " +
          "fields: " +
          missing.join(", ") +
          ". Customer-sole-declarer, all required fields " +
          "must be set explicitly; Nanorix refuses to infer any of them.",
      );
    }

    if (signer.algorithmName() !== SIGNATURE_ALGORITHM) {
      throw new Error(
        `Signer algorithm must be "${SIGNATURE_ALGORITHM}"; got ` +
          `"${signer.algorithmName()}"`,
      );
    }

    // Compute deferred hashes
    this.promptIntentHash = await sha512Hex(
      new TextEncoder().encode(this.promptIntentHashRawText as string),
    );
    this.outputContentHash = await sha512Hex(
      this.outputContentRawBytes as Uint8Array,
    );

    // Build assertions list
    const assertions: Record<string, unknown>[] = [];

    // Mandatory C2PA actions assertion (created by AI)
    assertions.push({
      label: AssertionType.C2PA_ACTIONS,
      data: {
        actions: [
          {
            action: C2PA_ACTION_CREATED,
            softwareAgent: CLAIM_GENERATOR_PREFIX,
            digitalSourceType: DIGITAL_SOURCE_TYPE_AI,
            when: this.generationTimestamp,
          },
        ],
      },
    });

    // Nanorix-namespaced binding assertion
    const nanorixData: Record<string, unknown> = {
      model_id: this.modelId,
      capsule_id: this.capsuleId,
      auditproof_id: this.auditproofId,
      verification_key_id: this.verificationKeyId,
      prompt_intent_hash: this.promptIntentHash,
      generation_timestamp: this.generationTimestamp,
      output_content_hash: this.outputContentHash,
      disclaimer: NARROW_CLAIM_DISCLAIMER,
    };
    if (this.modelWeightsHash !== null) {
      nanorixData["model_weights_hash"] = this.modelWeightsHash;
    }
    assertions.push({
      label: AssertionType.NANORIX_AI_GENERATED,
      data: nanorixData,
    });

    // Extension assertions (customer-supplied)
    assertions.push(...this.extraAssertions);

    // Canonical claims object (excludes claims_hash + signature)
    const claimsObject: Record<string, unknown> = {
      claim_generator: this.claimGenerator,
      claim_generator_info: this.claimGeneratorInfo,
      format: MANIFEST_FORMAT,
      assertions,
    };

    const canonicalBytes = canonicalizeBytes(claimsObject);
    const claimsHashRaw = await sha512(canonicalBytes);
    const claimsHashPrefixed = "sha512:" + bytesToHex(claimsHashRaw);

    // Sign the raw 64-byte SHA-512 digest (NOT the ASCII-hex form)
    const signatureBytes = await signer.sign(claimsHashRaw);
    const publicKeyBytes = await signer.publicKeyBytes();

    const signatureInfo: C2PASignatureInfo = {
      algorithm: SIGNATURE_ALGORITHM,
      publicKey: "base64:" + bytesToBase64(publicKeyBytes),
      signature: "base64:" + bytesToBase64(signatureBytes),
    };

    return new C2PAManifest({
      claimGenerator: this.claimGenerator,
      claimGeneratorInfo: [...this.claimGeneratorInfo],
      format: MANIFEST_FORMAT,
      assertions,
      claimsHash: claimsHashPrefixed,
      signatureInfo,
    });
  }

  // ── Deferred internal storage (hash computed at sign() time) ──

  private promptIntentHashRawText: string | null = null;
  private outputContentRawBytes: Uint8Array | null = null;
}

// ── Verification ──

function extractNanorixAssertion(
  assertions: ReadonlyArray<Record<string, unknown>>,
): Record<string, unknown> | null {
  for (const a of assertions) {
    if (a["label"] === AssertionType.NANORIX_AI_GENERATED) {
      const data = a["data"];
      if (data !== null && typeof data === "object" && !Array.isArray(data)) {
        return data as Record<string, unknown>;
      }
    }
  }
  return null;
}

function strOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/**
 * Verify a C2PA manifest offline.
 *
 * Returns a STRUCTURAL verification result (signatureValid +
 * claimsHashValid + extracted fields). Whether the structural pass
 * satisfies any regulatory regime (e.g., EU AI Act Article 50) is the
 * auditor's determination — downstream of this function.
 */
export async function verifyC2PAManifest(
  manifestBytes: Uint8Array | string,
  publicKeyBytes: Uint8Array,
): Promise<C2PAVerificationResult> {
  const manifestStr =
    typeof manifestBytes === "string"
      ? manifestBytes
      : new TextDecoder().decode(manifestBytes);
  const manifest = JSON.parse(manifestStr) as Record<string, unknown>;

  if (manifest === null || typeof manifest !== "object" || Array.isArray(manifest)) {
    throw new Error("Manifest must be a JSON object");
  }

  const claimGenerator = (manifest["claim_generator"] as string) ?? "";
  const assertions = manifest["assertions"];
  if (!Array.isArray(assertions)) {
    throw new Error("Manifest assertions must be a list");
  }

  const signatureBlock = manifest["signature"];
  if (
    signatureBlock === null ||
    typeof signatureBlock !== "object" ||
    Array.isArray(signatureBlock)
  ) {
    throw new Error("Manifest signature must be an object");
  }
  const sigBlock = signatureBlock as Record<string, unknown>;

  const algorithm = sigBlock["algorithm"] as string;
  if (algorithm !== SIGNATURE_ALGORITHM) {
    throw new Error(
      `Unsupported signature algorithm: "${algorithm}"; expected ` +
        `"${SIGNATURE_ALGORITHM}"`,
    );
  }

  const signatureB64 = sigBlock["signature"] as string;
  const publicKeyWire = sigBlock["public_key"] as string;
  if (!signatureB64 || !publicKeyWire) {
    throw new Error("Manifest signature block missing signature/public_key");
  }

  const claimsHashStored = manifest["claims_hash"];
  if (typeof claimsHashStored !== "string") {
    throw new Error("Manifest claims_hash must be a string");
  }

  // Recompute the canonical claims object
  const claimsObject: Record<string, unknown> = {
    claim_generator: manifest["claim_generator"] ?? "",
    claim_generator_info: manifest["claim_generator_info"] ?? [],
    format: manifest["format"] ?? MANIFEST_FORMAT,
    assertions: assertions as ReadonlyArray<Record<string, unknown>>,
  };
  const canonicalBytes = canonicalizeBytes(claimsObject);
  const claimsHashRecomputedRaw = await sha512(canonicalBytes);
  const claimsHashRecomputed =
    "sha512:" + bytesToHex(claimsHashRecomputedRaw);
  const claimsHashValid = claimsHashRecomputed === claimsHashStored;

  // Signature verification — over RAW 64-byte SHA-512 digest
  const signatureBytes = base64Decode(signatureB64);
  let signatureValid = false;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const g = globalThis as any;
    if (!g.crypto?.subtle) {
      throw new Error("Web Crypto subtle unavailable");
    }
    const pubKey = await g.crypto.subtle.importKey(
      "raw",
      publicKeyBytes.buffer as ArrayBuffer,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    signatureValid = await g.crypto.subtle.verify(
      { name: "Ed25519" },
      pubKey,
      signatureBytes.buffer as ArrayBuffer,
      claimsHashRecomputedRaw.buffer as ArrayBuffer,
    );
  } catch {
    signatureValid = false;
  }

  // Extract Nanorix-namespaced fields for inspection
  const nxData = extractNanorixAssertion(
    assertions as ReadonlyArray<Record<string, unknown>>,
  );
  const data = nxData ?? {};

  return {
    signatureValid,
    claimsHashValid,
    claimGenerator,
    assertions: assertions as ReadonlyArray<Record<string, unknown>>,
    outputContentHash: strOrNull(data["output_content_hash"]),
    modelId: strOrNull(data["model_id"]),
    modelWeightsHash: strOrNull(data["model_weights_hash"]),
    capsuleId: strOrNull(data["capsule_id"]),
    auditproofId: strOrNull(data["auditproof_id"]),
    verificationKeyId: strOrNull(data["verification_key_id"]),
    promptIntentHash: strOrNull(data["prompt_intent_hash"]),
    generationTimestamp: strOrNull(data["generation_timestamp"]),
    disclaimer: NARROW_CLAIM_DISCLAIMER,
  };
}
