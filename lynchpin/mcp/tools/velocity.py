"""Velocity MCP tools: time-series, narratives, symbol churn, temporal rhythm."""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import json_safe as _json_safe, latest_refresh_id as _latest_refresh_id, best_refresh_id




# ── D.4 Velocity Series ──────────────────────────────────────────────────────


@app.tool()
def velocity_series(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
    window_days: int = 7,
) -> list[dict[str, Any]]:
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
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return []

    proj_filter = ""
    params: list[Any] = [refresh_id]
    if projects:
        placeholders = ",".join(["?"] * len(projects))
        proj_filter = f"AND project IN ({placeholders})"
        params.extend(projects)

    with connect(path, read_only=True) as conn:
        sql = f"""
            SELECT project, date, commit_count,
                   ROUND(AVG(commit_count) OVER (
                       PARTITION BY project ORDER BY date
                       ROWS BETWEEN {int(window_days) - 1} PRECEDING AND CURRENT ROW
                   ), 1) AS rolling_avg,
                   SUM(commit_count) OVER (
                       PARTITION BY project ORDER BY date
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative,
                   source_count
            FROM project_day_correlation
            WHERE refresh_id = ? AND commit_count > 0 {proj_filter}
            ORDER BY project, date
        """
        rows = conn.execute(sql, params).fetchall()

    cols = ["project", "date", "commit_count", "rolling_avg", "cumulative", "source_count"]
    return [
        {c: _json_safe(v) for c, v in zip(cols, row)}
        for row in rows
    ]


# ── E.3 Gap Draft ────────────────────────────────────────────────────────────



# ── M.6 Velocity Narrative ───────────────────────────────────────────────────


@app.tool()
def velocity_narrative(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
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

    with connect(substrate_path(), read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _latest_refresh_id(conn)
            if refresh_id is None:
                return {"error": "no promote runs"}

        # Window bounds
        win = conn.execute("""
            SELECT MIN(date), MAX(date)
            FROM project_day_correlation WHERE refresh_id = ?
        """, [refresh_id]).fetchone()

        proj_filter = ""
        params: list[Any] = [refresh_id]
        if projects:
            placeholders = ",".join(["?"] * len(projects))
            proj_filter = f"AND project IN ({placeholders})"
            params.extend(projects)

        # Projects summary
        proj_rows = conn.execute(f"""
            SELECT project,
                   SUM(commit_count) AS commits,
                   COUNT(*) AS active_days,
                   ROUND(AVG(commit_count), 1) AS avg_daily
            FROM project_day_correlation
            WHERE refresh_id = ? AND commit_count > 0 {proj_filter}
            GROUP BY project ORDER BY commits DESC
        """, params).fetchall()

        # Peak
        peak = conn.execute(f"""
            SELECT project, date, commit_count
            FROM project_day_correlation
            WHERE refresh_id = ? {proj_filter}
            ORDER BY commit_count DESC LIMIT 1
        """, params).fetchone()

        total_commits = sum(r[1] for r in proj_rows)
        total_days = sum(r[2] for r in proj_rows)

        projects_list = [
            {"project": r[0], "commits": r[1],
             "active_days": r[2], "avg_daily": r[3]}
            for r in proj_rows
        ]

        # Build narrative text
        if proj_rows:
            top = proj_rows[0]
            lines = [
                f"In the window {win[0]} → {win[1]}: "
                f"{total_commits} commits across {len(proj_rows)} projects "
                f"({total_days} active project-days).",
                "",
                f"**{top[0]}** led with {top[1]} commits over "
                f"{top[2]} active days (avg {top[3]}/day).",
            ]
            if peak:
                lines.append(
                    f"Peak day: **{peak[0]}** on {peak[1]} "
                    f"({peak[2]} commits)."
                )
            if len(proj_rows) > 1:
                rest = [f"**{p[0]}** ({p[1]} commits)" for p in proj_rows[1:4]]
                lines.append(
                    f"Also active: {', '.join(rest)}."
                )
            if len(proj_rows) > 4:
                lines.append(
                    f"(+{len(proj_rows) - 4} more projects with lower activity)"
                )
            summary = "\n".join(lines)
        else:
            summary = "No project activity in this window."

    return {
        "window": {"start": _json_safe(win[0]), "end": _json_safe(win[1])},
        "total_commits": total_commits,
        "total_active_days": total_days,
        "peak": {
            "project": peak[0], "date": _json_safe(peak[1]),
            "commits": peak[2],
        } if peak else None,
        "projects": projects_list,
        "summary_text": summary,
    }


# ── A.1 D.3 WorkPackageDurability ────────────────────────────────────────────



# ══════════════════════════════════════════════════════════════════════════════
# Phase B — New capabilities from the complete substrate
# ══════════════════════════════════════════════════════════════════════════════


# ── B.1 Symbol-Level Velocity ────────────────────────────────────────────────


@app.tool()
def symbol_velocity(
    projects: list[str] | None = None,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
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

        outer_filter = ""
        inner_filter = ""
        params: list[Any] = [refresh_id, refresh_id]
        if projects:
            placeholders = ",".join(["?"] * len(projects))
            outer_filter = f"AND p.project IN ({placeholders})"
            inner_filter = f"AND project IN ({placeholders})"
            params = [refresh_id, *projects, refresh_id, *projects]

        rows = conn.execute(f"""
            SELECT COALESCE(p.project, sym.project) AS project,
                   COALESCE(p.date, sym.date) AS date,
                   COALESCE(p.commit_count, 0) AS commit_count,
                   COALESCE(sym.added, 0) AS symbols_added,
                   COALESCE(sym.modified, 0) AS symbols_modified,
                   COALESCE(sym.renamed, 0) AS symbols_renamed,
                   COALESCE(sym.total, 0) AS symbols_total
            FROM project_day_correlation p
            FULL OUTER JOIN (
                SELECT project, date,
                       SUM(CASE WHEN change_type = 'A' THEN 1 ELSE 0 END) AS added,
                       SUM(CASE WHEN change_type = 'M' THEN 1 ELSE 0 END) AS modified,
                       SUM(CASE WHEN change_type = 'R' THEN 1 ELSE 0 END) AS renamed,
                       COUNT(*) AS total
                FROM symbol_change
                WHERE refresh_id = ? {inner_filter}
                GROUP BY project, date
            ) sym ON p.project = sym.project AND p.date = sym.date
               AND p.refresh_id = ?
            {outer_filter}
            ORDER BY project, date
        """, params).fetchall()

    return [
        {"project": r[0], "date": _json_safe(r[1]),
         "commit_count": r[2], "symbols_added": r[3],
         "symbols_modified": r[4], "symbols_renamed": r[5],
         "symbols_total": r[6]}
        for r in rows
    ]


# ── B.2 File Hotspot Detection ───────────────────────────────────────────────



# ── B.3 Temporal Rhythm Analysis ─────────────────────────────────────────────


@app.tool()
def temporal_rhythm(
    project: str | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
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
                return {"hourly": [], "weekday": [], "peak_hour": None, "peak_weekday": None}

        proj_filter = "AND project = ?" if project else ""
        params: list[Any] = [refresh_id]
        if project:
            params.append(project)

        hourly = conn.execute(f"""
            SELECT EXTRACT(HOUR FROM authored_at)::INTEGER AS hr,
                   COUNT(*) AS cnt
            FROM commit_fact
            WHERE refresh_id = ? {proj_filter}
            GROUP BY hr ORDER BY hr
        """, params).fetchall()

        weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

        weekday = conn.execute(f"""
            SELECT EXTRACT(DOW FROM authored_at)::INTEGER AS dow,
                   COUNT(*) AS cnt
            FROM commit_fact
            WHERE refresh_id = ? {proj_filter}
            GROUP BY dow ORDER BY dow
        """, params).fetchall()

    peak_hour = max(hourly, key=lambda r: r[1])[0] if hourly else None
    peak_dow = max(weekday, key=lambda r: r[1]) if weekday else None

    return {
        "hourly": [{"hour": r[0], "count": r[1]} for r in hourly],
        "weekday": [{"weekday": r[0], "name": weekday_names[r[0]], "count": r[1]}
                     for r in weekday],
        "peak_hour": peak_hour,
        "peak_weekday": weekday_names[peak_dow[0]] if peak_dow else None,
    }


# ── B.4 Evidence Confidence Tiering ──────────────────────────────────────────
