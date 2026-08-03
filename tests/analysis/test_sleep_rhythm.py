"""Tests for sleep regularity and circadian-phase analysis."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from lynchpin.analysis import sleep_rhythm as sr


class _Seg:
    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end


class _Entry:
    def __init__(self, d: date, start: datetime, end: datetime, *, nap: bool = False) -> None:
        self.date = d
        self.segments = (_Seg(start, end),)
        self.is_nap = nap
        self.metrics = None
        self.signals = None
        self.total_minutes = (end - start).total_seconds() / 60.0
        self.effective_score = None


def _regular_nights(days: int, *, bedtime_hour: int = 23) -> list[_Entry]:
    out = []
    for i in range(days):
        d = date(2026, 1, 1) + timedelta(days=i)
        start = datetime.combine(d, datetime.min.time()).replace(hour=bedtime_hour)
        out.append(_Entry(d, start, start + timedelta(hours=8)))
    return out


def _patch_entries(monkeypatch, entries):
    monkeypatch.setattr(
        "lynchpin.sources.sleep.entries_in_range",
        lambda **kwargs: entries,
    )


def test_perfectly_regular_schedule_scores_near_100(monkeypatch):
    _patch_entries(monkeypatch, _regular_nights(14))
    res = sr.sleep_regularity(start=date(2026, 1, 1), end=date(2026, 1, 14))
    assert res.nights == 14
    assert res.sri is not None and res.sri > 95
    assert res.midpoint_sd_minutes is not None and res.midpoint_sd_minutes < 5
    assert res.midpoint_mean_hour == pytest.approx(3.0, abs=0.1)


def test_alternating_schedule_scores_far_lower(monkeypatch):
    entries = []
    for i in range(14):
        d = date(2026, 1, 1) + timedelta(days=i)
        hour = 23 if i % 2 == 0 else 11  # flip bedtime by 12 h every other day
        start = datetime.combine(d, datetime.min.time()).replace(hour=hour)
        entries.append(_Entry(d, start, start + timedelta(hours=8)))
    _patch_entries(monkeypatch, entries)
    res = sr.sleep_regularity(start=date(2026, 1, 1), end=date(2026, 1, 14))
    assert res.sri is not None
    assert res.sri < 20
    assert res.midpoint_sd_minutes is not None and res.midpoint_sd_minutes > 120


def test_naps_are_excluded(monkeypatch):
    entries = _regular_nights(5)
    nap_day = date(2026, 1, 3)
    entries.append(
        _Entry(
            nap_day,
            datetime.combine(nap_day, datetime.min.time()).replace(hour=14),
            datetime.combine(nap_day, datetime.min.time()).replace(hour=15),
            nap=True,
        )
    )
    _patch_entries(monkeypatch, entries)
    res = sr.sleep_regularity(start=date(2026, 1, 1), end=date(2026, 1, 5))
    assert res.nights == 5


def test_sparse_coverage_is_flagged(monkeypatch):
    entries = [_regular_nights(30)[i] for i in (0, 1, 2, 3)]
    _patch_entries(monkeypatch, entries)
    res = sr.sleep_regularity(start=date(2026, 1, 1), end=date(2026, 1, 30))
    assert any("sparse coverage" in c for c in res.caveats)


def test_no_nights_returns_empty_result(monkeypatch):
    _patch_entries(monkeypatch, [])
    res = sr.sleep_regularity(start=date(2026, 1, 1), end=date(2026, 1, 10))
    assert res.sri is None and res.nights == 0


def test_circadian_phase_locates_hr_minimum(monkeypatch):
    night_start = datetime(2026, 1, 1, 23, 0)
    night_end = night_start + timedelta(hours=8)
    entry = _Entry(date(2026, 1, 1), night_start, night_end)
    _patch_entries(monkeypatch, [entry])

    # HR dips to 48 bpm for an hour starting 2 h after onset, else 60.
    samples = []
    for i in range(8 * 60):
        t = night_start + timedelta(minutes=i)
        bpm = 48.0 if 120 <= i < 180 else 60.0
        samples.append((t, bpm))
    monkeypatch.setattr(sr, "_hr_minute_samples", lambda a, b: samples)

    res = sr.circadian_phase(start=date(2026, 1, 1), end=date(2026, 1, 1), smooth_minutes=30)
    (night,) = res.nights
    assert night.hr_min_time is not None
    # minimum window centre should land inside the 01:00-02:00 dip
    assert night_start + timedelta(minutes=120) <= night.hr_min_time <= night_start + timedelta(minutes=180)
    assert night.hr_min_bpm == 48.0
    assert night.offset_minutes is not None
    assert res.resting_hr_p05 == 48.0


def test_circadian_phase_reports_none_without_enough_samples(monkeypatch):
    night_start = datetime(2026, 1, 1, 23, 0)
    entry = _Entry(date(2026, 1, 1), night_start, night_start + timedelta(hours=8))
    _patch_entries(monkeypatch, [entry])
    monkeypatch.setattr(
        sr, "_hr_minute_samples",
        lambda a, b: [(night_start + timedelta(minutes=i), 60.0) for i in range(5)],
    )
    res = sr.circadian_phase(start=date(2026, 1, 1), end=date(2026, 1, 1))
    (night,) = res.nights
    assert night.hr_min_time is None
    assert night.hr_samples == 5
