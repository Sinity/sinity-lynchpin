import json
from datetime import UTC, datetime

import pytest

from lynchpin.sources import keylog


def test_trace_preserves_wheel_metadata_and_capture_limits(tmp_path):
    path = tmp_path / "2026-04-02.jsonl"
    rows = [
        {"ts": "2026-04-02T10:00:00Z", "event": "press", "keycode": "KEY_A", "changed": True, "session": "keyboard"},
        {"ts": "2026-04-02T10:00:01Z", "event": "pointer_rel", "code": "REL_WHEEL", "value": -1, "session": "mouse"},
        {"ts": "2026-04-02T10:00:02Z", "event": "pointer_rel", "code": "REL_X", "value": 4},
        {"ts": "2026-04-02T10:00:03Z", "event": "press", "keycode": "KEY_B", "modifiers": []},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n")
    result = keylog.read_input_trace(
        start=datetime(2026, 4, 2, 10, tzinfo=UTC),
        end=datetime(2026, 4, 3, 1, tzinfo=UTC),
        logs_root=tmp_path,
    )
    assert len(result.events) == 3
    assert result.events[1].code == "REL_WHEEL"
    assert result.events[1].value == -1
    assert result.events[1].source_line == 2
    assert result.events[1].source_path == str(path)
    assert not result.events[0].modifier_state_known
    assert result.events[2].modifier_state_known
    assert result.files[0].invalid_lines == 1
    assert result.files[1].status == "missing"
    assert result.event_counts["pointer_rel:REL_X"] == 1
    assert result.continuity == "unknown"
    assert not hasattr(result.events[0], "text")


def test_trace_reads_utc_file_for_local_midnight_and_half_open_end(tmp_path):
    path = tmp_path / "2026-04-01.jsonl"
    path.write_text('\n'.join(json.dumps({"ts": t, "event": "press", "keycode": "KEY_C"}) for t in (
        "2026-04-01T22:00:00Z", "2026-04-01T22:01:00Z",
    )) + '\n')
    result = keylog.read_input_trace(
        start=datetime.fromisoformat("2026-04-02T00:00:00+02:00"),
        end=datetime.fromisoformat("2026-04-02T00:01:00+02:00"),
        logs_root=tmp_path,
    )
    assert len(result.events) == 1
    assert len(result.files) == 1
    assert result.files[0].path == str(path)


def test_trace_rejects_naive_bounds(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        keylog.read_input_trace(start=datetime(2026, 4, 2), end=datetime(2026, 4, 3), logs_root=tmp_path)
