"""Tests for night-level sleep composites."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from lynchpin.sources import sleep_composite as sc


class _Seg:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _Metrics:
    def __init__(self, deep_pct=None, rem_pct=None):
        self.deep_pct, self.rem_pct = deep_pct, rem_pct


class _Signals:
    def __init__(self, hr_avg=None):
        self.hr_avg = hr_avg


class _Entry:
    def __init__(self, d, start, end, *, source="stage_derived", nap=False,
                 score=None, deep=None):
        self.date = d
        self.segments = (_Seg(start, end),)
        self.source = source
        self.is_nap = nap
        self.metrics = _Metrics(deep_pct=deep)
        self.signals = _Signals(hr_avg=60.0)
        self.total_minutes = (end - start).total_seconds() / 60.0
        self.effective_score = score
        self.score_estimated = score is not None


def _patch(monkeypatch, entries):
    monkeypatch.setattr(
        "lynchpin.sources.sleep.entries_in_range", lambda **kw: entries
    )


D = date(2026, 1, 1)
BASE = datetime(2026, 1, 1, 23, 0)


def test_fragmented_night_unions_into_one_episode(monkeypatch):
    # 23:00-01:00, 30 min gap, 01:30-05:30 -> one episode, 6 h asleep
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=2)),
        _Entry(D, BASE + timedelta(hours=2, minutes=30), BASE + timedelta(hours=6, minutes=30)),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert len(night.secondary) == 0
    assert night.main.fragment_count == 2
    assert night.main_asleep_minutes == pytest.approx(360.0)
    assert night.main.gap_minutes == pytest.approx(30.0)
    assert night.is_fragmented is True


def test_distant_records_become_separate_episodes(monkeypatch):
    # 23:00-03:00 then a second bout 11 h later -> two episodes, main is longest
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=4)),
        _Entry(D, BASE + timedelta(hours=15), BASE + timedelta(hours=16)),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert len(night.secondary) == 1
    assert night.main_asleep_minutes == pytest.approx(240.0)
    assert night.secondary[0].asleep_minutes == pytest.approx(60.0)
    assert night.total_asleep_minutes == pytest.approx(300.0)
    assert night.is_fragmented is False


def test_overlapping_duplicates_are_not_double_counted(monkeypatch):
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=6), source="merged"),
        _Entry(D, BASE + timedelta(hours=1), BASE + timedelta(hours=5), source="saa_only"),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert night.main_asleep_minutes == pytest.approx(360.0)
    assert night.main.fragment_count == 1


def test_canonical_understatement_is_measured(monkeypatch):
    # high-priority SHORT record vs low-priority LONG record covering the night
    entries = [
        _Entry(D, BASE + timedelta(hours=1), BASE + timedelta(hours=3), source="merged"),
        _Entry(D, BASE, BASE + timedelta(hours=10), source="stage_derived"),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert night.canonical_source == "merged"
    assert night.canonical_minutes == pytest.approx(120.0)
    assert night.main_asleep_minutes == pytest.approx(600.0)
    assert night.understated_by_canonical_minutes == pytest.approx(480.0)


def test_naps_excluded_from_composites(monkeypatch):
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=6)),
        _Entry(D, datetime(2026, 1, 1, 14, 0), datetime(2026, 1, 1, 14, 40), nap=True),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert night.record_count == 1
    assert night.total_asleep_minutes == pytest.approx(360.0)


def test_gap_threshold_controls_episode_splitting(monkeypatch):
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=2)),
        _Entry(D, BASE + timedelta(hours=4), BASE + timedelta(hours=6)),
    ]
    _patch(monkeypatch, entries)
    tight = sc.night_composites(start=D, end=D, gap_threshold_min=60)[0]
    loose = sc.night_composites(start=D, end=D, gap_threshold_min=180)[0]
    assert len(tight.secondary) == 1          # 120 min gap splits at 60
    assert len(loose.secondary) == 0          # ...but not at 180
    assert loose.main_asleep_minutes == pytest.approx(240.0)


def test_scalars_come_from_the_canonical_record(monkeypatch):
    entries = [
        _Entry(D, BASE, BASE + timedelta(hours=3), source="merged", score=77.0, deep=21.5),
        _Entry(D, BASE, BASE + timedelta(hours=9), source="stage_derived", deep=99.0),
    ]
    _patch(monkeypatch, entries)
    (night,) = sc.night_composites(start=D, end=D)
    assert night.score == 77.0
    assert night.deep_pct == 21.5           # not the long low-priority record's 99
    assert night.main_asleep_minutes == pytest.approx(540.0)
