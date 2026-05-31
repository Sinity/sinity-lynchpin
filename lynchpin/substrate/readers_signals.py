"""Read-only substrate readers for cross-source signal MCP tools.

Extracted from lynchpin.mcp.tools.signals to keep tool functions thin and
the SQL in the typed reader layer. All functions are SELECT-only.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


# ── source_correlation ────────────────────────────────────────────────────────


def load_source_co_occurrence(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (source_a, source_b, co_occurring_days) cross-source co-occurrence rows."""
    return conn.execute(
        """
        WITH source_days AS (
            SELECT DISTINCT source, project, date
            FROM evidence_node
            WHERE refresh_id = ? AND project IS NOT NULL
        )
        SELECT a.source AS source_a, b.source AS source_b,
               COUNT(*) AS co_occurring_days
        FROM source_days a
        JOIN source_days b ON a.project=b.project AND a.date=b.date AND a.source<b.source
        GROUP BY a.source, b.source
        HAVING COUNT(*) >= 3
        ORDER BY co_occurring_days DESC
        """,
        [refresh_id],
    ).fetchall()


# ── cross_source_lag ──────────────────────────────────────────────────────────


def load_attributed_commit_count(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> int:
    """Return count of commits with non-NULL ai_attribution."""
    proj_filter = "AND c.project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM commit_fact c
        WHERE c.refresh_id = ?
          AND c.ai_attribution IS NOT NULL {proj_filter}
        """,
        params,
    ).fetchone()
    return row[0] if row else 0


def load_ai_commit_lag_stats(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    time_window_hours: int = 24,
    project: str | None = None,
) -> tuple[Any, ...] | None:
    """Return (count, min_h, median_h, mean_h, max_h) lag stats between AI events and commits."""
    proj_filter = "AND c.project = ?" if project else ""
    params: list[Any] = [refresh_id, int(time_window_hours) * 3600]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        WITH candidate_paths AS (
            SELECT
                c.sha,
                ABS(EXTRACT(EPOCH FROM c.authored_at - we.start_ts)) AS lag_seconds,
                cp.commit_path,
                ep.event_path
            FROM commit_fact c
            JOIN ai_work_event we
              ON c.refresh_id = we.refresh_id
             AND c.project = we.project
            , UNNEST(c.paths) AS cp(commit_path)
            , UNNEST(we.file_paths) AS ep(event_path)
            WHERE c.refresh_id = ?
              AND c.ai_attribution IS NOT NULL
              AND we.start_ts IS NOT NULL
              AND ABS(EXTRACT(EPOCH FROM c.authored_at - we.start_ts)) <= ?
              AND len(c.paths) > 0
              AND len(we.file_paths) > 0 {proj_filter}
        ),
        path_matches AS (
            SELECT DISTINCT sha, lag_seconds
            FROM candidate_paths
            WHERE
                ends_with(ltrim(event_path, '/'), ltrim(commit_path, '/'))
                OR ends_with(ltrim(commit_path, '/'), ltrim(event_path, '/'))
        )
        SELECT COUNT(*),
               ROUND(MIN(lag_seconds)/3600.0,1),
               ROUND(QUANTILE_CONT(lag_seconds,0.5)/3600.0,1),
               ROUND(AVG(lag_seconds)/3600.0,1),
               ROUND(MAX(lag_seconds)/3600.0,1)
        FROM path_matches
        """,
        params,
    ).fetchone()


# ── project_health ────────────────────────────────────────────────────────────


def load_project_health_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, commits, active_days, prs, avg_merge_hours, symbol_changes, daily_churn_rate)."""
    proj_filter = "AND p.project = ?" if project else ""
    params: list[Any] = [refresh_id, refresh_id, refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT p.project,
               COALESCE(SUM(p.commit_count),0) AS commits,
               COUNT(DISTINCT p.date) AS active_days,
               COALESCE(pr.pr_count,0) AS prs,
               COALESCE(ROUND(pr.avg_merge_hours,1),0) AS avg_merge_hours,
               COALESCE(sym.symbol_changes,0) AS symbol_changes,
               COALESCE(ROUND(sym.churn_rate,1),0) AS daily_churn_rate
        FROM project_day_correlation p
        LEFT JOIN (
            SELECT project, COUNT(*) AS pr_count,
                   AVG(time_to_merge_minutes)/60.0 AS avg_merge_hours
            FROM pr_review_row WHERE refresh_id=?
            GROUP BY project
        ) pr ON p.project=pr.project
        LEFT JOIN (
            SELECT project, COUNT(*) AS symbol_changes,
                   COUNT(*)*1.0/COUNT(DISTINCT date) AS churn_rate
            FROM symbol_change WHERE refresh_id=?
            GROUP BY project
        ) sym ON p.project=sym.project
        WHERE p.refresh_id=? AND p.commit_count>0 {proj_filter}
        GROUP BY p.project, pr.pr_count, pr.avg_merge_hours,
                 sym.symbol_changes, sym.churn_rate
        ORDER BY commits DESC
        """,
        params,
    ).fetchall()


# ── daily_rhythm_fingerprint ──────────────────────────────────────────────────


def load_commit_rhythm_fingerprint(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, morning, afternoon, evening, night, weekend, weekday, total) rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT project,
               SUM(CASE WHEN EXTRACT(HOUR FROM authored_at)::INTEGER BETWEEN 5 AND 11 THEN 1 ELSE 0 END) AS morning,
               SUM(CASE WHEN EXTRACT(HOUR FROM authored_at)::INTEGER BETWEEN 12 AND 16 THEN 1 ELSE 0 END) AS afternoon,
               SUM(CASE WHEN EXTRACT(HOUR FROM authored_at)::INTEGER BETWEEN 17 AND 21 THEN 1 ELSE 0 END) AS evening,
               SUM(CASE WHEN EXTRACT(HOUR FROM authored_at)::INTEGER >= 22
                         OR EXTRACT(HOUR FROM authored_at)::INTEGER <= 4 THEN 1 ELSE 0 END) AS night,
               -- DuckDB DOW: Sun=0, Mon=1, …, Sat=6. Weekend = Sat (6) + Sun (0).
               SUM(CASE WHEN EXTRACT(DOW FROM authored_at)::INTEGER IN (0, 6) THEN 1 ELSE 0 END) AS weekend,
               SUM(CASE WHEN EXTRACT(DOW FROM authored_at)::INTEGER BETWEEN 1 AND 5 THEN 1 ELSE 0 END) AS weekday,
               COUNT(*) AS total
        FROM commit_fact
        WHERE refresh_id = ? {proj_filter}
        GROUP BY project
        ORDER BY total DESC
        """,
        params,
    ).fetchall()


# ── operator_day_correlation ──────────────────────────────────────────────────


def load_operator_day_window(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> tuple[Any, ...] | None:
    """Return (min_date, max_date, count) for operator_day at refresh_id."""
    return conn.execute(
        "SELECT MIN(date), MAX(date), COUNT(*) FROM operator_day WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()


def load_operator_day_lag_correlation(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    metric_a: str,
    metric_b: str,
    lag: int,
) -> tuple[Any, ...] | None:
    """Return (pearson_r, n) for a specific lag between two operator_day metrics."""
    # Note: metric_a and metric_b are pre-validated against the whitelist in the tool.
    return conn.execute(
        f'''
        SELECT corr(b."{metric_b}", a."{metric_a}"), COUNT(*)
        FROM operator_day a
        JOIN operator_day b
          ON b.refresh_id = a.refresh_id
         AND b.date = a.date + INTERVAL ({lag}) DAY
        WHERE a.refresh_id = ?
          AND a."{metric_a}" IS NOT NULL
          AND b."{metric_b}" IS NOT NULL
        ''',
        [refresh_id],
    ).fetchone()


# ── operator_rhythm (personal.py) ─────────────────────────────────────────────


def load_commit_timestamps_in_range(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date,
    end: date,
    project: str | None = None,
) -> list[Any]:
    """Return authored_at timestamps for commits in date range."""
    proj_filter = " AND project = ?" if project else ""
    params: list[Any] = [refresh_id, start, end]
    if project:
        params.append(project)
    return [
        r[0]
        for r in conn.execute(
            f"SELECT authored_at FROM commit_fact "
            f"WHERE refresh_id = ? AND authored_at::DATE BETWEEN ? AND ?"
            f"{proj_filter}",
            params,
        ).fetchall()
    ]


def load_ai_work_event_timestamps_in_range(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date,
    end: date,
    project: str | None = None,
) -> list[Any]:
    """Return start_ts timestamps for ai_work_event rows in date range."""
    proj_filter = " AND project = ?" if project else ""
    params: list[Any] = [refresh_id, start, end]
    if project:
        params.append(project)
    return [
        r[0]
        for r in conn.execute(
            f"SELECT start_ts FROM ai_work_event "
            f"WHERE refresh_id = ? AND start_ts::DATE BETWEEN ? AND ? "
            f"AND start_ts IS NOT NULL{proj_filter}",
            params,
        ).fetchall()
    ]


def load_ai_session_timestamps_in_range(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date,
    end: date,
    project: str | None = None,
) -> list[Any]:
    """Return start_ts timestamps for evidence_node ai_session rows in date range."""
    node_filter = ""
    params: list[Any] = [refresh_id, start, end]
    if project:
        node_filter = " AND project = ?"
        params.append(project)
    return [
        r[0]
        for r in conn.execute(
            "SELECT start_ts FROM evidence_node "
            "WHERE refresh_id = ? AND kind = 'ai_session' "
            "AND start_ts IS NOT NULL "
            "AND start_ts::DATE BETWEEN ? AND ?"
            + node_filter,
            params,
        ).fetchall()
    ]


def load_pressure_timestamps_in_range(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date,
    end: date,
) -> list[Any]:
    """Return start_ts timestamps for machine_episode evidence nodes in date range."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT start_ts FROM evidence_node "
            "WHERE refresh_id = ? AND kind = 'machine_episode' "
            "AND start_ts IS NOT NULL AND start_ts::DATE BETWEEN ? AND ?",
            [refresh_id, start, end],
        ).fetchall()
    ]


# ── activity_semantic_daily (personal.py) ─────────────────────────────────────


def load_activity_title_usage_by_dimension(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date,
    end: date,
    dimension: str,
) -> list[tuple[Any, ...]]:
    """Return (first_date, dim_value, focused_seconds) grouped by dimension.

    Note: ``dimension`` is pre-validated by the caller against a whitelist.
    """
    sql = f"""
        SELECT
            CAST(first_date AS DATE) as date,
            COALESCE({dimension}, 'unknown') as dim_value,
            SUM(focused_seconds) as focused_seconds
        FROM activity_title_usage
        WHERE first_date <= ? AND last_date >= ?
        GROUP BY date, dim_value
        ORDER BY date, focused_seconds DESC
    """
    return conn.execute(sql, [end, start]).fetchall()


__all__ = [
    "load_source_co_occurrence",
    "load_attributed_commit_count",
    "load_ai_commit_lag_stats",
    "load_project_health_rows",
    "load_commit_rhythm_fingerprint",
    "load_operator_day_window",
    "load_operator_day_lag_correlation",
    "load_commit_timestamps_in_range",
    "load_ai_work_event_timestamps_in_range",
    "load_ai_session_timestamps_in_range",
    "load_pressure_timestamps_in_range",
    "load_activity_title_usage_by_dimension",
]
