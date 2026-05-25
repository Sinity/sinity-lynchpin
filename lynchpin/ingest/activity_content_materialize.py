"""Materialize ActivityWatch content rollups from canonical title metadata."""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from ..core.parse import local_tz
from ..core.primitives import duration_s, split_by_day
from ..sources.activity_content import activity_content_daily_path, activity_title_usage_path
from ..sources.activitywatch import focus_spans
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from ..sources.title_metadata import hash_title, load_title_classification_map, normalize_title
from ..sources.title_metadata_rules import classify_title_via_rules

def materialize_activity_content(*, start: date | None=None, end: date | None=None, output: Path | None=None) -> dict[str, Any]:
    start, end = _default_window(start, end)
    default_output = activity_content_daily_path()
    output = output or default_output
    usage_output = activity_title_usage_path() if output == default_output else output.with_name('title_usage.ndjson')
    output.parent.mkdir(parents=True, exist_ok=True)
    usage_output.parent.mkdir(parents=True, exist_ok=True)
    classifications = load_title_classification_map()
    by_day: dict[date, dict[str, Any]] = {}
    title_usage: dict[tuple[str, str], dict[str, Any]] = {}
    source_counts: Counter[str] = Counter()
    matched_seconds_total = 0.0
    focused_seconds_total = 0.0
    cursor = start
    processed_days = 0
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=30), end)
        span_start = datetime.combine(cursor, time.min, tzinfo=local_tz())
        span_end = datetime.combine(chunk_end, time.min, tzinfo=local_tz())
        for span in focus_spans(start=span_start, end=span_end):
            if span.kind != 'focused' or not span.title or (not span.app) or (span.duration_s <= 0):
                continue
            normalized = normalize_title(span.app, span.title)
            key = hash_title(span.app, normalized)
            classification = classifications.get(key)
            if classification is None:
                classification = classify_title_via_rules(span.app, span.title, normalized)
            usage_key = (span.app, key)
            usage_row = title_usage.setdefault(usage_key, _empty_title_usage(title_hash=key, app=span.app, normalized_title=normalize_title(span.app, span.title), example_title=span.title, classification=classification))
            for day, segment in split_by_day(span.start, span.end):
                if day < start or day >= end:
                    continue
                seconds = duration_s(segment)
                if seconds <= 0:
                    continue
                _update_title_usage(usage_row, day=day, seconds=seconds)
                focused_seconds_total += seconds
                day_row = by_day.setdefault(day, _empty_day(day))
                day_row['focused_seconds'] += seconds
                if classification is None:
                    continue
                day_row['matched_seconds'] += seconds
                matched_seconds_total += seconds
                source = classification.classification_source or 'unknown'
                day_row['source_counts'][source] += 1
                source_counts[source] += 1
                if source == 'gpt':
                    day_row['gpt_matched_seconds'] += seconds
                _add_bucket(day_row['activity_seconds'], classification.activity, seconds)
                _add_bucket(day_row['content_type_seconds'], classification.content_type, seconds)
                _add_bucket(day_row['attention_seconds'], classification.attention_level, seconds)
                _add_bucket(day_row['topic_seconds'], classification.topic_category, seconds)
                _add_bucket(day_row['platform_seconds'], classification.platform, seconds)
        cursor = chunk_end
        processed_days += (chunk_end - span_start.date()).days
        _progress(f'processed {processed_days} day(s) through {chunk_end.isoformat()}')
    for day_row in by_day.values():
        _finish_day(day_row)
    with output.open('w', encoding='utf-8') as handle:
        for day in sorted(by_day):
            handle.write(json.dumps(by_day[day], ensure_ascii=False, sort_keys=True) + '\n')
    with usage_output.open('w', encoding='utf-8') as handle:
        for row in sorted(title_usage.values(), key=lambda item: (-float(item['focused_seconds']), item['app'], item['normalized_title'])):
            row['focused_seconds'] = round(float(row['focused_seconds']), 3)
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')
    manifest = {'dataset': 'lynchpin.activity_content_daily', 'materialized_at': datetime.now(timezone.utc).astimezone().isoformat(), 'materialized_path': str(output), 'title_usage_path': str(usage_output), 'row_count': len(by_day), 'title_usage_count': len(title_usage), 'unmatched_title_count': sum((1 for row in title_usage.values() if not row['matched'])), 'top_unmatched_titles': [{'app': row['app'], 'normalized_title': row['normalized_title'], 'focused_seconds': round(float(row['focused_seconds']), 3), 'span_count': row['span_count']} for row in sorted((row for row in title_usage.values() if not row['matched']), key=lambda item: -float(item['focused_seconds']))[:20]], 'first_date': min(by_day).isoformat() if by_day else None, 'last_date': max(by_day).isoformat() if by_day else None, 'window_start': start.isoformat(), 'window_end': end.isoformat(), 'focused_seconds': round(focused_seconds_total, 3), 'matched_seconds': round(matched_seconds_total, 3), 'matched_ratio': round(matched_seconds_total / focused_seconds_total, 6) if focused_seconds_total else 0.0, 'source_counts': dict(sorted(source_counts.items()))}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest

def _progress(message: str) -> None:
    stamp = datetime.now().astimezone().strftime('%H:%M:%S')
    sys.stderr.write(f'[{stamp}] activity-content: {message}\n')
    sys.stderr.flush()

def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if start is not None and end is not None:
        return (start, end)
    manifest = canonical_activitywatch_events_path().with_suffix('.manifest.json')
    if not manifest.exists():
        raise FileNotFoundError('canonical ActivityWatch events manifest is missing; run python -m lynchpin.ingest.activitywatch_materialize first')
    payload = json.loads(manifest.read_text(encoding='utf-8'))
    first = start or date.fromisoformat(str(payload['first_date']))
    last_inclusive = date.fromisoformat(str(payload['last_date']))
    return (first, end or last_inclusive + timedelta(days=1))

def _empty_day(day: date) -> dict[str, Any]:
    return {'date': day.isoformat(), 'focused_seconds': 0.0, 'matched_seconds': 0.0, 'gpt_matched_seconds': 0.0, 'unmatched_seconds': 0.0, 'matched_ratio': 0.0, 'gpt_matched_ratio': 0.0, 'activity_seconds': defaultdict(float), 'content_type_seconds': defaultdict(float), 'attention_seconds': defaultdict(float), 'topic_seconds': defaultdict(float), 'platform_seconds': defaultdict(float), 'source_counts': Counter()}

def _empty_title_usage(*, title_hash: str, app: str, normalized_title: str, example_title: str, classification: Any) -> dict[str, Any]:
    row = {'title_hash': title_hash, 'app': app, 'normalized_title': normalized_title, 'example_title': example_title, 'focused_seconds': 0.0, 'span_count': 0, 'first_date': None, 'last_date': None, 'matched': classification is not None}
    if classification is not None:
        row.update({'classification_source': classification.classification_source, 'confidence': classification.confidence, 'activity': classification.activity, 'content_type': classification.content_type, 'attention_level': classification.attention_level, 'topic_category': classification.topic_category, 'platform': classification.platform})
    return {key: value for key, value in row.items() if value is not None}

def _update_title_usage(row: dict[str, Any], *, day: date, seconds: float) -> None:
    row['focused_seconds'] = float(row.get('focused_seconds') or 0.0) + seconds
    row['span_count'] = int(row.get('span_count') or 0) + 1
    day_s = day.isoformat()
    first = row.get('first_date')
    last = row.get('last_date')
    row['first_date'] = day_s if first is None or day_s < str(first) else first
    row['last_date'] = day_s if last is None or day_s > str(last) else last

def _finish_day(row: dict[str, Any]) -> None:
    focused = float(row['focused_seconds'])
    matched = float(row['matched_seconds'])
    gpt_matched = float(row['gpt_matched_seconds'])
    row['unmatched_seconds'] = max(focused - matched, 0.0)
    row['matched_ratio'] = round(matched / focused, 6) if focused else 0.0
    row['gpt_matched_ratio'] = round(gpt_matched / focused, 6) if focused else 0.0
    for key in ('focused_seconds', 'matched_seconds', 'gpt_matched_seconds', 'unmatched_seconds'):
        row[key] = round(float(row[key]), 3)
    for key in ('activity_seconds', 'content_type_seconds', 'attention_seconds', 'topic_seconds', 'platform_seconds'):
        row[key] = {bucket: round(value, 3) for bucket, value in sorted(row[key].items()) if value > 0}
    row['source_counts'] = dict(sorted(row['source_counts'].items()))

def _add_bucket(target: dict[str, float], bucket: str | None, seconds: float) -> None:
    if bucket:
        target[bucket] += seconds

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Materialize ActivityWatch content rollups')
    parser.add_argument('--start', type=date.fromisoformat, default=None)
    parser.add_argument('--end', type=date.fromisoformat, default=None)
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_activity_content(start=args.start, end=args.end, output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
