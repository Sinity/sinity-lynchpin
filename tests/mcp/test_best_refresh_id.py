"""Tests for best_refresh_id with substrate_source_status awareness.

Covers:
  - Preferring refresh_ids with ok source_status
  - Falling back to latest materialized_at when no status row exists
  - Empty table handling
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lynchpin.mcp.tools._utils import best_refresh_id


def _setup_test_db(tmp_path):  # type: ignore[no-untyped-def]
    """Create a temporary substrate with commit_fact and substrate_source_status."""
    import duckdb

    db_path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(db_path))

    # Create minimal commit_fact schema
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            refresh_id VARCHAR,
            materialized_at TIMESTAMPTZ
        )
        """
    )

    # Create substrate_source_status
    conn.execute(
        """
        CREATE TABLE substrate_source_status (
            refresh_id VARCHAR,
            source VARCHAR,
            status VARCHAR,
            recorded_at TIMESTAMPTZ
        )
        """
    )

    conn.close()
    return db_path


def test_best_refresh_id_prefers_ok_source_status(monkeypatch, tmp_path) -> None:
    """When two refresh_ids exist, prefers one with matching ok source_status."""
    import duckdb

    db_path = _setup_test_db(tmp_path)
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    conn = duckdb.connect(str(db_path))

    # Insert two refresh_ids: older one has ok status, newer one doesn't
    older_id = "dag:2026-05-24T10:00:00Z..."
    newer_id = "current-state:2026-05-24:2026-05-25:all"

    # Older refresh_id with row + ok status
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?)",
        ["sha1", older_id, datetime(2026, 5, 24, 10, tzinfo=timezone.utc)],
    )
    conn.execute(
        "INSERT INTO substrate_source_status VALUES (?, ?, ?, ?)",
        [
            older_id,
            "commits",
            "ok",
            datetime(2026, 5, 24, 10, 30, tzinfo=timezone.utc),
        ],
    )

    # Newer refresh_id with row but no ok status (e.g. partial/error)
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?)",
        ["sha2", newer_id, datetime(2026, 5, 25, 0, tzinfo=timezone.utc)],
    )

    conn.close()

    # Should prefer older_id because it has ok source_status
    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as test_conn:
        result = best_refresh_id(test_conn, "commit_fact")
    assert result == older_id


def test_best_refresh_id_falls_back_to_latest_materialized_at(
    monkeypatch, tmp_path
) -> None:
    """When no refresh_id has ok source_status, falls back to latest materialized_at."""
    import duckdb

    db_path = _setup_test_db(tmp_path)
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    conn = duckdb.connect(str(db_path))

    # Two refresh_ids, neither with ok status
    id1 = "run1"
    id2 = "run2"

    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?)",
        ["sha1", id1, datetime(2026, 5, 24, 10, tzinfo=timezone.utc)],
    )
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?)",
        ["sha2", id2, datetime(2026, 5, 25, 0, tzinfo=timezone.utc)],
    )

    conn.close()

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as test_conn:
        result = best_refresh_id(test_conn, "commit_fact")
    # Should pick id2 because it has the latest materialized_at
    assert result == id2


def test_best_refresh_id_returns_none_for_empty_table(monkeypatch, tmp_path) -> None:
    """Empty table returns None."""
    db_path = _setup_test_db(tmp_path)
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    from lynchpin.substrate.connection import connect, substrate_path

    with connect(substrate_path(), read_only=True) as conn:
        result = best_refresh_id(conn, "commit_fact")
    assert result is None


def test_fallback_prefers_high_coverage_over_recent_when_no_status(
    monkeypatch, tmp_path
) -> None:
    """When no substrate_source_status row exists for a table, the fallback
    must rank by row_count DESC first, materialized_at DESC as tiebreaker.

    Regression for the bug where activity_content_day had a recent dag-refresh
    with 6 rows and an older current-state refresh with 404 rows; the
    "latest materialized_at" fallback picked the 6-row refresh and downstream
    queries silently returned mostly-empty results.
    """
    import duckdb

    db_path = _setup_test_db(tmp_path)
    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    conn = duckdb.connect(str(db_path))
    older_comprehensive = "current-state:wide"
    newer_narrow = "dag:narrow"

    # Older refresh: 5 rows (comprehensive)
    for i in range(5):
        conn.execute(
            "INSERT INTO commit_fact VALUES (?, ?, ?)",
            [f"sha-old-{i}", older_comprehensive,
             datetime(2026, 5, 24, 10, tzinfo=timezone.utc)],
        )
    # Newer refresh: 1 row (narrow window)
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?)",
        ["sha-new", newer_narrow,
         datetime(2026, 5, 25, 0, tzinfo=timezone.utc)],
    )

    conn.close()

    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as test_conn:
        result = best_refresh_id(test_conn, "commit_fact")
    assert result == older_comprehensive, (
        f"expected the 5-row refresh to win over the 1-row recent refresh "
        f"in the no-status fallback, got {result!r}"
    )
