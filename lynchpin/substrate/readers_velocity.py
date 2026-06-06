"""Read-only substrate readers for velocity MCP tools.

Extracted from lynchpin.mcp.tools.velocity to keep tool functions thin and
the SQL in the typed reader layer. All functions are SELECT-only.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


# ── velocity_series ───────────────────────────────────────────────────────────


def load_velocity_series(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    window_days: int = 7,
    projects: tuple[str, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, date, commit_count, rolling_avg, cumulative, source_count) rows."""
    proj_filter = ""
    params: list[Any] = [refresh_id]
    if projects:
        placeholders = ",".join(["?"] * len(projects))
        proj_filter = f"AND project IN ({placeholders})"
        params.extend(projects)

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
    return conn.execute(sql, params).fetchall()


# ── velocity_narrative ────────────────────────────────────────────────────────


def load_velocity_window(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> tuple[Any, Any] | None:
    """Return (min_date, max_date) for the refresh window."""
    return conn.execute(
        """
        SELECT MIN(date), MAX(date)
        FROM project_day_correlation WHERE refresh_id = ?
        """,
        [refresh_id],
    ).fetchone()


def load_velocity_project_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    projects: tuple[str, ...] | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, commits, active_days, avg_daily) per project."""
    proj_filter = ""
    params: list[Any] = [refresh_id]
    if projects:
        placeholders = ",".join(["?"] * len(projects))
        proj_filter = f"AND project IN ({placeholders})"
        params.extend(projects)

    return conn.execute(
        f"""
        SELECT project,
               SUM(commit_count) AS commits,
               COUNT(*) AS active_days,
               ROUND(AVG(commit_count), 1) AS avg_daily
        FROM project_day_correlation
        WHERE refresh_id = ? AND commit_count > 0 {proj_filter}
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
    proj_filter = ""
    params: list[Any] = [refresh_id]
    if projects:
        placeholders = ",".join(["?"] * len(projects))
        proj_filter = f"AND project IN ({placeholders})"
        params.extend(projects)

    return conn.execute(
        f"""
        SELECT project, date, commit_count
        FROM project_day_correlation
        WHERE refresh_id = ? {proj_filter}
        ORDER BY commit_count DESC LIMIT 1
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
    outer_filter = ""
    inner_filter = ""
    params: list[Any] = [refresh_id, refresh_id]
    if projects:
        placeholders = ",".join(["?"] * len(projects))
        outer_filter = f"AND p.project IN ({placeholders})"
        inner_filter = f"AND project IN ({placeholders})"
        params = [refresh_id, *projects, refresh_id, *projects]

    return conn.execute(
        f"""
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
    "load_velocity_series",
    "load_velocity_window",
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
