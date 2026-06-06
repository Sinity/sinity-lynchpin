"""Work-observation promotion for the materialization DAG substrate step."""

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
        rows = iter_all_invocations(start=start_dt, end=end_dt) if has_xtask else ()
        polylogue_rows = iter_polylogue_invocations(start=start_dt, end=end_dt) if has_polylogue_devtools else ()
        # xtask invocations and Polylogue devtool observations share the
        # work_observation table under one refresh_id. promote_rows deletes by
        # refresh_id alone (not by source), so two source-scoped delete+insert
        # calls would have the second clobber the first. Delete once here, then
        # append both sources so they coexist (and idempotence holds even when
        # one source is empty for the window).
        conn.execute("DELETE FROM work_observation WHERE refresh_id = ?", [refresh_id])
        counts["xtask_work_observations"] = promote_work_observations(
            conn,
            refresh_id=refresh_id,
            rows=rows,
            delete_existing=False,
        ) if has_xtask else 0
        counts["polylogue_devtools_work_observations"] = promote_polylogue_devtools_observations(
            conn,
            refresh_id=refresh_id,
            rows=polylogue_rows,
            delete_existing=False,
        ) if has_polylogue_devtools else 0
        counts["work_observations"] = (
            counts["xtask_work_observations"]
            + counts["polylogue_devtools_work_observations"]
        )
        stages = iter_all_stage_timings(start=start_dt, end=end_dt) if has_xtask else ()
        counts["work_observation_stages"] = promote_work_observation_stages(
            conn,
            refresh_id=refresh_id,
            rows=stages,
        ) if has_xtask else 0
        tests = iter_all_test_results(start=start_dt, end=end_dt) if has_xtask else ()
        counts["work_observation_test_results"] = promote_work_observation_test_results(
            conn,
            refresh_id=refresh_id,
            rows=tests,
        ) if has_xtask else 0
        source_bits = []
        if has_xtask:
            source_bits.append("xtask")
        if has_polylogue_devtools:
            source_bits.append("polylogue_devtools")
        breakdown = (
            f"xtask_invocations={counts['xtask_work_observations']}, "
            f"xtask_stages={counts['work_observation_stages']}, "
            f"xtask_tests={counts['work_observation_test_results']}, "
            f"polylogue_devtools={counts['polylogue_devtools_work_observations']}"
        )
        # Surface the silent-starvation case: xtask DBs were present and their
        # stage/test ledgers promoted rows, yet zero invocations landed. That
        # state previously recorded a healthy "ok" while starving the workload
        # resource attribution arm, so make it explicitly visible.
        xtask_invocations_missing = (
            has_xtask
            and counts["xtask_work_observations"] == 0
            and (counts["work_observation_stages"] or counts["work_observation_test_results"])
        )
        if xtask_invocations_missing:
            log.warning(
                "substrate_promote: xtask stage/test ledgers promoted rows but zero "
                "invocations landed in window %s..%s (%s); workload resource "
                "attribution will be starved",
                window_start,
                window_end,
                breakdown,
            )
        if not counts["work_observations"]:
            status = "empty"
            reason = f"no work observations in window from {', '.join(source_bits)} ({breakdown})"
        elif xtask_invocations_missing:
            status = "degraded"
            reason = f"xtask invocations missing while stage/test ledgers present ({breakdown})"
        else:
            status = "ok"
            reason = breakdown
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_WORK_OBSERVATIONS,
            status=status,
            reason=reason,
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

    Materialization windows are day-granularity half-open intervals. The
    default substrate materialization ends at ``date.today()``, which is correct
    for complete daily summaries but would exclude all live xtask invocations
    from the current day. Work observations are point-in-time operational
    events, so a materialization ending today includes today's live tail.
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
