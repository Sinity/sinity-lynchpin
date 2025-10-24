"""Predict tomorrow's productivity from today's signals.

Can we predict deep-work hours from prior-day data?

Features: sleep (duration, score), substance (doses, mg), stress (mean),
prior-day deep work, day of week, git churn.

Model: RandomForest regression with temporal CV.
Feature importance tells us what drives productivity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

from .operator_daily import OperatorDay, operator_daily_matrix


@dataclass(frozen=True)
class ProductivityPredictor:
    """A feature that predicts next-day productivity."""

    name: str
    importance: float  # 0-1, from RandomForest
    correlation: float  # Pearson r with next-day deep_work (training split only)
    direction: str  # "+" = more of this → more productivity, "-" = less


@dataclass
class ProductivityReport:
    """Complete productivity prediction analysis."""

    window_start: date
    window_end: date
    n_training_days: int

    # Model quality
    linear_r2: float  # R² on training split only (not a held-out metric)
    rf_r2_train: float
    rf_r2_test: float
    rf_mae_test: float  # in deep-work minutes

    # What drives productivity?
    predictors: list[ProductivityPredictor] = field(default_factory=list)

    # Baseline
    mean_deep_work_min: float = 0
    std_deep_work_min: float = 0

    summary: str = ""


def analyze(
    start: date,
    end: date,
    *,
    target: str = "aw_deep_work_min",
    lookback_days: int = 1,
) -> ProductivityReport:
    """Build productivity prediction model.

    Args:
        start, end: date range for training data
        target: which metric to predict ("aw_deep_work_min", "aw_active_hours", "git_commits")
        lookback_days: how many prior days to use as features (1 = yesterday only)
    """
    rows = operator_daily_matrix(start, end, skip_slow=True)
    rows_by_date = {r.date: r for r in rows}

    # Build feature matrix: for each day, use prior day's signals to predict today
    X, y, y_dates = _build_feature_matrix(rows_by_date, start, end, target, lookback_days)

    if len(X) < 30:
        return ProductivityReport(
            window_start=start, window_end=end, n_training_days=len(X),
            linear_r2=0, rf_r2_train=0, rf_r2_test=0, rf_mae_test=0,
        )

    report = ProductivityReport(
        window_start=start, window_end=end, n_training_days=len(X),
        linear_r2=0, rf_r2_train=0, rf_r2_test=0, rf_mae_test=0,
        mean_deep_work_min=float(np.mean(y)),
        std_deep_work_min=float(np.std(y)),
    )

    # Temporal 80/20 split — established before any model fitting so that
    # linear R² and per-feature correlations are train-only, not in-sample.
    split_idx = int(len(X) * 0.8)
    X_train, X_test = X[:split_idx], X[split_idx:]
    y_train, y_test = y[:split_idx], y[split_idx:]

    if len(X_test) < 5:
        return report

    # Linear regression baseline — fitted and scored on training split only.
    lm = LinearRegression(fit_intercept=True)
    lm.fit(X_train, y_train)
    report.linear_r2 = float(lm.score(X_train, y_train))

    rf = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    rf.fit(X_train, y_train)

    report.rf_r2_train = float(rf.score(X_train, y_train))
    report.rf_r2_test = float(rf.score(X_test, y_test))
    report.rf_mae_test = float(mean_absolute_error(y_test, rf.predict(X_test)))

    # Feature importance and per-feature correlation — training split only.
    feature_names = _feature_names()
    for i, name in enumerate(feature_names):
        if i < X_train.shape[1]:
            col_train = X_train[:, i]
            if np.std(col_train) > 0:
                r = float(np.corrcoef(col_train, y_train)[0, 1])
            else:
                r = 0.0
            report.predictors.append(ProductivityPredictor(
                name=name,
                importance=float(rf.feature_importances_[i]),
                correlation=r,
                direction="+" if r > 0 else "-",
            ))

    report.predictors.sort(key=lambda p: -p.importance)
    report.summary = _build_summary(report)
    return report


def write_report(out: Path, *, start: date, end: date) -> dict[str, Any]:
    import json
    from datetime import datetime, timezone
    from dataclasses import asdict
    from lynchpin.core.io import save_json
    report = analyze(start, end)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        **asdict(report),
    }
    save_json(out, json.loads(json.dumps(payload, default=str)))
    return payload


def _feature_names() -> list[str]:
    return [
        "yesterday_deep_work",
        "yesterday_active_hours",
        "yesterday_git_commits",
        "yesterday_stress_mean",
        "yesterday_sleep_hours",
        "yesterday_substance_mg",
        "yesterday_wykop_comments",
        "day_of_week",
    ]


def _build_feature_matrix(
    rows_by_date: dict[date, OperatorDay],
    start: date,
    end: date,
    target: str,
    lookback: int,
) -> tuple[np.ndarray, np.ndarray, list[date]]:
    """Build (X, y) where X = prior-day signals, y = today's target."""
    X_rows: list[list[float]] = []
    y_vals: list[float] = []
    y_dates: list[date] = []

    target_fn = {
        "aw_deep_work_min": lambda r: r.aw_deep_work_min or 0,
        "aw_active_hours": lambda r: r.aw_active_hours or 0,
        "git_commits": lambda r: float(r.git_commits),
    }.get(target)
    if target_fn is None:
        return np.array([]), np.array([]), []

    cursor = start + timedelta(days=lookback)
    while cursor <= end:
        today = rows_by_date.get(cursor)
        yesterday = rows_by_date.get(cursor - timedelta(days=1))
        if today is None or yesterday is None:
            cursor += timedelta(days=1)
            continue

        y_val = target_fn(today)
        if y_val == 0 and today.aw_active_hours is None:
            # No AW data for today — skip
            cursor += timedelta(days=1)
            continue

        features = [
            yesterday.aw_deep_work_min or 0,
            yesterday.aw_active_hours or 0,
            float(yesterday.git_commits),
            yesterday.stress_mean or 0,
            yesterday.sleep_hours or 0,
            sum(yesterday.substance_mg_by_name.values()),
            float(yesterday.wykop_comments),
            float(cursor.weekday()),  # 0=Monday
        ]

        X_rows.append(features)
        y_vals.append(y_val)
        y_dates.append(cursor)
        cursor += timedelta(days=1)

    if not X_rows:
        return np.array([]), np.array([]), []

    return np.array(X_rows, dtype=np.float64), np.array(y_vals, dtype=np.float64), y_dates


def _build_summary(report: ProductivityReport) -> str:
    lines = [
        f"Productivity Prediction: {report.window_start} → {report.window_end}",
        f"  Training days: {report.n_training_days}",
        f"  Baseline deep-work: {report.mean_deep_work_min:.0f} ± {report.std_deep_work_min:.0f} min/day",
        f"  RF R² test (held-out): {report.rf_r2_test:.3f}  MAE: {report.rf_mae_test:.0f} min",
        f"  RF R² train: {report.rf_r2_train:.3f}",
        f"  Linear R² (train-only, descriptive): {report.linear_r2:.3f}",
        "",
        "Top predictors (importance + correlation on training split):",
    ]
    for p in report.predictors[:8]:
        lines.append(
            f"  {p.name:30s} importance={p.importance:.3f}  r={p.correlation:+.3f} {p.direction}"
        )
    return "\n".join(lines)


__all__ = [
    "ProductivityPredictor",
    "ProductivityReport",
    "analyze",
]
