"""Personal-source table promoters for the DuckDB substrate."""

from __future__ import annotations

import logging
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from ._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def _lineage(
    conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str,
) -> list[tuple[str, tuple[tuple[date, date | None], ...]]]:
    """Return newest-first product partitions with the ranges newer ones replaced.

    Each partition carries every replacement range recorded by a newer partition
    in the chain, not only its immediate child's: after a Jan20 and then a Jan10
    replacement, a Jan15 row in the grandparent stays replaced.  An open range
    (``None`` end) is a lineage row written before replacement ends existed.
    """
    result: list[tuple[str, tuple[tuple[date, date | None], ...]]] = []
    seen: set[str] = set()
    current: str | None = refresh_id
    replaced: tuple[tuple[date, date | None], ...] = ()
    while current is not None:
        if current in seen:
            raise ValueError(f"cycle in {product} substrate lineage at {current}")
        seen.add(current)
        row = conn.execute(
            "SELECT predecessor_refresh_id, replacement_start, replacement_end "
            "FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
            [product, current],
        ).fetchone()
        if row is None:
            raise ValueError(f"missing {product} substrate lineage for refresh {current}")
        predecessor, start, end = row
        result.append((current, replaced))
        if predecessor is not None:
            parent = conn.execute(
                "SELECT 1 FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
                [product, predecessor],
            ).fetchone()
            if parent is None:
                raise ValueError(f"missing predecessor {predecessor!r} for {product} refresh {current}")
        if start is not None:
            replaced = (*replaced, (start, end))
        current = str(predecessor) if predecessor is not None else None
    return result


# The column that places a row inside a replacement range. Title metadata is
# undated: its partitions replace keys through rows and tombstones only.
_PRODUCT_DATE_COLUMN = {
    "activity_title_usage": "last_date",
    "title_metadata": None,
}


def _resolved_rows(
    conn: "duckdb.DuckDBPyConnection", *, product: str, refresh_id: str,
    table: str, columns: tuple[str, ...], key,
) -> list[tuple[Any, ...]]:
    date_column = _PRODUCT_DATE_COLUMN.get(product, "date")
    chosen: dict[Any, tuple[Any, ...]] = {}
    blocked: set[Any] = set()
    for partition, replaced in _lineage(conn, product=product, refresh_id=refresh_id):
        sql = f"SELECT {', '.join(columns)} FROM {table} WHERE refresh_id = ?"
        params: list[Any] = [partition]
        if date_column is not None:
            for start, end in replaced:
                if end is None:
                    sql += f" AND {date_column} < ?"
                    params.append(start)
                else:
                    sql += f" AND NOT ({date_column} >= ? AND {date_column} < ?)"
                    params.extend((start, end))
        for row in conn.execute(sql, params).fetchall():
            natural_key = key(row)
            if natural_key not in blocked:
                chosen[natural_key] = row
            blocked.add(natural_key)
        for (natural_key,) in conn.execute(
            "SELECT natural_key FROM substrate_product_tombstone WHERE product = ? AND refresh_id = ?",
            [product, partition],
        ).fetchall():
            if natural_key not in blocked:
                chosen.pop(natural_key, None)
            blocked.add(natural_key)
    return [chosen[natural_key] for natural_key in sorted(chosen, key=repr)]


def _commit_product(
    conn: "duckdb.DuckDBPyConnection", *, product: str, table: str,
    columns: tuple[str, ...], refresh_id: str, rows: list[tuple[Any, ...]],
    predecessor_refresh_id: str | None,
    replacement: tuple[date, date] | None,
    tombstones: Iterable[str] = (),
    input_fingerprint: str | None = None,
    logical_row_count: int | None = None,
    batch_size: int | None = None,
) -> int:
    """Publish one product partition: rows, tombstones and lineage together.

    Callers build ``rows`` completely before calling, so a failing input
    leaves any earlier partition and its metadata untouched.  A failed write
    rolls the whole partition back; the promotion then records an error and
    the outer candidate generation is rejected.
    """
    if predecessor_refresh_id is not None and predecessor_refresh_id == refresh_id:
        raise ValueError(f"{product} refresh {refresh_id} cannot be its own predecessor")
    if replacement is not None and predecessor_refresh_id is None:
        raise ValueError(f"{product} replacement range requires a predecessor refresh_id")
    if replacement is not None and not replacement[0] < replacement[1]:
        raise ValueError(f"empty {product} replacement range {replacement[0]}..{replacement[1]}")
    if predecessor_refresh_id is not None:
        _lineage(conn, product=product, refresh_id=predecessor_refresh_id)
    existing = conn.execute(
        "SELECT 1 FROM substrate_product_lineage WHERE product = ? AND refresh_id = ? "
        f"UNION ALL SELECT 1 FROM {table} WHERE refresh_id = ? LIMIT 1",
        [product, refresh_id, refresh_id],
    ).fetchone()
    if existing is not None:
        # DuckDB 1.1 rejects deleting and re-inserting one primary key in a
        # single transaction (tests/substrate/test_product_lineage.py reproduces
        # it natively), so a re-promoted partition is retired as one committed
        # unit before its replacement commits as another.
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(f"DELETE FROM {table} WHERE refresh_id = ?", [refresh_id])
            conn.execute(
                "DELETE FROM substrate_product_tombstone WHERE product = ? AND refresh_id = ?",
                [product, refresh_id],
            )
            conn.execute(
                "DELETE FROM substrate_product_lineage WHERE product = ? AND refresh_id = ?",
                [product, refresh_id],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    conn.execute("BEGIN TRANSACTION")
    try:
        count = promote_rows(
            conn, table=table, columns=columns, refresh_id=refresh_id, rows=rows,
            extractor=lambda row: row, batch_size=batch_size,
            delete_existing=False, wrap_transaction=False,
        )
        keys = sorted(set(tombstones))
        if keys:
            conn.executemany(
                "INSERT INTO substrate_product_tombstone (product, refresh_id, natural_key) VALUES (?, ?, ?)",
                [(product, refresh_id, natural_key) for natural_key in keys],
            )
        conn.execute(
            "INSERT INTO substrate_product_lineage (product, refresh_id, predecessor_refresh_id, "
            "replacement_start, replacement_end, input_fingerprint, logical_row_count, mode) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                product, refresh_id, predecessor_refresh_id,
                replacement[0] if replacement else None,
                replacement[1] if replacement else None,
                input_fingerprint, logical_row_count,
                "full" if predecessor_refresh_id is None else "incremental",
            ],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return count


def _replacement(
    product: str, previous_refresh_id: str | None,
    replacement_start: date | None, replacement_end: date | None,
) -> tuple[date, date] | None:
    if replacement_start is None and replacement_end is None:
        return None
    if replacement_start is None or replacement_end is None:
        raise ValueError(f"{product} replacement needs both a start and an end")
    if previous_refresh_id is None:
        raise ValueError(f"bounded {product} replacement requires a predecessor refresh_id")
    return replacement_start, replacement_end


def _promote_dated_product(
    conn: "duckdb.DuckDBPyConnection", *, product: str, table: str,
    columns: tuple[str, ...], refresh_id: str, rows: Iterable[tuple[Any, ...]],
    previous_refresh_id: str | None, replacement_start: date | None,
    replacement_end: date | None, row_date, tombstones: Iterable[str] = (),
    batch_size: int | None = None, clip_end: bool = True,
) -> int:
    """Promote a full partition, or one replacing ``[start, end)`` of its predecessor.

    Predecessor rows outside the range stay visible to readers, so a finite
    historical correction never removes later data it did not read.
    ``clip_end=False`` keeps rows whose date lies past the range: a title-usage
    aggregate that overlaps the range carries its last date beyond it.
    """
    replacement = _replacement(product, previous_refresh_id, replacement_start, replacement_end)
    built = list(rows)
    if replacement is not None:
        start, end = replacement
        built = [
            row for row in built
            if row_date(row) >= start and (not clip_end or row_date(row) < end)
        ]
    return _commit_product(
        conn, product=product, table=table, columns=columns, refresh_id=refresh_id,
        rows=built, predecessor_refresh_id=previous_refresh_id if replacement else None,
        replacement=replacement, tombstones=tombstones, batch_size=batch_size,
    )


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
    return read_personal_daily_signals(
        conn, refresh_id=refresh_id, start=start, end=end, source=source,
        metric=metric, limit=limit,
    ).rows


@dataclass(frozen=True)
class PersonalSignalSourceWindow:
    """What the resolved product holds for one signal source in a window."""

    source: str
    row_count: int
    observed_dates: tuple[date, ...]


@dataclass(frozen=True)
class PersonalDailySignalRead:
    """One coherent product read: bounded rows plus unbounded per-source counts.

    ``product_sources`` is every source the resolved product carries on any
    date (for the requested metric), so a source absent from the window can
    be told apart from a source the projection never carries.
    """

    rows: list[tuple[Any, ...]]
    window_row_count: int
    sources: dict[str, PersonalSignalSourceWindow]
    product_sources: frozenset[str]


def read_personal_daily_signals(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    source: str | None = None,
    metric: str | None = None,
    limit: int = 1000,
) -> PersonalDailySignalRead:
    """Resolve the product at ``refresh_id`` once and summarize the window.

    The product is resolved through its lineage, so a revised key carries its
    newest value and a tombstoned key is absent; ``end`` is inclusive.
    """
    resolved = _resolved_rows(
        conn, product="personal_daily_signals", refresh_id=refresh_id,
        table="personal_daily_signal",
        columns=("source", "date", "metric", "value", "dimensions", "dimension_key"),
        key=lambda row: (row[0], row[1], row[2], row[5]),
    )
    product_sources = frozenset(str(row[0]) for row in resolved if metric is None or row[2] == metric)
    rows = [row for row in resolved if (start is None or row[1] >= start) and (end is None or row[1] <= end)
            and (source is None or row[0] == source) and (metric is None or row[2] == metric)]
    rows.sort(key=lambda row: (row[1], row[0], row[2]))
    counts: dict[str, int] = defaultdict(int)
    dates: dict[str, set[date]] = defaultdict(set)
    for row in rows:
        counts[row[0]] += 1
        dates[row[0]].add(row[1])
    return PersonalDailySignalRead(
        rows=[row[:5] for row in rows[:min(max(limit, 1), 10_000)]],
        window_row_count=len(rows),
        sources={
            name: PersonalSignalSourceWindow(name, counts[name], tuple(sorted(dates[name])))
            for name in sorted(counts)
        },
        product_sources=product_sources,
    )


__all__ = [
    "load_operator_day_rows",
    "load_personal_daily_signals",
    "load_spotify_daily_rows",
    "PersonalDailySignalRead",
    "PersonalSignalSourceWindow",
    "read_personal_daily_signals",
    "promote_activity_content_buckets",
    "promote_activity_content_days",
    "promote_activity_title_usage",
    "promote_borg_drill_runs",
    "promote_operator_day_rows",
    "promote_personal_daily_signals",
    "promote_sinnix_generations",
    "promote_spotify_daily_rows",
    "promote_title_classifications_from_path",
    "TitleClassificationPromotion",
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
    replacement_start: date | None = None,
    replacement_end: date | None = None,
) -> int:
    """INSERT normalized daily personal-source signals as one product partition.

    With a replacement range the partition holds only the newly coalesced
    rows for ``[replacement_start, replacement_end)``; readers carry the
    verified predecessor outside that range.  An empty range is an intentional
    replacement rather than a reason to retain stale dates inside it.
    """
    def extract(row: tuple[str, date, str, float, dict[str, Any]]) -> tuple[Any, ...]:
        dimensions = json.dumps(row[4], sort_keys=True)
        return row[0], row[1], row[2], float(row[3]), dimensions, dimensions

    return _promote_dated_product(
        conn, product="personal_daily_signals", table="personal_daily_signal",
        columns=_PERSONAL_DAILY_SIGNAL_COLUMNS, refresh_id=refresh_id,
        rows=(extract(row) for row in _coalesce_daily_signals(rows)),
        previous_refresh_id=previous_refresh_id,
        replacement_start=replacement_start, replacement_end=replacement_end,
        row_date=lambda row: row[1],
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


@dataclass(frozen=True)
class TitleClassificationPromotion:
    """Rows one refresh wrote, and the titles readers resolve through it."""

    written: int
    logical: int


def promote_title_classifications_from_path(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    path: str,
    previous_refresh_id: str | None = None,
    input_fingerprint: str | None = None,
) -> TitleClassificationPromotion:
    """Promote only changed title keys, with tombstones for removals.

    An unchanged input fingerprint writes no rows and inherits every title
    from the predecessor, whose lineage already records how many there are.
    """
    predecessor = previous_refresh_id
    if predecessor is not None and predecessor == refresh_id:
        raise ValueError(f"title_metadata refresh {refresh_id} cannot be its own predecessor")
    inherited: tuple[Any, ...] | None = None
    if predecessor is not None:
        _lineage(conn, product="title_metadata", refresh_id=predecessor)
        inherited = conn.execute(
            "SELECT input_fingerprint, logical_row_count FROM substrate_product_lineage "
            "WHERE product = 'title_metadata' AND refresh_id = ?",
            [predecessor],
        ).fetchone()
    if (
        inherited is not None
        and input_fingerprint is not None
        and inherited[0] == input_fingerprint
        and inherited[1] is not None
    ):
        logical = int(inherited[1])
        _commit_product(
            conn, product="title_metadata", table="title_classification",
            columns=_TITLE_CLASSIFICATION_COLUMNS, refresh_id=refresh_id, rows=[],
            predecessor_refresh_id=predecessor, replacement=None,
            input_fingerprint=input_fingerprint, logical_row_count=logical,
        )
        return TitleClassificationPromotion(written=0, logical=logical)
    current = conn.execute(
        """SELECT title_hash, COALESCE(app, ''), raw_title, COALESCE(normalized_title, ''), activity, subject,
        content_type, attention_level, topic_category, platform, mode, app_kind, tool, domain,
        domain_category, is_ai_tool, is_ai_active, productivity_score, focus_score, confidence,
        classification_source, model_version, '{}'::JSON FROM read_json_auto(?)
        WHERE title_hash IS NOT NULL QUALIFY ROW_NUMBER() OVER
        (PARTITION BY title_hash ORDER BY confidence DESC NULLS LAST, app, normalized_title) = 1""", [path]
    ).fetchall()
    old_rows = {} if predecessor is None else {
        row[0]: row for row in _resolved_rows(
            conn, product="title_metadata", refresh_id=predecessor,
            table="title_classification", columns=_TITLE_CLASSIFICATION_COLUMNS, key=lambda row: row[0]
        )
    }
    current_keys = {row[0] for row in current}
    written = _commit_product(
        conn, product="title_metadata", table="title_classification",
        columns=_TITLE_CLASSIFICATION_COLUMNS, refresh_id=refresh_id,
        rows=[tuple(row) for row in current if old_rows.get(row[0]) != tuple(row)],
        predecessor_refresh_id=predecessor, replacement=None,
        tombstones=set(old_rows) - current_keys,
        input_fingerprint=input_fingerprint, logical_row_count=len(current_keys),
        batch_size=10_000,
    )
    return TitleClassificationPromotion(written=written, logical=len(current_keys))


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
    replacement_start: date | None = None,
    replacement_end: date | None = None,
) -> int:
    return _promote_dated_product(
        conn,
        product="activity_content_day", table="activity_content_day",
        columns=_ACTIVITY_CONTENT_DAY_COLUMNS,
        refresh_id=refresh_id,
        rows=(
            (
                row.date,
                row.focused_seconds,
                row.matched_seconds,
                row.gpt_matched_seconds,
                row.unmatched_seconds,
                row.matched_ratio,
                row.gpt_matched_ratio,
                json.dumps(row.source_counts, sort_keys=True),
            )
            for row in rows
        ),
        previous_refresh_id=previous_refresh_id,
        replacement_start=replacement_start, replacement_end=replacement_end,
        row_date=lambda row: row[0],
    )


def promote_activity_content_buckets(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    previous_refresh_id: str | None = None,
    replacement_start: date | None = None,
    replacement_end: date | None = None,
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

    return _promote_dated_product(
        conn,
        product="activity_content_bucket", table="activity_content_bucket",
        columns=("date", "dimension", "label", "seconds"),
        refresh_id=refresh_id,
        rows=bucket_rows(),
        previous_refresh_id=previous_refresh_id,
        replacement_start=replacement_start, replacement_end=replacement_end,
        row_date=lambda row: row[0],
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


def _title_usage_key(row: tuple[Any, ...]) -> str:
    return f"{row[0]}\x1f{row[1]}"


def promote_activity_title_usage(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    previous_refresh_id: str | None = None,
    replacement_start: date | None = None,
    replacement_end: date | None = None,
) -> int:
    built = [
        (
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
        )
        for row in rows
    ]
    replacement = _replacement(
        "activity_title_usage", previous_refresh_id, replacement_start, replacement_end,
    )
    removed: set[str] = set()
    if replacement is not None and previous_refresh_id is not None:
        # Predecessor titles last seen inside the range that this read no
        # longer reports are removed; titles last seen elsewhere were not read.
        start, end = replacement
        current_keys = {_title_usage_key(row) for row in built}
        removed = {
            _title_usage_key(row) for row in _resolved_rows(
                conn, product="activity_title_usage", refresh_id=previous_refresh_id,
                table="activity_title_usage", columns=_ACTIVITY_TITLE_USAGE_COLUMNS,
                key=_title_usage_key,
            )
            if row[7] is not None and start <= row[7] < end
        } - current_keys
    return _promote_dated_product(
        conn,
        product="activity_title_usage", table="activity_title_usage",
        columns=_ACTIVITY_TITLE_USAGE_COLUMNS,
        refresh_id=refresh_id,
        rows=built,
        previous_refresh_id=previous_refresh_id,
        replacement_start=replacement_start, replacement_end=replacement_end,
        row_date=lambda row: row[7],
        tombstones=removed,
        batch_size=10_000,
        clip_end=False,
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
