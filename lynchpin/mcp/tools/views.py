"""View-backed MCP tools: project-day correlations, closure chains, overlaps, PR reviews.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from dataclasses import asdict
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools.substrate import _json_safe

def _dc_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass to a JSON-serialisable dict.

    Handles tuple → list, date/datetime → ISO string recursively.
    """
    from datetime import date, datetime

    def _conv(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, (list, tuple)):
            return [_conv(i) for i in v]
        if isinstance(v, dict):
            return {k: _conv(vv) for k, vv in v.items()}
        return v
    d = asdict(obj)
    return {k: _conv(v) for k, v in d.items()}

@app.tool()
def project_day_correlations(refresh_id: str | None=None, start: str | None=None, end: str | None=None, projects: list[str] | None=None, min_source_count: int | None=None) -> list[dict[str, Any]]:
    from datetime import date as _date
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_project_day_correlations
    start_d: _date | None = _date.fromisoformat(start) if start else None
    end_d: _date | None = _date.fromisoformat(end) if end else None
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    path = substrate_path()
    with connect(path) as conn:
        rows = load_project_day_correlations(conn, refresh_id=refresh_id, start=start_d, end=end_d, projects=projs, min_source_count=min_source_count)
    return [_dc_to_dict(row) for row in rows]

@app.tool()
def closure_chain_walks(refresh_id: str | None=None, project: str | None=None, min_chain_depth: int | None=None) -> list[dict[str, Any]]:
    """Query the issue_closure_chain_walk view.

    Wraps ``lynchpin.duck.reader.load_issue_closure_chain_walks``.

    Parameters:
        refresh_id:     filter to a specific evidence-graph build.
        project:        filter by project name.
        min_chain_depth: only return chains with depth >= N.

    Returns list of dicts with keys: refresh_id, root_id, project,
    issue_number, reachable_node_ids, chain_depth, reachable_count.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_issue_closure_chain_walks
    path = substrate_path()
    with connect(path) as conn:
        rows = load_issue_closure_chain_walks(conn, refresh_id=refresh_id, project=project, min_chain_depth=min_chain_depth)
    return [_dc_to_dict(row) for row in rows]

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
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import compute_file_overlap_edges
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
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import compute_symbol_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_symbol_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def pr_review_rows(projects: list[str] | None=None, states: list[str] | None=None, only_with_friction: bool=False, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Read the pr_review_row substrate table.

    Wraps ``lynchpin.duck.reader.load_pr_review_rows``.

    Parameters:
        projects:          filter by project name list; None = all.
        states:            filter by PR state, e.g. ["merged", "open"].
        only_with_friction: when True, only return PRs with friction signals.
        refresh_id:        filter to a specific promote batch.

    Returns list of dicts matching PrReviewRow fields: project, number,
    title, state, url, author, created_at, closed_at, merged_at,
    review_count, review_decisions, review_round_count, reviewer_count,
    reviewers, review_comment_count, top_level_comment_count,
    changes_requested_count, approval_count, dismissed_count,
    time_to_first_review_minutes, time_to_close_minutes,
    time_to_merge_minutes, final_decision, friction_signals.
    """
    from lynchpin.duck.connection import connect, substrate_path
    from lynchpin.duck.reader import load_pr_review_rows
    projs: tuple[str, ...] | None = tuple(projects) if projects else None
    sts: tuple[str, ...] | None = tuple(states) if states else None
    path = substrate_path()
    with connect(path) as conn:
        rows = load_pr_review_rows(conn, projects=projs, states=sts, only_with_friction=only_with_friction, refresh_id=refresh_id)
    return [_dc_to_dict(row) for row in rows]

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
    from lynchpin.duck.connection import connect, substrate_path
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
                a = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id = ?', [refresh_a]).fetchone()[0]
                b = conn.execute(f'SELECT COUNT(*) FROM {table} WHERE refresh_id = ?', [refresh_b]).fetchone()[0]
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
            st_rows = conn.execute('\n                SELECT COALESCE(a.source, b.source) AS source,\n                       a.status AS status_a, b.status AS status_b\n                FROM substrate_source_status a\n                FULL OUTER JOIN substrate_source_status b\n                  ON a.source = b.source AND a.refresh_id = ? AND b.refresh_id = ?\n                WHERE (a.refresh_id = ? OR a.refresh_id IS NULL)\n                  AND (b.refresh_id = ? OR b.refresh_id IS NULL)\n                ORDER BY source\n            ', [refresh_b, refresh_a, refresh_a, refresh_b]).fetchall()
            diffs['source_status_diffs'] = [{'source': r[0], 'status_a': r[1], 'status_b': r[2]} for r in st_rows]
        except Exception:
            pass
    return diffs

@app.tool()
def velocity_series(projects: list[str] | None=None, refresh_id: str | None=None, window_days: int=7) -> list[dict[str, Any]]:
    """Project velocity time-series with rolling windows (Arc D.4).

    SQL window functions over project_day_correlation. Returns daily commit
    counts with rolling average and cumulative count per project.

    Parameters:
        projects:     filter to specific projects; None = all.
        refresh_id:   snapshot to query; default = most recent promote.
        window_days:  rolling-average window size (default 7).

    Returns:
        [{"project": str, "date": "YYYY-MM-DD", "commit_count": int,
          "rolling_avg": float, "cumulative": int, "source_count": int}]
    """
    from lynchpin.duck.connection import connect, substrate_path
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            row = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
            if row is None:
                return []
            refresh_id = row[0]
    proj_filter = ''
    params: list[Any] = [refresh_id]
    if projects:
        placeholders = ','.join(['?'] * len(projects))
        proj_filter = f'AND project IN ({placeholders})'
        params.extend(projects)
    with connect(path, read_only=True) as conn:
        sql = f'\n            SELECT project, date, commit_count,\n                   ROUND(AVG(commit_count) OVER (\n                       PARTITION BY project ORDER BY date\n                       ROWS BETWEEN {int(window_days) - 1} PRECEDING AND CURRENT ROW\n                   ), 1) AS rolling_avg,\n                   SUM(commit_count) OVER (\n                       PARTITION BY project ORDER BY date\n                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW\n                   ) AS cumulative,\n                   source_count\n            FROM project_day_correlation\n            WHERE refresh_id = ? AND commit_count > 0 {proj_filter}\n            ORDER BY project, date\n        '
        rows = conn.execute(sql, params).fetchall()
    cols = ['project', 'date', 'commit_count', 'rolling_avg', 'cumulative', 'source_count']
    return [{c: _json_safe(v) for c, v in zip(cols, row)} for row in rows]

@app.tool()
def substrate_gap_draft() -> dict[str, Any]:
    """Read substrate_source_status and emit a tracker-issue draft (Arc E.3).

    Identifies sources that are 'unavailable' or 'error' in the latest refresh
    and produces a structured draft suitable for a GitHub issue. Never
    auto-creates issues — the output is text for the user to review.

    Returns:
        {
            "needs_attention": bool,
            "draft_title": str | None,
            "draft_body": str | None,
            "gaps": [{"source": str, "status": str, "reason": str}],
            "all_sources_healthy": bool,
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        latest = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
        if latest is None:
            return {'needs_attention': False, 'draft_title': None, 'draft_body': None, 'gaps': [], 'all_sources_healthy': True}
        refresh_id = latest[0]
        gaps = conn.execute("SELECT source, status, reason, row_count FROM substrate_source_status WHERE refresh_id = ? AND status IN ('unavailable', 'error') ORDER BY source", [refresh_id]).fetchall()
        gap_list = [{'source': r[0], 'status': r[1], 'reason': r[2], 'row_count': r[3]} for r in gaps]
        if not gap_list:
            return {'needs_attention': False, 'draft_title': None, 'draft_body': None, 'gaps': [], 'all_sources_healthy': True}
        draft_title = f'substrate: {len(gap_list)} source(s) unavailable or errored'
        lines = [f'## Substrate gap report — {refresh_id}', '', f'{len(gap_list)} source(s) need attention:', '']
        for g in gap_list:
            lines.append(f"- **{g['source']}** — `{g['status']}`" + (f": {g['reason']}" if g['reason'] else ''))
        lines.extend(['', '### Action', '', 'Run `substrate_readiness_report` to review, then:'])
        for g in gap_list:
            if g['source'] == 'pr_review':
                lines.append('- `pr_review`: run `pr_review_topology` to generate `active_pr_review_topology.json`.')
            elif g['source'] == 'symbols':
                lines.append('- `symbols`: install tree-sitter grammars in the nix environment, then re-run `active_symbol_changes`.')
            else:
                lines.append(f"- `{g['source']}`: investigate — `{g.get('reason', 'unknown')}`")
        return {'needs_attention': True, 'draft_title': draft_title, 'draft_body': '\n'.join(lines), 'gaps': gap_list, 'all_sources_healthy': False}

@app.tool()
def substrate_confidence_matrix(refresh_id: str | None=None) -> dict[str, Any]:
    """Per-dimension confidence scores for the substrate (Arc M.17).

    Aggregates across evidence_node metadata, substrate_source_status,
    and evidence_graph_build caveats to produce a per-source confidence
    score based on row count, source freshness, cross-source agreement,
    and caveat count.

    Parameters:
        refresh_id: snapshot to assess; default = latest promote.

    Returns:
        {
            "refresh_id": str,
            "dimensions": [
                {"source": str, "node_count": int, "project_count": int,
                 "date_span_days": int, "has_caveats": bool,
                 "status": "ok|empty|unavailable|error"},
            ],
            "summary": {"total_nodes": int, "source_count": int,
                        "healthy_source_count": int, "confidence_pct": float},
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            row = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
            if row is None:
                return {'error': 'no promote runs'}
            refresh_id = row[0]
        dimensions = []
        for row in conn.execute("\n            SELECT\n                source,\n                COUNT(*) AS node_count,\n                COUNT(DISTINCT project) AS project_count,\n                COALESCE(DATE_DIFF('day', MIN(date), MAX(date)), 0) AS date_span_days,\n                COALESCE(SUM(CASE WHEN json_array_length(caveats) > 0 THEN 1 ELSE 0 END), 0) > 0 AS has_caveats\n            FROM evidence_node\n            WHERE refresh_id = ?\n            GROUP BY source\n            ORDER BY node_count DESC\n        ", [refresh_id]).fetchall():
            dimensions.append({'source': row[0], 'node_count': row[1], 'project_count': row[2], 'date_span_days': row[3], 'has_caveats': bool(row[4])})
        status_map = {}
        for srow in conn.execute('SELECT source, status FROM substrate_source_status WHERE refresh_id = ?', [refresh_id]).fetchall():
            status_map[srow[0]] = srow[1]
        for dim in dimensions:
            dim['status'] = status_map.get(dim['source'], 'unknown')
        total_nodes = sum((d['node_count'] for d in dimensions))
        healthy = sum((1 for d in dimensions if d['status'] == 'ok'))
        confidence = healthy / max(len(dimensions), 1) * 100
    return {'refresh_id': refresh_id, 'dimensions': dimensions, 'summary': {'total_nodes': total_nodes, 'source_count': len(dimensions), 'healthy_source_count': healthy, 'confidence_pct': round(confidence, 1)}}

@app.tool()
def kind_audit(refresh_id: str | None=None) -> dict[str, Any]:
    """Polylogue-vs-Lynchpin kind audit (Arc K.1).

    Reads ai_work_event.kind_* columns to surface agreement rates,
    disagreement cases, tier distributions, and per-kind confidence
    breakdowns. This is the quantitative foundation for the boundary doc
    (K.4) — how often does the lynchpin overlay disagree with polylogue's
    raw classification?

    Parameters:
        refresh_id: promote snapshot (default: latest).

    Returns:
        {
            "refresh_id": str,
            "total": int,
            "tier_distribution": {"high": N, "medium": N, "low": N},
            "source_distribution": {"agreement": N, "disagreement": N,
                                     "polylogue": N, "lynchpin_overlay": N},
            "disagreement_rate": float,
            "top_disagreements": [{"kind": str, "polylogue_kind": str,
                                    "overlay_kind": str, "count": int}],
            "per_kind_confidence": [{"kind": str, "count": int,
                                      "avg_confidence": float}],
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            row = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
            if row is None:
                return {'error': 'no promote runs'}
            refresh_id = row[0]
        total = conn.execute('SELECT COUNT(*) FROM ai_work_event WHERE refresh_id = ?', [refresh_id]).fetchone()[0]
        tiers = {}
        for r in conn.execute('SELECT kind_tier, COUNT(*) FROM ai_work_event WHERE refresh_id = ? GROUP BY kind_tier', [refresh_id]).fetchall():
            tiers[r[0] or 'null'] = r[1]
        sources = {}
        for r in conn.execute('SELECT kind_source, COUNT(*) FROM ai_work_event WHERE refresh_id = ? GROUP BY kind_source', [refresh_id]).fetchall():
            sources[r[0] or 'null'] = r[1]
        disagreements = conn.execute('\n            SELECT kind, polylogue_kind, overlay_kind, COUNT(*) AS cnt\n            FROM ai_work_event\n            WHERE refresh_id = ?\n              AND polylogue_kind IS NOT NULL\n              AND overlay_kind IS NOT NULL\n              AND polylogue_kind != overlay_kind\n            GROUP BY kind, polylogue_kind, overlay_kind\n            ORDER BY cnt DESC LIMIT 10\n        ', [refresh_id]).fetchall()
        per_kind = conn.execute('\n            SELECT kind, COUNT(*) AS cnt,\n                   ROUND(AVG(kind_confidence), 2) AS avg_conf\n            FROM ai_work_event\n            WHERE refresh_id = ? AND kind IS NOT NULL\n            GROUP BY kind ORDER BY cnt DESC LIMIT 20\n        ', [refresh_id]).fetchall()
    disagree_count = sources.get('disagreement', 0)
    return {'refresh_id': refresh_id, 'total': total, 'tier_distribution': tiers, 'source_distribution': sources, 'disagreement_rate': round(disagree_count / max(total, 1), 3), 'top_disagreements': [{'kind': r[0], 'polylogue_kind': r[1], 'overlay_kind': r[2], 'count': r[3]} for r in disagreements], 'per_kind_confidence': [{'kind': r[0], 'count': r[1], 'avg_confidence': r[2]} for r in per_kind]}

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
    from lynchpin.duck.connection import connect, substrate_path
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            row = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
            if row is None:
                return []
            refresh_id = row[0]
        rows = conn.execute('\n            SELECT ns.project AS proj_a, nt.project AS proj_b,\n                   COUNT(*) AS edge_count\n            FROM evidence_edge e\n            JOIN evidence_node ns\n              ON ns.id = e.source_id AND ns.refresh_id = e.refresh_id\n            JOIN evidence_node nt\n              ON nt.id = e.target_id AND nt.refresh_id = e.refresh_id\n            WHERE ns.project IS NOT NULL\n              AND nt.project IS NOT NULL\n              AND ns.project != nt.project\n              AND e.refresh_id = ?\n            GROUP BY ns.project, nt.project\n            HAVING COUNT(*) >= ?\n            ORDER BY edge_count DESC\n        ', [refresh_id, int(min_edge_count)]).fetchall()
    return [{'source_project': r[0], 'target_project': r[1], 'edge_count': r[2]} for r in rows]

@app.tool()
def velocity_narrative(projects: list[str] | None=None, refresh_id: str | None=None) -> dict[str, Any]:
    """Auto-summary of project velocity over the latest refresh window (Arc M.6).

    Aggregates project_day_correlation into a narrative summary: total
    commits, active days, peak day, per-project breakdown, and the
    dominant project. Renders as structured text suitable for inclusion
    in a context pack or seed note.

    Parameters:
        projects:   filter to specific projects; None = top 8 by commits.
        refresh_id: snapshot (default: latest).

    Returns:
        {
            "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
            "total_commits": int,
            "total_active_days": int,
            "peak": {"project": str, "date": "YYYY-MM-DD", "commits": int},
            "projects": [{"project": str, "commits": int, "active_days": int}],
            "summary_text": str,
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            row = conn.execute('SELECT refresh_id FROM substrate_source_status ORDER BY recorded_at DESC LIMIT 1').fetchone()
            if row is None:
                return {'error': 'no promote runs'}
            refresh_id = row[0]
        win = conn.execute('\n            SELECT MIN(date), MAX(date)\n            FROM project_day_correlation WHERE refresh_id = ?\n        ', [refresh_id]).fetchone()
        proj_filter = ''
        params: list[Any] = [refresh_id]
        if projects:
            placeholders = ','.join(['?'] * len(projects))
            proj_filter = f'AND project IN ({placeholders})'
            params.extend(projects)
        proj_rows = conn.execute(f'\n            SELECT project,\n                   SUM(commit_count) AS commits,\n                   COUNT(*) AS active_days,\n                   ROUND(AVG(commit_count), 1) AS avg_daily\n            FROM project_day_correlation\n            WHERE refresh_id = ? AND commit_count > 0 {proj_filter}\n            GROUP BY project ORDER BY commits DESC\n        ', params).fetchall()
        peak = conn.execute(f'\n            SELECT project, date, commit_count\n            FROM project_day_correlation\n            WHERE refresh_id = ? {proj_filter}\n            ORDER BY commit_count DESC LIMIT 1\n        ', params).fetchone()
        total_commits = sum((r[1] for r in proj_rows))
        total_days = sum((r[2] for r in proj_rows))
        projects_list = [{'project': r[0], 'commits': r[1], 'active_days': r[2], 'avg_daily': r[3]} for r in proj_rows]
        if proj_rows:
            top = proj_rows[0]
            lines = [f'In the window {win[0]} → {win[1]}: {total_commits} commits across {len(proj_rows)} projects ({total_days} active project-days).', '', f'**{top[0]}** led with {top[1]} commits over {top[2]} active days (avg {top[3]}/day).']
            if peak:
                lines.append(f'Peak day: **{peak[0]}** on {peak[1]} ({peak[2]} commits).')
            if len(proj_rows) > 1:
                rest = [f'**{p[0]}** ({p[1]} commits)' for p in proj_rows[1:4]]
                lines.append(f"Also active: {', '.join(rest)}.")
            if len(proj_rows) > 4:
                lines.append(f'(+{len(proj_rows) - 4} more projects with lower activity)')
            summary = '\n'.join(lines)
        else:
            summary = 'No project activity in this window.'
    return {'window': {'start': _json_safe(win[0]), 'end': _json_safe(win[1])}, 'total_commits': total_commits, 'total_active_days': total_days, 'peak': {'project': peak[0], 'date': _json_safe(peak[1]), 'commits': peak[2]} if peak else None, 'projects': projects_list, 'summary_text': summary}
