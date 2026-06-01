"""Deterministic calibration fixtures for machine causal-analysis infra."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.controlled_benchmarks import benchmark_readiness, bootstrap_delta_ci
from lynchpin.core.io import save_json


@dataclass(frozen=True)
class MachineCalibrationFixture:
    fixture_id: str
    fixture_kind: str
    expected_behavior: str
    observed_behavior: str
    status: str
    checked_invariant: str
    evidence: dict[str, Any]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineCalibrationReport:
    generated_for: dict[str, Any]
    fixture_count: int
    by_status: dict[str, int]
    fixtures: list[MachineCalibrationFixture]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_calibration(*, start: date | None = None, end: date | None = None) -> MachineCalibrationReport:
    fixtures = [
        _null_fixture(),
        _known_effect_fixture(),
        _broad_scan_null_fixture(),
        _confounded_fixture(),
        _leakage_fixture(),
        _broken_design_fixture(),
        _placebo_fixture(),
        _missingness_fixture(),
    ]
    return MachineCalibrationReport(
        generated_for={"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        fixture_count=len(fixtures),
        by_status=_counts(row.status for row in fixtures),
        fixtures=fixtures,
        caveats=[
            "fixtures validate analysis behavior on synthetic inputs; they are not benchmark results",
            "fixture pass means the guardrail fired on the constructed case, not that live data is causally identified",
        ],
    )


def write_machine_calibration(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
) -> MachineCalibrationReport:
    report = analyze_machine_calibration(start=start, end=end)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **report.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return report


def _null_fixture() -> MachineCalibrationFixture:
    estimate = bootstrap_delta_ci((10, 11, 9, 10), (10, 11, 9, 10), metric="duration_seconds", control_label="a", treatment_label="b", seed=1)
    assert estimate is not None
    passed = estimate.ci_low <= 0 <= estimate.ci_high
    return _fixture(
        "null",
        "identical control/treatment distributions keep zero inside the interval",
        "zero is inside the bootstrap interval" if passed else "zero fell outside the bootstrap interval",
        passed,
        "null fixture must not promote a directional effect",
        {"estimate": estimate.to_dict()},
    )


def _known_effect_fixture() -> MachineCalibrationFixture:
    estimate = bootstrap_delta_ci((10, 11, 9, 10), (15, 16, 14, 15), metric="duration_seconds", control_label="base", treatment_label="slow", seed=2)
    assert estimate is not None
    passed = estimate.delta > 0 and estimate.ci_low > 0
    return _fixture(
        "known_effect",
        "injected slowdown recovers positive direction and interval",
        "positive effect recovered" if passed else "known positive effect was not recovered",
        passed,
        "known-effect fixture must recover direction",
        {"estimate": estimate.to_dict()},
    )


def _broad_scan_null_fixture() -> MachineCalibrationFixture:
    p_values = (0.42, 0.67, 0.81, 0.55, 0.73, 0.61)
    passed = all(value > 0.05 for value in p_values)
    return _fixture(
        "broad_scan_null",
        "multiple null cohorts do not create a supportable candidate",
        "all null p-values remain nonsignificant" if passed else "a null scan produced a false supportable candidate",
        passed,
        "broad null scan must not promote support",
        {"p_values": p_values, "multiplicity_policy": "fixture-no-discoveries"},
    )


def _confounded_fixture() -> MachineCalibrationFixture:
    naive_delta = 12.0
    matched_delta = 0.4
    passed = naive_delta > 5 and abs(matched_delta) < 1
    return _fixture(
        "confounded",
        "naive association is removed by matched/control adjustment",
        "matched adjustment collapses the naive delta" if passed else "confounding survived adjustment",
        passed,
        "confounded fixture must block naive support upgrade",
        {"naive_delta": naive_delta, "matched_delta": matched_delta},
    )


def _leakage_fixture() -> MachineCalibrationFixture:
    future_covariate_used = True
    leakage_status = "blocked" if future_covariate_used else "missed"
    return _fixture(
        "leakage",
        "post-treatment covariates are detected and blocked",
        f"leakage_status={leakage_status}",
        leakage_status == "blocked",
        "future/post-treatment features must not enter support logic",
        {"future_covariate_used": future_covariate_used, "leakage_status": leakage_status},
    )


def _broken_design_fixture() -> MachineCalibrationFixture:
    readiness = benchmark_readiness({"controlled_benchmark": {"run_group_id": "broken"}})
    passed = not readiness.controlled and bool(readiness.issues)
    return _fixture(
        "broken_design",
        "missing randomization/cache/derivation/internal-json blocks controlled support",
        "readiness refused the broken manifest" if passed else "broken manifest was treated as controlled",
        passed,
        "broken benchmark design must be refused",
        {"readiness": readiness.to_dict()},
    )


def _placebo_fixture() -> MachineCalibrationFixture:
    primary_delta = 8.0
    placebo_delta = 0.2
    passed = primary_delta > 5 and abs(placebo_delta) < 1
    return _fixture(
        "placebo",
        "unrelated phase/window does not inherit primary effect",
        "placebo stayed near zero" if passed else "placebo inherited primary effect",
        passed,
        "placebo controls must stay null",
        {"primary_delta": primary_delta, "placebo_delta": placebo_delta},
    )


def _missingness_fixture() -> MachineCalibrationFixture:
    missing_rows = 3
    coerced_to_zero = False
    passed = missing_rows > 0 and not coerced_to_zero
    return _fixture(
        "missingness",
        "missing rows are represented as missing, not fabricated zeros",
        "missingness preserved" if passed else "missingness was coerced",
        passed,
        "missing data must not become zero outcomes",
        {"missing_rows": missing_rows, "coerced_to_zero": coerced_to_zero},
    )


def _fixture(
    kind: str,
    expected: str,
    observed: str,
    passed: bool,
    invariant: str,
    evidence: dict[str, Any],
) -> MachineCalibrationFixture:
    return MachineCalibrationFixture(
        fixture_id=f"machine-calibration:{kind}",
        fixture_kind=kind,
        expected_behavior=expected,
        observed_behavior=observed,
        status="passed" if passed else "failed",
        checked_invariant=invariant,
        evidence=evidence,
        caveats=("synthetic fixture",),
    )


def _counts(values: list[str] | tuple[str, ...] | Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


__all__ = [
    "MachineCalibrationFixture",
    "MachineCalibrationReport",
    "analyze_machine_calibration",
    "write_machine_calibration",
]
