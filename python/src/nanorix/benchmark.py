"""Cold-start benchmark SDK helper.

Layer-2 published artifact `(substrate × pattern × Capsulefile shape) → percentiles`.
Sibling to `nanorix.control_map` (mapping artifact). The customer
loads the latest published benchmark to inform their pilot evaluation —
NOT as a service-level commitment.

## Discipline anchors

- Measurements are
  STRUCTURAL FACTS (measured percentiles under the stated infrastructure
  revision). The SDK helper NEVER asserts "meets SLO X" — the customer
  performs their own pilot evaluation under their representative load.
- Benchmark output is
  layer-2 published artifact; NOT bound into AuditProof.

## Usage

```python
from nanorix.benchmark import load_benchmark, get_p95

# Load the embedded benchmark for the current version.
bench = load_benchmark()
assert bench.benchmark_version == "2026-05"

# Look up p95 for a specific (substrate × pattern × shape) cell.
p95 = bench.get_p95(
    substrate="cloud-run-gen2",
    pattern="pattern_1_single_decision",
    shape="small",
    dimension="total_ms",
)
print(f"Pattern 1 total p95 (small, Cloud Run gen2): {p95}ms")

# Inspect all cells matching a pattern.
for run in bench.iter_runs(pattern="pattern_4_per_record_batch"):
    print(f"{run.substrate}/{run.capsulefile_shape}: cold p95={run.cold_start_ms.p95}ms")
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, cast

# Canonical benchmark version stamp. Mirrors the Rust harness +
# /v1/cold-start-benchmark endpoint. Append-only
# Forever-Standard.
BENCHMARK_VERSION: str = "2026-05"

# JSON Schema version of the published artifact format.
SCHEMA_VERSION: str = "1.0"

# Canonical disclaimer. Mirrors the Rust harness DISCLAIMER constant +
# the endpoint disclaimer. The SDK helper carries this through unchanged.
DISCLAIMER: str = (
    "Cold-start measurements are structural facts captured under the stated "
    "infrastructure revision. Customer SLA expectations are determined by the "
    "customer's own pilot evaluation; Nanorix publishes these measurements as "
    "reference, not as service-level commitment. Whether these latencies are "
    "acceptable for the customer's use case is a customer judgment."
)


@dataclass(frozen=True)
class Percentiles:
    """P50/p95/p99 + std-deviation for a single dimension."""

    p50: int
    p95: int
    p99: int
    stddev: int


@dataclass(frozen=True)
class MeasurementRun:
    """Single (substrate × pattern × Capsulefile shape) measurement cell."""

    substrate: str
    region: str
    pattern: str
    capsulefile_shape: str
    iterations: int
    warmup_discarded: int
    cold_start_ms: Percentiles
    exec_ms: Percentiles
    destroy_ms: Percentiles
    total_ms: Percentiles
    audit_proof_emit_ms: Percentiles
    warm_pool_dispatch_ms_p99: Optional[int]
    production_wired: bool
    captured_under: Dict[str, Any]


@dataclass(frozen=True)
class BenchmarkArtifact:
    """Top-level published benchmark artifact."""

    benchmark_version: str
    schema_version: str
    harness_version: str
    generated_at: str
    measurements: List[MeasurementRun]
    methodology: str
    disclaimer: str

    def get_p95(
        self,
        *,
        substrate: str,
        pattern: str,
        shape: str,
        dimension: str = "total_ms",
    ) -> Optional[int]:
        """Return p95 for a specific cell + dimension.

        Returns None when the cell is not present in the artifact
        (e.g. a substrate that wasn't included in the sweep).

        Valid dimensions: cold_start_ms, exec_ms, destroy_ms, total_ms,
        audit_proof_emit_ms.
        """
        for run in self.iter_runs(
            substrate=substrate, pattern=pattern, shape=shape
        ):
            p = getattr(run, dimension, None)
            if p is None:
                return None
            return cast(Optional[int], p.p95)
        return None

    def iter_runs(
        self,
        *,
        substrate: Optional[str] = None,
        pattern: Optional[str] = None,
        shape: Optional[str] = None,
        production_wired_only: bool = False,
    ) -> Iterator[MeasurementRun]:
        """Iterate measurement runs filtered by optional facets."""
        for run in self.measurements:
            if substrate is not None and run.substrate != substrate:
                continue
            if pattern is not None and run.pattern != pattern:
                continue
            if shape is not None and run.capsulefile_shape != shape:
                continue
            if production_wired_only and not run.production_wired:
                continue
            yield run

    def production_wired_runs(self) -> List[MeasurementRun]:
        """Return only measurement runs from production-wired substrates.

        Scaffold-only substrates (Nitro / ACI / K8s-onprem in V1) report
        production_wired=False; this helper filters those out so customers
        building their SLA evaluation only consume the real-measurement
        cells.
        """
        return list(self.iter_runs(production_wired_only=True))


def _percentiles_from_dict(d: Dict[str, Any]) -> Percentiles:
    return Percentiles(
        p50=int(d["p50"]),
        p95=int(d["p95"]),
        p99=int(d["p99"]),
        stddev=int(d["stddev"]),
    )


def parse_artifact(doc: Dict[str, Any]) -> BenchmarkArtifact:
    """Parse a benchmark artifact dict into the typed shape."""
    measurements = []
    for m in doc.get("measurements", []):
        measurements.append(
            MeasurementRun(
                substrate=m["substrate"],
                region=m["region"],
                pattern=m["pattern"],
                capsulefile_shape=m["capsulefile_shape"],
                iterations=int(m["iterations"]),
                warmup_discarded=int(m["warmup_discarded"]),
                cold_start_ms=_percentiles_from_dict(m["cold_start_ms"]),
                exec_ms=_percentiles_from_dict(m["exec_ms"]),
                destroy_ms=_percentiles_from_dict(m["destroy_ms"]),
                total_ms=_percentiles_from_dict(m["total_ms"]),
                audit_proof_emit_ms=_percentiles_from_dict(m["audit_proof_emit_ms"]),
                warm_pool_dispatch_ms_p99=m.get("warm_pool_dispatch_ms_p99"),
                production_wired=bool(m["production_wired"]),
                captured_under=dict(m["captured_under"]),
            )
        )
    return BenchmarkArtifact(
        benchmark_version=doc["benchmark_version"],
        schema_version=doc["schema_version"],
        harness_version=doc["harness_version"],
        generated_at=doc["generated_at"],
        measurements=measurements,
        methodology=doc["methodology"],
        disclaimer=doc["disclaimer"],
    )


def load_benchmark(
    *,
    benchmark_version: str = BENCHMARK_VERSION,
    fetch_url: Optional[str] = None,
) -> BenchmarkArtifact:
    """Load the published benchmark artifact for the given version.

    By default loads the embedded canonical artifact. To fetch from a
    live Nanorix deployment, pass `fetch_url=https://api.nanorix.io/v1/cold-start-benchmark`.

    Forever-Standard: old benchmark versions remain
    available forever. The embedded artifact ships with the current
    version; older versions are fetched from the API endpoint.
    """
    if fetch_url is not None:
        import urllib.parse
        import urllib.request
        import json

        params = urllib.parse.urlencode({"benchmark_version": benchmark_version})
        url = f"{fetch_url}?{params}"
        with urllib.request.urlopen(url) as resp:  # noqa: S310 — customer URL
            data = json.load(resp)
        return parse_artifact(data)

    # Embedded path: load from the JSON file shipped alongside the SDK.
    # The SDK build is responsible for ensuring the embedded JSON is
    # synchronized with the canonical artifact at publish time.
    import json
    import importlib.resources

    try:
        files = importlib.resources.files("nanorix")
        path = files / "benchmark_data" / f"{benchmark_version}.json"
        with path.open("r") as f:
            data = json.load(f)
    except (FileNotFoundError, AttributeError):
        # Embedded data not present (e.g. test environment). Return a
        # minimal stub with the canonical metadata so callers can still
        # introspect benchmark_version + schema_version + disclaimer
        # without exception.
        return BenchmarkArtifact(
            benchmark_version=benchmark_version,
            schema_version=SCHEMA_VERSION,
            harness_version="0.1.0",
            generated_at="",
            measurements=[],
            methodology=(
                "Embedded benchmark data not bundled with this SDK build. "
                "Set fetch_url to retrieve from a live Nanorix deployment."
            ),
            disclaimer=DISCLAIMER,
        )

    return parse_artifact(data)


def get_p95(
    substrate: str,
    pattern: str,
    shape: str,
    *,
    dimension: str = "total_ms",
    benchmark_version: str = BENCHMARK_VERSION,
) -> Optional[int]:
    """Convenience: load the benchmark and return p95 for the cell.

    Returns None when the cell is not present in the artifact.
    """
    artifact = load_benchmark(benchmark_version=benchmark_version)
    return artifact.get_p95(
        substrate=substrate, pattern=pattern, shape=shape, dimension=dimension
    )
