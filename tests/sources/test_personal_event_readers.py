from __future__ import annotations

import json
from datetime import date


def _write_ndjson(path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_bookmark_reader_filters_half_open_window(tmp_path, monkeypatch) -> None:
    from lynchpin.sources.bookmarks import daily_bookmark_activity, iter_bookmarks

    path = tmp_path / "bookmarks.ndjson"
    _write_ndjson(
        path,
        [
            {
                "bookmark_id": "before",
                "domain": "before.example",
                "added_at": "2026-04-30T23:00:00+00:00",
            },
            {
                "bookmark_id": "inside",
                "domain": "inside.example",
                "added_at": "2026-05-01T12:00:00+00:00",
            },
            {
                "bookmark_id": "end",
                "domain": "end.example",
                "added_at": "2026-05-03T00:00:00+00:00",
            },
            {
                "bookmark_id": "undated",
                "domain": "undated.example",
                "added_at": None,
            },
        ],
    )
    monkeypatch.setattr("lynchpin.sources.bookmarks.bookmarks_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda name, *, window=None: None)

    rows = list(iter_bookmarks(path, start=date(2026, 5, 1), end=date(2026, 5, 3)))
    daily = daily_bookmark_activity(start=date(2026, 5, 1), end=date(2026, 5, 3))

    assert [row.bookmark_id for row in rows] == ["inside"]
    assert [(row.date, row.bookmark_count, row.top_domain) for row in daily] == [
        (date(2026, 5, 1), 1, "inside.example")
    ]


def test_bookmark_reader_converges_default_materialization(tmp_path, monkeypatch) -> None:
    from lynchpin.sources import bookmarks

    path = tmp_path / "bookmarks.ndjson"
    _write_ndjson(path, [{"bookmark_id": "inside", "added_at": "2026-05-01T12:00:00+00:00"}])
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(bookmarks, "bookmarks_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    assert [row.bookmark_id for row in bookmarks.iter_bookmarks(start=date(2026, 5, 1), end=date(2026, 5, 2))] == ["inside"]
    assert calls == [("browser_bookmarks", (date(2026, 5, 1), date(2026, 5, 2)))]

    calls.clear()
    assert [row.bookmark_id for row in bookmarks.iter_bookmarks(path)] == ["inside"]
    assert calls == []


def test_communication_reader_filters_half_open_window(tmp_path, monkeypatch) -> None:
    from lynchpin.sources.communications import (
        daily_communication_activity,
        iter_communication_events,
    )

    path = tmp_path / "communication_events.ndjson"
    _write_ndjson(
        path,
        [
            {
                "event_id": "before",
                "source": "messenger",
                "conversation_id": "a",
                "timestamp": "2026-04-30T23:00:00+00:00",
                "direction": "inbound",
            },
            {
                "event_id": "inside",
                "source": "messenger",
                "conversation_id": "b",
                "timestamp": "2026-05-01T12:00:00+00:00",
                "direction": "outbound",
            },
            {
                "event_id": "end",
                "source": "outlook",
                "conversation_id": "c",
                "timestamp": "2026-05-03T00:00:00+00:00",
                "direction": "inbound",
            },
            {
                "event_id": "undated",
                "source": "outlook",
                "conversation_id": "d",
                "timestamp": None,
                "direction": "inbound",
            },
        ],
    )
    monkeypatch.setattr("lynchpin.sources.communications.communication_events_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda name, *, window=None: None)

    rows = list(iter_communication_events(path, start=date(2026, 5, 1), end=date(2026, 5, 3)))
    daily = daily_communication_activity(start=date(2026, 5, 1), end=date(2026, 5, 3))

    assert [row.event_id for row in rows] == ["inside"]
    assert [
        (row.date, row.event_count, row.outbound_count, row.conversation_count, row.source_count)
        for row in daily
    ] == [(date(2026, 5, 1), 1, 1, 1, 1)]


def test_communication_reader_converges_default_materialization(tmp_path, monkeypatch) -> None:
    from lynchpin.sources import communications

    path = tmp_path / "communication_events.ndjson"
    _write_ndjson(
        path,
        [{"event_id": "inside", "timestamp": "2026-05-01T12:00:00+00:00", "conversation_id": "c"}],
    )
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(communications, "communication_events_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    rows = list(communications.iter_communication_events(start=date(2026, 5, 1), end=date(2026, 5, 2)))

    assert [row.event_id for row in rows] == ["inside"]
    assert calls == [("communications", (date(2026, 5, 1), date(2026, 5, 2)))]

    calls.clear()
    assert [row.event_id for row in communications.iter_communication_events(path)] == ["inside"]
    assert calls == []


def test_arbtt_reader_converges_default_materialization(tmp_path, monkeypatch) -> None:
    from lynchpin.sources import arbtt

    path = tmp_path / "events.ndjson"
    _write_ndjson(
        path,
        [
            {
                "event_id": "focus",
                "timestamp": "2026-05-01T12:00:00+00:00",
                "duration_s": 60,
            }
        ],
    )
    calls = []

    def fake_ensure_materialized(name, *, window=None):
        calls.append((name, window))

    monkeypatch.setattr(arbtt, "arbtt_events_path", lambda root=None: path)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure_materialized)

    assert [row.event_id for row in arbtt.iter_arbtt_events()] == ["focus"]
    assert calls == [("arbtt", None)]

    calls.clear()
    assert [row.event_id for row in arbtt.iter_arbtt_events(path)] == ["focus"]
    assert calls == []
