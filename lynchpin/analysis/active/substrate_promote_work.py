"""Work-observation promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from .substrate_promote_status import (
    SOURCE_WORK_OBSERVATIONS,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_work_sources(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_WORK_OBSERVATIONS):
        return
    try:
        from lynchpin.sources.xtask_history import iter_all_invocations, xtask_history_paths
        from lynchpin.substrate.work_observations import promote_work_observations

        paths = xtask_history_paths()
        if not any(path.exists() for _, path in paths):
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_WORK_OBSERVATIONS,
                status="unavailable",
                reason="no xtask history databases found",
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
            return

        start_dt, end_dt = _work_window_bounds(window_start, window_end)
        rows = list(iter_all_invocations(start=start_dt, end=end_dt))
        counts["work_observations"] = promote_work_observations(
            conn,
            refresh_id=refresh_id,
            rows=rows,
        )
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_WORK_OBSERVATIONS,
            status="ok" if rows else "empty",
            reason=None if rows else "no xtask invocations in window",
            row_count=counts["work_observations"],
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: work_observation promotion failed: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_WORK_OBSERVATIONS,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )


def _work_window_bounds(
    window_start: date,
    window_end: date,
    *,
    today: date | None = None,
) -> tuple[datetime, datetime]:
    """Return UTC bounds for live work observations.

    Refresh windows are day-granularity half-open intervals. The default
    substrate refresh ends at ``date.today()``, which is correct for complete
    daily summaries but would exclude all live xtask invocations from the
    current day. Work observations are point-in-time operational events, so a
    refresh ending today includes today's live tail.
    """
    effective_today = today or date.today()
    effective_end = window_end
    if window_end <= effective_today:
        effective_end = window_end + timedelta(days=1)
    return (
        datetime.combine(window_start, time.min, tzinfo=timezone.utc),
        datetime.combine(effective_end, time.min, tzinfo=timezone.utc),
    )


__all__ = ["promote_work_sources"]
