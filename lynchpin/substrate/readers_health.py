"""Read-only substrate readers for health/audit MCP tools.

Extracted from lynchpin.mcp.tools.health to keep tool functions thin and
the SQL in the typed reader layer. All functions are SELECT-only; the
connection must be obtained with read_only=True by the caller.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


# ── substrate_gap_draft helpers ───────────────────────────────────────────────


def load_source_gap_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (source, status, reason, row_count) for unavailable/error sources."""
    return conn.execute(
        "SELECT source, status, reason, row_count "
        "FROM substrate_source_status "
        "WHERE refresh_id = ? AND status IN ('unavailable', 'error') "
        "ORDER BY source",
        [refresh_id],
    ).fetchall()


# ── substrate_confidence_matrix ───────────────────────────────────────────────


def load_evidence_node_by_source(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (source, node_count, project_count, date_span_days, has_caveats)."""
    return conn.execute(
        """
        SELECT
            source,
            COUNT(*) AS node_count,
            COUNT(DISTINCT project) AS project_count,
            COALESCE(DATE_DIFF('day', MIN(date), MAX(date)), 0) AS date_span_days,
            COALESCE(SUM(CASE WHEN json_array_length(caveats) > 0 THEN 1 ELSE 0 END), 0) > 0 AS has_caveats
        FROM evidence_node
        WHERE refresh_id = ?
        GROUP BY source
        ORDER BY node_count DESC
        """,
        [refresh_id],
    ).fetchall()


def load_source_status_map(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (source, status) pairs for a refresh_id."""
    return conn.execute(
        "SELECT source, status FROM substrate_source_status WHERE refresh_id = ?",
        [refresh_id],
    ).fetchall()


# ── kind_audit ────────────────────────────────────────────────────────────────


def load_ai_work_event_count(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> int:
    """Return total row count for ai_work_event at refresh_id."""
    row = conn.execute(
        "SELECT COUNT(*) FROM ai_work_event WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()
    return row[0] if row else 0


def load_ai_work_event_tier_distribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (kind_tier, count) pairs."""
    return conn.execute(
        "SELECT kind_tier, COUNT(*) FROM ai_work_event "
        "WHERE refresh_id = ? GROUP BY kind_tier",
        [refresh_id],
    ).fetchall()


def load_ai_work_event_source_distribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (kind_source, count) pairs."""
    return conn.execute(
        "SELECT kind_source, COUNT(*) FROM ai_work_event "
        "WHERE refresh_id = ? GROUP BY kind_source",
        [refresh_id],
    ).fetchall()


def load_ai_work_event_disagreements(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return top-10 (kind, source_kind, overlay_kind, count) disagreement rows."""
    return conn.execute(
        """
        SELECT kind, source_kind, overlay_kind, COUNT(*) AS cnt
        FROM ai_work_event
        WHERE refresh_id = ?
          AND source_kind IS NOT NULL
          AND overlay_kind IS NOT NULL
          AND source_kind != overlay_kind
        GROUP BY kind, source_kind, overlay_kind
        ORDER BY cnt DESC LIMIT 10
        """,
        [refresh_id],
    ).fetchall()


def load_ai_work_event_per_kind_confidence(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return top-20 (kind, count, avg_confidence) rows."""
    return conn.execute(
        """
        SELECT kind, COUNT(*) AS cnt,
               ROUND(AVG(kind_confidence), 2) AS avg_conf
        FROM ai_work_event
        WHERE refresh_id = ? AND kind IS NOT NULL
        GROUP BY kind ORDER BY cnt DESC LIMIT 20
        """,
        [refresh_id],
    ).fetchall()


# ── work_package_durability ───────────────────────────────────────────────────


def load_symbol_change_count(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> int:
    """Return total symbol_change row count."""
    row = conn.execute(
        "SELECT COUNT(*) FROM symbol_change WHERE refresh_id = ?",
        [refresh_id],
    ).fetchone()
    return row[0] if row else 0


def load_symbol_survival_by_project_day(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    min_symbols: int,
) -> list[tuple[Any, ...]]:
    """Return (project, date, total_syms, surviving) per project-day."""
    return conn.execute(
        """
        WITH ranked AS (
            SELECT project, date, qualified_name, change_type,
                   ROW_NUMBER() OVER (
                       PARTITION BY qualified_name
                       ORDER BY date DESC, sha DESC
                   ) AS rn
            FROM symbol_change
            WHERE refresh_id = ?
        ),
        latest AS (
            SELECT project, date, qualified_name, change_type
            FROM ranked WHERE rn = 1
        )
        SELECT project, date,
               COUNT(*) AS total_syms,
               SUM(CASE WHEN change_type != 'DELETED' THEN 1 ELSE 0 END) AS surviving
        FROM latest
        GROUP BY project, date
        HAVING COUNT(*) >= ?
        ORDER BY date, project
        """,
        [refresh_id, int(min_symbols)],
    ).fetchall()


# ── evidence_confidence ───────────────────────────────────────────────────────


def load_evidence_node_source_caveats(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (source, node_count, caveated_pct) rows."""
    return conn.execute(
        """
        SELECT source,
               COUNT(*) AS node_count,
               ROUND(SUM(CASE WHEN json_array_length(caveats) > 0 THEN 1 ELSE 0 END)
                     * 100.0 / COUNT(*), 1) AS caveated_pct
        FROM evidence_node
        WHERE refresh_id = ?
        GROUP BY source
        ORDER BY node_count DESC
        """,
        [refresh_id],
    ).fetchall()


# ── source_anomalies ──────────────────────────────────────────────────────────


def load_project_day_anomaly_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (project, date, commit_count, ai_work_event_count, focus_seconds, source_count)."""
    return conn.execute(
        """
        SELECT project, date, commit_count, ai_work_event_count,
               focus_seconds, source_count
        FROM project_day_correlation
        WHERE refresh_id = ? AND source_count >= 1
        """,
        [refresh_id],
    ).fetchall()


# ── health_trend ──────────────────────────────────────────────────────────────


def load_ordered_refresh_ids(
    conn: "duckdb.DuckDBPyConnection",
) -> list[str]:
    """Return materialized substrate refresh_ids ordered oldest to newest."""
    from lynchpin.substrate.snapshots import ordered_materialized_refresh_ids

    return ordered_materialized_refresh_ids(conn, caller="load_ordered_refresh_ids")


def load_source_status_by_refresh(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (status, count) pairs for a refresh_id."""
    return conn.execute(
        "SELECT status, COUNT(*) FROM substrate_source_status "
        "WHERE refresh_id = ? GROUP BY status",
        [refresh_id],
    ).fetchall()


# ── cleanup_period_detect ─────────────────────────────────────────────────────


def load_commits_by_month(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date,
    end: date,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (year_month, commit_count) from project_day_correlation."""
    sql = """
        SELECT
            strftime('%Y-%m', date) as year_month,
            COUNT(*) as commit_count
        FROM project_day_correlation
        WHERE date >= ? AND date <= ?
    """
    params: list[Any] = [start, end]
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " GROUP BY year_month ORDER BY year_month"
    return conn.execute(sql, params).fetchall()


def load_ai_messages_by_month(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date,
    end: date,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (year_month, ai_messages) from project_day_correlation."""
    sql = """
        SELECT
            strftime('%Y-%m', date) as year_month,
            SUM(ai_work_event_count) as ai_messages
        FROM project_day_correlation
        WHERE date >= ? AND date <= ?
    """
    params: list[Any] = [start, end]
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " GROUP BY year_month ORDER BY year_month"
    return conn.execute(sql, params).fetchall()


__all__ = [
    "load_source_gap_rows",
    "load_evidence_node_by_source",
    "load_source_status_map",
    "load_ai_work_event_count",
    "load_ai_work_event_tier_distribution",
    "load_ai_work_event_source_distribution",
    "load_ai_work_event_disagreements",
    "load_ai_work_event_per_kind_confidence",
    "load_symbol_change_count",
    "load_symbol_survival_by_project_day",
    "load_evidence_node_source_caveats",
    "load_project_day_anomaly_rows",
    "load_ordered_refresh_ids",
    "load_source_status_by_refresh",
    "load_commits_by_month",
    "load_ai_messages_by_month",
]
