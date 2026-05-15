"""General statistical analysis over unified machine telemetry substrate tables."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.core.analytics import (
    AnomalyResult,
    ChangePoint,
    CorrelationResult,
    TrendResult,
    anomaly_score,
    cross_correlate,
    detect_changepoints,
    detect_trend,
)
from lynchpin.analysis.core.io import save_json
from lynchpin.substrate.connection import connect, substrate_path


@dataclass(frozen=True)
class MachineCoverage:
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    sources: dict[str, int]
    refreshes: dict[str, int]


@dataclass(frozen=True)
class DailyMachineTelemetry:
    day: date
    sample_count: int
    avg_load_1m: float | None
    p95_load_1m: float | None
    min_mem_avail_mb: int | None
    avg_io_psi_some: float | None
    avg_io_psi_full: float | None
    avg_gpu_power_w: float | None
    avg_gpu_pcie_gen: float | None


@dataclass(frozen=True)
class HardwareRegime:
    gpu_pcie_gen: int | None
    gpu_pcie_width: int | None
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    avg_load_1m: float | None
    avg_io_psi_some: float | None
    avg_io_psi_full: float | None
    min_mem_avail_mb: int | None
    avg_gpu_power_w: float | None
    load_ratio_vs_best_link: float | None
    io_full_ratio_vs_best_link: float | None


@dataclass(frozen=True)
class MachineSignalAnalysis:
    metric: str
    trend: TrendResult
    changepoints: list[ChangePoint]
    latest_anomaly: AnomalyResult | None


@dataclass(frozen=True)
class MachineCorrelation:
    left: str
    right: str
    correlations: list[CorrelationResult]


@dataclass(frozen=True)
class MachineTelemetryAnalysis:
    coverage: MachineCoverage
    daily: list[DailyMachineTelemetry]
    hardware_regimes: list[HardwareRegime]
    signals: list[MachineSignalAnalysis]
    correlations: list[MachineCorrelation]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_telemetry(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> MachineTelemetryAnalysis:
    """Analyze machine telemetry promoted into ``machine_metric_sample``.

    The output is a general troubleshooting surface: coverage, daily metric
    profiles, hardware-state regimes, trends, anomalies, and correlations. It
    is not limited to a single performance question. It does not re-read legacy
    CSV/parquet formats and it does not flatten process/cgroup evidence from
    below into machine metrics.
    """
    with connect(path or substrate_path(), read_only=True) as conn:
        coverage = _coverage(conn, start=start, end=end)
        daily = _daily(conn, start=start, end=end)
        hardware_regimes = _hardware_regimes(conn, start=start, end=end)

    signals = _signals(daily)
    correlations = _correlations(daily)
    caveats = _caveats(coverage, daily, hardware_regimes)
    return MachineTelemetryAnalysis(
        coverage=coverage,
        daily=daily,
        hardware_regimes=hardware_regimes,
        signals=signals,
        correlations=correlations,
        caveats=caveats,
    )


def write_machine_telemetry_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> MachineTelemetryAnalysis:
    analysis = analyze_machine_telemetry(start=start, end=end, path=path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(
        out,
        json.loads(json.dumps(payload, default=str)),
        sort_keys=True,
    )
    return analysis


def _window_clause(start: date | None, end: date | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("CAST(observed_at AS DATE) >= ?")
        params.append(start)
    if end is not None:
        clauses.append("CAST(observed_at AS DATE) <= ?")
        params.append(end)
    if not clauses:
        return "", params
    return "WHERE " + " AND ".join(clauses), params


def _coverage(conn: Any, *, start: date | None, end: date | None) -> MachineCoverage:
    where, params = _window_clause(start, end)
    row = conn.execute(
        f"""
        SELECT count(*), min(observed_at), max(observed_at)
        FROM machine_metric_sample
        {where}
        """,
        params,
    ).fetchone()
    source_rows = conn.execute(
        f"""
        SELECT source, count(*)
        FROM machine_metric_sample
        {where}
        GROUP BY source
        ORDER BY count(*) DESC
        """,
        params,
    ).fetchall()
    refresh_rows = conn.execute(
        f"""
        SELECT refresh_id, count(*)
        FROM machine_metric_sample
        {where}
        GROUP BY refresh_id
        ORDER BY count(*) DESC
        """,
        params,
    ).fetchall()
    return MachineCoverage(
        sample_count=int(row[0]),
        first_observed_at=row[1],
        last_observed_at=row[2],
        sources={str(source): int(count) for source, count in source_rows},
        refreshes={str(refresh): int(count) for refresh, count in refresh_rows},
    )


def _daily(conn: Any, *, start: date | None, end: date | None) -> list[DailyMachineTelemetry]:
    where, params = _window_clause(start, end)
    rows = conn.execute(
        f"""
        SELECT
            CAST(observed_at AS DATE) AS day,
            count(*) AS sample_count,
            avg(load_1m) AS avg_load_1m,
            quantile_cont(load_1m, 0.95) AS p95_load_1m,
            min(mem_avail_mb) AS min_mem_avail_mb,
            avg(coalesce(io_psi_some_avg10, io_psi_some_avg60)) AS avg_io_psi_some,
            avg(coalesce(io_psi_full_avg10, io_psi_full_avg60)) AS avg_io_psi_full,
            avg(gpu_power_w) AS avg_gpu_power_w,
            avg(gpu_pcie_gen) AS avg_gpu_pcie_gen
        FROM machine_metric_sample
        {where}
        GROUP BY day
        ORDER BY day
        """,
        params,
    ).fetchall()
    return [
        DailyMachineTelemetry(
            day=row[0],
            sample_count=int(row[1]),
            avg_load_1m=_round(row[2]),
            p95_load_1m=_round(row[3]),
            min_mem_avail_mb=None if row[4] is None else int(row[4]),
            avg_io_psi_some=_round(row[5]),
            avg_io_psi_full=_round(row[6]),
            avg_gpu_power_w=_round(row[7]),
            avg_gpu_pcie_gen=_round(row[8]),
        )
        for row in rows
    ]


def _hardware_regimes(conn: Any, *, start: date | None, end: date | None) -> list[HardwareRegime]:
    where, params = _window_clause(start, end)
    rows = conn.execute(
        f"""
        SELECT
            gpu_pcie_gen,
            gpu_pcie_width,
            count(*) AS sample_count,
            min(observed_at),
            max(observed_at),
            avg(load_1m),
            avg(coalesce(io_psi_some_avg10, io_psi_some_avg60)),
            avg(coalesce(io_psi_full_avg10, io_psi_full_avg60)),
            min(mem_avail_mb),
            avg(gpu_power_w)
        FROM machine_metric_sample
        {where}
        GROUP BY gpu_pcie_gen, gpu_pcie_width
        ORDER BY sample_count DESC
        """,
        params,
    ).fetchall()
    baseline = _best_link_row(rows)
    baseline_load = baseline[5] if baseline else None
    baseline_io_full = baseline[7] if baseline else None
    return [
        HardwareRegime(
            gpu_pcie_gen=None if row[0] is None else int(row[0]),
            gpu_pcie_width=None if row[1] is None else int(row[1]),
            sample_count=int(row[2]),
            first_observed_at=row[3],
            last_observed_at=row[4],
            avg_load_1m=_round(row[5]),
            avg_io_psi_some=_round(row[6]),
            avg_io_psi_full=_round(row[7]),
            min_mem_avail_mb=None if row[8] is None else int(row[8]),
            avg_gpu_power_w=_round(row[9]),
            load_ratio_vs_best_link=_ratio(row[5], baseline_load),
            io_full_ratio_vs_best_link=_ratio(row[7], baseline_io_full),
        )
        for row in rows
    ]


def _signals(daily: list[DailyMachineTelemetry]) -> list[MachineSignalAnalysis]:
    series = {
        "p95_load_1m": [row.p95_load_1m for row in daily],
        "min_mem_avail_mb": [row.min_mem_avail_mb for row in daily],
        "avg_io_psi_full": [row.avg_io_psi_full for row in daily],
        "avg_gpu_power_w": [row.avg_gpu_power_w for row in daily],
    }
    signals: list[MachineSignalAnalysis] = []
    for metric, raw_values in series.items():
        values = [float(value) for value in raw_values if value is not None]
        if not values:
            continue
        trend = detect_trend(values)
        changepoints = detect_changepoints(values)
        latest = anomaly_score(values[-1], values[:-1]) if len(values) > 5 else None
        signals.append(
            MachineSignalAnalysis(
                metric=metric,
                trend=trend,
                changepoints=changepoints,
                latest_anomaly=latest,
            )
        )
    return signals


def _correlations(daily: list[DailyMachineTelemetry]) -> list[MachineCorrelation]:
    pairs = [
        ("avg_gpu_pcie_gen", "p95_load_1m"),
        ("avg_io_psi_full", "p95_load_1m"),
        ("min_mem_avail_mb", "p95_load_1m"),
        ("avg_gpu_power_w", "p95_load_1m"),
    ]
    result: list[MachineCorrelation] = []
    for left, right in pairs:
        aligned = [
            (getattr(row, left), getattr(row, right))
            for row in daily
            if getattr(row, left) is not None and getattr(row, right) is not None
        ]
        if len(aligned) < 5:
            continue
        a, b = zip(*aligned)
        result.append(
            MachineCorrelation(
                left=left,
                right=right,
                correlations=cross_correlate([float(v) for v in a], [float(v) for v in b], max_lag=1),
            )
        )
    return result


def _caveats(
    coverage: MachineCoverage,
    daily: list[DailyMachineTelemetry],
    hardware_regimes: list[HardwareRegime],
) -> list[str]:
    caveats: list[str] = []
    if coverage.sample_count == 0:
        return ["machine_metric_sample has no rows for this window"]
    if len(daily) < 7:
        caveats.append("daily statistical tests are underpowered with fewer than 7 covered days")
    if any(regime.gpu_pcie_gen is None for regime in hardware_regimes):
        caveats.append("some legacy rows do not carry PCIe state")
    if "machine.stability_lab.power_watchdog" in coverage.sources:
        caveats.append("stability-lab rows are short controlled slices, not continuous telemetry")
    if not any(regime.gpu_pcie_gen is not None for regime in hardware_regimes):
        caveats.append("no PCIe state exists in this window")
    if statistics.mean(row.sample_count for row in daily) < 10:
        caveats.append("average daily sample count is low")
    caveats.append("below process/cgroup attribution is not yet joined into this analysis")
    return caveats


def _round(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _best_link_row(rows: list[Any]) -> Any | None:
    candidates = [row for row in rows if row[0] is not None and row[1] is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda row: (int(row[0]), int(row[1]), int(row[2])))


def _ratio(value: Any, baseline: Any) -> float | None:
    if value is None or baseline is None or abs(float(baseline)) < 1e-9:
        return None
    return round(float(value) / float(baseline), 4)
