"""Temporal signal detection — re-exported from sources layer.

The core detection engine and signal specs live in
``lynchpin.sources.temporal_signals`` so that ``ingest/`` modules can import
them without crossing the graph/ layer boundary.

This module re-exports all public and private symbols for backward
compatibility, and adds the substrate-optimised commit-count loader that
``sources/`` cannot host (it would require importing ``substrate/``).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

# Re-export all public symbols plus the private loaders that tests patch.
from ..sources.temporal_signals import (  # noqa: F401
    ANOMALY_BASELINE_DAYS,
    ANOMALY_MIN_HISTORY,
    ANOMALY_SCORE_THRESHOLD,
    PERIODICITY_MIN_SAMPLES,
    TREND_MIN_SAMPLES,
    SignalSpec,
    TemporalEvent,
    _detect_for_signal,
    _end_dt,
    _health_daily_rows,
    _load_active_hours,
    _load_ai_engaged,
    _load_ai_sessions,
    _load_arbtt_minutes,
    _load_bookmarks,
    _load_communications,
    _load_deep_work,
    _load_error_rate,
    _load_command_count,
    _load_fragmentation,
    _load_google_activity,
    _load_hrv,
    _load_resting_hr,
    _load_sleep_hours,
    _load_sleep_score,
    _load_web_visits,
    _load_youtube_activity,
    _polylogue_daily_rows,
    _start_dt,
    _terminal_daily_rows,
    default_signal_specs,
    detect_temporal_signals,
)


# ---------------------------------------------------------------------------
# Substrate-optimised commit loader — graph/ is allowed to import substrate/.
# Overrides the sources-layer _load_commits (live git only) with a version
# that tries the DuckDB substrate first and falls back to live git.
# ---------------------------------------------------------------------------

def _load_commit_counts_from_substrate(start: date, end: date) -> dict[date, float] | None:
    from ..core.primitives import logical_date
    from ..substrate.connection import connect, substrate_path
    from ..substrate.snapshots import best_materialized_refresh_id

    try:
        with connect(substrate_path(), read_only=True) as conn:
            refresh_id = best_materialized_refresh_id(
                conn,
                "commit_fact",
                caller="temporal_signals.commits_per_day",
            )
            if refresh_id is None:
                return None
            if not _commit_source_status_covers(conn, refresh_id=refresh_id, start=start, end=end):
                return None
            rows = conn.execute(
                """
                SELECT authored_at
                FROM commit_fact
                WHERE refresh_id = ?
                  AND authored_at::DATE >= ?
                  AND authored_at::DATE <= ?
                """,
                [refresh_id, start, end + timedelta(days=1)],
            ).fetchall()
    except Exception:
        return None

    by_day: dict[date, float] = defaultdict(float)
    for (authored_at,) in rows:
        day = logical_date(authored_at)
        if start <= day <= end:
            by_day[day] += 1.0
    return dict(by_day)


def _commit_source_status_covers(
    conn: Any,
    *,
    refresh_id: str,
    start: date,
    end: date,
) -> bool:
    row = conn.execute(
        """
        SELECT window_start, window_end
        FROM substrate_source_status
        WHERE refresh_id = ?
          AND source = 'commits'
          AND status = 'ok'
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        [refresh_id],
    ).fetchone()
    if not row:
        return False
    window_start, window_end = row
    if window_start is None or window_end is None:
        return False
    return window_start <= start and window_end >= end + timedelta(days=1)


def _load_commits(start: date, end: date) -> dict[date, float]:
    """Substrate-optimised commit loader (graph layer override).

    Tries the DuckDB substrate first; falls back to live git log.
    The sources-layer version skips the substrate step.
    """
    from ..sources.git import daily_activity

    materialized = _load_commit_counts_from_substrate(start, end)
    if materialized is not None:
        return materialized

    by_day: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        by_day[row.date] += row.commit_count
    return dict(by_day)
