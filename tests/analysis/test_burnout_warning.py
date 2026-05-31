"""Tests for burnout_warning early-warning module.

Pins the correctness contracts:

  1. Missing ≠ zero — a signal outside its observed coverage is ABSENT from
     that day's composite; n_signals reflects only what was actually present.
  2. All-missing day → risk_score is None, not a fake zero.
  3. High-risk streak is detected (flagged as a risk window).
  4. Sign conventions hold: high stress/fragmentation → positive contribution;
     short sleep → positive contribution (risk rises when sleep is low).
  5. A day with exactly MIN_SIGNALS_PER_DAY signals is scored (boundary case).
  6. A day with fewer than MIN_SIGNALS_PER_DAY signals → None.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pytest

from lynchpin.analysis import burnout_warning as bw
from lynchpin.analysis.operator_daily import OperatorDay
from lynchpin.core.coverage import CoverageBounds


# ── Helpers ────────────────────────────────────────────────────────────────────

def _day(
    d: date,
    *,
    stress: Optional[float] = None,
    sleep: Optional[float] = None,
    frag: Optional[float] = None,
    hr_resting: Optional[float] = None,
    hrv_sdnn: Optional[float] = None,
    hrv_rmssd: Optional[float] = None,
) -> OperatorDay:
    """Construct a minimal OperatorDay with only the burnout-signal fields set."""
    return OperatorDay(
        date=d,
        stress_mean=stress,
        sleep_hours=sleep,
        aw_fragmentation=frag,
        hr_resting_bpm=hr_resting,
        hrv_sdnn=hrv_sdnn,
        hrv_rmssd=hrv_rmssd,
    )


def _full_coverage(first: date, last: date) -> dict[str, CoverageBounds]:
    """Stub: all burnout-signal coverage_keys cover the full window."""
    keys = ("health", "activitywatch", "sleep")
    return {
        k: CoverageBounds(source=k, first=first, last=last, kind="export")
        for k in keys
    }


def _no_coverage() -> dict[str, CoverageBounds]:
    """Stub: no coverage for any source (all dates absent)."""
    return {}


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[OperatorDay],
    *,
    cov: Optional[dict[str, CoverageBounds]] = None,
) -> None:
    """Monkeypatch operator_daily_matrix and coverage_bounds."""
    monkeypatch.setattr(bw, "operator_daily_matrix", lambda start, end, **kw: rows)
    if cov is None:
        first = rows[0].date if rows else date(2025, 1, 1)
        last = rows[-1].date if rows else date(2025, 1, 1)
        cov = _full_coverage(first, last)
    monkeypatch.setattr(bw, "coverage_bounds", lambda: cov)


# ── Test: all-missing day yields None score ────────────────────────────────────

def test_all_missing_day_yields_none_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day with no signals in coverage → risk_score is None, not 0."""
    start = date(2025, 1, 1)
    # Build 30 days, all with None fields.
    rows = [_day(start + timedelta(days=i)) for i in range(30)]
    # No coverage at all.
    _patch(monkeypatch, rows, cov=_no_coverage())

    report = bw.analyze(start=start, end=rows[-1].date)

    assert all(dr.risk_score is None for dr in report.daily_risk), (
        "Days with no coverage should all have risk_score=None"
    )
    assert all(dr.n_signals == 0 for dr in report.daily_risk)


# ── Test: missing signals excluded from n_signals ─────────────────────────────

def test_missing_signals_excluded_from_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day with only 2 of 6 signals present → n_signals == 2, not 6."""
    start = date(2025, 1, 1)
    # stress + sleep only; fragmentation, HR, HRV absent.
    rows = [
        _day(start + timedelta(days=i), stress=40.0, sleep=7.0)
        for i in range(30)
    ]
    # Only health (stress) and sleep are covered; activitywatch absent.
    cov: dict[str, CoverageBounds] = {
        "health": CoverageBounds("health", start, rows[-1].date, "export"),
        "sleep": CoverageBounds("sleep", start, rows[-1].date, "export"),
    }
    _patch(monkeypatch, rows, cov=cov)

    report = bw.analyze(start=start, end=rows[-1].date)

    # stress → health, hrv_sdnn → health, hrv_rmssd → health, hr_resting → health
    # sleep → sleep
    # fragmentation → activitywatch (absent)
    # So: stress, hrv_sdnn, hrv_rmssd, hr_resting, sleep are covered via health+sleep.
    # hr_resting is None in OperatorDay → not counted.
    # hrv_sdnn/rmssd are None → not counted.
    # Covered and non-None: stress, sleep → n_signals == 2.
    for dr in report.daily_risk:
        assert dr.n_signals == 2, f"Expected 2 signals, got {dr.n_signals} on {dr.date}"
        assert "stress" in dr.contributing_signals
        assert "sleep" in dr.contributing_signals
        assert "fragmentation" not in dr.contributing_signals


# ── Test: fewer than MIN_SIGNALS → None score ─────────────────────────────────

def test_too_few_signals_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day with only 1 signal present → risk_score is None (below MIN_SIGNALS_PER_DAY)."""
    assert bw.MIN_SIGNALS_PER_DAY >= 2, "Test assumes MIN_SIGNALS_PER_DAY >= 2"
    start = date(2025, 1, 1)
    # Only stress; sleep absent from coverage.
    rows = [_day(start + timedelta(days=i), stress=50.0) for i in range(30)]
    cov: dict[str, CoverageBounds] = {
        "health": CoverageBounds("health", start, rows[-1].date, "export"),
        # sleep NOT in cov → absent
    }
    _patch(monkeypatch, rows, cov=cov)

    report = bw.analyze(start=start, end=rows[-1].date)

    for dr in report.daily_risk:
        # Only stress (and other None health signals) contributes.
        # stress non-None = 1 signal (health covered). n_signals == 1 < MIN_SIGNALS_PER_DAY.
        assert dr.risk_score is None or dr.n_signals >= bw.MIN_SIGNALS_PER_DAY, (
            f"Day {dr.date}: score={dr.risk_score} but n_signals={dr.n_signals}"
        )


# ── Test: exactly MIN_SIGNALS_PER_DAY → scored ────────────────────────────────

def test_exactly_min_signals_is_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day with exactly MIN_SIGNALS_PER_DAY signals produces a non-None score."""
    start = date(2025, 1, 1)
    rows = [
        _day(start + timedelta(days=i), stress=40.0, sleep=7.0)
        for i in range(30)
    ]
    cov: dict[str, CoverageBounds] = {
        "health": CoverageBounds("health", start, rows[-1].date, "export"),
        "sleep": CoverageBounds("sleep", start, rows[-1].date, "export"),
    }
    _patch(monkeypatch, rows, cov=cov)

    report = bw.analyze(start=start, end=rows[-1].date)

    scored = [dr for dr in report.daily_risk if dr.risk_score is not None]
    # stress + sleep are both constant → z-scores 0.0 each → scores 0.0 (not None).
    # Constant distribution: stdev = 0 → fallback stdev = 1.0; z = 0 for all days.
    assert scored, "Days with 2 signals (=MIN_SIGNALS_PER_DAY) must be scored"
    assert all(dr.n_signals == 2 for dr in scored)


# ── Test: high-risk streak is flagged ────────────────────────────────────────

def test_high_risk_streak_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A sustained high-stress, low-sleep streak should be flagged as a risk window."""
    start = date(2025, 1, 1)
    n = 60

    rows = []
    for i in range(n):
        if i < 30:
            # Baseline: moderate stress, normal sleep.
            rows.append(_day(start + timedelta(days=i), stress=30.0, sleep=7.5, frag=0.2))
        else:
            # High-risk period: high stress, very short sleep, high fragmentation.
            rows.append(_day(start + timedelta(days=i), stress=80.0, sleep=4.0, frag=0.9))

    _patch(monkeypatch, rows)

    report = bw.analyze(start=start, end=rows[-1].date)

    # All days should be scored (≥2 signals: stress + sleep + fragmentation).
    scored = [dr for dr in report.daily_risk if dr.risk_score is not None]
    assert len(scored) == n, f"Expected all {n} days scored, got {len(scored)}"

    # The second half should have higher risk scores than the first half.
    baseline_scores = [dr.risk_score for dr in report.daily_risk[:30] if dr.risk_score is not None]
    risk_scores = [dr.risk_score for dr in report.daily_risk[30:] if dr.risk_score is not None]

    assert baseline_scores and risk_scores
    assert sum(risk_scores) / len(risk_scores) > sum(baseline_scores) / len(baseline_scores), (
        "High-stress/low-sleep period should yield higher mean risk score"
    )

    # At least one risk window should be flagged.
    assert report.risk_windows, "A clear high-risk streak should produce at least one risk window"

    # At least one window should overlap the high-risk half.
    high_risk_start = rows[30].date
    assert any(w.start >= high_risk_start for w in report.risk_windows) or any(
        w.end >= high_risk_start for w in report.risk_windows
    ), "No risk window overlaps the high-risk streak"


# ── Test: sign conventions ────────────────────────────────────────────────────

def test_sign_conventions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify sign conventions:
    - High stress → positive risk contribution (higher score).
    - Short sleep → positive risk contribution (lower sleep = higher score).
    """
    start = date(2025, 1, 1)
    n = 40

    # Two groups: one with high stress + short sleep, one with low stress + long sleep.
    high_risk_rows = [
        _day(start + timedelta(days=i), stress=90.0, sleep=4.0)
        for i in range(n)
    ]
    low_risk_rows = [
        _day(start + timedelta(days=n + i), stress=10.0, sleep=9.0)
        for i in range(n)
    ]
    rows = high_risk_rows + low_risk_rows
    _patch(monkeypatch, rows)

    report = bw.analyze(start=start, end=rows[-1].date)

    scored = [dr for dr in report.daily_risk if dr.risk_score is not None]
    high_risk_scores = [dr.risk_score for dr in scored[:n] if dr.risk_score is not None]
    low_risk_scores = [dr.risk_score for dr in scored[n:] if dr.risk_score is not None]

    assert high_risk_scores and low_risk_scores
    assert (sum(high_risk_scores) / len(high_risk_scores)) > (
        sum(low_risk_scores) / len(low_risk_scores)
    ), (
        "High-stress/short-sleep group must have higher mean risk score "
        "than low-stress/long-sleep group"
    )


# ── Test: coverage provenance reported ────────────────────────────────────────

def test_coverage_provenance_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """BurnoutReport.signal_coverage has one entry per signal."""
    start = date(2025, 1, 1)
    rows = [_day(start + timedelta(days=i), stress=40.0, sleep=7.0) for i in range(20)]
    _patch(monkeypatch, rows)

    report = bw.analyze(start=start, end=rows[-1].date)

    assert len(report.signal_coverage) == len(bw._SIGNALS)
    # Each provenance string names its signal source.
    for prov in report.signal_coverage:
        assert isinstance(prov, str) and len(prov) > 0


# ── Test: absence mid-window does not fabricate spurious risk shifts ──────────

def test_absent_signal_does_not_fabricate_risk_shift(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a signal's coverage starts mid-window, the uncovered half must not
    read as 'very low risk' due to 0-imputation — it must be absent."""
    start = date(2025, 1, 1)
    n = 60
    # Constant stress + sleep: no real change.
    rows = [_day(start + timedelta(days=i), stress=40.0, sleep=7.0) for i in range(n)]

    # Sleep coverage starts only at day 30.
    sleep_first = rows[30].date
    cov: dict[str, CoverageBounds] = {
        "health": CoverageBounds("health", start, rows[-1].date, "export"),
        "sleep": CoverageBounds("sleep", sleep_first, rows[-1].date, "export"),
    }
    _patch(monkeypatch, rows, cov=cov)

    report = bw.analyze(start=start, end=rows[-1].date)

    # First 30 days: only stress covered → n_signals == 1 < MIN_SIGNALS_PER_DAY → None.
    for dr in report.daily_risk[:30]:
        assert dr.risk_score is None, (
            f"Day {dr.date} before sleep coverage should have None score, got {dr.risk_score}"
        )

    # Last 30 days: stress + sleep covered → scored.
    scored_second = [dr for dr in report.daily_risk[30:] if dr.risk_score is not None]
    # With constant inputs, all scored days should have approximately equal scores (near 0).
    if scored_second:
        scores = [dr.risk_score for dr in scored_second if dr.risk_score is not None]
        score_range = max(scores) - min(scores)
        assert score_range < 0.5, (
            f"Constant-input scored days should be near-flat; range={score_range:.3f}"
        )


# ── Test: empty range returns empty report ────────────────────────────────────

def test_empty_range_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty row set returns a valid empty BurnoutReport."""
    monkeypatch.setattr(bw, "operator_daily_matrix", lambda start, end, **kw: [])
    monkeypatch.setattr(bw, "coverage_bounds", lambda: {})

    start = date(2025, 1, 1)
    report = bw.analyze(start=start, end=start)

    assert report.n_days == 0
    assert report.daily_risk == []
    assert report.risk_windows == []
