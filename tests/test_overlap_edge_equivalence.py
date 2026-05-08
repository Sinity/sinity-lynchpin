"""Equivalence tests: SQL views must produce same edge sets as Python overlap builders.

When this passes, Arc 2.3b can delete the Python implementations.

Test strategy:
- Build GitCommitFact and WorkEvent instances in Python.
- Promote them to an in-memory DuckDB substrate via the Phase 2.1 promoters.
- Run the SQL reader (compute_file_overlap_edges / compute_symbol_overlap_edges).
- Hand-build EvidenceNode instances with the same data and run the Python builders.
- Compare edge sets as frozensets of (source_id, target_id, relation, evidence, weight).

Node ID format must match evidence_graph.py constructors:
- ai_work_event: f"polylogue:we:{event_id}:{project}"
- commit:        f"git:{project}:{sha}"
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

UTC = timezone.utc


# ── helpers ──────────────────────────────────────────────────────────────────


def _dt(y: int, m: int, d: int, h: int = 12, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, 0, tzinfo=UTC)


def _edge_tuple(edge: Any) -> tuple[str, str, str, str, float]:
    return (edge.source_id, edge.target_id, edge.relation, edge.evidence, edge.weight)


def _edge_set(edges: tuple[Any, ...]) -> frozenset[tuple[str, str, str, str, float]]:
    return frozenset(_edge_tuple(e) for e in edges)


def _make_commit_fact(
    sha: str,
    project: str,
    authored_at: datetime,
    paths: tuple[str, ...],
) -> Any:
    from lynchpin.sources.git import GitCommitFact
    return GitCommitFact(
        repo=project,
        commit=sha,
        authored_at=authored_at,
        author="Sinity",
        subject=f"feat: {sha}",
        lines_added=5,
        lines_deleted=1,
        lines_changed=6,
        files_changed=len(paths),
        paths=paths,
        path_roots=tuple(p.split("/")[0] for p in paths),
    )


def _make_work_event(
    event_id: str,
    project: str,
    start: datetime,
    file_paths: tuple[str, ...],
) -> Any:
    from lynchpin.sources.polylogue import WorkEvent
    return WorkEvent(
        event_id=event_id,
        conversation_id=f"conv-{event_id}",
        provider="claude-code",
        kind="coding",
        confidence=0.9,
        start=start,
        end=None,
        duration_ms=60_000,
        file_paths=file_paths,
        tools_used=("Edit", "Read"),
        summary=f"Work event {event_id}",
    )


def _make_evidence_node_we(event_id: str, project: str, start: datetime, file_paths: tuple[str, ...]) -> Any:
    """Build an EvidenceNode for an ai_work_event matching what _add_polylogue_work_events produces."""
    from lynchpin.composite.evidence_graph import EvidenceNode
    return EvidenceNode(
        id=f"polylogue:we:{event_id}:{project}",
        kind="ai_work_event",
        source="polylogue",
        date=start.date(),
        project=project,
        start=start,
        end=None,
        summary=f"Work event {event_id}",
        payload={
            "event_id": event_id,
            "file_paths": list(file_paths),
        },
    )


def _make_evidence_node_commit(sha: str, project: str, authored_at: datetime, paths: tuple[str, ...]) -> Any:
    """Build an EvidenceNode for a commit matching what _add_git produces."""
    from lynchpin.composite.evidence_graph import EvidenceNode
    return EvidenceNode(
        id=f"git:{project}:{sha}",
        kind="commit",
        source="git",
        date=authored_at.date(),
        project=project,
        start=authored_at,
        end=authored_at,
        summary=f"feat: {sha}",
        payload={
            "commit": sha,
            "paths": paths,
        },
    )


def _setup_db(tmp_path: Path, commit_facts: list[Any], work_events: list[Any], project: str) -> Any:
    """Apply schema, promote commits and work events, return open connection."""
    from lynchpin.duck.connection import apply_schema, connect
    from lynchpin.duck.promote import promote_ai_work_events, promote_commits

    db = tmp_path / "test.duckdb"
    conn = __import__("duckdb").connect(str(db))
    apply_schema(conn)

    def _resolver(ev: Any) -> str:
        return project

    promote_commits(conn, facts=commit_facts, refresh_id="r1")
    promote_ai_work_events(conn, events=work_events, refresh_id="r1", project_resolver=_resolver)
    return conn


# ── file_overlap equivalence ─────────────────────────────────────────────────


def test_file_overlap_sql_matches_python_simple_case(tmp_path: Path) -> None:
    """3 commits + 2 work events with varied file overlap. Python and SQL agree."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_file_overlap_edges
    from lynchpin.duck.reader import compute_file_overlap_edges

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    # Commit A shares files with WE1 only.
    commit_a = _make_commit_fact("sha-a", project, base_time, ("src/foo.py", "src/bar.py"))
    # Commit B shares files with both WE1 and WE2.
    commit_b = _make_commit_fact("sha-b", project, _dt(2026, 5, 1, 14), ("src/baz.py",))
    # Commit C shares no files with any WE.
    commit_c = _make_commit_fact("sha-c", project, base_time, ("docs/readme.md",))

    we1 = _make_work_event("we-001", project, _dt(2026, 5, 1, 11), ("src/foo.py", "src/baz.py"))
    we2 = _make_work_event("we-002", project, _dt(2026, 5, 1, 15), ("src/baz.py",))

    # SQL path
    conn = _setup_db(tmp_path, [commit_a, commit_b, commit_c], [we1, we2], project)
    try:
        sql_edges = compute_file_overlap_edges(conn)
    finally:
        conn.close()

    # Python path
    nodes = [
        _make_evidence_node_we(we1.event_id, project, we1.start, we1.file_paths),
        _make_evidence_node_we(we2.event_id, project, we2.start, we2.file_paths),
        _make_evidence_node_commit(commit_a.commit, project, commit_a.authored_at, commit_a.paths),
        _make_evidence_node_commit(commit_b.commit, project, commit_b.authored_at, commit_b.paths),
        _make_evidence_node_commit(commit_c.commit, project, commit_c.authored_at, commit_c.paths),
    ]
    py_edges = _polylogue_work_event_file_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges), (
        f"SQL edges: {_edge_set(sql_edges)}\nPython edges: {_edge_set(py_edges)}"
    )
    # At minimum: WE1↔A (foo.py), WE1↔B (baz.py), WE2↔B (baz.py)
    assert len(sql_edges) >= 3


def test_file_overlap_respects_24h_window(tmp_path: Path) -> None:
    """Commit 25h after work event must NOT match. 23h before must fire."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_file_overlap_edges
    from lynchpin.duck.reader import compute_file_overlap_edges

    project = "lynchpin"
    we_start = _dt(2026, 5, 1, 12)

    # 23h before start — within window
    commit_in = _make_commit_fact("sha-in", project, _dt(2026, 4, 30, 13), ("src/foo.py",))
    # 25h after start — outside window
    commit_out = _make_commit_fact("sha-out", project, _dt(2026, 5, 2, 13), ("src/foo.py",))

    we = _make_work_event("we-003", project, we_start, ("src/foo.py",))

    conn = _setup_db(tmp_path, [commit_in, commit_out], [we], project)
    try:
        sql_edges = compute_file_overlap_edges(conn)
    finally:
        conn.close()

    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit_in.commit, project, commit_in.authored_at, commit_in.paths),
        _make_evidence_node_commit(commit_out.commit, project, commit_out.authored_at, commit_out.paths),
    ]
    py_edges = _polylogue_work_event_file_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)

    source_target_pairs = {(e.source_id, e.target_id) for e in sql_edges}
    assert (f"polylogue:we:we-003:{project}", f"git:{project}:sha-in") in source_target_pairs
    assert (f"polylogue:we:we-003:{project}", f"git:{project}:sha-out") not in source_target_pairs


def test_file_overlap_respects_project_boundary(tmp_path: Path) -> None:
    """work_event.project='lynchpin', commit.project='sinex' → no edge even with file overlap."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_file_overlap_edges
    from lynchpin.duck.reader import compute_file_overlap_edges
    from lynchpin.duck.connection import apply_schema, connect
    from lynchpin.duck.promote import promote_ai_work_events, promote_commits

    base_time = _dt(2026, 5, 1, 12)

    # Commit in project "sinex" with overlapping file path
    commit = _make_commit_fact("sha-cross", "sinex", base_time, ("src/foo.py",))
    # WorkEvent in project "lynchpin"
    we = _make_work_event("we-004", "lynchpin", base_time, ("src/foo.py",))

    db = tmp_path / "test.duckdb"
    import duckdb as _duckdb
    conn = _duckdb.connect(str(db))
    apply_schema(conn)
    promote_commits(conn, facts=[commit], refresh_id="r1")
    # Promote work event with project="lynchpin"
    promote_ai_work_events(
        conn,
        events=[we],
        refresh_id="r1",
        project_resolver=lambda _ev: "lynchpin",
    )
    try:
        sql_edges = compute_file_overlap_edges(conn)
    finally:
        conn.close()

    nodes = [
        _make_evidence_node_we(we.event_id, "lynchpin", we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, "sinex", commit.authored_at, commit.paths),
    ]
    py_edges = _polylogue_work_event_file_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)
    assert len(sql_edges) == 0


def test_file_overlap_empty_file_paths(tmp_path: Path) -> None:
    """Work event with file_paths=() never produces edges."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_file_overlap_edges
    from lynchpin.duck.reader import compute_file_overlap_edges

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    commit = _make_commit_fact("sha-nf", project, base_time, ("src/foo.py",))
    we = _make_work_event("we-005", project, base_time, ())  # empty file_paths

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        sql_edges = compute_file_overlap_edges(conn)
    finally:
        conn.close()

    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    py_edges = _polylogue_work_event_file_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)
    assert len(sql_edges) == 0


def test_file_overlap_evidence_string_truncation(tmp_path: Path) -> None:
    """5 shared paths → evidence is 'shared paths: a, b, c (+2)' (sorted, top 3, +N)."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_file_overlap_edges
    from lynchpin.duck.reader import compute_file_overlap_edges

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)
    paths = ("src/aaa.py", "src/bbb.py", "src/ccc.py", "src/ddd.py", "src/eee.py")

    commit = _make_commit_fact("sha-trunc", project, base_time, paths)
    we = _make_work_event("we-006", project, base_time, paths)

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        sql_edges = compute_file_overlap_edges(conn)
    finally:
        conn.close()

    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    py_edges = _polylogue_work_event_file_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)
    assert len(sql_edges) == 1
    evidence = next(iter(sql_edges)).evidence
    assert evidence == "shared paths: src/aaa.py, src/bbb.py, src/ccc.py (+2)"


# ── symbol_overlap equivalence ────────────────────────────────────────────────


def _promote_symbol_changes(conn: Any, rows: list[dict], refresh_id: str = "r1") -> None:
    from lynchpin.duck.promote import promote_symbol_changes
    promote_symbol_changes(conn, rows=rows, refresh_id=refresh_id)


def test_symbol_overlap_sql_matches_python_simple_case(tmp_path: Path) -> None:
    """Symbol-level overlap test. Build symbol_change rows for relevant commits."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_symbol_overlap_edges
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    from unittest.mock import patch

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    commit = _make_commit_fact("sha-sym1", project, base_time, ("src/foo.py",))
    we = _make_work_event("we-sym-001", project, base_time, ("src/foo.py",))

    sym_rows = [
        {
            "sha": "sha-sym1",
            "project": project,
            "date": "2026-05-01",
            "path": "src/foo.py",
            "change_type": "M",
            "qualified_name": "MyClass.my_method",
            "symbol_kind": "method",
            "exported": True,
            "breaking_candidate": False,
        },
        {
            "sha": "sha-sym1",
            "project": project,
            "date": "2026-05-01",
            "path": "src/foo.py",
            "change_type": "A",
            "qualified_name": "MyClass.new_method",
            "symbol_kind": "method",
            "exported": False,
            "breaking_candidate": False,
        },
    ]

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        _promote_symbol_changes(conn, sym_rows)
        sql_edges = compute_symbol_overlap_edges(conn)
    finally:
        conn.close()

    # Python path: mock _load_symbol_changes_index to return our test data
    by_sha: dict[str, list[dict]] = {"sha-sym1": sym_rows}
    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    with patch(
        "lynchpin.composite.evidence_graph._load_symbol_changes_index",
        return_value=by_sha,
    ):
        py_edges = _polylogue_work_event_symbol_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges), (
        f"SQL edges: {_edge_set(sql_edges)}\nPython edges: {_edge_set(py_edges)}"
    )
    assert len(sql_edges) == 1
    assert next(iter(sql_edges)).weight == 0.95


def test_symbol_overlap_path_suffix_matching(tmp_path: Path) -> None:
    """AI sees 'src/foo.py', symbol_change has '/realm/project/x/src/foo.py' — must match (suffix in either direction)."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_symbol_overlap_edges
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    from unittest.mock import patch

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    # Commit paths use repo-relative form
    commit = _make_commit_fact("sha-suffix", project, base_time, ("src/foo.py",))
    # AI uses absolute path
    we = _make_work_event("we-suffix-001", project, base_time, ("/realm/project/lynchpin/src/foo.py",))

    sym_rows = [
        {
            "sha": "sha-suffix",
            "project": project,
            "date": "2026-05-01",
            "path": "src/foo.py",            # repo-relative in symbol_change
            "change_type": "M",
            "qualified_name": "Foo.bar",
            "symbol_kind": "method",
            "exported": True,
            "breaking_candidate": False,
        },
    ]

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        _promote_symbol_changes(conn, sym_rows)
        sql_edges = compute_symbol_overlap_edges(conn)
    finally:
        conn.close()

    # AI path (absolute) ends with symbol path (relative) after lstrip('/'):
    # ltrim('/realm/project/lynchpin/src/foo.py', '/') = 'realm/project/lynchpin/src/foo.py'
    # ltrim('src/foo.py', '/') = 'src/foo.py'
    # ends_with('realm/.../src/foo.py', 'src/foo.py') → True ✓

    by_sha: dict[str, list[dict]] = {"sha-suffix": sym_rows}
    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    with patch(
        "lynchpin.composite.evidence_graph._load_symbol_changes_index",
        return_value=by_sha,
    ):
        py_edges = _polylogue_work_event_symbol_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges), (
        f"SQL: {_edge_set(sql_edges)}\nPython: {_edge_set(py_edges)}"
    )
    assert len(sql_edges) == 1, "Absolute AI path should suffix-match repo-relative symbol path"


def test_symbol_overlap_no_symbol_data_returns_empty(tmp_path: Path) -> None:
    """No symbol_change rows → SQL returns empty, Python returns empty."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_symbol_overlap_edges
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    from unittest.mock import patch

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    commit = _make_commit_fact("sha-nosym", project, base_time, ("src/foo.py",))
    we = _make_work_event("we-nosym-001", project, base_time, ("src/foo.py",))

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        # No symbol_change rows promoted — table is empty for this SHA
        sql_edges = compute_symbol_overlap_edges(conn)
    finally:
        conn.close()

    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    # Python builder returns () when _load_symbol_changes_index returns {}
    with patch(
        "lynchpin.composite.evidence_graph._load_symbol_changes_index",
        return_value={},
    ):
        py_edges = _polylogue_work_event_symbol_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)
    assert len(sql_edges) == 0
    assert len(py_edges) == 0


def test_symbol_overlap_evidence_string_truncation(tmp_path: Path) -> None:
    """5 shared symbols → evidence is 'shared symbols: a, b, c (+2)' (sorted, top 3, +N)."""
    from lynchpin.composite.evidence_graph import _polylogue_work_event_symbol_overlap_edges
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    from unittest.mock import patch

    project = "lynchpin"
    base_time = _dt(2026, 5, 1, 12)

    commit = _make_commit_fact("sha-symtrunc", project, base_time, ("src/foo.py",))
    we = _make_work_event("we-symtrunc-001", project, base_time, ("src/foo.py",))

    symbols = ["Alpha.a", "Beta.b", "Gamma.g", "Delta.d", "Epsilon.e"]
    sym_rows = [
        {
            "sha": "sha-symtrunc",
            "project": project,
            "date": "2026-05-01",
            "path": "src/foo.py",
            "change_type": "M",
            "qualified_name": name,
            "symbol_kind": "method",
            "exported": True,
            "breaking_candidate": False,
        }
        for name in symbols
    ]

    conn = _setup_db(tmp_path, [commit], [we], project)
    try:
        _promote_symbol_changes(conn, sym_rows)
        sql_edges = compute_symbol_overlap_edges(conn)
    finally:
        conn.close()

    by_sha: dict[str, list[dict]] = {"sha-symtrunc": sym_rows}
    nodes = [
        _make_evidence_node_we(we.event_id, project, we.start, we.file_paths),
        _make_evidence_node_commit(commit.commit, project, commit.authored_at, commit.paths),
    ]
    with patch(
        "lynchpin.composite.evidence_graph._load_symbol_changes_index",
        return_value=by_sha,
    ):
        py_edges = _polylogue_work_event_symbol_overlap_edges(nodes)

    assert _edge_set(sql_edges) == _edge_set(py_edges)
    assert len(sql_edges) == 1
    evidence = next(iter(sql_edges)).evidence
    # Sorted: Alpha.a, Beta.b, Delta.d, Epsilon.e, Gamma.g → top 3 + (+2)
    assert evidence == "shared symbols: Alpha.a, Beta.b, Delta.d (+2)"


# ── _finalize_graph bridge tests ──────────────────────────────────────────────


def _make_full_we_node(
    event_id: str, project: str, start: datetime, file_paths: tuple[str, ...]
) -> Any:
    """Build an EvidenceNode with a full payload (conversation_id included).

    _extract_overlap_sources_from_nodes skips nodes missing conversation_id,
    so bridge tests need the complete payload shape that _add_polylogue_work_events
    actually produces.
    """
    from lynchpin.composite.evidence_graph import EvidenceNode
    return EvidenceNode(
        id=f"polylogue:we:{event_id}:{project}",
        kind="ai_work_event",
        source="polylogue",
        date=start.date(),
        project=project,
        start=start,
        end=None,
        summary=f"Work event {event_id}",
        payload={
            "event_id": event_id,
            "conversation_id": f"conv-{event_id}",
            "provider": "claude-code",
            "kind": "coding",
            "kind_confidence": 0.9,
            "duration_ms": 60_000,
            "file_paths": list(file_paths),
            "tools_used": ["Edit", "Read"],
        },
    )


def _make_full_commit_node(
    sha: str, project: str, authored_at: datetime, paths: tuple[str, ...]
) -> Any:
    """Build an EvidenceNode for a commit with a full payload."""
    from lynchpin.composite.evidence_graph import EvidenceNode
    return EvidenceNode(
        id=f"git:{project}:{sha}",
        kind="commit",
        source="git",
        date=authored_at.date(),
        project=project,
        start=authored_at,
        end=authored_at,
        summary=f"feat: {sha}",
        payload={
            "commit": sha,
            "paths": list(paths),
            "author": "Sinity",
            "subject": f"feat: {sha}",
            "lines_added": 5,
            "lines_deleted": 1,
            "lines_changed": 6,
            "files_changed": len(paths),
        },
    )


def test_finalize_graph_substrate_overlap_path_produces_equivalent_edges(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Bridge test: _finalize_graph with SQL path produces same edges as Python path.

    Build 3 ai_work_event nodes + 3 commit nodes with varied file overlap.
    Compare file_overlap and symbol_overlap edge sets from Python vs SQL path.
    """
    from unittest.mock import patch
    from lynchpin.composite.evidence_graph import _finalize_graph
    from datetime import date as _date

    project = "lynchpin"
    base = _dt(2026, 5, 1, 12)

    # WE1 overlaps with commit_a (src/foo.py) and commit_b (src/baz.py)
    # WE2 overlaps with commit_b only (src/baz.py)
    # WE3 has no overlapping files
    we1 = _make_full_we_node("we-fg-001", project, base, ("src/foo.py", "src/baz.py"))
    we2 = _make_full_we_node("we-fg-002", project, _dt(2026, 5, 1, 15), ("src/baz.py",))
    we3 = _make_full_we_node("we-fg-003", project, base, ("docs/readme.md",))

    commit_a = _make_full_commit_node("sha-fg-a", project, base, ("src/foo.py",))
    commit_b = _make_full_commit_node("sha-fg-b", project, _dt(2026, 5, 1, 13), ("src/baz.py",))
    commit_c = _make_full_commit_node("sha-fg-c", project, base, ("tests/test_x.py",))

    nodes = [we1, we2, we3, commit_a, commit_b, commit_c]
    start_d = _date(2026, 5, 1)
    end_d = _date(2026, 5, 1)
    now = _dt(2026, 5, 1, 20)

    # Override substrate_path so the SQL path writes to tmp_path, not the real substrate.
    db_path = tmp_path / "test_bridge.duckdb"
    monkeypatch.delenv("LYNCHPIN_SUBSTRATE_OVERLAP", raising=False)

    # Python path — no env var set.
    with patch("lynchpin.composite.evidence_graph._load_symbol_changes_index", return_value={}):
        py_graph = _finalize_graph(
            nodes=list(nodes),
            edges=[],
            start=start_d,
            end=end_d,
            mode="local-fast",
            generated_at=now,
        )

    py_overlap_edges = frozenset(
        _edge_tuple(e)
        for e in py_graph.edges
        if e.relation in ("file_overlap", "symbol_overlap")
    )

    # SQL path — LYNCHPIN_SUBSTRATE_OVERLAP=1, substrate pointed at tmp_path.
    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_OVERLAP", "1")
    with (
        patch("lynchpin.duck.connection.substrate_path", return_value=db_path),
        patch("lynchpin.composite.evidence_graph._load_symbol_changes_index", return_value={}),
    ):
        sql_graph = _finalize_graph(
            nodes=list(nodes),
            edges=[],
            start=start_d,
            end=end_d,
            mode="local-fast",
            generated_at=now,
        )

    sql_overlap_edges = frozenset(
        _edge_tuple(e)
        for e in sql_graph.edges
        if e.relation in ("file_overlap", "symbol_overlap")
    )

    assert sql_overlap_edges == py_overlap_edges, (
        f"SQL edges: {sql_overlap_edges}\nPython edges: {py_overlap_edges}"
    )
    # Sanity: at least WE1↔commit_a, WE1↔commit_b, WE2↔commit_b
    assert len(py_overlap_edges) >= 3


def test_finalize_graph_substrate_overlap_falls_back_silently_on_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """When substrate path is unwriteable, the graph still builds via Python fallback."""
    from unittest.mock import patch
    from lynchpin.composite.evidence_graph import _finalize_graph
    from datetime import date as _date
    from pathlib import Path as _Path

    project = "lynchpin"
    base = _dt(2026, 5, 1, 12)

    we = _make_full_we_node("we-fb-001", project, base, ("src/foo.py",))
    commit = _make_full_commit_node("sha-fb-a", project, base, ("src/foo.py",))
    nodes = [we, commit]
    start_d = _date(2026, 5, 1)
    end_d = _date(2026, 5, 1)
    now = _dt(2026, 5, 1, 20)

    # Point substrate to an unwriteable path to force failure.
    bad_path = tmp_path / "no_dir" / "no_subdir" / "bad.duckdb"

    monkeypatch.setenv("LYNCHPIN_SUBSTRATE_OVERLAP", "1")
    with (
        patch("lynchpin.duck.connection.substrate_path", return_value=bad_path),
        patch("lynchpin.composite.evidence_graph._load_symbol_changes_index", return_value={}),
    ):
        graph = _finalize_graph(
            nodes=list(nodes),
            edges=[],
            start=start_d,
            end=end_d,
            mode="local-fast",
            generated_at=now,
        )

    # Graph must be built successfully.
    assert graph is not None
    assert len(graph.nodes) >= 2

    # Python fallback must have fired — file_overlap edge between we and commit.
    file_overlap_edges = [e for e in graph.edges if e.relation == "file_overlap"]
    assert len(file_overlap_edges) >= 1, (
        "Expected Python fallback to produce at least one file_overlap edge"
    )
