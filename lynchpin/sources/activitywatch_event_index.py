"""Logical-day index over canonical ActivityWatch events."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.primitives import logical_date
from .activitywatch_models import AWEvent

ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION = 2

__all__ = [
    "ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION",
    "activitywatch_event_index_dir",
    "activitywatch_event_index_generation_dir",
    "activitywatch_event_index_manifest_path",
    "activitywatch_event_index_path",
    "activitywatch_event_index_product_paths",
    "iter_indexed_activitywatch_events",
]


def activitywatch_event_index_dir(root: Path | None = None) -> Path:
    # An explicit root already names the activitywatch lane's own parent
    # (tests use this to point at a fake tree one level up, same contract
    # as before the recut). Only the default changes: activitywatch moved
    # from captures/activitywatch to the nested activity/activitywatch/
    # activitywatch (merged with the export-wave material already there) in
    # the 2026-08-17 subject recut, so the un-overridden base needs the
    # extra "activitywatch" segment to land in the same place.
    base = root or (get_config().data_root / "activity" / "activitywatch")
    return base / "activitywatch/events_by_day"


def activitywatch_event_index_path(day: date, root: Path | None = None) -> Path:
    """Return the pre-generation static path retained for legacy indexes."""
    return activitywatch_event_index_dir(root) / f"{day.isoformat()}.ndjson"


def activitywatch_event_index_generation_dir(generation: str, root: Path | None = None) -> Path:
    """Return the immutable directory used by one published index generation."""
    return activitywatch_event_index_dir(root) / "generations" / generation


def activitywatch_event_index_manifest_path(root: Path | None = None) -> Path:
    return activitywatch_event_index_dir(root) / "manifest.json"


def _index_manifest(root: Path | None = None) -> dict[str, Any]:
    manifest_path = activitywatch_event_index_manifest_path(root)
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def activitywatch_event_index_product_paths(root: Path | None = None) -> dict[str, Path]:
    """Return paths from the one manifest that defines the serving generation.

    Schema v1 stored stable filenames directly. Schema v2 points at immutable
    generation files, so replacing the manifest is the publication boundary.
    """
    payload = _index_manifest(root)
    raw_paths = payload.get("product_paths")
    if not isinstance(raw_paths, dict):
        return {}
    return {
        str(day): Path(str(path))
        for day, path in raw_paths.items()
        if isinstance(path, str) and Path(path).exists()
    }


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
    manifest = _index_manifest(root)
    product_paths = activitywatch_event_index_product_paths(root)
    generation_manifest = manifest.get("schema_version") == ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION
    rows: list[AWEvent] = []
    cursor = first
    while cursor <= last:
        raw_day = cursor.isoformat()
        path = product_paths.get(raw_day)
        # Legacy schema-v1 indexes had no generation paths in their manifest.
        # Preserve read access to them until an explicit v2 materialization
        # publishes the first immutable generation. A v2 manifest deliberately
        # omits empty days, which must not fall through to stale legacy data.
        if path is None and not generation_manifest:
            path = activitywatch_event_index_path(cursor, root)
        if path is None:
            cursor += timedelta(days=1)
            continue
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
