"""Durable refresh-step observability for the DuckDB substrate."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb


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


__all__ = ["record_run_step"]
