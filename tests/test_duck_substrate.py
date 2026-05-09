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

Tests that depend on promote.py / reader.py landing from parallel teammates are
decorated with @pytest.mark.xfail(strict=False) so the suite stays importable
and the test file can be committed independently.
"""

from __future__ import annotations

import pytest
from datetime import date, datetime, timezone
from pathlib import Path

UTC = timezone.utc

# ── helpers ─────────────────────────────────────────────────────────────────


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


# ── schema / connection tests (no promote/reader dependency) ─────────────────


def test_apply_schema_creates_all_tables(tmp_path: Path) -> None:
    """apply_schema must create all domain tables + substrate_meta."""
    from lynchpin.duck.connection import apply_schema, connect

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
    }
    assert expected == table_names


def test_apply_schema_is_idempotent(tmp_path: Path) -> None:
    """Calling apply_schema twice must be a no-op; existing rows survive."""
    from lynchpin.duck.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO substrate_meta VALUES ('canary', 'alive')"
        )
        apply_schema(conn)  # second call
        row = conn.execute(
            "SELECT value FROM substrate_meta WHERE key = 'canary'"
        ).fetchone()

    assert row is not None
    assert row[0] == "alive"


def test_apply_schema_recreates_on_version_bump(tmp_path: Path) -> None:
    """Downgrading the stored version triggers drop+recreate; commit_fact is empty afterward."""
    from lynchpin.duck.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        conn.execute(
            "INSERT INTO commit_fact "
            "(sha, repo, authored_at, lines_added, lines_deleted, lines_changed, "
            "files_changed, paths, path_roots, refresh_id) "
            "VALUES ('abc', 'r', '2026-01-01 00:00:00+00', 1, 0, 1, 1, [], [], 'r1')"
        )
        # Simulate old version stored
        conn.execute("UPDATE substrate_meta SET value='0' WHERE key='version'")
        apply_schema(conn)  # must drop + recreate
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 0


def test_substrate_path_uses_local_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """substrate_path() must return a path under LynchpinConfig.local_root."""
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    # Clear module-level cached config if any
    import importlib
    import lynchpin.core.config as cfg_mod
    importlib.reload(cfg_mod)

    from lynchpin.duck.connection import substrate_path

    path = substrate_path()
    assert str(tmp_path / "local") in str(path)
    assert path.suffix == ".duckdb"


def test_concurrent_writers_documented_constraint(tmp_path: Path) -> None:
    """Single-writer-many-readers constraint: open read_only after writer creates the file."""
    from lynchpin.duck.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    # Writer creates schema
    with connect(db) as writer:
        apply_schema(writer)

    # Reader opens read_only — must not error
    with connect(db, read_only=True) as reader:
        tables = reader.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    assert any("commit_fact" in r[0] for r in tables)


# ── promote / reader tests (depend on parallel teammates' commits) ────────────
# All these tests import from lynchpin.duck.promote and lynchpin.duck.reader.
# Those modules are written by parallel teammates and may not be present yet.
# xfail(strict=False) allows the suite to be committed and the tests to pass
# once the modules land, without requiring changes here.


def _try_import_promote_reader():
    """Return (promote_mod, reader_mod) or skip the test if modules absent."""
    try:
        import lynchpin.duck.promote as promote_mod
        import lynchpin.duck.reader as reader_mod
        return promote_mod, reader_mod
    except ImportError as exc:
        pytest.skip(f"promote/reader not yet available: {exc}")


@pytest.fixture
def fresh_db(tmp_path: Path):
    """Yield a (conn, path) tuple with schema applied."""
    from lynchpin.duck.connection import apply_schema, connect
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
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    facts = [
        _make_commit_fact("sha001", repo="lynchpin", project="lynchpin"),
        _make_commit_fact("sha002", repo="sinex", project="sinex"),
        _make_commit_fact("sha003", repo="polylogue", project="polylogue"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = reader_mod.load_commit_facts(conn, refresh_id="r1")

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
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    facts = [_make_commit_fact(f"sha{i:03d}") for i in range(3)]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        promote_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        count = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]

    assert count == 3


def test_promote_commits_partition_isolation(tmp_path: Path) -> None:
    """Two refresh_ids stay isolated; total count = sum; per-id load returns correct slice."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    facts_r1 = [_make_commit_fact(f"r1sha{i:03d}") for i in range(3)]
    facts_r2 = [_make_commit_fact(f"r2sha{i:03d}") for i in range(2)]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_commits(conn, facts=facts_r1, refresh_id="r1")
        promote_mod.promote_commits(conn, facts=facts_r2, refresh_id="r2")
        total = conn.execute("SELECT COUNT(*) FROM commit_fact").fetchone()[0]
        loaded_r1 = reader_mod.load_commit_facts(conn, refresh_id="r1")

    assert total == 5
    assert len(loaded_r1) == 3
    assert all(c.commit.startswith("r1") for c in loaded_r1)


def test_load_commit_facts_filters_by_date(tmp_path: Path) -> None:
    """Load with start/end covers middle 3 of 5 days; only 3 returned."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    # day_offset 0..4 → May 1..5
    facts = [_make_commit_fact(f"sha{i:03d}", day_offset=i) for i in range(5)]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = reader_mod.load_commit_facts(
            conn,
            start=date(2026, 5, 2),
            end=date(2026, 5, 4),
        )

    assert len(loaded) == 3


def test_load_commit_facts_filters_by_project(tmp_path: Path) -> None:
    """Load filtered by projects= returns only matching rows."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    facts = [
        _make_commit_fact("sha001", repo="lynchpin", project="lynchpin"),
        _make_commit_fact("sha002", repo="sinex", project="sinex"),
        _make_commit_fact("sha003", repo="polylogue", project="polylogue"),
    ]
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_commits(conn, facts=facts, refresh_id="r1")
        loaded = reader_mod.load_commit_facts(conn, projects=("lynchpin",))

    assert len(loaded) == 1
    assert loaded[0].repo == "lynchpin"


# ── GitFileChangeFact round-trip ─────────────────────────────────────────────

def test_promote_file_changes_round_trip(tmp_path: Path) -> None:
    """Promote GitFileChangeFact rows and load them back structurally equal."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect
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
        promote_mod.promote_file_changes(conn, facts=facts, refresh_id="r1")
        loaded = reader_mod.load_file_change_facts(conn, refresh_id="r1")

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
    )


def test_promote_ai_work_events_round_trip_no_classifier(tmp_path: Path) -> None:
    """Promote WorkEvent rows (some with null timestamps) without classifier and load back."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    events = [
        _make_work_event("ev001", with_timestamps=True),
        _make_work_event("ev002", with_timestamps=False),
        _make_work_event("ev003", kind="research", with_timestamps=True),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_ai_work_events(conn, events=events, refresh_id="r1")
        loaded = reader_mod.load_ai_work_events(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_by_id = {row.event_id: row for row in loaded}

    ev1 = loaded_by_id["ev001"]
    assert ev1.kind == "implementation"
    assert tuple(ev1.file_paths) == ("src/ev001.py", "pyproject.toml")
    assert tuple(ev1.tools_used) == ("Read", "Edit")
    assert ev1.duration_ms == 3600_000
    assert ev1.start is not None
    assert ev1.end is not None

    ev2 = loaded_by_id["ev002"]
    assert ev2.start is None
    assert ev2.end is None


def test_promote_ai_work_events_with_classifier(tmp_path: Path) -> None:
    """Promote with a stub classifier; labels land in ai_work_event; load_ai_work_event_labels works."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect
    from lynchpin.composite.work_event_kind import WorkEventKindLabel

    stub_label = WorkEventKindLabel(
        kind="implementation",
        confidence=0.9,
        source="agreement",
        tier="high",
        polylogue_kind="implementation",
        polylogue_confidence=0.7,
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
        promote_mod.promote_ai_work_events(
            conn, events=events, refresh_id="r1", classifier=_stub_classifier
        )

        # Plain load still returns WorkEvent shape
        plain = reader_mod.load_ai_work_events(conn, refresh_id="r1")
        assert len(plain) == 2

        # Label load returns WorkEventKindLabel shape
        labels = reader_mod.load_ai_work_event_labels(conn, refresh_id="r1")
        assert len(labels) == 2
        for lbl in labels.values():
            assert lbl.kind == "implementation"
            assert lbl.tier == "high"
            assert lbl.source == "agreement"


def test_load_ai_work_events_min_kind_tier(tmp_path: Path) -> None:
    """Load with min_kind_tier='medium' returns only medium+high rows (2 of 3)."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect
    from lynchpin.composite.work_event_kind import WorkEventKindLabel

    def _classifier_by_tier(tier: str):
        def _clf(event):
            return WorkEventKindLabel(
                kind="implementation",
                confidence=0.8,
                source="polylogue",
                tier=tier,  # type: ignore[arg-type]
                polylogue_kind="implementation",
                polylogue_confidence=0.8,
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
            promote_mod.promote_ai_work_events(
                conn, events=[event], refresh_id=f"r{idx}", classifier=_classifier_by_tier(tier)
            )

        loaded = reader_mod.load_ai_work_events(conn, min_kind_tier="medium")

    assert len(loaded) == 2
    assert all(row.event_id in ("ev021", "ev022") for row in loaded)


# ── pr_review_row round-trip ─────────────────────────────────────────────────

def _make_pr_row(number: int, state: str = "merged", friction_signals: tuple[str, ...] = ()) -> dict:
    return {
        "project": "lynchpin",
        "number": number,
        "title": f"feat: PR #{number}",
        "state": state,
        "url": f"https://github.com/sinity/lynchpin/pull/{number}",
        "author": "Sinity",
        "created_at": "2026-05-01T10:00:00+00:00",
        "closed_at": "2026-05-02T10:00:00+00:00" if state in ("merged", "closed") else None,
        "merged_at": "2026-05-02T10:00:00+00:00" if state == "merged" else None,
        "review_count": 2,
        "review_decisions": ("APPROVED",),
        "review_round_count": 1,
        "reviewer_count": 1,
        "reviewers": ("reviewer-a",),
        "review_comment_count": 3,
        "top_level_comment_count": 1,
        "changes_requested_count": 0,
        "approval_count": 1,
        "dismissed_count": 0,
        "time_to_first_review_minutes": 60.0,
        "time_to_close_minutes": 1440.0,
        "time_to_merge_minutes": 1440.0 if state == "merged" else None,
        "final_decision": "APPROVED",
        "friction_signals": friction_signals,
    }


def test_promote_pr_review_rows_round_trip(tmp_path: Path) -> None:
    """Promote PrReviewRow dicts and load them back; assert structural equality."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    rows = [
        _make_pr_row(1, state="merged", friction_signals=("many_rounds",)),
        _make_pr_row(2, state="open"),
        _make_pr_row(3, state="closed"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_pr_review_rows(conn, rows=rows, refresh_id="r1")
        loaded = reader_mod.load_pr_review_rows(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_by_num = {r.number: r for r in loaded}
    pr1 = loaded_by_num[1]
    assert pr1.project == "lynchpin"
    assert pr1.state == "merged"
    assert tuple(pr1.friction_signals) == ("many_rounds",)
    pr2 = loaded_by_num[2]
    assert pr2.state == "open"


def test_load_pr_review_rows_only_with_friction(tmp_path: Path) -> None:
    """Load with only_with_friction=True returns only PRs with non-empty friction_signals."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    rows = [
        _make_pr_row(10, friction_signals=("many_rounds",)),
        _make_pr_row(11, friction_signals=("slow_merge",)),
        _make_pr_row(12, friction_signals=()),
        _make_pr_row(13, friction_signals=()),
        _make_pr_row(14, friction_signals=()),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_pr_review_rows(conn, rows=rows, refresh_id="r1")
        loaded = reader_mod.load_pr_review_rows(conn, only_with_friction=True)

    assert len(loaded) == 2
    assert all(len(r.friction_signals) > 0 for r in loaded)


# ── symbol_change round-trip ─────────────────────────────────────────────────

def _make_symbol_change_row(sha: str, qualified_name: str) -> dict:
    return {
        "project": "lynchpin",
        "sha": sha,
        "date": date(2026, 5, 1),
        "path": "lynchpin/core/config.py",
        "change_type": "M",
        "qualified_name": qualified_name,
        "symbol_kind": "function",
        "exported": True,
        "breaking_candidate": False,
    }


def test_promote_symbol_changes_round_trip(tmp_path: Path) -> None:
    """Promote symbol_change dicts and load them back; assert structural equality."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    rows = [
        _make_symbol_change_row("sha001", "lynchpin.core.config.get_config"),
        _make_symbol_change_row("sha001", "lynchpin.core.config.LynchpinConfig"),
        _make_symbol_change_row("sha002", "lynchpin.sources.git.commits_in_range"),
    ]

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_symbol_changes(conn, rows=rows, refresh_id="r1")
        loaded = reader_mod.load_symbol_changes(conn, refresh_id="r1")

    assert len(loaded) == 3
    loaded_names = {r["qualified_name"] if isinstance(r, dict) else r.qualified_name for r in loaded}
    assert "lynchpin.core.config.get_config" in loaded_names
    assert "lynchpin.sources.git.commits_in_range" in loaded_names


# ── evidence_graph round-trip tests (Arc 2.2) ────────────────────────────────


def _make_evidence_graph(
    start: date = date(2026, 5, 1),
    end: date = date(2026, 5, 7),
    mode: str = "local-fast",
    node_suffix: str = "",
) -> "Any":  # EvidenceGraph
    from lynchpin.composite.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
    from lynchpin.composite.evidence import EvidenceCaveat, EvidenceProvenance

    generated_at = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

    nodes = (
        EvidenceNode(
            id=f"commit:{node_suffix}sha001",
            kind="commit",
            source="git",
            date=date(2026, 5, 1),
            project="lynchpin",
            summary="feat: add evidence graph promotion",
            start=_dt(2026, 5, 1, 10),
            end=_dt(2026, 5, 1, 11),
            payload={"lines_added": 42, "subject": "feat: add evidence graph promotion"},
        ),
        EvidenceNode(
            id=f"ai_work:{node_suffix}ev001",
            kind="ai_work_event",
            source="polylogue",
            date=date(2026, 5, 2),
            project="lynchpin",
            summary="implementation session — evidence graph bridge",
            provenance=EvidenceProvenance(
                source="polylogue",
                cost="local-fast",
                path=None,
                generated_at=generated_at,
                note="test provenance",
            ),
        ),
        EvidenceNode(
            id=f"github:{node_suffix}pr99",
            kind="github_pr",
            source="github",
            date=date(2026, 5, 3),
            project="lynchpin",
            summary="PR #99: evidence graph promotion",
            url="https://github.com/sinity/lynchpin/pull/99",
            caveats=(
                EvidenceCaveat(source="github", status="partial", message="test caveat"),
            ),
        ),
    )

    edges = (
        EvidenceEdge(
            source_id=f"commit:{node_suffix}sha001",
            target_id=f"ai_work:{node_suffix}ev001",
            relation="same_project_day",
            evidence="shared project lynchpin on 2026-05-01",
            weight=1.0,
        ),
        EvidenceEdge(
            source_id=f"ai_work:{node_suffix}ev001",
            target_id=f"github:{node_suffix}pr99",
            relation="references",
            evidence="ai session references PR #99",
            weight=0.8,
        ),
    )

    return EvidenceGraph(
        start=start,
        end=end,
        generated_at=generated_at,
        mode=mode,  # type: ignore[arg-type]
        nodes=nodes,
        edges=edges,
        caveats=(),
    )


def test_promote_evidence_graph_round_trip(tmp_path: Path) -> None:
    """Promote a 3-node/2-edge graph and load it back; structural equality."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    graph = _make_evidence_graph()
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        counts = promote_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        loaded = reader_mod.load_evidence_graph(conn, refresh_id="r1")

    assert counts == {"build": 1, "nodes": 3, "edges": 2}
    assert loaded is not None

    # Window and metadata
    assert loaded.start == graph.start
    assert loaded.end == graph.end
    assert loaded.mode == graph.mode
    # Compare as UTC instants — DuckDB may return TIMESTAMPTZ in local tz.
    loaded_ga_utc = loaded.generated_at.astimezone(UTC).replace(tzinfo=None)
    graph_ga_utc = graph.generated_at.astimezone(UTC).replace(tzinfo=None)
    assert loaded_ga_utc == graph_ga_utc

    assert len(loaded.nodes) == 3
    assert len(loaded.edges) == 2

    # Check node with payload
    commit_node = next(n for n in loaded.nodes if n.kind == "commit")
    assert commit_node.payload is not None
    assert commit_node.payload.get("lines_added") == 42

    # Check node with provenance
    ai_node = next(n for n in loaded.nodes if n.kind == "ai_work_event")
    assert ai_node.provenance is not None
    assert ai_node.provenance.source == "polylogue"
    assert ai_node.provenance.note == "test provenance"

    # Check node with caveats
    gh_node = next(n for n in loaded.nodes if n.kind == "github_pr")
    assert len(gh_node.caveats) == 1
    assert gh_node.caveats[0].source == "github"
    assert gh_node.caveats[0].status == "partial"
    assert gh_node.url == "https://github.com/sinity/lynchpin/pull/99"

    # Edges
    loaded_relations = {(e.source_id, e.target_id, e.relation) for e in loaded.edges}
    assert ("commit:sha001", "ai_work:ev001", "same_project_day") in loaded_relations
    assert ("ai_work:ev001", "github:pr99", "references") in loaded_relations


def test_promote_evidence_graph_idempotent(tmp_path: Path) -> None:
    """Promoting the same graph twice under refresh_id='r1' must not double rows."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    graph = _make_evidence_graph()
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        promote_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)

        node_count = conn.execute(
            "SELECT COUNT(*) FROM evidence_node WHERE refresh_id = 'r1'"
        ).fetchone()[0]
        edge_count = conn.execute(
            "SELECT COUNT(*) FROM evidence_edge WHERE refresh_id = 'r1'"
        ).fetchone()[0]

    assert node_count == 3
    assert edge_count == 2


def test_promote_evidence_graph_partition_isolation(tmp_path: Path) -> None:
    """Two graphs with different refresh_ids have independent nodes."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    graph_r1 = _make_evidence_graph(node_suffix="r1_")
    graph_r2 = _make_evidence_graph(node_suffix="r2_")

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph_r1)
        promote_mod.promote_evidence_graph(conn, refresh_id="r2", graph=graph_r2)

        loaded_r1 = reader_mod.load_evidence_graph(conn, refresh_id="r1")
        loaded_r2 = reader_mod.load_evidence_graph(conn, refresh_id="r2")

    assert loaded_r1 is not None
    assert loaded_r2 is not None

    ids_r1 = {n.id for n in loaded_r1.nodes}
    ids_r2 = {n.id for n in loaded_r2.nodes}
    # No overlap — each graph has its own unique node IDs
    assert ids_r1.isdisjoint(ids_r2)
    assert all("r1_" in nid for nid in ids_r1)
    assert all("r2_" in nid for nid in ids_r2)


def test_load_evidence_graph_by_window(tmp_path: Path) -> None:
    """load_evidence_graph with start/end/mode (no refresh_id) finds the graph."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    start = date(2026, 5, 1)
    end = date(2026, 5, 7)
    mode = "local-fast"
    graph = _make_evidence_graph(start=start, end=end, mode=mode)

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        loaded = reader_mod.load_evidence_graph(conn, start=start, end=end, mode=mode)

    assert loaded is not None
    assert loaded.start == start
    assert loaded.end == end
    assert loaded.mode == mode
    assert len(loaded.nodes) == 3
    assert len(loaded.edges) == 2


def test_load_evidence_graph_returns_none_when_missing(tmp_path: Path) -> None:
    """load_evidence_graph returns None when no matching build exists."""
    promote_mod, reader_mod = _try_import_promote_reader()
    from lynchpin.duck.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        result = reader_mod.load_evidence_graph(conn, refresh_id="nonexistent")

    assert result is None


def test_finalize_graph_writes_substrate_when_flag_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_finalize_graph writes to substrate when LYNCHPIN_SUBSTRATE_WRITE=1."""
    import importlib
    from lynchpin.duck.connection import apply_schema, connect

    # Point LYNCHPIN_LOCAL_ROOT to tmp_path so substrate_path() writes there.
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path / "local"))
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_WRITE", "1")

    # Reload config so it picks up the new env var.
    import lynchpin.core.config as cfg_mod
    importlib.reload(cfg_mod)

    from lynchpin.composite.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode, _finalize_graph
    from lynchpin.composite.evidence import EvidenceCaveat

    nodes = [
        EvidenceNode(
            id="test:node1",
            kind="commit",
            source="git",
            date=date(2026, 5, 1),
            project="lynchpin",
            summary="test node",
        ),
    ]
    edges: list[EvidenceEdge] = []

    result = _finalize_graph(
        nodes=nodes,
        edges=edges,
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        mode="local-fast",  # type: ignore[arg-type]
        generated_at=_dt(2026, 5, 1, 12),
    )

    # The function must return a valid EvidenceGraph regardless of substrate write.
    assert isinstance(result, EvidenceGraph)
    assert len(result.nodes) >= 1  # deduplication may keep or drop

    # Verify the substrate was written.
    from lynchpin.duck.connection import substrate_path
    from lynchpin.duck.reader import load_evidence_graph

    substrate = substrate_path()
    assert substrate.exists(), "Substrate file must have been created by the write."

    with connect(substrate) as conn:
        apply_schema(conn)  # idempotent — tables already there
        loaded = load_evidence_graph(
            conn,
            start=date(2026, 5, 1),
            end=date(2026, 5, 1),
            mode="local-fast",
        )

    assert loaded is not None
    assert loaded.start == date(2026, 5, 1)

    # Cleanup: reset module state
    importlib.reload(cfg_mod)


def test_finalize_graph_substrate_write_fails_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_finalize_graph returns a valid graph even when the substrate write fails."""
    import importlib

    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_WRITE", "1")
    # Point substrate to an unwriteable location.
    unwriteable = tmp_path / "no_such_dir" / "substrate.duckdb"
    # Override substrate_path to return the unwriteable path.
    import lynchpin.duck.connection as duck_conn
    monkeypatch.setattr(duck_conn, "substrate_path", lambda: unwriteable)

    from lynchpin.composite.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode, _finalize_graph

    nodes = [
        EvidenceNode(
            id="test:failnode1",
            kind="commit",
            source="git",
            date=date(2026, 5, 1),
            project="lynchpin",
            summary="test fail node",
        ),
    ]

    # Must not raise — best-effort write, errors are logged not raised.
    result = _finalize_graph(
        nodes=nodes,
        edges=[],
        start=date(2026, 5, 1),
        end=date(2026, 5, 1),
        mode="local-fast",  # type: ignore[arg-type]
        generated_at=_dt(2026, 5, 1, 12),
    )

    assert isinstance(result, EvidenceGraph)
    assert len(result.nodes) >= 1
