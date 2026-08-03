"""Tests for the evening-activity vs same-night-sleep join."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from lynchpin.analysis import evening_activity_sleep as eas


class _Seg:
    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end


class _Metrics:
    def __init__(self, deep_pct=None, rem_pct=None) -> None:
        self.deep_pct = deep_pct
        self.rem_pct = rem_pct


class _Signals:
    def __init__(self, hr_avg=None) -> None:
        self.hr_avg = hr_avg


class _Entry:
    def __init__(self, d, start, end, *, nap=False, score=None, deep=None, hr=None) -> None:
        self.date = d
        self.segments = (_Seg(start, end),)
        self.is_nap = nap
        self.metrics = _Metrics(deep_pct=deep)
        self.signals = _Signals(hr_avg=hr)
        self.total_minutes = (end - start).total_seconds() / 60.0
        self.effective_score = score


def _patch(monkeypatch, entries, intervals):
    monkeypatch.setattr(
        "lynchpin.sources.sleep.entries_in_range", lambda **kw: entries
    )
    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.active_intervals",
        lambda start, end: intervals,
    )
    monkeypatch.setattr("lynchpin.core.parse.as_local", lambda dt: dt)


ONSET = datetime(2026, 1, 2, 1, 0)  # 01:00, so the evening window covers 21:00-01:00


def test_active_minutes_and_gap_measured_in_window(monkeypatch):
    entry = _Entry(date(2026, 1, 1), ONSET, ONSET + timedelta(hours=7), score=70)
    intervals = [
        (datetime(2026, 1, 1, 22, 0), datetime(2026, 1, 1, 23, 0)),   # 60 min in window
        (datetime(2026, 1, 1, 23, 30), datetime(2026, 1, 2, 0, 30)),  # 60 min, ends 30 min pre-onset
        (datetime(2026, 1, 1, 14, 0), datetime(2026, 1, 1, 15, 0)),   # outside window
    ]
    _patch(monkeypatch, [entry], intervals)
    (row,) = eas.evening_nights(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert row.active_min == pytest.approx(120.0)
    assert row.last_active_gap_min == pytest.approx(30.0)
    # late cutoff 23:00 -> 23:00-23:00 (0) + 23:30-00:30 (60)
    assert row.late_active_min == pytest.approx(60.0)
    assert row.onset_hour == pytest.approx(1.0)


def test_partial_overlap_is_clipped_to_the_window(monkeypatch):
    entry = _Entry(date(2026, 1, 1), ONSET, ONSET + timedelta(hours=7))
    # starts before the 21:00 window open and runs past onset
    intervals = [(datetime(2026, 1, 1, 20, 0), datetime(2026, 1, 2, 2, 0))]
    _patch(monkeypatch, [entry], intervals)
    (row,) = eas.evening_nights(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert row.active_min == pytest.approx(240.0)  # exactly the 4 h window
    assert row.last_active_gap_min == pytest.approx(0.0)


def test_naps_excluded(monkeypatch):
    nap = _Entry(date(2026, 1, 1), datetime(2026, 1, 1, 14, 0),
                 datetime(2026, 1, 1, 15, 0), nap=True)
    _patch(monkeypatch, [nap], [])
    assert eas.evening_nights(start=date(2026, 1, 1), end=date(2026, 1, 1)) == []


def test_no_activity_yields_zero_and_null_gap(monkeypatch):
    entry = _Entry(date(2026, 1, 1), ONSET, ONSET + timedelta(hours=7))
    _patch(monkeypatch, [entry], [])
    (row,) = eas.evening_nights(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert row.active_min == 0.0
    assert row.last_active_gap_min is None


def test_correlation_requires_min_nights(monkeypatch):
    entry = _Entry(date(2026, 1, 1), ONSET, ONSET + timedelta(hours=7), score=70)
    _patch(monkeypatch, [entry], [])
    res = eas.evening_activity_correlation(start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert res["nights"] == 1
    assert res["findings"] == []


def test_correlation_recovers_a_planted_relationship(monkeypatch):
    entries = []
    intervals = []
    for i in range(20):
        d = date(2026, 1, 1) + timedelta(days=i)
        onset = datetime.combine(d, datetime.min.time()) + timedelta(days=1, hours=1)
        # more evening activity -> shorter night, by construction
        active_minutes = 30 + 10 * i
        duration = timedelta(hours=9) - timedelta(minutes=5 * i)
        entries.append(_Entry(d, onset, onset + duration, score=70))
        intervals.append((onset - timedelta(minutes=active_minutes), onset))
    _patch(monkeypatch, entries, intervals)
    res = eas.evening_activity_correlation(
        start=date(2026, 1, 1), end=date(2026, 1, 20), min_nights=10
    )
    match = next(
        f for f in res["findings"]
        if f["exposure"] == "active_min" and f["outcome"] == "duration_min"
    )
    assert match["r"] < -0.9
    assert match["significant"] is True
    assert any("NOT causation" in c for c in res["caveats"])
