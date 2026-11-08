from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STARTED_MS = 1780257600000  # 2026-05-31T20:00:00Z


def _write_devkit_db(path: Path, *, run_id: int, project: str = "sinex") -> Path:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, project TEXT NOT NULL, checkout TEXT NOT NULL,
                command TEXT NOT NULL, args TEXT NOT NULL, head TEXT, dirty TEXT,
                toolchain TEXT, host TEXT, pid INTEGER NOT NULL, agentctl_job TEXT,
                started_ms INTEGER NOT NULL, duration_ms INTEGER, status TEXT NOT NULL,
                failure TEXT);
            CREATE TABLE IF NOT EXISTS stages (
                run INTEGER NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL,
                duration_ms INTEGER NOT NULL, ok INTEGER NOT NULL,
                PRIMARY KEY (run, position));
            CREATE TABLE IF NOT EXISTS tests (
                run INTEGER NOT NULL, binary TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, duration_ms INTEGER NOT NULL, output BLOB);
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, '/realm/worktree/sinex-q102', 'check', '--workspace',"
            " 'abc123', 'src/lib.rs', NULL, 'sinnix-prime', 1, NULL, ?, 25000, 'passed', NULL)",
            (run_id, project, STARTED_MS),
        )
    return path


def test_runs_become_invocations(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_invocations

    db = _write_devkit_db(tmp_path / "history.db", run_id=1)

    rows = list(iter_invocations(path=db))

    assert len(rows) == 1
    row = rows[0]
    assert row.source_id == "xtask:1"
    assert row.command == ("check", "--workspace")
    assert row.project == "sinex"
    assert row.status == "success"
    assert row.outcome_known is True
    assert row.started_at == datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    assert row.duration_s == 25.0
    assert row.git_commit == "abc123" and row.git_dirty is True
    assert row.workspace_root == "/realm/worktree/sinex-q102"
    assert row.workspace_name == "sinex-q102"
    # Devkit records no resource samples.
    assert row.process_count_max is None and row.cpu_usage_avg is None


def test_the_configured_store_is_the_only_one_read(tmp_path: Path, monkeypatch) -> None:
    from lynchpin.core import config as config_mod
    from lynchpin.sources.xtask_history import iter_all_invocations

    store = _write_devkit_db(tmp_path / "history.db", run_id=1)
    _write_devkit_db(tmp_path / "other.db", run_id=2)
    monkeypatch.setenv("LYNCHPIN_XTASK_HISTORY_DB", str(store))
    monkeypatch.setattr(config_mod, "_CONFIG", None)

    assert [row.source_id for row in iter_all_invocations()] == ["xtask:1"]


def test_time_window_filters_runs(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_invocations

    db = _write_devkit_db(tmp_path / "history.db", run_id=1)
    after = datetime(2026, 6, 1, tzinfo=timezone.utc)

    assert list(iter_invocations(path=db, start=after)) == []
    assert len(list(iter_invocations(path=db, end=after))) == 1


def test_stages_start_where_the_previous_stage_ended(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_stage_timings, iter_test_results

    db = _write_devkit_db(tmp_path / "history.db", run_id=9)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO stages VALUES (9, 0, 'fmt', 1000, 1)")
        conn.execute("INSERT INTO stages VALUES (9, 1, 'clippy', 2500, 1)")
        conn.execute("INSERT INTO tests VALUES (9, 'pkg', 'pkg::mod::test_name', 'passed', 120, NULL)")

    stages = list(iter_stage_timings(path=db, source_prefix="xtask:live"))
    tests = list(iter_test_results(path=db, source_prefix="xtask:live"))

    assert [stage.stage_name for stage in stages] == ["fmt", "clippy"]
    assert stages[1].source_id == "xtask:live:stage:9:1"
    assert stages[1].invocation_source_id == "xtask:live:9"
    assert stages[1].started_at == datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc)
    assert stages[1].duration_s == 2.5 and stages[1].success is True
    assert tests[0].invocation_source_id == "xtask:live:9"
    assert tests[0].package == "pkg" and tests[0].status == "passed"
    assert tests[0].duration_s == 0.12
