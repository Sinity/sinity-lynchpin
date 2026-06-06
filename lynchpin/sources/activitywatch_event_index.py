"""Logical-day index over canonical ActivityWatch events."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.primitives import logical_date
from .activitywatch_models import AWEvent

ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION = 1

__all__ = [
    "ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION",
    "activitywatch_event_index_dir",
    "activitywatch_event_index_manifest_path",
    "activitywatch_event_index_path",
    "iter_indexed_activitywatch_events",
]


def activitywatch_event_index_dir(root: Path | None = None) -> Path:
    base = root or get_config().captures_root
    return base / "activitywatch/events_by_day"


def activitywatch_event_index_path(day: date, root: Path | None = None) -> Path:
    return activitywatch_event_index_dir(root) / f"{day.isoformat()}.ndjson"


def activitywatch_event_index_manifest_path(root: Path | None = None) -> Path:
    return activitywatch_event_index_dir(root) / "manifest.json"


def iter_indexed_activitywatch_events(
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> Iterator[AWEvent]:
    """Yield indexed canonical events overlapping ``[start, end)``.

    Files are partitioned by ``logical_date(event.start)``. Read one logical day
    before the requested start to preserve long-duration events that overlap the
    left edge of the window.
    """

    first = logical_date(start) - timedelta(days=1)
    last = logical_date(end - timedelta(microseconds=1)) if end > start else logical_date(start)
    rows: list[AWEvent] = []
    cursor = first
    while cursor <= last:
        path = activitywatch_event_index_path(cursor, root)
        if path.exists():
            rows.extend(_read_day(path, bucket_prefix=bucket_prefix, start=start, end=end))
        cursor += timedelta(days=1)
    rows.sort(key=lambda event: event.start)
    yield from rows


def _read_day(
    path: Path,
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
) -> Iterator[AWEvent]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            bucket = str(payload.get("bucket") or "")
            if not bucket.startswith(bucket_prefix):
                continue
            event_start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            event_end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
            zero_in_window = event_end == event_start and start <= event_start < end
            if event_start >= end or (event_end <= start and not zero_in_window):
                continue
            data = payload.get("data")
            yield AWEvent(
                bucket=bucket,
                start=event_start,
                end=event_end,
                data=data if isinstance(data, dict) else {},
            )
