"""DuckDB substrate schema and connection contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_apply_schema_creates_all_tables(tmp_path: Path) -> None:
    """apply_schema must create all domain tables + substrate_meta."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()

    table_names = {r[0] for r in rows}
    expected = {
        "substrate_meta",
        "commit_fact",
        "file_change_fact",
        "ai_work_event",
        "symbol_change",
        "pr_review_row",
        "evidence_graph_build",
        "evidence_node",
        "evidence_edge",
        "substrate_source_status",
        "calendar_event",
        "spotify_daily",
        "machine_metric_sample",
        "machine_gpu_sample",
        "machine_network_sample",
        "machine_service_state",
        "machine_experiment_run",
    }
    assert expected <= table_names
    assert {
        "project_day_correlation",
        "issue_closure_chain_walk",
        "work_event_file_overlap",
        "work_event_symbol_overlap",
    } <= table_names


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    """Calling apply_schema twice must be a no-op; existing rows survive."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute("INSERT INTO substrate_meta VALUES ('canary', 'alive')")
        apply_schema(conn)
        row = conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'canary'"
        ).fetchone()

    assert row is not None
    assert row[0] == "alive"


def test_apply_schema_recreates_on_version_bump(tmp_path: Path) -> None:
    """Downgrading the stored version triggers drop+recreate; commit_fact is empty afterward."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO commit_fact "
            "(sha, repo, authored_at, lines_added, lines_deleted, lines_changed, "
            "files_changed, paths, path_roots, refresh_id) "
            "VALUES ('abc', 'r', '2026-01-01 00:00:00+00', 1, 0, 1, 1, [], [], 'r1')"
        )
        conn.execute("UPDATE substrate_meta SET value='0' WHERE key='version'")
        apply_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 0


def test_substrate_path_uses_local_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """substrate_path() must return a path under LynchpinConfig.local_root."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    import importlib
    import lynchpin.core.config as cfg_mod

    importlib.reload(cfg_mod)

    from lynchpin.substrate.connection import substrate_path

    path = substrate_path()
    assert str(tmp_path / "local") in str(path)
    assert path.suffix == ".duckdb"


def test_concurrent_writers_documented_constraint(tmp_path: Path) -> None:
    """Single-writer-many-readers constraint: open read_only after writer creates the file."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as writer:
        apply_schema(writer)

    with connect(db, read_only=True) as reader:
        tables = reader.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    assert any("commit_fact" in r[0] for r in tables)
