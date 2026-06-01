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
        from lynchpin.sources.polylogue_devtools import available as polylogue_devtools_available
        from lynchpin.sources.polylogue_devtools import iter_invocations as iter_polylogue_invocations
        from lynchpin.sources.xtask_history import iter_all_invocations, xtask_history_paths
        from lynchpin.sources.xtask_history import iter_all_stage_timings, iter_all_test_results
        from lynchpin.substrate.work_observations import (
            promote_polylogue_devtools_observations,
            promote_work_observation_stages,
            promote_work_observation_test_results,
            promote_work_observations,
        )

        paths = xtask_history_paths()
        has_xtask = any(path.exists() for _, path in paths)
        has_polylogue_devtools = polylogue_devtools_available()
        if not has_xtask and not has_polylogue_devtools:
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_WORK_OBSERVATIONS,
                status="unavailable",
                reason="no xtask history databases or Polylogue devtool ledgers found",
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
            return

        start_dt, end_dt = _work_window_bounds(window_start, window_end)
        rows = list(iter_all_invocations(start=start_dt, end=end_dt)) if has_xtask else []
        polylogue_rows = list(iter_polylogue_invocations(start=start_dt, end=end_dt)) if has_polylogue_devtools else []
        counts["xtask_work_observations"] = promote_work_observations(
            conn,
            refresh_id=refresh_id,
            rows=rows,
        ) if rows else 0
        counts["polylogue_devtools_work_observations"] = promote_polylogue_devtools_observations(
            conn,
            refresh_id=refresh_id,
            rows=polylogue_rows,
        ) if polylogue_rows else 0
        counts["work_observations"] = (
            counts["xtask_work_observations"]
            + counts["polylogue_devtools_work_observations"]
        )
        stages = list(iter_all_stage_timings(start=start_dt, end=end_dt)) if has_xtask else []
        counts["work_observation_stages"] = promote_work_observation_stages(
            conn,
            refresh_id=refresh_id,
            rows=stages,
        ) if stages else 0
        tests = list(iter_all_test_results(start=start_dt, end=end_dt)) if has_xtask else []
        counts["work_observation_test_results"] = promote_work_observation_test_results(
            conn,
            refresh_id=refresh_id,
            rows=tests,
        ) if tests else 0
        source_bits = []
        if has_xtask:
            source_bits.append("xtask")
        if has_polylogue_devtools:
            source_bits.append("polylogue_devtools")
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_WORK_OBSERVATIONS,
            status="ok" if counts["work_observations"] else "empty",
            reason=None if counts["work_observations"] else f"no work observations in window from {', '.join(source_bits)}",
            row_count=(
                counts["work_observations"]
                + counts["work_observation_stages"]
                + counts["work_observation_test_results"]
            ),
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
