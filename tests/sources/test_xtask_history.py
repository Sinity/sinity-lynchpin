from __future__ import annotations

import sqlite3
from pathlib import Path


def test_xtask_history_reads_invocation_rows(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_invocations

    db = _write_xtask_db(tmp_path / "xtask-history.db", row_id=1)

    rows = list(iter_invocations(path=db))

    assert len(rows) == 1
    row = rows[0]
    assert row.source_id == "xtask:1"
    assert row.command == ("check", "clippy")
    assert row.project == "sinex"
    assert row.git_dirty is True
    assert row.process_count_max == 11


def test_xtask_history_all_invocations_labels_archive_and_live(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from lynchpin.core import config as config_mod
    from lynchpin.sources.xtask_history import iter_all_invocations

    archive = _write_xtask_db(tmp_path / "archive.db", row_id=1)
    live = _write_xtask_db(tmp_path / "live.db", row_id=1)
    monkeypatch.setenv("LYNCHPIN_XTASK_HISTORY_ARCHIVE_DBS", str(archive))
    monkeypatch.setenv("LYNCHPIN_XTASK_HISTORY_DB", str(live))
    monkeypatch.setattr(config_mod, "_CONFIG", None)

    rows = list(iter_all_invocations())

    assert [row.source_id for row in rows] == ["xtask:archive1:1", "xtask:live:1"]


def test_xtask_history_reads_recovered_schema_by_column_name(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_invocations

    db = tmp_path / "archive.db"
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            """
            CREATE TABLE invocations (
                id INTEGER PRIMARY KEY, command TEXT, subcommand TEXT,
                profile TEXT, args_json TEXT, git_commit TEXT, git_dirty INTEGER,
                started_at TEXT, finished_at TEXT, duration_secs REAL,
                exit_code INTEGER, status TEXT, host TEXT, cwd TEXT,
                pid INTEGER, is_background INTEGER, stdout_path TEXT,
                stderr_path TEXT, stdout_content TEXT, stderr_content TEXT,
                cpu_usage_avg REAL, memory_usage_max_mb REAL,
                tree_fingerprint TEXT, scope_key TEXT, live_stage TEXT,
                pre_fix_errors INTEGER, pre_fix_warnings INTEGER,
                pre_fix_fixable INTEGER, launch_mode TEXT,
                process_cpu_usage_avg REAL, process_memory_usage_max_mb REAL,
                process_count_max INTEGER, resource_sample_count INTEGER,
                root_process_cpu_usage_avg REAL,
                root_process_memory_usage_max_mb REAL,
                shared_nix_daemon_cpu_usage_avg REAL,
                shared_nix_daemon_memory_usage_max_mb REAL,
                shared_nix_build_slice_cpu_usage_avg REAL,
                shared_nix_build_slice_memory_usage_max_mb REAL,
                shared_background_slice_cpu_usage_avg REAL,
                shared_background_slice_memory_usage_max_mb REAL,
                cancel_reason TEXT, is_zombie INTEGER,
                host_cpu_pressure_some_avg10_max REAL,
                host_io_pressure_some_avg10_max REAL,
                host_io_pressure_full_avg10_max REAL,
                host_memory_pressure_some_avg10_max REAL,
                host_memory_pressure_full_avg10_max REAL,
                shm_free_min_mb REAL, shm_used_max_mb REAL,
                cancelled_by TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO invocations
            (id, command, subcommand, profile, args_json, git_commit, git_dirty,
             started_at, finished_at, duration_secs, exit_code, status, host, cwd,
             live_stage, process_count_max, resource_sample_count)
            VALUES
            (7, 'check', 'test', 'profile-a', '[]', 'def456', 0,
             '2026-05-10T12:00:00Z', '2026-05-10T12:01:00Z', 60.0, 0,
             'success', 'sinnix-prime', '/realm/project/sinex', 'test', 33, 44)
            """
        )
    conn.close()

    row = next(iter_invocations(path=db, source_prefix="xtask:archive1"))

    assert row.source_id == "xtask:archive1:7"
    assert row.live_stage == "test"
    assert row.process_count_max == 33
    assert row.resource_sample_count == 44


def test_xtask_history_reads_stage_timings_and_test_results(tmp_path: Path) -> None:
    from lynchpin.sources.xtask_history import iter_stage_timings, iter_test_results

    db = _write_xtask_db(tmp_path / "xtask-history.db", row_id=9)
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            """
            CREATE TABLE stage_timings (
                id INTEGER PRIMARY KEY, invocation_id INTEGER, stage_name TEXT,
                started_at TEXT, duration_secs REAL, success INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO stage_timings
            VALUES (3, 9, 'clippy', '2026-05-31T19:47:20Z', 2.5, 1)
            """
        )
        conn.execute(
            """
            CREATE TABLE test_results (
                id INTEGER PRIMARY KEY, invocation_id INTEGER, test_name TEXT,
                package TEXT, status TEXT, duration_secs REAL, attempt INTEGER,
                output TEXT, slot_name TEXT, slot_wait_ms INTEGER,
                cleanup_ms INTEGER, failure_message TEXT, failure_type TEXT,
                test_mode TEXT, nats_context TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO test_results
            (id, invocation_id, test_name, package, status, duration_secs,
             attempt, slot_name, slot_wait_ms, cleanup_ms, failure_type,
             test_mode, nats_context)
            VALUES
            (4, 9, 'pkg::mod::test_name', 'pkg', 'pass', 0.12, 1,
             'slot-a', 10, 3, NULL, 'nextest', NULL)
            """
        )
    conn.close()

    stages = list(iter_stage_timings(path=db, source_prefix="xtask:live"))
    tests = list(iter_test_results(path=db, source_prefix="xtask:live"))

    assert stages[0].source_id == "xtask:live:stage:3"
    assert stages[0].invocation_source_id == "xtask:live:9"
    assert stages[0].stage_name == "clippy"
    assert stages[0].success is True
    assert tests[0].source_id == "xtask:live:test:4"
    assert tests[0].invocation_source_id == "xtask:live:9"
    assert tests[0].package == "pkg"
    assert tests[0].test_mode == "nextest"


def _write_xtask_db(db: Path, *, row_id: int) -> Path:
    conn = sqlite3.connect(db)
    with conn:
        conn.execute(
            """
            CREATE TABLE invocations (
                id INTEGER PRIMARY KEY, command TEXT NOT NULL, subcommand TEXT,
                profile TEXT, args_json TEXT, git_commit TEXT, git_dirty INTEGER,
                started_at TEXT NOT NULL, finished_at TEXT, duration_secs REAL,
                exit_code INTEGER, status TEXT NOT NULL, host TEXT NOT NULL,
                cwd TEXT NOT NULL, live_stage TEXT, cpu_usage_avg REAL,
                memory_usage_max_mb REAL, process_cpu_usage_avg REAL,
                process_memory_usage_max_mb REAL, root_process_cpu_usage_avg REAL,
                root_process_memory_usage_max_mb REAL,
                shared_nix_daemon_cpu_usage_avg REAL,
                shared_nix_daemon_memory_usage_max_mb REAL,
                shared_nix_build_slice_cpu_usage_avg REAL,
                shared_nix_build_slice_memory_usage_max_mb REAL,
                shared_background_slice_cpu_usage_avg REAL,
                shared_background_slice_memory_usage_max_mb REAL,
                host_cpu_pressure_some_avg10_max REAL,
                host_io_pressure_some_avg10_max REAL,
                host_io_pressure_full_avg10_max REAL,
                host_memory_pressure_some_avg10_max REAL,
                host_memory_pressure_full_avg10_max REAL,
                shm_free_min_mb REAL, shm_used_max_mb REAL,
                process_count_max INTEGER, resource_sample_count INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO invocations
            (id, command, subcommand, profile, args_json, git_commit, git_dirty,
             started_at, finished_at, duration_secs, exit_code, status, host, cwd,
             live_stage, cpu_usage_avg, memory_usage_max_mb, process_count_max,
             resource_sample_count)
            VALUES
            (?, 'check', 'clippy', NULL, '["--all"]', 'abc123', 1,
             '2026-05-31T19:47:18Z', '2026-05-31T19:48:18Z', 60.0, 0,
             'success', 'sinnix-prime', '/realm/project/sinex', 'clippy',
             42.0, 512.0, 11, 6)
            """,
            [row_id],
        )
    conn.close()
    return db
