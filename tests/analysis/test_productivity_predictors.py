"""Tests for lynchpin.analysis.productivity_predictors.

Focus areas:
- No train/test leakage: linear_r2 and per-feature correlations must be
  computed on the training split only (first 80% of rows chronologically).
- Public interface shape: ProductivityReport and ProductivityPredictor fields.
- Summary text leads with held-out test metric, labels linear R² as train-only.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pytest

from lynchpin.analysis.productivity_predictors import (
    ProductivityPredictor,
    ProductivityReport,
    analyze,
)
from lynchpin.analysis.operator_daily import OperatorDay


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_rows(n: int, seed: int = 42) -> list[OperatorDay]:
    """Generate n synthetic OperatorDay rows with deterministic signals."""
    rng = np.random.default_rng(seed)
    rows: list[OperatorDay] = []
    base = date(2025, 1, 1)
    for i in range(n):
        deep_work = float(rng.integers(0, 240))
        rows.append(
            OperatorDay(
                date=base + timedelta(days=i),
                aw_active_hours=float(rng.integers(1, 10)),
                aw_deep_work_min=deep_work,
                git_commits=int(rng.integers(0, 10)),
                stress_mean=float(rng.integers(20, 80)),
                sleep_hours=float(rng.uniform(5.0, 9.0)),
                substance_mg_by_name={},
                wykop_comments=int(rng.integers(0, 5)),
            )
        )
    return rows


def _rows_to_by_date(rows: list[OperatorDay]) -> dict[date, OperatorDay]:
    return {r.date: r for r in rows}


# ---------------------------------------------------------------------------
# Helpers to expose internals under test
# ---------------------------------------------------------------------------


def _run_with_rows(rows: list[OperatorDay]) -> ProductivityReport:
    """Patch operator_daily_matrix and call analyze()."""
    start = rows[0].date
    end = rows[-1].date
    with patch(
        "lynchpin.analysis.productivity_predictors.operator_daily_matrix",
        return_value=rows,
    ):
        return analyze(start, end)


# ---------------------------------------------------------------------------
# Public interface shape
# ---------------------------------------------------------------------------


class TestPublicTypes:
    def test_report_fields_exist(self):
        r = ProductivityReport(
            window_start=date(2025, 1, 1),
            window_end=date(2025, 12, 31),
            n_training_days=100,
            linear_r2=0.5,
            rf_r2_train=0.9,
            rf_r2_test=0.4,
            rf_mae_test=30.0,
        )
        assert r.linear_r2 == 0.5
        assert r.rf_r2_test == 0.4
        assert r.predictors == []

    def test_predictor_fields_exist(self):
        p = ProductivityPredictor(
            name="yesterday_deep_work",
            importance=0.3,
            correlation=0.6,
            direction="+",
        )
        assert p.direction == "+"


# ---------------------------------------------------------------------------
# Leakage: train-only stats must differ from whole-dataset stats
# ---------------------------------------------------------------------------


class TestNoLeakage:
    """Verify that linear_r2 and feature correlations use training data only.

    We compare the value the function returns against the all-data value
    (old leaked computation). They should differ for any non-trivial dataset
    because scikit-learn's LinearRegression.score(X_train, y_train) ≠
    LinearRegression.score(X_all, y_all) when fit on X_train.
    """

    def _compute_all_data_linear_r2(self, rows: list[OperatorDay]) -> float:
        """Reproduce the OLD (leaked) linear R² for comparison."""
        from sklearn.linear_model import LinearRegression
        from lynchpin.analysis.productivity_predictors import _build_feature_matrix

        by_date = _rows_to_by_date(rows)
        start = rows[0].date
        end = rows[-1].date
        X, y, _ = _build_feature_matrix(by_date, start, end, "aw_deep_work_min", 1)
        if len(X) < 2:
            return 0.0
        lm = LinearRegression(fit_intercept=True)
        lm.fit(X, y)
        return float(lm.score(X, y))

    def test_linear_r2_is_train_only(self):
        """linear_r2 must be scored against training split, not all data."""
        rows = _make_rows(120)
        report = _run_with_rows(rows)

        all_data_r2 = self._compute_all_data_linear_r2(rows)

        # The fix: report.linear_r2 should NOT equal the all-data (leaked) value.
        # Both come from the same fitted model (on training data only), so the
        # training-split score will be higher than the whole-dataset score when
        # the model is overfitting on training data — typical for RF/LM on small N.
        # They must differ for a non-trivial synthetic dataset.
        assert report.linear_r2 != pytest.approx(all_data_r2, abs=1e-6), (
            f"linear_r2={report.linear_r2:.4f} equals the all-data leaked value "
            f"{all_data_r2:.4f} — leakage may still be present"
        )

    def test_feature_correlations_are_train_only(self):
        """Per-feature correlation must differ from the all-data correlation."""
        import numpy as np
        from lynchpin.analysis.productivity_predictors import _build_feature_matrix

        rows = _make_rows(120)
        by_date = _rows_to_by_date(rows)
        start = rows[0].date
        end = rows[-1].date
        X, y, _ = _build_feature_matrix(by_date, start, end, "aw_deep_work_min", 1)

        split_idx = int(len(X) * 0.8)
        X_train = X[:split_idx]
        y_train = y[:split_idx]

        # All-data correlation for feature 0 (yesterday_deep_work)
        col_all = X[:, 0]
        r_all = float(np.corrcoef(col_all, y)[0, 1])

        report = _run_with_rows(rows)
        reported_r = next(
            p.correlation for p in report.predictors if p.name == "yesterday_deep_work"
        )

        # Train-only correlation
        col_train = X_train[:, 0]
        r_train = float(np.corrcoef(col_train, y_train)[0, 1])

        # Reported value must match train-only, not all-data
        assert reported_r == pytest.approx(r_train, abs=1e-9), (
            f"reported correlation {reported_r:.4f} != train-only {r_train:.4f}"
        )
        # Sanity: confirm train-only ≠ all-data (so the test is meaningful)
        assert r_train != pytest.approx(r_all, abs=1e-6), (
            "train-only and all-data correlations are identical — "
            "synthetic data may be degenerate"
        )

    def test_rf_r2_test_uses_held_out_data(self):
        """rf_r2_test must be scored on the held-out 20% only."""
        from sklearn.ensemble import RandomForestRegressor
        from lynchpin.analysis.productivity_predictors import _build_feature_matrix

        rows = _make_rows(120)
        by_date = _rows_to_by_date(rows)
        start = rows[0].date
        end = rows[-1].date
        X, y, _ = _build_feature_matrix(by_date, start, end, "aw_deep_work_min", 1)

        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]

        rf = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
        rf.fit(X_train, y_train)
        expected_test_r2 = float(rf.score(X_test, y_test))

        report = _run_with_rows(rows)
        assert report.rf_r2_test == pytest.approx(expected_test_r2, abs=1e-9)


# ---------------------------------------------------------------------------
# Summary text
# ---------------------------------------------------------------------------


class TestSummaryText:
    def test_summary_leads_with_test_metric(self):
        """RF R² test (held-out) must appear before linear R² in summary."""
        rows = _make_rows(120)
        report = _run_with_rows(rows)
        assert report.summary != ""
        rf_pos = report.summary.find("RF R² test")
        linear_pos = report.summary.find("Linear R²")
        assert rf_pos != -1, "summary missing 'RF R² test'"
        assert linear_pos != -1, "summary missing 'Linear R²'"
        assert rf_pos < linear_pos, (
            "summary should lead with held-out RF R² test before Linear R²"
        )

    def test_summary_labels_linear_r2_as_train_only(self):
        """linear_r2 line must indicate it is train-only / descriptive."""
        rows = _make_rows(120)
        report = _run_with_rows(rows)
        lines = report.summary.splitlines()
        linear_lines = [line for line in lines if "Linear R²" in line]
        assert linear_lines, "no Linear R² line in summary"
        line = linear_lines[0].lower()
        assert "train" in line or "descriptive" in line or "in-sample" in line, (
            f"Linear R² summary line does not indicate train-only: {linear_lines[0]!r}"
        )

    def test_summary_has_mae_label(self):
        rows = _make_rows(120)
        report = _run_with_rows(rows)
        assert "MAE" in report.summary


# ---------------------------------------------------------------------------
# Short-data guard
# ---------------------------------------------------------------------------


class TestShortDataGuard:
    def test_too_few_rows_returns_zero_report(self):
        rows = _make_rows(20)
        report = _run_with_rows(rows)
        assert report.rf_r2_test == 0.0
        assert report.linear_r2 == 0.0
        assert report.predictors == []
