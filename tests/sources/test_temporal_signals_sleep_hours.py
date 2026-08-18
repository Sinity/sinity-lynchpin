"""_load_sleep_hours must read composite minutes, not canonical (lynchpin-txz)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from lynchpin.sources.temporal_signals import _load_sleep_hours


class _Seg:
    def __init__(self, start, end):
        self.start, self.end = start, end


class _Entry:
    def __init__(self, d, onset, minutes, *, source="merged"):
        self.date = d
        self.segments = (_Seg(onset, onset + timedelta(minutes=minutes)),)
        self.is_nap = False
        self.total_minutes = minutes
        self.effective_score = None
        self.score_estimated = False
        self.source = source
        self.metrics = None
        self.signals = None


def test_load_sleep_hours_uses_composite_not_canonical_minutes(monkeypatch) -> None:
    """A short high-priority-source record must not shadow the composite night."""
    d = date(2026, 6, 1)
    onset = datetime(2026, 6, 1, 23, 0)
    entries = [
        _Entry(d, onset, 360.0, source="stage_derived"),
        _Entry(d, onset, 100.0, source="merged"),
    ]
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)

    hours = _load_sleep_hours(d, d)
    assert hours[d] == 6.0  # 360 min, not the canonical 100 min


def test_load_sleep_hours_matches_single_record_nights(monkeypatch) -> None:
    d = date(2026, 6, 2)
    onset = datetime(2026, 6, 2, 23, 0)
    entries = [_Entry(d, onset, 480.0, source="merged")]
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda **kw: entries)

    hours = _load_sleep_hours(d, d)
    assert hours[d] == 8.0
