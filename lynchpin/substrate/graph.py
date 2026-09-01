"""Evidence graph table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Any, cast

from lynchpin.substrate._filters import build_where
from lynchpin.substrate._helpers import promote_rows
from lynchpin.core.evidence import EVIDENCE_GRAPH_ORPHAN_CAVEAT, dedupe_caveats

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

    Each returned ``EvidenceEdge`` has weight 0.85 and an evidence string of the
    form ``'shared paths: a, b, c'`` or ``'shared paths: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_paths`` from DuckDB ``list_intersect`` is returned as a Python
    list; we sort in Python to guarantee deterministic evidence strings
    (list_intersect does not guarantee order).
    """
    from lynchpin.core.evidence_graph import EvidenceEdge

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

    Each returned ``EvidenceEdge`` has weight 0.95 and an evidence string of the
    form ``'shared symbols: a, b, c'`` or ``'shared symbols: a, b, c (+N)'``,
    exactly matching the Python builder.

    ``shared_symbols`` from ``ARRAY_AGG(DISTINCT ...)`` is a Python list with
    non-deterministic order; we sort in Python before formatting.
    """
    from lynchpin.core.evidence_graph import EvidenceEdge

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
            "SELECT refresh_id, start_date, end_date, mode, generated_at, caveats, "
            "predecessor_refresh_id, predecessor_tail_start "
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
            f"SELECT refresh_id, start_date, end_date, mode, generated_at, caveats, "
            f"predecessor_refresh_id, predecessor_tail_start "
            f"FROM evidence_graph_build {b_where} ORDER BY generated_at DESC LIMIT 1",
            b_params,
        ).fetchall()

    if not build_rows:
        return None

    (
        rid,
        start_date,
        end_date,
        build_mode,
        generated_at,
        build_caveats,
        predecessor_rid,
        predecessor_tail_start,
    ) = build_rows[0]

    lineage = _graph_lineage(
        conn,
        refresh_id=str(rid),
        predecessor_refresh_id=predecessor_rid,
        predecessor_tail_start=predecessor_tail_start,
    )

    # ------------------------------------------------------------------
    # 2. Hydrate nodes
    # ------------------------------------------------------------------
    node_rows: list[tuple[Any, ...]] = []
    shadowed_ids: set[str] = set()
    for partition_id, cutoff in lineage:
        sql = (
            "SELECT id, kind, source, date, project, summary, start_ts, end_ts, "
            "url, payload, provenance, caveats FROM evidence_node WHERE refresh_id = ?"
        )
        params: list[Any] = [partition_id]
        if cutoff is not None:
            sql += " AND date < ?"
            params.append(cutoff)
        partition_rows = conn.execute(sql, params).fetchall()
        for row in partition_rows:
            if str(row[0]) not in shadowed_ids:
                node_rows.append(row)
        shadowed_ids.update(str(row[0]) for row in partition_rows)

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
    edge_rows: list[tuple[Any, ...]] = []
    shadowed_edge_ids: set[tuple[str, str, str]] = set()
    newer_partition_ids: set[str] = set()
    for partition_id, cutoff in lineage:
        sql = (
            "SELECT source_id, target_id, relation, evidence, weight "
            "FROM evidence_edge WHERE refresh_id = ?"
        )
        params = [partition_id]
        if cutoff is not None:
            sql += (
                " AND source_id NOT IN (SELECT id FROM evidence_node WHERE refresh_id = ? AND date >= ?)"
                " AND target_id NOT IN (SELECT id FROM evidence_node WHERE refresh_id = ? AND date >= ?)"
            )
            params.extend([partition_id, cutoff, partition_id, cutoff])
        for row in conn.execute(sql, params).fetchall():
            key = (str(row[0]), str(row[1]), str(row[2]))
            if (
                key not in shadowed_edge_ids
                and key[0] not in newer_partition_ids
                and key[1] not in newer_partition_ids
            ):
                edge_rows.append(row)
            shadowed_edge_ids.add(key)
        newer_partition_ids.update(
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM evidence_node WHERE refresh_id = ?", [partition_id]
            ).fetchall()
        )

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
        caveats=dedupe_caveats(
            _hydrate_caveats(build_caveats) + (EVIDENCE_GRAPH_ORPHAN_CAVEAT,)
        ),
    )


def _graph_lineage(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    predecessor_refresh_id: str | None = None,
    predecessor_tail_start: date | None = None,
) -> list[tuple[str, date | None]]:
    """Return newest-to-oldest physical graph partitions and their cutoffs."""
    if predecessor_refresh_id is None or predecessor_tail_start is None:
        build_row = conn.execute(
            "SELECT predecessor_refresh_id, predecessor_tail_start "
            "FROM evidence_graph_build WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()
        predecessor_refresh_id, predecessor_tail_start = (
            build_row if build_row is not None else (None, None)
        )
    lineage: list[tuple[str, date | None]] = [(refresh_id, None)]
    current_refresh_id = predecessor_refresh_id
    current_tail_start = predecessor_tail_start
    while current_refresh_id is not None and current_tail_start is not None:
        lineage.append((str(current_refresh_id), current_tail_start))
        parent = conn.execute(
            "SELECT predecessor_refresh_id, predecessor_tail_start "
            "FROM evidence_graph_build WHERE refresh_id = ?",
            [current_refresh_id],
        ).fetchone()
        current_refresh_id, current_tail_start = (
            parent if parent is not None else (None, None)
        )
    return lineage


def _logical_graph_counts(
    conn: "duckdb.DuckDBPyConnection", *, refresh_id: str
) -> tuple[int, int]:
    """Count the logical overlay without hydrating graph payloads into Python."""
    lineage = _graph_lineage(conn, refresh_id=refresh_id)
    conn.execute(
        "CREATE OR REPLACE TEMPORARY TABLE logical_count_node_ids "
        "(id VARCHAR PRIMARY KEY)"
    )
    conn.execute(
        "CREATE OR REPLACE TEMPORARY TABLE logical_count_edge_ids "
        "(source_id VARCHAR, target_id VARCHAR, relation VARCHAR, "
        "PRIMARY KEY (source_id, target_id, relation))"
    )
    conn.execute(
        "CREATE OR REPLACE TEMPORARY TABLE logical_count_newer_node_ids "
        "(id VARCHAR PRIMARY KEY)"
    )
    for partition_id, cutoff in lineage:
        node_sql = (
            "INSERT INTO logical_count_node_ids "
            "SELECT id FROM evidence_node WHERE refresh_id = ?"
        )
        node_params: list[Any] = [partition_id]
        if cutoff is not None:
            node_sql += " AND date < ?"
            node_params.append(cutoff)
        node_sql += " ON CONFLICT (id) DO NOTHING"
        conn.execute(node_sql, node_params)

        edge_sql = (
            "INSERT INTO logical_count_edge_ids "
            "SELECT source_id, target_id, relation FROM evidence_edge "
            "WHERE refresh_id = ?"
        )
        edge_params: list[Any] = [partition_id]
        if cutoff is not None:
            edge_sql += (
                " AND source_id NOT IN (SELECT id FROM evidence_node "
                "WHERE refresh_id = ? AND date >= ?)"
                " AND target_id NOT IN (SELECT id FROM evidence_node "
                "WHERE refresh_id = ? AND date >= ?)"
            )
            edge_params.extend([partition_id, cutoff, partition_id, cutoff])
        edge_sql += (
            " AND source_id NOT IN (SELECT id FROM logical_count_newer_node_ids)"
            " AND target_id NOT IN (SELECT id FROM logical_count_newer_node_ids)"
            " ON CONFLICT (source_id, target_id, relation) DO NOTHING"
        )
        conn.execute(edge_sql, edge_params)
        conn.execute(
            "INSERT INTO logical_count_newer_node_ids "
            "SELECT id FROM evidence_node WHERE refresh_id = ? "
            "ON CONFLICT (id) DO NOTHING",
            [partition_id],
        )
    return (
        int(conn.execute("SELECT COUNT(*) FROM logical_count_node_ids").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM logical_count_edge_ids").fetchone()[0]),
    )


def _logical_graph_relation(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    table: str,
    columns: Sequence[str],
    key_columns: Sequence[str],
    cutoff_column: str | None = "date",
) -> tuple[str, list[Any]]:
    """Return a bounded SQL relation for the logical graph at ``refresh_id``.

    Incremental graph tables retain one physical partition per generation. The
    newest partition replaces predecessor rows in its tail, while older rows
    remain authoritative before that cutoff. This relation applies those same
    rules to any graph-backed table and shadows rows by the supplied logical
    key. Identifiers are code-owned table/column names, not user input.
    """
    lineage = _graph_lineage(conn, refresh_id=refresh_id)
    if not columns or not key_columns:
        raise ValueError("logical graph relations require columns and keys")
    selected = ", ".join(columns)
    keys = ", ".join(key_columns)
    parts: list[str] = []
    params: list[Any] = []
    for rank, (partition_id, cutoff) in enumerate(lineage):
        clauses = ["refresh_id = ?"]
        params.append(partition_id)
        if cutoff is not None and cutoff_column is not None:
            clauses.append(f"({cutoff_column} IS NULL OR {cutoff_column} < ?)")
            params.append(cutoff)
        parts.append(
            f"SELECT {selected}, {rank} AS _lineage_rank FROM {table} "
            f"WHERE {' AND '.join(clauses)}"
        )
    union = " UNION ALL ".join(parts)
    relation = (
        f"(SELECT {selected} FROM ({union}) AS _graph_rows "
        f"QUALIFY ROW_NUMBER() OVER "
        f"(PARTITION BY {keys} ORDER BY _lineage_rank) = 1)"
    )
    return relation, params


# ── evidence_graph ────────────────────────────────────────────────────────────


_EVIDENCE_NODE_COLUMNS = (
    "id",
    "kind",
    "source",
    "date",
    "project",
    "summary",
    "start_ts",
    "end_ts",
    "url",
    "payload",
    "provenance",
    "caveats",
)

_EVIDENCE_EDGE_COLUMNS = (
    "source_id",
    "target_id",
    "relation",
    "evidence",
    "weight",
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
        node.id,
        kind_str,
        node.source,
        node.date,
        node.project,
        node.summary,
        node.start,
        node.end,
        node.url,
        payload_json,
        provenance_struct,
        node_caveats_json,
    )


def _extract_edge(edge: Any) -> tuple[Any, ...]:
    relation_str = (
        edge.relation if isinstance(edge.relation, str) else str(edge.relation)
    )
    return (
        edge.source_id,
        edge.target_id,
        relation_str,
        edge.evidence,
        float(edge.weight),
    )


def load_evidence_graph_boundary_nodes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    tail_start: date,
    lookback_days: int,
) -> tuple[Any, ...]:
    """Load the small predecessor slice needed to derive tail-crossing edges.

    The date lookback covers bounded relation builders. The timestamp predicate
    additionally retains an earlier node whose interval remains open at the
    replacement boundary, which is the only way temporal-overlap relations can
    cross an arbitrarily old start date.
    """
    from datetime import datetime, time, timedelta
    from lynchpin.core.evidence_graph import EvidenceNode

    lookback_start = tail_start - timedelta(days=lookback_days)
    boundary_start = datetime.combine(tail_start, time.min).astimezone()
    relation, params = _logical_graph_relation(
        conn,
        refresh_id=refresh_id,
        table="evidence_node",
        columns=(
            "id",
            "kind",
            "source",
            "date",
            "project",
            "summary",
            "start_ts",
            "end_ts",
            "url",
            "payload",
            "provenance",
            "caveats",
        ),
        key_columns=("id",),
    )
    rows = conn.execute(
        f"""
        SELECT id, kind, source, date, project, summary,
               start_ts, end_ts, url, payload, provenance, caveats
        FROM {relation}
        WHERE (
              date >= ?
              OR (end_ts IS NOT NULL AND end_ts >= ?)
          )
        """,
        [*params, lookback_start, boundary_start],
    ).fetchall()
    return tuple(
        EvidenceNode(
            id=n_id,
            kind=n_kind,
            source=source,
            date=n_date,
            project=project,
            summary=summary or "",
            start=start_ts,
            end=end_ts,
            url=url,
            payload=_hydrate_payload(payload),
            provenance=_hydrate_provenance(provenance),
            caveats=_hydrate_caveats(caveats),
        )
        for (
            n_id,
            n_kind,
            source,
            n_date,
            project,
            summary,
            start_ts,
            end_ts,
            url,
            payload,
            provenance,
            caveats,
        ) in rows
    )


def compatible_graph_predecessor(
    conn: "duckdb.DuckDBPyConnection",
    *,
    current_refresh_id: str,
    full_start: date,
    tail_start: date,
    projects: Sequence[str] = (),
) -> str | None:
    """Return the compatible predecessor for one incremental generation.

    Only a graph that belongs to a published promotion (``ok`` or ``degraded``)
    and has an ``ok`` graph readiness row is eligible. A degraded generation
    can have incomplete optional sources while its graph carrier remains valid.
    Scope is exact: an all-project generation is
    never used as a project-scoped predecessor and vice versa.  In particular,
    a failed or partial graph row left behind by a prior attempt cannot become
    the historical carrier merely because it is newer.
    """
    requested_projects = sorted(set(projects))
    scope_predicate = (
        "len(graph.projects) = 0" if not requested_projects else "graph.projects = ?"
    )
    params: list[Any] = [full_start, tail_start]
    if requested_projects:
        params.append(requested_projects)
    params.extend([current_refresh_id])
    row = conn.execute(
        f"""
        SELECT graph.refresh_id
        FROM evidence_graph_build AS graph
        JOIN substrate_promotion_run AS promotion
          ON promotion.refresh_id = graph.refresh_id
         AND promotion.status IN ('ok', 'degraded')
        WHERE graph.start_date <= ?
          AND graph.end_date >= ?
          AND {scope_predicate}
          AND graph.refresh_id <> ?
          AND EXISTS (
              SELECT 1
              FROM substrate_source_status AS readiness
              WHERE readiness.refresh_id = graph.refresh_id
                AND readiness.source = 'evidence_graph'
                AND readiness.status = 'ok'
          )
        ORDER BY graph.generated_at DESC, graph.materialized_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return str(row[0]) if row is not None else None


def promote_incremental_evidence_graph(
    conn: "duckdb.DuckDBPyConnection",
    *,
    previous_refresh_id: str,
    refresh_id: str,
    graph: Any,
    full_start: date,
    tail_start: date,
    projects: Sequence[str] = (),
) -> dict[str, int]:
    """Publish a new full graph by overlaying its predecessor and replacing a tail.

    The database already belongs to a candidate generation, so this operation
    changes only that candidate. A new refresh records the predecessor
    partition instead of copying its historical rows. Rows and relations
    touching the tail are replaced from ``graph``; caller-provided graph edges
    include both tail-internal and boundary-crossing relations.
    """
    requested_projects = sorted(set(projects))
    replacing_existing_refresh = previous_refresh_id == refresh_id
    if replacing_existing_refresh:
        predecessor = conn.execute(
            """
            SELECT 1
            FROM evidence_graph_build AS graph
            WHERE graph.refresh_id = ?
              AND graph.start_date <= ?
              AND graph.end_date >= ?
              AND ((len(graph.projects) = 0 AND ? = []) OR graph.projects = ?)
            """,
            [
                previous_refresh_id,
                full_start,
                tail_start,
                requested_projects,
                requested_projects,
            ],
        ).fetchone()
    else:
        predecessor = conn.execute(
            """
            SELECT 1
            FROM evidence_graph_build AS graph
            JOIN substrate_promotion_run AS promotion
              ON promotion.refresh_id = graph.refresh_id
             AND promotion.status IN ('ok', 'degraded')
            WHERE graph.refresh_id = ?
              AND graph.start_date <= ?
              AND graph.end_date >= ?
              AND ((len(graph.projects) = 0 AND ? = []) OR graph.projects = ?)
              AND EXISTS (
                  SELECT 1 FROM substrate_source_status AS readiness
                  WHERE readiness.refresh_id = graph.refresh_id
                    AND readiness.source = 'evidence_graph'
                    AND readiness.status = 'ok'
              )
            """,
            [
                previous_refresh_id,
                full_start,
                tail_start,
                requested_projects,
                requested_projects,
            ],
        ).fetchone()
    if predecessor is None:
        raise ValueError(
            f"incremental graph predecessor is missing: {previous_refresh_id}"
        )

    if replacing_existing_refresh:
        archived_refresh_id = archive_evidence_graph_partition(
            conn, refresh_id=refresh_id
        )
        previous_refresh_id = archived_refresh_id
        replacing_existing_refresh = False

    conn.execute("DELETE FROM evidence_edge WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_node WHERE refresh_id = ?", [refresh_id])
    conn.execute("DELETE FROM evidence_graph_build WHERE refresh_id = ?", [refresh_id])
    conn.execute("BEGIN TRANSACTION")
    try:
        if not replacing_existing_refresh:
            # The candidate is already a copy-on-write clone of the verified
            # predecessor. Keep historical rows in that partition and record
            # the bounded overlay on the new build instead of copying them.
            pass
        promote_rows(
            conn,
            table="evidence_node",
            columns=_EVIDENCE_NODE_COLUMNS,
            refresh_id=refresh_id,
            rows=graph.nodes,
            extractor=_extract_node,
            batch_size=5000,
            refresh_id_position="first",
            delete_existing=False,
            wrap_transaction=False,
        )
        promote_rows(
            conn,
            table="evidence_edge",
            columns=_EVIDENCE_EDGE_COLUMNS,
            refresh_id=refresh_id,
            rows=graph.edges,
            extractor=_extract_edge,
            batch_size=5000,
            refresh_id_position="first",
            delete_existing=False,
            wrap_transaction=False,
        )
        caveats_json = json.dumps(
            [
                {
                    "source": caveat.source,
                    "status": caveat.status,
                    "message": caveat.message,
                }
                for caveat in graph.caveats
            ]
        )
        mode_str = graph.mode if isinstance(graph.mode, str) else str(graph.mode)
        build_values = [
            refresh_id,
            full_start,
            graph.end,
            mode_str,
            list(projects),
            0,
            0,
            caveats_json,
            graph.generated_at,
        ]
        conn.execute(
            """
            INSERT INTO evidence_graph_build (
                refresh_id, start_date, end_date, mode, projects,
                node_count, edge_count, caveats, generated_at,
                predecessor_refresh_id, predecessor_tail_start
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            build_values
            + [
                previous_refresh_id,
                tail_start,
            ],
        )
        node_count, edge_count = _logical_graph_counts(conn, refresh_id=refresh_id)
        conn.execute(
            "UPDATE evidence_graph_build SET node_count = ?, edge_count = ? "
            "WHERE refresh_id = ?",
            [node_count, edge_count, refresh_id],
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as rollback_exc:  # noqa: BLE001 - preserve original promote failure.
            log.warning(
                "promote_incremental_evidence_graph: rollback failed: %s", rollback_exc
            )
        raise
    return {"build": 1, "nodes": node_count, "edges": edge_count}


def archive_evidence_graph_partition(
    conn: "duckdb.DuckDBPyConnection", *, refresh_id: str
) -> str:
    """Rekey one physical graph partition so its logical ID can be retried.

    The operation runs before graph construction in the normal incremental
    path. Keeping it separate prevents the large indexed update from sharing
    a process memory peak with graph-source and analysis-overlay state.
    """
    archived_refresh_id = f"{refresh_id}:partition:{uuid.uuid4().hex}"
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "UPDATE evidence_edge SET refresh_id = ? WHERE refresh_id = ?",
            [archived_refresh_id, refresh_id],
        )
        conn.execute(
            "UPDATE evidence_node SET refresh_id = ? WHERE refresh_id = ?",
            [archived_refresh_id, refresh_id],
        )
        conn.execute(
            "UPDATE evidence_graph_build SET refresh_id = ? WHERE refresh_id = ?",
            [archived_refresh_id, refresh_id],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return archived_refresh_id


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
                refresh_id,
                graph.start,
                graph.end,
                mode_str,
                list(projects),
                len(graph.nodes),
                len(graph.edges),
                caveats_json,
                graph.generated_at,
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
            log.warning(
                "promote_evidence_graph: rollback failed after promote error: %s",
                rollback_exc,
            )
        raise

    log.info(
        "promote_evidence_graph: refresh_id=%s nodes=%d edges=%d",
        refresh_id,
        node_count,
        edge_count,
    )
    return {"build": 1, "nodes": node_count, "edges": edge_count}


__all__ = [
    "compute_file_overlap_edges",
    "compute_symbol_overlap_edges",
    "compatible_graph_predecessor",
    "list_evidence_graph_builds",
    "archive_evidence_graph_partition",
    "load_evidence_graph",
    "load_evidence_graph_boundary_nodes",
    "promote_evidence_graph",
    "promote_incremental_evidence_graph",
]
