from __future__ import annotations

from datetime import date, datetime, timezone

from lynchpin.sources.chrome_profile import ProfileVisit
from lynchpin.sources.ytmusic import daily_listening, iter_play_events


def _visit(ts: datetime, vid: str, title: str) -> ProfileVisit:
    return ProfileVisit(
        timestamp=ts,
        url=f"https://music.youtube.com/watch?v={vid}",
        title=title,
        profile="test",
    )


def test_replay_window_collapses_refreshes_but_keeps_replays():
    base = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    visits = [
        _visit(base, "abc123", "Song A | YouTube Music"),
        _visit(base.replace(second=30), "abc123", "Song A | YouTube Music"),  # refresh
        _visit(base.replace(minute=10), "abc123", "Song A | YouTube Music"),  # real replay
        _visit(base.replace(minute=5), "def456", "Song B - YouTube Music"),
    ]
    events = iter_play_events(visits=visits)
    assert [vid for _v, vid in events] == ["abc123", "def456", "abc123"]


def test_daily_listening_buckets_by_logical_day_and_normalizes_titles():
    # 01:30 UTC+0 is pre-06:00 local for any TZ >= UTC; use an unambiguous
    # afternoon time and a post-midnight one to hit both sides.
    afternoon = datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc)
    visits = [
        _visit(afternoon, "abc123", "Track One | YouTube Music - https://music.youtube.com/..."),
        _visit(afternoon.replace(minute=30), "def456", "Track Two - YouTube Music"),
    ]
    days = daily_listening(date(2026, 6, 1), date(2026, 6, 3), visits=visits)
    assert len(days) == 1
    day = days[0]
    assert day.play_count == 2 and day.unique_tracks == 2
    assert "Track One" in day.top_tracks and "Track Two" in day.top_tracks
