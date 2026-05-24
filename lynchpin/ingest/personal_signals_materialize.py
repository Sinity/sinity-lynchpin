"""Materialize derived daily signal products from canonical source products."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..sources.personal_signals import (
    personal_daily_signals_path,
    spotify_daily_path,
)


SignalRow = tuple[str, date, str, float, dict[str, Any]]


def materialize_personal_daily_signals(*, output: Path | None = None) -> dict[str, Any]:
    output = output or personal_daily_signals_path()
    rows = list(_personal_daily_signal_rows())
    rows.sort(key=lambda row: (row[1], row[0], row[2], json.dumps(row[4], sort_keys=True)))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for source, day, metric, value, dimensions in rows:
            handle.write(
                json.dumps(
                    {
                        "source": source,
                        "date": day.isoformat(),
                        "metric": metric,
                        "value": value,
                        "dimensions": dimensions,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    counts = Counter(source for source, *_ in rows)
    manifest = _manifest(
        dataset="lynchpin.personal_daily_signals",
        output=output,
        row_count=len(rows),
        first_date=min((row[1] for row in rows), default=None),
        last_date=max((row[1] for row in rows), default=None),
        source_counts=dict(sorted(counts.items())),
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def materialize_spotify_daily(*, output: Path | None = None) -> dict[str, Any]:
    from collections import Counter as _Counter

    from ..sources.spotify import iter_streams

    output = output or spotify_daily_path()
    by_day: dict[date, list[Any]] = defaultdict(list)
    for stream in iter_streams():
        end_time = getattr(stream, "end_time", None)
        if end_time is None:
            continue
        by_day[end_time.date()].append(stream)
    output.parent.mkdir(parents=True, exist_ok=True)
    row_count = 0
    with output.open("w", encoding="utf-8") as handle:
        for day in sorted(by_day):
            streams = by_day[day]
            artists = _Counter(str(getattr(row, "artist", "")) for row in streams if getattr(row, "artist", ""))
            tracks = _Counter(str(getattr(row, "track", "")) for row in streams if getattr(row, "track", ""))
            payload = {
                "date": day.isoformat(),
                "track_count": len(streams),
                "minutes_played": round(sum((getattr(row, "ms_played", 0) or 0) / 60_000 for row in streams), 1),
                "unique_artists": len(artists),
                "unique_tracks": len(tracks),
                "top_artists": [name for name, _ in artists.most_common(5)],
                "top_tracks": [name for name, _ in tracks.most_common(5)],
            }
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            row_count += 1
    manifest = _manifest(
        dataset="lynchpin.spotify_daily",
        output=output,
        row_count=row_count,
        first_date=min(by_day, default=None),
        last_date=max(by_day, default=None),
    )
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _personal_daily_signal_rows() -> Iterable[SignalRow]:
    from ..materialization import audit_materialization

    audit_by_name = {row.name: row for row in audit_materialization()}
    bounds = [
        (row.first_date, row.last_date)
        for row in audit_by_name.values()
        if row.status == "ready" and row.first_date is not None and row.last_date is not None
    ]
    if not bounds:
        return []
    start = min(first for first, _last in bounds if first is not None)
    end = max(last for _first, last in bounds if last is not None) + timedelta(days=1)
    return _window_personal_daily_signal_rows(start, end, audit_by_name)


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

    if _overlaps(audit_by_name, "webhistory", window_start, window_end):
        for web_row in daily_browsing(start=window_start, end=window_end):
            top_domain = web_row.top_domains[0][0] if web_row.top_domains else ""
            add("webhistory", web_row.date, "visit_count", web_row.visit_count, domain_count=web_row.unique_domains, top_domain=top_domain)

    if _overlaps(audit_by_name, "browser_bookmarks", window_start, window_end):
        from ..sources.bookmarks import daily_bookmark_activity

        for bookmark_row in daily_bookmark_activity(start=window_start, end=window_end):
            add("browser_bookmarks", bookmark_row.date, "bookmark_count", bookmark_row.bookmark_count, domain_count=bookmark_row.domain_count, top_domain=bookmark_row.top_domain)

    if _overlaps(audit_by_name, "communications", window_start, window_end):
        from ..sources.communications import daily_communication_activity

        for comm_row in daily_communication_activity(start=window_start, end=window_end):
            add("communications", comm_row.date, "event_count", comm_row.event_count, outbound_count=comm_row.outbound_count, source_count=comm_row.source_count)

    if _overlaps(audit_by_name, "arbtt", window_start, window_end):
        from ..sources.arbtt import daily_arbtt_activity

        for arbtt_row in daily_arbtt_activity(start=window_start, end=window_end):
            add("arbtt", arbtt_row.date, "active_minutes", arbtt_row.active_minutes, event_count=arbtt_row.event_count, program_count=arbtt_row.program_count)

    if _overlaps(audit_by_name, "activity_content", window_start, window_end):
        from ..sources.activity_content import iter_activity_content_days

        for content_row in iter_activity_content_days():
            if not (window_start <= content_row.date < window_end):
                continue
            add("activity_content", content_row.date, "focused_minutes", content_row.focused_seconds / 60.0)
            add("activity_content", content_row.date, "title_metadata_matched_ratio", content_row.matched_ratio)
            add("activity_content", content_row.date, "gpt_title_matched_ratio", content_row.gpt_matched_ratio)
            for activity, seconds in content_row.activity_seconds.items():
                add("activity_content", content_row.date, "activity_minutes", seconds / 60.0, activity=activity)
            for topic, seconds in content_row.topic_seconds.items():
                add("activity_content", content_row.date, "topic_minutes", seconds / 60.0, topic=topic)

    if _overlaps(audit_by_name, "health", window_start, window_end):
        from ..sources.health import daily_health_summary

        for health_row in daily_health_summary(start=window_start, end=window_end):
            if health_row.steps is not None:
                add("health", health_row.date, "steps", float(health_row.steps))
            if health_row.heart_rate_avg is not None:
                add("health", health_row.date, "avg_heart_rate", float(health_row.heart_rate_avg))
            if health_row.hrv_rmssd_avg is not None:
                add("health", health_row.date, "hrv_rmssd", float(health_row.hrv_rmssd_avg))

    if _overlaps(audit_by_name, "sleep", window_start, window_end):
        from ..sources.sleep import entries_in_range

        for entry in entries_in_range(window_start, window_end):
            add("sleep", entry.date, "sleep_minutes", float(entry.total_minutes), quality=entry.quality_label)
            if entry.avg_score is not None:
                add("sleep", entry.date, "sleep_score", float(entry.avg_score))

    if _overlaps(audit_by_name, "substance", window_start, window_end):
        from ..sources.substance import daily_summary as substance_daily_summary

        for substance_row in substance_daily_summary(start=window_start, end=window_end):
            add("substance", substance_row.date, "dose_count", substance_row.dose_count, substances=",".join(substance_row.substances))

    if _overlaps(audit_by_name, "spotify", window_start, window_end):
        from ..sources.spotify import daily_listening

        for spotify_row in daily_listening(start=window_start, end=window_end):
            add("spotify", spotify_row.date, "minutes_played", spotify_row.hours * 60.0, stream_count=spotify_row.stream_count)

    if _overlaps(audit_by_name, "reddit", window_start, window_end):
        from ..sources.reddit import daily_activity as reddit_daily_activity

        for reddit_row in reddit_daily_activity(start=window_start, end=window_end):
            add("reddit", reddit_row.date, "activity_count", reddit_row.comment_count + reddit_row.post_count, top_subreddits=",".join(reddit_row.top_subreddits))

    if _overlaps(audit_by_name, "facebook_messenger", window_start, window_end):
        from ..sources.exports import daily_messenger_activity

        for messenger_row in daily_messenger_activity(start=window_start, end=window_end):
            add("facebook_messenger", messenger_row.date, "message_count", messenger_row.message_count, thread_count=messenger_row.thread_count)

    if _overlaps(audit_by_name, "raindrop", window_start, window_end):
        from ..sources.exports import daily_raindrop_activity

        for raindrop_row in daily_raindrop_activity(start=window_start, end=window_end):
            add("raindrop", raindrop_row.date, "bookmarks_added", raindrop_row.bookmarks_added, unique_tags=raindrop_row.unique_tags)

    if _overlaps(audit_by_name, "google_takeout", window_start, window_end):
        rows.extend(_google_takeout_signal_rows(window_start, window_end))
    return rows


def _google_takeout_signal_rows(window_start: date, window_end: date) -> list[SignalRow]:
    counts: dict[tuple[str, str | None, date], float] = defaultdict(float)
    from ..sources.google_takeout_products import iter_daily_activity

    for row in iter_daily_activity(start=window_start, end=window_end):
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


def _manifest(
    *,
    dataset: str,
    output: Path,
    row_count: int,
    first_date: date | None,
    last_date: date | None,
    source_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "dataset": dataset,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "row_count": row_count,
        "first_date": first_date.isoformat() if first_date else None,
        "last_date": last_date.isoformat() if last_date else None,
    }
    if source_counts is not None:
        manifest["source_counts"] = source_counts
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize derived Lynchpin personal signal products")
    parser.add_argument("product", choices=("personal-daily-signals", "spotify-daily", "all"), nargs="?", default="all")
    args = parser.parse_args(argv)
    reports = []
    if args.product in {"spotify-daily", "all"}:
        reports.append(materialize_spotify_daily())
    if args.product in {"personal-daily-signals", "all"}:
        reports.append(materialize_personal_daily_signals())
    sys.stdout.write(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
