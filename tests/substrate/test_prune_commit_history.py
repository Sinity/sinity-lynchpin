"""Tests for substrate cleanup utilities (prune_commit_history)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import duckdb

UTC = timezone.utc


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


@pytest.fixture
def fresh_db(tmp_path: Path):
    """Yield a path with schema applied."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
    # Return just the path; tests will create their own connections
    yield db


def test_prune_commit_history_dry_run_empty_table(fresh_db):
    """Test dry_run on empty commit_fact table returns zeros."""
    db = fresh_db

    from lynchpin.substrate.connection import prune_commit_history

    result = prune_commit_history(keep_latest_n=1, dry_run=True, path=db)

    assert result["commit_fact"] == 0
    assert result["file_change_fact"] == 0
    assert result["symbol_change"] == 0
    assert result["dry_run"] is True
    assert result["refresh_ids_deleted"] == []


def test_prune_commit_history_dry_run_with_data(fresh_db):
    """Test dry_run counts rows that would be deleted without deleting them."""
    db = fresh_db
    from lynchpin.substrate.connection import connect

    # Insert some test data with different refresh_ids
    # Using NULL or specific values for materialized_at
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO commit_fact (
                sha, repo, project, authored_at, author, subject,
                lines_added, lines_deleted, lines_changed, files_changed,
                paths, path_roots, refresh_id, materialized_at
            )
            VALUES
                ('sha001', 'repo1', 'proj1', ?, 'author1', 'subject1',
                 10, 2, 12, 1, ['src/a.py'], ['src'], 'refresh-001', ?),
                ('sha002', 'repo1', 'proj1', ?, 'author1', 'subject2',
                 5, 1, 6, 1, ['src/b.py'], ['src'], 'refresh-001', ?),
                ('sha003', 'repo1', 'proj1', ?, 'author1', 'subject3',
                 15, 3, 18, 1, ['src/c.py'], ['src'], 'refresh-002', ?),
                ('sha004', 'repo1', 'proj1', ?, 'author1', 'subject4',
                 20, 4, 24, 1, ['src/d.py'], ['src'], 'refresh-003', ?)
            """,
            [
                _dt(2026, 5, 1), _dt(2026, 6, 1),  # refresh-001, earlier materialization
                _dt(2026, 5, 2), _dt(2026, 6, 2),  # refresh-001, earlier materialization
                _dt(2026, 5, 3), _dt(2026, 6, 5),  # refresh-002, middle materialization
                _dt(2026, 5, 4), _dt(2026, 6, 10),  # refresh-003, latest materialization
            ]
        )

    from lynchpin.substrate.connection import prune_commit_history

    # Dry run: keep latest 1, should count 3 rows to delete from refresh-001 and refresh-002
    result = prune_commit_history(keep_latest_n=1, dry_run=True, path=db)

    assert result["commit_fact"] == 3  # 2 from refresh-001 + 1 from refresh-002
    assert result["dry_run"] is True
    assert result["refresh_ids_kept"] == ["refresh-003"]
    assert set(result["refresh_ids_deleted"]) == {"refresh-001", "refresh-002"}

    # Verify data still exists (dry_run didn't delete)
    with connect(db) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
        assert remaining == 4


def test_prune_commit_history_actual_deletion(fresh_db):
    """Test actual deletion removes stale refresh_ids."""
    db = fresh_db
    from lynchpin.substrate.connection import connect

    # Insert test data
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO commit_fact (
                sha, repo, project, authored_at, author, subject,
                lines_added, lines_deleted, lines_changed, files_changed,
                paths, path_roots, refresh_id, materialized_at
            )
            VALUES
                ('sha001', 'repo1', 'proj1', ?, 'author1', 'subject1',
                 10, 2, 12, 1, ['src/a.py'], ['src'], 'refresh-001', ?),
                ('sha002', 'repo1', 'proj1', ?, 'author1', 'subject2',
                 5, 1, 6, 1, ['src/b.py'], ['src'], 'refresh-002', ?),
                ('sha003', 'repo1', 'proj1', ?, 'author1', 'subject3',
                 15, 3, 18, 1, ['src/c.py'], ['src'], 'refresh-003', ?)
            """,
            [
                _dt(2026, 5, 1), _dt(2026, 6, 1),
                _dt(2026, 5, 2), _dt(2026, 6, 5),
                _dt(2026, 5, 3), _dt(2026, 6, 10),
            ]
        )

    from lynchpin.substrate.connection import prune_commit_history

    # Actual deletion: keep latest 1
    result = prune_commit_history(keep_latest_n=1, dry_run=False, path=db)

    assert result["commit_fact"] == 2  # Deleted 2 rows
    assert result["dry_run"] is False
    assert result["refresh_ids_kept"] == ["refresh-003"]
    assert set(result["refresh_ids_deleted"]) == {"refresh-001", "refresh-002"}

    # Verify data was actually deleted
    with connect(db) as conn:
        remaining = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
        assert remaining == 1

        # Verify only refresh-003 remains
        refresh_ids = conn.execute(
            "SELECT DISTINCT refresh_id FROM commit_fact ORDER BY refresh_id"
        ).fetchall()
        assert [r[0] for r in refresh_ids] == ["refresh-003"]


def test_prune_commit_history_keep_multiple(fresh_db):
    """Test keeping multiple refresh_ids."""
    db = fresh_db
    from lynchpin.substrate.connection import connect

    # Insert test data with 4 refresh_ids
    with connect(db) as conn:
        for i in range(1, 5):
            conn.execute(
                """
                INSERT INTO commit_fact (
                    sha, repo, project, authored_at, author, subject,
                    lines_added, lines_deleted, lines_changed, files_changed,
                    paths, path_roots, refresh_id, materialized_at
                )
                VALUES (?, 'repo1', 'proj1', ?, 'author1', ?, 10, 2, 12, 1, ['src/a.py'], ['src'], ?, ?)
                """,
                [f"sha{i:03d}", _dt(2026, 5, i), f"subject{i}", f"refresh-{i:03d}", _dt(2026, 6, i)],
            )

    from lynchpin.substrate.connection import prune_commit_history

    # Keep latest 2
    result = prune_commit_history(keep_latest_n=2, dry_run=False, path=db)

    assert result["commit_fact"] == 2  # Deleted 2 rows
    assert result["refresh_ids_kept"] == ["refresh-004", "refresh-003"]
    assert set(result["refresh_ids_deleted"]) == {"refresh-001", "refresh-002"}

    # Verify correct rows remain
    with connect(db) as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id IN ('refresh-003', 'refresh-004')"
        ).fetchone()[0]
        assert remaining == 2
