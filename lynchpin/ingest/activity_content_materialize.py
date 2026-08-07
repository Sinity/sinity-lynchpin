"""Materialize ActivityWatch content rollups from canonical title metadata."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from ..core.io import latest_mtime_iso
from ..core.parse import local_tz
from ..core.primitives import duration_s, split_by_day
from ..sources.activity_content import activity_content_daily_path, activity_title_usage_path
from ..sources.activitywatch import focus_spans
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from ..sources.title_metadata import hash_title, load_title_classification_map, normalize_title, title_metadata_path
from ..sources.title_metadata_rules import classify_title_via_rules
from ._manifest import write_manifest


ACTIVITY_CONTENT_SCHEMA_VERSION = 1
DEFAULT_CHUNK_DAYS = 30
RECENT_CHUNK_DAYS = 1
RECENT_WINDOW_DAYS = 30


class _TitleUsageStore:
    """Disk-backed accumulator for high-cardinality title usage rows."""

    _columns = (
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

    def __init__(self, path: Path) -> None:
        self.path = path
        path.unlink(missing_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE title_usage (
                title_hash TEXT NOT NULL,
                app TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                example_title TEXT NOT NULL,
                focused_seconds REAL NOT NULL,
                span_count INTEGER NOT NULL,
                first_date TEXT NOT NULL,
                last_date TEXT NOT NULL,
                matched INTEGER NOT NULL,
                classification_source TEXT,
                confidence REAL,
                activity TEXT,
                content_type TEXT,
                attention_level TEXT,
                topic_category TEXT,
                platform TEXT,
                PRIMARY KEY (title_hash, app)
            )
            """
        )

    def ensure_row(
        self,
        *,
        title_hash: str,
        app: str,
        normalized_title: str,
        example_title: str,
        classification: Any,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO title_usage (
                title_hash, app, normalized_title, example_title,
                focused_seconds, span_count, first_date, last_date, matched,
                classification_source, confidence, activity, content_type,
                attention_level, topic_category, platform
            ) VALUES (?, ?, ?, ?, 0.0, 0, '', '', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title_hash,
                app,
                normalized_title,
                example_title,
                int(classification is not None),
                getattr(classification, "classification_source", None),
                getattr(classification, "confidence", None),
                getattr(classification, "activity", None),
                getattr(classification, "content_type", None),
                getattr(classification, "attention_level", None),
                getattr(classification, "topic_category", None),
                getattr(classification, "platform", None),
            ),
        )

    def add(self, *, title_hash: str, app: str, day: date, seconds: float) -> None:
        day_s = day.isoformat()
        self.connection.execute(
            """
            UPDATE title_usage
            SET focused_seconds = focused_seconds + ?,
                span_count = span_count + 1,
                first_date = CASE WHEN first_date = '' OR first_date > ? THEN ? ELSE first_date END,
                last_date = CASE WHEN last_date = '' OR last_date < ? THEN ? ELSE last_date END
            WHERE title_hash = ? AND app = ?
            """,
            (seconds, day_s, day_s, day_s, day_s, title_hash, app),
        )

    def commit(self) -> None:
        self.connection.commit()

    def iter_rows(self) -> Any:
        query = "SELECT * FROM title_usage ORDER BY focused_seconds DESC, app, normalized_title"
        for values in self.connection.execute(query):
            row = dict(zip(self._columns, values, strict=True))
            row["matched"] = bool(row["matched"])
            yield {key: value for key, value in row.items() if value is not None}

    def count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM title_usage").fetchone()[0])

    def unmatched_count(self) -> int:
        return int(self.connection.execute("SELECT COUNT(*) FROM title_usage WHERE matched = 0").fetchone()[0])

    def top_unmatched(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT app, normalized_title, focused_seconds, span_count
            FROM title_usage
            WHERE matched = 0
            ORDER BY focused_seconds DESC
            LIMIT 20
            """
        )
        return [
            {
                "app": app,
                "normalized_title": normalized_title,
                "focused_seconds": round(float(focused_seconds), 3),
                "span_count": span_count,
            }
            for app, normalized_title, focused_seconds, span_count in rows
        ]

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()
        self.path.unlink(missing_ok=True)


def materialize_activity_content(
    *,
    start: date | None = None,
    end: date | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    start, end = _default_window(start, end)
    default_output = activity_content_daily_path()
    output = output or default_output
    start, end = _expand_existing_window(start, end, output)
    usage_output = activity_title_usage_path() if output == default_output else output.with_name("title_usage.ndjson")
    input_files = activity_content_input_files()
    output.parent.mkdir(parents=True, exist_ok=True)
    usage_output.parent.mkdir(parents=True, exist_ok=True)
    classifications = load_title_classification_map()

    by_day: dict[date, dict[str, Any]] = {}
    title_usage = _TitleUsageStore(usage_output.with_name(f".{usage_output.name}.sqlite.tmp"))
    source_counts: Counter[str] = Counter()
    matched_seconds_total = 0.0
    focused_seconds_total = 0.0
    cursor = start
    processed_days = 0
    recent_start = end - timedelta(days=RECENT_WINDOW_DAYS)
    while cursor < end:
        if cursor < recent_start:
            chunk_end = min(cursor + timedelta(days=DEFAULT_CHUNK_DAYS), recent_start)
        else:
            chunk_end = min(cursor + timedelta(days=RECENT_CHUNK_DAYS), end)
        span_start = datetime.combine(cursor, time.min, tzinfo=local_tz())
        span_end = datetime.combine(chunk_end, time.min, tzinfo=local_tz())
        for span in focus_spans(start=span_start, end=span_end, enrich_polylogue=False):
            if span.kind != "focused" or not span.title or not span.app or span.duration_s <= 0:
                continue
            normalized = normalize_title(span.app, span.title)
            key = hash_title(span.app, normalized)
            classification = classifications.get(key)
            if classification is None:
                # Fallback: in-Lynchpin rules layer. Covers kitty + project-slug,
                # Claude Code session-prefix sentinels, and topic slugs.
                classification = classify_title_via_rules(span.app, span.title, normalized)
            title_usage.ensure_row(
                title_hash=key,
                app=span.app,
                normalized_title=normalized,
                example_title=span.title,
                classification=classification,
            )
            for day, segment in split_by_day(span.start, span.end):
                if day < start or day >= end:
                    continue
                seconds = duration_s(segment)
                if seconds <= 0:
                    continue
                title_usage.add(title_hash=key, app=span.app, day=day, seconds=seconds)
                focused_seconds_total += seconds
                day_row = by_day.setdefault(day, _empty_day(day))
                day_row["focused_seconds"] += seconds
                if classification is None:
                    continue
                day_row["matched_seconds"] += seconds
                matched_seconds_total += seconds
                source = classification.classification_source or "unknown"
                day_row["source_counts"][source] += 1
                source_counts[source] += 1
                if source == "gpt":
                    day_row["gpt_matched_seconds"] += seconds
                _add_bucket(day_row["activity_seconds"], classification.activity, seconds)
                _add_bucket(day_row["content_type_seconds"], classification.content_type, seconds)
                _add_bucket(day_row["attention_seconds"], classification.attention_level, seconds)
                _add_bucket(day_row["topic_seconds"], classification.topic_category, seconds)
                _add_bucket(day_row["platform_seconds"], classification.platform, seconds)
        cursor = chunk_end
        processed_days += (chunk_end - span_start.date()).days
        title_usage.commit()
        _progress(f"processed {processed_days} day(s) through {chunk_end.isoformat()}")

    for day_row in by_day.values():
        _finish_day(day_row)

    with output.open("w", encoding="utf-8") as handle:
        for day in sorted(by_day):
            handle.write(json.dumps(by_day[day], ensure_ascii=False, sort_keys=True) + "\n")
    with usage_output.open("w", encoding="utf-8") as handle:
        for row in title_usage.iter_rows():
            row["focused_seconds"] = round(float(row["focused_seconds"]), 3)
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    title_usage_count = title_usage.count()
    unmatched_title_count = title_usage.unmatched_count()
    top_unmatched_titles = title_usage.top_unmatched()
    title_usage.close()

    manifest = {
        "dataset": "lynchpin.activity_content_daily",
        "schema_version": ACTIVITY_CONTENT_SCHEMA_VERSION,
        "materialized_path": str(output),
        "title_usage_path": str(usage_output),
        "row_count": len(by_day),
        "title_usage_count": title_usage_count,
        "unmatched_title_count": unmatched_title_count,
        "top_unmatched_titles": top_unmatched_titles,
        "first_date": min(by_day).isoformat() if by_day else None,
        "last_date": max(by_day).isoformat() if by_day else None,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "focused_seconds": round(focused_seconds_total, 3),
        "matched_seconds": round(matched_seconds_total, 3),
        "matched_ratio": round(matched_seconds_total / focused_seconds_total, 6) if focused_seconds_total else 0.0,
        "source_counts": dict(sorted(source_counts.items())),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def activity_content_input_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in (canonical_activitywatch_events_path(), title_metadata_path())
        if path.exists()
    )


def _progress(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    sys.stderr.write(f"[{stamp}] activity-content: {message}\n")
    sys.stderr.flush()


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not manifest.exists():
        raise FileNotFoundError(
            "canonical ActivityWatch events manifest is missing; run "
            "python -m lynchpin.ingest.activitywatch_materialize first"
        )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    first = start or date.fromisoformat(str(payload["first_date"]))
    last_inclusive = date.fromisoformat(str(payload["last_date"]))
    return first, end or (last_inclusive + timedelta(days=1))


def _expand_existing_window(
    start: date, end: date, output: Path
) -> tuple[date, date]:
    """Keep global title-usage aggregates complete on windowed requests.

    Title usage has no per-day contribution table, so a partial rebuild cannot
    safely merge with the existing aggregate. Expand a request to the prior
    product bounds and let the canonical input manifest extend the right edge.
    Fresh outputs retain the caller's narrow window for focused tests and
    explicit backfills.
    """
    manifest = output.with_suffix(".manifest.json")
    if not output.exists() or not manifest.exists():
        return start, end
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        first = date.fromisoformat(str(payload["first_date"]))
        last = date.fromisoformat(str(payload["last_date"]))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return start, end
    return min(start, first), max(end, last + timedelta(days=1))


def _empty_day(day: date) -> dict[str, Any]:
    return {
        "date": day.isoformat(),
        "focused_seconds": 0.0,
        "matched_seconds": 0.0,
        "gpt_matched_seconds": 0.0,
        "unmatched_seconds": 0.0,
        "matched_ratio": 0.0,
        "gpt_matched_ratio": 0.0,
        "activity_seconds": defaultdict(float),
        "content_type_seconds": defaultdict(float),
        "attention_seconds": defaultdict(float),
        "topic_seconds": defaultdict(float),
        "platform_seconds": defaultdict(float),
        "source_counts": Counter(),
    }


def _finish_day(row: dict[str, Any]) -> None:
    focused = float(row["focused_seconds"])
    matched = float(row["matched_seconds"])
    gpt_matched = float(row["gpt_matched_seconds"])
    row["unmatched_seconds"] = max(focused - matched, 0.0)
    row["matched_ratio"] = round(matched / focused, 6) if focused else 0.0
    row["gpt_matched_ratio"] = round(gpt_matched / focused, 6) if focused else 0.0
    for key in (
        "focused_seconds",
        "matched_seconds",
        "gpt_matched_seconds",
        "unmatched_seconds",
    ):
        row[key] = round(float(row[key]), 3)
    for key in (
        "activity_seconds",
        "content_type_seconds",
        "attention_seconds",
        "topic_seconds",
        "platform_seconds",
    ):
        row[key] = {bucket: round(value, 3) for bucket, value in sorted(row[key].items()) if value > 0}
    row["source_counts"] = dict(sorted(row["source_counts"].items()))


def _add_bucket(target: dict[str, float], bucket: str | None, seconds: float) -> None:
    if bucket:
        target[bucket] += seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ActivityWatch content rollups")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_activity_content(start=args.start, end=args.end, output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
