/**
 * Order 6 sub-C tests — TS customer-side per-event signing helpers.
 *
 * Mirrors Python tests/test_signer.py coverage. Web Crypto Ed25519
 * via globalThis.crypto.subtle (Node 18+ + modern browsers).
 */
import { describe, it, expect } from "vitest";
import {
  Ed25519GeneratedKey,
  type EventAttestation,
  attestationToWireDict,
  canonicalizeEvent,
  signActivityEvent,
  verifyEventAttestation,
} from "../src/helpers/signer.js";

async function ed25519Supported(): Promise<boolean> {
  try {
    const c = (globalThis as unknown as { crypto?: Crypto }).crypto;
    if (!c?.subtle) return false;
    await c.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
    return true;
  } catch {
    return false;
  }
}

// ── Canonical JSON ──

describe("canonicalizeEvent", () => {
  it("sorts keys deterministically", () => {
    const a = canonicalizeEvent({ b: 2, a: 1, c: 3 });
    const b = canonicalizeEvent({ c: 3, a: 1, b: 2 });
    expect(new TextDecoder().decode(a)).toBe(new TextDecoder().decode(b));
    const parsed = JSON.parse(new TextDecoder().decode(a));
    expect(Object.keys(parsed)).toEqual(["a", "b", "c"]);
  });

  it("contains no whitespace separators", () => {
    const out = new TextDecoder().decode(
      canonicalizeEvent({ event: "test", at: "2026-05-06" }),
    );
    expect(out).not.toContain(" ");
    expect(out).not.toContain(": ");
  });

  it("pinned cross-impl vector — Python + Rust + TS produce identical hash", async () => {
    // Cross-impl invariant pin: this TS canonicalizeEvent + SHA-512 must
    // produce IDENTICAL hash to:
    // - Python SDK nanorix.helpers.signer.canonicalize_event + SHA-512
    // - Rust services/api/src/per_event_signing.rs::compute_event_message_hash
    //
    // If this hash drifts, ANY of the three implementations diverged from
    // RFC 8785 JCS — verifier replay would break cross-language. Pin all
    // three against this vector.
    const event = {
      event: "execution_completed",
      exit_code: 0,
      duration_seconds: 5,
      at: "2026-05-06T01:00:00Z",
    };
    const canonical = canonicalizeEvent(event);
    const subtle = (globalThis as unknown as { crypto: Crypto }).crypto.subtle;
    const digest = await subtle.digest("SHA-512", canonical.buffer as ArrayBuffer);
    const bytes = new Uint8Array(digest);
    let hex = "";
    for (let i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, "0");
    }
    const expected =
      "b68d4893d287625a8545e3896f90715dea06ae156e139f118009c9c7af979579" +
      "bb547bf02aac94068152a69b752f387fe8b6179eed9a8782f3866cf76dadb472";
    expect(hex).toBe(expected);
  });
});

// ── Ed25519GeneratedKey + signActivityEvent + verify ──

describe.skipIf(!(await ed25519Supported()))("Signer (Web Crypto Ed25519)", () => {
  it("Ed25519GeneratedKey returns 32-byte public + 64-byte signature", async () => {
    const key = await Ed25519GeneratedKey.generate();
    const pub = await key.publicKeyBytes();
    expect(pub.length).toBe(32);
    const sig = await key.sign(new TextEncoder().encode("hello"));
    expect(sig.length).toBe(64);
    expect(key.algorithmName()).toBe("Ed25519");
  });

  it("signActivityEvent returns wire-format attestation", async () => {
    const key = await Ed25519GeneratedKey.generate();
    const event = {
      event: "execution_completed",
      exit_code: 0,
      duration_seconds: 5,
      at: "2026-05-06T01:00:00Z",
    };
    const att = await signActivityEvent(key, event, { eventId: "evt-001" });
    expect(att.algorithm).toBe("Ed25519");
    expect(att.publicKey.startsWith("base64:")).toBe(true);
    expect(att.signature.startsWith("base64:")).toBe(true);
    expect(att.eventId).toBe("evt-001");
    expect(att.signedAt.endsWith("Z")).toBe(true);
  });

  it("attestationToWireDict matches snake_case wire shape", () => {
    const att: EventAttestation = {
      algorithm: "Ed25519",
      publicKey: "base64:AA==",
      signature: "base64:BB==",
      eventId: "e1",
      signedAt: "2026-05-06T01:00:00Z",
    };
    const wire = attestationToWireDict(att);
    expect(Object.keys(wire).sort()).toEqual([
      "algorithm",
      "event_id",
      "public_key",
      "signature",
      "signed_at",
    ]);
  });

  it("verifyEventAttestation succeeds with correct signature", async () => {
    const key = await Ed25519GeneratedKey.generate();
    const event = { event: "test", at: "2026-05-06T01:00:00Z" };
    const att = await signActivityEvent(key, event, { eventId: "e1" });
    expect(await verifyEventAttestation(att, event)).toBe(true);
  });

  it("verifyEventAttestation fails when event tampered", async () => {
    const key = await Ed25519GeneratedKey.generate();
    const event = { event: "test", at: "2026-05-06T01:00:00Z" };
    const att = await signActivityEvent(key, event, { eventId: "e1" });
    const tampered = { event: "DIFFERENT", at: "2026-05-06T01:00:00Z" };
    expect(await verifyEventAttestation(att, tampered)).toBe(false);
  });

  it("verifyEventAttestation rejects unsupported algorithm", async () => {
    const att: EventAttestation = {
      algorithm: "RSA-PSS-SHA256",
      publicKey: "base64:AA==",
      signature: "base64:AA==",
      eventId: "e1",
      signedAt: "2026-05-06T01:00:00Z",
    };
    expect(await verifyEventAttestation(att, { event: "test" })).toBe(false);
  });
});
