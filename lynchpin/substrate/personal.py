"""Personal-source table promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from ._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


# ── spotify_daily ─────────────────────────────────────────────────────────────


_SPOTIFY_DAILY_COLUMNS = (
    "date", "track_count", "minutes_played", "unique_artists", "unique_tracks",
    "top_artists", "top_tracks",
)


def promote_spotify_daily_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    """INSERT pre-materialized Spotify daily rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="spotify_daily",
        columns=_SPOTIFY_DAILY_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda row: (
            row.date,
            row.track_count,
            row.minutes_played,
            row.unique_artists,
            row.unique_tracks,
            list(row.top_artists),
            list(row.top_tracks),
        ),
    )


_OPERATOR_DAY_COLUMNS = (
    "date", "aw_active_hours", "aw_deep_work_min", "aw_fragmentation",
    "git_commits", "git_lines_added", "git_lines_deleted", "svn_commits",
    "stress_mean", "hr_mean_bpm", "hr_resting_bpm", "hrv_sdnn", "hrv_rmssd",
    "sleep_hours", "sleep_score", "steps",
    "substance_doses", "substance_mg_by_name",
    "wykop_comments", "reddit_comments", "sms_sent", "messenger_sent",
    "outlook_inbox", "polylogue_sessions", "polylogue_engaged_minutes",
    "web_visits", "web_social_visits", "shell_commands", "spotify_hours",
    "keylog_keypresses", "clipboard_entries", "irc_lines", "raw_log_entries",
    "substance_unique_count", "stress_min", "stress_max",
    "web_unique_domains", "polylogue_messages",
    "weather_temp_mean", "weather_precip_mm", "weather_sunshine_hours", "weather_cloud_pct",
    "mood_sentiment", "mood_dominant_emotion", "mood_message_count",
    "web_nsfw_share", "web_distraction_ratio", "web_top_category",
    "audio_energy", "audio_valence", "audio_danceability",
    "aw_outage_hours", "svn_files_changed",
    "keylog_sessions", "keylog_keybind_uses",
    "spo2_pct", "skin_temp_c",
    "sources_present",
)


def promote_operator_day_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    """INSERT pre-materialized OperatorDay rows (wide cross-source daily matrix).

    Nullable signals (Optional fields like spotify_hours, hrv_rmssd) are stored
    as NULL when absent — missing stays distinct from a real zero. sources_present
    is stored as a VARCHAR[] so consumers can tell which sources actually
    contributed each day.
    """
    return promote_rows(
        conn,
        table="operator_day",
        columns=_OPERATOR_DAY_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda r: (
            r.date,
            r.aw_active_hours,
            r.aw_deep_work_min,
            r.aw_fragmentation,
            r.git_commits,
            r.git_lines_added,
            r.git_lines_deleted,
            r.svn_commits,
            r.stress_mean,
            r.hr_mean_bpm,
            r.hr_resting_bpm,
            r.hrv_sdnn,
            r.hrv_rmssd,
            r.sleep_hours,
            r.sleep_score,
            r.steps,
            r.substance_doses,
            json.dumps(dict(r.substance_mg_by_name), sort_keys=True),
            r.wykop_comments,
            r.reddit_comments,
            r.sms_sent,
            r.messenger_sent,
            r.outlook_inbox,
            r.polylogue_sessions,
            r.polylogue_engaged_minutes,
            r.web_visits,
            r.web_social_visits,
            r.shell_commands,
            r.spotify_hours,
            r.keylog_keypresses,
            r.clipboard_entries,
            r.irc_lines,
            r.raw_log_entries,
            r.substance_unique_count,
            r.stress_min,
            r.stress_max,
            r.web_unique_domains,
            r.polylogue_messages,
            r.weather_temp_mean,
            r.weather_precip_mm,
            r.weather_sunshine_hours,
            r.weather_cloud_pct,
            r.mood_sentiment,
            r.mood_dominant_emotion,
            r.mood_message_count,
            r.web_nsfw_share,
            r.web_distraction_ratio,
            r.web_top_category,
            r.audio_energy,
            r.audio_valence,
            r.audio_danceability,
            r.aw_outage_hours,
            r.svn_files_changed,
            r.keylog_sessions,
            r.keylog_keybind_uses,
            r.spo2_pct,
            r.skin_temp_c,
            sorted(r.sources_present),
        ),
    )


def load_spotify_daily_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
) -> list[tuple[Any, ...]]:
    """Return spotify_daily rows for a refresh_id with optional date bounds.

    Returns (date, track_count, minutes_played, unique_artists,
    unique_tracks, top_artists, top_tracks) tuples.
    """
    sql = (
        "SELECT date, track_count, minutes_played, unique_artists, "
        "unique_tracks, top_artists, top_tracks FROM spotify_daily "
        "WHERE refresh_id = ?"
    )
    params: list[Any] = [refresh_id]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    sql += " ORDER BY date"
    return conn.execute(sql, params).fetchall()


def load_operator_day_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return operator_day rows as dicts for a refresh_id with optional filters.

    ``columns`` narrows the SELECT to a subset; must be valid column names from
    _OPERATOR_DAY_COLUMNS. All columns are returned when ``columns`` is None.
    """
    valid = set(_OPERATOR_DAY_COLUMNS)
    if columns:
        bad = [c for c in columns if c not in valid]
        if bad:
            raise ValueError(f"unknown operator_day columns: {bad!r}")
        select_cols = ", ".join(columns)
    else:
        select_cols = ", ".join(_OPERATOR_DAY_COLUMNS)
    sql = f"SELECT {select_cols} FROM operator_day WHERE refresh_id = ?"
    params: list[Any] = [refresh_id]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    sql += " ORDER BY date"
    col_names = list(columns) if columns else list(_OPERATOR_DAY_COLUMNS)
    return [dict(zip(col_names, row)) for row in conn.execute(sql, params).fetchall()]


def load_personal_daily_signals(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    source: str | None = None,
    metric: str | None = None,
    limit: int = 1000,
) -> list[tuple[Any, ...]]:
    """Return personal_daily_signal rows with optional filters.

    Returns (source, date, metric, value, dimensions) tuples.
    """
    sql = (
        "SELECT source, date, metric, value, dimensions "
        "FROM personal_daily_signal WHERE refresh_id = ?"
    )
    params: list[Any] = [refresh_id]
    if start:
        sql += " AND date >= ?"
        params.append(start)
    if end:
        sql += " AND date <= ?"
        params.append(end)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if metric:
        sql += " AND metric = ?"
        params.append(metric)
    sql += " ORDER BY date, source, metric LIMIT ?"
    params.append(min(max(limit, 1), 10_000))
    return conn.execute(sql, params).fetchall()


__all__ = [
    "load_operator_day_rows",
    "load_personal_daily_signals",
    "load_spotify_daily_rows",
    "promote_activity_content_buckets",
    "promote_activity_content_days",
    "promote_activity_title_usage",
    "promote_borg_drill_runs",
    "promote_operator_day_rows",
    "promote_personal_daily_signals",
    "promote_sinnix_generations",
    "promote_spotify_daily_rows",
    "promote_title_classifications",
    "promote_title_classifications_from_path",
    "verify_activity_content_integrity",
]


# ── personal_daily_signal ────────────────────────────────────────────────────


_PERSONAL_DAILY_SIGNAL_COLUMNS = (
    "source",
    "date",
    "metric",
    "value",
    "dimensions",
    "dimension_key",
)


def promote_personal_daily_signals(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[tuple[str, date, str, float, dict[str, Any]]],
) -> int:
    """INSERT normalized daily personal-source signals."""
    def extract(row: tuple[str, date, str, float, dict[str, Any]]) -> tuple[Any, ...]:
        dimensions = json.dumps(row[4], sort_keys=True)
        return (
            row[0],
            row[1],
            row[2],
            float(row[3]),
            dimensions,
            dimensions,
        )

    return promote_rows(
        conn,
        table="personal_daily_signal",
        columns=_PERSONAL_DAILY_SIGNAL_COLUMNS,
        refresh_id=refresh_id,
        rows=_coalesce_daily_signals(rows),
        extractor=extract,
    )


def _coalesce_daily_signals(
    rows: Iterable[tuple[str, date, str, float, dict[str, Any]]],
) -> Iterable[tuple[str, date, str, float, dict[str, Any]]]:
    buckets: dict[tuple[str, date, str, str], list[float]] = defaultdict(list)
    dimensions_by_key: dict[tuple[str, date, str, str], dict[str, Any]] = {}
    for source, day, metric, value, dimensions in rows:
        dimension_key = json.dumps(dimensions, sort_keys=True)
        key = (source, day, metric, dimension_key)
        buckets[key].append(float(value))
        dimensions_by_key[key] = dimensions

    for (source, day, metric, dimension_key), values in buckets.items():
        if _metric_uses_mean(metric):
            value = sum(values) / len(values)
        else:
            value = sum(values)
        yield source, day, metric, value, dimensions_by_key[(source, day, metric, dimension_key)]


def _metric_uses_mean(metric: str) -> bool:
    return metric in {"sleep_score", "avg_heart_rate", "hrv_rmssd"} or metric.startswith("avg_")


# ── title/content metadata ───────────────────────────────────────────────────


_TITLE_CLASSIFICATION_COLUMNS = (
    "title_hash",
    "app",
    "raw_title",
    "normalized_title",
    "activity",
    "subject",
    "content_type",
    "attention_level",
    "topic_category",
    "platform",
    "mode",
    "app_kind",
    "tool",
    "domain",
    "domain_category",
    "is_ai_tool",
    "is_ai_active",
    "productivity_score",
    "focus_score",
    "confidence",
    "classification_source",
    "model_version",
    "extra",
)


def promote_title_classifications(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    """INSERT canonical title classifications."""

    def extract(row: Any) -> tuple[Any, ...]:
        return (
            row.title_hash,
            row.app,
            row.raw_title,
            row.normalized_title,
            row.activity,
            row.subject,
            row.content_type,
            row.attention_level,
            row.topic_category,
            row.platform,
            row.mode,
            row.app_kind,
            row.tool,
            row.domain,
            row.domain_category,
            row.is_ai_tool,
            row.is_ai_active,
            row.productivity_score,
            row.focus_score,
            row.confidence,
            row.classification_source,
            row.model_version,
            json.dumps(row.extra or {}, sort_keys=True),
        )

    return promote_rows(
        conn,
        table="title_classification",
        columns=_TITLE_CLASSIFICATION_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=extract,
        batch_size=10_000,
    )


def promote_title_classifications_from_path(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    path: str,
) -> int:
    """Bulk INSERT canonical title classifications from NDJSON."""
    conn.execute("DELETE FROM title_classification WHERE refresh_id = ?", [refresh_id])
    conn.execute(
        """
        INSERT INTO title_classification (
            title_hash, app, raw_title, normalized_title, activity, subject,
            content_type, attention_level, topic_category, platform, mode,
            app_kind, tool, domain, domain_category, is_ai_tool, is_ai_active,
            productivity_score, focus_score, confidence, classification_source,
            model_version, extra, refresh_id
        )
        SELECT
            title_hash, COALESCE(app, ''), raw_title, COALESCE(normalized_title, ''), activity, subject,
            content_type, attention_level, topic_category, platform, mode,
            app_kind, tool, domain, domain_category, is_ai_tool, is_ai_active,
            productivity_score, focus_score, confidence, classification_source,
            model_version, '{}'::JSON, ?
        FROM read_json_auto(?)
        WHERE title_hash IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY title_hash
            ORDER BY confidence DESC NULLS LAST, app, normalized_title
        ) = 1
        """,
        [refresh_id, path],
    )
    return int(conn.execute("SELECT COUNT(*) FROM title_classification WHERE refresh_id = ?", [refresh_id]).fetchone()[0])


_ACTIVITY_CONTENT_DAY_COLUMNS = (
    "date",
    "focused_seconds",
    "matched_seconds",
    "gpt_matched_seconds",
    "unmatched_seconds",
    "matched_ratio",
    "gpt_matched_ratio",
    "source_counts",
)


def promote_activity_content_days(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    count = promote_rows(
        conn,
        table="activity_content_day",
        columns=_ACTIVITY_CONTENT_DAY_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda row: (
            row.date,
            row.focused_seconds,
            row.matched_seconds,
            row.gpt_matched_seconds,
            row.unmatched_seconds,
            row.matched_ratio,
            row.gpt_matched_ratio,
            json.dumps(row.source_counts, sort_keys=True),
        ),
    )
    # Clean up stale duplicates: if any of the dates we just promoted also
    # exist under an older refresh_id, remove the older row. This prevents
    # duplicate-date accumulation when both current-state CLI and materialization DAG
    # promote the same activity-content days with different refresh_ids.
    conn.execute(
        "DELETE FROM activity_content_day "
        "WHERE refresh_id != ? "
        "AND date IN (SELECT DISTINCT date FROM activity_content_day WHERE refresh_id = ?)",
        [refresh_id, refresh_id],
    )
    return count


def promote_activity_content_buckets(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    def bucket_rows() -> Iterable[tuple[date, str, str, float]]:
        dimensions = (
            ("activity", "activity_seconds"),
            ("content_type", "content_type_seconds"),
            ("attention", "attention_seconds"),
            ("topic", "topic_seconds"),
            ("platform", "platform_seconds"),
        )
        for row in rows:
            for dimension, attr in dimensions:
                values = getattr(row, attr)
                for label, seconds in values.items():
                    yield row.date, dimension, label, float(seconds)

    return promote_rows(
        conn,
        table="activity_content_bucket",
        columns=("date", "dimension", "label", "seconds"),
        refresh_id=refresh_id,
        rows=bucket_rows(),
        extractor=lambda row: row,
    )


_ACTIVITY_TITLE_USAGE_COLUMNS = (
    "title_hash",
    "app",
    "normalized_title",
    "example_title",
    "focused_seconds",
    "span_count",
    "first_date",
    "last_date",
    "matched",
    "classification_source",
    "confidence",
    "activity",
    "content_type",
    "attention_level",
    "topic_category",
    "platform",
)


def promote_activity_title_usage(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    return promote_rows(
        conn,
        table="activity_title_usage",
        columns=_ACTIVITY_TITLE_USAGE_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda row: (
            row.title_hash,
            row.app,
            row.normalized_title,
            row.example_title,
            row.focused_seconds,
            row.span_count,
            row.first_date,
            row.last_date,
            row.matched,
            row.classification_source,
            row.confidence,
            row.activity,
            row.content_type,
            row.attention_level,
            row.topic_category,
            row.platform,
        ),
        batch_size=10_000,
    )


# ── sinnix_generation ──────────────────────────────────────────────────────────


def promote_sinnix_generations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    records: Iterable[Any],
) -> int:
    """INSERT sinnix_generation rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="sinnix_generation",
        columns=("host", "generation", "activated_at", "store_path",
                 "sinnix_revision", "nixos_label"),
        refresh_id=refresh_id,
        rows=records,
        extractor=lambda r: (
            r.host or "",
            r.generation or "unknown",
            r.activated_at,
            r.store_path or "",
            r.sinnix_revision or "unknown",
            r.nixos_label or "",
        ),
    )


# ── borg_drill_run ─────────────────────────────────────────────────────────────


def promote_borg_drill_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    runs: Iterable[Any],
) -> int:
    """INSERT borg_drill_run rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="borg_drill_run",
        columns=("repo", "archive", "started_at", "ended_at",
                 "duration_s", "exit_code", "status", "stderr_tail",
                 "within_days"),
        refresh_id=refresh_id,
        rows=runs,
        extractor=lambda r: (
            r.repo or "",
            r.archive or "",
            r.started_at,
            r.ended_at,
            int(r.duration_s or 0),
            int(r.exit_code or 0),
            r.status or "unknown",
            r.stderr_tail or "",
            int(r.within_days or 0),
        ),
    )


def verify_activity_content_integrity(
    conn: "duckdb.DuckDBPyConnection",
) -> dict[str, int]:
    """Post-promotion integrity check for activity_content tables.

    Returns a dict with keys:
      - day_rows:        total rows in activity_content_day
      - day_unique_dates: unique dates (should equal day_rows after dedup)
      - day_duplicates:   duplicate-date count (should be 0)
      - bucket_rows:     total rows in activity_content_bucket
      - usage_rows:      total rows in activity_title_usage
    """
    day_rows = conn.execute(
        "SELECT COUNT(*) FROM activity_content_day"
    ).fetchone()[0]
    day_unique = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM activity_content_day"
    ).fetchone()[0]
    day_dups = day_rows - day_unique
    bucket_rows = conn.execute(
        "SELECT COUNT(*) FROM activity_content_bucket"
    ).fetchone()[0]
    usage_rows = conn.execute(
        "SELECT COUNT(*) FROM activity_title_usage"
    ).fetchone()[0]
    return {
        "day_rows": int(day_rows),
        "day_unique_dates": int(day_unique),
        "day_duplicates": int(day_dups),
        "bucket_rows": int(bucket_rows),
        "usage_rows": int(usage_rows),
    }
