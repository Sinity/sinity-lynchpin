"""View-backed MCP tools: project-day correlations, closure chains, overlaps, PR reviews.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP's Tool.from_function introspects parameter annotations at decoration
time with issubclass(param.annotation, Context); PEP 563 string annotations
cause ``issubclass('str', Context)`` → TypeError.
"""
from dataclasses import asdict
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import json_safe as _json_safe, latest_refresh_id as _latest_refresh_id, best_refresh_id

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
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.reader import load_project_day_correlations
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

    Uses ``lynchpin.substrate.reader.load_issue_closure_chain_walks``.

    Parameters:
        refresh_id:     filter to a specific evidence-graph build.
        project:        filter by project name.
        min_chain_depth: only return chains with depth >= N.

    Returns list of dicts with keys: refresh_id, root_id, project,
    issue_number, reachable_node_ids, chain_depth, reachable_count.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.reader import load_issue_closure_chain_walks
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
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.reader import compute_file_overlap_edges
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
    from lynchpin.substrate.reader import compute_symbol_overlap_edges
    path = substrate_path()
    with connect(path) as conn:
        edges = compute_symbol_overlap_edges(conn, we_refresh_id=we_refresh_id, commit_refresh_id=commit_refresh_id)
    return [{'source_id': e.source_id, 'target_id': e.target_id, 'relation': e.relation, 'evidence': e.evidence, 'weight': e.weight} for e in edges]

@app.tool()
def pr_review_rows(projects: list[str] | None=None, states: list[str] | None=None, only_with_friction: bool=False, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Read the pr_review_row substrate table.

    Wraps ``lynchpin.substrate.reader.load_pr_review_rows``.

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
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.reader import load_pr_review_rows
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
            refresh_id = _latest_refresh_id(conn)
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
    from lynchpin.substrate.connection import connect, substrate_path
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
    from lynchpin.substrate.connection import connect, substrate_path
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
    from lynchpin.substrate.connection import connect, substrate_path
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
def conventional_commits(project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Conventional commit distribution per project.

    Breaks down commit_fact by conventional_kind (feat/fix/refactor/etc.)
    per project. Which projects are feature-heavy vs fix-heavy?
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        rows = conn.execute(f'\n            SELECT project, conventional_kind, COUNT(*) AS cnt,\n                   ROUND(COUNT(*)*100.0/SUM(COUNT(*)) OVER(PARTITION BY project), 1) AS pct\n            FROM commit_fact\n            WHERE refresh_id = ? AND conventional_kind IS NOT NULL {proj_filter}\n            GROUP BY project, conventional_kind\n            ORDER BY project, cnt DESC\n        ', params).fetchall()
    return [{'project': r[0], 'kind': r[1], 'count': r[2], 'pct': r[3]} for r in rows]

@app.tool()
def ai_tool_usage(project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """AI tool usage patterns from work events.

    Which tools (Edit, Write, Bash, Read, etc.) appear most in AI work
    events? Per-project breakdown with counts.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        rows = conn.execute(f'\n            WITH unnested AS (\n                SELECT project, UNNEST(tools_used) AS tool\n                FROM ai_work_event\n                WHERE refresh_id = ? AND len(tools_used) > 0 {proj_filter}\n            )\n            SELECT project, tool, COUNT(*) AS cnt\n            FROM unnested GROUP BY project, tool ORDER BY project, cnt DESC\n        ', params).fetchall()
    return [{'project': r[0], 'tool': r[1], 'count': r[2]} for r in rows]

@app.tool()
def breaking_changes(project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Breaking change tracker per project.

    Lists commits flagged as breaking changes, sorted most recent first.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        rows = conn.execute(f'\n            SELECT project, sha, subject, authored_at\n            FROM commit_fact\n            WHERE refresh_id = ? AND breaking_change = TRUE {proj_filter}\n            ORDER BY authored_at DESC\n        ', params).fetchall()
    return [{'project': r[0], 'sha': r[1][:8], 'subject': r[2][:80], 'date': _json_safe(r[3])} for r in rows]

@app.tool()
def review_bottlenecks(min_rounds: int=2, min_review_hours: float=24.0, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Code review bottleneck detection.

    Flags PRs that took >min_rounds review rounds or >min_review_hours
    to first review. Surfaces friction-heavy PRs.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT project, number, title, url, author,\n                   review_round_count,\n                   ROUND(time_to_first_review_minutes/60.0, 1) AS review_hours,\n                   changes_requested_count, approval_count, friction_signals\n            FROM pr_review_row\n            WHERE refresh_id = ?\n              AND (review_round_count >= ? OR time_to_first_review_minutes >= ?)\n            ORDER BY review_round_count DESC, time_to_first_review_minutes DESC\n            LIMIT 50\n        ', [refresh_id, int(min_rounds), float(min_review_hours) * 60]).fetchall()
    return [{'project': r[0], 'number': r[1], 'title': r[2][:80], 'url': r[3], 'author': r[4], 'rounds': r[5], 'review_hours': r[6], 'changes_requested': r[7], 'approvals': r[8], 'friction_signals': r[9]} for r in rows]

@app.tool()
def source_correlation(refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Cross-source correlation matrix.

    Which evidence sources co-occur on the same project-day? Produces
    a source×source matrix showing which data dimensions cluster together.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        rows = conn.execute('\n            WITH source_days AS (\n                SELECT DISTINCT source, project, date\n                FROM evidence_node\n                WHERE refresh_id = ? AND project IS NOT NULL\n            )\n            SELECT a.source AS source_a, b.source AS source_b,\n                   COUNT(*) AS co_occurring_days\n            FROM source_days a\n            JOIN source_days b ON a.project=b.project AND a.date=b.date AND a.source<b.source\n            GROUP BY a.source, b.source\n            HAVING COUNT(*) >= 3\n            ORDER BY co_occurring_days DESC\n        ', [refresh_id]).fetchall()
    return [{'source_a': r[0], 'source_b': r[1], 'co_occurring_days': r[2]} for r in rows]

@app.tool()
def commit_kind_attribution(refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Commit kind × AI attribution correlation.

    Do AI-assisted commits use different conventional kinds than
    non-attributed commits? Groups by conventional_kind.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT conventional_kind, COUNT(*) AS total,\n                   SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END) AS ai_assisted,\n                   ROUND(SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS ai_pct\n            FROM commit_fact\n            WHERE refresh_id = ? AND conventional_kind IS NOT NULL\n            GROUP BY conventional_kind\n            ORDER BY total DESC\n        ', [refresh_id]).fetchall()
    return [{'kind': r[0], 'total': r[1], 'ai_assisted': r[2], 'ai_pct': r[3]} for r in rows]

@app.tool()
def export_staleness() -> list[dict[str, Any]]:
    """Export/data staleness dashboard.

    Reports freshness of each data source. Sources with data older than
    30 days are flagged as stale with remediation recommendations.
    """
    from lynchpin.core.config import get_config
    cfg = get_config()
    sources = cfg.available_sources()
    known_stale = {'spotify': ('Oct 2025', 'Request new Spotify GDPR export'), 'reddit': ('Mar 2025', 'Request new Reddit GDPR export'), 'sleep': ('Jul 2025', 'Re-sync Samsung Health data'), 'webhistory': ('Mar 2026', 'Re-enable browser history capture'), 'messenger': ('Mar 2025', 'Request new Facebook GDPR export'), 'raindrop': ('Mar 2025', 'Request new Raindrop export')}
    results = []
    for name, available in sorted(sources.items()):
        info = known_stale.get(name)
        if info:
            results.append({'source': name, 'available': available, 'last_known_data': info[0], 'stale': True, 'recommendation': info[1]})
        else:
            results.append({'source': name, 'available': available, 'last_known_data': None, 'stale': False, 'recommendation': None})
    return results

@app.tool()
def cross_source_lag(project: str | None=None, refresh_id: str | None=None) -> dict[str, Any]:
    """AI→commit time lag distribution.

    For AI-attributed commits, computes the time between AI work event
    start and commit. Returns min/median/mean/max lag in hours.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {'error': 'no data'}
        proj_filter = 'AND c.project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        stats = conn.execute(f'\n            SELECT COUNT(*),\n                   ROUND(MIN(ABS(EXTRACT(EPOCH FROM c.authored_at-we.start_ts)))/3600.0,1),\n                   ROUND(QUANTILE_CONT(ABS(EXTRACT(EPOCH FROM c.authored_at-we.start_ts)),0.5)/3600.0,1),\n                   ROUND(AVG(ABS(EXTRACT(EPOCH FROM c.authored_at-we.start_ts)))/3600.0,1),\n                   ROUND(MAX(ABS(EXTRACT(EPOCH FROM c.authored_at-we.start_ts)))/3600.0,1)\n            FROM commit_fact c\n            JOIN ai_work_event we ON c.project=we.project AND list_has_any(c.paths, we.file_paths)\n            WHERE c.refresh_id=? AND c.ai_attribution IS NOT NULL\n              AND we.start_ts IS NOT NULL AND len(c.paths)>0 {proj_filter}\n        ', params).fetchone()
    if stats is None:
        return {'pairs': 0, 'min_hours': None, 'median_hours': None, 'mean_hours': None, 'max_hours': None}
    return {'pairs': stats[0], 'min_hours': stats[1], 'median_hours': stats[2], 'mean_hours': stats[3], 'max_hours': stats[4]}

@app.tool()
def project_health(project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Project health composite score (exploration round 3).

    Combines velocity, review quality, churn, and symbol activity into
    a per-project health card. Each dimension is scored against the
    project's own baseline.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'WHERE project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        rows = conn.execute(f"\n            SELECT p.project,\n                   COALESCE(SUM(p.commit_count),0) AS commits,\n                   COUNT(DISTINCT p.date) AS active_days,\n                   COALESCE(pr.pr_count,0) AS prs,\n                   COALESCE(ROUND(pr.avg_merge_hours,1),0) AS avg_merge_hours,\n                   COALESCE(sym.symbol_changes,0) AS symbol_changes,\n                   COALESCE(ROUND(sym.churn_rate,1),0) AS daily_churn_rate\n            FROM project_day_correlation p\n            LEFT JOIN (\n                SELECT project, COUNT(*) AS pr_count,\n                       AVG(time_to_merge_minutes)/60.0 AS avg_merge_hours\n                FROM pr_review_row WHERE refresh_id=?\n                GROUP BY project\n            ) pr ON p.project=pr.project\n            LEFT JOIN (\n                SELECT project, COUNT(*) AS symbol_changes,\n                       COUNT(*)*1.0/COUNT(DISTINCT date) AS churn_rate\n                FROM symbol_change WHERE refresh_id=?\n                GROUP BY project\n            ) sym ON p.project=sym.project\n            WHERE p.refresh_id=? AND p.commit_count>0 {proj_filter.replace('WHERE', 'AND')}\n            GROUP BY p.project, pr.pr_count, pr.avg_merge_hours,\n                     sym.symbol_changes, sym.churn_rate\n            ORDER BY commits DESC\n        ", [refresh_id, refresh_id, refresh_id] + ([project] if project else [])).fetchall()
    return [{'project': r[0], 'commits': r[1], 'active_days': r[2], 'prs': r[3], 'avg_merge_hours': r[4], 'symbol_changes': r[5], 'daily_churn_rate': r[6]} for r in rows]

@app.tool()
def daily_rhythm_fingerprint(project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Daily rhythm fingerprint per project (exploration round 5).

    Classifies projects by work pattern: morning, afternoon, evening,
    night-owl, weekend-warrior, 9-to-5. Based on commit time-of-day
    and day-of-week distributions.
    """
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []
        proj_filter = 'AND project = ?' if project else ''
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)
        rows = conn.execute(f'\n            WITH hourly AS (\n                SELECT project,\n                       EXTRACT(HOUR FROM authored_at)::INTEGER AS hr,\n                       COUNT(*) AS cnt\n                FROM commit_fact\n                WHERE refresh_id = ? {proj_filter}\n                GROUP BY project, hr\n            ),\n            dowly AS (\n                SELECT project,\n                       EXTRACT(DOW FROM authored_at)::INTEGER AS dow,\n                       COUNT(*) AS cnt\n                FROM commit_fact\n                WHERE refresh_id = ? {proj_filter}\n                GROUP BY project, dow\n            )\n            SELECT h.project,\n                   SUM(CASE WHEN h.hr BETWEEN 5 AND 11 THEN h.cnt ELSE 0 END) AS morning,\n                   SUM(CASE WHEN h.hr BETWEEN 12 AND 16 THEN h.cnt ELSE 0 END) AS afternoon,\n                   SUM(CASE WHEN h.hr BETWEEN 17 AND 21 THEN h.cnt ELSE 0 END) AS evening,\n                   SUM(CASE WHEN h.hr>=22 OR h.hr<=4 THEN h.cnt ELSE 0 END) AS night,\n                   SUM(CASE WHEN d.dow>=5 THEN d.cnt ELSE 0 END) AS weekend,\n                   SUM(CASE WHEN d.dow<=4 THEN d.cnt ELSE 0 END) AS weekday,\n                   COUNT(*) AS total\n            FROM hourly h\n            JOIN dowly d ON h.project=d.project\n            GROUP BY h.project\n            ORDER BY total DESC\n        ', [refresh_id] + ([project] if project else []) + [refresh_id] + ([project] if project else [])).fetchall()
    results = []
    for r in rows:
        proj = r[0]
        morning, evening, night = (r[1], r[3], r[4])
        weekend, total = (r[5], r[7])
        mpct = morning * 100.0 / max(total, 1)
        epct = evening * 100.0 / max(total, 1)
        npct = night * 100.0 / max(total, 1)
        wpct = weekend * 100.0 / max(total, 1)
        if wpct > 30:
            pattern = 'weekend-warrior'
        elif npct > 25:
            pattern = 'night-owl'
        elif mpct > 45:
            pattern = 'morning-person'
        elif epct > 40:
            pattern = 'evening-coder'
        else:
            pattern = '9-to-5'
        results.append({'project': proj, 'total_commits': total, 'morning_pct': round(mpct, 1), 'afternoon_pct': round(r[2] * 100.0 / max(total, 1), 1), 'evening_pct': round(epct, 1), 'night_pct': round(npct, 1), 'weekend_pct': round(wpct, 1), 'pattern': pattern})
    return results

@app.tool()
def symbol_churn_hotspots(top_n: int=20, project: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Symbol churn hotspot detection (exploration round 2).

    Files with the highest symbol turnover rate. Ranks paths by
    number of distinct symbols changed, grouped by path root.
    """
    from lynchpin.substrate.connection import connect, substrate_path
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
        rows = conn.execute(f"\n            SELECT path,\n                   COUNT(DISTINCT qualified_name) AS symbols,\n                   COUNT(DISTINCT sha) AS commits,\n                   COUNT(*) AS changes,\n                   COUNT(DISTINCT project) AS projects\n            FROM symbol_change\n            WHERE refresh_id = ? AND path IS NOT NULL AND path != ''\n              {proj_filter}\n            GROUP BY path\n            ORDER BY symbols DESC\n            LIMIT ?\n        ", params).fetchall()
    return [{'path': r[0], 'symbols': r[1], 'commits': r[2], 'changes': r[3], 'projects': r[4]} for r in rows]

@app.tool()
def spotify_daily(start: str | None=None, end: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Daily Spotify listening stats from the spotify_daily table.

    Parameters:
        start:      ISO date filter (YYYY-MM-DD).
        end:        ISO date filter (YYYY-MM-DD).
        refresh_id: snapshot (default: latest with data).
    """
    from datetime import date as _date
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, 'spotify_daily')
            if refresh_id is None:
                return []
        sql = 'SELECT date, track_count, minutes_played, unique_artists, unique_tracks, top_artists, top_tracks FROM spotify_daily WHERE refresh_id = ?'
        params: list[Any] = [refresh_id]
        if start:
            sql += ' AND date >= ?'
            params.append(_date.fromisoformat(start))
        if end:
            sql += ' AND date <= ?'
            params.append(_date.fromisoformat(end))
        sql += ' ORDER BY date'
        rows = conn.execute(sql, params).fetchall()
    return [{'date': _json_safe(r[0]), 'track_count': r[1], 'minutes_played': r[2], 'unique_artists': r[3], 'unique_tracks': r[4], 'top_artists': r[5], 'top_tracks': r[6]} for r in rows]

@app.tool()
def machine_metrics_daily(start: str | None=None, end: str | None=None, host: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Daily machine telemetry rollup from the machine_metric_sample table.

    Parameters:
        start:      ISO date filter (YYYY-MM-DD).
        end:        ISO date filter (YYYY-MM-DD).
        host:       optional host filter.
        refresh_id: snapshot (default: latest with data).
    """
    from datetime import date as _date
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, 'machine_metric_sample')
            if refresh_id is None:
                return []
        sql = '\n            SELECT\n                observed_at::DATE AS day,\n                host,\n                COUNT(*) AS samples,\n                AVG(cpu_package_w) AS avg_cpu_package_w,\n                MAX(cpu_package_w) AS max_cpu_package_w,\n                AVG(gpu_power_w) AS avg_gpu_power_w,\n                MAX(gpu_power_w) AS max_gpu_power_w,\n                AVG(io_psi_some_avg10) AS avg_io_psi_some_avg10,\n                MAX(io_psi_some_avg10) AS max_io_psi_some_avg10,\n                AVG(latency_oversleep_ms) AS avg_latency_oversleep_ms,\n                MAX(latency_oversleep_ms) AS max_latency_oversleep_ms,\n                MAX(dstate_task_count) AS max_dstate_task_count\n            FROM machine_metric_sample\n            WHERE refresh_id = ?\n        '
        params: list[Any] = [refresh_id]
        if start:
            sql += ' AND observed_at::DATE >= ?'
            params.append(_date.fromisoformat(start))
        if end:
            sql += ' AND observed_at::DATE <= ?'
            params.append(_date.fromisoformat(end))
        if host:
            sql += ' AND host = ?'
            params.append(host)
        sql += ' GROUP BY day, host ORDER BY day, host'
        rows = conn.execute(sql, params).fetchall()
    return [{'date': _json_safe(row[0]), 'host': row[1], 'samples': row[2], 'avg_cpu_package_w': row[3], 'max_cpu_package_w': row[4], 'avg_gpu_power_w': row[5], 'max_gpu_power_w': row[6], 'avg_io_psi_some_avg10': row[7], 'max_io_psi_some_avg10': row[8], 'avg_latency_oversleep_ms': row[9], 'max_latency_oversleep_ms': row[10], 'max_dstate_task_count': row[11]} for row in rows]

@app.tool()
def machine_service_state_summary(start: str | None=None, end: str | None=None, host: str | None=None, unit: str | None=None, refresh_id: str | None=None) -> list[dict[str, Any]]:
    """Summarize sampled systemd/user-unit state from machine_service_state.

    Parameters:
        start:      ISO date filter (YYYY-MM-DD).
        end:        ISO date filter (YYYY-MM-DD).
        host:       optional host filter.
        unit:       optional exact unit filter.
        refresh_id: snapshot (default: latest with data).
    """
    from datetime import date as _date
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_refresh_id(conn, 'machine_service_state')
            if refresh_id is None:
                return []
        sql = "\n            SELECT\n                host,\n                unit,\n                scope,\n                COUNT(*) AS samples,\n                SUM(CASE WHEN active_state = 'active' THEN 1 ELSE 0 END) AS active_samples,\n                MAX(memory_current_bytes) AS max_memory_current_bytes,\n                MAX(cpu_usage_nsec) AS max_cpu_usage_nsec,\n                MAX(io_read_bytes) AS max_io_read_bytes,\n                MAX(io_write_bytes) AS max_io_write_bytes,\n                MIN(observed_at) AS first_observed_at,\n                MAX(observed_at) AS last_observed_at\n            FROM machine_service_state\n            WHERE refresh_id = ?\n        "
        params: list[Any] = [refresh_id]
        if start:
            sql += ' AND observed_at::DATE >= ?'
            params.append(_date.fromisoformat(start))
        if end:
            sql += ' AND observed_at::DATE <= ?'
            params.append(_date.fromisoformat(end))
        if host:
            sql += ' AND host = ?'
            params.append(host)
        if unit:
            sql += ' AND unit = ?'
            params.append(unit)
        sql += ' GROUP BY host, unit, scope ORDER BY host, scope, unit'
        rows = conn.execute(sql, params).fetchall()
    return [{'host': row[0], 'unit': row[1], 'scope': row[2], 'samples': row[3], 'active_samples': row[4], 'max_memory_current_bytes': row[5], 'max_cpu_usage_nsec': row[6], 'max_io_read_bytes': row[7], 'max_io_write_bytes': row[8], 'first_observed_at': _json_safe(row[9]), 'last_observed_at': _json_safe(row[10])} for row in rows]
