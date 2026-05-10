"""Tests for sources/sleep_infer.py."""

from datetime import date, datetime, timedelta, timezone

from lynchpin.sources.activitywatch import AWEvent
from lynchpin.sources.sleep import SleepEntry, SleepSegment
from lynchpin.graph.sleep_infer import infer_sleep

UTC = timezone.utc


def _entry(start: datetime, end: datetime, *, score: float | None = 80) -> SleepEntry:
    return SleepEntry(
        date=start.date(),
        total_minutes=(end - start).total_seconds() / 60,
        segments=(SleepSegment(start=start, end=end, duration_minutes=(end - start).total_seconds() / 60,
                               score=score, device="watch", comment=None),),
        avg_score=score,
    )


def test_infer_sleep_keeps_watch_sleep_when_aw_stays_active(monkeypatch):
    start = datetime(2026, 3, 15, 1, tzinfo=UTC)
    end = datetime(2026, 3, 15, 9, tzinfo=UTC)
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda *_args, **_kw: [_entry(start, end)])
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_architecture", lambda *_args, **_kw: [])
    monkeypatch.setattr("lynchpin.sources.activitywatch.active_intervals", lambda **_kw: [(start, end)])
    monkeypatch.setattr("lynchpin.sources.keylog.has_coverage", lambda **_kw: True)
    monkeypatch.setattr("lynchpin.sources.keylog.keypress_count", lambda **_kw: 0)
    monkeypatch.setattr("lynchpin.sources.activitywatch.window_events", lambda **_kw: [
        AWEvent("bucket", start, start, {"app": "google-chrome", "title": "music.youtube.com/watch?v=x"}),
        AWEvent("bucket", end - timedelta(minutes=1), end - timedelta(minutes=1), {"app": "google-chrome", "title": "music.youtube.com/watch?v=x"}),
    ])

    result = infer_sleep(start=date(2026, 3, 14), end=date(2026, 3, 15), include_media=True)

    assert len(result) == 1
    assert result[0].source == "watch_only"
    assert "aw_active_probably_stale" in result[0].evidence
    assert "ambient_media_during_sleep" in result[0].evidence
    assert result[0].keypress_count == 0


def test_infer_sleep_collapses_overlapping_watch_records(monkeypatch):
    start = datetime(2026, 3, 15, 1, tzinfo=UTC)
    short = _entry(start, start + timedelta(hours=2), score=70)
    long = _entry(start, start + timedelta(hours=8), score=80)
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda *_args, **_kw: [short, long])
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_architecture", lambda *_args, **_kw: [])
    monkeypatch.setattr("lynchpin.sources.activitywatch.active_intervals", lambda **_kw: [])
    monkeypatch.setattr("lynchpin.sources.keylog.has_coverage", lambda **_kw: True)
    monkeypatch.setattr("lynchpin.sources.keylog.keypress_count", lambda **_kw: 0)

    result = infer_sleep(start=date(2026, 3, 14), end=date(2026, 3, 15), include_media=False)

    assert len(result) == 1
    assert result[0].sleep_duration_min == 480
    assert "collapsed_2_overlapping_watch_records" in result[0].evidence


def test_infer_sleep_flags_keypress_contradictions(monkeypatch):
    start = datetime(2026, 3, 15, 7, tzinfo=UTC)
    end = start + timedelta(hours=7)
    monkeypatch.setattr("lynchpin.sources.sleep.entries_in_range", lambda *_args, **_kw: [_entry(start, end, score=None)])
    monkeypatch.setattr("lynchpin.sources.sleep.sleep_architecture", lambda *_args, **_kw: [])
    monkeypatch.setattr("lynchpin.sources.activitywatch.active_intervals", lambda **_kw: [])
    monkeypatch.setattr("lynchpin.sources.keylog.has_coverage", lambda **_kw: True)
    monkeypatch.setattr("lynchpin.sources.keylog.keypress_count", lambda **_kw: 120)

    result = infer_sleep(start=date(2026, 3, 15), end=date(2026, 3, 15), include_media=False)

    assert len(result) == 1
    assert "keypresses_during_watch_sleep" in result[0].evidence
    assert result[0].confidence < 0.5
