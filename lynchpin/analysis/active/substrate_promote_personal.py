"""Personal-source promotion for the refresh DAG substrate step."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from .substrate_promote_status import (
    SOURCE_BORG_DRILL,
    SOURCE_ACTIVITY_CONTENT,
    SOURCE_PERSONAL_DAILY_SIGNAL,
    SOURCE_SINNIX_GENERATION,
    SOURCE_SPOTIFY_DAILY,
    SOURCE_TITLE_CLASSIFICATION,
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
        promote_activity_content_buckets,
        promote_activity_content_days,
        promote_activity_title_usage,
        promote_personal_daily_signals,
        promote_sinnix_generations,
        promote_spotify_daily_rows,
        promote_title_classifications_from_path,
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

    # ── operator_day: materialize the wide cross-source daily matrix ─────
    # Self-contained best-effort: the heavy operator_daily_matrix build runs
    # here in the offline refresh so MCP correlation tools can read it via
    # fast SQL. Failure is logged and never aborts the rest of the promotion.
    try:
        from lynchpin.analysis.operator_daily import operator_daily_matrix
        from lynchpin.substrate.personal import promote_operator_day_rows

        operator_rows = [
            r
            for r in operator_daily_matrix(window_start, window_end)
            if window_start <= r.date < window_end
        ]
        if operator_rows:
            counts["operator_day"] = promote_operator_day_rows(
                conn,
                refresh_id=refresh_id,
                rows=operator_rows,
            )
    except Exception as exc:
        log.warning("substrate_promote: operator_day promotion skipped: %s", exc)

    # ── spotify_daily: best-effort promotion from streaming history ──────
    if selection.includes(SOURCE_SPOTIFY_DAILY):
        try:
            from lynchpin.sources.personal_signals import iter_spotify_daily_signals

            spotify_rows = [
                row
                for row in iter_spotify_daily_signals()
                if window_start <= row.date < window_end
            ]
            if spotify_rows:
                counts["spotify_daily"] = promote_spotify_daily_rows(
                    conn,
                    refresh_id=refresh_id,
                    rows=spotify_rows,
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

    # ── personal_daily_signal: normalized daily metrics for canonical products
    if selection.includes(SOURCE_TITLE_CLASSIFICATION):
        try:
            from lynchpin.sources.title_metadata import title_metadata_path

            counts["title_classification"] = promote_title_classifications_from_path(
                conn,
                refresh_id=refresh_id,
                path=str(title_metadata_path()),
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_TITLE_CLASSIFICATION,
                status="ok" if counts["title_classification"] else "empty",
                reason=None if counts["title_classification"] else "no title classifications available",
                row_count=counts["title_classification"],
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            log.warning("substrate_promote: title_classification promotion skipped: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_TITLE_CLASSIFICATION,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )

    if selection.includes(SOURCE_ACTIVITY_CONTENT):
        try:
            from lynchpin.sources.activity_content import iter_activity_content_days, iter_activity_title_usage

            # Promote ALL NDJSON rows, not just the current window.
            # Window-filtering caused coverage gaps between DAG runs —
            # dates present in the NDJSON but falling outside the
            # incremental window were silently dropped. Full promotion
            # is cheap (511 rows) and the dedup step in the promoter
            # removes stale refresh_ids for the same dates.
            content_rows = list(iter_activity_content_days())
            usage_rows = [
                row
                for row in iter_activity_title_usage()
                if row.last_date is not None
                and row.first_date is not None
            ]
            counts["activity_content_day"] = promote_activity_content_days(
                conn,
                refresh_id=refresh_id,
                rows=content_rows,
            )
            counts["activity_content_bucket"] = promote_activity_content_buckets(
                conn,
                refresh_id=refresh_id,
                rows=content_rows,
            )
            counts["activity_title_usage"] = promote_activity_title_usage(
                conn,
                refresh_id=refresh_id,
                rows=usage_rows,
            )
            row_count = counts["activity_content_day"] + counts["activity_content_bucket"] + counts["activity_title_usage"]
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_ACTIVITY_CONTENT,
                status="ok" if row_count else "empty",
                reason=None if row_count else "no activity-content rows in window",
                row_count=row_count,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            log.warning("substrate_promote: activity_content promotion skipped: %s", exc)
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_ACTIVITY_CONTENT,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )

    # ── personal_daily_signal: normalized daily metrics for canonical products
    if selection.includes(SOURCE_PERSONAL_DAILY_SIGNAL):
        try:
            from lynchpin.sources.personal_signals import iter_personal_daily_signals

            signal_rows = [
                (row.source, row.date, row.metric, row.value, row.dimensions)
                for row in iter_personal_daily_signals()
                if window_start <= row.date < window_end
            ]
            counts["personal_daily_signal"] = promote_personal_daily_signals(
                conn,
                refresh_id=refresh_id,
                rows=signal_rows,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_PERSONAL_DAILY_SIGNAL,
                status="ok" if signal_rows else "empty",
                reason=None if signal_rows else "no daily personal-source signals in window",
                row_count=counts["personal_daily_signal"],
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:
            log.warning(
                "substrate_promote: personal_daily_signal promotion skipped: %s",
                exc,
            )
            record_source_status(
                conn,
                refresh_id=refresh_id,
                source=SOURCE_PERSONAL_DAILY_SIGNAL,
                status="error",
                reason=str(exc),
                row_count=0,
                window_start=window_start,
                window_end=window_end,
            )
