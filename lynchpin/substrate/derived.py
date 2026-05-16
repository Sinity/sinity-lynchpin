"""Derived substrate view row readers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_in_filter, build_where

if TYPE_CHECKING:
    import duckdb

# ---------------------------------------------------------------------------
# project_day_correlation  (Arc 2.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectDayCorrelationRow:
    project: str
    date: date
    refresh_id: str
    commit_count: int
    ai_session_count: int
    ai_work_event_count: int
    github_item_count: int
    focus_count: int
    terminal_count: int
    raw_log_count: int
    commit_shas: tuple[str, ...]
    conversation_ids: tuple[str, ...]
    github_node_ids: tuple[str, ...]
    focus_minutes: float
    shell_minutes: float
    source_count: int


def load_project_day_correlations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    min_source_count: int | None = None,
) -> list[ProjectDayCorrelationRow]:
    """Read project_day_correlation rows. Filters compose with AND.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).

    ``min_source_count=2`` surfaces only project-days with cross-source support.
    ``focus_seconds`` and ``shell_seconds`` from the view are divided by 60
    to produce ``focus_minutes`` / ``shell_minutes`` on the returned dataclass.
    DuckDB ARRAY_AGG results (Python list) are converted to tuple; NULL arrays
    become empty tuples.
    """
    from lynchpin.substrate.views import ensure_views

    ensure_views(conn)

    clauses: list[str] = []
    params: list[Any] = []

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if start is not None and end is not None:
        clauses.append("date BETWEEN ? AND ?")
        params.extend([start, end])
    elif start is not None:
        clauses.append("date >= ?")
        params.append(start)
    elif end is not None:
        clauses.append("date <= ?")
        params.append(end)
    add_in_filter("project", projects, clauses, params)
    if min_source_count is not None:
        clauses.append("source_count >= ?")
        params.append(min_source_count)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            project, date, refresh_id,
            commit_count, ai_session_count, ai_work_event_count,
            github_item_count, focus_count, terminal_count,
            raw_log_count,
            commit_shas, conversation_ids, github_node_ids,
            focus_seconds, shell_seconds, source_count
        FROM project_day_correlation
        {where}
        ORDER BY date, project
    """
    rows = conn.execute(sql, params).fetchall()

    results: list[ProjectDayCorrelationRow] = []
    for (
        proj,
        row_date,
        rid,
        commit_count,
        ai_session_count,
        ai_work_event_count,
        github_item_count,
        focus_count,
        terminal_count,
        raw_log_count,
        commit_shas,
        conversation_ids,
        github_node_ids,
        focus_seconds,
        shell_seconds,
        source_count,
    ) in rows:
        results.append(
            ProjectDayCorrelationRow(
                project=proj,
                date=row_date,
                refresh_id=rid,
                commit_count=commit_count or 0,
                ai_session_count=ai_session_count or 0,
                ai_work_event_count=ai_work_event_count or 0,
                github_item_count=github_item_count or 0,
                focus_count=focus_count or 0,
                terminal_count=terminal_count or 0,
                raw_log_count=raw_log_count or 0,
                commit_shas=tuple(s for s in (commit_shas or []) if s is not None),
                conversation_ids=tuple(
                    s for s in (conversation_ids or []) if s is not None
                ),
                github_node_ids=tuple(
                    s for s in (github_node_ids or []) if s is not None
                ),
                focus_minutes=(focus_seconds or 0.0) / 60.0,
                shell_minutes=(shell_seconds or 0.0) / 60.0,
                source_count=source_count or 0,
            )
        )
    return results


# ---------------------------------------------------------------------------
# issue_closure_chain_walk  (Arc 2.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueClosureChainWalkRow:
    refresh_id: str
    root_id: str
    project: str
    issue_number: str | None
    reachable_node_ids: tuple[str, ...]
    chain_depth: int
    reachable_count: int


def load_issue_closure_chain_walks(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    project: str | None = None,
    min_chain_depth: int | None = None,
) -> list[IssueClosureChainWalkRow]:
    """Read issue_closure_chain_walk rows from the recursive CTE.

    Calls ``ensure_views`` first (idempotent CREATE OR REPLACE).

    Surfaces the structural shape of closure chains (which nodes are reachable
    from which issue) for downstream classification by
    ``lynchpin/graph/issue_closure_chain.py``.  The Python layer still owns
    status classification (complete/partial/broken/orphaned).

    ``issue_number`` is returned as VARCHAR (the view coalesces the JSON field to
    a string regardless of whether it was stored as integer or string in the
    payload).  The reachable_node_ids DuckDB list is converted to tuple; NULL
    arrays become empty tuples.
    """
    from lynchpin.substrate.views import ensure_views

    ensure_views(conn)

    clauses: list[str] = []
    params: list[Any] = []

    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    if min_chain_depth is not None:
        clauses.append("chain_depth >= ?")
        params.append(min_chain_depth)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            refresh_id, root_id, project, issue_number,
            reachable_node_ids, chain_depth, reachable_count
        FROM issue_closure_chain_walk
        {where}
        ORDER BY project, root_id
    """
    rows = conn.execute(sql, params).fetchall()

    results: list[IssueClosureChainWalkRow] = []
    for (
        rid,
        root_id,
        proj,
        issue_number,
        reachable_node_ids,
        chain_depth,
        reachable_count,
    ) in rows:
        results.append(
            IssueClosureChainWalkRow(
                refresh_id=rid,
                root_id=root_id,
                project=proj,
                issue_number=issue_number,
                reachable_node_ids=tuple(reachable_node_ids or []),
                chain_depth=chain_depth or 0,
                reachable_count=reachable_count or 0,
            )
        )
    return results


__all__ = [
    "IssueClosureChainWalkRow",
    "ProjectDayCorrelationRow",
    "load_issue_closure_chain_walks",
    "load_project_day_correlations",
]
