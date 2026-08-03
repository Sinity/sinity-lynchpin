"""Tests for per-date sleep-coverage classification."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from lynchpin.analysis import sleep_coverage as scov


class _Event:
    def __init__(self, ts: datetime) -> None:
        self.ts = ts


class _Bounds:
    def __init__(self, first: date, last: date) -> None:
        self.first, self.last = first, last


class _Episode:
    def __init__(self, minutes: float) -> None:
        self.asleep_minutes = minutes


class _Composite:
    def __init__(self, d: date, minutes: float = 420.0) -> None:
        self.date = d
        self.main = _Episode(minutes)
        self.main_asleep_minutes = minutes


D0 = date(2026, 1, 5)


def _setup(monkeypatch, *, composites=(), hr_stamps=(), presses=(),
           keylog_window=(date(2026, 1, 1), date(2026, 1, 31))):
    monkeypatch.setattr(
        "lynchpin.sources.sleep_composite.night_composites",
        lambda **kw: list(composites),
    )
    monkeypatch.setattr(scov, "_hr_minute_index", lambda a, b: list(hr_stamps))
    import lynchpin.sources.keylog as keylog_mod

    monkeypatch.setattr(
        keylog_mod, "coverage_bounds",
        lambda: _Bounds(*keylog_window) if keylog_window else None,
        raising=False,
    )
    monkeypatch.setattr(
        keylog_mod, "keypresses",
        lambda **kw: [_Event(t) for t in presses],
        raising=False,
    )


def _day_hr(day: date, count: int) -> list[datetime]:
    base = datetime.combine(day, time(scov._DAY_START_HOUR))
    return [base + timedelta(minutes=i) for i in range(count)]


def _typing(day: date, start_hour: int, hours: int) -> list[datetime]:
    """One keystroke per minute for `hours` starting at start_hour."""
    base = datetime.combine(day, time(0)) + timedelta(hours=start_hour)
    return [base + timedelta(minutes=i) for i in range(hours * 60)]


def test_tracked_date_when_a_composite_exists(monkeypatch):
    _setup(monkeypatch, composites=[_Composite(D0)], hr_stamps=_day_hr(D0, 600))
    res = scov.classify_coverage(start=D0, end=D0)
    (v,) = res.verdicts
    assert v.verdict == scov.TRACKED
    assert v.sleep_minutes == 420.0


def test_no_hr_is_unknown_not_unslept(monkeypatch):
    # plenty of typing, but the watch was off: we know nothing about sleep
    _setup(monkeypatch, hr_stamps=[], presses=_typing(D0, 10, 8))
    res = scov.classify_coverage(start=D0, end=D0)
    (v,) = res.verdicts
    assert v.verdict == scov.UNKNOWN_NO_SENSOR
    assert v.is_unknown is True
    assert res.analyzable_days == 0
    assert any("watch off" in e for e in v.evidence)


def test_long_pc_silence_with_hr_reads_as_unrecorded_sleep(monkeypatch):
    # typing 08:00-14:00, then silent the rest of the logical day
    _setup(monkeypatch, hr_stamps=_day_hr(D0, 900), presses=_typing(D0, 8, 6))
    res = scov.classify_coverage(start=D0, end=D0)
    (v,) = res.verdicts
    assert v.verdict == scov.EVIDENCED_SLEEP_UNRECORDED
    assert v.pc_quiet_minutes is not None and v.pc_quiet_minutes >= 240
    assert v.is_unknown is False


def test_continuous_activity_with_hr_reads_as_awake(monkeypatch):
    # typing across the whole logical day in 3 h blocks with only short gaps
    presses = (
        _typing(D0, 7, 5) + _typing(D0, 13, 5) + _typing(D0, 19, 5)
        + _typing(D0 + timedelta(days=1), 1, 4)
    )
    _setup(monkeypatch, hr_stamps=_day_hr(D0, 1400), presses=presses)
    res = scov.classify_coverage(start=D0, end=D0)
    (v,) = res.verdicts
    assert v.verdict == scov.EVIDENCED_AWAKE
    assert v.pc_active_minutes is not None and v.pc_active_minutes >= 240


def test_day_away_from_computer_is_ambiguous_not_slept(monkeypatch):
    """A full silent day with the watch on is 'away from PC', not a 24 h sleep."""
    _setup(monkeypatch, hr_stamps=_day_hr(D0, 1000), presses=[])
    res = scov.classify_coverage(start=D0, end=D0)
    (v,) = res.verdicts
    assert v.verdict == scov.AMBIGUOUS
    assert v.is_unknown is True
    assert any("away from PC" in e for e in v.evidence)


def test_dates_outside_pc_coverage_are_unknown(monkeypatch):
    old = date(2020, 5, 5)
    _setup(monkeypatch, hr_stamps=_day_hr(old, 800), presses=[],
           keylog_window=(date(2026, 1, 1), date(2026, 1, 31)))
    res = scov.classify_coverage(start=old, end=old)
    (v,) = res.verdicts
    assert v.verdict == scov.UNKNOWN_NO_PC_EVIDENCE
    assert v.is_unknown is True


def test_summary_counts_and_exclusions(monkeypatch):
    d1, d3 = D0, D0 + timedelta(days=2)  # d1+1 deliberately has no evidence
    _setup(
        monkeypatch,
        composites=[_Composite(d1)],
        hr_stamps=_day_hr(d1, 600) + _day_hr(d3, 900),
        presses=_typing(d3, 8, 6),
    )
    res = scov.classify_coverage(start=d1, end=d3)
    assert res.counts[scov.TRACKED] == 1
    assert res.counts[scov.UNKNOWN_NO_SENSOR] == 1          # d2: no HR at all
    assert res.counts[scov.EVIDENCED_SLEEP_UNRECORDED] == 1  # d3
    assert res.analyzable_days == 2
    assert res.excluded_days == 1
    assert res.evidence_windows["keylog"] is not None
