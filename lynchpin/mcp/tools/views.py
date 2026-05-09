"""View-backed MCP tools: project-day correlations, closure chains, overlaps, PR reviews.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from dataclasses import asdict
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools.substrate import _json_safe, _latest_refresh_id

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
            st_rows = conn.execute('\n                WITH a AS (\n                    SELECT source, status FROM substrate_source_status\n                    WHERE refresh_id = ?\n                ),\n                b AS (\n                    SELECT source, status FROM substrate_source_status\n                    WHERE refresh_id = ?\n                )\n                SELECT COALESCE(a.source, b.source) AS source,\n                       a.status AS status_a, b.status AS status_b\n                FROM a\n                FULL OUTER JOIN b ON a.source = b.source\n                ORDER BY source\n            ', [refresh_a, refresh_b]).fetchall()
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
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
        refresh_id = _latest_refresh_id(conn)
        if refresh_id is None:
            return {'needs_attention': False, 'draft_title': None, 'draft_body': None, 'gaps': [], 'all_sources_healthy': True}
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'error': 'no promote runs'}
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'error': 'no promote runs'}
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'error': 'no promote runs'}
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

@app.tool()
def work_package_durability(refresh_id: str | None=None, min_symbols: int=10) -> dict[str, Any]:
    """Symbol survival at HEAD — which work packages persist? (Arc D.3)

    Self-joins symbol_change on qualified_name: symbols whose latest
    change_type is not a deletion are considered surviving. Groups by
    project + date to compute per-day survival rates.

    Parameters:
        refresh_id:  snapshot (default: latest).
        min_symbols: minimum symbol count per project-day to include.

    Returns:
        {
            "refresh_id": str,
            "total_symbols": int,
            "surviving": int,
            "overall_survival_rate": float,
            "per_project_day": [
                {"project": str, "date": str, "total": int,
                 "surviving": int, "rate": float}
            ],
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'error': 'no promote runs'}
        total = conn.execute('SELECT COUNT(*) FROM symbol_change WHERE refresh_id = ?', [refresh_id]).fetchone()[0]
        rows = conn.execute("\n            WITH ranked AS (\n                SELECT project, date, qualified_name, change_type,\n                       ROW_NUMBER() OVER (\n                           PARTITION BY qualified_name\n                           ORDER BY date DESC, sha DESC\n                       ) AS rn\n                FROM symbol_change\n                WHERE refresh_id = ?\n            ),\n            latest AS (\n                SELECT project, date, qualified_name, change_type\n                FROM ranked WHERE rn = 1\n            )\n            SELECT project, date,\n                   COUNT(*) AS total_syms,\n                   SUM(CASE WHEN change_type != 'D' THEN 1 ELSE 0 END) AS surviving\n            FROM latest\n            GROUP BY project, date\n            HAVING COUNT(*) >= ?\n            ORDER BY date, project\n        ", [refresh_id, int(min_symbols)]).fetchall()
        surviving_total = sum((r[3] for r in rows))
        per_day = [{'project': r[0], 'date': _json_safe(r[1]), 'total': r[2], 'surviving': r[3], 'rate': round(r[3] / max(r[2], 1), 3)} for r in rows]
    return {'refresh_id': refresh_id, 'total_symbols': total, 'surviving': surviving_total, 'overall_survival_rate': round(surviving_total / max(total, 1), 3), 'per_project_day': per_day}

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
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
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

@app.tool()
def calendar_events(start: str | None=None, end: str | None=None, calendar: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Calendar events from the substrate (Arc M.12).

    Degraded-mode contract: returns [] when no calendar data has been
    ingested upstream. Queries the calendar_event table.

    Parameters:
        start:    ISO date filter (YYYY-MM-DD).
        end:      ISO date filter (YYYY-MM-DD).
        calendar: filter by calendar name.
        refresh_id: snapshot (default: latest).

    Returns:
        [{"uid", "calendar", "summary", "start_at", "end_at", "all_day",
          "location", "attendees", "description", "status"}]
    """
    from datetime import date as _date
    from lynchpin.duck.connection import connect, substrate_path
    sql = 'SELECT uid, calendar, summary, start_at, end_at, all_day, location, attendees, description, status FROM calendar_event WHERE 1=1'
    params: list[Any] = []
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        sql += ' AND refresh_id = ?'
        params.append(refresh_id)
        if start:
            sql += ' AND start_at >= ?'
            params.append(_date.fromisoformat(start))
        if end:
            sql += ' AND start_at <= ?'
            params.append(_date.fromisoformat(end))
        if calendar:
            sql += ' AND calendar = ?'
            params.append(calendar)
        sql += ' ORDER BY start_at'
        rows = conn.execute(sql, params).fetchall()
    return [{'uid': r[0], 'calendar': r[1], 'summary': r[2], 'start_at': _json_safe(r[3]), 'end_at': _json_safe(r[4]), 'all_day': r[5], 'location': r[6], 'attendees': r[7], 'description': r[8], 'status': r[9]} for r in rows]

@app.tool()
def refactor_candidates(project: str | None=None, refresh_id: str | None=None, min_similarity: float=0.6) -> list[dict[str, Any]]:
    """Detect refactor candidates via symbol renaming patterns (Arc M.4).

    Finds symbol_change rows with change_type='RENAMED' or symbols that
    were deleted then added with a similar name in a nearby time window.
    Groups by project and ranks by symbol count.

    Parameters:
        project:        filter to one project; None = all.
        refresh_id:     snapshot (default: latest).
        min_similarity: prefix-match ratio threshold (0.0-1.0).

    Returns:
        [{"project": str, "old_name": str, "new_name": str,
          "similarity": float, "date": str, "sha": str}]
    """
    from difflib import SequenceMatcher
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        renamed = conn.execute(f"\n            SELECT project, qualified_name, date, sha, path\n            FROM symbol_change\n            WHERE refresh_id = ? AND change_type = 'R' {proj_filter}\n            ORDER BY date\n        ", params).fetchall()
        pairs = conn.execute(f"\n            WITH added AS (\n                SELECT project, qualified_name, date, sha\n                FROM symbol_change\n                WHERE refresh_id = ? AND change_type = 'A' {proj_filter}\n            ),\n            deleted AS (\n                SELECT project, qualified_name, date, sha\n                FROM symbol_change\n                WHERE refresh_id = ? AND change_type = 'D' {proj_filter}\n            )\n            SELECT a.project, d.qualified_name AS old_name,\n                   a.qualified_name AS new_name,\n                   a.date, a.sha\n            FROM added a\n            JOIN deleted d ON a.project = d.project\n            WHERE a.date >= d.date\n            ORDER BY a.date\n            LIMIT 500\n        ", [refresh_id] + ([project] if project else []) + [refresh_id] + ([project] if project else [])).fetchall()
    params.clear()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for proj, name, d, sha, path in renamed:
        candidates.append({'project': proj, 'old_name': name, 'new_name': name, 'similarity': 1.0, 'date': _json_safe(d), 'sha': sha[:8], 'source': 'explicit_rename'})
    for proj, old, new, d, sha in pairs:
        if old == new:
            continue
        key = (old, new)
        if key in seen:
            continue
        sim = SequenceMatcher(None, old, new).ratio()
        if sim < min_similarity:
            continue
        seen.add(key)
        candidates.append({'project': proj, 'old_name': old, 'new_name': new, 'similarity': round(sim, 3), 'date': _json_safe(d), 'sha': sha[:8], 'source': 'similarity_match'})
    candidates.sort(key=lambda c: -c['similarity'])
    return candidates[:50]

@app.tool()
def symbol_velocity(projects: list[str] | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Symbol-level churn per project per day (Phase B.1).

    Extends commit-count velocity with symbol-change dimensions: added,
    deleted, modified, renamed per project-day. Joins symbol_change to
    project_day_correlation for a unified velocity surface.

    Parameters:
        projects:   filter to specific projects; None = all.
        refresh_id: snapshot (default: latest).

    Returns:
        [{"project": str, "date": str, "commit_count": int,
          "symbols_added": int, "symbols_modified": int,
          "symbols_renamed": int, "symbols_total": int}]
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        outer_filter = ''
        inner_filter = ''
        params: list[Any] = [refresh_id, refresh_id]
        if projects:
            placeholders = ','.join(['?'] * len(projects))
            outer_filter = f'AND p.project IN ({placeholders})'
            inner_filter = f'AND project IN ({placeholders})'
            params = [refresh_id, *projects, refresh_id, *projects]
        rows = conn.execute(f"\n            SELECT COALESCE(p.project, s.project) AS project,\n                   COALESCE(p.date, s.date) AS date,\n                   COALESCE(p.commit_count, 0) AS commit_count,\n                   COALESCE(sym.added, 0) AS symbols_added,\n                   COALESCE(sym.modified, 0) AS symbols_modified,\n                   COALESCE(sym.renamed, 0) AS symbols_renamed,\n                   COALESCE(sym.total, 0) AS symbols_total\n            FROM project_day_correlation p\n            FULL OUTER JOIN (\n                SELECT project, date,\n                       SUM(CASE WHEN change_type = 'A' THEN 1 ELSE 0 END) AS added,\n                       SUM(CASE WHEN change_type = 'M' THEN 1 ELSE 0 END) AS modified,\n                       SUM(CASE WHEN change_type = 'R' THEN 1 ELSE 0 END) AS renamed,\n                       COUNT(*) AS total\n                FROM symbol_change\n                WHERE refresh_id = ? {inner_filter}\n                GROUP BY project, date\n            ) sym ON p.project = sym.project AND p.date = sym.date\n               AND p.refresh_id = ?\n            {outer_filter}\n            ORDER BY project, date\n        ", params).fetchall()
    return [{'project': r[0], 'date': _json_safe(r[1]), 'commit_count': r[2], 'symbols_added': r[3], 'symbols_modified': r[4], 'symbols_renamed': r[5], 'symbols_total': r[6]} for r in rows]

@app.tool()
def file_hotspots(top_n: int=20, project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Churn hotspots — most frequently changed files/directories (Phase B.2).

    Aggregates file_change_fact by path_root over the refresh window.
    Surfaces fragile modules that change disproportionately often.

    Parameters:
        top_n:      number of hotspots to return.
        project:    filter to one project; None = all.
        refresh_id: snapshot (default: latest).

    Returns:
        [{"path_root": str, "commits": int, "file_changes": int,
          "project_count": int, "top_project": str}]
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        params.append(int(top_n))
        rows = conn.execute(f"\n            SELECT path_root,\n                   COUNT(DISTINCT sha) AS commits,\n                   COUNT(*) AS file_changes,\n                   COUNT(DISTINCT project) AS project_count,\n                   MODE(project) AS top_project\n            FROM file_change_fact\n            WHERE refresh_id = ? AND path_root IS NOT NULL\n              AND path_root != '' {proj_filter}\n            GROUP BY path_root\n            ORDER BY commits DESC\n            LIMIT ?\n        ", params).fetchall()
    return [{'path_root': r[0], 'commits': r[1], 'file_changes': r[2], 'project_count': r[3], 'top_project': r[4]} for r in rows]

@app.tool()
def temporal_rhythm(project: str | None=None, refresh_id: str | None=None) -> dict[str, Any]:
    """Commit time-of-day × day-of-week patterns per project (Phase B.3).

    Groups commit_fact by hour and weekday to surface work rhythms:
    morning vs night coding, weekend vs weekday sprints.

    Parameters:
        project:    filter to one project; None = all.
        refresh_id: snapshot (default: latest).

    Returns:
        {
            "hourly": [{"hour": 0-23, "count": int}],
            "weekday": [{"weekday": 0-6, "name": "Mon", "count": int}],
            "peak_hour": int, "peak_weekday": str,
        }
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'hourly': [], 'weekday': [], 'peak_hour': None, 'peak_weekday': None}
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        hourly = conn.execute(f'\n            SELECT EXTRACT(HOUR FROM authored_at)::INTEGER AS hr,\n                   COUNT(*) AS cnt\n            FROM commit_fact\n            WHERE refresh_id = ? {proj_filter}\n            GROUP BY hr ORDER BY hr\n        ', params).fetchall()
        weekday_names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        weekday = conn.execute(f'\n            SELECT EXTRACT(DOW FROM authored_at)::INTEGER AS dow,\n                   COUNT(*) AS cnt\n            FROM commit_fact\n            WHERE refresh_id = ? {proj_filter}\n            GROUP BY dow ORDER BY dow\n        ', params).fetchall()
    peak_hour = max(hourly, key=lambda r: r[1])[0] if hourly else None
    peak_dow = max(weekday, key=lambda r: r[1]) if weekday else None
    return {'hourly': [{'hour': r[0], 'count': r[1]} for r in hourly], 'weekday': [{'weekday': r[0], 'name': weekday_names[r[0]], 'count': r[1]} for r in weekday], 'peak_hour': peak_hour, 'peak_weekday': weekday_names[peak_dow[0]] if peak_dow else None}

@app.tool()
def evidence_confidence(refresh_id: str | None=None) -> list[dict[str, Any]]:
    from lynchpin.duck.connection import connect, substrate_path
    RELIABILITY = {'git': 'high', 'polylogue': 'medium', 'terminal': 'medium', 'activitywatch': 'medium', 'github': 'medium', 'github_ref': 'medium', 'raw_log': 'low', 'analysis': 'low', 'calendar': 'medium'}
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT source,\n                   COUNT(*) AS node_count,\n                   ROUND(SUM(CASE WHEN json_array_length(caveats) > 0 THEN 1 ELSE 0 END)\n                         * 100.0 / COUNT(*), 1) AS caveated_pct\n            FROM evidence_node\n            WHERE refresh_id = ?\n            GROUP BY source\n            ORDER BY node_count DESC\n        ', [refresh_id]).fetchall()
    results = []
    for r in rows:
        base = RELIABILITY.get(r[0], 'low')
        caveat_pct = r[2] or 0.0
        if caveat_pct > 20 and base == 'high':
            tier = 'medium'
        elif caveat_pct > 20 and base == 'medium':
            tier = 'low'
        else:
            tier = base
        results.append({'source': r[0], 'node_count': r[1], 'caveated_pct': caveat_pct, 'reliability_base': base, 'confidence_tier': tier})
    return results

@app.tool()
def source_anomalies(refresh_id: str | None=None, threshold_sigma: float=2.0) -> list[dict[str, Any]]:
    """Cross-source anomaly detection per project-day (Phase B.5).

    Flags project-days where one evidence dimension is anomalously
    high or low relative to others:
    - commits without AI sessions (manual/unassisted work)
    - AI sessions without commits (exploration that didn't ship)
    - focus without git activity (reading/debugging)
    - git activity without focus (automated/CI commits)

    Parameters:
        refresh_id:       snapshot (default: latest).
        threshold_sigma:  z-score threshold for flagging (default 2.0).

    Returns:
        [{"project": str, "date": str, "anomaly_type": str,
          "commit_count": int, "ai_count": int, "focus_min": float,
          "source_count": int, "detail": str}]
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT project, date, commit_count, ai_work_event_count,\n                   focus_seconds, source_count\n            FROM project_day_correlation\n            WHERE refresh_id = ? AND source_count >= 3\n        ', [refresh_id]).fetchall()
    if not rows:
        return []
    from collections import defaultdict
    proj_commits: dict[str, list[int]] = defaultdict(list)
    proj_ai: dict[str, list[int]] = defaultdict(list)
    proj_focus: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        p = r[0]
        proj_commits[p].append(r[2])
        proj_ai[p].append(r[3])
        if r[4] is not None:
            proj_focus[p].append(r[4])

    def _z(val: float, vals: list[float]) -> float:
        if len(vals) < 2:
            return 0.0
        mean = sum(vals) / len(vals)
        std = (sum(((v - mean) ** 2 for v in vals)) / len(vals)) ** 0.5
        return abs((val - mean) / std) if std > 0 else 0.0
    anomalies = []
    for r in rows:
        proj, d, commits, ai, focus_sec, src = r
        c_vals = proj_commits.get(proj, [])
        a_vals = proj_ai.get(proj, [])
        f_vals = proj_focus.get(proj, [])
        if commits > 0 and ai == 0 and c_vals and (_z(commits, c_vals) > threshold_sigma):
            anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'commits_without_ai', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'detail': f'{commits} commits with 0 AI sessions — manual/unassisted work'})
        if ai > 0 and commits == 0 and a_vals and (_z(ai, a_vals) > threshold_sigma):
            anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'ai_without_commits', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'detail': f"{ai} AI work events with 0 commits — exploration that didn't ship"})
        if (focus_sec or 0) > 0 and commits == 0 and f_vals:
            fz = _z(focus_sec or 0, f_vals)
            if fz > threshold_sigma:
                anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'focus_without_git', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'detail': f'{round((focus_sec or 0) / 60, 1)} focus-minutes with 0 commits — reading/debugging'})
    anomalies.sort(key=lambda a: (a['project'], a['date']))
    return anomalies

@app.tool()
def promote_analysis_product(title: str, path: str, refresh_id: str | None=None, dry_run: bool=False) -> dict[str, Any]:
    """Register an analysis product as an evidence node (Phase C.1).

    Context packs, current-state reports, and narrative outputs are
    themselves evidence. This tool promotes them into the evidence_node
    table so they can be queried alongside source data.

    Parameters:
        title:      display title.
        path:       file path to product.
        refresh_id: snapshot to anchor to (default: latest).
        dry_run:    preview without writing.

    Returns:
        {"promoted": bool, "node_id": str, "dry_run": bool}
    """
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from lynchpin.duck.connection import connect, substrate_path, apply_schema
    now = _dt.now(_tz.utc)
    node_id = f"analysis_product:{title.replace(' ', '_')}:{now.strftime('%Y%m%d')}"
    if dry_run:
        return {'promoted': False, 'node_id': node_id, 'dry_run': True}
    with connect(substrate_path(), read_only=False) as conn:
        apply_schema(conn)
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'promoted': False, 'node_id': node_id, 'error': 'no promote runs'}
        conn.execute('DELETE FROM evidence_node WHERE refresh_id = ? AND id = ?', [refresh_id, node_id])
        conn.execute("\n            INSERT INTO evidence_node (\n                refresh_id, id, kind, source, date, project,\n                summary, start_ts, end_ts, url, payload, provenance, caveats\n            ) VALUES (?, ?, 'analysis_product', 'analysis',\n                      CURRENT_DATE, NULL, ?, ?, ?, NULL, ?, NULL, '[]')\n        ", [refresh_id, node_id, title, now, now, _json.dumps({'title': title, 'path': path, 'generated_at': now.isoformat()})])
    return {'promoted': True, 'node_id': node_id, 'dry_run': False}

@app.tool()
def health_trend(refresh_id: str | None=None, alert_threshold_pct: float=10.0) -> dict[str, Any]:
    """Substrate health trend across refresh snapshots (Phase C.2).

    Compares latest refresh with prior one; emits alert when confidence
    drops > alert_threshold_pct. Builds on substrate_source_status.

    Parameters:
        refresh_id:         latest snapshot (default: most recent).
        alert_threshold_pct: drop percentage that triggers alert.

    Returns:
        {"current": str, "prior": str, "confidence_current": float,
         "confidence_prior": float, "delta": float, "alert": bool,
         "gaps_current": int, "gaps_prior": int}
    """
    from lynchpin.duck.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        refresh_ids = [r[0] for r in conn.execute('SELECT DISTINCT refresh_id FROM substrate_source_status ORDER BY recorded_at').fetchall()]
    if len(refresh_ids) < 2:
        return {'alert': False, 'detail': 'need 2+ refresh snapshots for trend', 'current': refresh_ids[-1] if refresh_ids else None, 'prior': None}
    current = refresh_id or refresh_ids[-1]
    prior = refresh_ids[-2]
    with connect(substrate_path(), read_only=True) as conn:

        def _conf(rid):
            rows = conn.execute('SELECT status, COUNT(*) FROM substrate_source_status WHERE refresh_id = ? GROUP BY status', [rid]).fetchall()
            statuses = {r[0]: r[1] for r in rows}
            total = sum(statuses.values())
            ok_count = statuses.get('ok', 0)
            gaps = statuses.get('unavailable', 0) + statuses.get('error', 0)
            return (ok_count / max(total, 1) * 100, gaps)
        conf_curr, gaps_curr = _conf(current)
        conf_prior, gaps_prior = _conf(prior)
        delta = conf_curr - conf_prior
        alert = delta < -alert_threshold_pct
    return {'current': current, 'prior': prior, 'confidence_current': round(conf_curr, 1), 'confidence_prior': round(conf_prior, 1), 'delta': round(delta, 1), 'alert': alert, 'gaps_current': gaps_curr, 'gaps_prior': gaps_prior}
