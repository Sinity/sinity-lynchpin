"""Measurement-system diagnostics for machine causal analysis."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from lynchpin.core.io import load_json_if_exists, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineMeasurementCheck:
    check_id: str
    check_kind: str
    status: str
    summary: str
    evidence: dict[str, Any]
    support_consequence: str


@dataclass(frozen=True)
class MachineMeasurementSystemReport:
    generated_for: dict[str, Any]
    check_count: int
    by_status: dict[str, int]
    checks: list[MachineMeasurementCheck]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_measurement_system(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    experiments_path: Path | None = None,
    work_observations_path: Path | None = None,
) -> MachineMeasurementSystemReport:
    feature_frames = _payload(feature_frames_path, "machine_analysis_feature_frames.json")
    experiments = _payload(experiments_path, "machine_experiment_claims.json")
    work = _payload(work_observations_path, "machine_work_observations.json")
    checks = [
        _timer_check(),
        _censoring_check(feature_frames),
        _baseline_repeatability_check(work),
        _warmup_carryover_check(experiments),
        _variance_decomposition_check(experiments),
    ]
    return MachineMeasurementSystemReport(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": [
                "machine_analysis_feature_frames.json",
                "machine_experiment_claims.json",
                "machine_work_observations.json",
                "python_clock_info",
            ],
        },
        check_count=len(checks),
        by_status=_counts(row.status for row in checks),
        checks=checks,
        caveats=[
            "measurement checks are readiness diagnostics, not performance claims",
            "baseline repeatability from observational work summaries is weaker than repeated controlled runs",
        ],
    )


def write_machine_measurement_system(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    experiments_path: Path | None = None,
    work_observations_path: Path | None = None,
) -> MachineMeasurementSystemReport:
    report = analyze_machine_measurement_system(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        experiments_path=experiments_path,
        work_observations_path=work_observations_path,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **report.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return report


def _timer_check() -> MachineMeasurementCheck:
    monotonic = time.get_clock_info("monotonic")
    perf = time.get_clock_info("perf_counter")
    status = "passed" if monotonic.monotonic and perf.monotonic else "failed"
    return MachineMeasurementCheck(
        check_id="machine-measurement:timer",
        check_kind="timer_resolution_clock_source",
        status=status,
        summary=f"monotonic={monotonic.implementation}; perf_counter={perf.implementation}",
        evidence={
            "monotonic": _clock_payload(monotonic),
            "perf_counter": _clock_payload(perf),
        },
        support_consequence="controlled runs can record stable local clock metadata" if status == "passed" else "timing metadata blocks controlled support",
    )


def _censoring_check(feature_frames: dict[str, Any] | None) -> MachineMeasurementCheck:
    frame = feature_frames.get("frame") if isinstance(feature_frames, dict) and isinstance(feature_frames.get("frame"), dict) else {}
    censored = int(frame.get("censored_count") or 0)
    rows = int(frame.get("row_count") or 0)
    status = "passed" if rows and "censoring_summary" in frame else "missing"
    return MachineMeasurementCheck(
        check_id="machine-measurement:censoring",
        check_kind="censored_timeout_handling",
        status=status,
        summary=f"{censored}/{rows} feature-frame rows are censored",
        evidence={"row_count": rows, "censored_count": censored, "censoring_summary": frame.get("censoring_summary", {})},
        support_consequence="censored outcomes remain explicit" if status == "passed" else "missing censoring metadata blocks honest timeout-aware estimates",
    )


def _baseline_repeatability_check(work: dict[str, Any] | None) -> MachineMeasurementCheck:
    summaries = work.get("stage_summaries", []) if isinstance(work, dict) and isinstance(work.get("stage_summaries"), list) else []
    repeatable = [row for row in summaries if isinstance(row, dict) and int(row.get("observation_count") or 0) >= 3]
    spreads = []
    for row in repeatable:
        median = _float(row.get("median_duration_s"))
        p95 = _float(row.get("p95_duration_s"))
        if median is not None and p95 is not None:
            spreads.append(round(p95 - median, 6))
    status = "limited" if repeatable else "missing"
    if len(repeatable) >= 3:
        status = "passed"
    return MachineMeasurementCheck(
        check_id="machine-measurement:baseline-repeatability",
        check_kind="baseline_repeatability",
        status=status,
        summary=f"{len(repeatable)} repeated stage summaries available",
        evidence={"repeated_stage_count": len(repeatable), "p95_minus_median_seconds": spreads[:25]},
        support_consequence="observational repeatability exists; controlled baselines still preferred" if repeatable else "no repeated baseline summaries available",
    )


def _warmup_carryover_check(experiments: dict[str, Any] | None) -> MachineMeasurementCheck:
    packs = experiments.get("claim_packs", []) if isinstance(experiments, dict) and isinstance(experiments.get("claim_packs"), list) else []
    controlled = [row for row in packs if isinstance(row, dict) and row.get("claim_mode") == "controlled_benchmark"]
    status = "passed" if any(row.get("cache_condition") for row in controlled) else "untestable"
    return MachineMeasurementCheck(
        check_id="machine-measurement:warmup-carryover",
        check_kind="warmup_carryover",
        status=status,
        summary=f"{len(controlled)} controlled run packs available",
        evidence={"controlled_run_count": len(controlled), "cache_conditions": sorted({str(row.get("cache_condition")) for row in controlled if row.get("cache_condition")})},
        support_consequence="cache-condition blocking can assess carryover" if status == "passed" else "warmup/carryover cannot be checked without controlled cache-condition runs",
    )


def _variance_decomposition_check(experiments: dict[str, Any] | None) -> MachineMeasurementCheck:
    estimates = experiments.get("effect_estimates", []) if isinstance(experiments, dict) and isinstance(experiments.get("effect_estimates"), list) else []
    status = "passed" if estimates else "untestable"
    return MachineMeasurementCheck(
        check_id="machine-measurement:variance-decomposition",
        check_kind="variance_decomposition",
        status=status,
        summary=f"{len(estimates)} controlled effect estimates available",
        evidence={"estimate_count": len(estimates), "run_group_ids": [row.get("run_group_id") for row in estimates if isinstance(row, dict)]},
        support_consequence="variance can be decomposed from controlled estimates" if status == "passed" else "variance decomposition awaits controlled benchmark estimates",
    )


def _payload(path: Path | None, name: str) -> dict[str, Any] | None:
    payload = load_json_if_exists(path or resolve_analysis_path(name))
    return payload if isinstance(payload, dict) else None


def _clock_payload(info: Any) -> dict[str, Any]:
    return {
        "implementation": info.implementation,
        "monotonic": info.monotonic,
        "adjustable": info.adjustable,
        "resolution_seconds": info.resolution,
    }


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


__all__ = [
    "MachineMeasurementCheck",
    "MachineMeasurementSystemReport",
    "analyze_machine_measurement_system",
    "write_machine_measurement_system",
]
