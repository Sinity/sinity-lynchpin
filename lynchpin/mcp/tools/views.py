"""View-backed MCP tools: project-day correlations, closure chains, overlaps.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id, dataclass_to_json_dict, json_safe as _json_safe

@app.tool()
def project_day_correlations(refresh_id: str | None=None, start: str | None=None, end: str | None=None, projects: list[str] | None=None, min_source_count: int | None=None) -> list[dict[str, Any]]:
    from datetime import date as _date
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.derived import load_project_day_correlations
    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    path = substrate_path()
    with connect(path) as conn:
        rows = load_project_day_correlations(conn, refresh_id=refresh_id, start=start_d, end=end_d, projects=projs, min_source_count=min_source_count)
    return [dataclass_to_json_dict(row) for row in rows]

@app.tool()
def closure_chain_walks(refresh_id: str | None=None, project: str | None=None, min_chain_depth: int | None=None) -> list[dict[str, Any]]:
    """Query the issue_closure_chain_walk view.

    Uses ``lynchpin.substrate.derived.load_issue_closure_chain_walks``.

    Parameters:
        refresh_id:     filter to a specific evidence-graph build.
        project:        filter by project name.
        min_chain_depth: only return chains with depth >= N.

    Returns list of dicts with keys: refresh_id, root_id, project,
    issue_number, reachable_node_ids, chain_depth, reachable_count.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.derived import load_issue_closure_chain_walks
    path = substrate_path()
    with connect(path) as conn:
        rows = load_issue_closure_chain_walks(conn, refresh_id=refresh_id, project=project, min_chain_depth=min_chain_depth)
    return [dataclass_to_json_dict(row) for row in rows]

@app.tool()
def file_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_file_overlap view and return edge dicts.

    Each row represents a file-overlap edge between an AI work-event node
    and a commit node that share file paths within ±24 h.

    Parameters:
        we_refresh_id:     filter to a specific work-event promote batch.
        commit_refresh_id: filter to a specific commit promote batch.

    Returns list of dicts with keys: source_id, target_id, relation,
    evidence, weight.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import compute_file_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_file_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def symbol_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_symbol_overlap view and return edge dicts.

    Each row represents a symbol-overlap edge between an AI work-event node
    and a commit node that reference the same qualified symbol names.

    Parameters:
        we_refresh_id:     filter to a specific work-event promote batch.
        commit_refresh_id: filter to a specific commit promote batch.

    Returns list of dicts with keys: source_id, target_id, relation,
    evidence, weight.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import compute_symbol_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_symbol_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def context_pack_diff(refresh_a: str | None=None, refresh_b: str | None=None) -> dict[str, Any]:
    """Compare two substrate refresh snapshots (Arc D.2).

    Computes the difference between two refresh_ids across every source
    dimension: commits, ai_work_events, evidence nodes/edges, and per-source
    status. This is the "compare to prior month" answer from the I.1 success
    criterion — a single query instead of a DAG re-run.

    Parameters:
        refresh_a: "before" refresh_id (default: second-most-recent promote).
        refresh_b: "after"  refresh_id (default: most-recent promote).

    Returns:
        {
            "refresh_a": str, "refresh_b": str,
            "commits": {"a": N, "b": N, "delta": N}, ...,
            "project_day_top_changes": [...],
            "source_status_diffs": [...],
        }
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        refresh_ids = [r[0] for r in conn.execute('SELECT DISTINCT refresh_id FROM substrate_source_status ORDER BY recorded_at').fetchall()]
    if len(refresh_ids) < 1:
        return {'error': 'no refresh snapshots found'}
    if refresh_b is None:
        refresh_b = refresh_ids[-1]
    if refresh_a is None:
        refresh_a = refresh_ids[-2] if len(refresh_ids) >= 2 else refresh_ids[-1]
    diffs: dict[str, Any] = {'refresh_a': refresh_a, 'refresh_b': refresh_b}
    with connect(substrate_path(), read_only=True) as conn:
        tables = ('commit_fact', 'file_change_fact', 'ai_work_event', 'symbol_change', 'pr_review_row', 'evidence_node', 'evidence_edge')
        for table in tables:
            try:
                a_row = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id = ?', [refresh_a]).fetchone()
                b_row = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id = ?', [refresh_b]).fetchone()
                a = a_row[0] if a_row else 0
                b = b_row[0] if b_row else 0
                diffs[table] = {'a': a, 'b': b, 'delta': b - a}
            except Exception:
                diffs[table] = {'a': -1, 'b': -1, 'delta': 0, 'error': f'table {table} may not have refresh_id column'}
        diffs['project_day_top_changes'] = []
        try:
            pd_rows = conn.execute('\n                WITH a AS (\n                    SELECT project, date, commit_count,\n                           ai_work_event_count, source_count\n                    FROM project_day_correlation\n                    WHERE refresh_id = ?\n                ),\n                b AS (\n                    SELECT project, date, commit_count,\n                           ai_work_event_count, source_count\n                    FROM project_day_correlation\n                    WHERE refresh_id = ?\n                )\n                SELECT\n                    COALESCE(a.project, b.project) AS project,\n                    COALESCE(a.date, b.date) AS date,\n                    COALESCE(a.commit_count, 0) AS a_commits,\n                    COALESCE(b.commit_count, 0) AS b_commits,\n                    COALESCE(b.commit_count, 0) - COALESCE(a.commit_count, 0) AS delta\n                FROM a\n                FULL OUTER JOIN b ON a.project = b.project AND a.date = b.date\n                WHERE COALESCE(b.commit_count, 0) <> COALESCE(a.commit_count, 0)\n                ORDER BY ABS(COALESCE(b.commit_count, 0) - COALESCE(a.commit_count, 0)) DESC\n                LIMIT 20\n            ', [refresh_a, refresh_b]).fetchall()
            diffs['project_day_top_changes'] = [{'project': r[0], 'date': _json_safe(r[1]), 'a_commits': r[2], 'b_commits': r[3], 'delta': r[4]} for r in pd_rows]
        except Exception:
            pass
        diffs['source_status_diffs'] = []
        try:
            st_rows = conn.execute('\n                WITH a AS (\n                    SELECT source, status FROM substrate_source_status\n                    WHERE refresh_id = ?\n                ),\n                b AS (\n                    SELECT source, status FROM substrate_source_status\n                    WHERE refresh_id = ?\n                )\n                SELECT COALESCE(a.source, b.source) AS source,\n                       a.status AS status_a, b.status AS status_b\n                FROM a\n                FULL OUTER JOIN b ON a.source = b.source\n                ORDER BY source\n            ', [refresh_a, refresh_b]).fetchall()
            diffs['source_status_diffs'] = [{'source': r[0], 'status_a': r[1], 'status_b': r[2]} for r in st_rows]
        except Exception:
            pass
    return diffs

@app.tool()
def project_relationship_graph(refresh_id: str | None=None, min_edge_count: int=1) -> list[dict[str, Any]]:
    """Cross-project evidence edge graph (Arc M.11).

    Finds edges between evidence nodes in different projects — shared AI
    sessions, commit references across repos, temporal co-occurrence.
    Produces a directed graph: (source_project, target_project, edge_count)
    suitable for visualization or dependency analysis.

    Parameters:
        refresh_id:     promote snapshot (default: latest).
        min_edge_count: minimum edge count to include a pair.

    Returns:
        [{"source_project": str, "target_project": str, "edge_count": int}]
    """
    from lynchpin.substrate.connection import connect, substrate_path
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, 'evidence_node')
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT ns.project AS proj_a, nt.project AS proj_b,\n                   COUNT(*) AS edge_count\n            FROM evidence_edge e\n            JOIN evidence_node ns\n              ON ns.id = e.source_id AND ns.refresh_id = e.refresh_id\n            JOIN evidence_node nt\n              ON nt.id = e.target_id AND nt.refresh_id = e.refresh_id\n            WHERE ns.project IS NOT NULL\n              AND nt.project IS NOT NULL\n              AND ns.project != nt.project\n              AND e.refresh_id = ?\n            GROUP BY ns.project, nt.project\n            HAVING COUNT(*) >= ?\n            ORDER BY edge_count DESC\n        ', [refresh_id, int(min_edge_count)]).fetchall()
    return [{'source_project': r[0], 'target_project': r[1], 'edge_count': r[2]} for r in rows]

@app.tool()
def frontier_slo(projects: list[str] | None=None, refresh_id: str | None=None) -> dict[str, Any]:
    """PR review SLO dashboard over pr_review_row (Arc M.9).

    Time-to-merge quantiles, review round distributions, and friction
    signal breakdowns per project.

    Parameters:
        projects:   filter to specific projects; None = all.
        refresh_id: snapshot (default: latest).

    Returns:
        {
            "refresh_id": str,
            "total_prs": int,
            "merged": int,
            "per_project": [{"project": str, "prs": int, "merged": int,
                              "p50_merge_m": float, "p75_merge_m": float,
                              "avg_rounds": float, "friction_pct": float}],
            "overall": {"p50_merge_m": float, "p75_merge_m": float,
                        "p95_merge_m": float, "avg_rounds": float},
            "friction_breakdown": [{"project": str, "signal": str, "count": int}],
        }
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, 'pr_review_row')
            if refresh_id is None:
                return {'error': 'no promote runs'}
        proj_filter = ''
        params: list[Any] = [refresh_id]
        if projects:
            placeholders = ','.join(['?'] * len(projects))
            proj_filter = f'AND project IN ({placeholders})'
            params.extend(projects)
        per_project = conn.execute(f"\n            SELECT project,\n                   COUNT(*) AS prs,\n                   SUM(CASE WHEN state = 'merged' THEN 1 ELSE 0 END) AS merged,\n                   ROUND(QUANTILE_CONT(time_to_merge_minutes, 0.5), 1) AS p50_m,\n                   ROUND(QUANTILE_CONT(time_to_merge_minutes, 0.75), 1) AS p75_m,\n                   ROUND(AVG(review_round_count), 1) AS avg_rounds,\n                   ROUND(\n                       SUM(CASE WHEN len(friction_signals) > 0 THEN 1 ELSE 0 END)\n                       * 100.0 / NULLIF(SUM(CASE WHEN state = 'merged' THEN 1 ELSE 0 END), 0), 1\n                   ) AS friction_pct\n            FROM pr_review_row\n            WHERE refresh_id = ? {proj_filter}\n            GROUP BY project\n            ORDER BY prs DESC\n        ", params).fetchall()
        overall = conn.execute(f'\n            SELECT ROUND(QUANTILE_CONT(time_to_merge_minutes, 0.5), 1),\n                   ROUND(QUANTILE_CONT(time_to_merge_minutes, 0.75), 1),\n                   ROUND(QUANTILE_CONT(time_to_merge_minutes, 0.95), 1),\n                   ROUND(AVG(review_round_count), 1)\n            FROM pr_review_row\n            WHERE refresh_id = ? AND merged_at IS NOT NULL\n              {proj_filter}\n        ', params).fetchone()
        friction = conn.execute(f'\n            SELECT project, signal, COUNT(*) AS cnt\n            FROM (\n                SELECT project, UNNEST(friction_signals) AS signal\n                FROM pr_review_row\n                WHERE refresh_id = ? AND len(friction_signals) > 0 {proj_filter}\n            ) f\n            GROUP BY project, signal\n            ORDER BY cnt DESC LIMIT 15\n        ', params).fetchall()
        total_prs = sum((r[1] for r in per_project))
        total_merged = sum((r[2] for r in per_project))
    return {'refresh_id': refresh_id, 'total_prs': total_prs, 'merged': total_merged, 'per_project': [{'project': r[0], 'prs': r[1], 'merged': r[2], 'p50_merge_m': r[3], 'p75_merge_m': r[4], 'avg_rounds': r[5], 'friction_pct': r[6]} for r in per_project], 'overall': {'p50_merge_m': overall[0] if overall else None, 'p75_merge_m': overall[1] if overall else None, 'p95_merge_m': overall[2] if overall else None, 'avg_rounds': overall[3] if overall else None}, 'friction_breakdown': [{'project': r[0], 'signal': r[1], 'count': r[2]} for r in friction]}
