"""Tests for commit_kind_attribution degraded flag (audit E).

Covers:
  - degraded=True when ai_attribution is all NULL
  - degraded=False when ai_attribution has data
  - correct response shape with degraded field
"""

from __future__ import annotations

import duckdb

from lynchpin.mcp.tools.change import commit_kind_attribution


def test_commit_kind_attribution_degraded_when_no_ai_attribution(
    monkeypatch, tmp_path
) -> None:
    """Returns degraded=True when ai_attribution is all NULL but rows exist."""
    db_path = tmp_path / "substrate.duckdb"
    refresh_id = "test:2026-05-01:2026-05-31:all"
    conn = duckdb.connect(str(db_path))

    # Create minimal commit_fact schema
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            conventional_kind VARCHAR,
            ai_attribution VARCHAR,
            refresh_id VARCHAR
        )
        """
    )

    # Insert commits with conventional_kind but no ai_attribution
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?)",
        ["sha1", "feat", None, refresh_id],
    )
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?)",
        ["sha2", "fix", None, refresh_id],
    )

    conn.close()

    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    result = commit_kind_attribution(refresh_id=refresh_id)
    assert result["degraded"] is True
    assert "ai_attribution backfill not run" in (result["reason"] or "")
    assert isinstance(result["rows"], list)
    assert len(result["rows"]) == 2


def test_commit_kind_attribution_not_degraded_when_has_ai_attribution(
    monkeypatch, tmp_path
) -> None:
    """Returns degraded=False when some rows have ai_attribution."""
    db_path = tmp_path / "substrate.duckdb"
    refresh_id = "test:2026-05-01:2026-05-31:all"
    conn = duckdb.connect(str(db_path))

    # Create minimal commit_fact schema
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            conventional_kind VARCHAR,
            ai_attribution VARCHAR,
            refresh_id VARCHAR
        )
        """
    )

    # Insert commits with some ai_attribution values
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?)",
        ["sha1", "feat", "claude", refresh_id],
    )
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?)",
        ["sha2", "fix", None, refresh_id],
    )

    conn.close()

    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    result = commit_kind_attribution(refresh_id=refresh_id)
    assert result["degraded"] is False
    assert result["reason"] is None
    assert len(result["rows"]) == 2
    # Verify data structure - rows should have kind, total, ai_assisted, ai_pct
    for row in result["rows"]:
        assert "kind" in row
        assert "total" in row
        assert "ai_assisted" in row
        assert "ai_pct" in row
    # At least one row should have non-zero ai_pct
    assert any(row["ai_pct"] > 0 for row in result["rows"])
