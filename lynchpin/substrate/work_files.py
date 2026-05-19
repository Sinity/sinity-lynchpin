"""File-change table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def load_file_change_facts(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    projects: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:  # list[GitFileChangeFact]
    """SELECT and hydrate ``file_change_fact`` rows to ``GitFileChangeFact``."""
    from lynchpin.sources.git import GitFileChangeFact

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("authored_at", start, end, clauses, params)
    add_in_filter("project", projects, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            sha, repo, authored_at, path, path_root,
            lines_added, lines_deleted, lines_changed
        FROM file_change_fact
        {where}
        ORDER BY authored_at, sha, path
    """
    rows = conn.execute(sql, params).fetchall()

    results: list[Any] = []
    for (
        sha,
        repo,
        authored_at,
        path,
        path_root,
        lines_added,
        lines_deleted,
        lines_changed,
    ) in rows:
        results.append(
            GitFileChangeFact(
                repo=repo,
                commit=sha,
                authored_at=authored_at,
                path=path,
                path_root=path_root or "",
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                lines_changed=lines_changed,
            )
        )
    return results


# ---------------------------------------------------------------------------
# ai_work_event
# ---------------------------------------------------------------------------


_FILE_CHANGE_COLUMNS = (
    "sha", "repo", "project", "authored_at", "path", "path_root",
    "lines_added", "lines_deleted", "lines_changed",
    "change_type", "previous_path",
)


def promote_file_changes(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    facts: Iterable[Any],  # Iterable[GitFileChangeFact]
    project_lookup: Callable[[str], str | None] | None = None,
    annotations: Mapping[tuple[str, str], dict[str, Any]] | None = None,
) -> int:
    """INSERT file-change rows, idempotent on refresh_id.

    ``annotations`` is a mapping of (sha, path) → dict with keys
    change_type, status_code, previous_path from the JSON source.
    """
    ann = annotations or {}

    def extract(f: Any) -> tuple[Any, ...]:
        proj = project_lookup(f.repo) if project_lookup else f.repo
        a = ann.get((f.commit, f.path), {})
        return (
            f.commit, f.repo, proj, f.authored_at, f.path, f.path_root,
            f.lines_added, f.lines_deleted, f.lines_changed,
            a.get("change_type") or a.get("status_code"),
            a.get("previous_path"),
        )

    return promote_rows(
        conn,
        table="file_change_fact",
        columns=_FILE_CHANGE_COLUMNS,
        refresh_id=refresh_id,
        rows=facts,
        extractor=extract,
    )


# ── ai_work_event ─────────────────────────────────────────────────────────────

__all__ = ["load_file_change_facts", "promote_file_changes"]
