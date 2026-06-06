"""Tests for sources/keylog.py."""

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import keylog


def test_keylog_log_files_materializes_requested_window(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03-15.jsonl").write_text("{}\n", encoding="utf-8")
    cfg = SimpleNamespace(keylog_root=tmp_path)
    calls = []

    def fake_ensure(name, *, cfg, window=None):
        calls.append((name, cfg, window))

    monkeypatch.setattr(keylog, "get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", fake_ensure)

    files = keylog.log_files(start=date(2026, 3, 15), end=date(2026, 3, 15))

    assert files == [logs / "2026-03-15.jsonl"]
    assert calls == [("keylog", cfg, (date(2026, 3, 15), date(2026, 3, 16)))]


def test_keylog_log_file_index_sees_new_daily_file(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    first = logs / "2026-03-15.jsonl"
    second = logs / "2026-03-16.jsonl"
    first.write_text("{}\n", encoding="utf-8")
    cfg = SimpleNamespace(keylog_root=tmp_path)

    monkeypatch.setattr(keylog, "get_config", lambda: cfg)
    monkeypatch.setattr("lynchpin.materialization.ensure_materialized", lambda *_args, **_kwargs: None)

    assert keylog.log_files(start=date(2026, 3, 15), end=date(2026, 3, 16)) == [first]

    second.write_text("{}\n", encoding="utf-8")

    assert keylog.log_files(start=date(2026, 3, 15), end=date(2026, 3, 16)) == [first, second]


def test_keylog_counts_press_metadata_separately_from_snapshot_text(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03-15.jsonl").write_text(
        "\n".join([
            json.dumps({
                "ts": "2026-03-15T10:00:00.000Z",
                "event": "press",
                "session": "s1",
                "window": "unknown",
                "keycode": "KEY_A",
                "changed": True,
            }),
            json.dumps({
                "ts": "2026-03-15T10:00:01.000Z",
                "event": "snapshot",
                "session": "s1",
                "window": "unknown",
                "buffer": "hello text content",
            }),
        ]) + "\n"
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    start = datetime(2026, 3, 15, 9, 59, tzinfo=timezone.utc)
    end = datetime(2026, 3, 15, 10, 1, tzinfo=timezone.utc)

    events = list(keylog.events(start=start, end=end))
    assert len(events) == 2
    assert keylog.keypress_count(start=start, end=end) == 1
    assert not hasattr(events[1], "text")

    snapshots = list(keylog.text_snapshots(start=start, end=end))
    assert len(snapshots) == 1
    assert snapshots[0].text == "hello text content"


def test_keylog_low_level_readers_can_skip_nested_materialization(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03-15.jsonl").write_text(
        "\n".join([
            json.dumps({
                "ts": "2026-03-15T10:00:00Z",
                "event": "press",
            }),
            json.dumps({
                "ts": "2026-03-15T10:00:01Z",
                "event": "snapshot",
                "buffer": "hello",
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))
    monkeypatch.setattr(
        "lynchpin.materialization.ensure_materialized",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("caller already converged keylog materialization")
        ),
    )

    start = datetime(2026, 3, 15, 9, 59, tzinfo=timezone.utc)
    end = datetime(2026, 3, 15, 10, 2, tzinfo=timezone.utc)

    assert keylog.keypress_count(start=start, end=end, ensure=False) == 1
    assert [row.text for row in keylog.text_snapshots(start=start, end=end, ensure=False)] == ["hello"]
    assert keylog.has_coverage(start=start, end=end, ensure=False)


def test_keylog_events_parse_modifier_state_metadata(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03-15.jsonl").write_text(
        "\n".join([
            json.dumps({
                "ts": "2026-03-15T10:00:00.000Z",
                "event": "press",
                "keycode": "KEY_RETURN",
                "modifiers": ["KEY_LEFTMETA", "control", "ignored"],
            }),
            json.dumps({
                "ts": "2026-03-15T10:00:01.000Z",
                "event": "press",
                "keycode": "KEY_H",
                "modifier_state": {"SUPER": True, "SHIFT": False, "ALT": True},
            }),
        ]) + "\n"
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    rows = list(
        keylog.events(
            start=datetime(2026, 3, 15, 9, 59, tzinfo=timezone.utc),
            end=datetime(2026, 3, 15, 10, 2, tzinfo=timezone.utc),
            kinds={"press"},
        )
    )

    assert rows[0].modifiers == ("CTRL", "SUPER")
    assert rows[1].modifiers == ("ALT", "SUPER")


def test_keylog_daily_activity_summarizes_sessions(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "2026-03-15.jsonl").write_text(
        "\n".join([
            json.dumps({"ts": "2026-03-15T10:00:00Z", "event": "press", "session": "a", "changed": True}),
            json.dumps({"ts": "2026-03-15T10:01:00Z", "event": "press", "session": "b", "changed": False}),
        ]) + "\n"
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    day = keylog.daily_activity(start=datetime(2026, 3, 15).date(), end=datetime(2026, 3, 15).date())[0]
    assert day.keypress_count == 2
    assert day.changed_keypress_count == 1
    assert day.session_count == 2


def test_keylog_daily_activity_scans_multi_day_window_once(monkeypatch):
    calls = []

    def fake_events(*, start, end, kinds=None, ensure=True):
        calls.append((start, end, kinds))
        yield keylog.KeylogEvent(
            ts=datetime(2026, 3, 15, 10, tzinfo=timezone.utc),
            event="press",
            session="a",
            window=None,
            keycode="KEY_A",
            changed=True,
        )
        yield keylog.KeylogEvent(
            ts=datetime(2026, 3, 17, 10, tzinfo=timezone.utc),
            event="snapshot",
            session="b",
            window=None,
            keycode=None,
            changed=None,
        )

    monkeypatch.setattr(keylog, "events", fake_events)

    rows = keylog.daily_activity(
        start=datetime(2026, 3, 15).date(),
        end=datetime(2026, 3, 17).date(),
    )

    assert len(calls) == 1
    assert [row.date.isoformat() for row in rows] == [
        "2026-03-15",
        "2026-03-16",
        "2026-03-17",
    ]
    assert rows[0].keypress_count == 1
    assert rows[1].event_count == 0
    assert rows[2].event_count == 1
