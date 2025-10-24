"""Tests for anomaly_crossref — metric label correctness and coverage gating.

These tests do NOT touch real data: they exercise the module's internal logic
(metric table, _build_anomaly, coverage filtering) in isolation using
constructed OperatorDay instances and mock coverage bounds.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch


from lynchpin.analysis.anomaly_crossref import (
    _ANOMALY_METRICS,
    _SOURCE_COVERAGE_KEY,
    _build_anomaly,
    AnomalyCrossReference,
    analyze,
)
from lynchpin.analysis.operator_daily import OperatorDay
from lynchpin.core.coverage import CoverageBounds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(d: date, **kwargs: Any) -> OperatorDay:
    """Build a minimal OperatorDay for a given date."""
    return OperatorDay(date=d, **kwargs)


# ---------------------------------------------------------------------------
# Task 1: metric-label correctness
# ---------------------------------------------------------------------------


def test_all_metric_labels_are_non_empty():
    """Every entry in _ANOMALY_METRICS must carry a non-empty metric label."""
    for source, _fn, direction, threshold, label in _ANOMALY_METRICS:
        assert label, (
            f"Metric label is empty for ({source!r}, {direction!r}, {threshold}). "
            "This was the original bug: lambda.__doc__ is None, so the label was ''."
        )


def test_metric_labels_are_all_distinct():
    """All metric labels must be unique — two checks on the same field (e.g.
    git_commits low vs. high) must produce distinguishable names."""
    labels = [label for *_, label in _ANOMALY_METRICS]
    assert len(labels) == len(set(labels)), (
        f"Duplicate metric labels: {[label for label in labels if labels.count(label) > 1]}"
    )


def test_git_low_and_high_have_different_labels():
    """The two git_commits checks (low and high) were previously both
    indistinguishable because both produced metric=''. Verify they now differ."""
    git_labels = [
        label
        for source, _fn, direction, _threshold, label in _ANOMALY_METRICS
        if source == "git"
    ]
    assert len(git_labels) == 2, f"Expected 2 git checks, got {git_labels}"
    assert git_labels[0] != git_labels[1], (
        f"Both git checks have the same label: {git_labels[0]!r}"
    )
    assert "low" in git_labels[0] or "low" in git_labels[1]
    assert "high" in git_labels[0] or "high" in git_labels[1]


def test_build_anomaly_preserves_metric_label():
    """_build_anomaly must pass the metric label straight through to AnomalyDay."""
    row = _make_row(date(2026, 1, 15), git_commits=42)
    anomaly = _build_anomaly(row, "git", "git_commits_high", 42.0, 3.1, "high")
    assert anomaly.metric == "git_commits_high"
    assert anomaly.source == "git"
    assert anomaly.direction == "high"


def test_build_anomaly_metric_not_empty_for_any_standard_check():
    """Smoke-test every standard metric tuple through _build_anomaly."""
    row = _make_row(
        date(2026, 1, 10),
        aw_active_hours=4.0,
        aw_deep_work_min=90.0,
        git_commits=5,
        stress_mean=50.0,
        sleep_hours=7.0,
        hr_mean_bpm=70.0,
        substance_mg_by_name={"test_substance": 20.0},
        substance_doses=2,
        wykop_comments=3,
        reddit_comments=1,
    )
    for source, fn, direction, _threshold, label in _ANOMALY_METRICS:
        val = fn(row)
        if val is None:
            continue
        anomaly = _build_anomaly(row, source, label, float(val), 2.5, direction)
        assert anomaly.metric == label, (
            f"metric label lost for ({source!r}, {direction!r}): "
            f"expected {label!r}, got {anomaly.metric!r}"
        )
        assert anomaly.metric != "", (
            f"metric is empty string for ({source!r}, {direction!r})"
        )


# ---------------------------------------------------------------------------
# Task 2: coverage gating (missing != zero)
# ---------------------------------------------------------------------------


def _make_bounds(source: str, first: date | None, last: date | None) -> CoverageBounds:
    from lynchpin.core.coverage import _classify_source
    return CoverageBounds(
        source=source,
        first=first,
        last=last,
        kind=_classify_source(source),
    )


def test_source_coverage_key_mapping_complete():
    """Every source_label in _ANOMALY_METRICS has an entry in _SOURCE_COVERAGE_KEY."""
    source_labels = {source for source, *_ in _ANOMALY_METRICS}
    missing = source_labels - set(_SOURCE_COVERAGE_KEY)
    assert not missing, (
        f"Source labels missing from _SOURCE_COVERAGE_KEY: {missing}"
    )


@patch("lynchpin.analysis.anomaly_crossref.operator_daily_matrix")
@patch("lynchpin.analysis.anomaly_crossref.coverage_bounds")
def test_coverage_gating_excludes_out_of_bounds_rows(mock_cov, mock_matrix):
    """Rows outside source coverage must not be used in anomaly detection.

    Setup: 30 rows where AW data only covers the first 22 dates.  The last 8
    rows have aw_active_hours=0.0 (the fabricated-zero).  Without coverage
    gating those zeros would drag the mean down and produce spurious anomalies.
    With gating only the 22 covered rows are used.
    """
    rows = []
    # 22 covered days with realistic hours
    cov_start = date(2026, 1, 1)
    cov_end = date(2026, 1, 22)
    for i in range(22):
        d = date(2026, 1, 1 + i)
        rows.append(_make_row(d, aw_active_hours=8.0))

    # 8 days outside coverage — zeros that OperatorDay defaults to
    for i in range(8):
        d = date(2026, 1, 23 + i)
        rows.append(_make_row(d, aw_active_hours=0.0))

    mock_matrix.return_value = rows

    # Coverage: AW only covers the first 22 days
    aw_bounds = _make_bounds("activitywatch", cov_start, cov_end)
    bounds_map: dict[str, CoverageBounds] = {}
    for label, cov_key in _SOURCE_COVERAGE_KEY.items():
        bounds_map[cov_key] = aw_bounds if cov_key == "activitywatch" else _make_bounds(cov_key, None, None)
    mock_cov.return_value = bounds_map

    report = analyze(date(2026, 1, 1), date(2026, 1, 30))

    # The 8 zero-days are outside coverage and must not appear as anomalies
    aw_anomalies = [a for a in report.anomalies if a.source == "aw"]
    low_aw_on_uncovered = [
        a for a in aw_anomalies
        if a.date > cov_end and a.metric == "aw_active_hours_low"
    ]
    assert low_aw_on_uncovered == [], (
        f"Spurious AW anomalies on uncovered dates: "
        f"{[a.date for a in low_aw_on_uncovered]}"
    )


@patch("lynchpin.analysis.anomaly_crossref.operator_daily_matrix")
@patch("lynchpin.analysis.anomaly_crossref.coverage_bounds")
def test_coverage_provenance_recorded(mock_cov, mock_matrix):
    """The report must carry provenance strings for each source label checked."""
    rows = [_make_row(date(2026, 1, 1 + i)) for i in range(30)]
    mock_matrix.return_value = rows

    aw_bounds = _make_bounds("activitywatch", date(2026, 1, 1), date(2026, 1, 30))
    bounds_map: dict[str, CoverageBounds] = {
        "activitywatch": aw_bounds,
    }
    # Others get empty bounds (no coverage info)
    for label, cov_key in _SOURCE_COVERAGE_KEY.items():
        if cov_key not in bounds_map:
            bounds_map[cov_key] = _make_bounds(cov_key, None, None)
    mock_cov.return_value = bounds_map

    report = analyze(date(2026, 1, 1), date(2026, 1, 30))

    assert "aw" in report.coverage_provenance, "AW source should have provenance"
    prov = report.coverage_provenance["aw"]
    assert "activitywatch" in prov
    assert "2026-01-01" in prov
    assert "2026-01-30" in prov


@patch("lynchpin.analysis.anomaly_crossref.operator_daily_matrix")
@patch("lynchpin.analysis.anomaly_crossref.coverage_bounds")
def test_no_data_returns_empty_report(mock_cov, mock_matrix):
    """analyze() with no rows returns an empty AnomalyCrossReference."""
    mock_matrix.return_value = []
    mock_cov.return_value = {}

    report = analyze(date(2026, 1, 1), date(2026, 1, 31))

    assert report.n_days == 0
    assert report.anomalies == []
    assert report.coverage_provenance == {}


# ---------------------------------------------------------------------------
# AnomalyCrossReference dataclass is additive-compatible
# ---------------------------------------------------------------------------


def test_anomaly_cross_reference_has_coverage_provenance_field():
    """AnomalyCrossReference must expose coverage_provenance (new additive field)."""
    report = AnomalyCrossReference(
        window_start=date(2026, 1, 1),
        window_end=date(2026, 1, 31),
        n_days=31,
    )
    assert hasattr(report, "coverage_provenance")
    assert isinstance(report.coverage_provenance, dict)
    report.coverage_provenance["aw"] = "activitywatch: covers 2026-01-01 → 2026-01-31 (capture)"
    assert "aw" in report.coverage_provenance
