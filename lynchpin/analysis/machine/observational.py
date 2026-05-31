"""Observational command-performance comparisons by machine state."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class CommandStateCohort:
    tool: str
    work_state: str
    pressure_state: str
    command_count: int
    error_count: int
    median_duration_seconds: float | None
    p95_duration_seconds: float | None


@dataclass(frozen=True)
class ObservationalCommandDelta:
    tool: str
    work_state: str
    pressure_state: str
    baseline_state: str
    pressure_count: int
    baseline_count: int
    median_delta_seconds: float | None
    p95_delta_seconds: float | None
    error_rate_delta: float | None
    interpretation: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineObservationalCommandAnalysis:
    generated_for: dict[str, Any]
    cohort_count: int
    delta_count: int
    cohorts: list[CommandStateCohort]
    deltas: list[ObservationalCommandDelta]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_observational_command_deltas(
    *,
    start: date | None = None,
    end: date | None = None,
    command_path: Path | None = None,
    min_cohort_size: int = 2,
) -> MachineObservationalCommandAnalysis:
    payload = load_json_object(
        command_path or resolve_analysis_path("command_performance_windows.json"),
        label="command performance windows",
    )
    windows = [row for row in payload.get("windows", []) if isinstance(row, dict)]
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in windows:
        tool = str(row.get("tool") or "unknown")
        work_state = str(row.get("machine_work_state") or "unknown")
        pressure_state = str(row.get("machine_pressure_state") or "unjoined")
        grouped[(tool, work_state, pressure_state)].append(row)

    cohorts = [_cohort(tool, work_state, pressure_state, rows) for (tool, work_state, pressure_state), rows in grouped.items()]
    cohorts.sort(key=lambda row: (-row.command_count, row.tool, row.work_state, row.pressure_state))
    deltas = _deltas(grouped, min_cohort_size=min_cohort_size)
    caveats = list(payload.get("caveats") or [])
    caveats.append("observational association only; pressure states are not randomized treatments")
    if not deltas:
        caveats.append("no tool/work-state cohort had both pressure and quiet baseline samples")
    return MachineObservationalCommandAnalysis(
        generated_for=_generated_for(start, end, min_cohort_size),
        cohort_count=len(cohorts),
        delta_count=len(deltas),
        cohorts=cohorts,
        deltas=deltas,
        caveats=sorted(dict.fromkeys(str(caveat) for caveat in caveats if caveat)),
    )


def write_observational_command_deltas(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    command_path: Path | None = None,
    min_cohort_size: int = 2,
) -> MachineObservationalCommandAnalysis:
    analysis = analyze_observational_command_deltas(
        start=start,
        end=end,
        command_path=command_path,
        min_cohort_size=min_cohort_size,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _cohort(tool: str, work_state: str, pressure_state: str, rows: list[dict[str, Any]]) -> CommandStateCohort:
    durations = sorted(_float(row.get("duration_seconds")) for row in rows)
    return CommandStateCohort(
        tool=tool,
        work_state=work_state,
        pressure_state=pressure_state,
        command_count=len(rows),
        error_count=sum(1 for row in rows if row.get("exit_code") not in (None, 0)),
        median_duration_seconds=round(statistics.median(durations), 3) if durations else None,
        p95_duration_seconds=_p95(durations),
    )


def _deltas(
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]],
    *,
    min_cohort_size: int,
) -> list[ObservationalCommandDelta]:
    deltas: list[ObservationalCommandDelta] = []
    by_tool_work: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for (tool, work_state, pressure_state), rows in grouped.items():
        by_tool_work[(tool, work_state)][pressure_state] = rows

    for (tool, work_state), by_pressure in by_tool_work.items():
        baseline, baseline_state, baseline_caveats = _baseline_rows(
            by_pressure,
            min_cohort_size=min_cohort_size,
        )
        if not baseline:
            continue
        baseline_stats = _stats(baseline)
        for pressure_state, rows in by_pressure.items():
            if pressure_state in {"quiet", "hardware_regime", "unjoined"}:
                continue
            if len(rows) < min_cohort_size:
                continue
            pressure_stats = _stats(rows)
            deltas.append(
                ObservationalCommandDelta(
                    tool=tool,
                    work_state=work_state,
                    pressure_state=pressure_state,
                    baseline_state=baseline_state,
                    pressure_count=len(rows),
                    baseline_count=len(baseline),
                    median_delta_seconds=_delta(pressure_stats["median"], baseline_stats["median"]),
                    p95_delta_seconds=_delta(pressure_stats["p95"], baseline_stats["p95"]),
                    error_rate_delta=_delta(pressure_stats["error_rate"], baseline_stats["error_rate"]),
                    interpretation="matched observational delta by tool and work_state",
                    caveats=(
                        "not causal; command mix and user behavior may differ within this cohort",
                        f"minimum cohort size {min_cohort_size}",
                        *baseline_caveats,
                    ),
                )
            )
    return sorted(
        deltas,
        key=lambda row: (
            row.median_delta_seconds is None,
            -(row.median_delta_seconds or 0.0),
            row.tool,
            row.work_state,
            row.pressure_state,
        ),
    )


def _baseline_rows(
    by_pressure: dict[str, list[dict[str, Any]]],
    *,
    min_cohort_size: int,
) -> tuple[list[dict[str, Any]], str, tuple[str, ...]]:
    quiet = by_pressure.get("quiet") or []
    if len(quiet) >= min_cohort_size:
        return quiet, "quiet", ()
    baseline = [*quiet, *(by_pressure.get("hardware_regime") or [])]
    if len(baseline) >= min_cohort_size:
        return baseline, "quiet_or_hardware_regime", ()
    return [], "quiet_or_hardware_regime", ()


def _stats(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    durations = sorted(_float(row.get("duration_seconds")) for row in rows)
    error_rate = sum(1 for row in rows if row.get("exit_code") not in (None, 0)) / len(rows) if rows else None
    return {
        "median": round(statistics.median(durations), 3) if durations else None,
        "p95": _p95(durations),
        "error_rate": round(error_rate, 4) if error_rate is not None else None,
    }


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 4)


def _float(value: object) -> float:
    try:
        return float(str(value or 0.0))
    except ValueError:
        return 0.0


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)


def _generated_for(start: date | None, end: date | None, min_cohort_size: int) -> dict[str, Any]:
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "source": "command_performance_windows.json",
        "min_cohort_size": min_cohort_size,
    }
