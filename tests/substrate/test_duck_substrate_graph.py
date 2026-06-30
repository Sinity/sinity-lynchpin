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
from typing import Any

import pytest

UTC = timezone.utc

# ── helpers ─────────────────────────────────────────────────────────────────


def _dt(y: int, m: int, d: int, h: int = 12) -> datetime:
    return datetime(y, m, d, h, 0, 0, tzinfo=UTC)


# ── evidence_graph round-trip tests (Arc 2.2) ────────────────────────────────


def _make_evidence_graph(
    start: date = date(2026, 5, 1),
    end: date = date(2026, 5, 7),
    mode: str = "materialized",
    node_suffix: str = "",
) -> "Any":  # EvidenceGraph
    from lynchpin.core.evidence import EvidenceCaveat, EvidenceProvenance
    from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode

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
            payload={
                "lines_added": 42,
                "subject": "feat: add evidence graph promotion",
            },
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
                cost="materialized",
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
                EvidenceCaveat(
                    source="github", status="partial", message="test caveat"
                ),
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
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    graph = _make_evidence_graph()
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        counts = graph_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        loaded = graph_mod.load_evidence_graph(conn, refresh_id="r1")

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
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    graph = _make_evidence_graph()
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        graph_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        graph_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)

        node_count = conn.execute(
            "SELECT COUNT(*) FROM evidence_node WHERE refresh_id = 'r1'"
        ).fetchone()[0]
        edge_count = conn.execute(
            "SELECT COUNT(*) FROM evidence_edge WHERE refresh_id = 'r1'"
        ).fetchone()[0]

    assert node_count == 3
    assert edge_count == 2


def test_promote_evidence_graph_preserves_commit_error_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DuckDB fatal can invalidate the connection before ROLLBACK works."""
    from lynchpin.substrate import graph as graph_mod

    graph = _make_evidence_graph()

    class FatalConn:
        def execute(self, sql: str, params: object | None = None) -> "FatalConn":
            if sql == "COMMIT":
                raise RuntimeError("Corrupted ART index")
            if sql == "ROLLBACK":
                raise RuntimeError("database has been invalidated")
            return self

    monkeypatch.setattr(graph_mod, "promote_rows", lambda *args, **kwargs: 1)

    with pytest.raises(RuntimeError, match="Corrupted ART index"):
        graph_mod.promote_evidence_graph(FatalConn(), refresh_id="r1", graph=graph)


def test_promote_evidence_graph_partition_isolation(tmp_path: Path) -> None:
    """Two graphs with different refresh_ids have independent nodes."""
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    graph_r1 = _make_evidence_graph(node_suffix="r1_")
    graph_r2 = _make_evidence_graph(node_suffix="r2_")

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        graph_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph_r1)
        graph_mod.promote_evidence_graph(conn, refresh_id="r2", graph=graph_r2)

        loaded_r1 = graph_mod.load_evidence_graph(conn, refresh_id="r1")
        loaded_r2 = graph_mod.load_evidence_graph(conn, refresh_id="r2")

    assert loaded_r1 is not None
    assert loaded_r2 is not None

    ids_r1 = {n.id for n in loaded_r1.nodes}
    ids_r2 = {n.id for n in loaded_r2.nodes}
    # No overlap — each graph has its own unique node IDs
    assert ids_r1.isdisjoint(ids_r2)
    assert all("r1_" in nid for nid in ids_r1)
    assert all("r2_" in nid for nid in ids_r2)


def test_load_evidence_graph_by_window(tmp_path: Path) -> None:
    """load_evidence_graph with start/end (no refresh_id) finds the graph."""
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    start = date(2026, 5, 1)
    end = date(2026, 5, 7)
    mode = "materialized"
    graph = _make_evidence_graph(start=start, end=end, mode=mode)

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        graph_mod.promote_evidence_graph(conn, refresh_id="r1", graph=graph)
        loaded = graph_mod.load_evidence_graph(conn, start=start, end=end)

    assert loaded is not None
    assert loaded.start == start
    assert loaded.end == end
    assert loaded.mode == mode
    assert len(loaded.nodes) == 3
    assert len(loaded.edges) == 2


def test_load_evidence_graph_returns_none_when_missing(tmp_path: Path) -> None:
    """load_evidence_graph returns None when no matching build exists."""
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        result = graph_mod.load_evidence_graph(conn, refresh_id="nonexistent")

    assert result is None


def test_finalize_graph_writes_to_substrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_finalize_graph writes to substrate when promotion is requested."""
    from lynchpin.substrate.connection import apply_schema, connect

    substrate = tmp_path / "substrate.duckdb"
    import lynchpin.substrate.connection as duck_conn

    monkeypatch.setattr(duck_conn, "substrate_path", lambda: substrate)

    from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
    from lynchpin.graph.evidence_graph import _finalize_graph

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
        mode="materialized",
        generated_at=_dt(2026, 5, 1, 12),
        promote=True,
    )

    # The function must return a valid EvidenceGraph regardless of substrate write.
    assert isinstance(result, EvidenceGraph)
    assert len(result.nodes) >= 1  # deduplication may keep or drop

    # Verify the substrate was written.
    from lynchpin.substrate.graph import load_evidence_graph

    assert substrate.exists(), "Substrate file must have been created by the write."

    with connect(substrate) as conn:
        apply_schema(conn)  # idempotent — tables already there
        loaded = load_evidence_graph(
            conn,
            start=date(2026, 5, 1),
            end=date(2026, 5, 1),
        )

    assert loaded is not None
    assert loaded.start == date(2026, 5, 1)


def test_finalize_graph_substrate_write_fails_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_finalize_graph returns a valid graph even when the substrate write fails."""
    # Point substrate to an unwriteable location.
    unwriteable = tmp_path / "no_such_dir" / "substrate.duckdb"
    # Override substrate_path to return the unwriteable path.
    import lynchpin.substrate.connection as duck_conn

    monkeypatch.setattr(duck_conn, "substrate_path", lambda: unwriteable)

    from lynchpin.core.evidence_graph import EvidenceGraph, EvidenceNode
    from lynchpin.graph.evidence_graph import _finalize_graph

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
        mode="materialized",  # type: ignore[arg-type]
        generated_at=_dt(2026, 5, 1, 12),
        promote=True,
    )

    assert isinstance(result, EvidenceGraph)
    assert len(result.nodes) >= 1
