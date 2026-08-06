/**
 * P0 #4 / Pre-SDK-1.0-publish — 422 status code mapping tests (TS).
 *
 * Cross-SDK parity with the Python SDK: same
 * wire-shape inputs MUST produce same exception types in both SDKs.
 */
import { describe, it, expect } from "vitest";
import {
  BadRequestError,
  InputProvenanceRejectedError,
  NanorixError,
  RateLimitError,
  SecretsInjectionRejectedError,
  UnprocessableEntityError,
  raiseForStatus,
} from "../src/errors.js";

describe("422 status code mapping", () => {
  it("input_provenance_rejected throws typed subclass with input_hash", () => {
    const body = {
      error: {
        code: "input_provenance_rejected",
        message: "Manifest input_hash mismatch",
        reason: "hash_mismatch",
        input_hash: "sha512:abcd1234",
      },
    };
    try {
      raiseForStatus(422, body);
      expect.fail("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(InputProvenanceRejectedError);
      expect(e).toBeInstanceOf(UnprocessableEntityError);
      expect(e).toBeInstanceOf(NanorixError);
      const err = e as InputProvenanceRejectedError;
      expect(err.statusCode).toBe(422);
      expect(err.reason).toBe("hash_mismatch");
      expect(err.inputHash).toBe("sha512:abcd1234");
      expect(err.message).toContain("Manifest input_hash mismatch");
    }
  });

  it("secrets_injection_rejected throws typed subclass with secret_id", () => {
    const body = {
      error: {
        code: "secrets_injection_rejected",
        message: "Secret 's_db_pwd' could not be injected",
        reason: "secret_not_found",
        secret_id: "s_db_pwd",
      },
    };
    try {
      raiseForStatus(422, body);
      expect.fail("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(SecretsInjectionRejectedError);
      expect(e).toBeInstanceOf(UnprocessableEntityError);
      expect(e).toBeInstanceOf(NanorixError);
      const err = e as SecretsInjectionRejectedError;
      expect(err.statusCode).toBe(422);
      expect(err.reason).toBe("secret_not_found");
      expect(err.secretId).toBe("s_db_pwd");
    }
  });

  it("unknown error.code falls back to UnprocessableEntityError (Forever-Standard)", () => {
    const body = {
      error: {
        code: "future_unknown_422_code",
        message: "Some future 422",
        reason: "future_reason",
      },
    };
    try {
      raiseForStatus(422, body);
      expect.fail("should have thrown");
    } catch (e) {
      // Must be exactly UnprocessableEntityError (NOT a subclass)
      expect(e).toBeInstanceOf(UnprocessableEntityError);
      expect(e).not.toBeInstanceOf(InputProvenanceRejectedError);
      expect(e).not.toBeInstanceOf(SecretsInjectionRejectedError);
      const err = e as UnprocessableEntityError;
      expect(err.reason).toBe("future_reason");
    }
  });

  it("422 with no error.code still raises UnprocessableEntityError", () => {
    const body = { error: "Just a string" };
    try {
      raiseForStatus(422, body);
      expect.fail("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(UnprocessableEntityError);
      const err = e as UnprocessableEntityError;
      expect(err.statusCode).toBe(422);
      expect(err.reason).toBeUndefined();
    }
  });

  it("InputProvenanceRejectedError carries inputHash attribute", () => {
    const e = new InputProvenanceRejectedError("test", {
      reason: "hash_mismatch",
      inputHash: "sha512:xyz",
    });
    expect(e.inputHash).toBe("sha512:xyz");
    expect(e.reason).toBe("hash_mismatch");
  });

  it("SecretsInjectionRejectedError carries secretId attribute", () => {
    const e = new SecretsInjectionRejectedError("test", {
      reason: "decryption_failed",
      secretId: "s_api_key",
    });
    expect(e.secretId).toBe("s_api_key");
    expect(e.reason).toBe("decryption_failed");
  });
});

describe("Status map regression — 422 must not break other status codes", () => {
  it("400 still maps to BadRequestError", () => {
    expect(() => raiseForStatus(400, { error: "bad request" })).toThrow(
      BadRequestError,
    );
  });

  it("429 still maps to RateLimitError with retry_after", () => {
    try {
      raiseForStatus(429, { error: "rate limited", retry_after: 30 });
      expect.fail("should throw");
    } catch (e) {
      expect(e).toBeInstanceOf(RateLimitError);
      expect((e as RateLimitError).retryAfter).toBe(30);
    }
  });
});

describe("Customer self-resolution pattern (pre-pilot acceptance bar)", () => {
  it("dispatches on e.reason for self-resolution paths", () => {
    const body = {
      error: {
        code: "secrets_injection_rejected",
        message: "Secret missing",
        reason: "secret_not_found",
        secret_id: "s_stripe_key",
      },
    };
    const rotated: string[] = [];
    const rotateSecret = (sid: string) => rotated.push(sid);

    try {
      raiseForStatus(422, body);
    } catch (e) {
      if (e instanceof SecretsInjectionRejectedError) {
        if (e.reason === "secret_not_found") {
          rotateSecret(e.secretId!);
        } else if (e.reason === "decryption_failed") {
          expect.fail("should have hit secret_not_found branch");
        }
      } else {
        throw e;
      }
    }

    expect(rotated).toEqual(["s_stripe_key"]);
  });
});
