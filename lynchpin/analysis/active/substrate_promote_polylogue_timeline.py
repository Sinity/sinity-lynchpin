"""Promote Polylogue time-composition rows into the substrate."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_POLYLOGUE_TIMELINE,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_polylogue_timeline_source(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    if not selection.includes(SOURCE_POLYLOGUE_TIMELINE):
        return
    try:
        from lynchpin.sources.polylogue import archive_readiness
        from lynchpin.sources.polylogue_timeline import (
            session_compositions,
            session_timeline,
            timeline_overlaps,
        )
        from lynchpin.substrate.polylogue_timeline import (
            promote_polylogue_cross_source_overlaps,
            promote_polylogue_session_compositions,
            promote_polylogue_timeline_spans,
        )

        readiness = archive_readiness()
        if readiness.status == "unavailable":
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_POLYLOGUE_TIMELINE,
                status="unavailable",
                reason=f"polylogue not ready ({readiness.status}): {readiness.reason}",
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
            return

        compositions = session_compositions(
            start=window_start,
            end=window_end,
            include_cross_source=True,
        )
        spans = []
        overlaps = []
        for comp in compositions:
            if comp.status != "ok":
                continue
            session_spans = session_timeline(comp.session_id, include_cross_source=True)
            native = [s for s in session_spans if s.lane in {"message", "message_gap", "semantic"}]
            external = [s for s in session_spans if s.lane not in {"message", "message_gap", "semantic"}]
            spans.extend(session_spans)
            overlaps.extend(timeline_overlaps(native, external))

        counts["polylogue_timeline_spans"] = promote_polylogue_timeline_spans(
            conn,
            refresh_id=refresh_id,
            rows=spans,
        )
        counts["polylogue_session_time_compositions"] = (
            promote_polylogue_session_compositions(
                conn,
                refresh_id=refresh_id,
                rows=compositions,
            )
        )
        counts["polylogue_cross_source_overlaps"] = (
            promote_polylogue_cross_source_overlaps(
                conn,
                refresh_id=refresh_id,
                rows=overlaps,
            )
        )
        status = "ok" if compositions else "empty"
        reason = None if compositions else "polylogue facade returned no sessions in window"
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_POLYLOGUE_TIMELINE,
            status=status,
            reason=reason,
            row_count=len(compositions),
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        log.warning("substrate_promote: Polylogue timeline promotion failed: %s", exc)
        record_source_status(
            conn,
            refresh_id=refresh_id,
            source=SOURCE_POLYLOGUE_TIMELINE,
            status="error",
            reason=str(exc),
            row_count=0,
            window_start=window_start,
            window_end=window_end,
        )
