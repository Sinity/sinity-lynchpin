"""Velocity MCP tools: time-series, narratives, symbol churn, temporal rhythm."""

from typing import Any

from lynchpin.mcp.server import app
from lynchpin.mcp.tools._utils import (
    best_refresh_id as _best_refresh_id,
    json_safe as _json_safe,
    latest_refresh_id as _latest_refresh_id,
)




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
            refresh_id = _best_refresh_id(conn, "project_day_correlation")
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

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            refresh_id = _best_refresh_id(conn, "project_day_correlation")
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
                       SUM(CASE WHEN change_type = 'ADDED' THEN 1 ELSE 0 END) AS added,
                       SUM(CASE WHEN change_type = 'MODIFIED' THEN 1 ELSE 0 END) AS modified,
                       SUM(CASE WHEN change_type = 'RENAMED' THEN 1 ELSE 0 END) AS renamed,
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


# Path patterns that are NOT real-engineering code: lockfiles, snapshots,
# generated artifacts, fixture data, minified bundles. Used to compute
# `lines_added_clean` in engineering_throughput.
_NON_CODE_PATH_PATTERNS = (
    "Cargo.lock",
    "flake.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    ".snap",
    "/fixtures/",
    "/__snapshots__/",
    "/generated/",
    "/.lynchpin/generated/",
    "ai_activity.json",
    "focus_timeline.json",
    "narrative_window.json",
    ".min.js",
    ".min.css",
)


def _refresh_with_best_coverage(conn: Any, project: str) -> str | None:
    """Choose a refresh_id where both commit_fact and file_change_fact have
    rows for `project`. Resolves the current-state/dag shadowing bug where
    one refresh has commits but the other has file_changes.
    """
    rows = conn.execute(
        """
        SELECT cf.refresh_id, COUNT(DISTINCT cf.sha) AS commits,
               COUNT(fcf.path) AS file_changes
        FROM commit_fact cf
        LEFT JOIN file_change_fact fcf
          ON fcf.refresh_id = cf.refresh_id AND fcf.sha = cf.sha
        WHERE cf.project = ?
        GROUP BY cf.refresh_id
        HAVING commits > 0 AND file_changes > 0
        ORDER BY file_changes DESC, commits DESC
        """,
        [project],
    ).fetchall()
    return rows[0][0] if rows else None


def _is_non_code_path(path: str) -> bool:
    """True iff a path matches any non-code pattern (lockfiles/snapshots/etc.)."""
    if not path:
        return False
    return any(pattern in path for pattern in _NON_CODE_PATH_PATTERNS)


@app.tool()
def engineering_throughput(
    project: str,
    start: str | None = None,
    end: str | None = None,
    granularity: str = "week",
    refresh_id: str | None = None,
) -> dict[str, Any]:
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

    if granularity not in ("day", "week", "month"):
        return {
            "project": project, "granularity": granularity,
            "refresh_id": None, "degraded": True,
            "reason": f"unsupported granularity {granularity!r} (use day, week, or month)",
            "substrate_window": None, "periods": [],
        }

    path = substrate_path()
    with connect(path, read_only=True) as conn:
        if refresh_id is None:
            # Pick the refresh that has the richest *combined* coverage of
            # commit_fact + file_change_fact for this project. Picking the
            # refresh with most commit rows alone backfires when that
            # refresh's file_change_fact has narrow project scope (e.g.
            # current-state:2024-09-01 only covers intercept-bounce +
            # knowledgebase, not sinex/sinnix/sinity-lynchpin).
            refresh_id = _refresh_with_best_coverage(conn, project)
            if refresh_id is None:
                refresh_id = _best_refresh_id(conn, "commit_fact")

        if refresh_id is None:
            return {
                "project": project, "granularity": granularity,
                "refresh_id": None, "degraded": True,
                "reason": "no commit_fact promote runs found",
                "substrate_window": None, "periods": [],
            }

        bounds = conn.execute(
            "SELECT MIN(authored_at::DATE), MAX(authored_at::DATE) "
            "FROM commit_fact WHERE refresh_id = ?",
            [refresh_id],
        ).fetchone()
        substrate_window = {
            "start": _json_safe(bounds[0]),
            "end": _json_safe(bounds[1]),
        }

        proj_check = conn.execute(
            "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = ? AND project = ?",
            [refresh_id, project],
        ).fetchone()
        if proj_check[0] == 0:
            return {
                "project": project, "granularity": granularity,
                "refresh_id": refresh_id, "degraded": True,
                "reason": f"no commit_fact rows for project {project!r} in this snapshot",
                "substrate_window": substrate_window, "periods": [],
            }

        params: list[Any] = [refresh_id, project]
        date_filter = ""
        if start:
            date_filter += " AND authored_at::DATE >= ?"
            params.append(_d.fromisoformat(start))
        if end:
            date_filter += " AND authored_at::DATE <= ?"
            params.append(_d.fromisoformat(end))

        commits_sql = f"""
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   COUNT(*) AS n,
                   SUM(lines_added) AS la, SUM(lines_deleted) AS ld,
                   SUM(files_changed) AS fc
            FROM commit_fact
            WHERE refresh_id = ? AND project = ?{date_filter}
            GROUP BY period ORDER BY period
        """
        commit_rows = {r[0]: r for r in conn.execute(commits_sql, params).fetchall()}

        # file_change_fact for clean line counts
        file_rows: dict[Any, tuple[int, int]] = {}
        try:
            file_sql = f"""
                SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                       SUM(lines_added) AS la, SUM(lines_deleted) AS ld, path
                FROM file_change_fact
                WHERE refresh_id = ? AND project = ?{date_filter}
                GROUP BY period, path
            """
            agg_clean: dict[Any, tuple[int, int]] = {}
            for period, la, ld, p in conn.execute(file_sql, params).fetchall():
                if _is_non_code_path(p or ""):
                    continue
                la = la or 0
                ld = ld or 0
                cur = agg_clean.get(period, (0, 0))
                agg_clean[period] = (cur[0] + la, cur[1] + ld)
            file_rows = agg_clean
            fcf_present = True
        except Exception:
            fcf_present = False

        # symbol_change for symbol counts
        symbol_rows: dict[Any, dict[str, int]] = {}
        try:
            sym_sql = f"""
                SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                       change_type, COUNT(*) AS n
                FROM symbol_change sc
                JOIN commit_fact cf USING (sha)
                WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}
                GROUP BY period, change_type
            """
            for period, ct, n in conn.execute(sym_sql, params).fetchall():
                bucket = symbol_rows.setdefault(period, {})
                bucket[ct] = n
            sc_present = bool(symbol_rows)
        except Exception:
            sc_present = False

    periods = []
    for period_date, row in sorted(commit_rows.items(), key=lambda kv: kv[0]):
        # row = (period, n, la, ld, fc) from the SELECT above
        _, n, la, ld, fc = row
        clean_la, clean_ld = file_rows.get(period_date, (la or 0, ld or 0))
        # symbol_change.change_type is stored as uppercase words
        # ('ADDED'/'MODIFIED'/'RENAMED'/'DELETED') by the materializer in
        # analysis.code_index.symbol_changes. The single-letter / lowercase
        # fallbacks remain in case a future writer changes encoding.
        sym_bucket = symbol_rows.get(period_date, {})
        sa = sym_bucket.get("ADDED", 0) + sym_bucket.get("A", 0) + sym_bucket.get("added", 0)
        sm = sym_bucket.get("MODIFIED", 0) + sym_bucket.get("M", 0) + sym_bucket.get("modified", 0)
        sr = sym_bucket.get("RENAMED", 0) + sym_bucket.get("R", 0) + sym_bucket.get("renamed", 0)
        mean_lpc = round(clean_la / max(1, n), 1)
        # granularity_index is undefined (None) when clean_la == 0
        # (zero-line weeks have no meaningful granularity)
        granularity_index = (
            round(n / clean_la * 1000, 3) if clean_la > 0 else None
        )
        periods.append({
            "period_start": _json_safe(period_date),
            "commit_count": n,
            "files_changed": fc or 0,
            "lines_added": la or 0,
            "lines_deleted": ld or 0,
            "lines_added_clean": clean_la,
            "lines_deleted_clean": clean_ld,
            "symbols_added": sa,
            "symbols_modified": sm,
            "symbols_renamed": sr,
            "symbols_total": sa + sm + sr,
            "mean_lines_per_commit_clean": mean_lpc,
            "granularity_index": granularity_index,
        })

    reasons = []
    if not fcf_present:
        reasons.append("file_change_fact empty for this snapshot — lines_added_clean falls back to raw lines_added")
    if not sc_present:
        reasons.append("symbol_change empty for this snapshot — symbol counts all zero")
    degraded = bool(reasons)

    return {
        "project": project,
        "granularity": granularity,
        "refresh_id": refresh_id,
        "degraded": degraded,
        "reason": "; ".join(reasons) if reasons else None,
        "substrate_window": substrate_window,
        "periods": periods,
    }
