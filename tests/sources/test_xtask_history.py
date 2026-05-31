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
