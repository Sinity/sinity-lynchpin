"""Health, audit, and anomaly MCP tools."""
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_materialized_refresh_id, ensure_substrate_materialized_for_read, half_open_date_window, json_safe as _json_safe, latest_materialized_refresh_id

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
    from lynchpin.substrate.readers_health import load_source_gap_rows
    ensure_substrate_materialized_for_read(caller='substrate_gap_draft')
    with connect(substrate_path(), read_only=True) as conn:
        refresh_id = latest_materialized_refresh_id(conn, caller='substrate_gap_draft')
        if refresh_id is None:
            return {'needs_attention': False, 'draft_title': None, 'draft_body': None, 'gaps': [], 'all_sources_healthy': True}
        gaps = load_source_gap_rows(conn, refresh_id=refresh_id)
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
    score based on row count, observed date coverage, cross-source agreement,
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
    from lynchpin.substrate.readers_health import load_evidence_node_by_source, load_source_status_map
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='substrate_confidence_matrix')
    return {'refresh_id': refresh_id, 'dimensions': dimensions, 'summary': {'total_nodes': total_nodes, 'source_count': len(dimensions), 'healthy_source_count': healthy, 'confidence_pct': round(confidence, 1)}}

@app.tool()
def kind_audit(refresh_id: str | None=None) -> dict[str, Any]:
    """Source-label-vs-Lynchpin kind audit (Arc K.1).

    Reads ai_work_event.kind_* columns to surface agreement rates,
    disagreement cases, tier distributions, and per-kind confidence
    breakdowns. This is the quantitative foundation for the boundary doc
    (K.4) — how often does the Lynchpin overlay disagree with the upstream
    work-event label?

    Parameters:
        refresh_id: promote snapshot (default: latest).

    Returns:
        {
            "refresh_id": str,
            "total": int,
            "tier_distribution": {"high": N, "medium": N, "low": N},
            "source_distribution": {"agreement": N, "disagreement": N,
                                     "source": N, "lynchpin_overlay": N},
            "disagreement_rate": float,
            "top_disagreements": [{"kind": str, "source_kind": str,
                                    "overlay_kind": str, "count": int}],
            "per_kind_confidence": [{"kind": str, "count": int,
                                      "avg_confidence": float}],
        }
    """
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_health import load_ai_work_event_count, load_ai_work_event_disagreements, load_ai_work_event_per_kind_confidence, load_ai_work_event_source_distribution, load_ai_work_event_tier_distribution
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='kind_audit')
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'ai_work_event', caller='kind_audit')
            if refresh_id is None:
                return {'error': 'no promote runs'}
        total = load_ai_work_event_count(conn, refresh_id=refresh_id)
        tiers = {}
        for r in load_ai_work_event_tier_distribution(conn, refresh_id=refresh_id):
            tiers[r[0] or 'null'] = r[1]
        sources = {}
        for r in load_ai_work_event_source_distribution(conn, refresh_id=refresh_id):
            sources[r[0] or 'null'] = r[1]
        disagreements = load_ai_work_event_disagreements(conn, refresh_id=refresh_id)
        per_kind = load_ai_work_event_per_kind_confidence(conn, refresh_id=refresh_id)
    disagree_count = sources.get('disagreement', 0)
    return {'refresh_id': refresh_id, 'total': total, 'tier_distribution': tiers, 'source_distribution': sources, 'disagreement_rate': round(disagree_count / max(total, 1), 3), 'top_disagreements': [{'kind': r[0], 'source_kind': r[1], 'overlay_kind': r[2], 'count': r[3]} for r in disagreements], 'per_kind_confidence': [{'kind': r[0], 'count': r[1], 'avg_confidence': r[2]} for r in per_kind]}

@app.tool()
def work_package_durability(refresh_id: str | None=None, min_symbols: int=10) -> dict[str, Any]:
    """Symbol survival at HEAD — which work packages persist? (Arc D.3)

    Self-joins symbol_change on qualified_name: symbols whose latest
    change_type is not a deletion are considered surviving. Groups by
    project + date to compute per-day survival rates.

    Parameters:
        refresh_id:  snapshot (default: best symbol_change coverage).
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
    from lynchpin.substrate.readers_health import load_symbol_change_count, load_symbol_survival_by_project_day
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='work_package_durability')
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'symbol_change', caller='work_package_durability')
            if refresh_id is None:
                return {'error': 'no promote runs'}
        total = load_symbol_change_count(conn, refresh_id=refresh_id)
        rows = load_symbol_survival_by_project_day(conn, refresh_id=refresh_id, min_symbols=min_symbols)
        surviving_total = sum((r[3] for r in rows))
        per_day = [{'project': r[0], 'date': _json_safe(r[1]), 'total': r[2], 'surviving': r[3], 'rate': round(r[3] / max(r[2], 1), 3)} for r in rows]
    return {'refresh_id': refresh_id, 'total_symbols': total, 'surviving': surviving_total, 'overall_survival_rate': round(surviving_total / max(total, 1), 3), 'per_project_day': per_day}

@app.tool()
def evidence_confidence(refresh_id: str | None=None) -> list[dict[str, Any]]:
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_health import load_evidence_node_source_caveats
    RELIABILITY = {'git': 'high', 'polylogue': 'medium', 'terminal': 'medium', 'activitywatch': 'medium', 'github': 'medium', 'github_ref': 'medium', 'raw_log': 'low', 'analysis': 'low'}
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='evidence_confidence')
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'evidence_node', caller='evidence_confidence')
            if refresh_id is None:
                return []
        rows = load_evidence_node_source_caveats(conn, refresh_id=refresh_id)
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
    from lynchpin.substrate.readers_health import load_project_day_anomaly_rows
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='source_anomalies')
    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = best_materialized_refresh_id(conn, 'project_day_correlation', caller='source_anomalies')
            if refresh_id is None:
                return []
        rows = load_project_day_anomaly_rows(conn, refresh_id=refresh_id)
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
            refresh_id = latest_materialized_refresh_id(conn, caller='adversarial_review')
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
    from lynchpin.substrate.readers_health import load_ordered_refresh_ids, load_source_status_by_refresh
    if refresh_id is None:
        ensure_substrate_materialized_for_read(caller='health_trend')
    with connect(substrate_path(), read_only=True) as conn:
        refresh_ids = load_ordered_refresh_ids(conn)
    if len(refresh_ids) < 2:
        return {'alert': False, 'detail': 'need 2+ refresh snapshots for trend', 'current': refresh_ids[-1] if refresh_ids else None, 'prior': None}
    current = refresh_id or refresh_ids[-1]
    prior = refresh_ids[-2]
    with connect(substrate_path(), read_only=True) as conn:

        def _conf(rid: str) -> tuple[float, int]:
            rows = load_source_status_by_refresh(conn, refresh_id=rid)
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

@app.tool()
def cleanup_period_detect(start: str, end: str, project: str | None=None) -> list[dict[str, Any]]:
    """Detect likely squash-cleanup periods via AI messages/commit ratio.

    Identifies months where the messages-to-commits ratio is anomalously high
    (>5000:1), indicating probable git history rewriting. These months should
    not be compared with atomic-commit months for velocity analysis.

    Args:
        start: Start date (ISO format, e.g., "2026-05-01").
        end: End date (ISO format, e.g., "2026-05-31").
        project: Optional project filter (matched against commit project).

    Returns:
        List of dicts with keys:
        - year_month: "YYYY-MM" format
        - commit_count: commits in the month
        - ai_messages: AI session messages in the month
        - ratio: messages / max(1, commits)
        - likely_cleanup: bool (True if ratio > 5000)
    """
    from datetime import date as _date_cls
    from lynchpin.substrate.connection import connect, substrate_path
    from lynchpin.substrate.readers_health import load_commits_by_month, load_ai_messages_by_month
    start_d = _date_cls.fromisoformat(start)
    end_d = _date_cls.fromisoformat(end)
    ensure_substrate_materialized_for_read(caller='cleanup_period_detect', window=half_open_date_window(start_d, end_d))
    with connect(substrate_path(), read_only=True) as conn:
        commits_by_month: dict[str, int] = {}
        for row in load_commits_by_month(conn, start=start_d, end=end_d, project=project):
            commits_by_month[row[0]] = row[1]
        messages_by_month: dict[str, int] = {}
        for row in load_ai_messages_by_month(conn, start=start_d, end=end_d, project=project):
            if row[1]:
                messages_by_month[row[0]] = int(row[1])
    all_months = sorted(set(commits_by_month.keys()) | set(messages_by_month.keys()))
    result: list[dict[str, Any]] = []
    for month in all_months:
        commit_count = commits_by_month.get(month, 0)
        ai_msgs = messages_by_month.get(month, 0)
        ratio = ai_msgs / max(1, commit_count)
        likely_cleanup = ratio > 5000
        result.append({'year_month': month, 'commit_count': commit_count, 'ai_messages': ai_msgs, 'ratio': round(ratio, 1), 'likely_cleanup': likely_cleanup})
    return result
