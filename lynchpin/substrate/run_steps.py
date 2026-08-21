"""Durable refresh-step observability for the DuckDB substrate."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


def reconcile_orphaned_running_steps(
    conn: "duckdb.DuckDBPyConnection",
    *,
    stale_before: datetime,
) -> int:
    """Mark stuck 'running' steps as orphaned so reads stop seeing them as live.

    substrate_run_step is append-only: a step records a 'running' row when it
    starts and a 'success'/'error' row when it finishes. If the process dies
    mid-step (killed, crashed — not a caught Python exception, which
    _run_stage already records as 'error'), nothing ever appends that
    terminal row, and any reader that takes the latest row per
    (refresh_id, step) sees a permanently misleading "still running" state
    (lynchpin-b5q). This appends an 'orphaned' terminal row for any step
    whose most recent row is still 'running' and was recorded before
    ``stale_before``, so the next read resolves to a real terminal status.

    Returns the number of steps reconciled.
    """
    rows = conn.execute(
        """
        SELECT refresh_id, step, started_at
        FROM (
            SELECT refresh_id, step, status, started_at,
                   row_number() OVER (
                       PARTITION BY refresh_id, step
                       ORDER BY
                           recorded_at DESC,
                           CASE WHEN status = 'running' THEN 0 ELSE 1 END DESC
                   ) AS rn
            FROM substrate_run_step
        )
        WHERE rn = 1 AND status = 'running' AND started_at < ?
        """,
        [stale_before],
    ).fetchall()
    finished_at = datetime.now(timezone.utc)
    for refresh_id, step, started_at in rows:
        record_run_step(
            conn,
            refresh_id=refresh_id,
            step=step,
            status="orphaned",
            message=(
                "reconciled: no terminal status was ever recorded for this "
                "step and it has been stale since before this check; the "
                "run that started it most likely died mid-step (killed or "
                "crashed) rather than completing"
            ),
            started_at=started_at,
            finished_at=finished_at,
        )
    return len(rows)


def record_run_step(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    step: str,
    status: str,
    message: str | None = None,
    row_count: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    """Append one progress/status row for a materialization or promotion step."""
    conn.execute(
        """
        INSERT INTO substrate_run_step
        (refresh_id, step, status, message, row_count, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            refresh_id,
            step,
            status,
            message,
            row_count,
            started_at,
            finished_at,
        ],
    )


__all__ = ["record_run_step", "reconcile_orphaned_running_steps"]
