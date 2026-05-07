"""Predictive readiness model: deterministic forecast of next-day deep work
capacity from rolling correlations of sleep + health + prior-day focus.

This is the first forward-looking layer in the lynchpin pipeline. It is
explicitly heuristic — historical OLS over a short window — and exposes
all coefficients so the user can sanity-check or override the model.

No LLM, no external API. Numpy-only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

DEFAULT_WINDOW_DAYS = 60
MIN_SAMPLE_N = 30
MIN_R_SQUARED = 0.25
FEATURE_NAMES = (
    "sleep_hours",
    "sleep_score",
    "hrv_rmssd",
    "resting_hr",
    "prior_focus_hours",
    "prior_deep_work_min",
)


@dataclass(frozen=True)
class ReadinessForecast:
    target_date: date
    predicted_deep_work_min: float
    confidence_interval_95: tuple[float, float]
    inputs: dict[str, float]
    coefficients: dict[str, float]
    intercept: float
    r_squared: float
    sample_n: int
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class ReadinessUnavailable:
    target_date: date
    reason: str
    sample_n: int
    r_squared: float | None


def build_readiness_forecast(
    *,
    target_date: date,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> ReadinessForecast | ReadinessUnavailable:
    """Build a forecast for ``target_date``.

    Uses the prior ``window_days`` of joined sleep + health + AW data as
    training rows. Target is next-day deep work minutes.
    """
    history_start = target_date - timedelta(days=window_days)
    history_end = target_date - timedelta(days=1)

    rows = _build_training_rows(start=history_start, end=history_end)
    if len(rows) < MIN_SAMPLE_N:
        return ReadinessUnavailable(
            target_date=target_date,
            reason=f"insufficient history: only {len(rows)} usable rows (need {MIN_SAMPLE_N})",
            sample_n=len(rows),
            r_squared=None,
        )

    features = _stack_features(rows)
    targets = _stack_targets(rows)
    coeffs, intercept, r_squared, residual_std = _ols(features, targets)

    if r_squared < MIN_R_SQUARED:
        return ReadinessUnavailable(
            target_date=target_date,
            reason=f"model fit too weak: r²={r_squared:.3f} (threshold {MIN_R_SQUARED})",
            sample_n=len(rows),
            r_squared=r_squared,
        )

    forecast_inputs = _features_for_target(target_date)
    if forecast_inputs is None:
        return ReadinessUnavailable(
            target_date=target_date,
            reason="missing input features for target date (no recent sleep entry)",
            sample_n=len(rows),
            r_squared=r_squared,
        )

    feature_vec = [forecast_inputs[name] for name in FEATURE_NAMES]
    prediction = intercept + sum(c * f for c, f in zip(coeffs, feature_vec))
    # 95% CI ≈ ± 1.96 * residual_std (Gaussian approximation; no leverage adjustment).
    ci_low = max(0.0, prediction - 1.96 * residual_std)
    ci_high = max(0.0, prediction + 1.96 * residual_std)

    caveats: list[str] = []
    if window_days < 60:
        caveats.append("short window — predictions volatile to outliers")
    if any(abs(c) > 200 for c in coeffs):
        caveats.append("at least one coefficient is large; model may be overfit")
    caveats.append(
        "model is heuristic OLS over recent days; coefficients exposed for inspection"
    )

    return ReadinessForecast(
        target_date=target_date,
        predicted_deep_work_min=round(max(0.0, prediction), 1),
        confidence_interval_95=(round(ci_low, 1), round(ci_high, 1)),
        inputs={k: round(v, 3) for k, v in forecast_inputs.items()},
        coefficients={name: round(c, 4) for name, c in zip(FEATURE_NAMES, coeffs)},
        intercept=round(intercept, 3),
        r_squared=round(r_squared, 4),
        sample_n=len(rows),
        caveats=tuple(caveats),
    )


# ── Training data assembly ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _TrainingRow:
    sleep_date: date
    workday: date
    sleep_hours: float
    sleep_score: float
    hrv_rmssd: float
    resting_hr: float
    prior_focus_hours: float
    prior_deep_work_min: float
    target_deep_work_min: float


def _build_training_rows(*, start: date, end: date) -> list[_TrainingRow]:
    from ..sources.activitywatch import daily_activity
    from ..sources.health import daily_health_summary
    from ..sources.sleep import sleep_productivity

    sp = sleep_productivity(start=start, end=end)
    if not sp:
        return []
    sp_by_date = {row.sleep_date: row for row in sp if row.sleep_score is not None}

    health_by_date = {
        row.date: row
        for row in daily_health_summary(start=start, end=end)
    }

    aw_by_date = {row.date: row for row in daily_activity(start=start, end=end)}

    rows: list[_TrainingRow] = []
    for sleep_date, sp_row in sorted(sp_by_date.items()):
        workday = sleep_date + timedelta(days=1)
        target = sp_row.workday_deep_work_min
        if target is None:
            continue
        hr = health_by_date.get(sleep_date)
        prior_aw = aw_by_date.get(sleep_date)
        if hr is None or prior_aw is None:
            continue
        if hr.hrv_rmssd_avg is None or hr.heart_rate_resting is None:
            continue
        if sp_row.sleep_score is None:
            continue
        rows.append(
            _TrainingRow(
                sleep_date=sleep_date,
                workday=workday,
                sleep_hours=float(sp_row.sleep_hours),
                sleep_score=float(sp_row.sleep_score),
                hrv_rmssd=float(hr.hrv_rmssd_avg),
                resting_hr=float(hr.heart_rate_resting),
                prior_focus_hours=float(prior_aw.active_hours),
                prior_deep_work_min=float(prior_aw.deep_work_min),
                target_deep_work_min=float(target),
            )
        )
    return rows


def _features_for_target(target_date: date) -> dict[str, float] | None:
    """Build the feature row used for the forecast itself.

    The "sleep" inputs come from the night before ``target_date`` (i.e.
    ``target_date - 1``). ``prior_focus`` and ``prior_deep_work`` are from
    that same prior day.
    """
    from ..sources.activitywatch import daily_activity
    from ..sources.health import daily_health_summary
    from ..sources.sleep import sleep_for_date

    sleep_date = target_date - timedelta(days=1)
    entry = sleep_for_date(sleep_date)
    if entry is None or entry.avg_score is None or entry.total_minutes is None:
        return None
    health = next(
        (r for r in daily_health_summary(start=sleep_date, end=sleep_date)),
        None,
    )
    if health is None or health.hrv_rmssd_avg is None or health.heart_rate_resting is None:
        return None
    prior_day = next(
        (r for r in daily_activity(start=sleep_date, end=sleep_date)),
        None,
    )
    if prior_day is None:
        return None
    return {
        "sleep_hours": float(entry.total_minutes) / 60.0,
        "sleep_score": float(entry.avg_score),
        "hrv_rmssd": float(health.hrv_rmssd_avg),
        "resting_hr": float(health.heart_rate_resting),
        "prior_focus_hours": float(prior_day.active_hours),
        "prior_deep_work_min": float(prior_day.deep_work_min),
    }


# ── Linear algebra (OLS via numpy) ───────────────────────────────────────────


def _stack_features(rows: Sequence[_TrainingRow]) -> list[list[float]]:
    return [
        [
            r.sleep_hours,
            r.sleep_score,
            r.hrv_rmssd,
            r.resting_hr,
            r.prior_focus_hours,
            r.prior_deep_work_min,
        ]
        for r in rows
    ]


def _stack_targets(rows: Sequence[_TrainingRow]) -> list[float]:
    return [r.target_deep_work_min for r in rows]


def _ols(features: list[list[float]], targets: list[float]) -> tuple[list[float], float, float, float]:
    """Plain OLS with intercept. Returns (coeffs, intercept, r², residual_std).

    Pure-Python implementation of the normal-equations solve. Numpy is not
    a hard dependency in this environment, so we Gauss-Jordan a 7x7 system.
    """
    n = len(features)
    if not features or n == 0:
        return [0.0] * len(FEATURE_NAMES), 0.0, 0.0, 0.0
    p = len(features[0])  # number of features

    # Augment with intercept column.
    aug = [[1.0, *row] for row in features]
    cols = p + 1

    # Build X^T X (cols x cols) and X^T y (cols).
    xtx = [[0.0] * cols for _ in range(cols)]
    xty = [0.0] * cols
    for i in range(n):
        row = aug[i]
        yi = targets[i]
        for a in range(cols):
            xty[a] += row[a] * yi
            for b in range(cols):
                xtx[a][b] += row[a] * row[b]

    solution = _solve(xtx, xty)
    if solution is None:
        return [0.0] * p, 0.0, 0.0, 0.0
    intercept = solution[0]
    coeffs = solution[1:]

    # Residuals + r² + residual std.
    ss_res = 0.0
    y_mean = sum(targets) / n
    ss_tot = sum((y - y_mean) ** 2 for y in targets)
    for i in range(n):
        pred = sum(aug[i][a] * solution[a] for a in range(cols))
        ss_res += (targets[i] - pred) ** 2
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    df = max(1, n - cols)
    residual_std = (ss_res / df) ** 0.5
    return coeffs, intercept, r_squared, residual_std


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Gauss-Jordan with partial pivoting. Returns solution or None if singular."""
    n = len(matrix)
    aug = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        # Partial pivot
        pivot = col
        for r in range(col + 1, n):
            if abs(aug[r][col]) > abs(aug[pivot][col]):
                pivot = r
        if abs(aug[pivot][col]) < 1e-12:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        # Normalize pivot row
        pv = aug[col][col]
        for c in range(col, n + 1):
            aug[col][c] /= pv
        # Eliminate other rows
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0:
                continue
            for c in range(col, n + 1):
                aug[r][c] -= factor * aug[col][c]
    return [aug[i][n] for i in range(n)]


# ── Evidence graph integration ───────────────────────────────────────────────


def readiness_payload(
    forecast: ReadinessForecast | ReadinessUnavailable,
) -> dict[str, Any]:
    """Render a forecast (or unavailable record) into a graph node payload."""
    if isinstance(forecast, ReadinessUnavailable):
        return {
            "status": "unavailable",
            "reason": forecast.reason,
            "sample_n": forecast.sample_n,
            "r_squared": forecast.r_squared,
        }
    return {
        "status": "available",
        "predicted_deep_work_min": forecast.predicted_deep_work_min,
        "ci_low": forecast.confidence_interval_95[0],
        "ci_high": forecast.confidence_interval_95[1],
        "inputs": forecast.inputs,
        "coefficients": forecast.coefficients,
        "intercept": forecast.intercept,
        "r_squared": forecast.r_squared,
        "sample_n": forecast.sample_n,
        "caveats": list(forecast.caveats),
    }
