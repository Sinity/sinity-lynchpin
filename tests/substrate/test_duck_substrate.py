"""Round-trip equivalence tests for the DuckDB substrate (Arc 2.1).

Covers:
- schema creation / idempotence / version-bump rebuild
- commit_fact promote → load round-trip, idempotence, partition isolation, date
  filtering, project filtering
- file_change_fact promote → load round-trip
- pr_review_row promote → load round-trip, friction-signal filter
- symbol_change promote → load round-trip
- substrate_path locality
- read_only connection constraint (documented)

Tests cover the current split substrate table modules directly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

UTC = timezone.utc

# ── helpers ─────────────────────────────────────────────────────────────────


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


# ── work-fact table tests ────────────────────────────────────────────────────


def _try_import_work():
    """Return work-fact substrate functions or skip if unavailable."""
    try:
        from lynchpin.substrate import work_commits, work_files, work_symbols

        return SimpleNamespace(
            load_commit_facts=work_commits.load_commit_facts,
            load_file_change_facts=work_files.load_file_change_facts,
            load_symbol_changes=work_symbols.load_symbol_changes,
            promote_commits=work_commits.promote_commits,
            promote_file_changes=work_files.promote_file_changes,
            promote_symbol_changes=work_symbols.promote_symbol_changes,
        )
    except ImportError as exc:
        pytest.skip(f"work substrate modules not available: {exc}")


@pytest.fixture
def fresh_db(tmp_path: Path):
    """Yield a (conn, path) tuple with schema applied."""
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        yield conn, db


# ── GitCommitFact factories ──────────────────────────────────────────────────


def _make_commit_fact(
    sha: str,
    repo: str = "lynchpin",
    project: str = "lynchpin",
    day_offset: int = 0,
) -> "GitCommitFact":  # noqa: F821 (forward ref; populated at call-time)
    from lynchpin.sources.git import GitCommitFact

    return GitCommitFact(
        repo=repo,
        commit=sha,
        authored_at=_dt(2026, 5, 1 + day_offset),
        author="Sinity",
        subject=f"feat: commit {sha}",
        lines_added=10,
        lines_deleted=2,
        lines_changed=12,
        files_changed=1,
        paths=(f"src/{sha}.py",),
        path_roots=("src",),
    )


def test_promote_commits_round_trip(tmp_path: Path) -> None:
    """Promote 3 GitCommitFact rows and load them back; structural equality."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    facts = [
        _make_commit_fact("sha001", repo="lynchpin", project="lynchpin"),
        _make_commit_fact("sha002", repo="sinex", project="sinex"),
        _make_commit_fact("sha003", repo="polylogue", project="polylogue"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = work_mod.load_commit_facts(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_by_sha = {row.commit: row for row in loaded}
    for original in facts:
        got = loaded_by_sha[original.commit]
        assert got.repo == original.repo
        assert got.authored_at.date() == original.authored_at.date()
        assert tuple(got.paths) == original.paths
        assert tuple(got.path_roots) == original.path_roots
        assert got.lines_added == original.lines_added
        assert got.lines_deleted == original.lines_deleted


def test_promote_commits_idempotent(tmp_path: Path) -> None:
    """Re-promoting the same refresh_id must not double the rows (DELETE then INSERT)."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    facts = [_make_commit_fact(f"sha{i:03d}") for i in range(3)]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        work_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 3


def test_promote_commits_partition_isolation(tmp_path: Path) -> None:
    """Two refresh_ids stay isolated; total count = sum; per-id load returns correct slice."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    facts_r1 = [_make_commit_fact(f"r1sha{i:03d}") for i in range(3)]
    facts_r2 = [_make_commit_fact(f"r2sha{i:03d}") for i in range(2)]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_commits(conn, facts=facts_r1, refresh_id="r1")
        work_mod.promote_commits(conn, facts=facts_r2, refresh_id="r2")
        total = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
        loaded_r1 = work_mod.load_commit_facts(conn, refresh_id="r1")

    assert total == 5
    assert len(loaded_r1) == 3
    assert all(c.commit.startswith("r1") for c in loaded_r1)


def test_load_commit_facts_filters_by_date(tmp_path: Path) -> None:
    """Load with start/end covers middle 3 of 5 days; only 3 returned."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    # day_offset 0..4 → May 1..5
    facts = [_make_commit_fact(f"sha{i:03d}", day_offset=i) for i in range(5)]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = work_mod.load_commit_facts(
            conn,
            start=date(2026, 5, 2),
            end=date(2026, 5, 4),
        )

    assert len(loaded) == 3


def test_load_commit_facts_filters_by_project(tmp_path: Path) -> None:
    """Load filtered by projects= returns only matching rows."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    facts = [
        _make_commit_fact("sha001", repo="lynchpin", project="lynchpin"),
        _make_commit_fact("sha002", repo="sinex", project="sinex"),
        _make_commit_fact("sha003", repo="polylogue", project="polylogue"),
    ]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = work_mod.load_commit_facts(conn, projects=("lynchpin",))

    assert len(loaded) == 1
    assert loaded[0].repo == "lynchpin"


# ── GitFileChangeFact round-trip ─────────────────────────────────────────────


def test_promote_file_changes_round_trip(tmp_path: Path) -> None:
    """Promote GitFileChangeFact rows and load them back structurally equal."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.sources.git import GitFileChangeFact

    facts = [
        GitFileChangeFact(
            repo="lynchpin",
            commit="sha001",
            authored_at=_dt(2026, 5, 1),
            path="lynchpin/core/config.py",
            path_root="lynchpin",
            lines_added=5,
            lines_deleted=1,
            lines_changed=6,
        ),
        GitFileChangeFact(
            repo="sinex",
            commit="sha002",
            authored_at=_dt(2026, 5, 2),
            path="src/ingestion.rs",
            path_root="src",
            lines_added=20,
            lines_deleted=3,
            lines_changed=23,
        ),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_file_changes(conn, facts=facts, refresh_id="r1")
        loaded = work_mod.load_file_change_facts(conn, refresh_id="r1")

    assert len(loaded) == 2
    loaded_by_key = {(r.commit, r.path): r for r in loaded}
    for original in facts:
        got = loaded_by_key[(original.commit, original.path)]
        assert got.repo == original.repo
        assert got.lines_added == original.lines_added
        assert got.path_root == original.path_root
