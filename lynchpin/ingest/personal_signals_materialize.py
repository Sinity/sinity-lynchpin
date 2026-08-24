"""Materialize derived daily signal products from canonical source products."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.personal_signals import (
    personal_daily_signals_path,
    spotify_daily_path,
)
from .exports_materialize import spotify_streams_path
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import (
    atomic_write_indexed_ndjson,
    atomic_write_ndjson,
    guard_incremental_shrinkage,
    replace_indexed_ndjson_tail,
    write_manifest,
)


SignalRow = tuple[str, date, str, float, dict[str, Any]]
PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION = 2
SPOTIFY_DAILY_SCHEMA_VERSION = 1


def materialize_personal_daily_signals(
    *,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    output = output or personal_daily_signals_path()
    bounded_tail = False
    previous_manifest: dict[str, Any] = {}
    if (start is None) != (end is None):
        raise MaterializationError("personal_signals_materialize", reason="personal daily-signal materialization requires both start and end")
    if start is not None and end is not None:
        if end <= start:
            raise MaterializationError("personal_signals_materialize", reason="personal daily-signal materialization end must be after start")
        window_rows, input_files = _window_personal_daily_signal_rows_with_inputs(start, end)
        previous_manifest = _read_manifest(output.with_suffix(".manifest.json"))
        bounded_tail = _can_replace_tail(output, previous_manifest, start=start, end=end)
        if output.exists() and not bounded_tail:
            raise MaterializationError(
                "lynchpin.personal_daily_signals",
                reason="incremental daily-signal materialization requires an indexed append-compatible tail",
            )
        rows = window_rows
        signal_dates = [row[1] for row in (window_rows if bounded_tail else rows)]
        covered_dates = _merge_covered_dates(
            manifest=output.with_suffix(".manifest.json"),
            start=start,
            end=end,
            verified_bounds=_combined_bounds(previous_manifest, signal_dates),
        )
    else:
        rows, input_files = _personal_daily_signal_rows_with_inputs()
        covered_dates = tuple(sorted({row[1] for row in rows}))
    rows.sort(key=lambda row: (row[1], row[0], row[2], json.dumps(row[4], sort_keys=True)))
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded_rows = [
        {
            "source": source,
            "date": day.isoformat(),
            "metric": metric,
            "value": value,
            "dimensions": dimensions,
        }
        for source, day, metric, value, dimensions in rows
    ]
    old_counts = _int_dict(previous_manifest.get("row_counts"))
    if start is not None and end is not None and bounded_tail:
        row_counts = {}
        for day, count in old_counts.items():
            try:
                if date.fromisoformat(day) < start:
                    row_counts[day] = count
            except ValueError:
                continue
        for row in encoded_rows:
            day = str(row["date"])
            row_counts[day] = row_counts.get(day, 0) + 1
        total_row_count = sum(row_counts.values())
    else:
        row_counts = dict(Counter(str(row["date"]) for row in encoded_rows))
        total_row_count = len(encoded_rows)
    if start is not None and end is not None:
        guard_incremental_shrinkage(
            output.with_suffix(".manifest.json"),
            total_row_count,
            dataset="lynchpin.personal_daily_signals",
        )
    if start is not None and end is not None and bounded_tail:
        row_offsets = replace_indexed_ndjson_tail(
            output,
            encoded_rows,
            start=start,
            date_getter=lambda row: date.fromisoformat(str(row["date"])),
            offsets=previous_manifest.get("row_offsets"),
        )
    else:
        row_offsets = atomic_write_indexed_ndjson(
            output,
            encoded_rows,
            date_getter=lambda row: date.fromisoformat(str(row["date"])),
        )
    counts = Counter(str(row["source"]) for row in encoded_rows)
    all_dates = [row[1] for row in rows]
    if bounded_tail:
        for key in ("first_date", "last_date"):
            raw = previous_manifest.get(key)
            if isinstance(raw, str):
                try:
                    all_dates.append(date.fromisoformat(raw))
                except ValueError:
                    pass
    manifest = _manifest(
        dataset="lynchpin.personal_daily_signals",
        schema_version=PERSONAL_DAILY_SIGNALS_SCHEMA_VERSION,
        output=output,
        row_count=total_row_count,
        first_date=min(all_dates, default=None),
        last_date=max(all_dates, default=None),
        source_counts=dict(sorted(counts.items())),
        input_files=input_files,
        covered_dates=covered_dates,
        window_start=start,
        window_end=end,
        row_offsets=row_offsets,
        row_counts=dict(sorted(row_counts.items())),
        refresh_id=refresh_id,
    )
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def materialize_spotify_daily(
    *,
    output: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    from collections import Counter as _Counter

    from ..core.primitives import logical_date
    from ..sources.spotify import iter_streams

    output = output or spotify_daily_path()
    if (start is None) != (end is None):
        raise MaterializationError("personal_signals_materialize", reason="Spotify daily materialization requires both start and end")
    if start is not None and end is not None and end <= start:
        raise MaterializationError("personal_signals_materialize", reason="Spotify daily materialization end must be after start")
    input_files = spotify_daily_input_files()
    by_day: dict[date, list[Any]] = defaultdict(list)
    for stream in iter_streams(start=start, end=end):
        end_time = getattr(stream, "end_time", None)
        if end_time is None:
            continue
        day = logical_date(end_time)
        if start is not None and end is not None and not (start <= day < end):
            continue
        by_day[day].append(stream)

    window_rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        streams = by_day[day]
        artists = _Counter(str(getattr(row, "artist", "")) for row in streams if getattr(row, "artist", ""))
        tracks = _Counter(str(getattr(row, "track", "")) for row in streams if getattr(row, "track", ""))
        window_rows.append(
            {
                "date": day.isoformat(),
                "track_count": len(streams),
                "minutes_played": round(sum((getattr(row, "ms_played", 0) or 0) / 60_000 for row in streams), 1),
                "unique_artists": len(artists),
                "unique_tracks": len(tracks),
                "top_artists": [name for name, _ in artists.most_common(5)],
                "top_tracks": [name for name, _ in tracks.most_common(5)],
            }
        )
    if start is not None and end is not None:
        rows = _merge_existing_spotify_daily_rows(
            output=output,
            start=start,
            end=end,
            window_rows=window_rows,
        )
        spotify_dates = [date.fromisoformat(str(row["date"])) for row in rows]
        covered_dates = _merge_covered_dates(
            manifest=output.with_suffix(".manifest.json"),
            start=start,
            end=end,
            verified_bounds=(min(spotify_dates), max(spotify_dates)) if spotify_dates else None,
        )
    else:
        rows = window_rows
        covered_dates = tuple(sorted(by_day))
    rows.sort(key=lambda row: str(row["date"]))

    output.parent.mkdir(parents=True, exist_ok=True)
    if start is not None and end is not None:
        guard_incremental_shrinkage(
            output.with_suffix(".manifest.json"),
            len(rows),
            dataset="lynchpin.spotify_daily",
        )
    atomic_write_ndjson(output, rows)
    manifest = _manifest(
        dataset="lynchpin.spotify_daily",
        schema_version=SPOTIFY_DAILY_SCHEMA_VERSION,
        output=output,
        row_count=len(rows),
        first_date=covered_dates[0] if covered_dates else None,
        last_date=covered_dates[-1] if covered_dates else None,
        input_files=input_files,
        covered_dates=covered_dates,
        window_start=start,
        window_end=end,
    )
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def spotify_daily_input_files() -> tuple[Path, ...]:
    path = spotify_streams_path()
    return (path,) if path.exists() else ()


def _merge_existing_spotify_daily_rows(
    *,
    output: Path,
    start: date,
    end: date,
    window_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    outside_window = [
        row for row in _read_existing_spotify_daily_rows(output)
        if not (start <= date.fromisoformat(str(row["date"])) < end)
    ]
    return [*outside_window, *window_rows]


def _read_existing_spotify_daily_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("date"):
                rows.append(payload)
    return rows


def _personal_daily_signal_rows() -> Iterable[SignalRow]:
    rows, _input_files = _personal_daily_signal_rows_with_inputs()
    return rows


def _personal_daily_signal_rows_with_inputs() -> tuple[list[SignalRow], tuple[Path, ...]]:
    from ..materialization import audit_materialization

    audit_by_name = {row.name: row for row in audit_materialization()}
    bounds = [
        (row.first_date, row.last_date)
        for row in audit_by_name.values()
        if row.status == "ready" and row.first_date is not None and row.last_date is not None
    ]
    if not bounds:
        return [], ()
    start = min(first for first, _last in bounds if first is not None)
    end = max(last for _first, last in bounds if last is not None) + timedelta(days=1)
    rows = _window_personal_daily_signal_rows(start, end, audit_by_name)
    source_counts = Counter(source for source, *_ in rows)
    return rows, _personal_daily_signal_input_files(audit_by_name, source_counts.keys())


def _window_personal_daily_signal_rows_with_inputs(
    window_start: date,
    window_end: date,
) -> tuple[list[SignalRow], tuple[Path, ...]]:
    from ..materialization import audit_materialization

    audit_by_name = {row.name: row for row in audit_materialization()}
    rows = _window_personal_daily_signal_rows(window_start, window_end, audit_by_name)
    source_counts = Counter(source for source, *_ in rows)
    input_sources = set(source_counts) | _overlapping_input_sources(
        audit_by_name,
        window_start,
        window_end,
    )
    return rows, _personal_daily_signal_input_files(audit_by_name, input_sources)


def _overlapping_input_sources(
    audit_by_name: dict[str, Any],
    window_start: date,
    window_end: date,
) -> set[str]:
    return {
        name
        for name in audit_by_name
        if name != "personal_daily_signals"
        and _overlaps(audit_by_name, name, window_start, window_end)
    }


def _window_personal_daily_signal_rows(
    window_start: date,
    window_end: date,
    audit_by_name: dict[str, Any],
) -> list[SignalRow]:
    rows: list[SignalRow] = []

    def add(source: str, day: date, metric: str, value: float, **dimensions: Any) -> None:
        if window_start <= day < window_end:
            rows.append((source, day, metric, value, dimensions))

    from ..sources.web import daily_browsing

    clip = _clip_window(audit_by_name, "webhistory", window_start, window_end)
    if clip is not None:
        clip_start, clip_end = clip
        for web_row in daily_browsing(start=clip_start, end=_inclusive_end(clip_end), ensure=False):
            top_domain = web_row.top_domains[0][0] if web_row.top_domains else ""
            add("webhistory", web_row.date, "visit_count", web_row.visit_count, domain_count=web_row.unique_domains, top_domain=top_domain)

    clip = _clip_window(audit_by_name, "browser_bookmarks", window_start, window_end)
    if clip is not None:
        from ..sources.bookmarks import daily_bookmark_activity

        clip_start, clip_end = clip
        for bookmark_row in daily_bookmark_activity(start=clip_start, end=clip_end, ensure=False):
            add("browser_bookmarks", bookmark_row.date, "bookmark_count", bookmark_row.bookmark_count, domain_count=bookmark_row.domain_count, top_domain=bookmark_row.top_domain)

    clip = _clip_window(audit_by_name, "communications", window_start, window_end)
    if clip is not None:
        from ..sources.communications import daily_communication_activity

        clip_start, clip_end = clip
        for comm_row in daily_communication_activity(start=clip_start, end=clip_end, ensure=False):
            add("communications", comm_row.date, "event_count", comm_row.event_count, outbound_count=comm_row.outbound_count, source_count=comm_row.source_count)

    clip = _clip_window(audit_by_name, "arbtt", window_start, window_end)
    if clip is not None:
        from ..sources.arbtt import daily_arbtt_activity

        clip_start, clip_end = clip
        for arbtt_row in daily_arbtt_activity(start=clip_start, end=clip_end, ensure=False):
            add("arbtt", arbtt_row.date, "active_minutes", arbtt_row.active_minutes, event_count=arbtt_row.event_count, program_count=arbtt_row.program_count)

    clip = _clip_window(audit_by_name, "activity_content", window_start, window_end)
    if clip is not None:
        from ..sources.activity_content import iter_activity_content_days

        clip_start, clip_end = clip
        for content_row in iter_activity_content_days(start=clip_start, end=clip_end, ensure=False):
            add("activity_content", content_row.date, "focused_minutes", content_row.focused_seconds / 60.0)
            add("activity_content", content_row.date, "title_metadata_matched_ratio", content_row.matched_ratio)
            add("activity_content", content_row.date, "gpt_title_matched_ratio", content_row.gpt_matched_ratio)
            for activity, seconds in content_row.activity_seconds.items():
                add("activity_content", content_row.date, "activity_minutes", seconds / 60.0, activity=activity)
            for topic, seconds in content_row.topic_seconds.items():
                add("activity_content", content_row.date, "topic_minutes", seconds / 60.0, topic=topic)

    clip = _clip_window(audit_by_name, "health", window_start, window_end)
    if clip is not None:
        from ..sources.health import daily_health_summary

        clip_start, clip_end = clip
        for health_row in daily_health_summary(start=clip_start, end=_inclusive_end(clip_end)):
            if health_row.steps is not None:
                add("health", health_row.date, "steps", float(health_row.steps))
            if health_row.stress_avg is not None:
                add("health", health_row.date, "stress_avg", float(health_row.stress_avg), count=health_row.stress_count)
            if health_row.heart_rate_avg is not None:
                add("health", health_row.date, "avg_heart_rate", float(health_row.heart_rate_avg))
            if health_row.heart_rate_resting is not None:
                add("health", health_row.date, "resting_heart_rate", float(health_row.heart_rate_resting))
            if health_row.hrv_rmssd_avg is not None:
                add("health", health_row.date, "hrv_rmssd", float(health_row.hrv_rmssd_avg), count=health_row.hrv_count)
            if health_row.spo2_avg is not None:
                add("health", health_row.date, "spo2_avg", float(health_row.spo2_avg), count=health_row.spo2_count)
            if health_row.vitality_score is not None:
                add("health", health_row.date, "vitality_score", float(health_row.vitality_score), calories=health_row.calories)

    clip = _clip_window(audit_by_name, "keylog", window_start, window_end)
    if clip is not None:
        from ..sources.keylog import daily_activity as keylog_daily_activity

        clip_start, clip_end = clip
        for keylog_row in keylog_daily_activity(start=clip_start, end=_inclusive_end(clip_end)):
            add("keylog", keylog_row.date, "keypress_count", float(keylog_row.keypress_count))
            add(
                "keylog",
                keylog_row.date,
                "changed_keypress_count",
                float(keylog_row.changed_keypress_count),
            )
            add("keylog", keylog_row.date, "event_count", float(keylog_row.event_count))
            add("keylog", keylog_row.date, "session_count", float(keylog_row.session_count))

    clip = _clip_window(audit_by_name, "sleep", window_start, window_end)
    if clip is not None:
        from ..sources.sleep import entries_in_range, sleep_architecture

        clip_start, clip_end = clip
        for entry in entries_in_range(start=clip_start, end=_inclusive_end(clip_end)):
            add("sleep", entry.date, "sleep_minutes", float(entry.total_minutes), quality=entry.quality_label)
            if entry.avg_score is not None:
                add("sleep", entry.date, "sleep_score", float(entry.avg_score))
        for arch in sleep_architecture(start=clip_start, end=_inclusive_end(clip_end)):
            add("sleep", arch.date, "sleep_arch_total_minutes", float(arch.total_min), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_awake_minutes", float(arch.awake_min), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_light_minutes", float(arch.light_min), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_deep_minutes", float(arch.deep_min), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_rem_minutes", float(arch.rem_min), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_deep_pct", float(arch.deep_pct), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_rem_pct", float(arch.rem_pct), sleep_id=arch.sleep_id)
            add("sleep", arch.date, "sleep_stage_transitions", float(arch.stage_transitions), sleep_id=arch.sleep_id)
            if arch.first_rem_min is not None:
                add("sleep", arch.date, "sleep_first_rem_minutes", float(arch.first_rem_min), sleep_id=arch.sleep_id)

    clip = _clip_window(audit_by_name, "substance", window_start, window_end)
    if clip is not None:
        from ..sources.substance import daily_summary as substance_daily_summary

        clip_start, clip_end = clip
        for substance_row in substance_daily_summary(start=clip_start, end=_inclusive_end(clip_end)):
            add("substance", substance_row.date, "dose_count", substance_row.dose_count, substances=",".join(substance_row.substances))

    clip = _clip_window(audit_by_name, "spotify", window_start, window_end)
    if clip is not None:
        from ..sources.spotify import daily_listening

        clip_start, clip_end = clip
        for spotify_row in daily_listening(start=clip_start, end=clip_end, ensure=False):
            add("spotify", spotify_row.date, "minutes_played", spotify_row.hours * 60.0, stream_count=spotify_row.stream_count)

    clip = _clip_window(audit_by_name, "reddit", window_start, window_end)
    if clip is not None:
        from ..sources.reddit import daily_activity as reddit_daily_activity

        clip_start, clip_end = clip
        for reddit_row in reddit_daily_activity(start=clip_start, end=clip_end, ensure=False):
            add("reddit", reddit_row.date, "activity_count", reddit_row.comment_count + reddit_row.post_count, top_subreddits=",".join(reddit_row.top_subreddits))

    clip = _clip_window(audit_by_name, "wykop", window_start, window_end)
    if clip is not None:
        from ..sources.wykop import daily_activity as wykop_daily_activity

        clip_start, clip_end = clip
        for wykop_row in wykop_daily_activity(
            start=clip_start,
            end=_inclusive_end(clip_end),
        ):
            day = wykop_row.date
            add("wykop", day, "comment_count", float(wykop_row.comments))
            add("wykop", day, "own_chars", float(wykop_row.own_chars))
            add("wykop", day, "total_chars", float(wykop_row.total_chars))
            add("wykop", day, "upvote_count", float(wykop_row.upvotes))
            add("wykop", day, "downvote_count", float(wykop_row.downvotes))

    clip = _clip_window(audit_by_name, "themotte", window_start, window_end)
    if clip is not None:
        from ..sources.themotte import daily_activity as themotte_daily_activity

        clip_start, clip_end = clip
        for themotte_row in themotte_daily_activity(start=clip_start, end=clip_end):
            day = themotte_row.date
            add("themotte", day, "message_count", float(themotte_row.messages), peers=",".join(themotte_row.peers))
            add("themotte", day, "outbound_message_count", float(themotte_row.outbound_messages))
            add("themotte", day, "notification_count", float(themotte_row.notifications))

    clip = _clip_window(audit_by_name, "facebook_messenger", window_start, window_end)
    if clip is not None:
        from ..sources.exports import daily_messenger_activity

        clip_start, clip_end = clip
        for messenger_row in daily_messenger_activity(start=clip_start, end=clip_end, ensure=False):
            add("facebook_messenger", messenger_row.date, "message_count", messenger_row.message_count, thread_count=messenger_row.thread_count)

    clip = _clip_window(audit_by_name, "raindrop", window_start, window_end)
    if clip is not None:
        from ..sources.exports import daily_raindrop_activity

        clip_start, clip_end = clip
        for raindrop_row in daily_raindrop_activity(start=clip_start, end=clip_end, ensure=False):
            add("raindrop", raindrop_row.date, "bookmarks_added", raindrop_row.bookmarks_added, unique_tags=raindrop_row.unique_tags)

    clip = _clip_window(audit_by_name, "google_takeout", window_start, window_end)
    if clip is not None:
        clip_start, clip_end = clip
        rows.extend(_google_takeout_signal_rows(clip_start, clip_end))
    return rows


def _merge_covered_dates(
    *,
    manifest: Path,
    start: date,
    end: date,
    verified_bounds: tuple[date, date] | None = None,
) -> tuple[date, ...]:
    return merge_manifest_covered_dates(manifest=manifest, start=start, end=end, verified_bounds=verified_bounds)


def _inclusive_end(window_end: date) -> date:
    """Translate an internal half-open end date for legacy inclusive readers."""

    return window_end - timedelta(days=1)


def _google_takeout_signal_rows(window_start: date, window_end: date) -> list[SignalRow]:
    counts: dict[tuple[str, str | None, date], float] = defaultdict(float)
    from ..sources.google_takeout_products import iter_daily_activity

    for row in iter_daily_activity(start=window_start, end=window_end, ensure=False):
        counts[(row.product, row.service, row.date)] += row.event_count
    return [
        (
            "google_takeout",
            day,
            f"{product}_count",
            value,
            {"service": service} if service else {},
        )
        for (product, service, day), value in sorted(counts.items(), key=lambda item: (item[0][0], item[0][1] or "", item[0][2]))
    ]


def _overlaps(audit_by_name: dict[str, Any], source: str, window_start: date, window_end: date) -> bool:
    from ..materialization import materialized_dataset_overlaps

    row = audit_by_name.get(source)
    return bool(row and materialized_dataset_overlaps(row, start=window_start, end=window_end))


def _clip_window(
    audit_by_name: dict[str, Any],
    source: str,
    window_start: date,
    window_end: date,
) -> tuple[date, date] | None:
    """Intersect ``[window_start, window_end)`` with ``source``'s own known
    real coverage bounds (``first_date``..``last_date + 1``). Returns
    ``None`` when there is no overlap.

    Without this, a source with a dense/always-emits per-day reader (e.g.
    ``keylog.daily_activity()``, which builds an explicit zero-filled dict
    for every day in the requested range regardless of whether keylog was
    capturing that day) silently manufactures placeholder rows for dates
    outside its real capture window whenever the *outer* window it's asked
    about is wider than its own coverage -- which happens on every
    full/default-window run of ``materialize_personal_daily_signals``,
    since that window is the union of every ready source's bounds, not any
    single source's own bounds (lynchpin-ee9: 23,016 of 24,136 keylog rows
    in daily_signals.ndjson were zero-value placeholders for 2010-2025,
    before keylog's real 2025-10-06 capture start).
    """
    row = audit_by_name.get(source)
    if row is None or row.first_date is None or row.last_date is None:
        return None
    clip_start = max(window_start, row.first_date)
    clip_end = min(window_end, row.last_date + timedelta(days=1))
    if clip_start >= clip_end:
        return None
    return clip_start, clip_end


def _personal_daily_signal_input_files(
    audit_by_name: dict[str, Any],
    sources: Iterable[str],
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for source in sorted(set(sources)):
        row = audit_by_name.get(source)
        if row is None:
            continue
        candidate_paths = tuple(getattr(row, "materialized_paths", ())) or tuple(getattr(row, "raw_roots", ()))
        for path in candidate_paths:
            if not isinstance(path, Path) or not path.exists():
                continue
            if path.suffix == ".json" and "manifest" in path.name:
                continue
            paths.append(path)
    return tuple(dict.fromkeys(paths))


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _int_dict(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, int)}


def _can_replace_tail(
    output: Path,
    manifest: dict[str, Any],
    *,
    start: date,
    end: date,
) -> bool:
    if not output.exists() or not isinstance(manifest.get("row_offsets"), dict):
        return False
    last = manifest.get("last_date")
    try:
        return isinstance(last, str) and end >= date.fromisoformat(last) + timedelta(days=1)
    except ValueError:
        return False


def _combined_bounds(
    previous_manifest: dict[str, Any],
    signal_dates: list[date],
) -> tuple[date, date] | None:
    bounds: list[date] = list(signal_dates)
    for key in ("first_date", "last_date"):
        raw = previous_manifest.get(key)
        if isinstance(raw, str):
            try:
                bounds.append(date.fromisoformat(raw))
            except ValueError:
                pass
    return (min(bounds), max(bounds)) if bounds else None


def _manifest(
    *,
    dataset: str,
    schema_version: int | None = None,
    output: Path,
    row_count: int,
    first_date: date | None,
    last_date: date | None,
    source_counts: dict[str, int] | None = None,
    input_files: tuple[Path, ...] = (),
    covered_dates: tuple[date, ...] = (),
    window_start: date | None = None,
    window_end: date | None = None,
    row_offsets: dict[str, int] | None = None,
    row_counts: dict[str, int] | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "dataset": dataset,
        "materialized_path": str(output),
        "row_count": row_count,
        "first_date": first_date.isoformat() if first_date else None,
        "last_date": last_date.isoformat() if last_date else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "row_order": "logical_date",
        "refresh_id": refresh_id,
    }
    if window_start is not None and window_end is not None:
        manifest["window_start"] = window_start.isoformat()
        manifest["window_end"] = window_end.isoformat()
        manifest["window_semantics"] = "start inclusive, end exclusive"
    if schema_version is not None:
        manifest["schema_version"] = schema_version
    if source_counts is not None:
        manifest["source_counts"] = source_counts
    if row_offsets is not None:
        manifest["row_offsets"] = row_offsets
    if row_counts is not None:
        manifest["row_counts"] = row_counts
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize derived Lynchpin personal signal products")
    parser.add_argument("product", choices=("personal-daily-signals", "spotify-daily", "all"), nargs="?", default="all")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    reports = []
    if args.product in {"spotify-daily", "all"}:
        reports.append(materialize_spotify_daily(start=args.start, end=args.end))
    if args.product in {"personal-daily-signals", "all"}:
        reports.append(materialize_personal_daily_signals(start=args.start, end=args.end))
    sys.stdout.write(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
