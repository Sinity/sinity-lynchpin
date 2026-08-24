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
import json
import logging
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


def test_promote_incremental_evidence_graph_copies_predecessor_and_replaces_tail(tmp_path: Path) -> None:
    from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, connect

    predecessor = _make_evidence_graph()
    tail = EvidenceGraph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 8),
        generated_at=datetime(2026, 5, 8, 12, tzinfo=UTC),
        mode="materialized",
        nodes=(
            EvidenceNode(
                id="commit:tail",
                kind="commit",
                source="git",
                date=date(2026, 5, 6),
                project="lynchpin",
                summary="tail commit",
            ),
        ),
        edges=(
            EvidenceEdge(
                source_id="commit:sha001",
                target_id="commit:tail",
                relation="temporal_overlap",
                evidence="crosses tail boundary",
                weight=0.7,
            ),
        ),
        caveats=(),
    )
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        graph_mod.promote_evidence_graph(conn, refresh_id="old", graph=predecessor)
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES ('old', 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
            VALUES ('old', 'evidence_graph', 'graph', 'ok', NULL, 3, NULL, NULL, now())
            """
        )
        counts = graph_mod.promote_incremental_evidence_graph(
            conn,
            previous_refresh_id="old",
            refresh_id="new",
            graph=tail,
            full_start=predecessor.start,
            tail_start=date(2026, 5, 5),
        )
        same_refresh_counts = graph_mod.promote_incremental_evidence_graph(
            conn,
            previous_refresh_id="new",
            refresh_id="new",
            graph=tail,
            full_start=predecessor.start,
            tail_start=date(2026, 5, 5),
        )
        loaded = graph_mod.load_evidence_graph(conn, refresh_id="new")
        full = EvidenceGraph(
            start=predecessor.start,
            end=tail.end,
            generated_at=tail.generated_at,
            mode=tail.mode,
            nodes=(*predecessor.nodes, *tail.nodes),
            edges=(*predecessor.edges, *tail.edges),
            caveats=(),
        )
        graph_mod.promote_evidence_graph(conn, refresh_id="full", graph=full)
        loaded_full = graph_mod.load_evidence_graph(conn, refresh_id="full")

    assert counts == {"build": 1, "nodes": 4, "edges": 3}
    assert same_refresh_counts == counts
    assert loaded is not None
    assert loaded.start == predecessor.start
    assert loaded.end == tail.end
    assert {node.id for node in loaded.nodes} == {
        "commit:sha001",
        "ai_work:ev001",
        "github:pr99",
        "commit:tail",
    }
    assert ("commit:sha001", "commit:tail", "temporal_overlap") in {
        (edge.source_id, edge.target_id, edge.relation) for edge in loaded.edges
    }
    assert loaded_full is not None
    assert {
        (edge.source_id, edge.target_id, edge.relation, edge.evidence)
        for edge in loaded.edges
    } == {
        (edge.source_id, edge.target_id, edge.relation, edge.evidence)
        for edge in loaded_full.edges
    }
    assert {node.id for node in loaded.nodes} == {node.id for node in loaded_full.nodes}


def test_incremental_context_graph_reuses_candidate_predecessor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Normal maintenance replaces a tail without duplicating history."""
    import duckdb

    from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode
    from lynchpin.graph import context_pack, evidence_edges
    from lynchpin.substrate import graph as graph_mod
    from lynchpin.substrate.connection import apply_schema, candidate_generation, connect

    predecessor = _make_evidence_graph()
    tail = EvidenceGraph(
        start=date(2026, 5, 5),
        end=date(2026, 5, 8),
        generated_at=datetime(2026, 5, 8, 12, tzinfo=UTC),
        mode="materialized",
        nodes=(
            EvidenceNode(
                id="commit:tail",
                kind="commit",
                source="git",
                date=date(2026, 5, 6),
                project="lynchpin",
                summary="tail commit",
            ),
            EvidenceNode(
                id="commit:tail2",
                kind="commit",
                source="git",
                date=date(2026, 5, 7),
                project="lynchpin",
                summary="second tail commit",
            ),
        ),
        edges=(
            EvidenceEdge(
                source_id="commit:sha001",
                target_id="commit:tail",
                relation="temporal_overlap",
                evidence="crosses tail boundary",
                weight=0.7,
            ),
        ),
        caveats=(),
    )
    monkeypatch.setenv("LYNCHPIN_LOCAL_ROOT", str(tmp_path))
    db = tmp_path / "duck" / "substrate.duckdb"
    db.parent.mkdir()
    import lynchpin.substrate.connection as duck_conn

    monkeypatch.setattr(
        duck_conn,
        "substrate_path",
        lambda: duck_conn._substrate_path_override.get() or db,
    )
    with duckdb.connect(str(db)) as conn:
        apply_schema(conn)
        graph_mod.promote_evidence_graph(conn, refresh_id="stable", graph=predecessor)
        conn.execute(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES ('stable', 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
            """
        )
        conn.execute(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
            VALUES ('stable', 'evidence_graph', 'graph', 'ok', NULL, 1, NULL, NULL, now())
            """
        )

    monkeypatch.setattr(context_pack, "build_evidence_graph", lambda **_kwargs: tail)
    tail_tail_edge = EvidenceEdge(
        source_id="commit:tail",
        target_id="commit:tail2",
        relation="same_project_day",
        evidence="tail-internal edge",
        weight=1.0,
    )
    monkeypatch.setattr(evidence_edges, "same_project_day_edges", lambda _nodes: (tail_tail_edge,))
    monkeypatch.setattr(evidence_edges, "temporal_overlap_edges", lambda _nodes: ())
    monkeypatch.setattr(evidence_edges, "temporal_proximity_edges", lambda _nodes: ())
    monkeypatch.setattr(
        evidence_edges,
        "overlap_edges_via_substrate",
        lambda _nodes, **_kwargs: (tail_tail_edge,),
    )
    monkeypatch.setattr(
        evidence_edges,
        "polylogue_work_event_tool_overlap_edges",
        lambda _nodes: (),
    )
    monkeypatch.setattr(evidence_edges, "mentions_project_edges", lambda _nodes: ())

    with caplog.at_level(logging.INFO, logger="lynchpin.graph.context_pack"):
        with candidate_generation():
            context_pack.materialize_incremental_evidence_graph(
                start=predecessor.start,
                end=tail.end,
                tail_start=tail.start,
            )

    monkeypatch.setattr("lynchpin.materialization.audit_materialization", lambda: ())
    from lynchpin.cli import substrate_snapshot

    with candidate_generation():
        substrate_snapshot._record_snapshot_materialization_statuses(
            start=predecessor.start,
            end=tail.end,
            projects=(),
        )
        substrate_snapshot._record_snapshot_promotion_run(
            start=predecessor.start,
            end=tail.end,
            projects=(),
        )

    with connect(db, read_only=True) as conn:
        refresh_ids = conn.execute(
            "SELECT refresh_id FROM evidence_graph_build ORDER BY refresh_id"
        ).fetchall()
        current_refresh_id = context_pack._current_state_refresh_id(
            start=predecessor.start,
            end=tail.end,
            projects=(),
        )
        loaded = graph_mod.load_evidence_graph(conn, refresh_id=current_refresh_id)
        current_row = substrate_snapshot._current_graph_row(
            conn,
            start=predecessor.start,
            end=tail.end,
            projects=(),
        )
        current_refresh_id_row = conn.execute(
            "SELECT refresh_id FROM substrate_promotion_run "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        current_graph_status = conn.execute(
            """
            SELECT refresh_id, status FROM substrate_source_status
            WHERE source = 'evidence_graph'
            ORDER BY recorded_at DESC LIMIT 1
            """
        ).fetchone()

    assert refresh_ids == [("current-state:2026-05-01:2026-05-08:all",), ("stable",)]
    assert current_row == (5, 3)
    assert current_refresh_id_row == ("current-state:2026-05-01:2026-05-08:all",)
    assert current_graph_status == ("current-state:2026-05-01:2026-05-08:all", "ok")
    assert loaded is not None
    assert {node.id for node in loaded.nodes} == {
        "commit:sha001",
        "ai_work:ev001",
        "github:pr99",
        "commit:tail",
        "commit:tail2",
    }
    metrics = [
        json.loads(record.message.removeprefix("evidence_graph_performance "))
        for record in caplog.records
        if record.message.startswith("evidence_graph_performance ")
    ]
    by_stage = {metric["stage"]: metric for metric in metrics}
    assert by_stage["crossing_boundary_edges"]["edge_count"] == 0
    assert set(by_stage) == {
        "predecessor_boundary",
        "tail_graph_build",
        "crossing_boundary_edges",
        "candidate_graph_write",
        "analysis_claim_build",
        "candidate_claim_write",
    }
    assert all(
        {"elapsed_seconds", "cpu_seconds", "average_cpu_cores"} <= metric.keys()
        for metric in metrics
    )


def test_compatible_predecessor_requires_success_and_exact_scope(tmp_path: Path) -> None:
    import duckdb

    from lynchpin.substrate.connection import apply_schema
    from lynchpin.substrate.graph import compatible_graph_predecessor, promote_evidence_graph

    db = tmp_path / "sub.duckdb"
    graph = _make_evidence_graph()
    with duckdb.connect(str(db)) as conn:
        apply_schema(conn)
        promote_evidence_graph(conn, refresh_id="all-ok", graph=graph)
        promote_evidence_graph(conn, refresh_id="all-degraded", graph=graph)
        promote_evidence_graph(conn, refresh_id="project-ok", graph=graph, projects=("alpha",))
        promote_evidence_graph(conn, refresh_id="all-failed", graph=graph)
        conn.executemany(
            """
            INSERT INTO substrate_promotion_run
            (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
            VALUES (?, ?, NULL, NULL, NULL, 'test', '{}', now(), now())
            """,
            [
                ("all-ok", "ok"),
                ("all-degraded", "degraded"),
                ("project-ok", "ok"),
                ("all-failed", "error"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO substrate_source_status
            (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
            VALUES (?, 'evidence_graph', 'graph', 'ok', NULL, 3, NULL, NULL, now())
            """,
            [("all-ok",), ("all-degraded",), ("project-ok",), ("all-failed",)],
        )

        assert compatible_graph_predecessor(
            conn,
            current_refresh_id="new-all",
            full_start=date(2026, 5, 1),
            tail_start=date(2026, 5, 5),
        ) == "all-degraded"
        assert compatible_graph_predecessor(
            conn,
            current_refresh_id="new-alpha",
            full_start=date(2026, 5, 1),
            tail_start=date(2026, 5, 5),
            projects=("alpha",),
        ) == "project-ok"
        assert compatible_graph_predecessor(
            conn,
            current_refresh_id="new-beta",
            full_start=date(2026, 5, 1),
            tail_start=date(2026, 5, 5),
            projects=("beta",),
        ) is None


def test_promote_incremental_analysis_claims_replaces_same_refresh_tail(tmp_path: Path) -> None:
    from lynchpin.substrate.claims import (
        AnalysisClaimRow,
        promote_analysis_claims,
        promote_incremental_analysis_claims,
    )
    from lynchpin.substrate.connection import apply_schema, connect

    old = AnalysisClaimRow(
        claim_id="old",
        claim_type="work",
        project="lynchpin",
        date=date(2026, 5, 1),
        support_level="supported",
        confidence=0.9,
        score=1.0,
        summary="old claim",
        source_ids=(),
        relation_ids=(),
        caveats=(),
        payload={},
    )
    tail = AnalysisClaimRow(
        claim_id="tail",
        claim_type="work",
        project="lynchpin",
        date=date(2026, 5, 6),
        support_level="supported",
        confidence=0.9,
        score=1.0,
        summary="tail claim",
        source_ids=(),
        relation_ids=(),
        caveats=(),
        payload={},
    )
    db = tmp_path / "sub.duckdb"
    with connect(db) as conn:
        apply_schema(conn)
        promote_analysis_claims(conn, refresh_id="same", claims=(old, tail))
        count = promote_incremental_analysis_claims(
            conn,
            previous_refresh_id="same",
            refresh_id="same",
            tail_start=date(2026, 5, 5),
            claims=(tail,),
        )
        ids = {
            row[0]
            for row in conn.execute(
                "SELECT claim_id FROM analysis_claim WHERE refresh_id = 'same'"
            ).fetchall()
        }

    assert count == 2
    assert ids == {"old", "tail"}


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
    import duckdb

    from lynchpin.substrate.connection import (
        bind_candidate_publication,
        bootstrap_candidate_generation,
        connect,
    )

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

    refresh_id = "graph:2026-05-01:2026-05-01:all"
    with bootstrap_candidate_generation() as generation:
        result = _finalize_graph(
            nodes=nodes,
            edges=edges,
            start=date(2026, 5, 1),
            end=date(2026, 5, 1),
            mode="materialized",
            generated_at=_dt(2026, 5, 1, 12),
            promote=True,
        )
        with duckdb.connect(str(generation.candidate)) as conn:
            conn.execute(
                """
                INSERT INTO substrate_promotion_run
                (refresh_id, status, reason, window_start, window_end, mode, counts, started_at, finished_at)
                VALUES (?, 'ok', NULL, NULL, NULL, 'test', '{}', now(), now())
                """,
                [refresh_id],
            )
            conn.execute(
                """
                INSERT INTO substrate_source_status
                (refresh_id, source, kind, status, reason, row_count, window_start, window_end, recorded_at)
                VALUES (?, 'evidence_graph', 'graph', 'ok', NULL, 1, NULL, NULL, now())
                """,
                [refresh_id],
            )
        bind_candidate_publication(generation, refresh_id)

    # The function must return a valid EvidenceGraph regardless of substrate write.
    assert isinstance(result, EvidenceGraph)
    assert len(result.nodes) >= 1  # deduplication may keep or drop

    # Verify the substrate was written.
    from lynchpin.substrate.graph import load_evidence_graph

    assert substrate.exists(), "Substrate file must have been created by the write."

    with connect(substrate, read_only=True) as conn:
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
