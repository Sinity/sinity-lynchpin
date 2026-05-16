"""Calendar and Spotify promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_CALENDAR,
    SOURCE_SPOTIFY_DAILY,
    SourceSelection,
    record_source_status,
)

log = logging.getLogger(__name__)


def promote_personal_sources(
    conn: Any,
    *,
    refresh_id: str,
    window_start: date,
    window_end: date,
    counts: dict[str, int],
    selection: SourceSelection,
) -> None:
    from lynchpin.substrate.personal import (
        promote_calendar_events,
        promote_spotify_daily,
    )

    # ── calendar_events: best-effort promotion from JSONL source ──────────
    if selection.includes(SOURCE_CALENDAR):
        try:
            from lynchpin.core.config import get_config
            from lynchpin.sources.calendar import iter_events

            cal_path = get_config().calendar_jsonl
            calendar_events = list(iter_events(start=window_start, end=window_end))
            if calendar_events:
                counts["calendar_events"] = promote_calendar_events(
                    conn,
                    refresh_id=refresh_id,
                    events=calendar_events,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_CALENDAR,
                    status="ok",
                    reason=None,
                    row_count=counts["calendar_events"],
                    window_start=window_start,
                    window_end=window_end,
                )
            else:
                cal_exists = cal_path.exists()
                status = "unavailable" if not cal_exists else "empty"
                reason = (
                    f"calendar JSONL not found at {cal_path}"
                    if not cal_exists
                    else "no calendar events in window"
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_CALENDAR,
                    status=status,
                    reason=reason,
                    row_count=0,
                    window_start=window_start,
                    window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: calendar promotion skipped: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_CALENDAR,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )

    # ── spotify_daily: best-effort promotion from streaming history ──────
    if selection.includes(SOURCE_SPOTIFY_DAILY):
        try:
            from lynchpin.sources.spotify import iter_streams

            streams = list(iter_streams())
            if streams:
                counts["spotify_daily"] = promote_spotify_daily(
                    conn,
                    refresh_id=refresh_id,
                    streams=streams,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SPOTIFY_DAILY,
                    status="ok",
                    reason=None,
                    row_count=counts["spotify_daily"],
                    window_start=window_start,
                    window_end=window_end,
                )
            else:
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SPOTIFY_DAILY,
                    status="empty",
                    reason="no Spotify streams in window",
                    row_count=0,
                    window_start=window_start,
                    window_end=window_end,
                )
        except Exception as exc:
            log.warning(
                "substrate_promote: spotify_daily promotion skipped: %s", exc
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_SPOTIFY_DAILY,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
