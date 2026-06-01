"""Observational cohort contrasts for machine attribution mining."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import random
import statistics
from pathlib import Path
from typing import Any

from lynchpin.core.analytics import _benjamini_hochberg
from lynchpin.core.io import load_json_object, resolve_analysis_path, save_json


@dataclass(frozen=True)
class MachineCohortContrast:
    contrast_id: str
    scan_id: str
    cohort_id: str
    dimensions: dict[str, Any]
    outcome_metric: str
    treated_n: int
    comparison_n: int
    treated_median: float | None
    comparison_median: float | None
    median_delta: float | None
    median_ratio: float | None
    cliffs_delta: float | None
    bootstrap_ci_95: tuple[float | None, float | None]
    mann_whitney_p: float | None
    q_value: float | None
    statistical_signal: str
    support_ceiling: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineComparisonAnalysis:
    generated_for: dict[str, Any]
    contrast_count: int
    contrasts: list[MachineCohortContrast]
    multiplicity_policy: str
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_comparisons(
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    mining_path: Path | None = None,
    bootstrap_iterations: int = 500,
    limit: int = 100,
) -> MachineComparisonAnalysis:
    frame_payload = load_json_object(
        feature_frames_path or resolve_analysis_path("machine_analysis_feature_frames.json"),
        label="machine analysis feature frames",
    )
    mining_payload = load_json_object(
        mining_path or resolve_analysis_path("machine_mining.json"),
        label="machine mining",
    )
    frame = frame_payload.get("frame") if isinstance(frame_payload.get("frame"), dict) else {}
    rows = [row for row in frame.get("rows", []) if isinstance(row, dict)]
    cohorts = [row for row in mining_payload.get("cohorts", []) if isinstance(row, dict)]
    dimensions = tuple(
        mining_payload.get("scan", {}).get("dimensions", ())
        if isinstance(mining_payload.get("scan"), dict)
        else ()
    )
    if not dimensions:
        dimensions = ("stage_name", "project")
    grouped = _observed_groups(rows, dimensions)
    all_values = [value for values in grouped.values() for value in values]
    contrasts = [
        _contrast(
            cohort=cohort,
            dimensions=dimensions,
            treated=grouped.get(_dimension_key_from_mapping(cohort.get("dimensions"), dimensions), []),
            comparison=_comparison_values(
                grouped,
                exclude=_dimension_key_from_mapping(cohort.get("dimensions"), dimensions),
            ),
            outcome_metric=str(frame.get("outcome_metric") or "stage.duration_s"),
            bootstrap_iterations=bootstrap_iterations,
        )
        for cohort in cohorts
    ]
    q_values = _benjamini_hochberg({
        idx: row.mann_whitney_p
        for idx, row in enumerate(contrasts)
        if row.mann_whitney_p is not None
    })
    contrasts = [_with_q(row, q_values.get(idx)) for idx, row in enumerate(contrasts)]
    contrasts.sort(
        key=lambda row: (
            row.statistical_signal != "screening_signal",
            -(abs(row.median_delta or 0.0)),
            -(row.treated_n + row.comparison_n),
            row.contrast_id,
        )
    )
    if limit > 0:
        contrasts = contrasts[:limit]
    return MachineComparisonAnalysis(
        generated_for={
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "source": ["machine_analysis_feature_frames.json", "machine_mining.json"],
            "unit": frame.get("unit_type"),
            "outcome_metric": frame.get("outcome_metric"),
            "observed_value_count": len(all_values),
            "bootstrap_iterations": bootstrap_iterations,
            "limit": limit,
        },
        contrast_count=len(contrasts),
        contrasts=contrasts,
        multiplicity_policy="Benjamini-Hochberg q-values over emitted cohort-vs-rest contrasts; exploratory only",
        caveats=[
            "cohort-vs-rest contrasts are observational and unmatched",
            "censored/failed rows are excluded from numeric estimates and retained only in cohort caveats",
            "bootstrap intervals quantify sampling variation, not causal identification",
        ],
    )


def write_machine_comparisons(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    feature_frames_path: Path | None = None,
    mining_path: Path | None = None,
    bootstrap_iterations: int = 500,
    limit: int = 100,
) -> MachineComparisonAnalysis:
    analysis = analyze_machine_comparisons(
        start=start,
        end=end,
        feature_frames_path=feature_frames_path,
        mining_path=mining_path,
        bootstrap_iterations=bootstrap_iterations,
        limit=limit,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _observed_groups(
    rows: list[dict[str, Any]],
    dimensions: tuple[str, ...],
) -> dict[tuple[Any, ...], list[float]]:
    grouped: dict[tuple[Any, ...], list[float]] = defaultdict(list)
    for row in rows:
        if row.get("censoring_status") != "observed":
            continue
        value = _float_or_none(row.get("outcome_value"))
        if value is None:
            continue
        grouped[_dimension_key(row, dimensions)].append(value)
    return dict(grouped)


def _contrast(
    *,
    cohort: dict[str, Any],
    dimensions: tuple[str, ...],
    treated: list[float],
    comparison: list[float],
    outcome_metric: str,
    bootstrap_iterations: int,
) -> MachineCohortContrast:
    cohort_id = str(cohort.get("cohort_id") or "")
    scan_id = str(cohort.get("scan_id") or "")
    treated_median = _median(treated)
    comparison_median = _median(comparison)
    median_delta = (
        round(treated_median - comparison_median, 6)
        if treated_median is not None and comparison_median is not None
        else None
    )
    ratio = (
        round(treated_median / comparison_median, 6)
        if treated_median is not None and comparison_median not in (None, 0.0)
        else None
    )
    p_value, cliffs = _rank_test(treated, comparison)
    ci = _bootstrap_median_delta_ci(
        treated,
        comparison,
        seed=_seed(cohort_id, scan_id, dimensions),
        iterations=bootstrap_iterations,
    )
    caveats = [
        "comparison is rest-of-frame, not matched or randomized",
        "contrast remains candidate-level even when statistically strong",
    ]
    if len(treated) < 5 or len(comparison) < 5:
        caveats.append("small sample; interval and rank test are unstable")
    return MachineCohortContrast(
        contrast_id=_contrast_id(cohort_id, scan_id),
        scan_id=scan_id,
        cohort_id=cohort_id,
        dimensions=dict(cohort.get("dimensions") or {}),
        outcome_metric=outcome_metric,
        treated_n=len(treated),
        comparison_n=len(comparison),
        treated_median=treated_median,
        comparison_median=comparison_median,
        median_delta=median_delta,
        median_ratio=ratio,
        cliffs_delta=cliffs,
        bootstrap_ci_95=ci,
        mann_whitney_p=p_value,
        q_value=None,
        statistical_signal="pending_multiplicity",
        support_ceiling="candidate",
        caveats=tuple(caveats),
    )


def _with_q(row: MachineCohortContrast, q_value: float | None) -> MachineCohortContrast:
    ci_low, ci_high = row.bootstrap_ci_95
    excludes_zero = (
        ci_low is not None
        and ci_high is not None
        and ((ci_low > 0 and ci_high > 0) or (ci_low < 0 and ci_high < 0))
    )
    signal = (
        "screening_signal"
        if q_value is not None
        and q_value <= 0.10
        and excludes_zero
        and row.treated_n >= 5
        and row.comparison_n >= 5
        else "descriptive"
    )
    return MachineCohortContrast(
        **{**asdict(row), "q_value": _round_probability(q_value) if q_value is not None else None, "statistical_signal": signal}
    )


def _comparison_values(grouped: dict[tuple[Any, ...], list[float]], *, exclude: tuple[Any, ...]) -> list[float]:
    return [value for key, values in grouped.items() if key != exclude for value in values]


def _dimension_key(row: dict[str, Any], dimensions: tuple[str, ...]) -> tuple[Any, ...]:
    covariates = row.get("covariates") if isinstance(row.get("covariates"), dict) else {}
    return tuple(row.get(dimension, covariates.get(dimension)) for dimension in dimensions)


def _dimension_key_from_mapping(value: object, dimensions: tuple[str, ...]) -> tuple[Any, ...]:
    mapping = value if isinstance(value, dict) else {}
    return tuple(mapping.get(dimension) for dimension in dimensions)


def _rank_test(treated: list[float], comparison: list[float]) -> tuple[float | None, float | None]:
    if len(treated) < 2 or len(comparison) < 2:
        return None, None
    try:
        from scipy.stats import mannwhitneyu  # type: ignore[import-untyped]

        result = mannwhitneyu(treated, comparison, alternative="two-sided")
        u_stat = float(result.statistic)
        p_value = float(result.pvalue)
    except Exception:
        u_stat = _mann_whitney_u(treated, comparison)
        p_value = None
    cliffs = (2.0 * u_stat / (len(treated) * len(comparison))) - 1.0
    return _round_probability(p_value) if p_value is not None else None, round(cliffs, 6)


def _mann_whitney_u(a: list[float], b: list[float]) -> float:
    wins = 0.0
    for left in a:
        for right in b:
            if left > right:
                wins += 1.0
            elif left == right:
                wins += 0.5
    return wins


def _bootstrap_median_delta_ci(
    treated: list[float],
    comparison: list[float],
    *,
    seed: int,
    iterations: int,
) -> tuple[float | None, float | None]:
    if not treated or not comparison or iterations <= 0:
        return (None, None)
    try:
        return _numpy_bootstrap_median_delta_ci(
            treated,
            comparison,
            seed=seed,
            iterations=iterations,
        )
    except Exception:
        pass
    rng = random.Random(seed)
    deltas = []
    for _ in range(iterations):
        t_sample = [treated[rng.randrange(len(treated))] for _ in treated]
        c_sample = [comparison[rng.randrange(len(comparison))] for _ in comparison]
        deltas.append(statistics.median(t_sample) - statistics.median(c_sample))
    deltas.sort()
    lo = deltas[max(0, int(iterations * 0.025))]
    hi = deltas[min(len(deltas) - 1, int(iterations * 0.975))]
    return (round(lo, 6), round(hi, 6))


def _numpy_bootstrap_median_delta_ci(
    treated: list[float],
    comparison: list[float],
    *,
    seed: int,
    iterations: int,
) -> tuple[float | None, float | None]:
    import numpy as np

    rng = np.random.default_rng(seed)
    treated_arr = np.asarray(treated, dtype=float)
    comparison_arr = np.asarray(comparison, dtype=float)
    treated_idx = rng.integers(0, len(treated_arr), size=(iterations, len(treated_arr)))
    comparison_idx = rng.integers(0, len(comparison_arr), size=(iterations, len(comparison_arr)))
    deltas = (
        np.median(treated_arr[treated_idx], axis=1)
        - np.median(comparison_arr[comparison_idx], axis=1)
    )
    quantiles = np.quantile(deltas, [0.025, 0.975])
    return (round(float(quantiles[0]), 6), round(float(quantiles[1]), 6))


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round_probability(value: float) -> float:
    if value <= 0:
        return 1e-300
    return float(f"{value:.6g}")


def _seed(*parts: Any) -> int:
    return int(hashlib.sha1("\0".join(str(part) for part in parts).encode()).hexdigest()[:8], 16)


def _contrast_id(*parts: Any) -> str:
    raw = "\0".join(str(part) for part in parts)
    return f"machine-contrast:{hashlib.sha1(raw.encode()).hexdigest()[:16]}"


__all__ = [
    "MachineCohortContrast",
    "MachineComparisonAnalysis",
    "analyze_machine_comparisons",
    "write_machine_comparisons",
]
