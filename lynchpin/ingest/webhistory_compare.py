"""Webhistory dataset comparison helpers."""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Tuple

from ..sources.captures.webhistory import WebHistoryVisit, iter_gestalt_events, iter_ndjson_events, normalize_url


def compare_webhistory(canonical: Path, candidate: Path, tolerance: int, sample: int) -> dict[str, object]:
    canonical_events = _load_events(canonical)
    candidate_events = _load_events(candidate)
    canonical_index = _build_index(canonical_events)
    candidate_index = _build_index(candidate_events)

    canonical_missing = 0
    canonical_missing_sample: List[Dict[str, str]] = []
    canonical_matched = 0
    for timestamp, url, source in canonical_events:
        if _has_match(candidate_index, url, timestamp, tolerance):
            canonical_matched += 1
            continue
        canonical_missing += 1
        if len(canonical_missing_sample) < sample:
            canonical_missing_sample.append(
                {
                    "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                    "url": url,
                    "source": source,
                }
            )

    candidate_only = 0
    candidate_only_sample: List[Dict[str, str]] = []
    candidate_matched = 0
    for timestamp, url, source in candidate_events:
        if _has_match(canonical_index, url, timestamp, tolerance):
            candidate_matched += 1
            continue
        candidate_only += 1
        if len(candidate_only_sample) < sample:
            candidate_only_sample.append(
                {
                    "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                    "url": url,
                    "source": source,
                }
            )

    canonical_start, canonical_end = _range(canonical_events)
    candidate_start, candidate_end = _range(candidate_events)

    return {
        "canonical": str(canonical),
        "candidate": str(candidate),
        "tolerance_seconds": tolerance,
        "canonical_count": len(canonical_events),
        "candidate_count": len(candidate_events),
        "canonical_range": {"start": canonical_start, "end": canonical_end},
        "candidate_range": {"start": candidate_start, "end": candidate_end},
        "canonical_matched": canonical_matched,
        "canonical_missing": canonical_missing,
        "canonical_missing_sample": canonical_missing_sample,
        "candidate_matched": candidate_matched,
        "candidate_only": candidate_only,
        "candidate_only_sample": candidate_only_sample,
    }


def iter_events(path: Path) -> Iterator[WebHistoryVisit]:
    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in {".ndjson", ".jsonl"}:
            yield from iter_ndjson_events(path)
            return
        raise ValueError(f"Unsupported file type: {path}")
    yield from iter_gestalt_events(path)


def _to_seconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _load_events(path: Path) -> List[Tuple[int, str, str]]:
    rows: List[Tuple[int, str, str]] = []
    for event in iter_events(path):
        url = normalize_url(event.url or "")
        if not url:
            continue
        rows.append((_to_seconds(event.timestamp), url, event.source))
    return rows


def _build_index(events: Iterable[Tuple[int, str, str]]) -> Dict[str, List[int]]:
    index: Dict[str, List[int]] = {}
    for timestamp, url, _source in events:
        index.setdefault(url, []).append(timestamp)
    for timestamps in index.values():
        timestamps.sort()
    return index


def _has_match(index: Dict[str, List[int]], url: str, timestamp: int, tolerance: int) -> bool:
    candidates = index.get(url)
    if not candidates:
        return False
    lower = timestamp - tolerance
    pos = bisect_left(candidates, lower)
    return bool(pos < len(candidates) and abs(candidates[pos] - timestamp) <= tolerance)


def _range(events: List[Tuple[int, str, str]]) -> Tuple[str | None, str | None]:
    if not events:
        return None, None
    timestamps = [row[0] for row in events]
    return (
        datetime.fromtimestamp(min(timestamps), tz=timezone.utc).isoformat(),
        datetime.fromtimestamp(max(timestamps), tz=timezone.utc).isoformat(),
    )
