import { describe, it, expect } from "vitest";
import {
  raiseForStatus,
  NanorixError,
  AuthenticationError,
  NotFoundError,
  ConflictError,
  BadRequestError,
  GoneError,
  PayloadTooLargeError,
  QuotaExceededError,
  RateLimitError,
  ExecutionError,
} from "../src/errors.js";

describe("errors", () => {
  it("raiseForStatus maps all status codes", () => {
    const cases: Array<[number, new (...args: unknown[]) => NanorixError]> = [
      [400, BadRequestError],
      [401, AuthenticationError],
      [402, QuotaExceededError],
      [403, QuotaExceededError],
      [404, NotFoundError],
      [409, ConflictError],
      [410, GoneError],
      [413, PayloadTooLargeError],
      [429, RateLimitError],
    ];

    for (const [code, ExpectedClass] of cases) {
      try {
        raiseForStatus(code, { error: `test ${code}` });
        expect.unreachable(`Should have thrown for ${code}`);
      } catch (err) {
        expect(err).toBeInstanceOf(ExpectedClass);
      }
    }
  });

  it("error preserves body", () => {
    try {
      raiseForStatus(404, { error: "not found", details: "cap_123" });
      expect.unreachable();
    } catch (err) {
      const e = err as NotFoundError;
      expect(e.body?.details).toBe("cap_123");
      expect(e.statusCode).toBe(404);
    }
  });

  it("RateLimitError has retryAfter", () => {
    try {
      raiseForStatus(429, { error: "slow down", retry_after: 2.5 });
      expect.unreachable();
    } catch (err) {
      const e = err as RateLimitError;
      expect(e.retryAfter).toBe(2.5);
    }
  });

  it("ExecutionError has exitCode, stdout, stderr", () => {
    const err = new ExecutionError("cmd failed", {
      exitCode: 1,
      stdout: "out",
      stderr: "err",
    });
    expect(err.exitCode).toBe(1);
    expect(err.stdout).toBe("out");
    expect(err.stderr).toBe("err");
    expect(err.name).toBe("ExecutionError");
  });
});
