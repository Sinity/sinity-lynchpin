"""Health, audit, and anomaly MCP tools."""
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id as _best_refresh_id, json_safe as _json_safe, latest_refresh_id as _latest_refresh_id

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
    from lynchpin.substrate.connection import connect, substrate_path
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
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, 'evidence_node')
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
    from lynchpin.substrate.connection import connect, substrate_path
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
    from lynchpin.substrate.connection import connect, substrate_path
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
def evidence_confidence(refresh_id: str | None=None) -> list[dict[str, Any]]:
    from lynchpin.substrate.connection import connect, substrate_path
    RELIABILITY = {'git': 'high', 'polylogue': 'medium', 'terminal': 'medium', 'activitywatch': 'medium', 'github': 'medium', 'github_ref': 'medium', 'raw_log': 'low', 'analysis': 'low'}
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, 'evidence_node')
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
    from lynchpin.substrate.connection import connect, substrate_path
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, 'project_day_correlation')
            if refresh_id is None:
                return []
        rows = conn.execute('\n            SELECT project, date, commit_count, ai_work_event_count,\n                   focus_seconds, source_count\n            FROM project_day_correlation\n            WHERE refresh_id = ? AND source_count >= 1\n        ', [refresh_id]).fetchall()
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
        commit_z = _z(commits, c_vals)
        ai_z = _z(ai, a_vals)
        focus_z = _z(focus_sec or 0, f_vals)
        if commits > 0 and ai == 0:
            anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'commits_without_ai_event', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'severity_z': round(commit_z, 3), 'detail': f'{commits} commits with 0 project-attributed AI work events; interpret as an attribution gap until ai_work_event.project coverage is healthy'})
        if ai > 0 and commits == 0 and (ai_z > threshold_sigma):
            anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'ai_without_commits', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'severity_z': round(ai_z, 3), 'detail': f"{ai} AI work events with 0 commits — exploration that didn't ship"})
        if (focus_sec or 0) > 0 and commits == 0:
            anomalies.append({'project': proj, 'date': _json_safe(d), 'anomaly_type': 'focus_without_git', 'commit_count': commits, 'ai_count': ai, 'focus_min': round((focus_sec or 0) / 60, 1), 'source_count': src, 'severity_z': round(focus_z, 3), 'detail': f'{round((focus_sec or 0) / 60, 1)} focus-minutes with 0 commits — reading, debugging, planning, or delayed materialization'})
    anomalies.sort(key=lambda a: (a['project'], a['date']))
    return anomalies

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
    from lynchpin.substrate.connection import connect, substrate_path, apply_schema
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
    from lynchpin.substrate.connection import connect, substrate_path
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
