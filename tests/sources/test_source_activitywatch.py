"""Tests for sources/activitywatch.py — focus spans, sessions, deep work, etc.

These are unit tests using synthetic data, not live DB queries.
"""

import json
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path

from lynchpin.sources.activitywatch import (
    FocusSpan, AppSession, _merge_adjacent, _focus_stretches, _session_ctx, _deep_compatible,
)
from lynchpin.sources.activitywatch_raw import (
    event_bounds,
    events,
    events_from_activitywatch_dbs,
)

UTC = timezone.utc
def dt(h, m=0, s=0): return datetime(2026, 3, 15, h, m, s, tzinfo=UTC)

def make_span(start, end, kind="focused", app="kitty", title="test", mode="coding", project="sinex"):
    return FocusSpan(start=start, end=end, kind=kind, app=app, title=title, mode=mode, project=project)


class TestFocusSpan:
    def test_duration(self):
        s = make_span(dt(10), dt(11))
        assert s.duration_s == 3600.0

    def test_date(self):
        s = make_span(dt(10), dt(11))
        assert s.date == date(2026, 3, 15)


class TestMergeAdjacent:
    def test_same_shape_merges(self):
        spans = [
            make_span(dt(10, 0), dt(10, 30)),
            make_span(dt(10, 30), dt(11, 0)),
        ]
        merged = list(_merge_adjacent(spans))
        assert len(merged) == 1
        assert merged[0].start == dt(10, 0)
        assert merged[0].end == dt(11, 0)

    def test_different_app_no_merge(self):
        spans = [
            make_span(dt(10), dt(10, 30), app="kitty"),
            make_span(dt(10, 30), dt(11), app="firefox"),
        ]
        merged = list(_merge_adjacent(spans))
        assert len(merged) == 2


class TestSessionCtx:
    def test_project_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode="coding", project="sinex", interruptions=0)
        assert _session_ctx(s) == "project:sinex"

    def test_mode_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode="coding", project=None, interruptions=0)
        assert _session_ctx(s) == "mode:coding"

    def test_app_key(self):
        s = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="test", titles=("test",), mode=None, project=None, interruptions=0)
        assert _session_ctx(s) == "app:kitty"


class TestDeepCompatible:
    def test_same_project(self):
        a = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        b = AppSession(app="kitty", start=dt(11, 5), end=dt(12), duration_s=3300,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        assert _deep_compatible(a, b)

    def test_different_project(self):
        a = AppSession(app="kitty", start=dt(10), end=dt(11), duration_s=3600,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0)
        b = AppSession(app="kitty", start=dt(11, 5), end=dt(12), duration_s=3300,
                       title_dominant="t", titles=("t",), mode="coding", project="polylogue", interruptions=0)
        assert not _deep_compatible(a, b)


class TestFocusStretches:
    def test_basic(self):
        sessions = [
            AppSession(app="kitty", start=dt(10), end=dt(10, 30), duration_s=1800,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0),
            AppSession(app="kitty", start=dt(10, 31), end=dt(11), duration_s=1740,
                       title_dominant="t", titles=("t",), mode="coding", project="sinex", interruptions=0),
            AppSession(app="firefox", start=dt(12), end=dt(12, 30), duration_s=1800,
                       title_dominant="t", titles=("t",), mode="research", project=None, interruptions=0),
        ]
        stretches = _focus_stretches(sessions)
        assert len(stretches) == 2  # first two merge (same ctx, <5min gap), third is separate


def _aw_db(path: Path, rows: list[tuple[str, datetime, datetime, dict]]) -> None:
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("CREATE TABLE buckets (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE events (bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT)")
        buckets: dict[str, int] = {}
        for bucket, start, end, payload in rows:
            bucket_id = buckets.setdefault(bucket, len(buckets) + 1)
            conn.execute("INSERT OR IGNORE INTO buckets (id, name) VALUES (?, ?)", (bucket_id, bucket))
            conn.execute(
                "INSERT INTO events (bucketrow, starttime, endtime, data) VALUES (?, ?, ?, ?)",
                (
                    bucket_id,
                    int(start.timestamp() * 1_000_000_000),
                    int(end.timestamp() * 1_000_000_000),
                    json.dumps(payload),
                ),
            )


def test_activitywatch_raw_merges_archive_dbs_and_filters_bad_rows(monkeypatch, tmp_path: Path) -> None:
    live = tmp_path / "live.db"
    archive_dir = tmp_path / "archive-dbs"
    archive_dir.mkdir()
    archive = archive_dir / "reset.db"
    row = ("aw-watcher-window_host", dt(10), dt(10, 5), {"app": "kitty"})
    _aw_db(live, [row, ("aw-watcher-window_host", dt(12), dt(12), {"app": "zero"})])
    _aw_db(archive, [row, ("aw-watcher-window_host", dt(9), dt(9, 5), {"app": "old"})])

    class Config:
        activitywatch_db = live
        activitywatch_archive_db_dir = archive_dir

    monkeypatch.setattr("lynchpin.sources.activitywatch_raw.get_config", lambda: Config())

    events = list(events_from_activitywatch_dbs("aw-watcher-window_", start=dt(8), end=dt(11)))
    assert [event.data["app"] for event in events] == ["old", "kitty"]
    assert event_bounds("aw-watcher-window_") == (date(2026, 3, 15), date(2026, 3, 15), 2)


def test_activitywatch_ndjson_events_use_bucket_index(monkeypatch, tmp_path: Path) -> None:
    """Canonical NDJSON reads should slice matching buckets, not full-scan all events."""
    path = tmp_path / "activitywatch/events.ndjson"
    path.parent.mkdir()
    rows = [
        {
            "bucket": "aw-watcher-afk_host",
            "start": "2026-03-15T09:00:00+00:00",
            "end": "2026-03-15T10:00:00+00:00",
            "data": {"status": "not-afk"},
        },
        {
            "bucket": "aw-watcher-window_host",
            "start": "2026-03-15T10:00:00+00:00",
            "end": "2026-03-15T10:00:00+00:00",
            "data": {"app": "kitty"},
        },
        {
            "bucket": "aw-watcher-web-firefox_host",
            "start": "2026-03-15T11:00:00+00:00",
            "end": "2026-03-15T12:00:00+00:00",
            "data": {"url": "https://example.com"},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    class Config:
        captures_root = tmp_path

    monkeypatch.setattr("lynchpin.sources.activitywatch_raw.get_config", lambda: Config())

    window = list(events("aw-watcher-window_", start=dt(8), end=dt(13)))
    web = list(events("aw-watcher-web-", start=dt(8), end=dt(13)))

    assert [event.data for event in window] == [{"app": "kitty"}]
    assert [event.data for event in web] == [{"url": "https://example.com"}]


def test_active_intervals_accepts_date_for_inclusive_day_window(monkeypatch) -> None:
    """active_intervals must treat ``end`` as inclusive when passed a date.

    Before #24 the docstring claimed `datetime` only; callers passing
    ``date(d), date(d)`` got `as_local(end)` → midnight start-of-day, which
    is equal to start, yielding a zero-width window and an empty result.
    Now both bounds accept dates and `end` expands to midnight of the next
    day via `end_of_day_local`.
    """
    from datetime import datetime as _dt, timezone as _tz
    from lynchpin.sources.activitywatch import active_intervals
    from lynchpin.sources.activitywatch_raw import AWEvent

    UTC = _tz.utc
    captured_bounds: dict[str, object] = {}

    def fake_afk_events(*, start, end):
        captured_bounds["start"] = start
        captured_bounds["end"] = end
        yield AWEvent(
            bucket="aw-watcher-afk_host",
            start=_dt(2026, 3, 15, 10, tzinfo=UTC),
            end=_dt(2026, 3, 15, 11, tzinfo=UTC),
            data={"status": "not-afk"},
        )

    monkeypatch.setattr(
        "lynchpin.sources.activitywatch.afk_events", fake_afk_events
    )
    # Bypass the keylog-driven repair pass — this test is about the
    # date-input / window-clipping plumbing, not the repair pipeline.
    # Stub repair_afk_events to pass events through unchanged.
    from types import SimpleNamespace

    def _passthrough(events, **kwargs):
        for e in events:
            status = (e.data or {}).get("status") or "unknown"
            yield SimpleNamespace(
                bucket=e.bucket, start=e.start, end=e.end,
                status=status, original_status=status, repaired=False,
            )

    monkeypatch.setattr(
        "lynchpin.sources.activitywatch_repair.repair_afk_events", _passthrough
    )

    # Pass DATE for both — previously zero-width, now spans the day.
    intervals = active_intervals(date(2026, 3, 15), date(2026, 3, 15))
    assert len(intervals) == 1
    # The window passed to afk_events must be a full day, not zero-width.
    assert captured_bounds["end"] > captured_bounds["start"]
