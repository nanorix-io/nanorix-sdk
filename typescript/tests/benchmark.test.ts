/**
 * Tests for the TypeScript SDK cold-start benchmark helper (Batch 1 Item 4).
 *
 * Covers:
 * - Canonical constants (BENCHMARK_VERSION, SCHEMA_VERSION, DISCLAIMER)
 * - parseArtifact() round-trip from raw JSON
 * - BenchmarkArtifact.getP95 lookup + None for unknown cells
 * - iterRuns filter facets
 * - productionWiredRuns filters scaffold substrates
 * - loadBenchmark() stub fallback without fetchUrl
 * - loadBenchmark() fetchUrl path with mocked fetch
 * - getP95() convenience wrapper
 * - Forbidden vocabulary scan on the canonical disclaimer
 */
import { describe, expect, it } from "vitest";

import {
  BENCHMARK_DISCLAIMER,
  BENCHMARK_SCHEMA_VERSION,
  BENCHMARK_VERSION,
  BenchmarkArtifact,
  getBenchmarkP95,
  loadBenchmark,
  parseBenchmarkArtifact,
} from "../src/index.js";

const SAMPLE_ARTIFACT = {
  benchmark_version: "2026-05",
  schema_version: "1.0",
  harness_version: "0.1.0",
  generated_at: "2026-05-27T20:00:00Z",
  measurements: [
    {
      substrate: "cloud-run-gen2",
      region: "us-central1",
      pattern: "pattern_1_single_decision",
      capsulefile_shape: "small",
      iterations: 1000,
      warmup_discarded: 100,
      cold_start_ms: { p50: 312, p95: 480, p99: 612, stddev: 87 },
      exec_ms: { p50: 24, p95: 41, p99: 58, stddev: 9 },
      destroy_ms: { p50: 47, p95: 89, p99: 134, stddev: 21 },
      total_ms: { p50: 383, p95: 555, p99: 681, stddev: 96 },
      audit_proof_emit_ms: { p50: 18, p95: 31, p99: 43, stddev: 7 },
      production_wired: true,
      captured_under: {
        git_sha: "abcdef",
        infra_rev: "rev-150",
        rust_version: "1.93",
        test_run_id: "test-uuid",
        harness_version: "0.1.0",
        captured_at: "2026-05-27T20:00:00Z",
      },
    },
    {
      substrate: "nitro",
      region: "us-east-1",
      pattern: "pattern_4_per_record_batch",
      capsulefile_shape: "medium",
      iterations: 500,
      warmup_discarded: 50,
      cold_start_ms: { p50: 612, p95: 780, p99: 850, stddev: 110 },
      exec_ms: { p50: 280, p95: 350, p99: 410, stddev: 35 },
      destroy_ms: { p50: 65, p95: 120, p99: 180, stddev: 30 },
      total_ms: { p50: 957, p95: 1250, p99: 1440, stddev: 155 },
      audit_proof_emit_ms: { p50: 28, p95: 45, p99: 65, stddev: 10 },
      production_wired: false,
      captured_under: {
        git_sha: "abcdef",
        infra_rev: "rev-150",
        rust_version: "1.93",
        test_run_id: "test-uuid-2",
        harness_version: "0.1.0",
        captured_at: "2026-05-27T20:00:00Z",
      },
    },
  ],
  methodology: "Each measurement run = warm-up + N iterations on a single substrate region. Warm-up iterations are discarded.",
  disclaimer: BENCHMARK_DISCLAIMER,
};

describe("benchmark constants", () => {
  it("BENCHMARK_VERSION is 2026-05 (matches harness + endpoint)", () => {
    expect(BENCHMARK_VERSION).toBe("2026-05");
  });

  it("SCHEMA_VERSION is 1.0", () => {
    expect(BENCHMARK_SCHEMA_VERSION).toBe("1.0");
  });

  it("DISCLAIMER carries required discipline language", () => {
    expect(BENCHMARK_DISCLAIMER).toContain("structural facts");
    expect(BENCHMARK_DISCLAIMER).toContain("customer");
  });

  it("DISCLAIMER does not contain forbidden verdict vocabulary", () => {
    for (const forbidden of ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]) {
      expect(BENCHMARK_DISCLAIMER).not.toContain(forbidden);
    }
  });
});

describe("parseArtifact + BenchmarkArtifact", () => {
  it("parses a sample artifact into typed shape", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    expect(artifact).toBeInstanceOf(BenchmarkArtifact);
    expect(artifact.benchmark_version).toBe("2026-05");
    expect(artifact.measurements.length).toBe(2);
  });

  it("getP95 returns p95 for a known cell", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const p95 = artifact.getP95({
      substrate: "cloud-run-gen2",
      pattern: "pattern_1_single_decision",
      shape: "small",
      dimension: "total_ms",
    });
    expect(p95).toBe(555);
  });

  it("getP95 defaults to total_ms dimension", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const p95 = artifact.getP95({
      substrate: "cloud-run-gen2",
      pattern: "pattern_1_single_decision",
      shape: "small",
    });
    expect(p95).toBe(555);
  });

  it("getP95 returns null for unknown cell", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const p95 = artifact.getP95({
      substrate: "cloud-run-gen2",
      pattern: "pattern_99_nonexistent",
      shape: "small",
    });
    expect(p95).toBeNull();
  });

  it("iterRuns filters by substrate facet", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const nitroRuns = Array.from(artifact.iterRuns({ substrate: "nitro" }));
    expect(nitroRuns.length).toBe(1);
    expect(nitroRuns[0].substrate).toBe("nitro");
  });

  it("iterRuns filters by pattern facet", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const p4Runs = Array.from(
      artifact.iterRuns({ pattern: "pattern_4_per_record_batch" }),
    );
    expect(p4Runs.length).toBe(1);
    expect(p4Runs[0].pattern).toBe("pattern_4_per_record_batch");
  });

  it("productionWiredRuns filters scaffold substrates", () => {
    const artifact = parseBenchmarkArtifact(SAMPLE_ARTIFACT);
    const wired = artifact.productionWiredRuns();
    for (const run of wired) {
      expect(run.production_wired).toBe(true);
    }
    // Nitro entry in sample must be filtered out
    for (const run of wired) {
      expect(["nitro", "aci", "k8s-onprem"]).not.toContain(run.substrate);
    }
  });
});

describe("loadBenchmark", () => {
  it("returns stub when no fetchUrl provided", async () => {
    const artifact = await loadBenchmark();
    expect(artifact.benchmark_version).toBe(BENCHMARK_VERSION);
    expect(artifact.schema_version).toBe(BENCHMARK_SCHEMA_VERSION);
    expect(artifact.disclaimer).toBe(BENCHMARK_DISCLAIMER);
    expect(artifact.measurements.length).toBe(0);
  });

  it("fetches from fetchUrl when provided", async () => {
    const fetchImpl = async (url: string) => {
      expect(url).toContain("/v1/cold-start-benchmark");
      expect(url).toContain("benchmark_version=2026-05");
      return {
        ok: true,
        status: 200,
        json: async () => SAMPLE_ARTIFACT,
      } as unknown as Response;
    };
    const artifact = await loadBenchmark({
      fetchUrl: "https://api.nanorix.io/v1/cold-start-benchmark",
      fetchImpl,
    });
    expect(artifact.measurements.length).toBe(2);
    expect(artifact.benchmark_version).toBe("2026-05");
  });

  it("throws on fetch failure", async () => {
    const fetchImpl = async () =>
      ({
        ok: false,
        status: 500,
        json: async () => ({}),
      }) as unknown as Response;
    await expect(
      loadBenchmark({
        fetchUrl: "https://api.nanorix.io/v1/cold-start-benchmark",
        fetchImpl,
      }),
    ).rejects.toThrow(/Failed to fetch benchmark/);
  });
});

describe("getBenchmarkP95 convenience", () => {
  it("returns null for stub artifact (no embedded data)", async () => {
    const p95 = await getBenchmarkP95({
      substrate: "cloud-run-gen2",
      pattern: "pattern_1_single_decision",
      shape: "small",
    });
    // Stub returns empty measurements; cell is "unknown" by definition
    expect(p95).toBeNull();
  });
});

describe("sample artifact fixture matches schema", () => {
  it("all required top-level fields present", () => {
    for (const required of [
      "benchmark_version",
      "schema_version",
      "harness_version",
      "generated_at",
      "measurements",
      "methodology",
      "disclaimer",
    ]) {
      expect(SAMPLE_ARTIFACT).toHaveProperty(required);
    }
  });

  it("all required measurement fields present", () => {
    const m = SAMPLE_ARTIFACT.measurements[0];
    for (const required of [
      "substrate",
      "region",
      "pattern",
      "capsulefile_shape",
      "iterations",
      "warmup_discarded",
      "cold_start_ms",
      "exec_ms",
      "destroy_ms",
      "total_ms",
      "audit_proof_emit_ms",
      "production_wired",
      "captured_under",
    ]) {
      expect(m).toHaveProperty(required);
    }
  });
});
