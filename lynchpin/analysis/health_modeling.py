"""Health signal modeling — alignment, regression, back-projection.

Pairs with lynchpin.sources.samsung_binning for fine-grained binning data
and lynchpin.sources.health for processed summary data.

Core capabilities:
  - align_signals: point-in-time stress matches to HRV windows + HR context
  - fit_stress_model: linear regression + RandomForest for stress-score formula
  - model_diagnostics: coefficients, feature importance, temporal CV residuals
  - back_project_hrv: estimate HRV contribution from the HR→stress residual

Construct-validity notes:
  - Samsung's stress score formula is proprietary; models here are observational
    approximations not a verified reverse-engineering.
  - HRV data starts 2025-05-21 (post Galaxy Watch firmware update); pre-2025
    HRV is unknowable from Samsung data.
  - HR-only models (R²≈0.24) explain ~1/4 of stress variance; the remainder is
    Samsung's proprietary blending, circadian factors, and measurement noise.
  - Cross-validation uses TimeSeriesSplit to avoid leaking future into past.

Example:
    from lynchpin.sources.samsung_binning import iter_stress_bins, iter_hrv_bins, iter_hr_bins
    from lynchpin.analysis.health_modeling import align_signals, fit_stress_model

    rows = align_signals(iter_stress_bins(), iter_hrv_bins(), iter_hr_bins())
    model = fit_stress_model(rows)
    print(model.summary())
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterator, Optional, Sequence

import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from ..sources.samsung_binning import HRBin, HRVBin, StressBin


# ══════════════════════════════════════════════════════════════════════════════
# Aligned signal container
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AlignedSignals:
    """One row of time-aligned cross-signal measurements.

    Stress is a point measurement; the ts is the stress bin timestamp.
    HRV and HR are from the window that contains or is nearest to that ts.
    """

    ts: datetime
    stress: float
    stress_flag: int  # Samsung validity flag: 1=valid, 0=invalid
    hr: Optional[float] = None
    sdnn: Optional[float] = None
    rmssd: Optional[float] = None
    hr_min: Optional[float] = None
    hr_max: Optional[float] = None
    hrv_window_start: Optional[datetime] = None
    hrv_window_end: Optional[datetime] = None

    @property
    def hour(self) -> int:
        return self.ts.hour

    @property
    def has_hrv(self) -> bool:
        return self.sdnn is not None and self.rmssd is not None

    @property
    def has_hr(self) -> bool:
        return self.hr is not None


# ══════════════════════════════════════════════════════════════════════════════
# Model result types
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LinearModelFit:
    """Linear regression diagnostics for stress-model fitting."""

    intercept: float
    coefficients: dict[str, float]  # feature_name → coefficient
    p_values: dict[str, float]  # feature_name → p-value (OLS t-test)
    r2: float
    adj_r2: float
    rmse: float
    mae: float
    n_samples: int
    n_features: int
    feature_names: list[str]
    residuals_mean: float
    residuals_std: float
    cv_r2_scores: list[float] = field(default_factory=list)
    cv_mean_r2: Optional[float] = None
    cv_std_r2: Optional[float] = None

    def summary(self) -> str:
        lines = [
            f"Linear Model (n={self.n_samples}, R²={self.r2:.4f}, adj-R²={self.adj_r2:.4f})",
            f"RMSE={self.rmse:.2f}, MAE={self.mae:.2f}",
            "",
            "Coefficients:",
        ]
        for name in self.feature_names:
            coef = self.coefficients[name]
            pv = self.p_values.get(name, float("nan"))
            sig = "*" if pv < 0.05 else ("**" if pv < 0.01 else ("***" if pv < 0.001 else ""))
            lines.append(f"  {name:20s} {coef:+.4f}  (p={pv:.4f}{sig})")
        lines.append(f"  {'(intercept)':20s} {self.intercept:+.4f}")
        if self.cv_mean_r2 is not None:
            lines.append(f"\nCV (TimeSeriesSplit): R²={self.cv_mean_r2:.4f} ± {self.cv_std_r2:.4f}")
        return "\n".join(lines)


@dataclass(frozen=True)
class RFModelFit:
    """RandomForest diagnostics for stress-model fitting."""

    feature_importance: dict[str, float]  # feature_name → importance (sums to 1.0)
    r2_train: float
    r2_test: float
    mae_test: float
    rmse_test: float
    n_samples: int
    n_trees: int
    cv_r2_scores: list[float] = field(default_factory=list)
    cv_mean_r2: Optional[float] = None
    cv_std_r2: Optional[float] = None

    def summary(self) -> str:
        lines = [
            f"RandomForest (n={self.n_samples}, trees={self.n_trees})",
            f"R² train={self.r2_train:.4f}, test={self.r2_test:.4f}",
            f"RMSE test={self.rmse_test:.2f}, MAE test={self.mae_test:.2f}",
            "",
            "Feature importance:",
        ]
        for name, imp in sorted(self.feature_importance.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:20s} {imp:.4f}  ({imp * 100:.1f}%)")
        if self.cv_mean_r2 is not None:
            lines.append(f"\nCV (TimeSeriesSplit): R²={self.cv_mean_r2:.4f} ± {self.cv_std_r2:.4f}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ResidualPeriod:
    """Period where model residuals are systematically off (diagnostic)."""

    start: datetime
    end: datetime
    n_points: int
    mean_residual: float  # positive = model under-predicts stress
    residual_std: float
    label: str  # human-readable anomaly description


# ══════════════════════════════════════════════════════════════════════════════
# Signal alignment
# ══════════════════════════════════════════════════════════════════════════════


def align_signals(
    stress_bins: Iterator[StressBin],
    hrv_bins: Iterator[HRVBin],
    hr_bins: Iterator[HRBin],
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    *,
    valid_only: bool = True,
) -> list[AlignedSignals]:
    """Align stress point-measurements to HRV windows and nearest HR.

    Strategy:
      - Materialize HRV bins as sorted list (sorted by ts, imported order).
      - Materialize HR bins as sorted list.
      - For each stress bin: binary-search HRV windows for containment;
        binary-search HR bins for nearest timestamp.
      - If valid_only: skip stress bins with flag=0 (Samsung invalid).

    Returns list sorted by ts.
    """
    hrv_list = sorted(hrv_bins, key=lambda h: h.ts)
    hr_list = sorted(hr_bins, key=lambda h: h.ts)

    if not hrv_list and not hr_list:
        # Nothing to align against — return stress-only rows
        stress_only: list[AlignedSignals] = []
        for sb in stress_bins:
            if start and sb.ts < start:
                continue
            if end and sb.ts > end:
                break
            if valid_only and sb.flag == 0:
                continue
            stress_only.append(AlignedSignals(ts=sb.ts, stress=sb.score, stress_flag=sb.flag))
        return stress_only

    # Pre-compute HRV interval starts for binary search
    hrv_starts = [h.ts for h in hrv_list]
    hr_timestamps = [h.ts for h in hr_list]

    rows: list[AlignedSignals] = []
    for sb in stress_bins:
        if start and sb.ts < start:
            continue
        if end and sb.ts > end:
            break
        if valid_only and sb.flag == 0:
            continue

        # Find containing HRV window via binary search on start times
        hrv = _find_containing_hrv(sb.ts, hrv_list, hrv_starts)

        # Find nearest HR measurement
        hr = _find_nearest_hr(sb.ts, hr_list, hr_timestamps)

        rows.append(
            AlignedSignals(
                ts=sb.ts,
                stress=sb.score,
                stress_flag=sb.flag,
                hr=hr.heart_rate if hr else None,
                hr_min=hr.heart_rate_min if hr else None,
                hr_max=hr.heart_rate_max if hr else None,
                sdnn=hrv.sdnn if hrv else None,
                rmssd=hrv.rmssd if hrv else None,
                hrv_window_start=hrv.ts if hrv else None,
                hrv_window_end=hrv.end_ts if hrv else None,
            )
        )

    return rows


def _find_containing_hrv(
    ts: datetime, hrv_list: list[HRVBin], hrv_starts: list[datetime]
) -> Optional[HRVBin]:
    """Binary search for the HRV window that contains ts."""
    if not hrv_list:
        return None
    idx = bisect.bisect_right(hrv_starts, ts) - 1
    if idx < 0:
        idx = 0
    # Check a few windows around the insertion point (windows can overlap)
    for i in range(max(0, idx - 2), min(len(hrv_list), idx + 3)):
        h = hrv_list[i]
        if h.ts <= ts <= h.end_ts:
            return h
    return None


def _find_nearest_hr(
    ts: datetime, hr_list: list[HRBin], hr_timestamps: list[datetime]
) -> Optional[HRBin]:
    """Binary search for nearest HR measurement to ts."""
    if not hr_list:
        return None
    idx = bisect.bisect_left(hr_timestamps, ts)
    # Check candidate positions
    candidates = []
    for i in range(max(0, idx - 2), min(len(hr_list), idx + 2)):
        candidates.append((abs((hr_list[i].ts - ts).total_seconds()), hr_list[i]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    if candidates[0][0] < 300:  # within 5 minutes
        return candidates[0][1]
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Feature matrix construction
# ══════════════════════════════════════════════════════════════════════════════


def rows_to_matrix(
    rows: list[AlignedSignals],
    features: Sequence[str],
    target: str = "stress",
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Convert aligned rows to X (n×f float64) and y (n×1) arrays.

    Only returns rows where all requested feature values are non-None.
    The returned arrays are dense: missing values cause the row to be dropped.

    Args:
        rows: aligned signal rows from align_signals()
        features: list of feature names — "hr", "sdnn", "rmssd", "hour", etc.
        target: column name for y — "stress" (default), "sdnn", "rmssd"

    Returns:
        X: (n_samples, n_features) float64 ndarray
        y: (n_samples,) float64 ndarray
    """
    _FEATURE_ACCESSORS: dict[str, Callable[[AlignedSignals], Any]] = {
        "hr": lambda r: r.hr,
        "sdnn": lambda r: r.sdnn,
        "rmssd": lambda r: r.rmssd,
        "hr_min": lambda r: r.hr_min,
        "hr_max": lambda r: r.hr_max,
        "hour": lambda r: r.hour,
        "stress": lambda r: r.stress,
    }

    accessors = []
    for f in features:
        if f not in _FEATURE_ACCESSORS:
            raise KeyError(f"Unknown feature '{f}'. Available: {sorted(_FEATURE_ACCESSORS)}")
        accessors.append(_FEATURE_ACCESSORS[f])

    target_fn = _FEATURE_ACCESSORS.get(target)
    if target_fn is None:
        raise KeyError(f"Unknown target '{target}'")

    xs: list[list[float]] = []
    ys: list[float] = []

    for row in rows:
        vals = [fn(row) for fn in accessors]
        yv = target_fn(row)
        if any(v is None for v in vals) or yv is None:
            continue
        xs.append(vals)
        ys.append(yv)

    return np.array(xs, dtype=np.float64), np.array(ys, dtype=np.float64)


# ══════════════════════════════════════════════════════════════════════════════
# Model fitting
# ══════════════════════════════════════════════════════════════════════════════


def fit_linear_model(
    rows: list[AlignedSignals],
    features: Sequence[str] = ("hr", "sdnn", "rmssd"),
    *,
    cv_splits: int = 5,
) -> LinearModelFit:
    """Fit OLS linear regression with temporal cross-validation.

    Features default: hr + sdnn + rmssd (the physiologically relevant set).
    CV uses TimeSeriesSplit to avoid leaking future data into past folds.
    """
    X, y = rows_to_matrix(rows, features, target="stress")

    if len(X) == 0:
        raise ValueError("No complete rows for the requested features")

    # Fit
    model = LinearRegression(fit_intercept=True)
    model.fit(X, y)

    residuals = y - model.predict(X)
    r2 = model.score(X, y)
    n, p = X.shape
    adj_r2 = 1 - (1 - r2) * (n - 1) / max(1, n - p - 1)
    rmse = math.sqrt(mean_squared_error(y, model.predict(X)))
    mae = mean_absolute_error(y, model.predict(X))

    # P-values via OLS t-test
    p_values = _ols_pvalues(X, y, model.coef_, model.intercept_, residuals, features)

    coefs = {name: float(model.coef_[i]) for i, name in enumerate(features)}

    # Temporal CV
    cv_scores: list[float] = []
    if len(X) >= cv_splits * 2:
        tscv = TimeSeriesSplit(n_splits=cv_splits)
        for train_idx, test_idx in tscv.split(X):
            X_tr, X_te = X[train_idx], X[test_idx]
            y_tr, y_te = y[train_idx], y[test_idx]
            cv_m = LinearRegression(fit_intercept=True)
            cv_m.fit(X_tr, y_tr)
            cv_scores.append(cv_m.score(X_te, y_te))

    return LinearModelFit(
        intercept=float(model.intercept_),
        coefficients=coefs,
        p_values=p_values,
        r2=float(r2),
        adj_r2=float(adj_r2),
        rmse=float(rmse),
        mae=float(mae),
        n_samples=n,
        n_features=p,
        feature_names=list(features),
        residuals_mean=float(np.mean(residuals)),
        residuals_std=float(np.std(residuals)),
        cv_r2_scores=[float(s) for s in cv_scores],
        cv_mean_r2=float(np.mean(cv_scores)) if cv_scores else None,
        cv_std_r2=float(np.std(cv_scores)) if cv_scores else None,
    )


def fit_rf_model(
    rows: list[AlignedSignals],
    features: Sequence[str] = ("hr", "sdnn", "rmssd"),
    *,
    n_estimators: int = 200,
    test_size: float = 0.2,
    random_state: int = 42,
    cv_splits: int = 5,
) -> RFModelFit:
    """Fit RandomForest regressor with train/test split and CV.

    Uses a temporal split (first 80% train, last 20% test) as the
    primary evaluation, plus TimeSeriesSplit for CV.
    """
    X, y = rows_to_matrix(rows, features, target="stress")

    if len(X) < 10:
        raise ValueError(f"Need at least 10 rows, got {len(X)}")

    n = len(X)
    split_idx = int(n * (1 - test_size))

    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    model = RandomForestRegressor(
        n_estimators=n_estimators,
        random_state=random_state,
        n_jobs=-1,
        max_features="sqrt",
    )
    model.fit(X_train, y_train)

    y_pred_test = model.predict(X_test)
    y_pred_train = model.predict(X_train)

    r2_train = float(r2_score(y_train, y_pred_train))
    r2_test = float(r2_score(y_test, y_pred_test))
    mae_test = float(mean_absolute_error(y_test, y_pred_test))
    rmse_test = float(math.sqrt(mean_squared_error(y_test, y_pred_test)))

    importance = {name: float(model.feature_importances_[i]) for i, name in enumerate(features)}

    # Temporal CV
    cv_scores: list[float] = []
    if len(X) >= cv_splits * 2:
        tscv = TimeSeriesSplit(n_splits=cv_splits)
        for train_idx, test_idx in tscv.split(X):
            X_tr, X_te = X[train_idx], X[test_idx]
            y_tr, y_te = y[train_idx], y[test_idx]
            cv_m = RandomForestRegressor(
                n_estimators=n_estimators,
                random_state=random_state,
                n_jobs=-1,
            )
            cv_m.fit(X_tr, y_tr)
            cv_scores.append(float(cv_m.score(X_te, y_te)))

    return RFModelFit(
        feature_importance=importance,
        r2_train=r2_train,
        r2_test=r2_test,
        mae_test=mae_test,
        rmse_test=rmse_test,
        n_samples=n,
        n_trees=n_estimators,
        cv_r2_scores=[float(s) for s in cv_scores],
        cv_mean_r2=float(np.mean(cv_scores)) if cv_scores else None,
        cv_std_r2=float(np.std(cv_scores)) if cv_scores else None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Stepwise fitting (compare feature subsets)
# ══════════════════════════════════════════════════════════════════════════════


def compare_feature_subsets(
    rows: list[AlignedSignals],
    feature_sets: dict[str, Sequence[str]],
) -> dict[str, LinearModelFit]:
    """Fit linear model for each feature subset; return ranked by adj-R².

    Args:
        rows: aligned signal rows
        feature_sets: label → list of feature names
          Example: {"HR only": ["hr"], "HR+SDNN": ["hr", "sdnn"], ...}

    Returns:
        dict label → LinearModelFit, ordered by adj_r2 descending
    """
    results = {}
    for label, features in feature_sets.items():
        results[label] = fit_linear_model(rows, features)
    return dict(sorted(results.items(), key=lambda kv: -kv[1].adj_r2))


# ══════════════════════════════════════════════════════════════════════════════
# Residual analysis
# ══════════════════════════════════════════════════════════════════════════════


def find_residual_periods(
    rows: list[AlignedSignals],
    features: Sequence[str] = ("hr",),
    *,
    threshold_sigma: float = 2.0,
    min_gap_minutes: int = 60,
    min_period_points: int = 10,
) -> list[ResidualPeriod]:
    """Find periods where stress residuals are systematically large.

    Only rows with complete feature data are used. Residuals are indexed
    by position in the filtered subset, so the returned ResidualPeriod
    timestamps are from the filtered rows.
    """
    # Build filtered list of rows that have all features
    filtered_rows: list[AlignedSignals] = []
    for row in rows:
        vals: list[Optional[float]] = []
        for f in features:
            if f == "hr":
                vals.append(row.hr)
            elif f == "sdnn":
                vals.append(row.sdnn)
            elif f == "rmssd":
                vals.append(row.rmssd)
        if any(v is None for v in vals):
            continue
        filtered_rows.append(row)

    if len(filtered_rows) < 20:
        return []

    X, y = rows_to_matrix(filtered_rows, features, target="stress")
    model = LinearRegression(fit_intercept=True)
    model.fit(X, y)
    residuals = y - model.predict(X)
    std = float(np.std(residuals))
    threshold = threshold_sigma * std

    # Group consecutive anomalous points into periods
    periods: list[ResidualPeriod] = []
    current_idxs: list[int] = []

    def _flush() -> None:
        nonlocal current_idxs
        if len(current_idxs) >= min_period_points:
            r_vals = [float(residuals[i]) for i in current_idxs]
            t_start = filtered_rows[current_idxs[0]].ts
            t_end = filtered_rows[current_idxs[-1]].ts
            mean_r = statistics.mean(r_vals)
            direction = "over-predicts" if mean_r < 0 else "under-predicts"
            stdev_r = statistics.stdev(r_vals) if len(r_vals) > 1 else 0.0
            periods.append(
                ResidualPeriod(
                    start=t_start,
                    end=t_end,
                    n_points=len(current_idxs),
                    mean_residual=mean_r,
                    residual_std=stdev_r,
                    label=f"Model {direction} stress (|μ|={abs(mean_r):.1f}, σ={stdev_r:.1f})",
                )
            )
        current_idxs = []

    for i in range(len(filtered_rows)):
        r = float(residuals[i])
        if abs(r) > threshold:
            if current_idxs and (filtered_rows[i].ts - filtered_rows[current_idxs[-1]].ts).total_seconds() > min_gap_minutes * 60:
                _flush()
                current_idxs = [i]
            else:
                current_idxs.append(i)
        else:
            _flush()

    _flush()
    return periods


# ══════════════════════════════════════════════════════════════════════════════
# Back-projection (HRV estimation from stress residual)
# ══════════════════════════════════════════════════════════════════════════════


def hr_contribution_model(
    rows: list[AlignedSignals],
) -> tuple[LinearModelFit, NDArray[np.float64], NDArray[np.float64]]:
    """Fit stress←HR model; return model and the residual (HRV contribution).

    The residual = actual_stress - HR_predicted_stress is the portion of
    the stress score that HR alone cannot explain. When HRV is available,
    this residual correlates with HRV metrics. For periods without HRV,
    the residual can be bounded but not decomposed.

    Returns:
        (model, residuals, predicted) — residuals are y - ŷ
    """
    model = fit_linear_model(rows, features=["hr"])
    X, y = rows_to_matrix(rows, ["hr"], target="stress")
    predicted = model.intercept + model.coefficients["hr"] * X[:, 0]
    residuals = y - predicted
    return model, residuals, predicted


def residual_hrv_correlation(
    rows: list[AlignedSignals],
) -> dict[str, float]:
    """Correlate the HR→stress residual with HRV metrics.

    Only uses rows where HRV is available. Positive correlation means
    higher HRV → higher residual (stress higher than HR predicts).
    """

    usable = [r for r in rows if r.has_hrv and r.has_hr]
    if len(usable) < 10:
        return {}

    X, y = rows_to_matrix(usable, ["hr"], target="stress")
    residual = y - (
        LinearRegression(fit_intercept=True).fit(X, y).predict(X)
    )

    sdnn_vals = [r.sdnn for r in usable if r.sdnn is not None]
    rmssd_vals = [r.rmssd for r in usable if r.rmssd is not None]
    # Only use rows where HRV is also available for the same residual index
    if len(sdnn_vals) < 10:
        return {}

    return {
        "residual_vs_sdnn": float(np.corrcoef(residual[:len(sdnn_vals)], sdnn_vals)[0, 1]),
        "residual_vs_rmssd": float(np.corrcoef(residual[:len(rmssd_vals)], rmssd_vals)[0, 1]),
        "n_rows": len(usable),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Hourly aggregation (for joining with AW / polylogue / substance sources)
# ══════════════════════════════════════════════════════════════════════════════


def hourly_aggregates(
    rows: list[AlignedSignals],
) -> list[dict[str, object]]:
    """Aggregate aligned signals to hourly buckets for cross-source joins.

    Returns one dict per hour with: hour (datetime), stress_mean, stress_median,
    stress_min, stress_max, hr_mean, sdnn_mean, rmssd_mean, n_points.
    """
    buckets: dict[datetime, list[AlignedSignals]] = defaultdict(list)

    for row in rows:
        hour_key = row.ts.replace(minute=0, second=0, microsecond=0)
        buckets[hour_key].append(row)

    result = []
    for hour in sorted(buckets):
        bucket = buckets[hour]
        stresses = [r.stress for r in bucket]
        hrs = [r.hr for r in bucket if r.hr is not None]
        sdnns = [r.sdnn for r in bucket if r.sdnn is not None]
        rmssds = [r.rmssd for r in bucket if r.rmssd is not None]

        result.append(
            {
                "hour": hour,
                "n_points": len(bucket),
                "stress_mean": statistics.mean(stresses),
                "stress_median": statistics.median(stresses),
                "stress_min": min(stresses),
                "stress_max": max(stresses),
                "hr_mean": statistics.mean(hrs) if hrs else None,
                "sdnn_mean": statistics.mean(sdnns) if sdnns else None,
                "rmssd_mean": statistics.mean(rmssds) if rmssds else None,
            }
        )

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Quick diagnostics — pull it all together
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StressModelReport:
    """Complete stress-score modeling report."""

    n_aligned_total: int
    n_aligned_with_hrv: int
    n_aligned_with_hr: int
    hr_only: LinearModelFit
    feature_comparison: dict[str, LinearModelFit]
    rf: Optional[RFModelFit] = None
    residual_periods: list[ResidualPeriod] = field(default_factory=list)
    residual_hrv_corr: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            "Stress Model Report",
            "──────────────────",
            f"Aligned rows: {self.n_aligned_total} total, "
            f"{self.n_aligned_with_hrv} with HRV, "
            f"{self.n_aligned_with_hr} with HR",
            "",
            "── Feature subset comparison ──",
        ]
        for label, m in self.feature_comparison.items():
            lines.append(f"  {label}: R²={m.r2:.4f}, adj-R²={m.adj_r2:.4f}, RMSE={m.rmse:.2f}")

        lines.append("")
        lines.append("── HR-only model ──")
        lines.append(self.hr_only.summary())

        if self.rf is not None:
            lines.append("")
            lines.append("── RandomForest ──")
            lines.append(self.rf.summary())

        if self.residual_periods:
            lines.append("")
            lines.append(f"── Anomalous residual periods ({len(self.residual_periods)}) ──")
            for rp in self.residual_periods[:10]:
                lines.append(f"  {rp.start:%Y-%m-%d %H:%M} → {rp.end:%Y-%m-%d %H:%M}: {rp.label}")

        if self.residual_hrv_corr:
            lines.append("")
            lines.append("── Residual × HRV correlation ──")
            for k, v in self.residual_hrv_corr.items():
                lines.append(f"  {k}: {v:+.4f}")

        return "\n".join(lines)


def build_report(
    rows: list[AlignedSignals],
    *,
    rf: bool = False,
) -> StressModelReport:
    """Quick comprehensive stress-model diagnostics.

    Args:
        rows: aligned signal rows from align_signals()
        rf: if True, also fit RandomForest (adds computation time)

    Returns:
        StressModelReport with all diagnostics
    """
    feature_sets: dict[str, Sequence[str]] = {
        "HR only": ["hr"],
        "HR+SDNN": ["hr", "sdnn"],
        "HR+RMSSD": ["hr", "rmssd"],
        "HR+SDNN+RMSSD": ["hr", "sdnn", "rmssd"],
    }
    if not any(r.has_hrv for r in rows):
        feature_sets = {"HR only": ["hr"]}

    comparison = compare_feature_subsets(rows, feature_sets)
    hr_only = comparison.get("HR only") or fit_linear_model(rows, features=["hr"])
    residual_periods = find_residual_periods(rows, features=["hr"])
    residual_hrv = residual_hrv_correlation(rows) if any(r.has_hrv for r in rows) else {}

    rf_model = None
    if rf and any(r.has_hrv for r in rows):
        try:
            rf_model = fit_rf_model(rows, features=["hr", "sdnn", "rmssd"])
        except ValueError:
            pass

    return StressModelReport(
        n_aligned_total=len(rows),
        n_aligned_with_hrv=sum(1 for r in rows if r.has_hrv),
        n_aligned_with_hr=sum(1 for r in rows if r.has_hr),
        hr_only=hr_only,
        feature_comparison=comparison,
        rf=rf_model,
        residual_periods=residual_periods,
        residual_hrv_corr=residual_hrv,
    )


# ══════════════════════════════════════════════════════════════════════════════
# OLS p-values (internal)
# ══════════════════════════════════════════════════════════════════════════════


def _ols_pvalues(
    X: NDArray[np.float64],
    y: NDArray[np.float64],
    coef: NDArray[np.float64],
    intercept: float,
    residuals: NDArray[np.float64],
    feature_names: Sequence[str],
) -> dict[str, float]:
    """Compute two-sided t-test p-values for OLS coefficients."""
    n, p = X.shape
    df = n - p - 1
    if df < 1:
        return {name: float("nan") for name in feature_names}

    # Add intercept column to design matrix
    X_aug = np.column_stack([np.ones(n), X])
    rss = float(residuals @ residuals)
    sigma2 = rss / df

    try:
        cov = sigma2 * np.linalg.inv(X_aug.T @ X_aug)
        se = np.sqrt(np.diag(cov))
    except np.linalg.LinAlgError:
        return {name: float("nan") for name in feature_names}

    # se[0] = intercept SE; se[1:] = coefficient SEs
    try:
        from scipy.stats import t as t_dist
    except ImportError:
        return {name: float("nan") for name in feature_names}

    p_values: dict[str, float] = {}
    for i, name in enumerate(feature_names):
        t_stat = float(abs(coef[i]) / max(float(se[i + 1]), 1e-10))
        p_val: float = 2.0 * float(t_dist.sf(t_stat, df))
        p_values[name] = p_val

    return p_values


__all__ = [
    "AlignedSignals",
    "LinearModelFit",
    "RFModelFit",
    "ResidualPeriod",
    "StressModelReport",
    "align_signals",
    "rows_to_matrix",
    "fit_linear_model",
    "fit_rf_model",
    "compare_feature_subsets",
    "find_residual_periods",
    "hr_contribution_model",
    "residual_hrv_correlation",
    "hourly_aggregates",
    "build_report",
]
