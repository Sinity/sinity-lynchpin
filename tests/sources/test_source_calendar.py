"""Tests for the calendar source (M.12)."""

from __future__ import annotations

import json
from datetime import date, timezone
from pathlib import Path


from lynchpin.sources import calendar as cal

UTC = timezone.utc


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events))


def _evt(**overrides) -> dict:
    base = {
        "uid": "evt-1",
        "calendar": "Personal",
        "summary": "Meeting",
        "start_at": "2026-05-08T15:00:00+02:00",
        "end_at":   "2026-05-08T16:00:00+02:00",
        "all_day":  False,
        "location": "",
        "attendees": [],
        "description": "",
        "status": "confirmed",
    }
    base.update(overrides)
    return base


def test_iter_events_filters_by_date_window(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    _write_jsonl(path, [
        _evt(uid="early", start_at="2026-04-01T10:00:00+02:00", end_at="2026-04-01T11:00:00+02:00"),
        _evt(uid="in_window", start_at="2026-05-08T10:00:00+02:00", end_at="2026-05-08T11:00:00+02:00"),
        _evt(uid="late", start_at="2026-06-01T10:00:00+02:00", end_at="2026-06-01T11:00:00+02:00"),
    ])
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    events = list(cal.iter_events(start=date(2026, 5, 1), end=date(2026, 5, 31)))
    assert [e.uid for e in events] == ["in_window"]


def test_duration_minutes_for_timed_event(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    _write_jsonl(path, [
        _evt(uid="hour-long", start_at="2026-05-08T10:00:00+02:00",
             end_at="2026-05-08T11:00:00+02:00"),
    ])
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    events = list(cal.iter_events(start=date(2026, 5, 8), end=date(2026, 5, 8)))
    assert events[0].duration_minutes == 60.0


def test_daily_load_aggregates_per_day(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    _write_jsonl(path, [
        _evt(uid="m1", calendar="Work",
             start_at="2026-05-08T10:00:00+02:00", end_at="2026-05-08T11:00:00+02:00"),
        _evt(uid="m2", calendar="Personal",
             start_at="2026-05-08T14:00:00+02:00", end_at="2026-05-08T15:00:00+02:00"),
        _evt(uid="m3", calendar="Personal",
             start_at="2026-05-09T10:00:00+02:00", end_at="2026-05-09T10:30:00+02:00"),
    ])
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    rows = cal.daily_load(start=date(2026, 5, 8), end=date(2026, 5, 9))
    assert len(rows) == 2
    by_day = {r.date: r for r in rows}
    may_8 = by_day[date(2026, 5, 8)]
    assert may_8.event_count == 2
    assert may_8.timed_minutes == 120
    assert may_8.calendars == {"Work": 1, "Personal": 1}


def test_busy_window_unions_overlapping_events(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    _write_jsonl(path, [
        # Two overlapping 60-min events: 10:00-11:00 and 10:30-11:30
        # → busy window 10:00-11:30 = 90 min
        _evt(uid="m1", start_at="2026-05-08T10:00:00+02:00",
             end_at="2026-05-08T11:00:00+02:00"),
        _evt(uid="m2", start_at="2026-05-08T10:30:00+02:00",
             end_at="2026-05-08T11:30:00+02:00"),
    ])
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    rows = cal.daily_load(start=date(2026, 5, 8), end=date(2026, 5, 8))
    assert rows[0].busy_window_minutes == 90.0
    # Total minutes is the sum of individual durations (120), not the union (90).
    assert rows[0].total_minutes == 120


def test_all_day_events_dont_inflate_timed_minutes(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    # All-day events span 09:00→09:00 next day so they fall after the
    # 06:00 logical-day boundary used by lynchpin.core.primitives.logical_date.
    _write_jsonl(path, [
        _evt(uid="vacation", all_day=True, summary="Vacation",
             start_at="2026-05-08T09:00:00+02:00",
             end_at="2026-05-09T09:00:00+02:00"),
        _evt(uid="meeting", start_at="2026-05-08T10:00:00+02:00",
             end_at="2026-05-08T11:00:00+02:00"),
    ])
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    rows = cal.daily_load(start=date(2026, 5, 8), end=date(2026, 5, 8))
    row = rows[0]
    assert row.event_count == 2
    assert row.all_day_count == 1
    assert row.timed_minutes == 60  # only the timed meeting
    assert row.busy_window_minutes == 60


def test_missing_file_returns_empty_iterator(tmp_path, monkeypatch):
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: tmp_path / "nope.jsonl")
    events = list(cal.iter_events(start=date(2026, 5, 1), end=date(2026, 5, 31)))
    assert events == []


def test_malformed_lines_are_skipped(tmp_path, monkeypatch):
    path = tmp_path / "calendar.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "not json\n"
        + json.dumps(_evt(uid="ok"))
        + "\n"
        + "{}\n"  # empty/missing uid
    )
    monkeypatch.setattr(cal, "_calendar_path_or_default", lambda: path)
    events = list(cal.iter_events(start=date(2026, 5, 1), end=date(2026, 5, 31)))
    assert [e.uid for e in events] == ["ok"]
