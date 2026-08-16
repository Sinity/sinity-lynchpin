"""Substrate promoter and reader for polylogue_verify_run.

Uses refresh_id='latest' for overwrite semantics: the source history is read
in full on every materialization, so each promotion replaces the prior
partition rather than accumulating duplicates.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

_RUN_COLUMNS: tuple[str, ...] = (
    "run_id",
    "run_at",
    "tier",
    "git_head",
    "checkout_root",
    "checkout_name",
    "worktree_fingerprint",
    "exit_code",
    "status",
    "diagnosis",
    "duration_s",
    "verification_scope",
    "release_baseline_allowed",
    "artifact_dir",
    "step_count",
    "failed_step_count",
    "slowest_step",
    "slowest_step_s",
    "tests_passed",
    "tests_failed",
    "selected_count",
    "terminal_count",
    "terminal_green",
    "complete_corpus_covered",
    "pytest_wall_s",
)

REFRESH_ID = "latest"

__all__ = ["iter_polylogue_verify_runs", "promote_polylogue_verify_runs"]


def promote_polylogue_verify_runs(
    conn: duckdb.DuckDBPyConnection,
    *,
    rows: list[dict[str, Any]],
) -> int:
    """Promote polylogue_verify_run rows using refresh_id='latest'."""
    from lynchpin.substrate._helpers import promote_rows

    return promote_rows(
        conn,
        table="polylogue_verify_run",
        columns=_RUN_COLUMNS,
        refresh_id=REFRESH_ID,
        rows=rows,
        extractor=lambda r: tuple(r.get(c) for c in _RUN_COLUMNS),
    )


def iter_polylogue_verify_runs(
    conn: duckdb.DuckDBPyConnection,
    *,
    tier: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield promoted verification runs, newest first."""
    columns = ", ".join(_RUN_COLUMNS)
    sql = f"SELECT {columns} FROM polylogue_verify_run WHERE refresh_id = ?"
    params: list[Any] = [REFRESH_ID]
    if tier is not None:
        sql += " AND tier = ?"
        params.append(tier)
    sql += " ORDER BY run_at DESC NULLS LAST"
    for row in conn.execute(sql, params).fetchall():
        yield dict(zip(_RUN_COLUMNS, row, strict=True))
