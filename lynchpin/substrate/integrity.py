"""Measured endpoint integrity for one logical evidence-graph generation."""

from __future__ import annotations

from typing import Any

from lynchpin.core.evidence import EvidenceCaveat


def unavailable_graph_integrity(refresh_id: str | None = None) -> dict[str, Any]:
    return {
        "status": "missing",
        "scope": "logical_graph_generation",
        "refresh_id": refresh_id,
        "measured": False,
        "node_count": None,
        "total_edges": None,
        "missing_source": None,
        "missing_target": None,
        "orphaned_edges": None,
        "orphan_ratio": None,
        "message": "No measured evidence-graph build is available for this selection.",
    }


def non_graph_integrity() -> dict[str, Any]:
    result = unavailable_graph_integrity()
    result.update(
        status="not_applicable",
        scope="derived_overlap_view",
        message="These overlap rows are not the persisted evidence-edge graph.",
    )
    return result


def integrity_from_counts(
    refresh_id: str,
    node_count: int,
    total_edges: int,
    missing_source: int,
    missing_target: int,
    orphaned_edges: int,
) -> dict[str, Any]:
    return {
        "status": "partial" if orphaned_edges else "available",
        "scope": "logical_graph_generation",
        "refresh_id": refresh_id,
        "measured": True,
        "node_count": node_count,
        "total_edges": total_edges,
        "missing_source": missing_source,
        "missing_target": missing_target,
        "orphaned_edges": orphaned_edges,
        "orphan_ratio": orphaned_edges / total_edges if total_edges else 0.0,
        "message": (
            f"{orphaned_edges:,} of {total_edges:,} logical evidence edges have "
            "one or more unresolved endpoints in the selected generation."
        ),
    }


def integrity_caveats(integrity: dict[str, Any]) -> tuple[EvidenceCaveat, ...]:
    status = integrity["status"]
    if status in {"available", "not_applicable"}:
        return ()
    return (EvidenceCaveat("evidence_graph", status, integrity["message"]),)


def measure_graph_integrity(conn: Any, refresh_id: str | None) -> dict[str, Any]:
    """Count logical candidates before filtering unresolved endpoints.

    Reuse the graph reader's partition lineage. Separate endpoint joins avoid
    quadratic correlated OR predicates; only node IDs/dates and edge IDs are
    read, never JSON payloads. No global or cross-generation union substitutes
    for the selected prefix-plus-tail overlay.
    """
    if refresh_id is None:
        return unavailable_graph_integrity()
    if conn.execute(
        "SELECT 1 FROM evidence_graph_build WHERE refresh_id = ?", [refresh_id]
    ).fetchone() is None:
        return unavailable_graph_integrity(refresh_id)

    from lynchpin.substrate.graph import _graph_lineage

    lineage = _graph_lineage(conn, refresh_id=refresh_id)
    parameters: list[Any] = []
    for rank, (partition_id, cutoff) in enumerate(lineage):
        parameters.extend((partition_id, cutoff, rank))
    values = ", ".join("(?::VARCHAR, ?::DATE, ?::INTEGER)" for _ in lineage)
    row = conn.execute(
        f"""
        WITH partitions(refresh_id, cutoff, rank) AS (VALUES {values}),
        node_partitions AS MATERIALIZED (
            SELECT n.id, n.refresh_id, n.date, p.cutoff, p.rank
            FROM evidence_node n JOIN partitions p USING (refresh_id)
        ),
        nodes AS MATERIALIZED (
            SELECT DISTINCT id FROM node_partitions
            WHERE cutoff IS NULL OR date < cutoff
        ),
        newest_node AS MATERIALIZED (
            SELECT id, min(rank) AS rank FROM node_partitions GROUP BY id
        ),
        edges AS MATERIALIZED (
            SELECT DISTINCT e.source_id, e.target_id, e.relation
            FROM evidence_edge e JOIN partitions p USING (refresh_id)
            LEFT JOIN node_partitions s ON s.refresh_id = e.refresh_id AND s.id = e.source_id
            LEFT JOIN node_partitions t ON t.refresh_id = e.refresh_id AND t.id = e.target_id
            LEFT JOIN newest_node sr ON sr.id = e.source_id
            LEFT JOIN newest_node tr ON tr.id = e.target_id
            WHERE (p.cutoff IS NULL OR (
                (s.date IS NULL OR s.date < p.cutoff)
                AND (t.date IS NULL OR t.date < p.cutoff)
            ))
            AND (sr.rank IS NULL OR sr.rank >= p.rank)
            AND (tr.rank IS NULL OR tr.rank >= p.rank)
        )
        SELECT (SELECT count(*) FROM nodes), count(*),
            count(*) FILTER (WHERE s.id IS NULL),
            count(*) FILTER (WHERE t.id IS NULL),
            count(*) FILTER (WHERE s.id IS NULL OR t.id IS NULL)
        FROM edges
        LEFT JOIN nodes s ON s.id = edges.source_id
        LEFT JOIN nodes t ON t.id = edges.target_id
        """,
        parameters,
    ).fetchone()
    return integrity_from_counts(refresh_id, *(int(value) for value in row))
