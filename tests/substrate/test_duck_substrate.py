"""Round-trip equivalence tests for the DuckDB substrate (Arc 2.1).

Covers:
- schema creation / idempotence / version-bump rebuild
- commit_fact promote → load round-trip, idempotence, partition isolation, date
  filtering, project filtering
- file_change_fact promote → load round-trip
- ai_work_event promote → load (with and without classifier), min_kind_tier filter
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
        from lynchpin.substrate import work_ai, work_commits, work_files, work_symbols

        return SimpleNamespace(
            load_ai_work_event_labels=work_ai.load_ai_work_event_labels,
            load_ai_work_events=work_ai.load_ai_work_events,
            load_commit_facts=work_commits.load_commit_facts,
            load_file_change_facts=work_files.load_file_change_facts,
            load_symbol_changes=work_symbols.load_symbol_changes,
            promote_ai_work_events=work_ai.promote_ai_work_events,
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


# ── ai_work_event round-trips ────────────────────────────────────────────────


def _make_work_event(
    event_id: str,
    kind: str = "implementation",
    with_timestamps: bool = True,
    project: str = "lynchpin",
    workflow_shape: str | None = None,
    terminal_state: str | None = None,
) -> "WorkEvent":  # noqa: F821
    from lynchpin.sources.polylogue import WorkEvent

    start = _dt(2026, 5, 1, 10) if with_timestamps else None
    end = _dt(2026, 5, 1, 11) if with_timestamps else None
    return WorkEvent(
        event_id=event_id,
        conversation_id=f"conv-{event_id}",
        provider="claude-code",
        kind=kind,
        confidence=0.85,
        start=start,
        end=end,
        duration_ms=3600_000 if with_timestamps else 0,
        file_paths=(f"src/{event_id}.py", "pyproject.toml"),
        tools_used=("Read", "Edit"),
        summary=f"work event {event_id}",
        workflow_shape=workflow_shape,
        workflow_shape_confidence=0.86 if workflow_shape else 0.0,
        terminal_state=terminal_state,
        terminal_state_confidence=0.72 if terminal_state else 0.0,
    )


def test_promote_ai_work_events_round_trip_no_classifier(tmp_path: Path) -> None:
    """Promote WorkEvent rows (some with null timestamps) without classifier and load back."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    events = [
        _make_work_event(
            "ev001",
            with_timestamps=True,
            workflow_shape="agentic_loop",
            terminal_state="tool_left",
        ),
        _make_work_event("ev002", with_timestamps=False),
        _make_work_event("ev003", kind="research", with_timestamps=True),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_ai_work_events(conn, events=events, refresh_id="r1")
        loaded = work_mod.load_ai_work_events(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_by_id = {row.event_id: row for row in loaded}

    ev1 = loaded_by_id["ev001"]
    assert ev1.kind == "implementation"
    assert tuple(ev1.file_paths) == ("src/ev001.py", "pyproject.toml")
    assert tuple(ev1.tools_used) == ("Read", "Edit")
    assert ev1.duration_ms == 3600_000
    assert ev1.start is not None
    assert ev1.end is not None
    assert ev1.workflow_shape == "agentic_loop"
    assert ev1.workflow_shape_confidence == 0.86
    assert ev1.terminal_state == "tool_left"
    assert ev1.terminal_state_confidence == 0.72

    ev2 = loaded_by_id["ev002"]
    assert ev2.start is None
    assert ev2.end is None


def test_promote_ai_work_events_deduplicates_source_event_ids(tmp_path: Path) -> None:
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect

    first = _make_work_event("ev_duplicate", kind="research")
    second = _make_work_event("ev_duplicate", kind="implementation")

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        written = work_mod.promote_ai_work_events(
            conn,
            events=[first, second],
            refresh_id="r1",
        )
        loaded = work_mod.load_ai_work_events(conn, refresh_id="r1")

    assert written == 1
    assert len(loaded) == 1
    assert loaded[0].kind == "implementation"


def test_promote_ai_work_events_resolves_project_from_paths(tmp_path: Path) -> None:
    """Project resolver output is persisted for SQL joins and project-day analysis."""
    work_mod = _try_import_work()
    from lynchpin.core.classify import resolve_project
    from lynchpin.substrate.connection import apply_schema, connect

    event = _make_work_event("ev_project")
    event = type(event)(
        event_id=event.event_id,
        conversation_id=event.conversation_id,
        provider=event.provider,
        kind=event.kind,
        confidence=event.confidence,
        start=event.start,
        end=event.end,
        duration_ms=event.duration_ms,
        file_paths=(
            "/realm/project/sinnix/modules/services/machine-telemetry.nix",
            "modules/services/machine-telemetry.nix",
        ),
        tools_used=event.tools_used,
        summary=event.summary,
    )

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_ai_work_events(
            conn,
            events=[event],
            refresh_id="r1",
            project_resolver=lambda ev: next(
                (
                    project
                    for project in (resolve_project(path) for path in ev.file_paths)
                    if project
                ),
                None,
            ),
        )
        row = conn.execute(
            "SELECT project FROM ai_work_event WHERE refresh_id = 'r1'"
        ).fetchone()

    assert row == ("sinnix",)


def test_promote_ai_work_events_with_classifier(tmp_path: Path) -> None:
    """Promote with a stub classifier; labels land in ai_work_event; load_ai_work_event_labels works."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.core.work_event_kind import WorkEventKindLabel

    stub_label = WorkEventKindLabel(
        kind="implementation",
        confidence=0.9,
        source="agreement",
        tier="high",
        source_kind="implementation",
        source_confidence=0.7,
        overlay_kind="implementation",
        overlay_confidence=0.85,
        features={},
    )

    def _stub_classifier(event):
        return stub_label

    events = [_make_work_event("ev010"), _make_work_event("ev011")]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        work_mod.promote_ai_work_events(
            conn, events=events, refresh_id="r1", classifier=_stub_classifier
        )

        # Plain load still returns WorkEvent shape
        plain = work_mod.load_ai_work_events(conn, refresh_id="r1")
        assert len(plain) == 2

        # Label load returns WorkEventKindLabel shape
        labels = work_mod.load_ai_work_event_labels(conn, refresh_id="r1")
        assert len(labels) == 2
        for lbl in labels.values():
            assert lbl.kind == "implementation"
            assert lbl.tier == "high"
            assert lbl.source == "agreement"


def test_load_ai_work_events_min_kind_tier(tmp_path: Path) -> None:
    """Load with min_kind_tier='medium' returns only medium+high rows (2 of 3)."""
    work_mod = _try_import_work()
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.core.work_event_kind import WorkEventKindLabel

    def _classifier_by_tier(tier: str):
        def _clf(event):
            return WorkEventKindLabel(
                kind="implementation",
                confidence=0.8,
                source="source",
                tier=tier,  # type: ignore[arg-type]
                source_kind="implementation",
                source_confidence=0.8,
                overlay_kind=None,
                overlay_confidence=0.0,
                features={},
            )

        return _clf

    events_tiers = [
        (_make_work_event("ev020"), "low"),
        (_make_work_event("ev021"), "medium"),
        (_make_work_event("ev022"), "high"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        for idx, (event, tier) in enumerate(events_tiers):
            work_mod.promote_ai_work_events(
                conn,
                events=[event],
                refresh_id=f"r{idx}",
                classifier=_classifier_by_tier(tier),
            )

        loaded = work_mod.load_ai_work_events(conn, min_kind_tier="medium")

    assert len(loaded) == 2
    assert all(row.event_id in ("ev021", "ev022") for row in loaded)
