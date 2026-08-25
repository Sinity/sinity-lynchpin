"""Personal-source table promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any
from types import SimpleNamespace

from ._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def _lineage(
    conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str,
) -> list[tuple[str, date | None]]:
    """Return newest-first product partitions, rejecting broken ancestry."""
    result: list[tuple[str, date | None]] = []
    seen: set[str] = set()
    current: str | None = refresh_id
    cutoff_for_current: date | None = None
    while current is not None:
        if current in seen:
            raise ValueError(f"cycle in {product} substrate lineage at {current}")
        seen.add(current)
        row = conn.execute(
            "SELECT predecessor_refresh_id, replacement_start FROM substrate_product_lineage "
            "WHERE product = ? AND refresh_id = ?", [product, current]
        ).fetchone()
        if row is None:
            raise ValueError(f"missing {product} substrate lineage for refresh {current}")
        predecessor, cutoff = row
        result.append((current, cutoff_for_current))
        if predecessor is not None:
            parent = conn.execute(
                "SELECT 1 FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
                [product, predecessor],
            ).fetchone()
            if parent is None:
                raise ValueError(f"missing predecessor {predecessor!r} for {product} refresh {current}")
        cutoff_for_current = cutoff
        current = str(predecessor) if predecessor is not None else None
    return result


def _begin_product(conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str) -> None:
    conn.execute("DELETE FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?", [product, refresh_id])
    conn.execute("DELETE FROM substrate_product_tombstone WHERE product = ? AND refresh_id = ?", [product, refresh_id])


def _record_product(
    conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str,
    predecessor_refresh_id: str | None, replacement_start: date | None,
    input_fingerprint: str | None, mode: str,
) -> None:
    conn.execute(
        "INSERT INTO substrate_product_lineage VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
        [product, refresh_id, predecessor_refresh_id, replacement_start, input_fingerprint, mode],
    )


def _resolved_rows(
    conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str,
    table: str, columns: tuple[str, ...], key,
) -> list[tuple[Any, ...]]:
    chosen: dict[Any, tuple[Any, ...]] = {}
    blocked: set[Any] = set()
    for partition, cutoff in _lineage(conn, product=product, refresh_id=refresh_id):
        sql = f"SELECT {', '.join(columns)} FROM {table} WHERE refresh_id = ?"
        params: list[Any] = [partition]
        if cutoff is not None and product == "activity_title_usage":
            sql += " AND last_date < ?"
            params.append(cutoff)
        elif cutoff is not None and product != "title_metadata":
            sql += " AND date < ?"
            params.append(cutoff)
        for row in conn.execute(sql, params).fetchall():
            natural_key = key(row)
            if natural_key not in blocked:
                chosen[natural_key] = row
            blocked.add(natural_key)
        for (natural_key,) in conn.execute(
            "SELECT natural_key FROM substrate_product_tombstone WHERE product = ? AND refresh_id = ?",
            [product, partition],
        ).fetchall():
            blocked.add(natural_key)
            chosen.pop(natural_key, None)
    return list(chosen.values())


def _promote_tail_rows(
    conn: "duckdb.DuckDBPyConnection", *, product: str, table: str,
    columns: tuple[str, ...], refresh_id: str, rows: Iterable[Any], extractor,
    previous_refresh_id: str | None, tail_start: date | None, batch_size: int | None = None,
    date_getter=None,
) -> int:
    if tail_start is None:
        _begin_product(conn, product=product, refresh_id=refresh_id)
        count = promote_rows(conn, table=table, columns=columns, refresh_id=refresh_id,
                             rows=rows, extractor=extractor, batch_size=batch_size)
        _record_product(conn, product=product, refresh_id=refresh_id,
                        predecessor_refresh_id=None, replacement_start=None,
                        input_fingerprint=None, mode="full")
        return count
    if previous_refresh_id is None:
        raise ValueError(f"incremental {product} promotion requires a predecessor refresh_id")
    if previous_refresh_id == refresh_id:
        old = conn.execute(
            "SELECT predecessor_refresh_id, replacement_start FROM substrate_product_lineage "
            "WHERE product = ? AND refresh_id = ?", [product, refresh_id]
        ).fetchone()
        if old is None:
            raise ValueError(f"missing {product} substrate lineage for refresh {refresh_id}")
        previous_refresh_id = old[0]
    _lineage(conn, product=product, refresh_id=previous_refresh_id)
    _begin_product(conn, product=product, refresh_id=refresh_id)
    def row_date(row: Any) -> date:
        if date_getter is not None:
            return date_getter(row)
        return row.date if hasattr(row, "date") else row[1]
    filtered = (row for row in rows if row_date(row) >= tail_start)
    count = promote_rows(conn, table=table, columns=columns, refresh_id=refresh_id,
                         rows=filtered, extractor=extractor, batch_size=batch_size)
    _record_product(conn, product=product, refresh_id=refresh_id,
                    predecessor_refresh_id=previous_refresh_id, replacement_start=tail_start,
                    input_fingerprint=None, mode="incremental")
    return count


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
    rows = _resolved_rows(
        conn, product="personal_daily_signals", refresh_id=refresh_id,
        table="personal_daily_signal",
        columns=("source", "date", "metric", "value", "dimensions", "dimension_key"),
        key=lambda row: (row[0], row[1], row[2], row[5]),
    )
    rows = [row for row in rows if (start is None or row[1] >= start) and (end is None or row[1] <= end)
            and (source is None or row[0] == source) and (metric is None or row[2] == metric)]
    rows.sort(key=lambda row: (row[1], row[0], row[2]))
    return [row[:5] for row in rows[:min(max(limit, 1), 10_000)]]


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
    previous_refresh_id: str | None = None,
    incremental_tail_start: date | None = None,
) -> int:
    """INSERT normalized daily personal-source signals.

    An incremental snapshot is a new refresh partition.  Copy the verified
    predecessor before the tail, then replace only ``[incremental_tail_start,
    end)`` with the newly coalesced rows.  This keeps sparse coverage and
    makes an empty tail an intentional replacement rather than a reason to
    retain stale dates.
    """
    if incremental_tail_start is not None:
        if previous_refresh_id is None:
            raise ValueError("incremental daily-signal promotion requires a predecessor refresh_id")
        return _promote_incremental_personal_daily_signals(
            conn,
            refresh_id=refresh_id,
            previous_refresh_id=previous_refresh_id,
            tail_start=incremental_tail_start,
            rows=rows,
        )

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

    count = promote_rows(
        conn,
        table="personal_daily_signal",
        columns=_PERSONAL_DAILY_SIGNAL_COLUMNS,
        refresh_id=refresh_id,
        rows=_coalesce_daily_signals(rows),
        extractor=extract,
    )
    _begin_product(conn, product="personal_daily_signals", refresh_id=refresh_id)
    _record_product(conn, product="personal_daily_signals", refresh_id=refresh_id,
                    predecessor_refresh_id=None, replacement_start=None,
                    input_fingerprint=None, mode="full")
    return count


def _promote_incremental_personal_daily_signals(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    previous_refresh_id: str,
    tail_start: date,
    rows: Iterable[tuple[str, date, str, float, dict[str, Any]]],
) -> int:
    """Promote only the replacement tail; readers carry the predecessor."""
    def extract(row: tuple[str, date, str, float, dict[str, Any]]) -> tuple[Any, ...]:
        dimensions = json.dumps(row[4], sort_keys=True)
        return row[0], row[1], row[2], float(row[3]), dimensions, dimensions
    return _promote_tail_rows(
        conn, product="personal_daily_signals", table="personal_daily_signal",
        columns=_PERSONAL_DAILY_SIGNAL_COLUMNS, refresh_id=refresh_id,
        rows=_coalesce_daily_signals(rows), extractor=extract,
        previous_refresh_id=previous_refresh_id, tail_start=tail_start,
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
    previous_refresh_id: str | None = None,
    incremental_tail_start: date | None = None,
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

    return _promote_tail_rows(
        conn,
        product="title_metadata", table="title_classification",
        columns=_TITLE_CLASSIFICATION_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=extract,
        batch_size=10_000,
        previous_refresh_id=previous_refresh_id, tail_start=incremental_tail_start,
    )


def promote_title_classifications_from_path(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    path: str,
    previous_refresh_id: str | None = None,
    input_fingerprint: str | None = None,
) -> int:
    """Promote only changed title keys, with tombstones for removals."""
    predecessor = previous_refresh_id
    if predecessor == refresh_id:
        old = conn.execute(
            "SELECT predecessor_refresh_id FROM substrate_product_lineage WHERE product='title_metadata' AND refresh_id=?",
            [refresh_id],
        ).fetchone()
        predecessor = old[0] if old else None
    previous_fingerprint = None
    if predecessor is not None:
        _lineage(conn, product="title_metadata", refresh_id=predecessor)
        previous_fingerprint = conn.execute(
            "SELECT input_fingerprint FROM substrate_product_lineage WHERE product='title_metadata' AND refresh_id=?",
            [predecessor],
        ).fetchone()[0]
    _begin_product(conn, product="title_metadata", refresh_id=refresh_id)
    conn.execute("DELETE FROM title_classification WHERE refresh_id = ?", [refresh_id])
    if predecessor is not None and input_fingerprint is not None and input_fingerprint == previous_fingerprint:
        _record_product(conn, product="title_metadata", refresh_id=refresh_id,
                        predecessor_refresh_id=predecessor, replacement_start=None,
                        input_fingerprint=input_fingerprint, mode="incremental")
        return 0
    result = conn.execute(
        """SELECT title_hash, COALESCE(app, ''), raw_title, COALESCE(normalized_title, ''), activity, subject,
        content_type, attention_level, topic_category, platform, mode, app_kind, tool, domain,
        domain_category, is_ai_tool, is_ai_active, productivity_score, focus_score, confidence,
        classification_source, model_version, '{}'::JSON FROM read_json_auto(?)
        WHERE title_hash IS NOT NULL QUALIFY ROW_NUMBER() OVER
        (PARTITION BY title_hash ORDER BY confidence DESC NULLS LAST, app, normalized_title) = 1""", [path]
    )
    current = result.fetchall()
    old_rows = {} if predecessor is None else {
        row[0]: row for row in _resolved_rows(
            conn, product="title_metadata", refresh_id=predecessor,
            table="title_classification", columns=(*_TITLE_CLASSIFICATION_COLUMNS,), key=lambda row: row[0]
        )
    }
    current_keys = {row[0] for row in current}
    changed = [SimpleNamespace(**dict(zip(_TITLE_CLASSIFICATION_COLUMNS, row)))
               for row in current if row[0] not in old_rows or tuple(row) != old_rows[row[0]]]
    count = promote_title_classifications(conn, refresh_id=refresh_id, rows=changed)
    for title_hash in sorted(set(old_rows) - current_keys):
        conn.execute("INSERT INTO substrate_product_tombstone VALUES ('title_metadata', ?, ?, CURRENT_TIMESTAMP)",
                     [refresh_id, title_hash])
    # promote_title_classifications recorded a self-contained lineage; replace it with the overlay metadata.
    conn.execute("DELETE FROM substrate_product_lineage WHERE product='title_metadata' AND refresh_id=?", [refresh_id])
    _record_product(conn, product="title_metadata", refresh_id=refresh_id,
                    predecessor_refresh_id=predecessor, replacement_start=None,
                    input_fingerprint=input_fingerprint, mode="incremental" if predecessor else "full")
    return count


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
    previous_refresh_id: str | None = None,
    incremental_tail_start: date | None = None,
) -> int:
    count = _promote_tail_rows(
        conn,
        product="activity_content_day", table="activity_content_day",
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
        previous_refresh_id=previous_refresh_id, tail_start=incremental_tail_start,
        date_getter=lambda row: row.date,
    )
    return count


def promote_activity_content_buckets(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    previous_refresh_id: str | None = None,
    incremental_tail_start: date | None = None,
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

    return _promote_tail_rows(
        conn,
        product="activity_content_bucket", table="activity_content_bucket",
        columns=("date", "dimension", "label", "seconds"),
        refresh_id=refresh_id,
        rows=bucket_rows(),
        extractor=lambda row: row,
        previous_refresh_id=previous_refresh_id, tail_start=incremental_tail_start,
        date_getter=lambda row: row[0],
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
    previous_refresh_id: str | None = None,
    incremental_tail_start: date | None = None,
) -> int:
    materialized_rows = list(rows) if incremental_tail_start is not None else rows
    predecessor_keys: set[tuple[str, str]] = set()
    if incremental_tail_start is not None and previous_refresh_id is not None:
        predecessor_keys = {
            (row[0], row[1]) for row in _resolved_rows(
                conn, product="activity_title_usage", refresh_id=previous_refresh_id,
                table="activity_title_usage", columns=_ACTIVITY_TITLE_USAGE_COLUMNS,
                key=lambda row: f"{row[0]}\x1f{row[1]}",
            ) if row[7] is not None and row[7] >= incremental_tail_start
        }
    count = _promote_tail_rows(
        conn,
        product="activity_title_usage", table="activity_title_usage",
        columns=_ACTIVITY_TITLE_USAGE_COLUMNS,
        refresh_id=refresh_id,
        rows=materialized_rows,
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
        previous_refresh_id=previous_refresh_id, tail_start=incremental_tail_start,
        date_getter=lambda row: row.last_date,
    )
    if predecessor_keys:
        current_keys = {(row.title_hash, row.app) for row in materialized_rows}
        for title_hash, app in sorted(predecessor_keys - current_keys):
            conn.execute(
                "INSERT INTO substrate_product_tombstone VALUES ('activity_title_usage', ?, ?, CURRENT_TIMESTAMP)",
                [refresh_id, f"{title_hash}\x1f{app}"],
            )
    return count


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
