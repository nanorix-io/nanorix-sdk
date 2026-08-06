/**
 * ADR-039 + ADR-041 Wave B Item 3 — TypeScript SDK per-record receipt +
 * parent-proof tests.
 *
 * Faithful TS port of `sdk/python/tests/test_capsule_record.py`.
 * Covers `LiveCapsule.record(...)` + `LiveCapsule.withRecord(...)` +
 * `LiveCapsule.addParentProof(...)` ergonomic surfaces. Validates:
 *
 * - RecordContext lifecycle (open / bind / event / commit / abort)
 * - withRecord callback sugar (auto-commit on resolve, auto-abort on throw)
 * - pattern_tag closed-enum validation (15 valid variants; invalid rejected)
 * - Field length limits (record_id, parent_*) enforced client-side
 * - SHA-512 of canonical-JCS bound output matches the expected hash
 * - Cross-language byte-equivalence vs Python reference vectors
 * - HTTP mock for `/v1/capsules/:id/record` + `/v1/capsules/:id/parent`
 * - AuditProof response parsing with record_receipts + parent_proof_hashes
 * - ParentProofLink validation (sha512 / base64 prefixes carried as customer-supplied)
 * - Adapter-side resolver flow: receive AuditProof -> iterate receipts -> resolve mapping
 * - Forever-Standard round-trip: undefined receipts -> JSON -> parse -> still undefined
 * - RecordReceipt has NO `control_tags` field (three-layer separation)
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  expectTypeOf,
  it,
} from "vitest";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { NanorixClient } from "../src/client.js";
import {
  CONTROL_MAP_DISCLAIMER,
  CONTROL_MAP_FRAMEWORK_VERSION,
  loadMapping,
  resolveControls,
} from "../src/index.js";
import {
  MAX_HASH_LEN,
  MAX_PARENT_JURISDICTION_LEN,
  MAX_PARENT_KEY_ID_LEN,
  MAX_PARENT_ORG_TAG_LEN,
  MAX_PARENT_ROLE_LEN,
  MAX_PARENT_SIG_LEN,
  MAX_RECORD_ID_LEN,
  PATTERN_TAGS,
  RecordContext,
  buildParentLinkBody,
  canonicalHash as receiptCanonicalHash,
  sha512Hex,
  validateParentLinkFields,
  validatePatternTag,
  withRecordContext,
} from "../src/capsule_record.js";
import type {
  PatternTag,
  RecordOptions,
  ParentProofOptions,
} from "../src/capsule_record.js";
import { BadRequestError } from "../src/errors.js";
import { canonicalizeBytes } from "../src/_jcs.js";
import type {
  CdpDocument,
  ParentProofLink,
  RecordReceipt,
  VerificationCdp,
} from "../src/models.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

// ── Constants ──

const CAPSULE_RESPONSE = {
  id: "cap_test001",
  status: "active",
  capsulefile_id: "cf_abc",
  max_lifetime_seconds: 300,
  created_at: "2026-05-26T00:00:00Z",
};

function recordResponse(
  recordIndex: number,
  recordId = "pa_123",
  capsuleId = "cap_test001",
): Record<string, unknown> {
  return {
    capsule_id: capsuleId,
    record_index: recordIndex,
    record_id: recordId,
    record_chain_hash: `sha512:${"b".repeat(128)}`,
    buffered_count: recordIndex + 1,
    disclaimer:
      "The receipt is signed evidence. Control IDs are derived at adapter " +
      "time via the ADR-040 published mapping artifact; whether the " +
      "evidence is acceptable for a control is determined by the customer's " +
      "compliance program and the auditor's adjudication, not by Nanorix.",
  };
}

function parentResponse(
  capsuleId = "cap_test001",
  parentChainHash = `sha512:${"d".repeat(128)}`,
): Record<string, unknown> {
  return {
    capsule_id: capsuleId,
    parent_chain_hash: parentChainHash,
    buffered_count: 1,
    disclaimer:
      "parent_proof_hashes attest to lineage, not to compliance. Each " +
      "link attests to its own boundary; the auditor adjudicates substantive " +
      "acceptability.",
  };
}

/** Build a fake post callback that records every body it's handed. */
function makeFakePost(): {
  fn: (capsuleId: string, body: Record<string, unknown>) => Promise<Record<string, unknown>>;
  calls: { capsuleId: string; body: Record<string, unknown> }[];
} {
  const calls: { capsuleId: string; body: Record<string, unknown> }[] = [];
  let counter = 0;
  const fn = async (
    capsuleId: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> => {
    calls.push({ capsuleId, body });
    return recordResponse(counter++, body.record_id as string);
  };
  return { fn, calls };
}

// ── 1. PatternTag closed-enum validation ──

describe("PatternTag closed-enum (ADR-039 'pattern_tag rule')", () => {
  it("PATTERN_TAGS has exactly 15 variants", () => {
    expect(PATTERN_TAGS).toHaveLength(15);
  });

  it("PATTERN_TAGS matches the Rust wire form set", () => {
    const expected = new Set([
      "pa",
      "extraction",
      "annotation",
      "agent_step",
      "agent_turn",
      "rcm_claim",
      "rcm_eligibility",
      "rcm_remit",
      "ncpdp_script",
      "dicom_study",
      "dicom_sr",
      "screening_hit",
      "fhir_record",
      "ehr_document",
      "custom",
    ]);
    expect(new Set(PATTERN_TAGS)).toEqual(expected);
  });

  it("validatePatternTag accepts every canonical variant", () => {
    for (const tag of PATTERN_TAGS) {
      expect(validatePatternTag(tag)).toBe(tag);
    }
  });

  it("validatePatternTag accepts undefined", () => {
    expect(validatePatternTag(undefined)).toBeUndefined();
  });

  it("validatePatternTag rejects an unknown variant", () => {
    expect(() => validatePatternTag("invalid_pattern")).toThrow(
      BadRequestError,
    );
    expect(() => validatePatternTag("invalid_pattern")).toThrow(
      /not a recognized variant/,
    );
  });

  it("validatePatternTag is case-sensitive (uppercase rejected)", () => {
    expect(() => validatePatternTag("PA")).toThrow(BadRequestError);
    expect(() => validatePatternTag("Pa")).toThrow(BadRequestError);
  });

  it("validatePatternTag rejects kebab-case (snake_case wire form only)", () => {
    expect(() => validatePatternTag("rcm-claim")).toThrow(BadRequestError);
    expect(() => validatePatternTag("dicom-study")).toThrow(BadRequestError);
  });

  it("PatternTag union widens automatically (compile-time check)", () => {
    // Type-level assertion: every PATTERN_TAGS element is assignable to PatternTag.
    const tag: PatternTag = "pa";
    expectTypeOf<PatternTag>().toEqualTypeOf<(typeof PATTERN_TAGS)[number]>();
    expect(tag).toBe("pa");
  });
});

// ── 2. Field length limits ──

describe("Field length limits (mirror services/api/src/routes/records.rs)", () => {
  it("RecordContext rejects oversize record_id (>256 chars)", () => {
    expect(() => {
      new RecordContext(
        "cap_test001",
        "x".repeat(MAX_RECORD_ID_LEN + 1),
        undefined,
        async () => ({}),
      );
    }).toThrow(/256/);
  });

  it("RecordContext rejects empty record_id", () => {
    expect(() => {
      new RecordContext("cap_test001", "", undefined, async () => ({}));
    }).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects oversize parent_chain_hash (>144 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(MAX_HASH_LEN)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(88)}`,
      }),
    ).toThrow(/144/);
  });

  it("validateParentLinkFields rejects oversize parent_key_id (>256 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "k".repeat(MAX_PARENT_KEY_ID_LEN + 1),
        parentSignature: `base64:${"b".repeat(88)}`,
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects oversize parent_signature (>256 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(MAX_PARENT_SIG_LEN)}`,
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects oversize parent_role (>128 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(88)}`,
        parentRole: "r".repeat(MAX_PARENT_ROLE_LEN + 1),
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects oversize parent_jurisdiction (>16 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(88)}`,
        parentJurisdiction: "j".repeat(MAX_PARENT_JURISDICTION_LEN + 1),
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects oversize parent_organization_tag (>256 chars)", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(88)}`,
        parentOrganizationTag: "o".repeat(MAX_PARENT_ORG_TAG_LEN + 1),
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects empty parent_chain_hash", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: "",
        parentKeyId: "cust-auth-banjo",
        parentSignature: `base64:${"b".repeat(88)}`,
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects empty parent_key_id", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "",
        parentSignature: `base64:${"b".repeat(88)}`,
      }),
    ).toThrow(BadRequestError);
  });

  it("validateParentLinkFields rejects empty parent_signature", () => {
    expect(() =>
      validateParentLinkFields({
        parentChainHash: `sha512:${"a".repeat(128)}`,
        parentKeyId: "cust-auth-banjo",
        parentSignature: "",
      }),
    ).toThrow(BadRequestError);
  });
});

// ── 3. Hash helpers (cross-impl byte-equivalence) ──

describe("sha512Hex + canonicalHash (cross-impl byte-equivalence)", () => {
  it("sha512Hex returns sha512:{128 hex} prefix form", async () => {
    const out = await sha512Hex("hello");
    expect(out.startsWith("sha512:")).toBe(true);
    expect(out.length).toBe(7 + 128);
    expect(out.length).toBeLessThanOrEqual(MAX_HASH_LEN);
  });

  it("sha512Hex of 'hello' matches Python reference vector", async () => {
    // Python: hashlib.sha512(b"hello").hexdigest() — same canonical bytes,
    // same hash, both SDKs produce byte-identical "sha512:...".
    const expected =
      "sha512:9b71d224bd62f3785d96d46ad3ea3d73319bfbc2890caadae2dff72519673ca72323c3d99ba5c11d7c7acc6e14b8c5da0c4663475c2e5c3adef46f73bcdec043";
    expect(await sha512Hex("hello")).toBe(expected);
  });

  it("sha512Hex of Uint8Array matches Python reference vector", async () => {
    // Python: hashlib.sha512(b"hello").hexdigest()
    const expected =
      "sha512:9b71d224bd62f3785d96d46ad3ea3d73319bfbc2890caadae2dff72519673ca72323c3d99ba5c11d7c7acc6e14b8c5da0c4663475c2e5c3adef46f73bcdec043";
    const bytes = new TextEncoder().encode("hello");
    expect(await sha512Hex(bytes)).toBe(expected);
  });

  it("sha512Hex of empty string matches Python reference vector", async () => {
    // Python: hashlib.sha512(b"").hexdigest() — genesis hash.
    const expected =
      "sha512:cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e";
    expect(await sha512Hex("")).toBe(expected);
  });

  it("canonicalHash dispatches through JCS — keys sorted", async () => {
    const data = { b: 2, a: 1 };
    // JCS sorts keys lexically -> {"a":1,"b":2}.
    const canonicalBytes = canonicalizeBytes(data);
    expect(new TextDecoder().decode(canonicalBytes)).toBe('{"a":1,"b":2}');
    const out = await receiptCanonicalHash(data);
    expect(out).toBe(await sha512Hex(new TextDecoder().decode(canonicalBytes)));
  });

  it("canonicalHash is deterministic across input key order", async () => {
    const out1 = await receiptCanonicalHash({ x: 1, y: 2, z: 3 });
    const out2 = await receiptCanonicalHash({ z: 3, y: 2, x: 1 });
    expect(out1).toBe(out2);
  });

  it("canonicalHash of {a:1,b:2} matches Python reference vector", async () => {
    // Anchor vector: Python canonical_hash({"a":1, "b":2}) ==
    //   sha512:{hex of SHA-512 of b'{"a":1,"b":2}'}
    const py = await sha512Hex('{"a":1,"b":2}');
    expect(await receiptCanonicalHash({ b: 2, a: 1 })).toBe(py);
    expect(await receiptCanonicalHash({ a: 1, b: 2 })).toBe(py);
  });

  it("canonicalHash supports nested objects + arrays", async () => {
    const out = await receiptCanonicalHash({
      activity_trail: [
        { event: "x", at: "2026-05-26T00:00:00Z" },
        { event: "y", at: "2026-05-26T00:00:01Z" },
      ],
      record_id: "pa_123",
    });
    expect(out.startsWith("sha512:")).toBe(true);
  });
});

// ── 4. RecordContext lifecycle ──

describe("RecordContext lifecycle", () => {
  it("commit() POSTs the buffered receipt", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await ctx.bindOutput({ decision: "approved" });
    await ctx.commit();

    expect(calls).toHaveLength(1);
    expect(calls[0].capsuleId).toBe("cap_test001");
    expect(calls[0].body.record_id).toBe("pa_001");
    expect(calls[0].body.pattern_tag).toBe("pa");
    expect(calls[0].body.record_output_hash as string).toMatch(/^sha512:/);
  });

  it("commit() exposes server-computed record_chain_hash and record_index", async () => {
    const ctx = new RecordContext("cap_test001", "claim_xyz", "rcm_claim", async (cap, body) => {
      return recordResponse(7, body.record_id as string);
    });
    await ctx.bindInput({ input: "x" });
    await ctx.bindOutput({ output: "y" });
    await ctx.commit();

    expect(ctx.recordChainHash).toMatch(/^sha512:/);
    expect(ctx.recordIndex).toBe(7);
    expect(ctx.committed).toBe(true);
  });

  it("commit() is idempotent — second call is a no-op", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_a", "pa", fn);
    await ctx.bindOutput({ x: 1 });
    await ctx.commit();
    await ctx.commit(); // no-op
    expect(calls).toHaveLength(1);
  });

  it("abort() prevents commit — POST is suppressed", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await ctx.bindOutput({ x: 1 });
    ctx.abort();
    await ctx.commit(); // no-op after abort
    expect(calls).toHaveLength(0);
    expect(ctx.aborted).toBe(true);
  });

  it("activity_trail buffers events and emits them on commit", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    ctx.event({ type: "ReasoningStepCompleted", step_id: "ddx" });
    ctx.event({ type: "ReasoningStepCompleted", step_id: "rx" });
    await ctx.bindOutput({ approved: true });
    await ctx.commit();

    const body = calls[0].body;
    expect(body.activity_trail).toBeDefined();
    const trail = body.activity_trail as Record<string, unknown>[];
    expect(trail).toHaveLength(2);
    expect(trail[0].step_id).toBe("ddx");
    expect(trail[1].step_id).toBe("rx");
  });

  it("omits pattern_tag from body when undefined", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", undefined, fn);
    await ctx.bindOutput(new TextEncoder().encode("raw bytes"));
    await ctx.commit();
    expect(calls[0].body.pattern_tag).toBeUndefined();
    expect(Object.keys(calls[0].body)).not.toContain("pattern_tag");
  });

  it("bindInput / bindOutput accept Uint8Array and hash bytes directly", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await ctx.bindInput(new TextEncoder().encode("raw input bytes"));
    await ctx.bindOutput(new TextEncoder().encode("raw output bytes"));
    await ctx.commit();

    expect(calls[0].body.record_input_hash).toBe(
      await sha512Hex(new TextEncoder().encode("raw input bytes")),
    );
    expect(calls[0].body.record_output_hash).toBe(
      await sha512Hex(new TextEncoder().encode("raw output bytes")),
    );
  });

  it("bindInput / bindOutput accept strings and hash UTF-8 bytes", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await ctx.bindOutput("hello world");
    await ctx.commit();
    expect(calls[0].body.record_output_hash).toBe(await sha512Hex("hello world"));
  });

  it("rejects bind after commit (re-entrancy guard)", async () => {
    const { fn } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await ctx.commit();
    await expect(ctx.bindOutput({ x: 1 })).rejects.toThrow(BadRequestError);
  });

  it("rejects bind after abort", async () => {
    const { fn } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    ctx.abort();
    await expect(ctx.bindOutput({ x: 1 })).rejects.toThrow(BadRequestError);
  });

  it("event() rejects non-object payloads", () => {
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", async () => ({}));
    expect(() => ctx.event(null as unknown as Record<string, unknown>)).toThrow(BadRequestError);
    expect(() => ctx.event([] as unknown as Record<string, unknown>)).toThrow(BadRequestError);
  });
});

// ── 5. withRecord callback sugar ──

describe("withRecordContext (callback sugar)", () => {
  it("auto-commits on resolve", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await withRecordContext(ctx, async (r) => {
      await r.bindOutput({ ok: true });
    });
    expect(calls).toHaveLength(1);
    expect(ctx.committed).toBe(true);
  });

  it("auto-aborts on throw and re-raises the caller error", async () => {
    const { fn, calls } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    await expect(
      withRecordContext(ctx, async (r) => {
        await r.bindOutput({ x: 1 });
        throw new Error("customer code blew up");
      }),
    ).rejects.toThrow("customer code blew up");
    expect(calls).toHaveLength(0);
    expect(ctx.aborted).toBe(true);
  });

  it("returns the value returned by the callback", async () => {
    const { fn } = makeFakePost();
    const ctx = new RecordContext("cap_test001", "pa_001", "pa", fn);
    const result = await withRecordContext(ctx, async (r) => {
      await r.bindOutput({ ok: true });
      return 42;
    });
    expect(result).toBe(42);
  });
});

// ── 6. End-to-end against mocked HTTP ──

describe("End-to-end via NanorixClient + FetchMock", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    client = new NanorixClient({ apiKey: TEST_API_KEY });
  });

  afterEach(() => {
    mock.restore();
  });

  it("capsule.record(...) + commit() hits POST /v1/capsules/:id/record", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: CAPSULE_RESPONSE });
    mock.on("POST", "/v1/capsules/cap_test001/record", {
      status: 201,
      body: recordResponse(0),
    });
    mock.install();

    const capsule = await client.capsules.create();
    const ctx = capsule.record({ recordId: "pa_123", patternTag: "pa" });
    await ctx.bindOutput({ decision: "approved" });
    await ctx.commit();

    // 2 calls: POST /capsules, POST /capsules/cap_test001/record.
    const recordCall = mock.calls.find((c) =>
      c.url.includes("/v1/capsules/cap_test001/record"),
    );
    expect(recordCall).toBeDefined();
    const body = recordCall!.body as Record<string, unknown>;
    expect(body.record_id).toBe("pa_123");
    expect(body.pattern_tag).toBe("pa");
    expect((body.record_output_hash as string).startsWith("sha512:")).toBe(true);
  });

  it("capsule.withRecord(...) auto-commits one POST per iteration", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: CAPSULE_RESPONSE });
    mock.on("POST", "/v1/capsules/cap_test001/record", {
      status: 201,
      body: recordResponse(0),
    });
    mock.install();

    const capsule = await client.capsules.create();
    for (let i = 0; i < 5; i++) {
      await capsule.withRecord(
        { recordId: `claim_${i}`, patternTag: "rcm_claim" },
        async (r) => {
          await r.bindOutput({ claim_id: i, amount: 100 + i });
        },
      );
    }

    const recordPosts = mock.calls.filter((c) =>
      c.url.includes("/v1/capsules/cap_test001/record"),
    );
    expect(recordPosts).toHaveLength(5);
  });

  it("capsule.addParentProof(...) hits POST /v1/capsules/:id/parent", async () => {
    const parentHash = `sha512:${"d".repeat(128)}`;
    mock.on("POST", "/v1/capsules", { status: 200, body: CAPSULE_RESPONSE });
    mock.on("POST", "/v1/capsules/cap_test001/parent", {
      status: 201,
      body: parentResponse("cap_test001", parentHash),
    });
    mock.install();

    const capsule = await client.capsules.create();
    const response = await capsule.addParentProof({
      parentChainHash: parentHash,
      parentKeyId: "cust-auth-banjo-prod",
      parentSignature: `base64:${"b".repeat(88)}`,
      parentRole: "input-data-source",
      parentOrganizationTag: "vendor:banjo-health",
      parentJurisdiction: "US",
    });

    const parentCall = mock.calls.find((c) =>
      c.url.includes("/v1/capsules/cap_test001/parent"),
    );
    expect(parentCall).toBeDefined();
    const body = parentCall!.body as Record<string, unknown>;
    expect(body.parent_chain_hash).toBe(parentHash);
    expect(body.parent_key_id).toBe("cust-auth-banjo-prod");
    expect(body.parent_role).toBe("input-data-source");
    expect(body.parent_organization_tag).toBe("vendor:banjo-health");
    expect(response.buffered_count).toBe(1);
  });

  it("addParentProof omits optional fields when undefined", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: CAPSULE_RESPONSE });
    mock.on("POST", "/v1/capsules/cap_test001/parent", {
      status: 201,
      body: parentResponse(),
    });
    mock.install();

    const capsule = await client.capsules.create();
    await capsule.addParentProof({
      parentChainHash: `sha512:${"a".repeat(128)}`,
      parentKeyId: "cust-auth-x",
      parentSignature: `base64:${"b".repeat(88)}`,
    });

    const parentCall = mock.calls.find((c) =>
      c.url.includes("/v1/capsules/cap_test001/parent"),
    );
    const body = parentCall!.body as Record<string, unknown>;
    expect(Object.keys(body)).not.toContain("parent_role");
    expect(Object.keys(body)).not.toContain("parent_jurisdiction");
    expect(Object.keys(body)).not.toContain("parent_organization_tag");
  });
});

// ── 7. AuditProof response parsing ──

describe("AuditProof response parsing (record_receipts + parent_proof_hashes)", () => {
  it("CdpDocument carries record_receipts + record_receipts_merkle_root", () => {
    const raw: Record<string, unknown> = {
      capsule_id: "cap_test001",
      activity: [],
      record_receipts: [
        {
          record_index: 0,
          record_id: "pa_001",
          record_input_hash: `sha512:${"1".repeat(128)}`,
          record_output_hash: `sha512:${"2".repeat(128)}`,
          record_chain_hash: `sha512:${"3".repeat(128)}`,
          pattern_tag: "pa",
          merkle_inclusion_proof: [],
        },
        {
          record_index: 1,
          record_id: "pa_002",
          record_input_hash: `sha512:${"4".repeat(128)}`,
          record_output_hash: `sha512:${"5".repeat(128)}`,
          record_chain_hash: `sha512:${"6".repeat(128)}`,
          pattern_tag: "pa",
          merkle_inclusion_proof: ["7".repeat(128)],
        },
      ],
      record_receipts_merkle_root: `sha512:${"8".repeat(128)}`,
    };
    const doc = raw as CdpDocument;
    expect(doc.record_receipts).toBeDefined();
    expect(doc.record_receipts).toHaveLength(2);
    expect(doc.record_receipts![0].record_id).toBe("pa_001");
    expect(doc.record_receipts![0].pattern_tag).toBe("pa");
    expect(doc.record_receipts![1].merkle_inclusion_proof).toEqual([
      "7".repeat(128),
    ]);
    expect(doc.record_receipts_merkle_root).toBe(`sha512:${"8".repeat(128)}`);
  });

  it("CdpDocument carries parent_proof_hashes + parent_proofs_merkle_root", () => {
    const raw: Record<string, unknown> = {
      capsule_id: "cap_test001",
      activity: [],
      parent_proof_hashes: [
        {
          parent_chain_hash: `sha512:${"d".repeat(128)}`,
          parent_key_id: "cust-auth-banjo-prod",
          parent_signature: `base64:${"b".repeat(88)}`,
          parent_role: "input-data-source",
          parent_organization_tag: "vendor:banjo-health",
          parent_jurisdiction: "US",
        },
      ],
      parent_proofs_merkle_root: `sha512:${"e".repeat(128)}`,
    };
    const doc = raw as CdpDocument;
    expect(doc.parent_proof_hashes).toBeDefined();
    expect(doc.parent_proof_hashes).toHaveLength(1);
    const p = doc.parent_proof_hashes![0];
    expect(p.parent_key_id).toBe("cust-auth-banjo-prod");
    expect(p.parent_role).toBe("input-data-source");
    expect(doc.parent_proofs_merkle_root).toBe(`sha512:${"e".repeat(128)}`);
  });

  it("VerificationCdp carries the same wave-N fields", () => {
    const raw: Record<string, unknown> = {
      capsule_id: "cap_test001",
      record_receipts: [
        {
          record_index: 0,
          record_id: "claim_001",
          record_input_hash: `sha512:${"1".repeat(128)}`,
          record_output_hash: `sha512:${"2".repeat(128)}`,
          record_chain_hash: `sha512:${"3".repeat(128)}`,
          pattern_tag: "rcm_claim",
          merkle_inclusion_proof: [],
        },
      ],
      record_receipts_merkle_root: `sha512:${"8".repeat(128)}`,
    };
    const proof = raw as VerificationCdp;
    expect(proof.record_receipts).toBeDefined();
    expect(proof.record_receipts![0].pattern_tag).toBe("rcm_claim");
  });
});

// ── 8. Forever-Standard discipline (ADR-006 I0) ──

describe("Forever-Standard (ADR-006 I0)", () => {
  it("pre-Wave-N AuditProof (undefined receipts) round-trips through JSON unchanged", () => {
    const raw: CdpDocument = {
      capsule_id: "cap_pre_wave_n",
      activity: [],
      destruction_trigger: "user_explicit",
    };
    expect(raw.record_receipts).toBeUndefined();
    expect(raw.record_receipts_merkle_root).toBeUndefined();
    expect(raw.parent_proof_hashes).toBeUndefined();
    expect(raw.parent_proofs_merkle_root).toBeUndefined();

    const serialized = JSON.stringify(raw);
    expect(serialized).not.toContain("record_receipts");
    expect(serialized).not.toContain("record_receipts_merkle_root");
    expect(serialized).not.toContain("parent_proof_hashes");
    expect(serialized).not.toContain("parent_proofs_merkle_root");

    const reparsed = JSON.parse(serialized) as CdpDocument;
    expect(reparsed.record_receipts).toBeUndefined();
    expect(reparsed.parent_proof_hashes).toBeUndefined();
  });

  it("RecordReceipt interface has NO control_tags field (three-layer separation)", () => {
    // Sample receipt from a live AuditProof.
    const sample: RecordReceipt = {
      record_index: 0,
      record_id: "pa_001",
      record_input_hash: `sha512:${"1".repeat(128)}`,
      record_output_hash: `sha512:${"2".repeat(128)}`,
      record_chain_hash: `sha512:${"3".repeat(128)}`,
      pattern_tag: "pa",
      merkle_inclusion_proof: [],
    };
    // Compile-time check: writing `sample.control_tags = [...]` would have
    // been rejected if `control_tags` were a declared property.
    // Runtime check: scan the canonical receipt source for forbidden field
    // emission to catch future regressions that would re-introduce
    // control_tags as a field (architectural drift).
    expect("control_tags" in sample).toBe(false);

    // Also: the RecordReceipt interface declaration must not contain
    // a control_tags field. Scan the source.
    const here = fileURLToPath(import.meta.url);
    const modelsPath = resolvePath(here, "..", "..", "src", "models.ts");
    const modelsText = readFileSync(modelsPath, "utf-8");
    // The interface block from `interface RecordReceipt {` to the next `}`.
    const blockMatch = modelsText.match(/interface RecordReceipt\s*\{[\s\S]*?\n\}/);
    expect(blockMatch).not.toBeNull();
    const block = blockMatch![0];
    expect(block.includes("control_tags")).toBe(false);
  });

  it("ParentProofLink field set matches ADR-041", () => {
    const sample: ParentProofLink = {
      parent_chain_hash: `sha512:${"d".repeat(128)}`,
      parent_key_id: "cust-auth-x",
      parent_signature: `base64:${"b".repeat(88)}`,
    };
    // Compile-time: writing parent_role / parent_jurisdiction /
    // parent_organization_tag is allowed as optional. Writing any other
    // field would surface via the index signature only.
    sample.parent_role = "input-data-source";
    sample.parent_jurisdiction = "US";
    sample.parent_organization_tag = "vendor:banjo-health";
    expect(Object.keys(sample).sort()).toEqual([
      "parent_chain_hash",
      "parent_jurisdiction",
      "parent_key_id",
      "parent_organization_tag",
      "parent_role",
      "parent_signature",
    ]);
  });
});

// ── 9. Adapter-side resolver flow (three-layer demo) ──

describe("Adapter-side mapping resolution (ADR-040)", () => {
  it("end-to-end three-layer flow: receipt -> mapping.resolve -> control IDs", () => {
    // 1. Simulated AuditProof from Nanorix API.
    const rawProof: Record<string, unknown> = {
      capsule_id: "cap_anterior_pa",
      record_receipts: [
        {
          record_index: 0,
          record_id: "pa_a",
          record_input_hash: `sha512:${"1".repeat(128)}`,
          record_output_hash: `sha512:${"2".repeat(128)}`,
          record_chain_hash: `sha512:${"3".repeat(128)}`,
          pattern_tag: "pa",
          merkle_inclusion_proof: [],
        },
        {
          record_index: 1,
          record_id: "pa_b",
          record_input_hash: `sha512:${"4".repeat(128)}`,
          record_output_hash: `sha512:${"5".repeat(128)}`,
          record_chain_hash: `sha512:${"6".repeat(128)}`,
          pattern_tag: "pa",
          merkle_inclusion_proof: ["x".repeat(128)],
        },
      ],
    };
    const proof = rawProof as VerificationCdp;

    // 2 + 3 + 4. Adapter applies the mapping artifact.
    const mapping = loadMapping(CONTROL_MAP_FRAMEWORK_VERSION);
    const customerJurisdiction = "US";
    const customerClassification = "PHI";

    for (const receipt of proof.record_receipts ?? []) {
      const controlIds = mapping.resolve(
        customerJurisdiction,
        customerClassification,
        receipt.pattern_tag ?? "custom",
      );
      expect(controlIds).toContain("HIPAA-164.312(b)");
      expect(controlIds).toContain("URAC-UM-AC-1");
    }
  });

  it("convenience resolveControls helper returns canonical control IDs", () => {
    const ids = resolveControls("US", "PHI", "rcm_claim");
    expect(ids).toContain("HIPAA-164.312(b)");
    expect(ids).toContain("HITRUST-02.e");
    expect(ids).toContain("ACA-1557");
  });
});

// ── 10. Forbidden language discipline (CCO policy) ──

describe("Forbidden verdict language (CCO policy)", () => {
  it("capsule_record.ts source has no forbidden vocabulary", () => {
    const here = fileURLToPath(import.meta.url);
    const sourcePath = resolvePath(here, "..", "..", "src", "capsule_record.ts");
    const text = readFileSync(sourcePath, "utf-8");

    // Verdict-noun forms. Receipts attest to evidence; auditors adjudicate.
    for (const forbidden of ["COMPLIANT", "SATISFIED", "MEETS"]) {
      expect(text.includes(forbidden)).toBe(false);
    }
    // Verdict-phrase forms.
    const lower = text.toLowerCase();
    for (const phrase of ["satisfied", "is compliant", "meets the"]) {
      expect(lower.includes(phrase)).toBe(false);
    }
  });

  it("capsule_record.ts module docstring mentions three-layer separation + auditor adjudication", () => {
    const here = fileURLToPath(import.meta.url);
    const sourcePath = resolvePath(here, "..", "..", "src", "capsule_record.ts");
    const text = readFileSync(sourcePath, "utf-8");
    expect(text).toContain("Three-layer separation");
    expect(text).toMatch(/auditor/i);
    expect(text).toMatch(/adjudicat/i);
  });

  it("CONTROL_MAP_DISCLAIMER uses required vocabulary", () => {
    expect(CONTROL_MAP_DISCLAIMER).toContain("references");
    expect(CONTROL_MAP_DISCLAIMER).toMatch(/auditor/i);
    for (const forbidden of ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]) {
      expect(CONTROL_MAP_DISCLAIMER.includes(forbidden)).toBe(false);
    }
  });
});

// ── 11. Parent link body builder ──

describe("buildParentLinkBody", () => {
  it("omits optional fields when undefined", () => {
    const body = buildParentLinkBody({
      parentChainHash: `sha512:${"a".repeat(128)}`,
      parentKeyId: "cust-auth-x",
      parentSignature: `base64:${"b".repeat(88)}`,
    });
    expect(body).toEqual({
      parent_chain_hash: `sha512:${"a".repeat(128)}`,
      parent_key_id: "cust-auth-x",
      parent_signature: `base64:${"b".repeat(88)}`,
    });
  });

  it("includes optional fields when provided", () => {
    const body = buildParentLinkBody({
      parentChainHash: `sha512:${"a".repeat(128)}`,
      parentKeyId: "cust-auth-x",
      parentSignature: `base64:${"b".repeat(88)}`,
      parentRole: "rag-retrieval",
      parentJurisdiction: "EU",
      parentOrganizationTag: "vendor:llamaindex-cloud",
    });
    expect(body.parent_role).toBe("rag-retrieval");
    expect(body.parent_jurisdiction).toBe("EU");
    expect(body.parent_organization_tag).toBe("vendor:llamaindex-cloud");
  });

  it("snake_case wire form matches the Rust API", () => {
    const body = buildParentLinkBody({
      parentChainHash: "sha512:a",
      parentKeyId: "k",
      parentSignature: "base64:s",
    });
    // No camelCase keys leak into the wire form.
    for (const key of Object.keys(body)) {
      expect(key).not.toMatch(/[A-Z]/);
    }
  });
});

// ── 12. Type-level checks for RecordOptions + ParentProofOptions ──

describe("Public option-type shapes", () => {
  it("RecordOptions accepts only PatternTag for patternTag", () => {
    // Compile-time + runtime smoke: this object literal must type-check.
    const opts: RecordOptions = { recordId: "pa_a", patternTag: "pa" };
    expect(opts.recordId).toBe("pa_a");
    expect(opts.patternTag).toBe("pa");
  });

  it("ParentProofOptions only requires the three signed-fields", () => {
    const opts: ParentProofOptions = {
      parentChainHash: "sha512:abc",
      parentKeyId: "cust-auth-x",
      parentSignature: "base64:sig",
    };
    expect(opts.parentRole).toBeUndefined();
  });
});
