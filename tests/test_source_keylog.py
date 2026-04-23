"""Tests for sources/keylog.py."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.sources import keylog


def test_keylog_counts_press_metadata_without_snapshot_buffers(tmp_path, monkeypatch):
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
                "buffer": "sensitive text must not surface",
            }),
        ]) + "\n"
    )
    monkeypatch.setattr(keylog, "get_config", lambda: SimpleNamespace(keylog_root=tmp_path))

    start = datetime(2026, 3, 15, 9, 59, tzinfo=timezone.utc)
    end = datetime(2026, 3, 15, 10, 1, tzinfo=timezone.utc)

    events = list(keylog.events(start=start, end=end))
    assert len(events) == 2
    assert keylog.keypress_count(start=start, end=end) == 1
    assert not hasattr(events[1], "buffer")


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
