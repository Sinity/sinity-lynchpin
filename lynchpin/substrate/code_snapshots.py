"""Substrate promoters and readers for code_snapshot_run / code_snapshot_slice.

Uses refresh_id='latest' for overwrite semantics: each promotion deletes the
prior 'latest' partition and inserts a fresh one, so the substrate always
holds the single most-recent chisel run per project.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import duckdb


_RUN_COLUMNS: tuple[str, ...] = (
    "project",
    "run_at",
    "git_commit",
    "git_branch",
    "git_dirty",
    "issues_open",
    "issues_closed",
    "gitlog_commits",
    "xml_valid",
    "elapsed_s",
    "status",
    "errors",
    "output_dir",
    "total_bytes",
)

_SLICE_COLUMNS: tuple[str, ...] = (
    "project",
    "filename",
    "kind",
    "size_bytes",
    "path",
)

REFRESH_ID = "latest"


def promote_code_snapshot_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    """Promote code_snapshot_run rows using refresh_id='latest'."""
    from lynchpin.substrate._helpers import promote_rows

    return promote_rows(
        conn,
        table="code_snapshot_run",
        columns=_RUN_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=lambda r: tuple(r.get(c) for c in _RUN_COLUMNS),
    )


def promote_code_snapshot_slices(
    conn: "duckdb.DuckDBPyConnection",
    *,
    rows: list[dict[str, Any]],
) -> int:
    """Promote code_snapshot_slice rows using refresh_id='latest'."""
    from lynchpin.substrate._helpers import promote_rows

    return promote_rows(
        conn,
        table="code_snapshot_slice",
        columns=_SLICE_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=lambda r: tuple(r.get(c) for c in _SLICE_COLUMNS),
    )


def iter_code_snapshot_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    project: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield code_snapshot_run rows as dicts (most recent run per project)."""
    sql = "SELECT * FROM code_snapshot_run WHERE refresh_id = 'latest'"
    params: list[Any] = []
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY project"
    result = conn.execute(sql, params)
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def iter_code_snapshot_slices(
    conn: "duckdb.DuckDBPyConnection",
    *,
    project: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield code_snapshot_slice rows as dicts, optionally filtered by project."""
    sql = "SELECT * FROM code_snapshot_slice WHERE refresh_id = 'latest'"
    params: list[Any] = []
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY project, kind, filename"
    result = conn.execute(sql, params)
    cols = [d[0] for d in (result.description or [])]
    for row in result.fetchall():
        yield dict(zip(cols, row))


def count_code_snapshot_slices(conn: "duckdb.DuckDBPyConnection") -> int:
    """Return total number of slice rows for the current 'latest' run."""
    row = conn.execute(
        "SELECT COUNT(*) FROM code_snapshot_slice WHERE refresh_id = 'latest'"
    ).fetchone()
    return int(row[0]) if row else 0
