"""Velocity MCP tools: time-series, narratives, symbol churn, temporal rhythm."""
from typing import Any
from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import best_refresh_id as _best_refresh_id, json_safe as _json_safe, latest_refresh_id as _latest_refresh_id

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
    from lynchpin.substrate.connection import connect, substrate_path
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, 'project_day_correlation')
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
    from lynchpin.substrate.connection import connect, substrate_path
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, 'project_day_correlation')
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
    from lynchpin.substrate.connection import connect, substrate_path
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
        rows = conn.execute(f"\n            SELECT COALESCE(p.project, sym.project) AS project,\n                   COALESCE(p.date, sym.date) AS date,\n                   COALESCE(p.commit_count, 0) AS commit_count,\n                   COALESCE(sym.added, 0) AS symbols_added,\n                   COALESCE(sym.modified, 0) AS symbols_modified,\n                   COALESCE(sym.renamed, 0) AS symbols_renamed,\n                   COALESCE(sym.total, 0) AS symbols_total\n            FROM project_day_correlation p\n            FULL OUTER JOIN (\n                SELECT project, date,\n                       SUM(CASE WHEN change_type = 'ADDED' THEN 1 ELSE 0 END) AS added,\n                       SUM(CASE WHEN change_type = 'MODIFIED' THEN 1 ELSE 0 END) AS modified,\n                       SUM(CASE WHEN change_type = 'RENAMED' THEN 1 ELSE 0 END) AS renamed,\n                       COUNT(*) AS total\n                FROM symbol_change\n                WHERE refresh_id = ? {inner_filter}\n                GROUP BY project, date\n            ) sym ON p.project = sym.project AND p.date = sym.date\n               AND p.refresh_id = ?\n            {outer_filter}\n            ORDER BY project, date\n        ", params).fetchall()
    return [{'project': r[0], 'date': _json_safe(r[1]), 'commit_count': r[2], 'symbols_added': r[3], 'symbols_modified': r[4], 'symbols_renamed': r[5], 'symbols_total': r[6]} for r in rows]

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
    from lynchpin.substrate.connection import connect, substrate_path
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
_NON_CODE_PATH_PATTERNS = ('Cargo.lock', 'flake.lock', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'uv.lock', 'poetry.lock', 'Pipfile.lock', '.snap', '/fixtures/', '/__snapshots__/', '/generated/', '/.lynchpin/generated/', 'ai_activity.json', 'focus_timeline.json', 'narrative_window.json', '.min.js', '.min.css')

def _refresh_with_best_coverage(conn: Any, project: str) -> str | None:
    """Choose a refresh_id where both commit_fact and file_change_fact have
    rows for `project`. Resolves the current-state/dag shadowing bug where
    one refresh has commits but the other has file_changes.
    """
    rows = conn.execute('\n        SELECT cf.refresh_id, COUNT(DISTINCT cf.sha) AS commits,\n               COUNT(fcf.path) AS file_changes\n        FROM commit_fact cf\n        LEFT JOIN file_change_fact fcf\n          ON fcf.refresh_id = cf.refresh_id AND fcf.sha = cf.sha\n        WHERE cf.project = ?\n        GROUP BY cf.refresh_id\n        HAVING commits > 0 AND file_changes > 0\n        ORDER BY file_changes DESC, commits DESC\n        ', [project]).fetchall()
    return rows[0][0] if rows else None

def _is_non_code_path(path: str) -> bool:
    """True iff a path matches any non-code pattern (lockfiles/snapshots/etc.)."""
    if not path:
        return False
    return any((pattern in path for pattern in _NON_CODE_PATH_PATTERNS))

@app.tool()
def engineering_throughput(project: str, start: str | None=None, end: str | None=None, granularity: str='week', refresh_id: str | None=None, grouping: str='raw') -> dict[str, Any]:
    """Decomposed engineering-throughput estimate for a project window.

    Composes commit_fact + file_change_fact + symbol_change so that the
    "is project X actually accelerating, or just committing more
    granularly?" question can be answered from one tool rather than four
    silo'd ones (velocity_series, symbol_velocity, file_hotspots,
    commit_kind_attribution).

    Parameters:
        project:     canonical project name (required — windowing across
                     all projects is the existing velocity_series).
        start, end:  ISO dates; default = full window of the snapshot.
        granularity: "day" | "week" | "month". Aggregation period.
        refresh_id:  substrate snapshot. Defaults to latest commit_fact build.
        grouping:    "raw" (per-commit, default) or "pr" (group commits
                     by PR number extracted from subject, falling back to
                     sha for commits without ``(#N)``).  "pr" normalises
                     across the March 2026 workflow regime change so that
                     atomic-commit months are comparable with squash-merge
                     months.

    Returns rows shaped:
        {
            "project": str,
            "granularity": str,
            "refresh_id": str | None,
            "degraded": bool,
            "reason": str | None,
            "substrate_window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
            "periods": [
                {
                    "period_start", "commit_count", "files_changed",
                    "lines_added", "lines_deleted",
                    "lines_added_clean", "lines_deleted_clean",
                    "symbols_added", "symbols_modified", "symbols_renamed",
                    "symbols_total",
                    "mean_lines_per_commit_clean", "granularity_index",
                },
                ...
            ],
        }
    """
    from datetime import date as _d
    from lynchpin.substrate.connection import connect, substrate_path
    if granularity not in ('day', 'week', 'month'):
        return {'project': project, 'granularity': granularity, 'refresh_id': None, 'degraded': True, 'reason': f'unsupported granularity {granularity!r} (use day, week, or month)', 'substrate_window': None, 'periods': []}
    if grouping not in ('raw', 'pr'):
        return {'project': project, 'granularity': granularity, 'refresh_id': None, 'degraded': True, 'reason': f'unsupported grouping {grouping!r} (use raw or pr)', 'substrate_window': None, 'periods': []}
    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _refresh_with_best_coverage(conn, project)
            if refresh_id is None:
                refresh_id = _best_refresh_id(conn, 'commit_fact')
        if refresh_id is None:
            return {'project': project, 'granularity': granularity, 'refresh_id': None, 'degraded': True, 'reason': 'no commit_fact promote runs found', 'substrate_window': None, 'periods': []}
        bounds = conn.execute('SELECT MIN(authored_at::DATE), MAX(authored_at::DATE) FROM commit_fact WHERE refresh_id = ?', [refresh_id]).fetchone()
        substrate_window = {'start': _json_safe(bounds[0]), 'end': _json_safe(bounds[1])}
        proj_check = conn.execute('SELECT COUNT(*) FROM commit_fact WHERE refresh_id = ? AND project = ?', [refresh_id, project]).fetchone()
        if proj_check[0] == 0:
            return {'project': project, 'granularity': granularity, 'refresh_id': refresh_id, 'degraded': True, 'reason': f'no commit_fact rows for project {project!r} in this snapshot', 'substrate_window': substrate_window, 'periods': []}
        params: list[Any] = [refresh_id, project]
        date_filter = ''
        if start:
            date_filter += ' AND authored_at::DATE >= ?'
            params.append(_d.fromisoformat(start))
        if end:
            date_filter += ' AND authored_at::DATE <= ?'
            params.append(_d.fromisoformat(end))
        if grouping == 'pr':
            commits_sql = f"\n                WITH pr_commits AS (\n                    SELECT\n                        COALESCE(\n                            NULLIF(regexp_extract(subject, '\\(#(\\d+)\\)', 1), ''),\n                            sha\n                        ) AS group_key,\n                        MAX(authored_at) AS authored_at,\n                        SUM(lines_added) AS lines_added,\n                        SUM(lines_deleted) AS lines_deleted,\n                        SUM(files_changed) AS files_changed,\n                        COUNT(*) AS commits_in_group\n                    FROM commit_fact\n                    WHERE refresh_id = ? AND project = ?{date_filter}\n                    GROUP BY group_key\n                )\n                SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                       COUNT(*) AS n,\n                       SUM(lines_added) AS la, SUM(lines_deleted) AS ld,\n                       SUM(files_changed) AS fc\n                FROM pr_commits\n                GROUP BY period ORDER BY period\n            "
        else:
            commits_sql = f"\n                SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                       COUNT(*) AS n,\n                       SUM(lines_added) AS la, SUM(lines_deleted) AS ld,\n                       SUM(files_changed) AS fc\n                FROM commit_fact\n                WHERE refresh_id = ? AND project = ?{date_filter}\n                GROUP BY period ORDER BY period\n            "
        commit_rows = {r[0]: r for r in conn.execute(commits_sql, params).fetchall()}
        file_rows: dict[Any, tuple[int, int]] = {}
        try:
            if grouping == 'pr':
                file_sql = f"\n                    WITH pr_files AS (\n                        SELECT\n                            COALESCE(\n                                NULLIF(regexp_extract(cf.subject, '\\(#(\\d+)\\)', 1), ''),\n                                cf.sha\n                            ) AS group_key,\n                            MAX(cf.authored_at) AS authored_at,\n                            fcf.path,\n                            SUM(fcf.lines_added) AS la,\n                            SUM(fcf.lines_deleted) AS ld\n                        FROM file_change_fact fcf\n                        JOIN commit_fact cf USING (sha)\n                        WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}\n                        GROUP BY group_key, fcf.path\n                    )\n                    SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                           SUM(la) AS la, SUM(ld) AS ld, path\n                    FROM pr_files\n                    GROUP BY period, path\n                "
            else:
                file_sql = f"\n                    SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                           SUM(lines_added) AS la, SUM(lines_deleted) AS ld, path\n                    FROM file_change_fact\n                    WHERE refresh_id = ? AND project = ?{date_filter}\n                    GROUP BY period, path\n                "
            agg_clean: dict[Any, tuple[int, int]] = {}
            for period, la, ld, p in conn.execute(file_sql, params).fetchall():
                if _is_non_code_path(p or ''):
                    continue
                la = la or 0
                ld = ld or 0
                cur = agg_clean.get(period, (0, 0))
                agg_clean[period] = (cur[0] + la, cur[1] + ld)
            file_rows = agg_clean
            fcf_present = True
        except Exception:
            fcf_present = False
        symbol_rows: dict[Any, dict[str, int]] = {}
        try:
            if grouping == 'pr':
                sym_sql = f"\n                    WITH pr_symbols AS (\n                        SELECT\n                            COALESCE(\n                                NULLIF(regexp_extract(cf.subject, '\\(#(\\d+)\\)', 1), ''),\n                                cf.sha\n                            ) AS group_key,\n                            sc.change_type,\n                            sc.qualified_name,\n                            sc.path,\n                            MAX(cf.authored_at) AS authored_at\n                        FROM symbol_change sc\n                        JOIN commit_fact cf USING (sha)\n                        WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}\n                        GROUP BY group_key, sc.change_type, sc.qualified_name, sc.path\n                    )\n                    SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                           change_type, COUNT(*) AS n\n                    FROM pr_symbols\n                    GROUP BY period, change_type\n                "
            else:
                sym_sql = f"\n                    SELECT date_trunc('{granularity}', authored_at)::DATE AS period,\n                           change_type, COUNT(*) AS n\n                    FROM symbol_change sc\n                    JOIN commit_fact cf USING (sha)\n                    WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}\n                    GROUP BY period, change_type\n                "
            for period, ct, n in conn.execute(sym_sql, params).fetchall():
                bucket = symbol_rows.setdefault(period, {})
                bucket[ct] = n
            sc_present = bool(symbol_rows)
        except Exception:
            sc_present = False
    periods = []
    for period_date, row in sorted(commit_rows.items(), key=lambda kv: kv[0]):
        _, n, la, ld, fc = row
        clean_la, clean_ld = file_rows.get(period_date, (la or 0, ld or 0))
        sym_bucket = symbol_rows.get(period_date, {})
        sa = sym_bucket.get('ADDED', 0) + sym_bucket.get('A', 0) + sym_bucket.get('added', 0)
        sm = sym_bucket.get('MODIFIED', 0) + sym_bucket.get('M', 0) + sym_bucket.get('modified', 0)
        sr = sym_bucket.get('RENAMED', 0) + sym_bucket.get('R', 0) + sym_bucket.get('renamed', 0)
        mean_lpc = round(clean_la / max(1, n), 1)
        granularity_index = round(n / clean_la * 1000, 3) if clean_la > 0 else None
        if granularity_index is None:
            commit_regime = None
        elif granularity_index < 0.5:
            commit_regime = 'pr_squash_merge'
        elif granularity_index > 1.0:
            commit_regime = 'atomic_commits'
        else:
            commit_regime = 'transitional'
        periods.append({'period_start': _json_safe(period_date), 'commit_count': n, 'files_changed': fc or 0, 'lines_added': la or 0, 'lines_deleted': ld or 0, 'lines_added_clean': clean_la, 'lines_deleted_clean': clean_ld, 'symbols_added': sa, 'symbols_modified': sm, 'symbols_renamed': sr, 'symbols_total': sa + sm + sr, 'mean_lines_per_commit_clean': mean_lpc, 'granularity_index': granularity_index, 'commit_regime': commit_regime})
    reasons = []
    if not fcf_present:
        reasons.append('file_change_fact empty for this snapshot — lines_added_clean falls back to raw lines_added')
    if not sc_present:
        reasons.append('symbol_change empty for this snapshot — symbol counts all zero')
    degraded = bool(reasons)
    return {'project': project, 'granularity': granularity, 'grouping': grouping, 'refresh_id': refresh_id, 'degraded': degraded, 'reason': '; '.join(reasons) if reasons else None, 'substrate_window': substrate_window, 'periods': periods}
