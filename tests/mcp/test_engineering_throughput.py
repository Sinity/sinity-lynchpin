"""Tests for the engineering_throughput MCP tool.

Covers: path-pattern exclusion (the testable pure function), graceful
degradation when substrate tables are empty, and per-period decomposition
against an in-memory DuckDB.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.mcp.tools.velocity import _is_non_code_path, engineering_throughput


# ---------------------------------------------------------------------------
# Path-pattern exclusion (pure function)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "Cargo.lock",
        "flake.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "uv.lock",
        "poetry.lock",
        "snapshots/example.snap",
        "tests/fixtures/sample.json",
        "src/__snapshots__/component.spec.snap",
        "lynchpin/.lynchpin/generated/foo.json",
        "static/js/bundle.min.js",
        "static/css/styles.min.css",
        # Lynchpin retrospective scaffold dumps — surfaced via dogfooding
        # when 17.9M lines on 66 commits were reported for a single week.
        "retrospective/scaffold/2026/H1/Q1/March/2026-03-11/ai_activity.json",
        "retrospective/scaffold/2026/H1/Q1/January/2026-01-24/focus_timeline.json",
    ],
)
def test_is_non_code_path_matches_lockfiles_snapshots_generated(path: str) -> None:
    assert _is_non_code_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "src/main.rs",
        "lynchpin/sources/git.py",
        "flake.nix",  # nix flake config IS code
        "pyproject.toml",
        "docs/architecture.md",
        "tests/test_thing.py",  # tests are real code
        "Cargo.toml",
        "",
    ],
)
def test_is_non_code_path_does_not_match_real_code(path: str) -> None:
    assert _is_non_code_path(path) is False


# ---------------------------------------------------------------------------
# End-to-end against in-memory DuckDB
# ---------------------------------------------------------------------------

def _make_substrate(tmp_path):  # type: ignore[no-untyped-def]
    """Create a temporary substrate.duckdb seeded with one project + commits."""
    import duckdb

    db_path = tmp_path / "substrate.duckdb"
    refresh_id = "test:2026-05-01:2026-05-31:all"
    conn = duckdb.connect(str(db_path))

    # Minimal commit_fact schema (enough columns for the tool's queries).
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            lines_added INTEGER,
            lines_deleted INTEGER,
            files_changed INTEGER,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE file_change_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            path VARCHAR,
            lines_added INTEGER,
            lines_deleted INTEGER,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE symbol_change (
            sha VARCHAR,
            project VARCHAR,
            date DATE,
            change_type VARCHAR,
            refresh_id VARCHAR
        )
        """
    )

    # Two weeks of commits for project 'lp'.
    commits = [
        ("sha1", "lp", datetime(2026, 5, 4, 10, tzinfo=timezone.utc), 200, 50, 5),
        ("sha2", "lp", datetime(2026, 5, 5, 11, tzinfo=timezone.utc), 100, 20, 2),
        ("sha3", "lp", datetime(2026, 5, 12, 14, tzinfo=timezone.utc), 50, 10, 1),
    ]
    for sha, proj, at, la, ld, fc in commits:
        conn.execute(
            "INSERT INTO commit_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
            [sha, proj, at, la, ld, fc, refresh_id],
        )

    # File changes — sha1 splits between real code (150) and Cargo.lock (50)
    files = [
        ("sha1", "lp", datetime(2026, 5, 4, 10, tzinfo=timezone.utc), "src/main.rs", 150, 30),
        ("sha1", "lp", datetime(2026, 5, 4, 10, tzinfo=timezone.utc), "Cargo.lock", 50, 20),
        ("sha2", "lp", datetime(2026, 5, 5, 11, tzinfo=timezone.utc), "src/lib.rs", 100, 20),
        ("sha3", "lp", datetime(2026, 5, 12, 14, tzinfo=timezone.utc), "src/util.rs", 50, 10),
    ]
    for sha, proj, at, path, la, ld in files:
        conn.execute(
            "INSERT INTO file_change_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
            [sha, proj, at, path, la, ld, refresh_id],
        )

    # Symbol changes for week 1 only.
    symbols = [
        ("sha1", "lp", date(2026, 5, 4), "A"),
        ("sha1", "lp", date(2026, 5, 4), "M"),
        ("sha1", "lp", date(2026, 5, 4), "M"),
        ("sha2", "lp", date(2026, 5, 5), "R"),
    ]
    for sha, proj, d, ct in symbols:
        conn.execute(
            "INSERT INTO symbol_change VALUES (?, ?, ?, ?, ?)",
            [sha, proj, d, ct, refresh_id],
        )

    conn.close()
    return db_path, refresh_id


def test_engineering_throughput_decomposes_clean_vs_raw_lines(monkeypatch, tmp_path) -> None:
    db_path, refresh_id = _make_substrate(tmp_path)

    def fake_substrate_path() -> str:
        return str(db_path)

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", fake_substrate_path)

    result = engineering_throughput(
        project="lp",
        granularity="week",
        refresh_id=refresh_id,
    )
    assert result["project"] == "lp"
    assert result["granularity"] == "week"
    assert result["materialized_refresh_id"] == refresh_id
    assert result["refresh_id"] == refresh_id
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["materialization"]["caller"] == "engineering_throughput"
    # Two weeks of data with commits.
    assert len(result["periods"]) == 2

    week1 = result["periods"][0]
    # Week 1: sha1 (200/50) + sha2 (100/20) = 300/70 raw, 250/50 clean
    # (excluding 50/20 Cargo.lock from sha1).
    assert week1["commit_count"] == 2
    assert week1["lines_added"] == 300
    assert week1["lines_added_clean"] == 250
    assert week1["lines_deleted_clean"] == 50
    # Symbol decomposition for week 1: 1 added + 2 modified + 1 renamed = 4 total
    assert week1["symbols_added"] == 1
    assert week1["symbols_modified"] == 2
    assert week1["symbols_renamed"] == 1
    assert week1["symbols_total"] == 4


def test_pr_grouped_file_changes_do_not_join_stale_refresh_rows(tmp_path) -> None:
    import duckdb

    from lynchpin.substrate.readers_velocity import load_file_change_by_period

    db_path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            subject VARCHAR,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE file_change_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            path VARCHAR,
            lines_added INTEGER,
            lines_deleted INTEGER,
            refresh_id VARCHAR
        )
        """
    )
    authored_at = datetime(2026, 5, 4, 10, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?, ?)",
        ["same-sha", "lp", authored_at, "feat: fixture (#42)", "wanted"],
    )
    conn.execute(
        "INSERT INTO file_change_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["same-sha", "lp", authored_at, "src/current.rs", 10, 1, "wanted"],
    )
    conn.execute(
        "INSERT INTO file_change_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["same-sha", "lp", authored_at, "src/stale.rs", 999, 99, "stale"],
    )

    rows = load_file_change_by_period(
        conn,
        refresh_id="wanted",
        project="lp",
        granularity="week",
        grouping="pr",
    )

    assert rows == [(date(2026, 5, 4), 10, 1, "src/current.rs")]


def test_symbol_change_periods_do_not_join_stale_refresh_rows(tmp_path) -> None:
    import duckdb

    from lynchpin.substrate.readers_velocity import load_symbol_change_by_period

    db_path = tmp_path / "substrate.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            subject VARCHAR,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE symbol_change (
            sha VARCHAR,
            project VARCHAR,
            date DATE,
            path VARCHAR,
            change_type VARCHAR,
            qualified_name VARCHAR,
            refresh_id VARCHAR
        )
        """
    )
    authored_at = datetime(2026, 5, 4, 10, tzinfo=timezone.utc)
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?, ?)",
        ["same-sha", "lp", authored_at, "feat: fixture (#42)", "wanted"],
    )
    conn.execute(
        "INSERT INTO symbol_change VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["same-sha", "lp", date(2026, 5, 4), "src/current.rs", "ADDED", "current", "wanted"],
    )
    conn.execute(
        "INSERT INTO symbol_change VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["same-sha", "lp", date(2026, 5, 4), "src/stale.rs", "MODIFIED", "stale", "stale"],
    )

    raw_rows = load_symbol_change_by_period(
        conn,
        refresh_id="wanted",
        project="lp",
        granularity="week",
        grouping="raw",
    )
    pr_rows = load_symbol_change_by_period(
        conn,
        refresh_id="wanted",
        project="lp",
        granularity="week",
        grouping="pr",
    )

    assert raw_rows == [(date(2026, 5, 4), "ADDED", 1)]
    assert pr_rows == [(date(2026, 5, 4), "ADDED", 1)]


def test_engineering_throughput_returns_degraded_for_unknown_project(monkeypatch, tmp_path) -> None:
    db_path, refresh_id = _make_substrate(tmp_path)

    monkeypatch.setattr(
        "lynchpin.substrate.connection.substrate_path",
        lambda: str(db_path),
    )

    result = engineering_throughput(
        project="not_a_real_project",
        granularity="week",
        refresh_id=refresh_id,
    )
    assert result["degraded"] is True
    assert result["materialized_refresh_id"] == refresh_id
    assert result["refresh_id"] == refresh_id
    assert result["materialization"]["name"] == "evidence_graph_substrate"
    assert result["reason"] is not None
    assert "not_a_real_project" in result["reason"]
    assert result["periods"] == []


def test_engineering_throughput_rejects_unsupported_granularity() -> None:
    result = engineering_throughput(
        project="lp",
        granularity="fortnight",
    )
    assert result["degraded"] is True
    assert result["materialized_refresh_id"] is None
    assert result["refresh_id"] is None
    assert "fortnight" in (result["reason"] or "")
    assert result["periods"] == []


def test_engineering_throughput_granularity_index_reveals_atomic_commits(
    monkeypatch, tmp_path
) -> None:
    """Atomic commits (many commits, few lines each) produce high index;
    fat squash commits (few commits, many lines) produce low index.
    """
    db_path, refresh_id = _make_substrate(tmp_path)
    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: str(db_path))

    result = engineering_throughput(project="lp", granularity="week", refresh_id=refresh_id)
    week1 = result["periods"][0]
    # Week 1: 2 commits / 250 clean lines = 8.0 per 1000 lines
    assert week1["granularity_index"] == pytest.approx(2 / 250 * 1000, rel=0.01)


def test_engineering_throughput_granularity_index_is_none_when_zero_clean_lines(
    monkeypatch, tmp_path
) -> None:
    """granularity_index is None (not NaN or infinity) when clean_la == 0."""
    import duckdb

    db_path = tmp_path / "substrate.duckdb"
    refresh_id = "test:2026-05-01:2026-05-31:all"
    conn = duckdb.connect(str(db_path))

    # Minimal commit_fact schema
    conn.execute(
        """
        CREATE TABLE commit_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            lines_added INTEGER,
            lines_deleted INTEGER,
            files_changed INTEGER,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE file_change_fact (
            sha VARCHAR,
            project VARCHAR,
            authored_at TIMESTAMPTZ,
            path VARCHAR,
            lines_added INTEGER,
            lines_deleted INTEGER,
            refresh_id VARCHAR
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE symbol_change (
            sha VARCHAR,
            project VARCHAR,
            date DATE,
            change_type VARCHAR,
            refresh_id VARCHAR
        )
        """
    )

    # Insert a commit with only lockfile changes (excluded from clean count)
    conn.execute(
        "INSERT INTO commit_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["sha1", "test", datetime(2026, 5, 4, 10, tzinfo=timezone.utc), 0, 0, 1, refresh_id],
    )
    # All files are lockfiles (non-code) with zero lines
    conn.execute(
        "INSERT INTO file_change_fact VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["sha1", "test", datetime(2026, 5, 4, 10, tzinfo=timezone.utc), "Cargo.lock", 0, 0, refresh_id],
    )

    conn.close()

    monkeypatch.setattr("lynchpin.substrate.connection.substrate_path", lambda: str(db_path))

    result = engineering_throughput(project="test", granularity="week", refresh_id=refresh_id)
    period = result["periods"][0]
    # clean_la == 0, so granularity_index should be None
    assert period["granularity_index"] is None
    # Verify clean lines are zero
    assert period["lines_added_clean"] == 0
    assert period["commit_count"] == 1
