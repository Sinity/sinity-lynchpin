from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from lynchpin.sources.terminal import AtuinCommand


def test_materialize_atuin_history_records_input_high_water(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize
    from lynchpin.ingest.terminal_materialize import ATUIN_HISTORY_SCHEMA_VERSION

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    cfg = SimpleNamespace(atuin_db=db)

    monkeypatch.setattr(terminal_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        terminal_materialize,
        "commands_from_atuin_db",
        lambda _db: iter(
            [
                AtuinCommand(
                    timestamp=datetime(2026, 1, 1, 10, tzinfo=timezone.utc),
                    duration_ns=1,
                    exit_code=0,
                    cwd="/repo",
                    command="pytest",
                )
            ]
        ),
    )

    manifest = terminal_materialize.materialize_atuin_history(output=output)

    assert manifest["row_count"] == 1
    assert manifest["schema_version"] == ATUIN_HISTORY_SCHEMA_VERSION
    assert manifest["input_file_count"] == 1
    assert manifest["input_latest_mtime"] is not None
    assert manifest["date_boundary"] == "logical_06:00_local"
    assert manifest["first_timestamp_date"] == "2026-01-01"
    assert manifest["last_timestamp_date"] == "2026-01-01"


def test_materialize_atuin_history_records_logical_date_bounds(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    cfg = SimpleNamespace(atuin_db=db)

    monkeypatch.setattr(terminal_materialize, "get_config", lambda: cfg)
    monkeypatch.setattr(
        terminal_materialize,
        "commands_from_atuin_db",
        lambda _db: iter(
            [
                AtuinCommand(
                    timestamp=datetime(2026, 6, 6, 1, tzinfo=timezone.utc),
                    duration_ns=1,
                    exit_code=0,
                    cwd="/repo",
                    command="codex resume",
                ),
            ]
        ),
    )

    manifest = terminal_materialize.materialize_atuin_history(output=output)

    assert manifest["first_timestamp_date"] == "2026-06-06"
    assert manifest["last_timestamp_date"] == "2026-06-06"
    assert manifest["first_date"] == "2026-06-05"
    assert manifest["last_date"] == "2026-06-05"


def test_materialize_atuin_history_merges_requested_window(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    output.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-05T10:00:00+00:00",
                        "duration_ns": 1,
                        "exit_code": 0,
                        "cwd": "/repo",
                        "command": "before",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-06T10:00:00+00:00",
                        "duration_ns": 1,
                        "exit_code": 0,
                        "cwd": "/repo",
                        "command": "old-window",
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-07T10:00:00+00:00",
                        "duration_ns": 1,
                        "exit_code": 0,
                        "cwd": "/repo",
                        "command": "after",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "covered_dates": ["2026-06-05", "2026-06-06", "2026-06-07"],
                "first_date": "2026-06-05",
                "last_date": "2026-06-07",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = SimpleNamespace(atuin_db=db)
    replacement = AtuinCommand(
        timestamp=datetime(2026, 6, 6, 11, tzinfo=timezone.utc),
        duration_ns=2,
        exit_code=0,
        cwd="/repo",
        command="new-window",
    )

    monkeypatch.setattr(terminal_materialize, "get_config", lambda: cfg)
    calls = []

    def fake_commands_from_atuin_db(_db, **kwargs):
        calls.append(kwargs)
        return iter([replacement])

    monkeypatch.setattr(terminal_materialize, "commands_from_atuin_db", fake_commands_from_atuin_db)

    manifest = terminal_materialize.materialize_atuin_history(
        output=output,
        start=date(2026, 6, 6),
        end=date(2026, 6, 7),
    )

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["command"] for row in rows] == ["before", "new-window", "after"]
    assert calls and calls[0]["start"] is not None and calls[0]["end"] is not None
    assert manifest["covered_dates"] == ["2026-06-05", "2026-06-06", "2026-06-07"]
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"
