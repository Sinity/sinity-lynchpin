from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config


@dataclass
class SleepSegment:
    start: Optional[str]
    end: Optional[str]
    duration_minutes: float
    score: Optional[float]
    device: Optional[str]
    comment: str


@dataclass
class SleepEntry:
    date: str
    total_minutes: float
    segments: List[SleepSegment]
    avg_score: Optional[float]


def _sleep_path(path: Optional[Path]) -> Path:
    return path or get_config().sleep_jsonl


def _sleep_depends_on(*args: object, **kwargs: object) -> Tuple[str, str]:
    path = kwargs.get("path")
    if path is None and args:
        path = args[0]
    if path is not None and not isinstance(path, Path):
        path = Path(path)
    sleep_path = _sleep_path(path)
    return (str(sleep_path), file_signature(sleep_path))


@persistent_cache(
    "sleep_entries",
    depends_on=_sleep_depends_on,
)
def iter_sleep(path: Optional[Path] = None) -> Iterator[SleepEntry]:
    sleep_path = _sleep_path(path)
    if not sleep_path.exists():
        return iter(())

    def generator() -> Iterator[SleepEntry]:
        bucket: Dict[str, List[SleepSegment]] = {}
        totals: Dict[str, float] = {}
        scores: Dict[str, List[float]] = {}
        with sleep_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                end_local = record.get("end_local") or record.get("end_utc")
                if not end_local:
                    continue
                try:
                    end_date = datetime.fromisoformat(end_local.replace("Z", "+00:00")).date().isoformat()
                except ValueError:
                    continue
                metrics = record.get("metrics") or {}
                duration = float(metrics.get("sleep_duration") or 0.0)
                segment = SleepSegment(
                    start=record.get("start_local") or record.get("start_utc"),
                    end=end_local,
                    duration_minutes=duration,
                    score=metrics.get("sleep_score"),
                    device=record.get("device_name") or record.get("device_uuid"),
                    comment=(record.get("sleep_as_android") or {}).get("comment") or "",
                )
                bucket.setdefault(end_date, []).append(segment)
                totals[end_date] = totals.get(end_date, 0.0) + duration
                if isinstance(segment.score, (int, float)):
                    scores.setdefault(end_date, []).append(float(segment.score))
        for date_key, segments in bucket.items():
            avg_score = None
            if scores.get(date_key):
                avg_score = sum(scores[date_key]) / len(scores[date_key])
            yield SleepEntry(
                date=date_key,
                total_minutes=totals.get(date_key, 0.0),
                segments=segments,
                avg_score=avg_score,
            )

    return generator()


def iter_segments(path: Optional[Path] = None) -> Iterator[SleepSegment]:
    for entry in iter_sleep(path):
        yield from entry.segments


def sleep_by_date(target_iso: str, path: Optional[Path] = None) -> Optional[SleepEntry]:
    for entry in iter_sleep(path):
        if entry.date == target_iso:
            return entry
    return None
