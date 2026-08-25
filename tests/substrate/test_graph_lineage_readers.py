from __future__ import annotations

import json
from datetime import date, datetime, timezone


TAIL = date(2026, 5, 5)
UTC = timezone.utc


def _build(conn, refresh_id: str, predecessor: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO evidence_graph_build (
            refresh_id, start_date, end_date, mode, projects, node_count,
            edge_count, caveats, generated_at, predecessor_refresh_id,
            predecessor_tail_start
        ) VALUES (?, ?, ?, 'materialized', [], 0, 0, '[]', ?, ?, ?)
        """,
        [
            refresh_id,
            date(2026, 5, 1),
            date(2026, 5, 10),
            datetime(2026, 5, 10, tzinfo=UTC),
            predecessor,
            TAIL if predecessor else None,
        ],
    )


def _node(
    conn,
    refresh_id: str,
    node_id: str,
    kind: str,
    node_date: date,
    project: str = "lynchpin",
    source: str = "git",
    payload: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO evidence_node (
            refresh_id, id, kind, source, date, project, summary, payload, caveats
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]')
        """,
        [
            refresh_id,
            node_id,
            kind,
            source,
            node_date,
            project,
            f"{node_id} summary",
            json.dumps(payload) if payload is not None else None,
        ],
    )


def _edge(conn, refresh_id: str, source_id: str, target_id: str, relation: str) -> None:
    conn.execute(
        """
        INSERT INTO evidence_edge
            (refresh_id, source_id, target_id, relation, evidence, weight)
        VALUES (?, ?, ?, ?, 'test', 1.0)
        """,
        [refresh_id, source_id, target_id, relation],
    )


def test_graph_readers_use_complete_overlay_and_shadow_replacements(tmp_path) -> None:
    from lynchpin.substrate.connection import apply_schema, connect
    from lynchpin.substrate.derived import (
        load_issue_closure_chain_walks,
        load_project_day_correlations,
    )
    from lynchpin.substrate.graph import load_evidence_graph_boundary_nodes
    from lynchpin.substrate.readers_signals import load_source_co_occurrence
    from lynchpin.substrate.readers_velocity import load_velocity_series

    with connect(tmp_path / "graph.duckdb") as conn:
        apply_schema(conn)
        _build(conn, "r0")
        _build(conn, "r1", "r0")

        # Historical rows survive the tail, while the shared key is replaced.
        for day in (date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3)):
            _node(conn, "r0", f"a-{day}", "commit", day, source="git")
            _node(conn, "r0", f"b-{day}", "ai_work_event", day, source="polylogue")
        _node(conn, "r0", "shared", "commit", date(2026, 5, 3), source="old")
        _node(conn, "r1", "shared", "github_issue", TAIL, source="new")
        _node(conn, "r1", "tail", "commit", date(2026, 5, 6), source="git")

        # A historical issue root walks through a predecessor edge whose target
        # is replaced in the tail; the logical node must be the replacement.
        _node(
            conn,
            "r0",
            "issue",
            "github_issue",
            date(2026, 5, 2),
            payload={"number": 7},
        )
        _node(conn, "r0", "target", "github_pr", date(2026, 5, 3))
        _edge(conn, "r0", "issue", "target", "references")
        _node(conn, "r1", "target", "commit", TAIL)
        _edge(conn, "r1", "issue", "target", "references")

        cooccurrence = load_source_co_occurrence(conn, refresh_id="r1")
        assert any(
            row[0:2] == ("git", "polylogue") and row[2] == 3 for row in cooccurrence
        )

        boundary = load_evidence_graph_boundary_nodes(
            conn, refresh_id="r1", tail_start=TAIL, lookback_days=10
        )
        boundary_ids = {node.id for node in boundary}
        assert {"a-2026-05-01", "issue", "target", "shared"} <= boundary_ids
        assert next(node for node in boundary if node.id == "shared").source == "new"

        correlations = load_project_day_correlations(conn, refresh_id="r1")
        correlation_days = {(row.date, row.commit_count) for row in correlations}
        assert date(2026, 5, 1) in {day for day, _ in correlation_days}
        assert (TAIL, 1) in correlation_days

        walks = load_issue_closure_chain_walks(conn, refresh_id="r1")
        issue_walk = next(row for row in walks if row.root_id == "issue")
        assert issue_walk.reachable_node_ids == ("issue", "target")
        assert issue_walk.chain_depth == 1

        velocity = load_velocity_series(conn, refresh_id="r1")
        assert any(row[1] == date(2026, 5, 1) for row in velocity)


def test_claim_evidence_reads_historical_nodes_and_shadows_claim_and_edge(
    tmp_path,
) -> None:
    from lynchpin.substrate.claims import load_claim_evidence
    from lynchpin.substrate.connection import apply_schema, connect

    with connect(tmp_path / "claims.duckdb") as conn:
        apply_schema(conn)
        _build(conn, "r0")
        _build(conn, "r1", "r0")
        _node(conn, "r0", "historical", "commit", date(2026, 5, 2))
        _node(conn, "r0", "replaced", "commit", date(2026, 5, 2), source="old")
        _node(conn, "r1", "replaced", "github_issue", TAIL, source="new")
        _edge(conn, "r0", "historical", "replaced", "supports")
        _edge(conn, "r1", "historical", "replaced", "supports")
        conn.execute(
            """
            INSERT INTO analysis_claim (
                refresh_id, claim_id, claim_type, date, summary, source_ids,
                relation_ids, caveats, payload
            ) VALUES ('r0', 'claim:test', 'test', '2026-05-02', 'old',
                      ['historical', 'replaced'], ['historical->replaced:supports'], '[]', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO analysis_claim (
                refresh_id, claim_id, claim_type, date, summary, source_ids,
                relation_ids, caveats, payload
            ) VALUES ('r1', 'claim:test', 'test', '2026-05-05', 'new',
                      ['historical', 'replaced'], ['historical->replaced:supports'], '[]', '{}')
            """
        )

        claim = load_claim_evidence(conn, claim_id="claim:test", refresh_id="r1")

    assert claim is not None
    assert claim["summary"] == "new"
    assert {row["id"] for row in claim["evidence_nodes"]} == {"historical", "replaced"}
    assert (
        next(row for row in claim["evidence_nodes"] if row["id"] == "replaced")[
            "source"
        ]
        == "new"
    )
    assert len(claim["evidence_edges"]) == 1
