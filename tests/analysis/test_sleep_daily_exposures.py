"""Tests for the exploratory daily-exposure vs sleep scan."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from lynchpin.analysis import sleep_daily_exposures as sde


class _Seg:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _Metrics:
    def __init__(self, deep_pct=None, rem_pct=None):
        self.deep_pct = deep_pct
        self.rem_pct = rem_pct


class _Signals:
    def __init__(self, hr_avg=None):
        self.hr_avg = hr_avg


class _Entry:
    def __init__(self, d, *, minutes=480.0, score=70.0, nap=False):
        self.date = d
        start = datetime.combine(d, datetime.min.time()) + timedelta(hours=23)
        self.segments = (_Seg(start, start + timedelta(minutes=minutes)),)
        self.is_nap = nap
        self.metrics = _Metrics(deep_pct=20.0)
        self.signals = _Signals(hr_avg=58.0)
        self.total_minutes = minutes
        self.effective_score = score
        self.score_estimated = False
        self.source = "merged"


class _KeyDay:
    def __init__(self, d, keypress_count):
        self.date = d
        self.keypress_count = keypress_count
        self.session_count = 3


def test_min_detectable_r_shrinks_with_sample_size():
    assert sde.min_detectable_r(30) > sde.min_detectable_r(100) > sde.min_detectable_r(400)
    assert sde.min_detectable_r(100) == pytest.approx(0.277, abs=0.01)


def test_planted_relationship_is_recovered_and_flagged_powered(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(60)]
    # more keypresses -> shorter sleep, by construction
    entries = [_Entry(d, minutes=540 - 3 * i) for i, d in enumerate(days)]
    keydays = [_KeyDay(d, 1000 + 200 * i) for i, d in enumerate(days)]

    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)
    monkeypatch.setattr(sde, "_keylog_exposures", lambda s, e: {
        "keylog.keypress_count": {k.date: float(k.keypress_count) for k in keydays}
    })
    monkeypatch.setattr(sde, "_weather_exposures", lambda s, e: {})

    res = sde.daily_exposure_correlation(start=days[0], end=days[-1])
    match = next(
        f for f in res["findings"]
        if f.exposure == "keylog.keypress_count" and f.outcome == "duration_min"
    )
    assert match.r < -0.95
    assert match.significant is True
    assert match.underpowered is False


def test_flat_exposure_yields_no_finding(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    entries = [_Entry(d) for d in days]
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)
    monkeypatch.setattr(sde, "_keylog_exposures", lambda s, e: {
        "keylog.keypress_count": {d: 100.0 for d in days}  # zero variance
    })
    monkeypatch.setattr(sde, "_weather_exposures", lambda s, e: {})
    res = sde.daily_exposure_correlation(start=days[0], end=days[-1])
    assert res["findings"] == []


def test_underpowered_null_is_distinguished_from_absence(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    entries = [_Entry(d, minutes=480 + (i % 5)) for i, d in enumerate(days)]
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)
    monkeypatch.setattr(sde, "_keylog_exposures", lambda s, e: {
        "keylog.keypress_count": {d: float(i % 7) for i, d in enumerate(days)}
    })
    monkeypatch.setattr(sde, "_weather_exposures", lambda s, e: {})
    res = sde.daily_exposure_correlation(start=days[0], end=days[-1])
    assert res["findings"], "expected tested pairs at n=30"
    weak = [f for f in res["findings"] if not f.significant]
    assert weak and all(f.min_detectable_r > 0.4 for f in weak)
    assert any(f.underpowered for f in weak)


def test_naps_excluded_and_unavailable_sources_reported(monkeypatch):
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    entries = [_Entry(d) for d in days] + [_Entry(days[0], nap=True)]
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)

    def boom(s, e):
        raise RuntimeError("no capture")

    monkeypatch.setattr(sde, "_keylog_exposures", boom)
    monkeypatch.setattr(sde, "_weather_exposures", lambda s, e: {})
    res = sde.daily_exposure_correlation(start=days[0], end=days[-1])
    assert res["nights"] == 30
    assert any("keylog" in u for u in res["unavailable"])
    assert any("EXPLORATORY" in c for c in res["caveats"])


def test_duration_outcome_uses_composite_not_canonical_minutes(monkeypatch):
    """A fragmented/short-canonical night must report composite duration.

    Mirrors the measured production case (sinity-lynchpin sleep_composite
    docstring, 2023-05-30): a short high-priority-source record and a longer
    low-priority-source record on the same night. Canonical selection picks
    the short one; ``_night_outcomes`` must report the union instead.
    """
    d = date(2026, 2, 1)
    short = datetime.combine(d, datetime.min.time()) + timedelta(hours=23)
    long_end = short + timedelta(minutes=360)
    entries = [
        _Entry(d, minutes=100.0),  # source="merged" -> highest priority
        _Entry(d, minutes=360.0),  # will be overwritten to a lower-priority source below
    ]
    entries[0].segments = (_Seg(short, short + timedelta(minutes=100)),)
    entries[1].segments = (_Seg(short, long_end),)
    entries[1].source = "stage_derived"
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)
    monkeypatch.setattr(sde, "_keylog_exposures", lambda s, e: {})
    monkeypatch.setattr(sde, "_weather_exposures", lambda s, e: {})

    outcomes = sde._night_outcomes(d, d)
    assert outcomes[d]["duration_min"] == 360.0
