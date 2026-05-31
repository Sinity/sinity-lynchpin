"""Read-only substrate readers for code-change MCP tools.

Extracted from lynchpin.mcp.tools.change to keep tool functions thin and
the SQL in the typed reader layer. All functions are SELECT-only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb


# ── refactor_candidates ───────────────────────────────────────────────────────


def load_renamed_symbols(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, qualified_name, date, sha, path) for RENAMED symbols."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT project, qualified_name, date, sha, path
        FROM symbol_change
        WHERE refresh_id = ? AND change_type = 'RENAMED' {proj_filter}
        ORDER BY date
        """,
        params,
    ).fetchall()


def load_added_deleted_symbol_pairs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, old_name, new_name, date, sha) added/deleted pairs."""
    proj_filter = "AND project = ?" if project else ""
    proj_args = [project] if project else []

    return conn.execute(
        f"""
        WITH added AS (
            SELECT project, qualified_name, date, sha
            FROM symbol_change
            WHERE refresh_id = ? AND change_type = 'ADDED' {proj_filter}
        ),
        deleted AS (
            SELECT project, qualified_name, date, sha
            FROM symbol_change
            WHERE refresh_id = ? AND change_type = 'DELETED' {proj_filter}
        )
        SELECT a.project, d.qualified_name AS old_name,
               a.qualified_name AS new_name,
               a.date, a.sha
        FROM added a
        JOIN deleted d ON a.project = d.project
        WHERE a.date >= d.date
        ORDER BY a.date
        LIMIT 500
        """,
        [refresh_id, *proj_args, refresh_id, *proj_args],
    ).fetchall()


# ── file_hotspots ─────────────────────────────────────────────────────────────


def load_file_churn_hotspots(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    top_n: int = 20,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (path_root, commits, file_changes, project_count, top_project) rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)
    params.append(int(top_n))

    return conn.execute(
        f"""
        SELECT path_root,
               COUNT(DISTINCT sha) AS commits,
               COUNT(*) AS file_changes,
               COUNT(DISTINCT project) AS project_count,
               MODE(project) AS top_project
        FROM file_change_fact
        WHERE refresh_id = ? AND path_root IS NOT NULL
          AND path_root != '' {proj_filter}
        GROUP BY path_root
        ORDER BY commits DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


# ── conventional_commits ──────────────────────────────────────────────────────


def load_conventional_commit_distribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, conventional_kind, count, pct) rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT project, conventional_kind, COUNT(*) AS cnt,
               ROUND(COUNT(*)*100.0/SUM(COUNT(*)) OVER(PARTITION BY project), 1) AS pct
        FROM commit_fact
        WHERE refresh_id = ? AND conventional_kind IS NOT NULL {proj_filter}
        GROUP BY project, conventional_kind
        ORDER BY project, cnt DESC
        """,
        params,
    ).fetchall()


# ── breaking_changes ──────────────────────────────────────────────────────────


def load_breaking_change_commits(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (project, sha, subject, authored_at) for breaking change commits."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)

    return conn.execute(
        f"""
        SELECT project, sha, subject, authored_at
        FROM commit_fact
        WHERE refresh_id = ? AND breaking_change = TRUE {proj_filter}
        ORDER BY authored_at DESC
        """,
        params,
    ).fetchall()


# ── commit_kind_attribution ───────────────────────────────────────────────────


def load_ai_attribution_count(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> int:
    """Return count of commit_fact rows with non-NULL ai_attribution."""
    row = conn.execute(
        "SELECT COUNT(*) FROM commit_fact "
        "WHERE refresh_id = ? AND ai_attribution IS NOT NULL",
        [refresh_id],
    ).fetchone()
    return row[0] if row else 0


def load_commit_kind_ai_attribution(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
) -> list[tuple[Any, ...]]:
    """Return (conventional_kind, total, ai_assisted, ai_pct) rows."""
    return conn.execute(
        """
        SELECT conventional_kind, COUNT(*) AS total,
               SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END) AS ai_assisted,
               ROUND(SUM(CASE WHEN ai_attribution IS NOT NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS ai_pct
        FROM commit_fact
        WHERE refresh_id = ? AND conventional_kind IS NOT NULL
        GROUP BY conventional_kind
        ORDER BY total DESC
        """,
        [refresh_id],
    ).fetchall()


# ── symbol_churn_hotspots ─────────────────────────────────────────────────────


def load_symbol_churn_hotspots(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    top_n: int = 20,
    project: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return (path, symbols, commits, changes, projects) symbol churn rows."""
    proj_filter = "AND project = ?" if project else ""
    params: list[Any] = [refresh_id]
    if project:
        params.append(project)
    params.append(int(top_n))

    return conn.execute(
        f"""
        SELECT path,
               COUNT(DISTINCT qualified_name) AS symbols,
               COUNT(DISTINCT sha) AS commits,
               COUNT(*) AS changes,
               COUNT(DISTINCT project) AS projects
        FROM symbol_change
        WHERE refresh_id = ? AND path IS NOT NULL AND path != ''
          {proj_filter}
        GROUP BY path
        ORDER BY symbols DESC
        LIMIT ?
        """,
        params,
    ).fetchall()


__all__ = [
    "load_renamed_symbols",
    "load_added_deleted_symbol_pairs",
    "load_file_churn_hotspots",
    "load_conventional_commit_distribution",
    "load_breaking_change_commits",
    "load_ai_attribution_count",
    "load_commit_kind_ai_attribution",
    "load_symbol_churn_hotspots",
]
