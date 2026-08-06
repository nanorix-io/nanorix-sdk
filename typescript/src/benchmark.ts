/**
 * Cold-start benchmark SDK helper (Batch 1 Item 4, 2026-05-27).
 *
 * Layer-2 published artifact `(substrate × pattern × Capsulefile shape) → percentiles`.
 * Sibling to control_map.ts (mapping artifact). The customer
 * loads the latest published benchmark to inform their pilot
 * evaluation — NOT as a service-level commitment.
 *
 * ## Discipline anchors
 *
 * - `feedback_narrow_signed_claim_auditor_certifies.md`: measurements
 *   are STRUCTURAL FACTS (measured percentiles under the stated
 *   infrastructure revision). The SDK helper NEVER asserts "meets
 *   SLO X" — the customer performs their own pilot evaluation under
 *   their representative load.
 * - `feedback_signed_primitive_vs_derived_value.md`: benchmark output
 *   is layer-2 published artifact; NOT bound into AuditProof.
 *
 * ## Usage
 *
 * ```typescript
 * import { loadBenchmark, getP95 } from "@nanorix/sdk";
 *
 * const bench = await loadBenchmark();
 * const p95 = bench.getP95({
 *   substrate: "cloud-run-gen2",
 *   pattern: "pattern_1_single_decision",
 *   shape: "small",
 *   dimension: "total_ms",
 * });
 * console.log(`Pattern 1 total p95 (small, Cloud Run gen2): ${p95}ms`);
 * ```
 */

/** Canonical benchmark version stamp. Mirrors the Rust harness +
 * `/v1/cold-start-benchmark` endpoint. Append-only. */
export const BENCHMARK_VERSION = "2026-05" as const;

/** JSON Schema version for the published artifact format. */
export const SCHEMA_VERSION = "1.0" as const;

/** Canonical disclaimer mirroring the Rust harness + endpoint. */
export const DISCLAIMER =
  "Cold-start measurements are structural facts captured under the stated " +
  "infrastructure revision. Customer SLA expectations are determined by the " +
  "customer's own pilot evaluation; Nanorix publishes these measurements as " +
  "reference, not as service-level commitment. Whether these latencies are " +
  "acceptable for the customer's use case is a customer judgment.";

export interface Percentiles {
  readonly p50: number;
  readonly p95: number;
  readonly p99: number;
  readonly stddev: number;
}

export interface CaptureContext {
  readonly git_sha: string;
  readonly infra_rev: string;
  readonly rust_version: string;
  readonly test_run_id: string;
  readonly harness_version: string;
  readonly captured_at: string;
}

export interface MeasurementRun {
  readonly substrate: string;
  readonly region: string;
  readonly pattern: string;
  readonly capsulefile_shape: string;
  readonly iterations: number;
  readonly warmup_discarded: number;
  readonly cold_start_ms: Percentiles;
  readonly exec_ms: Percentiles;
  readonly destroy_ms: Percentiles;
  readonly total_ms: Percentiles;
  readonly audit_proof_emit_ms: Percentiles;
  readonly warm_pool_dispatch_ms_p99?: number;
  readonly production_wired: boolean;
  readonly captured_under: CaptureContext;
}

export type Dimension =
  | "cold_start_ms"
  | "exec_ms"
  | "destroy_ms"
  | "total_ms"
  | "audit_proof_emit_ms";

export interface BenchmarkFilter {
  substrate?: string;
  pattern?: string;
  shape?: string;
  production_wired_only?: boolean;
}

export interface BenchmarkLookup {
  substrate: string;
  pattern: string;
  shape: string;
  dimension?: Dimension;
}

/** Typed wrapper around a published benchmark artifact. */
export class BenchmarkArtifact {
  constructor(
    public readonly benchmark_version: string,
    public readonly schema_version: string,
    public readonly harness_version: string,
    public readonly generated_at: string,
    public readonly measurements: ReadonlyArray<MeasurementRun>,
    public readonly methodology: string,
    public readonly disclaimer: string,
  ) {}

  /** Return p95 for a specific cell + dimension. Returns null when the
   * cell is not present in the artifact. */
  getP95(lookup: BenchmarkLookup): number | null {
    const dimension = lookup.dimension ?? "total_ms";
    for (const run of this.iterRuns({
      substrate: lookup.substrate,
      pattern: lookup.pattern,
      shape: lookup.shape,
    })) {
      const p = run[dimension];
      if (!p) return null;
      return p.p95;
    }
    return null;
  }

  /** Iterate measurement runs filtered by optional facets. */
  *iterRuns(filter: BenchmarkFilter = {}): IterableIterator<MeasurementRun> {
    for (const run of this.measurements) {
      if (filter.substrate !== undefined && run.substrate !== filter.substrate)
        continue;
      if (filter.pattern !== undefined && run.pattern !== filter.pattern)
        continue;
      if (filter.shape !== undefined && run.capsulefile_shape !== filter.shape)
        continue;
      if (filter.production_wired_only && !run.production_wired) continue;
      yield run;
    }
  }

  /** Return only measurement runs from production-wired substrates.
   * Scaffold-only substrates (Nitro / ACI / K8s-onprem in V1) report
   * production_wired=false. */
  productionWiredRuns(): MeasurementRun[] {
    return Array.from(this.iterRuns({ production_wired_only: true }));
  }
}

interface RawArtifactJson {
  benchmark_version: string;
  schema_version: string;
  harness_version: string;
  generated_at: string;
  measurements: MeasurementRun[];
  methodology: string;
  disclaimer: string;
}

export function parseArtifact(doc: RawArtifactJson): BenchmarkArtifact {
  return new BenchmarkArtifact(
    doc.benchmark_version,
    doc.schema_version,
    doc.harness_version,
    doc.generated_at,
    doc.measurements,
    doc.methodology,
    doc.disclaimer,
  );
}

/** Load the published benchmark artifact.
 *
 * By default returns a stub (no embedded JSON for the TS SDK — fetch
 * from the API endpoint). To fetch from a live Nanorix deployment,
 * pass fetchUrl=https://api.nanorix.io/v1/cold-start-benchmark.
 *
 * Forever-Standard: old benchmark versions remain
 * available forever. The customer can pass benchmark_version to pin
 * to a specific version.
 */
export async function loadBenchmark(opts: {
  benchmark_version?: string;
  fetchUrl?: string;
  fetchImpl?: typeof fetch;
} = {}): Promise<BenchmarkArtifact> {
  const benchmarkVersion = opts.benchmark_version ?? BENCHMARK_VERSION;
  const fetchImpl = opts.fetchImpl ?? (typeof fetch !== "undefined" ? fetch : undefined);

  if (opts.fetchUrl !== undefined && fetchImpl) {
    const url = `${opts.fetchUrl}?benchmark_version=${encodeURIComponent(benchmarkVersion)}`;
    const resp = await fetchImpl(url);
    if (!resp.ok) {
      throw new Error(
        `Failed to fetch benchmark from ${url}: HTTP ${resp.status}`,
      );
    }
    const data = (await resp.json()) as RawArtifactJson;
    return parseArtifact(data);
  }

  // No fetch URL: return a minimal stub with the canonical metadata so
  // callers can still introspect benchmark_version + schema_version +
  // disclaimer without an exception. Customers wanting live data set
  // fetchUrl explicitly.
  return new BenchmarkArtifact(
    benchmarkVersion,
    SCHEMA_VERSION,
    "0.1.0",
    "",
    [],
    "No embedded benchmark data in TS SDK build. Set fetchUrl to retrieve from a live Nanorix deployment (e.g. https://api.nanorix.io/v1/cold-start-benchmark).",
    DISCLAIMER,
  );
}

/** Convenience: load the benchmark and return p95 for the cell.
 * Returns null when the cell is not present. */
export async function getP95(
  lookup: BenchmarkLookup & { benchmark_version?: string; fetchUrl?: string },
): Promise<number | null> {
  const bench = await loadBenchmark({
    benchmark_version: lookup.benchmark_version,
    fetchUrl: lookup.fetchUrl,
  });
  return bench.getP95(lookup);
}
