"""GitHub issue and PR table readers and promoters for the DuckDB substrate.

Six tables: github_issue, github_issue_comment, github_pr,
github_pr_comment, github_pr_review, github_pr_review_comment.
All use refresh_id='latest' for overwrite semantics.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

REFRESH_ID = "latest"


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ─── github_issue ─────────────────────────────────────────────────────────────

_ISSUE_COLUMNS = (
    "project", "number", "title", "body", "state", "author",
    "labels", "comment_count", "created_at", "updated_at",
    "closed_at", "url",
)


def _issue_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("number") or 0),
        r.get("title"),
        r.get("body"),
        r.get("state"),
        r.get("author"),
        list(r.get("labels") or []),
        int(r.get("comment_count") or 0),
        _parse_iso(r.get("created_at")),
        _parse_iso(r.get("updated_at")),
        _parse_iso(r.get("closed_at")),
        r.get("url"),
    )


def promote_github_issues(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_issue",
        columns=_ISSUE_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_issue_extract,
    )


# ─── github_issue_comment ─────────────────────────────────────────────────────

_ISSUE_COMMENT_COLUMNS = (
    "project", "issue_number", "comment_idx", "author",
    "body", "created_at", "url",
)


def _issue_comment_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("issue_number") or 0),
        int(r.get("comment_idx") or 0),
        r.get("author"),
        r.get("body"),
        _parse_iso(r.get("created_at")),
        r.get("url"),
    )


def promote_github_issue_comments(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_issue_comment",
        columns=_ISSUE_COMMENT_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_issue_comment_extract,
    )


# ─── github_pr ────────────────────────────────────────────────────────────────

_PR_COLUMNS = (
    "project", "number", "title", "body", "state", "author",
    "labels", "merge_commit", "review_decision",
    "comment_count", "review_count", "review_comment_count",
    "created_at", "updated_at", "closed_at", "merged_at", "url",
)


def _pr_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("number") or 0),
        r.get("title"),
        r.get("body"),
        r.get("state"),
        r.get("author"),
        list(r.get("labels") or []),
        r.get("merge_commit"),
        r.get("review_decision"),
        int(r.get("comment_count") or 0),
        int(r.get("review_count") or 0),
        int(r.get("review_comment_count") or 0),
        _parse_iso(r.get("created_at")),
        _parse_iso(r.get("updated_at")),
        _parse_iso(r.get("closed_at")),
        _parse_iso(r.get("merged_at")),
        r.get("url"),
    )


def promote_github_prs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_pr",
        columns=_PR_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_pr_extract,
    )


# ─── github_pr_comment ────────────────────────────────────────────────────────

_PR_COMMENT_COLUMNS = (
    "project", "pr_number", "comment_idx", "author",
    "body", "created_at", "url",
)


def _pr_comment_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("pr_number") or 0),
        int(r.get("comment_idx") or 0),
        r.get("author"),
        r.get("body"),
        _parse_iso(r.get("created_at")),
        r.get("url"),
    )


def promote_github_pr_comments(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_pr_comment",
        columns=_PR_COMMENT_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_pr_comment_extract,
    )


# ─── github_pr_review ─────────────────────────────────────────────────────────

_PR_REVIEW_COLUMNS = (
    "project", "pr_number", "review_idx", "author",
    "state", "body", "submitted_at", "url",
)


def _pr_review_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("pr_number") or 0),
        int(r.get("review_idx") or 0),
        r.get("author"),
        r.get("state"),
        r.get("body"),
        _parse_iso(r.get("submitted_at")),
        r.get("url"),
    )


def promote_github_pr_reviews(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_pr_review",
        columns=_PR_REVIEW_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_pr_review_extract,
    )


# ─── github_pr_review_comment ─────────────────────────────────────────────────

_PR_REVIEW_COMMENT_COLUMNS = (
    "project", "pr_number", "comment_idx", "author",
    "body", "path", "line", "diff_hunk", "created_at", "url",
)


def _pr_review_comment_extract(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        r.get("project") or "",
        int(r.get("pr_number") or 0),
        int(r.get("comment_idx") or 0),
        r.get("author"),
        r.get("body"),
        r.get("path"),
        r.get("line"),
        r.get("diff_hunk"),
        _parse_iso(r.get("created_at")),
        r.get("url"),
    )


def promote_github_pr_review_comments(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    return promote_rows(
        conn,
        table="github_pr_review_comment",
        columns=_PR_REVIEW_COMMENT_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=_pr_review_comment_extract,
    )


# ─── Readers ──────────────────────────────────────────────────────────────────

def iter_github_issues(
    conn: "duckdb.DuckDBPyConnection",
    *,
    project: str | None = None,
    state: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield github_issue rows as dicts for the current 'latest' refresh."""
    sql = "SELECT * FROM github_issue WHERE refresh_id = ?"
    params: list[Any] = [REFRESH_ID]
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    if state is not None:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY project, number"
    result = conn.execute(sql, params)
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def iter_github_prs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    project: str | None = None,
    state: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield github_pr rows as dicts for the current 'latest' refresh."""
    sql = "SELECT * FROM github_pr WHERE refresh_id = ?"
    params: list[Any] = [REFRESH_ID]
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    if state is not None:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY project, number"
    result = conn.execute(sql, params)
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def get_github_issue(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> dict[str, Any] | None:
    """Return a single github_issue row as a dict, or None if not found."""
    result = conn.execute(
        "SELECT * FROM github_issue"
        " WHERE project = ? AND number = ? AND refresh_id = ?",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    row = result.fetchone()
    return dict(zip(cols, row)) if row else None


def get_github_pr(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> dict[str, Any] | None:
    """Return a single github_pr row as a dict, or None if not found."""
    result = conn.execute(
        "SELECT * FROM github_pr"
        " WHERE project = ? AND number = ? AND refresh_id = ?",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    row = result.fetchone()
    return dict(zip(cols, row)) if row else None


def iter_github_issue_comments(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> Iterator[dict[str, Any]]:
    """Yield comments for a specific issue, ordered by comment_idx."""
    result = conn.execute(
        "SELECT * FROM github_issue_comment"
        " WHERE project = ? AND issue_number = ? AND refresh_id = ?"
        " ORDER BY comment_idx",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def iter_github_pr_comments(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> Iterator[dict[str, Any]]:
    """Yield top-level comments for a specific PR, ordered by comment_idx."""
    result = conn.execute(
        "SELECT * FROM github_pr_comment"
        " WHERE project = ? AND pr_number = ? AND refresh_id = ?"
        " ORDER BY comment_idx",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def iter_github_pr_reviews(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> Iterator[dict[str, Any]]:
    """Yield review submissions for a specific PR, ordered by review_idx."""
    result = conn.execute(
        "SELECT * FROM github_pr_review"
        " WHERE project = ? AND pr_number = ? AND refresh_id = ?"
        " ORDER BY review_idx",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def iter_github_pr_review_comments(
    conn: "duckdb.DuckDBPyConnection",
    project: str,
    number: int,
) -> Iterator[dict[str, Any]]:
    """Yield inline review comments for a specific PR, ordered by comment_idx."""
    result = conn.execute(
        "SELECT * FROM github_pr_review_comment"
        " WHERE project = ? AND pr_number = ? AND refresh_id = ?"
        " ORDER BY comment_idx",
        [project, number, REFRESH_ID],
    )
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


__all__ = [
    "REFRESH_ID",
    "promote_github_issues",
    "promote_github_issue_comments",
    "promote_github_prs",
    "promote_github_pr_comments",
    "promote_github_pr_reviews",
    "promote_github_pr_review_comments",
    "iter_github_issues",
    "iter_github_prs",
    "get_github_issue",
    "get_github_pr",
    "iter_github_issue_comments",
    "iter_github_pr_comments",
    "iter_github_pr_reviews",
    "iter_github_pr_review_comments",
]
