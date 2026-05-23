"""Personal-source promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_BORG_DRILL,
    SOURCE_SINNIX_GENERATION,
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
        promote_borg_drill_runs,
        promote_sinnix_generations,
        promote_spotify_daily,
    )

    # ── sinnix_generation: best-effort promotion from activation JSONL ───
    if selection.includes(SOURCE_SINNIX_GENERATION):
        try:
            from lynchpin.core.config import get_config
            from lynchpin.sources.sinnix_generations import generation_records

            gen_path = get_config().sinnix_generations_jsonl
            generations = list(generation_records())
            if generations:
                counts["sinnix_generation"] = promote_sinnix_generations(
                    conn,
                    refresh_id=refresh_id,
                    records=generations,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SINNIX_GENERATION,
                    status="ok",
                    reason=None,
                    row_count=counts["sinnix_generation"],
                    window_start=window_start,
                    window_end=window_end,
                )
            else:
                gen_exists = gen_path.exists()
                status = "unavailable" if not gen_exists else "empty"
                reason = (
                    f"sinnix generations JSONL not found at {gen_path}"
                    if not gen_exists
                    else "JSONL exists but contains no activation records"
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_SINNIX_GENERATION,
                    status=status,
                    reason=reason,
                    row_count=0,
                    window_start=window_start,
                    window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: sinnix_generation promotion skipped: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_SINNIX_GENERATION,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )

    # ── borg_drill_run: deep-verify drill outcomes from sinnix-borg-drill ─
    if selection.includes(SOURCE_BORG_DRILL):
        try:
            from lynchpin.core.config import get_config
            from lynchpin.sources.borg_drill import drill_runs

            drill_path = get_config().borg_drill_jsonl
            runs = list(drill_runs())
            if runs:
                counts["borg_drill_run"] = promote_borg_drill_runs(
                    conn,
                    refresh_id=refresh_id,
                    runs=runs,
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_BORG_DRILL,
                    status="ok",
                    reason=None,
                    row_count=counts["borg_drill_run"],
                    window_start=window_start,
                    window_end=window_end,
                )
            else:
                drill_exists = drill_path.exists()
                status = "unavailable" if not drill_exists else "empty"
                reason = (
                    f"borg drill JSONL not found at {drill_path}"
                    if not drill_exists
                    else "JSONL exists but contains no drill runs"
                )
                record_source_status(
                    conn,
                    refresh_id=refresh_id,
                    source=SOURCE_BORG_DRILL,
                    status=status,
                    reason=reason,
                    row_count=0,
                    window_start=window_start,
                    window_end=window_end,
                )
        except Exception as exc:
            log.warning("substrate_promote: borg_drill_run promotion skipped: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_BORG_DRILL,
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
