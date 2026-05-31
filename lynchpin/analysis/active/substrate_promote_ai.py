"""AI work-event promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_backfill import _backfill_ai_attribution
from .substrate_promote_status import (
    SOURCE_AI_WORK_EVENTS,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_ai_sources(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_AI_WORK_EVENTS):
        return

    try:
        from lynchpin.core.classify import resolve_project
        from lynchpin.graph.work_event_kind import overlay_label
        from lynchpin.sources.polylogue import archive_readiness, work_events
        from lynchpin.substrate.work_ai import promote_ai_work_events

        # Check readiness first — polylogue may be rematerializing,
        # in which case work_events() can block for minutes.
        readiness = archive_readiness()
        if readiness.status != "ready":
            log.warning(
                "substrate_promote: polylogue not ready (status=%s: %s); "
                "AI work events skipped this run",
                readiness.status, readiness.reason,
            )
            record_source_status(
                conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                status="unavailable",
                reason=f"polylogue not ready ({readiness.status}): {readiness.reason}",
                row_count=0, window_start=window_start, window_end=window_end,
            )
            return

        import concurrent.futures

        def _classify(ev: Any) -> Any:
            return overlay_label(
                source_kind=ev.kind,
                source_confidence=float(ev.confidence or 0.0),
                file_paths=ev.file_paths,
                tools_used=ev.tools_used,
                duration_ms=int(ev.duration_ms or 0),
            )

        def _project_resolver(ev: Any) -> str | None:
            for path in ev.file_paths:
                project = resolve_project(path)
                if project:
                    return project
            return None

        def _fetch() -> list[Any]:
            return list(work_events(start=window_start, end=window_end))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch)
            try:
                events = future.result(timeout=45)
            except concurrent.futures.TimeoutError:
                log.warning(
                    "substrate_promote: polylogue work_events() timed out "
                    "(45 s); AI sources skipped this run"
                )
                record_source_status(
                    conn, refresh_id=refresh_id, source=SOURCE_AI_WORK_EVENTS,
                    status="unavailable",
                    reason="polylogue API call timed out",
                    row_count=0, window_start=window_start, window_end=window_end,
                )
                return
        if events:
            counts["ai_work_events"] = promote_ai_work_events(
                conn,
                refresh_id=refresh_id,
                events=events,
                project_resolver=_project_resolver,
                classifier=_classify,
            )
            counts["ai_attribution_backfill"] = _backfill_ai_attribution(
                conn,
                refresh_id=refresh_id,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_AI_WORK_EVENTS,
                status="ok",
                reason=None,
                row_count=counts["ai_work_events"],
                window_start=window_start,
                window_end=window_end,
            )
            return

        # Distinguish genuinely empty archives from stale products.
        from lynchpin.sources.polylogue import archive_readiness

        readiness = archive_readiness()
        if readiness.work_event_count == 0:
            status = "empty"
            reason = "polylogue archive has no work events in window"
        elif readiness.status != "ready":
            status = "unavailable"
            reason = (
                f"polylogue not ready (status={readiness.status}): {readiness.reason}"
            )
        else:
            status = "unavailable"
            reason = (
                "polylogue archive_readiness=ready but work_events() "
                "returned [] — likely stale insight rows; run "
                "`polylogue doctor --repair --target session_insights`"
            )
        log.warning(
            "substrate_promote: ai_work_events empty in window %s–%s (%s: %s)",
            window_start,
            window_end,
            status,
            reason,
        )
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_AI_WORK_EVENTS,
            status=status,
            reason=reason,
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: AI work events promotion failed: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_AI_WORK_EVENTS,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )
