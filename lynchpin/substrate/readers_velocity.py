"""Read-only substrate readers for velocity MCP tools.

Extracted from lynchpin.mcp.tools.velocity to keep tool functions thin and
the SQL in the typed reader layer. All functions are SELECT-only.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


def _project_filter(column: str, projects: tuple[str, ...] | None) -> tuple[str, list[Any]]:
    """Return an ``AND`` clause for a project selection.

    ``None`` selects every project; an empty tuple selects none.
    """
    if projects is None:
        return "", []
    if not projects:
        return "AND FALSE", []
    placeholders = ",".join(["?"] * len(projects))
    return f"AND {column} IN ({placeholders})", list(projects)


# ── velocity_series ───────────────────────────────────────────────────────────


def load_graph_coverage_window(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> tuple[date, date] | None:
    """Return the logical graph's declared (start, end) window, or None.

    Inside this window a project-day without commits is a known zero; outside
    it (or without a build record) coverage is unknown.
    """
    from lynchpin.substrate.graph import _graph_lineage

    head = conn.execute(
        "SELECT end_date FROM evidence_graph_build WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()
    if head is None:
        return None
    partitions = [partition_id for partition_id, _cutoff in _graph_lineage(conn, refresh_id=refresh_id)]
    placeholders = ",".join(["?"] * len(partitions))
    row = conn.execute(
        f"SELECT MIN(start_date) FROM evidence_graph_build WHERE refresh_id IN ({placeholders})",
        partitions,
    ).fetchone()
    if row is None or row[0] is None or head[0] is None:
        return None
    return row[0], head[0]


def load_velocity_series(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    window_days: int = 7,
    projects: tuple[str, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Return trailing calendar-day commit windows per project.

    Rows are ``(project, date, commit_count, rolling_avg, cumulative,
    source_count, window_days_covered, active_days, active_day_avg)``.
    ``rolling_avg`` is commits over the last ``window_days`` calendar days
    divided by ``window_days``; days without commits inside the coverage
    window count as zeros. It is None when part of the window lies outside
    coverage. ``active_day_avg`` answers the different question of commits
    per day that had any. Days whose whole trailing window is zero are
    omitted.
    """
    from lynchpin.substrate.graph import _logical_graph_relation

    if window_days < 1:
        raise ValueError("window_days must be positive")
    relation, params = _logical_graph_relation(
        conn,
        refresh_id=refresh_id,
        table="project_day_correlation",
        columns=("project", "date", "commit_count", "source_count"),
        key_columns=("project", "date"),
    )
    proj_filter, proj_params = _project_filter("project", projects)
    params.extend(proj_params)

    rows = conn.execute(
        f"""
        SELECT project, date, commit_count, source_count
        FROM {relation}
        WHERE commit_count > 0 {proj_filter}
        ORDER BY project, date
        """,
        params,
    ).fetchall()
    coverage = load_graph_coverage_window(conn, refresh_id=refresh_id)

    by_project: dict[str, dict[date, tuple[int, int]]] = {}
    for project, day, commits, sources in rows:
        by_project.setdefault(project, {})[day] = (int(commits), int(sources or 0))

    result: list[tuple[Any, ...]] = []
    for project in sorted(by_project):
        observed = by_project[project]
        days = set(observed)
        if coverage is not None:
            days.update(_date_range(*coverage))
        cumulative = 0
        for day in sorted(days):
            commits, sources = observed.get(day, (0, 0))
            cumulative += commits
            window = [day - timedelta(days=offset) for offset in range(window_days)]
            covered = (
                sum(1 for d in window if coverage[0] <= d <= coverage[1])
                if coverage is not None
                else None
            )
            window_commits = [observed[d][0] for d in window if d in observed]
            if not commits and not window_commits:
                continue
            total = sum(window_commits)
            rolling_avg = round(total / window_days, 3) if covered == window_days else None
            active_days = len(window_commits)
            result.append((
                project,
                day,
                commits,
                rolling_avg,
                cumulative,
                sources,
                covered,
                active_days,
                round(total / active_days, 3) if active_days else None,
            ))
    return result


def _date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


# ── velocity_narrative ────────────────────────────────────────────────────────


def load_velocity_project_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    projects: tuple[str, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, commits, active_days, avg_per_active_day) per project."""
    from lynchpin.substrate.graph import _logical_graph_relation

    relation, params = _logical_graph_relation(
        conn,
        refresh_id=refresh_id,
        table="project_day_correlation",
        columns=("project", "date", "commit_count"),
        key_columns=("project", "date"),
    )
    proj_filter, proj_params = _project_filter("project", projects)
    params.extend(proj_params)

    return conn.execute(
        f"""
        SELECT project,
               SUM(commit_count) AS commits,
               COUNT(*) AS active_days,
               ROUND(AVG(commit_count), 1) AS avg_per_active_day
        FROM {relation}
        WHERE commit_count > 0 {proj_filter}
        GROUP BY project ORDER BY commits DESC
        """,
        params,
    ).fetchall()


def load_velocity_peak(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    projects: tuple[str, ...] | None = None,
) -> tuple[Any, ...] | None:
    """Return (project, date, commit_count) for the single peak day."""
    from lynchpin.substrate.graph import _logical_graph_relation

    relation, params = _logical_graph_relation(
        conn,
        refresh_id=refresh_id,
        table="project_day_correlation",
        columns=("project", "date", "commit_count"),
        key_columns=("project", "date"),
    )
    proj_filter, proj_params = _project_filter("project", projects)
    params.extend(proj_params)

    return conn.execute(
        f"""
        SELECT project, date, commit_count
        FROM {relation}
        WHERE TRUE {proj_filter}
        ORDER BY commit_count DESC, date, project LIMIT 1
        """,
        params,
    ).fetchone()


# ── symbol_velocity ───────────────────────────────────────────────────────────


def load_symbol_velocity_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    projects: tuple[str, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, date, commit_count, symbols_added, symbols_modified, symbols_renamed, symbols_total)."""
    from lynchpin.substrate.graph import _logical_graph_relation

    relation, relation_params = _logical_graph_relation(
        conn,
        refresh_id=refresh_id,
        table="project_day_correlation",
        columns=("project", "date", "commit_count"),
        key_columns=("project", "date"),
    )
    # Both sides are filtered before the FULL OUTER JOIN: a predicate in the
    # ON clause would keep every unmatched row of an unrelated project.
    proj_filter, proj_params = _project_filter("project", projects)
    params: list[Any] = [*relation_params, *proj_params, refresh_id, *proj_params]

    return conn.execute(
        f"""
        WITH p AS (SELECT * FROM {relation} WHERE TRUE {proj_filter}),
        sym AS (
            SELECT project, date,
                   SUM(CASE WHEN change_type = 'ADDED' THEN 1 ELSE 0 END) AS added,
                   SUM(CASE WHEN change_type = 'MODIFIED' THEN 1 ELSE 0 END) AS modified,
                   SUM(CASE WHEN change_type = 'RENAMED' THEN 1 ELSE 0 END) AS renamed,
                   COUNT(*) AS total
            FROM symbol_change
            WHERE refresh_id = ? {proj_filter}
            GROUP BY project, date
        )
        SELECT COALESCE(p.project, sym.project) AS project,
               COALESCE(p.date, sym.date) AS date,
               COALESCE(p.commit_count, 0) AS commit_count,
               COALESCE(sym.added, 0) AS symbols_added,
               COALESCE(sym.modified, 0) AS symbols_modified,
               COALESCE(sym.renamed, 0) AS symbols_renamed,
               COALESCE(sym.total, 0) AS symbols_total
        FROM p
        FULL OUTER JOIN sym ON p.project = sym.project AND p.date = sym.date
        ORDER BY project, date
        """,
        params,
    ).fetchall()


# ── temporal_rhythm ───────────────────────────────────────────────────────────


def load_commit_hourly_distribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (hour, count) rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT EXTRACT(HOUR FROM authored_at)::INTEGER AS hr,
               COUNT(*) AS cnt
        FROM commit_fact
        WHERE refresh_id = ? {proj_filter}
        GROUP BY hr ORDER BY hr
        """,
        params,
    ).fetchall()


def load_commit_weekday_distribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (dow, count) rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT EXTRACT(DOW FROM authored_at)::INTEGER AS dow,
               COUNT(*) AS cnt
        FROM commit_fact
        WHERE refresh_id = ? {proj_filter}
        GROUP BY dow ORDER BY dow
        """,
        params,
    ).fetchall()


# ── engineering_throughput ────────────────────────────────────────────────────


def load_commit_fact_window_bounds(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> tuple[Any, Any] | None:
    """Return (min_date, max_date) across all commit_fact rows for refresh_id."""
    return conn.execute(
        "SELECT MIN(authored_at::DATE), MAX(authored_at::DATE) "
        "FROM commit_fact WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()


def load_commit_fact_project_count(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str,
) -> int:
    """Return number of commit_fact rows for a specific project."""
    row = conn.execute(
        "SELECT COUNT(*) FROM commit_fact WHERE refresh_id = ? AND project = ?",
        [refresh_id, project],
    ).fetchone()
    return row[0] if row else 0


def load_commit_throughput_by_period(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str,
    granularity: str,
    grouping: str,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[Any, ...]]:
    """Return (period, n, lines_added, lines_deleted, files_changed) aggregated by granularity."""
    params: list[Any] = [refresh_id, project]
    date_filter = ""
    if start:
        date_filter += " AND authored_at::DATE >= ?"
        params.append(start)
    if end:
        date_filter += " AND authored_at::DATE <= ?"
        params.append(end)

    if grouping == "pr":
        sql = f"""
            WITH pr_commits AS (
                SELECT
                    COALESCE(
                        NULLIF(regexp_extract(subject, '\\(#(\\d+)\\)', 1), ''),
                        sha
                    ) AS group_key,
                    MAX(authored_at) AS authored_at,
                    SUM(lines_added) AS lines_added,
                    SUM(lines_deleted) AS lines_deleted,
                    SUM(files_changed) AS files_changed,
                    COUNT(*) AS commits_in_group
                FROM commit_fact
                WHERE refresh_id = ? AND project = ?{date_filter}
                GROUP BY group_key
            )
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   COUNT(*) AS n,
                   SUM(lines_added) AS la, SUM(lines_deleted) AS ld,
                   SUM(files_changed) AS fc
            FROM pr_commits
            GROUP BY period ORDER BY period
        """
    else:
        sql = f"""
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   COUNT(*) AS n,
                   SUM(lines_added) AS la, SUM(lines_deleted) AS ld,
                   SUM(files_changed) AS fc
            FROM commit_fact
            WHERE refresh_id = ? AND project = ?{date_filter}
            GROUP BY period ORDER BY period
        """
    return conn.execute(sql, params).fetchall()


def load_file_change_by_period(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str,
    granularity: str,
    grouping: str,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[Any, ...]]:
    """Return (period, lines_added, lines_deleted, path) file-level rows."""
    params: list[Any] = [refresh_id, project]
    date_filter = ""
    if start:
        date_filter += " AND authored_at::DATE >= ?"
        params.append(start)
    if end:
        date_filter += " AND authored_at::DATE <= ?"
        params.append(end)

    if grouping == "pr":
        sql = f"""
            WITH pr_files AS (
                SELECT
                    COALESCE(
                        NULLIF(regexp_extract(cf.subject, '\\(#(\\d+)\\)', 1), ''),
                        cf.sha
                    ) AS group_key,
                    MAX(cf.authored_at) AS authored_at,
                    fcf.path,
                    SUM(fcf.lines_added) AS la,
                    SUM(fcf.lines_deleted) AS ld
                FROM file_change_fact fcf
                JOIN commit_fact cf
                  ON fcf.sha = cf.sha
                 AND fcf.refresh_id = cf.refresh_id
                WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}
                GROUP BY group_key, fcf.path
            )
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   SUM(la) AS la, SUM(ld) AS ld, path
            FROM pr_files
            GROUP BY period, path
        """
    else:
        sql = f"""
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   SUM(lines_added) AS la, SUM(lines_deleted) AS ld, path
            FROM file_change_fact
            WHERE refresh_id = ? AND project = ?{date_filter}
            GROUP BY period, path
        """
    return conn.execute(sql, params).fetchall()


def load_symbol_change_by_period(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str,
    granularity: str,
    grouping: str,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[Any, ...]]:
    """Return (period, change_type, count) symbol-level rows."""
    params: list[Any] = [refresh_id, project]
    date_filter = ""
    if start:
        date_filter += " AND authored_at::DATE >= ?"
        params.append(start)
    if end:
        date_filter += " AND authored_at::DATE <= ?"
        params.append(end)

    if grouping == "pr":
        sql = f"""
            WITH pr_symbols AS (
                SELECT
                    COALESCE(
                        NULLIF(regexp_extract(cf.subject, '\\(#(\\d+)\\)', 1), ''),
                        cf.sha
                    ) AS group_key,
                    sc.change_type,
                    sc.qualified_name,
                    sc.path,
                    MAX(cf.authored_at) AS authored_at
                FROM symbol_change sc
                JOIN commit_fact cf
                  ON sc.sha = cf.sha
                 AND sc.refresh_id = cf.refresh_id
                WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}
                GROUP BY group_key, sc.change_type, sc.qualified_name, sc.path
            )
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   change_type, COUNT(*) AS n
            FROM pr_symbols
            GROUP BY period, change_type
        """
    else:
        sql = f"""
            SELECT date_trunc('{granularity}', authored_at)::DATE AS period,
                   change_type, COUNT(*) AS n
            FROM symbol_change sc
            JOIN commit_fact cf
              ON sc.sha = cf.sha
             AND sc.refresh_id = cf.refresh_id
            WHERE cf.refresh_id = ? AND cf.project = ?{date_filter}
            GROUP BY period, change_type
        """
    return conn.execute(sql, params).fetchall()


def load_best_coverage_refresh_id(
    conn: "duckdb.DuckDBPyConnection",
    *,
    project: str,
) -> str | None:
    """Choose refresh_id with best combined commit_fact + file_change_fact coverage."""
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


__all__ = [
    "load_graph_coverage_window",
    "load_velocity_series",
    "load_velocity_project_summary",
    "load_velocity_peak",
    "load_symbol_velocity_rows",
    "load_commit_hourly_distribution",
    "load_commit_weekday_distribution",
    "load_commit_fact_window_bounds",
    "load_commit_fact_project_count",
    "load_commit_throughput_by_period",
    "load_file_change_by_period",
    "load_symbol_change_by_period",
    "load_best_coverage_refresh_id",
]
