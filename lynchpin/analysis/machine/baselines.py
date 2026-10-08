"""Observational baselines over continuous machine telemetry."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any, Iterable

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json
from lynchpin.analysis.machine.sql import latest_machine_rows
from lynchpin.analysis.machine.telemetry import analyze_machine_telemetry
from lynchpin.core.analytics import ChangePoint, detect_changepoints
from lynchpin.substrate.connection import connect, substrate_path


DEFAULT_ERA_BOUNDARIES = (date(2026, 5, 12),)
BASELINE_METRICS = (
    "load_1m",
    "mem_avail_mb",
    "io_psi_full",
    "latency_oversleep_ms",
    "dstate_task_count",
    "gpu_power_w",
    "gpu_temp_c",
)


@dataclass(frozen=True)
class RobustMetricBand:
    metric: str
    sample_count: int
    median: float | None
    mad: float | None
    q1: float | None
    q3: float | None
    iqr: float | None


@dataclass(frozen=True)
class BaselineGroup:
    dimension: str
    key: str
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    metrics: tuple[RobustMetricBand, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class DailySignalBaseline:
    metric: str
    sample_count: int
    changepoints: list[ChangePoint]
    anomaly_runs: tuple[tuple[date, date, int], ...]


@dataclass(frozen=True)
class EraComparison:
    boundary: date
    before_sample_count: int
    after_sample_count: int
    metrics: tuple[dict[str, Any], ...]
    interpretation: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class WorkContextBaseline:
    dimension: str
    key: str
    window_count: int
    windows_with_episodes: int
    episode_overlap_rate: float
    top_episode_kinds: tuple[tuple[str, int], ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineObservationalBaselines:
    generated_for: dict[str, Any]
    by_hour: list[BaselineGroup]
    by_source: list[BaselineGroup]
    by_hardware_regime: list[BaselineGroup]
    daily_signals: list[DailySignalBaseline]
    era_comparisons: list[EraComparison]
    work_context: list[WorkContextBaseline]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_observational_baselines(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    context_path: Path | None = None,
    era_boundaries: Iterable[date] = DEFAULT_ERA_BOUNDARIES,
) -> MachineObservationalBaselines:
    with connect(path or substrate_path(), read_only=True) as conn:
        by_hour = _baseline_groups(conn, "hour_of_day", _hour_sql(), start=start, end=end)
        by_source = _baseline_groups(conn, "source", "source", start=start, end=end)
        by_hardware = _baseline_groups(conn, "hardware_regime", _hardware_sql(), start=start, end=end)

    telemetry = analyze_machine_telemetry(start=start, end=end, path=path)
    daily_signals = _daily_signal_baselines(telemetry)
    era_comparisons = _era_comparisons(telemetry, tuple(era_boundaries))
    work_context, context_caveats = _work_context_baselines(context_path=context_path)
    caveats = _caveats(by_hour, by_source, by_hardware, telemetry.coverage.sample_count, context_caveats)
    return MachineObservationalBaselines(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "metrics": list(BASELINE_METRICS),
            "era_boundaries": [boundary.isoformat() for boundary in era_boundaries],
        },
        by_hour=by_hour,
        by_source=by_source,
        by_hardware_regime=by_hardware,
        daily_signals=daily_signals,
        era_comparisons=era_comparisons,
        work_context=work_context,
        caveats=caveats,
    )


def write_machine_observational_baselines(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    context_path: Path | None = None,
) -> MachineObservationalBaselines:
    analysis = analyze_machine_observational_baselines(start=start, end=end, path=path, context_path=context_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _baseline_groups(
    conn: Any,
    dimension: str,
    key_expr: str,
    *,
    start: date | None,
    end: date | None,
) -> list[BaselineGroup]:
    where, params = _window_clause(start, end)
    metric_rows = latest_machine_rows("machine_metric_sample")
    rows = conn.execute(
        f"""
        SELECT
            {key_expr} AS baseline_key,
            observed_at,
            load_1m,
            mem_avail_mb,
            coalesce(io_psi_full_avg10, io_psi_full_avg60),
            latency_oversleep_ms,
            dstate_task_count,
            gpu_power_w,
            gpu_temp_c
        FROM ({metric_rows})
        {where}
        ORDER BY baseline_key, observed_at
        """,
        params,
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row[0])
        bucket = grouped.setdefault(key, {"times": [], "values": {metric: [] for metric in BASELINE_METRICS}})
        bucket["times"].append(row[1])
        for idx, metric in enumerate(BASELINE_METRICS, start=2):
            if row[idx] is not None:
                bucket["values"][metric].append(float(row[idx]))
    result = [_baseline_group(dimension, key, data) for key, data in grouped.items()]
    result.sort(key=lambda group: (-group.sample_count, group.key))
    return result


def _baseline_group(dimension: str, key: str, data: dict[str, Any]) -> BaselineGroup:
    times = sorted(data["times"])
    sample_count = len(times)
    metrics = tuple(_band(metric, data["values"][metric]) for metric in BASELINE_METRICS)
    caveats = []
    if sample_count < 30:
        caveats.append("low sample count for robust baseline")
    return BaselineGroup(
        dimension=dimension,
        key=key,
        sample_count=sample_count,
        first_observed_at=times[0] if times else None,
        last_observed_at=times[-1] if times else None,
        metrics=tuple(metric for metric in metrics if metric.sample_count > 0 and metric.median is not None),
        caveats=tuple(caveats),
    )


def _band(metric: str, values: list[float]) -> RobustMetricBand:
    if not values:
        return RobustMetricBand(metric=metric, sample_count=0, median=None, mad=None, q1=None, q3=None, iqr=None)
    ordered = sorted(values)
    median = statistics.median(ordered)
    mad = statistics.median(abs(value - median) for value in ordered)
    q1f = _quantile(ordered, 0.25)
    q3f = _quantile(ordered, 0.75)
    return RobustMetricBand(
        metric=metric,
        sample_count=len(ordered),
        median=_round(median),
        mad=_round(mad),
        q1=q1f,
        q3=q3f,
        iqr=_round(q3f - q1f) if q1f is not None and q3f is not None else None,
    )


def _daily_signal_baselines(telemetry: Any) -> list[DailySignalBaseline]:
    series = {
        "p95_load_1m": [(row.day, row.p95_load_1m) for row in telemetry.daily],
        "min_mem_avail_mb": [(row.day, row.min_mem_avail_mb) for row in telemetry.daily],
        "avg_io_psi_full": [(row.day, row.avg_io_psi_full) for row in telemetry.daily],
        "avg_gpu_power_w": [(row.day, row.avg_gpu_power_w) for row in telemetry.daily],
    }
    result = []
    for metric, raw in series.items():
        points = [(day, float(value)) for day, value in raw if value is not None]
        values = [value for _, value in points]
        result.append(
            DailySignalBaseline(
                metric=metric,
                sample_count=len(values),
                changepoints=detect_changepoints(values) if len(values) >= 10 else [],
                anomaly_runs=_anomaly_runs(points),
            )
        )
    return result


def _anomaly_runs(points: list[tuple[date, float]]) -> tuple[tuple[date, date, int], ...]:
    if len(points) < 7:
        return ()
    values = [value for _, value in points]
    med = statistics.median(values)
    deviations = [abs(value - med) for value in values]
    mad = statistics.median(deviations)
    if mad <= 1e-9:
        return ()
    threshold = med + 3.5 * 1.4826 * mad
    runs: list[tuple[date, date, int]] = []
    current: list[date] = []
    for day, value in points:
        if value > threshold:
            current.append(day)
        elif current:
            runs.append((current[0], current[-1], len(current)))
            current = []
    if current:
        runs.append((current[0], current[-1], len(current)))
    return tuple(runs)


def _era_comparisons(telemetry: Any, boundaries: tuple[date, ...]) -> list[EraComparison]:
    result: list[EraComparison] = []
    daily = telemetry.daily
    for boundary in boundaries:
        before = [row for row in daily if row.day < boundary]
        after = [row for row in daily if row.day >= boundary]
        metrics = tuple(
            _era_metric(metric, before, after)
            for metric in ("p95_load_1m", "min_mem_avail_mb", "avg_io_psi_full", "avg_gpu_power_w")
        )
        caveats = ["observational comparison only; do not infer causality without manifest-backed controlled runs"]
        if len(before) < 3 or len(after) < 3:
            caveats.append("one or both eras have fewer than 3 covered days")
        result.append(EraComparison(
            boundary=boundary,
            before_sample_count=len(before),
            after_sample_count=len(after),
            metrics=metrics,
            interpretation="observational before/after summary",
            caveats=tuple(caveats),
        ))
    return result


def _era_metric(metric: str, before: list[Any], after: list[Any]) -> dict[str, Any]:
    before_values = [float(value) for row in before if (value := getattr(row, metric)) is not None]
    after_values = [float(value) for row in after if (value := getattr(row, metric)) is not None]
    before_median = statistics.median(before_values) if before_values else None
    after_median = statistics.median(after_values) if after_values else None
    delta = None
    if before_median is not None and after_median is not None:
        delta = after_median - before_median
    return {
        "metric": metric,
        "before_median": _round(before_median),
        "after_median": _round(after_median),
        "delta": _round(delta),
        "before_n": len(before_values),
        "after_n": len(after_values),
    }


def _work_context_baselines(*, context_path: Path | None) -> tuple[list[WorkContextBaseline], list[str]]:
    payload = load_json_object(
        context_path or resolve_analysis_path("machine_context_windows.json"),
        label="machine context windows",
    )
    windows = [row for row in payload.get("windows", []) if isinstance(row, dict)]
    rows = []
    for dimension, groups in (
        ("project", _group_windows_by_project(windows)),
        ("work_kind", _group_windows_by_field(windows, "work_kind")),
        ("provider", _group_windows_by_field(windows, "provider")),
    ):
        for key, grouped in groups.items():
            rows.append(_work_context_baseline(dimension, key, grouped))
    rows.sort(key=lambda row: (row.dimension, -row.window_count, row.key))
    return rows, []


def _work_context_baseline(dimension: str, key: str, windows: list[dict[str, Any]]) -> WorkContextBaseline:
    kind_counts: dict[str, int] = {}
    windows_with_episodes = 0
    for window in windows:
        episode_count = int(window.get("episode_count") or 0)
        if episode_count:
            windows_with_episodes += 1
        for episode in window.get("episodes") or []:
            if isinstance(episode, dict) and episode.get("kind"):
                kind = str(episode["kind"])
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
    top_kinds = tuple(sorted(kind_counts.items(), key=lambda item: (-item[1], item[0]))[:5])
    caveats = ("derived from materialized machine_context_windows artifact",)
    return WorkContextBaseline(
        dimension=dimension,
        key=key,
        window_count=len(windows),
        windows_with_episodes=windows_with_episodes,
        episode_overlap_rate=round(windows_with_episodes / len(windows), 4) if windows else 0.0,
        top_episode_kinds=top_kinds,
        caveats=caveats,
    )


def _group_windows_by_project(windows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        projects = window.get("projects") if isinstance(window.get("projects"), list) else []
        for project in projects or ["(unattributed)"]:
            groups.setdefault(str(project), []).append(window)
    return groups


def _group_windows_by_field(windows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        groups.setdefault(str(window.get(field) or "(none)"), []).append(window)
    return groups


def _caveats(
    by_hour: list[BaselineGroup],
    by_source: list[BaselineGroup],
    by_hardware: list[BaselineGroup],
    sample_count: int,
    context_caveats: list[str],
) -> list[str]:
    caveats = [
        "baselines are observational; use experiment manifests for controlled benchmark claims",
        "robust bands summarize telemetry distributions and do not identify responsible processes by themselves",
        *context_caveats,
    ]
    if sample_count == 0:
        caveats.append("machine_metric_sample has no rows")
    if len(by_hour) < 12:
        caveats.append("hour-of-day baseline has sparse hour coverage")
    if len(by_source) > 1:
        caveats.append("multiple telemetry sources are present; source-specific baselines should not be pooled blindly")
    if not any(group.key not in {"unknown", "NonexNone"} for group in by_hardware):
        caveats.append("hardware-regime baseline lacks concrete PCIe state")
    return sorted(dict.fromkeys(caveats))


def _window_clause(start: date | None, end: date | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(observed_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(observed_at AS DATE) <= ?")
        params.append(end)
    return ("WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _hour_sql() -> str:
    return "CAST(date_part('hour', observed_at) AS VARCHAR)"


def _hardware_sql() -> str:
    return "coalesce('gen' || CAST(gpu_pcie_gen AS VARCHAR) || 'x' || CAST(gpu_pcie_width AS VARCHAR), 'unknown')"


def _quantile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return _round(ordered[idx])


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)
