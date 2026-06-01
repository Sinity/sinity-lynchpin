"""Mining scan registry and cohort summaries for machine attribution."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any

from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineMiningScan:
    scan_id: str
    frame_id: str
    unit_type: str
    outcome_metric: str
    dimensions: tuple[str, ...]
    row_count: int
    comparison_universe_size: int
    emitted_candidate_count: int
    multiplicity_policy: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineObservationCohort:
    cohort_id: str
    scan_id: str
    dimensions: dict[str, Any]
    row_count: int
    observed_count: int
    censored_count: int
    median_outcome: float | None
    p95_outcome: float | None
    max_outcome: float | None
    missing_value_count: int
    leakage_status: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineLaggedExposureSummary:
    summary_id: str
    dimensions: dict[str, Any]
    lag_window_seconds: int
    pressure_metric: str
    row_count: int
    paired_count: int
    high_prior_pressure_count: int
    median_outcome_after_prior_pressure: float | None
    median_outcome_without_prior_pressure: float | None
    median_delta: float | None
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineAnomalyCluster:
    cluster_id: str
    dimensions: dict[str, Any]
    row_count: int
    anomaly_count: int
    outcome_threshold: float
    max_outcome: float
    pressure_signature: tuple[str, ...]
    representative_unit_ids: tuple[str, ...]
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineMiningAnalysis:
    generated_for: dict[str, Any]
    scan: MachineMiningScan
    scan_count: int
    scans: list[MachineMiningScan]
    cohort_count: int
    cohorts: list[MachineObservationCohort]
    lagged_exposure_count: int
    lagged_exposures: list[MachineLaggedExposureSummary]
    anomaly_cluster_count: int
    anomaly_clusters: list[MachineAnomalyCluster]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_mining(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    dimensions: tuple[str, ...] = ("stage_name", "project"),
    min_cohort_size: int = 2,
    limit: int = 100,
) -> MachineMiningAnalysis:
    payload = load_json_object(
        feature_frames_path or resolve_analysis_path("machine_analysis_feature_frames.json"),
        label="machine analysis feature frames",
    )
    frame = payload.get("frame") if isinstance(payload, dict) else {}
    rows = frame.get("rows") if isinstance(frame, dict) else []
    if not isinstance(rows, list):
        rows = []
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            grouped[_dimension_key(row, dimensions)].append(row)
    cohorts = [
        _cohort(scan_id="", dimensions=dimensions, key=key, rows=items)
        for key, items in grouped.items()
        if len(items) >= min_cohort_size
    ]
    scan_id = _scan_id(frame.get("frame_id"), dimensions, len(rows), min_cohort_size)
    cohorts = [
        MachineObservationCohort(
            cohort_id=row.cohort_id,
            scan_id=scan_id,
            dimensions=row.dimensions,
            row_count=row.row_count,
            observed_count=row.observed_count,
            censored_count=row.censored_count,
            median_outcome=row.median_outcome,
            p95_outcome=row.p95_outcome,
            max_outcome=row.max_outcome,
            missing_value_count=row.missing_value_count,
            leakage_status=row.leakage_status,
            caveats=row.caveats,
        )
        for row in cohorts
    ]
    cohorts.sort(key=lambda row: (row.max_outcome is None, -(row.max_outcome or 0.0), -row.row_count, row.cohort_id))
    if limit > 0:
        cohorts = cohorts[:limit]
    lagged_exposures = _lagged_exposures(
        rows=[row for row in rows if isinstance(row, dict)],
        dimensions=dimensions,
        lag_window_seconds=3600,
        limit=limit,
    )
    anomaly_clusters = _anomaly_clusters(
        rows=[row for row in rows if isinstance(row, dict)],
        dimensions=dimensions,
        min_cluster_size=min_cohort_size,
        limit=limit,
    )
    scan = MachineMiningScan(
        scan_id=scan_id,
        frame_id=str(frame.get("frame_id") or ""),
        unit_type=str(frame.get("unit_type") or "unknown"),
        outcome_metric=str(frame.get("outcome_metric") or "unknown"),
        dimensions=dimensions,
        row_count=len(rows),
        comparison_universe_size=len(grouped),
        emitted_candidate_count=len(cohorts),
        multiplicity_policy="scan_registry_denominator_only; candidate ranking must apply FDR/support gates before claims",
        caveats=(
            f"minimum cohort size {min_cohort_size}",
            "scan registry records searched universe so top cohorts are not cherry-picked winners",
        ),
    )
    return MachineMiningAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": "machine_analysis_feature_frames.json",
            "dimensions": list(dimensions),
            "min_cohort_size": min_cohort_size,
            "limit": limit,
        },
        scan=scan,
        scan_count=1,
        scans=[scan],
        cohort_count=len(cohorts),
        cohorts=cohorts,
        lagged_exposure_count=len(lagged_exposures),
        lagged_exposures=lagged_exposures,
        anomaly_cluster_count=len(anomaly_clusters),
        anomaly_clusters=anomaly_clusters,
        caveats=[
            "mining output is exploratory unless validated by held-out windows, controls, or controlled benchmarks",
            "cohort summaries are observational and carry no causal wording",
            "lagged exposure and anomaly summaries are candidate-generation inputs, not causal support",
        ],
    )


def write_machine_mining(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    dimensions: tuple[str, ...] = ("stage_name", "project"),
    min_cohort_size: int = 2,
    limit: int = 100,
) -> MachineMiningAnalysis:
    analysis = analyze_machine_mining(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        dimensions=dimensions,
        min_cohort_size=min_cohort_size,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _cohort(
    *,
    scan_id: str,
    dimensions: tuple[str, ...],
    key: tuple[Any, ...],
    rows: list[dict[str, Any]],
) -> MachineObservationCohort:
    outcomes = sorted(_float(row.get("outcome_value")) for row in rows if _float(row.get("outcome_value")) is not None)
    leakage = "ok" if all(row.get("leakage_status") == "ok" for row in rows) else "invalid"
    dims = dict(zip(dimensions, key, strict=True))
    missing = sum(sum(1 for missing in (row.get("missingness") or {}).values() if missing) for row in rows)
    censored = sum(1 for row in rows if row.get("censoring_status") != "observed")
    return MachineObservationCohort(
        cohort_id=_cohort_id(dimensions, key),
        scan_id=scan_id,
        dimensions=dims,
        row_count=len(rows),
        observed_count=len(rows) - censored,
        censored_count=censored,
        median_outcome=round(statistics.median(outcomes), 3) if outcomes else None,
        p95_outcome=_p95(outcomes),
        max_outcome=round(max(outcomes), 3) if outcomes else None,
        missing_value_count=missing,
        leakage_status=leakage,
        caveats=(
            "cohort is mined from observational feature frames",
            "support ceiling is candidate until validation/control checks exist",
        ),
    )


def _dimension_key(row: dict[str, Any], dimensions: tuple[str, ...]) -> tuple[Any, ...]:
    covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
    values: list[Any] = []
    for dimension in dimensions:
        if dimension in row:
            values.append(row.get(dimension))
        else:
            values.append(covariates.get(dimension))
    return tuple(values)


def _lagged_exposures(
    *,
    rows: list[dict[str, Any]],
    dimensions: tuple[str, ...],
    lag_window_seconds: int,
    limit: int,
) -> list[MachineLaggedExposureSummary]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_dimension_key(row, dimensions)].append(row)
    summaries: list[MachineLaggedExposureSummary] = []
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda row: _dt(row.get("outcome_window_start")) or datetime.min.replace(tzinfo=timezone.utc))
        for metric in _pressure_metrics(items):
            high: list[float] = []
            low: list[float] = []
            paired = 0
            for idx, row in enumerate(ordered):
                started = _dt(row.get("outcome_window_start"))
                outcome = _float(row.get("outcome_value"))
                if started is None or outcome is None:
                    continue
                prior = [
                    candidate
                    for candidate in ordered[:idx]
                    if _within_lag(candidate, started=started, seconds=lag_window_seconds)
                ]
                if not prior:
                    continue
                paired += 1
                prior_high = any((_cov_float(candidate, metric) or 0.0) > 0.0 for candidate in prior)
                (high if prior_high else low).append(outcome)
            if paired < 2 or not high:
                continue
            high_median = _median(high)
            low_median = _median(low)
            delta = round(high_median - low_median, 3) if high_median is not None and low_median is not None else None
            summaries.append(
                MachineLaggedExposureSummary(
                    summary_id=_digest("lagged-exposure", ",".join(dimensions), *key, metric, lag_window_seconds),
                    dimensions=dict(zip(dimensions, key, strict=True)),
                    lag_window_seconds=lag_window_seconds,
                    pressure_metric=metric,
                    row_count=len(items),
                    paired_count=paired,
                    high_prior_pressure_count=len(high),
                    median_outcome_after_prior_pressure=high_median,
                    median_outcome_without_prior_pressure=low_median,
                    median_delta=delta,
                    caveats=(
                        "prior pressure is derived from earlier same-cohort feature rows, not continuous telemetry",
                        "lagged exposure summary is exploratory unless validated by held-out windows or controls",
                    ),
                )
            )
    summaries.sort(key=lambda row: (row.median_delta is None, -(row.median_delta or 0.0), -row.high_prior_pressure_count, row.summary_id))
    return summaries[:limit] if limit > 0 else summaries


def _anomaly_clusters(
    *,
    rows: list[dict[str, Any]],
    dimensions: tuple[str, ...],
    min_cluster_size: int,
    limit: int,
) -> list[MachineAnomalyCluster]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_dimension_key(row, dimensions)].append(row)
    clusters: list[MachineAnomalyCluster] = []
    for key, items in grouped.items():
        outcomes = [_float(row.get("outcome_value")) for row in items]
        values = sorted(value for value in outcomes if value is not None)
        if len(values) < min_cluster_size:
            continue
        threshold = _anomaly_threshold(values)
        anomalies = [
            row for row in items
            if (_float(row.get("outcome_value")) is not None and _float(row.get("outcome_value")) >= threshold)
        ]
        if len(anomalies) < min_cluster_size:
            continue
        signature = tuple(
            metric for metric in _pressure_metrics(anomalies)
            if any((_cov_float(row, metric) or 0.0) > 0.0 for row in anomalies)
        )
        clusters.append(
            MachineAnomalyCluster(
                cluster_id=_digest("anomaly-cluster", ",".join(dimensions), *key, threshold),
                dimensions=dict(zip(dimensions, key, strict=True)),
                row_count=len(items),
                anomaly_count=len(anomalies),
                outcome_threshold=round(threshold, 3),
                max_outcome=round(max(values), 3),
                pressure_signature=signature,
                representative_unit_ids=tuple(str(row.get("unit_id")) for row in anomalies[:5] if row.get("unit_id")),
                caveats=(
                    "anomaly cluster is a recurring tail signature, not root-cause proof",
                    "pressure signature is descriptive and may be concurrent with the outcome",
                ),
            )
        )
    clusters.sort(key=lambda row: (-row.anomaly_count, -row.max_outcome, row.cluster_id))
    return clusters[:limit] if limit > 0 else clusters


def _pressure_metrics(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    metrics: set[str] = set()
    for row in rows:
        covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
        metrics.update(key for key in covariates if key.startswith("host_") and "pressure" in key)
    return tuple(sorted(metrics))


def _within_lag(row: dict[str, Any], *, started: datetime, seconds: int) -> bool:
    prior_end = _dt(row.get("outcome_window_end")) or _dt(row.get("outcome_window_start"))
    if prior_end is None or prior_end > started:
        return False
    return 0.0 <= (started - prior_end).total_seconds() <= seconds


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _cov_float(row: dict[str, Any], key: str) -> float | None:
    covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
    return _float(covariates.get(key))


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _anomaly_threshold(values: list[float]) -> float:
    if len(values) < 8:
        return statistics.median(values)
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations) if deviations else 0.0
    p90 = values[min(len(values) - 1, int(len(values) * 0.90))]
    if mad > 0:
        return max(median + 3 * mad, p90)
    return values[min(len(values) - 1, int(len(values) * 0.95))]


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None



def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    idx = min(len(values) - 1, int(len(values) * 0.95))
    return round(values[idx], 3)


def _scan_id(frame_id: Any, dimensions: tuple[str, ...], row_count: int, min_cohort_size: int) -> str:
    return _digest("scan", frame_id, ",".join(dimensions), row_count, min_cohort_size)


def _cohort_id(dimensions: tuple[str, ...], key: tuple[Any, ...]) -> str:
    return _digest("cohort", ",".join(dimensions), *key)


def _digest(prefix: str, *parts: Any) -> str:
    raw = "\0".join("" if part is None else str(part) for part in parts)
    return f"machine-{prefix}:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineMiningAnalysis",
    "MachineMiningScan",
    "MachineObservationCohort",
    "analyze_machine_mining",
    "write_machine_mining",
]
