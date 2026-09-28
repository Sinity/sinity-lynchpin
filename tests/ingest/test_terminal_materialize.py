from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier, Event
from types import SimpleNamespace

from lynchpin.sources.terminal import AtuinCommand


def _command(day: int) -> AtuinCommand:
    return AtuinCommand(
        timestamp=datetime(2026, 1, day, 10, tzinfo=timezone.utc),
        duration_ns=1, exit_code=0, cwd="/repo", command=f"command-day-{day}",
    )


def test_sparse_atuin_refresh_keeps_verified_empty_days_with_unchanged_input(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    monkeypatch.setattr(terminal_materialize, "get_config", lambda: SimpleNamespace(atuin_db=db))
    monkeypatch.setattr(terminal_materialize, "commands_from_atuin_db", lambda _db, **_kw: iter([_command(2)]))

    windows = [(1, 4), (2, 4), (1, 3), (2, 4)]
    coverage = [
        terminal_materialize.materialize_atuin_history(
            output=output, start=date(2026, 1, first), end=date(2026, 1, last),
        )["covered_dates"]
        for first, last in windows
    ]
    assert coverage == [["2026-01-01", "2026-01-02", "2026-01-03"]] * 4
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_empty_atuin_refresh_on_changed_input_drops_unscanned_coverage(monkeypatch, tmp_path):
    """Fails if a refresh with no rows (so no verified bounds) re-stamps prior days."""
    from lynchpin.ingest import terminal_materialize

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    monkeypatch.setattr(terminal_materialize, "get_config", lambda: SimpleNamespace(atuin_db=db))
    monkeypatch.setattr(terminal_materialize, "commands_from_atuin_db", lambda _db, **_kw: iter(()))

    first = terminal_materialize.materialize_atuin_history(
        output=output, start=date(2026, 1, 1), end=date(2026, 1, 4),
    )
    assert first["covered_dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]

    db.write_text("fixture with a newer Atuin snapshot", encoding="utf-8")
    second = terminal_materialize.materialize_atuin_history(
        output=output, start=date(2026, 1, 2), end=date(2026, 1, 3),
    )

    assert second["covered_dates"] == ["2026-01-02"]
    assert second["input_versions"] != first["input_versions"]
    assert output.read_text(encoding="utf-8") == ""


def test_concurrent_disjoint_atuin_windows_both_publish(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    monkeypatch.setattr(terminal_materialize, "get_config", lambda: SimpleNamespace(atuin_db=db))
    barrier = Barrier(2)

    def source(_db, **_kwargs):
        barrier.wait(timeout=10)
        return iter([_command(day) for day in (1, 2, 3)])

    monkeypatch.setattr(terminal_materialize, "commands_from_atuin_db", source)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [
            pool.submit(terminal_materialize.materialize_atuin_history, output=output,
                        start=date(2026, 1, first), end=date(2026, 1, last))
            for first, last in ((1, 2), (2, 4))
        ]
        manifests = [call.result(timeout=20) for call in calls]

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads(output.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert sorted(row["command"] for row in rows) == [f"command-day-{day}" for day in (1, 2, 3)]
    assert sorted(item["row_count"] for item in manifests) in ([1, 3], [2, 3])
    assert manifest["row_count"] == len(rows) == 3
    assert manifest["covered_dates"] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_atuin_reader_waits_for_data_and_manifest_publication(monkeypatch, tmp_path):
    from lynchpin.ingest import terminal_materialize
    from lynchpin.sources import terminal

    db = tmp_path / "history.db"
    db.write_text("fixture", encoding="utf-8")
    output = tmp_path / "history.ndjson"
    monkeypatch.setattr(terminal_materialize, "get_config", lambda: SimpleNamespace(atuin_db=db))
    monkeypatch.setattr(terminal_materialize, "commands_from_atuin_db", lambda _db, **_kw: iter([_command(1)]))
    monkeypatch.setattr(terminal, "canonical_atuin_history_path", lambda: output)
    data_written = Event()
    finish_manifest = Event()
    reader_started = Event()
    original = terminal_materialize.write_manifest

    def delayed_manifest(path, fields):
        data_written.set()
        assert finish_manifest.wait(timeout=10)
        original(path, fields)

    monkeypatch.setattr(terminal_materialize, "write_manifest", delayed_manifest)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(terminal_materialize.materialize_atuin_history, output=output)
        assert data_written.wait(timeout=10)
        def read_commands():
            reader_started.set()
            return list(terminal.commands(ensure=False))

        reader = pool.submit(read_commands)
        assert reader_started.wait(timeout=10)
        assert not reader.done()
        finish_manifest.set()
        assert writer.result(timeout=10)["row_count"] == 1
        assert [command.command for command in reader.result(timeout=10)] == ["command-day-1"]


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
    # The old manifest never recorded input_versions, so it cannot establish
    # its carried-forward coverage claims for 2026-06-05/07 against this
    # run's input; only 2026-06-06 (this run's actual window) is claimed.
    # The rows for the other days are still preserved in the output above --
    # only the coverage *claim* outside the rescanned window is dropped.
    assert manifest["covered_dates"] == ["2026-06-06"]
    assert manifest["window_start"] == "2026-06-06"
    assert manifest["window_end"] == "2026-06-07"
