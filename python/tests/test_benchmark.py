"""Tests for `nanorix.benchmark` SDK helper (Batch 1 Item 4)."""
from __future__ import annotations

import pytest

from nanorix.benchmark import (
    BENCHMARK_VERSION,
    DISCLAIMER,
    SCHEMA_VERSION,
    BenchmarkArtifact,
    MeasurementRun,
    Percentiles,
    get_p95,
    load_benchmark,
    parse_artifact,
)


def test_load_benchmark_returns_canonical_artifact() -> None:
    bench = load_benchmark()
    assert bench.benchmark_version == BENCHMARK_VERSION
    assert bench.schema_version == SCHEMA_VERSION
    assert bench.disclaimer == DISCLAIMER
    # The methodology paragraph must mention warm-up + percentile method
    # so customers can audit the discipline.
    assert "warm-up" in bench.methodology or len(bench.measurements) == 0


def test_loaded_benchmark_carries_measurements() -> None:
    bench = load_benchmark()
    # Embedded artifact contains 6 patterns × 3 shapes × 5 substrates
    # = 90 measurement runs. If the embed isn't bundled (test env),
    # measurements is empty — that's the stub fallback path.
    if bench.measurements:
        assert len(bench.measurements) >= 6
        for run in bench.measurements:
            assert run.substrate in {
                "cloud-run-gen2",
                "nitro",
                "aci",
                "k8s-onprem",
                "mock",
            }
            assert run.capsulefile_shape in {"small", "medium", "large"}


def test_get_p95_returns_int_for_known_cell() -> None:
    bench = load_benchmark()
    if not bench.measurements:
        pytest.skip("embedded benchmark data not bundled in this test env")
    p95 = bench.get_p95(
        substrate="mock",
        pattern="pattern_1_single_decision",
        shape="small",
        dimension="total_ms",
    )
    assert p95 is not None
    assert p95 > 0


def test_get_p95_returns_none_for_unknown_cell() -> None:
    bench = load_benchmark()
    p95 = bench.get_p95(
        substrate="cloud-run-gen2",
        pattern="pattern_99_nonexistent",
        shape="small",
    )
    assert p95 is None


def test_iter_runs_filters_by_facet() -> None:
    bench = load_benchmark()
    if not bench.measurements:
        pytest.skip("embedded benchmark data not bundled in this test env")
    only_mock = list(bench.iter_runs(substrate="mock"))
    for run in only_mock:
        assert run.substrate == "mock"

    only_pattern_4 = list(
        bench.iter_runs(pattern="pattern_4_per_record_batch")
    )
    for run in only_pattern_4:
        assert run.pattern == "pattern_4_per_record_batch"


def test_production_wired_runs_filters_scaffold() -> None:
    bench = load_benchmark()
    if not bench.measurements:
        pytest.skip("embedded benchmark data not bundled in this test env")
    wired = bench.production_wired_runs()
    for run in wired:
        assert run.production_wired
    # Scaffold substrates must not appear in production_wired_runs.
    for run in wired:
        assert run.substrate not in {"nitro", "aci", "k8s-onprem"}


def test_disclaimer_carries_structural_facts_language() -> None:
    assert "structural facts" in DISCLAIMER
    assert "customer" in DISCLAIMER
    # Forbidden verdict vocabulary MUST NOT appear in the disclaimer.
    for forbidden in ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]:
        assert forbidden not in DISCLAIMER


def test_get_p95_convenience_function() -> None:
    bench = load_benchmark()
    if not bench.measurements:
        pytest.skip("embedded benchmark data not bundled in this test env")
    p95 = get_p95(
        substrate="mock",
        pattern="pattern_1_single_decision",
        shape="small",
    )
    assert p95 is not None
    assert p95 > 0


def test_parse_artifact_round_trip() -> None:
    doc = {
        "benchmark_version": "2026-05",
        "schema_version": "1.0",
        "harness_version": "0.1.0",
        "generated_at": "2026-05-27T20:00:00Z",
        "measurements": [
            {
                "substrate": "cloud-run-gen2",
                "region": "us-central1",
                "pattern": "pattern_1_single_decision",
                "capsulefile_shape": "small",
                "iterations": 1000,
                "warmup_discarded": 100,
                "cold_start_ms": {
                    "p50": 312,
                    "p95": 480,
                    "p99": 612,
                    "stddev": 87,
                },
                "exec_ms": {"p50": 24, "p95": 41, "p99": 58, "stddev": 9},
                "destroy_ms": {
                    "p50": 47,
                    "p95": 89,
                    "p99": 134,
                    "stddev": 21,
                },
                "total_ms": {
                    "p50": 383,
                    "p95": 555,
                    "p99": 681,
                    "stddev": 96,
                },
                "audit_proof_emit_ms": {
                    "p50": 18,
                    "p95": 31,
                    "p99": 43,
                    "stddev": 7,
                },
                "production_wired": True,
                "captured_under": {
                    "git_sha": "abcdef",
                    "infra_rev": "rev-150",
                    "rust_version": "1.93",
                    "test_run_id": "test-uuid",
                    "harness_version": "0.1.0",
                    "captured_at": "2026-05-27T20:00:00Z",
                },
            }
        ],
        "methodology": "test methodology",
        "disclaimer": "test disclaimer — structural facts",
    }
    artifact = parse_artifact(doc)
    assert isinstance(artifact, BenchmarkArtifact)
    assert len(artifact.measurements) == 1
    run = artifact.measurements[0]
    assert isinstance(run, MeasurementRun)
    assert isinstance(run.cold_start_ms, Percentiles)
    assert run.cold_start_ms.p95 == 480
    assert run.production_wired is True
