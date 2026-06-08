"""View-backed MCP tools: project-day correlations, closure chains, overlaps.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_materialized_refresh_id, dataclass_to_json_dict, ensure_substrate_materialized_for_read, half_open_date_window, json_safe as _json_safe, pinned_materialization_for_read

@app.tool()
def project_day_correlations(refresh_id: str | None=None, start: str | None=None, end: str | None=None, projects: list[str] | None=None, min_source_count: int | None=None) -> list[dict[str, Any]]:
    from datetime import date as _date
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.derived import load_project_day_correlations
    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='project_day_correlations', window=half_open_date_window(start_d, end_d))
    path = substrate_path()
    with connect(path) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'project_day_correlation', caller='project_day_correlations')
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
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='closure_chain_walks')
    path = substrate_path()
    with connect(path) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'issue_closure_chain_walk', caller='closure_chain_walks')
        rows = load_issue_closure_chain_walks(conn, refresh_id=refresh_id, project=project, min_chain_depth=min_chain_depth)
    return [dataclass_to_json_dict(row) for row in rows]

def file_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_file_overlap view and return edge dicts."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import compute_file_overlap_edges
    if we_refresh_id is None or commit_refresh_id is None:
        ensure_substrate_materialized_for_read(caller='file_overlap_edges')
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_file_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

def symbol_overlap_edges(we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Query the work_event_symbol_overlap view and return edge dicts."""
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import compute_symbol_overlap_edges
    if we_refresh_id is None or commit_refresh_id is None:
        ensure_substrate_materialized_for_read(caller='symbol_overlap_edges')
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_symbol_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def overlap_edges(level: str='file', we_refresh_id: str | None=None, commit_refresh_id: str | None=None) -> list[dict[str, Any]]:
    """AI work-event ↔ commit overlap edges. level: file (shared paths ±24h), symbol (shared qualified symbol names)."""
    if level == 'file':
        return file_overlap_edges(we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    if level == 'symbol':
        return symbol_overlap_edges(we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'error': f'unknown level {level!r}. choices: file, symbol'}]

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
    from lynchpin.substrate.snapshots import ordered_materialized_refresh_ids
    with connect(substrate_path(), read_only=True) as conn:
        refresh_ids = ordered_materialized_refresh_ids(conn, caller='context_pack_diff')
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
    materialization = ensure_substrate_materialized_for_read(caller='frontier_slo') if refresh_id is None else pinned_materialization_for_read(caller='frontier_slo', refresh_id=refresh_id)
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'pr_review_row', caller='review_friction_summary')
            if refresh_id is None:
                return {'error': 'no promote runs', 'materialization': materialization}
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
    return {'refresh_id': refresh_id, 'materialization': materialization, 'total_prs': total_prs, 'merged': total_merged, 'per_project': [{'project': r[0], 'prs': r[1], 'merged': r[2], 'p50_merge_m': r[3], 'p75_merge_m': r[4], 'avg_rounds': r[5], 'friction_pct': r[6]} for r in per_project], 'overall': {'p50_merge_m': overall[0] if overall else None, 'p75_merge_m': overall[1] if overall else None, 'p95_merge_m': overall[2] if overall else None, 'avg_rounds': overall[3] if overall else None}, 'friction_breakdown': [{'project': r[0], 'signal': r[1], 'count': r[2]} for r in friction]}

@app.tool()
def project_pair_signals(project_a: str | None=None, project_b: str | None=None, refresh_id: str | None=None, limit: int=50) -> list[dict[str, Any]]:
    from lynchpin.graph.project_relationships import build_project_relationships
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import load_evidence_graph
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='project_pair_signals')
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'evidence_node', caller='project_pair_signals')
            if refresh_id is None:
                return []
        graph = load_evidence_graph(conn, refresh_id=refresh_id)
    if graph is None:
        return []
    rel_graph = build_project_relationships(graph)
    relationships = list(rel_graph.relationships)
    if project_a is not None and project_b is not None:
        pair = tuple(sorted((project_a, project_b)))
        relationships = [rel for rel in relationships if (rel.project_a, rel.project_b) == pair]
    elif project_a is not None or project_b is not None:
        target = project_a or project_b
        relationships = [rel for rel in relationships if target in (rel.project_a, rel.project_b)]
    out: list[dict[str, Any]] = []
    for rel in relationships[:max(1, int(limit))]:
        out.append({'project_a': rel.project_a, 'project_b': rel.project_b, 'total_weight': rel.weight, 'edge_count': sum(rel.signal_counts.values()), 'signals': dict(rel.signal_counts), 'sample_evidence_node_ids': list(rel.sample_evidence_node_ids)})
    return out

@app.tool()
def walk_evidence(start_id: str, edge_kinds: list[str] | None=None, max_depth: int=3, max_nodes: int=200, direction: str='out', refresh_id: str | None=None) -> dict[str, Any]:
    """Generic BFS walk over the evidence graph from a starting node.

    Cycle-safe, bidirectional, hard-capped (depth ≤ 5, nodes ≤ 1000).
    Replaces the need for raw SQL exploration of evidence_edge for
    "what's connected to X?" questions.

    Parameters:
        start_id:    evidence_node.id to start from.
        edge_kinds:  filter edges by relation. None = all.
        max_depth:   BFS depth limit (clamped to 5).
        max_nodes:   total node visit cap (clamped to 1000).
        direction:   "out", "in", or "both".
        refresh_id:  substrate snapshot. Defaults to latest evidence_graph build.

    Returns:
        {
            "start_id", "direction", "edge_kinds", "max_depth", "max_nodes",
            "truncated": bool, "reason": str | None,
            "nodes": [{"id", "kind", "source", "date", "project", "summary",
                       "depth", "parent_edge_source", "parent_edge_target",
                       "parent_edge_relation", "direction_followed"}, ...],
            "edges": [{"source_id", "target_id", "relation", "evidence", "weight"}, ...],
        }
    """
    from lynchpin.graph.walk import walk_evidence as _walk
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.graph import load_evidence_graph
    materialization = ensure_substrate_materialized_for_read(caller='walk_evidence') if refresh_id is None else pinned_materialization_for_read(caller='walk_evidence', refresh_id=refresh_id)
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'evidence_node', caller='walk_evidence')
            if refresh_id is None:
                return {'start_id': start_id, 'direction': direction, 'edge_kinds': edge_kinds, 'max_depth': max_depth, 'max_nodes': max_nodes, 'truncated': False, 'materialization': materialization, 'reason': 'no evidence_graph build available', 'nodes': [], 'edges': []}
        graph = load_evidence_graph(conn, refresh_id=refresh_id)
    if graph is None:
        return {'start_id': start_id, 'direction': direction, 'edge_kinds': edge_kinds, 'max_depth': max_depth, 'max_nodes': max_nodes, 'truncated': False, 'materialization': materialization, 'reason': f'evidence_graph build {refresh_id!r} not found', 'nodes': [], 'edges': []}
    result = _walk(graph, start_id, edge_kinds=edge_kinds, max_depth=int(max_depth), max_nodes=int(max_nodes), direction=direction)
    return {'start_id': result.start_id, 'direction': result.direction, 'edge_kinds': list(result.edge_kinds) if result.edge_kinds else None, 'max_depth': result.max_depth, 'max_nodes': result.max_nodes, 'truncated': result.truncated, 'materialization': materialization, 'reason': result.reason, 'nodes': [{'id': step.node.id, 'kind': step.node.kind, 'source': step.node.source, 'date': _json_safe(step.node.date), 'project': step.node.project, 'summary': step.node.summary, 'depth': step.depth, 'parent_edge_source': step.parent_edge.source_id if step.parent_edge else None, 'parent_edge_target': step.parent_edge.target_id if step.parent_edge else None, 'parent_edge_relation': step.parent_edge.relation if step.parent_edge else None, 'direction_followed': step.direction_followed} for step in result.steps], 'edges': [{'source_id': edge.source_id, 'target_id': edge.target_id, 'relation': edge.relation, 'evidence': edge.evidence, 'weight': edge.weight} for edge in result.edges]}

@app.tool()
def url_crossref(start: str, end: str, min_sources: int=2, sources: str='reddit,irc,wykop,raindrop', limit: int=100) -> list[dict[str, Any]]:
    """Aggregate URL mentions across personal sources, surface cross-channel hits.

    Streams URL-bearing rows from reddit (own + extrinsic quoted blocks split
    apart), IRC (operator vs ambient), wykop (links + comments + entries),
    raindrop bookmarks, and web history visits — normalizes each URL (strip
    utm/fbclid, lowercase host, drop www, https-only) — collapses by URL.

    Parameters:
    - ``start`` / ``end``: ISO date range (inclusive).
    - ``min_sources``: minimum distinct source count for a URL to be returned.
      Default 2 surfaces URLs that crossed channels (mentioned somewhere AND
      bookmarked or visited). Pass 1 for the full ranking.
    - ``sources``: comma-separated subset of
      ``reddit,irc,wykop,raindrop,web`` — defaults to the four text-mention
      sources, excluding ``web`` because the visit firehose is huge.
    - ``limit``: max rows returned, ranked by total mention count.

    Each row carries:
    - ``url``, ``domain``, ``total_mentions``
    - ``by_source`` map (irc=N, reddit=N, …)
    - ``by_role`` map (own/quoted/mention/visit/bookmark/link counts)
    - ``first_seen`` / ``last_seen`` ISO timestamps
    - ``sample_snippets`` (up to 3 distinct context strings)

    The ``own`` vs ``quoted`` role split is important: a URL in a reddit
    blockquote is something sinity was responding to, not something sinity
    shared. Same for ``mention`` (ambient IRC) vs ``own`` (operator IRC).
    """
    from datetime import date
    from lynchpin.analysis.url_crossref import aggregate_by_url, iter_url_mentions
    src_set = {s.strip() for s in sources.split(',') if s.strip()}
    mentions = iter_url_mentions(start=date.fromisoformat(start), end=date.fromisoformat(end), sources=src_set)
    aggs = aggregate_by_url(mentions)
    filtered = [a for a in aggs if len(a.by_source) >= min_sources][:limit]
    return [{'url': a.url, 'domain': a.domain, 'total_mentions': a.total_mentions, 'by_source': a.by_source, 'by_role': a.by_role, 'first_seen': a.first_seen.isoformat() if a.first_seen else None, 'last_seen': a.last_seen.isoformat() if a.last_seen else None, 'sample_snippets': list(a.sample_snippets)} for a in filtered]
