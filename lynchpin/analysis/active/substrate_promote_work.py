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
        from lynchpin.sources.agentctl import (
            AgentctlObservationError,
            read_observation_snapshot,
        )
        from lynchpin.sources.polylogue_verification import (
            PolylogueVerificationContractError,
            PolylogueVerificationError,
            iter_observations as iter_polylogue_verification,
            read_verification_snapshot,
        )
        from lynchpin.sources.xtask_history import iter_all_invocations, xtask_history_path
        from lynchpin.sources.xtask_history import iter_all_stage_timings, iter_all_test_results
        from lynchpin.substrate.work_observations import (
            promote_agentctl_observations,
            promote_agentctl_receipt_refs,
            promote_polylogue_verification_observations,
            promote_work_observation_stages,
            promote_work_observation_test_results,
            promote_work_observations,
        )

        # One ledger, shared by every checkout and worktree: nothing to sweep or
        # mirror before reading. Rows carry their own workspace provenance, and
        # a worktree still running an older xtask is absorbed at the source with
        # `xtask history unify`.
        has_xtask = xtask_history_path().exists()
        agentctl_unavailable_reason: str | None
        try:
            agentctl_snapshot = read_observation_snapshot()
        except AgentctlObservationError as error:
            agentctl_snapshot = None
            agentctl_unavailable_reason = str(error)
        else:
            agentctl_unavailable_reason = None
        polylogue_unavailable_reason: str | None
        polylogue_contract_violated = False
        try:
            polylogue_snapshot = read_verification_snapshot()
        except PolylogueVerificationError as error:
            polylogue_snapshot = None
            polylogue_unavailable_reason = str(error)
            # A lane that exists but breaks its contract is a defect to surface,
            # not an absent optional source.
            polylogue_contract_violated = isinstance(error, PolylogueVerificationContractError)
        else:
            polylogue_unavailable_reason = None
        if not has_xtask and agentctl_snapshot is None and polylogue_snapshot is None:
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_WORK_OBSERVATIONS,
                status="degraded" if polylogue_contract_violated else "unavailable",
                reason=(
                    "no xtask history database, AgentCTL observations "
                    f"({agentctl_unavailable_reason}) or Polylogue verification "
                    f"lane ({polylogue_unavailable_reason}) found"
                ),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
            return

        start_dt, end_dt = _work_window_bounds(window_start, window_end)
        rows = iter_all_invocations(start=start_dt, end=end_dt) if has_xtask else ()
        agentctl_rows = (
            tuple(
                row
                for row in agentctl_snapshot.observations
                if start_dt <= row.started_at < end_dt
            )
            if agentctl_snapshot is not None
            else ()
        )
        # xtask invocations, AgentCTL jobs and Polylogue verification runs share
        # the work_observation table under one refresh_id. promote_rows deletes by
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
        counts["agentctl_work_observations"] = promote_agentctl_observations(
            conn,
            refresh_id=refresh_id,
            rows=agentctl_rows,
            delete_existing=False,
        ) if agentctl_snapshot is not None else 0
        counts["agentctl_receipt_refs"] = promote_agentctl_receipt_refs(
            conn,
            refresh_id=refresh_id,
            rows=agentctl_rows,
        ) if agentctl_snapshot is not None else 0
        counts["polylogue_verification_observations"] = promote_polylogue_verification_observations(
            conn,
            refresh_id=refresh_id,
            rows=iter_polylogue_verification(start=start_dt, end=end_dt, snapshot=polylogue_snapshot),
            delete_existing=False,
        ) if polylogue_snapshot is not None else 0
        counts["work_observations"] = (
            counts["xtask_work_observations"]
            + counts["agentctl_work_observations"]
            + counts["polylogue_verification_observations"]
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
        if agentctl_snapshot is not None:
            source_bits.append("agentctl")
        if polylogue_snapshot is not None:
            source_bits.append("polylogue_verification")
        breakdown = (
            f"xtask_invocations={counts['xtask_work_observations']}, "
            f"xtask_stages={counts['work_observation_stages']}, "
            f"xtask_tests={counts['work_observation_test_results']}, "
            f"agentctl={counts['agentctl_work_observations']}, "
            f"agentctl_receipt_refs={counts['agentctl_receipt_refs']}, "
            f"polylogue_verification={counts['polylogue_verification_observations']}"
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
        polylogue_rejected = len(polylogue_snapshot.rejected) if polylogue_snapshot is not None else 0
        if polylogue_rejected:
            breakdown = (
                f"{breakdown}, polylogue_verification_rejected={polylogue_rejected} "
                f"(first: {polylogue_snapshot.rejected[0][1]})"
            )
        if polylogue_contract_violated or polylogue_rejected:
            status = "degraded"
            reason = breakdown
        elif not counts["work_observations"]:
            status = "empty"
            reason = f"no work observations in window from {', '.join(source_bits)} ({breakdown})"
        elif xtask_invocations_missing:
            status = "degraded"
            reason = f"xtask invocations missing while stage/test ledgers present ({breakdown})"
        else:
            status = "ok"
            reason = breakdown
        if agentctl_snapshot is None and has_xtask:
            reason = f"{reason}; AgentCTL observations unavailable: {agentctl_unavailable_reason}"
        if polylogue_snapshot is None:
            reason = f"{reason}; Polylogue verification lane unavailable: {polylogue_unavailable_reason}"
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
                + counts["agentctl_receipt_refs"]
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
