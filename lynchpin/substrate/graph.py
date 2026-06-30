"""Evidence graph table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from lynchpin.substrate._filters import build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# evidence_graph
# ---------------------------------------------------------------------------


def _hydrate_provenance(prov: Any) -> "Any | None":
    """Convert a DuckDB STRUCT dict to EvidenceProvenance, or None if all nulls."""
    from lynchpin.core.evidence import EvidenceProvenance

    if prov is None:
        return None
    # DuckDB returns STRUCT columns as plain dicts.
    if not isinstance(prov, dict):
        return None
    if not any(v is not None for v in prov.values()):
        return None
    return EvidenceProvenance(
        source=prov.get("source") or "",
        cost=prov.get("cost") or "materialized",
        path=prov.get("path"),
        generated_at=prov.get("generated_at"),
        note=prov.get("note"),
    )


def _hydrate_caveats(raw: Any) -> "tuple[Any, ...]":
    """Convert a JSON column (list[dict] or str) to tuple[EvidenceCaveat, ...]."""
    import json as _json
    from lynchpin.core.evidence import EvidenceCaveat

    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = _json.loads(raw)
    if not isinstance(raw, list):
        return ()
    out = []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                EvidenceCaveat(
                    source=item.get("source") or "",
                    status=item.get("status") or "available",
                    message=item.get("message") or "",
                )
            )
    return tuple(out)


def _hydrate_payload(raw: Any) -> "dict[str, Any] | None":
    """Return a dict from a JSON column (DuckDB may return dict or str)."""
    import json as _json

    if raw is None:
        return None
    if isinstance(raw, dict):
        return cast(dict[str, Any], raw)
    if isinstance(raw, str):
        parsed = _json.loads(raw)
        return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None
    return None


def list_evidence_graph_builds(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
) -> list[dict[str, Any]]:
    """List metadata about stored builds without hydrating nodes/edges."""
    clauses: list[str] = []
    params: list[Any] = []

    if start is not None:
        clauses.append("start_date = ?")
        params.append(start)
    if end is not None:
        clauses.append("end_date = ?")
        params.append(end)
    where = build_where(clauses, params)
    sql = f"""
        SELECT refresh_id, start_date, end_date, mode, projects,
               node_count, edge_count, caveats, generated_at, materialized_at
        FROM evidence_graph_build
        {where}
        ORDER BY generated_at DESC
    """
    rows = conn.execute(sql, params).fetchall()

    return [
        {
            "refresh_id": refresh_id,
            "start_date": start_date,
            "end_date": end_date,
            "mode": mode_val,
            "projects": projects,
            "node_count": node_count,
            "edge_count": edge_count,
            "caveats": caveats,
            "generated_at": generated_at,
            "materialized_at": materialized_at,
        }
        for (
            refresh_id,
            start_date,
            end_date,
            mode_val,
            projects,
            node_count,
            edge_count,
            caveats,
            generated_at,
            materialized_at,
        ) in rows
    ]


# ---------------------------------------------------------------------------
# Overlap-edge readers (SQL view equivalents of Python double-loop builders)
# ---------------------------------------------------------------------------


def _format_evidence(prefix: str, items: list[str]) -> str:
    """Format the evidence string using the same truncation logic as the Python builders.

    ``prefix`` is either ``'shared paths'`` or ``'shared symbols'``.
    Items should already be sorted before being passed in.
    """
    preview = ", ".join(items[:3])
    suffix = f" (+{len(items) - 3})" if len(items) > 3 else ""
    return f"{prefix}: {preview}{suffix}"


def compute_file_overlap_edges(
    conn: "duckdb.DuckDBPyConnection",
    *,
    we_refresh_id: str | None = None,
    commit_refresh_id: str | None = None,
) -> "tuple[Any, ...]":
    """Compute file_overlap edges via SQL view; return same shape as
    the ``work_event_file_overlap`` SQL view produces.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).  Each
    returned ``EvidenceEdge`` has weight 0.85 and an evidence string of the
    form ``'shared paths: a, b, c'`` or ``'shared paths: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_paths`` from DuckDB ``list_intersect`` is returned as a Python
    list; we sort in Python to guarantee deterministic evidence strings
    (list_intersect does not guarantee order).
    """
    from lynchpin.core.evidence_graph import EvidenceEdge
    from lynchpin.substrate.views import ensure_views

    ensure_views(conn)

    clauses: list[str] = ["overlap_count > 0"]
    params: list[Any] = []
    if we_refresh_id is not None:
        clauses.append("we_refresh_id = ?")
        params.append(we_refresh_id)
    if commit_refresh_id is not None:
        clauses.append("commit_refresh_id = ?")
        params.append(commit_refresh_id)

    where = "WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT source_id, target_id, shared_paths
        FROM work_event_file_overlap
        {where}
    """
    rows = conn.execute(sql, params).fetchall()

    edges: list[Any] = []
    for source_id, target_id, shared_paths in rows:
        shared = sorted(p for p in (shared_paths or []) if p)
        if not shared:
            continue
        evidence = _format_evidence("shared paths", shared)
        edges.append(
            EvidenceEdge(source_id, target_id, "file_overlap", evidence, weight=0.85)
        )
    return tuple(edges)


def compute_symbol_overlap_edges(
    conn: "duckdb.DuckDBPyConnection",
    *,
    we_refresh_id: str | None = None,
    commit_refresh_id: str | None = None,
) -> "tuple[Any, ...]":
    """Compute symbol_overlap edges via SQL view; return same shape as
    the ``work_event_symbol_overlap`` SQL view produces.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).  Each
    returned ``EvidenceEdge`` has weight 0.95 and an evidence string of the
    form ``'shared symbols: a, b, c'`` or ``'shared symbols: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_symbols`` from ``ARRAY_AGG(DISTINCT ...)`` is a Python list with
    non-deterministic order; we sort in Python before formatting.
    """
    from lynchpin.core.evidence_graph import EvidenceEdge
    from lynchpin.substrate.views import ensure_views

    ensure_views(conn)

    clauses: list[str] = ["symbol_count > 0"]
    params: list[Any] = []
    if we_refresh_id is not None:
        clauses.append("we_refresh_id = ?")
        params.append(we_refresh_id)
    if commit_refresh_id is not None:
        clauses.append("commit_refresh_id = ?")
        params.append(commit_refresh_id)

    where = "WHERE " + " AND ".join(clauses)
    sql = f"""
        SELECT source_id, target_id, shared_symbols
        FROM work_event_symbol_overlap
        {where}
    """
    rows = conn.execute(sql, params).fetchall()

    edges: list[Any] = []
    for source_id, target_id, shared_symbols in rows:
        symbol_names = sorted(s for s in (shared_symbols or []) if s)
        if not symbol_names:
            continue
        evidence = _format_evidence("shared symbols", symbol_names)
        edges.append(
            EvidenceEdge(source_id, target_id, "symbol_overlap", evidence, weight=0.95)
        )
    return tuple(edges)


def load_evidence_graph(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
) -> "Any | None":  # EvidenceGraph | None
    """Hydrate a previously-promoted EvidenceGraph from the substrate.

    Selection rules:
    - If refresh_id is given, return that exact build (or None if absent).
    - Otherwise pick the most recent build covering (start, end);
      projects filter requires the stored projects array to contain ALL
      requested projects, or empty stored projects (= all).
    - Returns None when no matching build exists.

    Column-shape notes:
    - ``payload`` JSON column: DuckDB returns dict directly when the column
      type is JSON and the value is a JSON object.  We fall back to
      ``json.loads`` if a str arrives (older serialisation path).
    - ``provenance`` STRUCT: DuckDB returns a plain dict with the five keys
      (source, cost, path, generated_at, note); any may be None.  We build
      EvidenceProvenance only when at least one field is non-null.
    - ``caveats`` JSON: DuckDB returns a list of dicts or a JSON string;
      we normalise both paths.
    """
    from lynchpin.core.evidence_graph import EvidenceEdge, EvidenceGraph, EvidenceNode

    # ------------------------------------------------------------------
    # 1. Resolve the build row
    # ------------------------------------------------------------------
    if refresh_id is not None:
        build_rows = conn.execute(
            "SELECT refresh_id, start_date, end_date, mode, generated_at, caveats "
            "FROM evidence_graph_build WHERE refresh_id = ?",
            [refresh_id],
        ).fetchall()
    else:
        b_clauses: list[str] = []
        b_params: list[Any] = []
        if start is not None:
            b_clauses.append("start_date <= ?")
            b_params.append(start)
        if end is not None:
            b_clauses.append("end_date >= ?")
            b_params.append(end)
        if projects:
            # Stored projects must contain ALL requested projects, or be empty (= all).
            b_clauses.append("(len(projects) = 0 OR list_has_all(projects, ?))")
            b_params.append(list(projects))
        b_where = build_where(b_clauses, b_params)
        build_rows = conn.execute(
            f"SELECT refresh_id, start_date, end_date, mode, generated_at, caveats "
            f"FROM evidence_graph_build {b_where} ORDER BY generated_at DESC LIMIT 1",
            b_params,
        ).fetchall()

    if not build_rows:
        return None

    rid, start_date, end_date, build_mode, generated_at, build_caveats = build_rows[0]

    # ------------------------------------------------------------------
    # 2. Hydrate nodes
    # ------------------------------------------------------------------
    node_rows = conn.execute(
        """
        SELECT id, kind, source, date, project, summary,
               start_ts, end_ts, url, payload, provenance, caveats
        FROM evidence_node
        WHERE refresh_id = ?
        """,
        [rid],
    ).fetchall()

    nodes: list[EvidenceNode] = []
    for (
        n_id,
        n_kind,
        n_source,
        n_date,
        n_project,
        n_summary,
        n_start,
        n_end,
        n_url,
        n_payload,
        n_prov,
        n_caveats,
    ) in node_rows:
        nodes.append(
            EvidenceNode(
                id=n_id,
                kind=n_kind,
                source=n_source,
                date=n_date,
                project=n_project,
                summary=n_summary or "",
                start=n_start,
                end=n_end,
                url=n_url,
                payload=_hydrate_payload(n_payload),
                provenance=_hydrate_provenance(n_prov),
                caveats=_hydrate_caveats(n_caveats),
            )
        )

    # ------------------------------------------------------------------
    # 3. Hydrate edges
    # ------------------------------------------------------------------
    edge_rows = conn.execute(
        """
        SELECT source_id, target_id, relation, evidence, weight
        FROM evidence_edge
        WHERE refresh_id = ?
        """,
        [rid],
    ).fetchall()

    edges: list[EvidenceEdge] = []
    for e_source_id, e_target_id, e_relation, e_evidence, e_weight in edge_rows:
        edges.append(
            EvidenceEdge(
                source_id=e_source_id,
                target_id=e_target_id,
                relation=e_relation,
                evidence=e_evidence or "",
                weight=e_weight if e_weight is not None else 1.0,
            )
        )

    # ------------------------------------------------------------------
    # 4. Build EvidenceGraph
    # ------------------------------------------------------------------
    return EvidenceGraph(
        start=start_date,
        end=end_date,
        generated_at=generated_at,
        mode=build_mode,
        nodes=tuple(nodes),
        edges=tuple(edges),
        caveats=_hydrate_caveats(build_caveats),
    )


# ── evidence_graph ────────────────────────────────────────────────────────────


_EVIDENCE_NODE_COLUMNS = (
    "id", "kind", "source", "date", "project", "summary",
    "start_ts", "end_ts", "url", "payload", "provenance", "caveats",
)

_EVIDENCE_EDGE_COLUMNS = (
    "source_id", "target_id", "relation", "evidence", "weight",
)


def _extract_node(node: Any) -> tuple[Any, ...]:
    payload_json = json.dumps(node.payload) if node.payload is not None else None
    node_caveats_json = json.dumps(
        [
            {"source": c.source, "status": c.status, "message": c.message}
            for c in node.caveats
        ]
    )
    # DuckDB accepts a plain Python dict for STRUCT columns — field names
    # must match the STRUCT definition exactly. Pass None if no provenance.
    if node.provenance is not None:
        p = node.provenance
        provenance_struct: dict[str, Any] | None = {
            "source": p.source,
            "cost": p.cost if isinstance(p.cost, str) else str(p.cost),
            "path": p.path,
            "generated_at": p.generated_at,
            "note": p.note,
        }
    else:
        provenance_struct = None
    kind_str = node.kind if isinstance(node.kind, str) else str(node.kind)
    return (
        node.id, kind_str, node.source, node.date, node.project, node.summary,
        node.start, node.end, node.url, payload_json, provenance_struct, node_caveats_json,
    )


def _extract_edge(edge: Any) -> tuple[Any, ...]:
    relation_str = edge.relation if isinstance(edge.relation, str) else str(edge.relation)
    return (
        edge.source_id, edge.target_id, relation_str, edge.evidence, float(edge.weight),
    )


def promote_evidence_graph(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    graph: Any,  # EvidenceGraph — imported lazily to avoid circular imports
    projects: Sequence[str] = (),
) -> dict[str, int]:
    """Idempotently promote an EvidenceGraph to substrate.

    Writes one row to evidence_graph_build, then bulk-inserts nodes and edges.
    DELETEs prior rows for the same refresh_id first (child tables first, then
    parent).

    Returns: {"build": 1, "nodes": N, "edges": M}.
    """
    log.info(
        "promote_evidence_graph: writing refresh_id=%s nodes=%d edges=%d",
        refresh_id,
        len(graph.nodes),
        len(graph.edges),
    )
    # DuckDB 1.1 has primary-index limitations around delete-then-insert and
    # cannot INSERT OR REPLACE rows containing LIST columns. Clear the small
    # parent row before the bulk child-row transaction.
    conn.execute("DELETE FROM evidence_edge WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_node WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_graph_build WHERE refresh_id = ?", [refresh_id])
    conn.execute("BEGIN TRANSACTION")
    try:
        caveats_json = json.dumps(
            [
                {"source": c.source, "status": c.status, "message": c.message}
                for c in graph.caveats
            ]
        )
        mode_str = graph.mode if isinstance(graph.mode, str) else str(graph.mode)
        conn.execute(
            """
            INSERT INTO evidence_graph_build (
                refresh_id, start_date, end_date, mode, projects,
                node_count, edge_count, caveats, generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                refresh_id, graph.start, graph.end, mode_str, list(projects),
                len(graph.nodes), len(graph.edges), caveats_json, graph.generated_at,
            ],
        )

        log.info("promote_evidence_graph: writing evidence_node rows")
        node_count = promote_rows(
            conn,
            table="evidence_node",
            columns=_EVIDENCE_NODE_COLUMNS,
            refresh_id=refresh_id,
            rows=graph.nodes,
            extractor=_extract_node,
            batch_size=5000,
            refresh_id_position="first",
            delete_existing=False,
            wrap_transaction=False,  # this fn owns the surrounding transaction
        )
        log.info("promote_evidence_graph: writing evidence_edge rows")
        edge_count = promote_rows(
            conn,
            table="evidence_edge",
            columns=_EVIDENCE_EDGE_COLUMNS,
            refresh_id=refresh_id,
            rows=graph.edges,
            extractor=_extract_edge,
            batch_size=5000,
            refresh_id_position="first",
            delete_existing=False,
            wrap_transaction=False,  # this fn owns the surrounding transaction
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_exc:  # noqa: BLE001 - preserve original promote failure.
            log.warning("promote_evidence_graph: rollback failed after promote error: %s", rollback_exc)
        raise

    log.info(
        "promote_evidence_graph: refresh_id=%s nodes=%d edges=%d",
        refresh_id, node_count, edge_count,
    )
    return {"build": 1, "nodes": node_count, "edges": edge_count}


__all__ = [
    "compute_file_overlap_edges",
    "compute_symbol_overlap_edges",
    "list_evidence_graph_builds",
    "load_evidence_graph",
    "promote_evidence_graph",
]
