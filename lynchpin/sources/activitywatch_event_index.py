"""Logical-day index over canonical ActivityWatch events."""

from __future__ import annotations

import json
import hashlib
from importlib.resources import files
import zoneinfo
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.errors import SourceUnavailableError
from ..core.parse import local_tz
from ..core.primitives import DAY_BOUNDARY_HOUR, logical_date
from .activitywatch_models import AWEvent

ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION = 3

__all__ = [
    "ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION",
    "activitywatch_event_index_recipe",
    "activitywatch_event_index_dir",
    "activitywatch_event_index_generation_dir",
    "activitywatch_event_index_manifest_path",
    "activitywatch_event_index_path",
    "activitywatch_event_index_product_paths",
    "iter_indexed_activitywatch_events",
]


def activitywatch_event_index_recipe() -> dict[str, Any]:
    """Identify the day-key and deduplication rules used by this process."""
    from .activitywatch_dedup import MERGE_PULSETIME_S

    tz = local_tz()
    key = getattr(tz, "key", None)
    timezone_identity: dict[str, Any]
    if isinstance(key, str):
        rules = None
        for directory in zoneinfo.TZPATH:
            path = Path(directory) / key
            if path.is_file():
                rules = path.read_bytes()
                break
        if rules is None:
            try:
                rules = files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()
            except (ModuleNotFoundError, FileNotFoundError) as exc:
                raise SourceUnavailableError("activitywatch_event_index", reason="timezone rules are unavailable") from exc
        timezone_identity = {"key": key, "rules_sha256": hashlib.sha256(rules).hexdigest()}
    else:
        offset = tz.utcoffset(None)
        timezone_identity = {"name": str(tz), "offset_seconds": offset.total_seconds() if offset is not None else None}
    return {"version": 1, "day_boundary_hour": DAY_BOUNDARY_HOUR,
            "timezone": timezone_identity, "deduplication": "bucket-start-end-data-merge-v1",
            "merge_pulsetime_seconds": MERGE_PULSETIME_S}


def activitywatch_event_index_dir(root: Path | None = None) -> Path:
    base = root or (get_config().data_root / "activity")
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
    from ..core.input_snapshot import read_input_path

    manifest_path = read_input_path(activitywatch_event_index_manifest_path(root))
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
    return _product_paths(_index_manifest(root))


def _product_paths(payload: dict[str, Any]) -> dict[str, Path]:
    raw_paths = payload.get("product_paths")
    if not isinstance(raw_paths, dict):
        return {}
    return {
        str(day): Path(str(path))
        for day, path in raw_paths.items()
        if isinstance(path, str)
    }


def iter_indexed_activitywatch_events(
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
    root: Path | None = None,
) -> Iterator[AWEvent]:
    """Yield indexed canonical events overlapping ``[start, end)``.

    Files are partitioned by ``logical_date(event.start)``. Per-partition end
    bounds retain arbitrarily long overlaps without scanning unrelated history.
    Each read resolves one manifest and leaves original event fields intact.
    """

    if end.timestamp() <= start.timestamp():
        return
    last = logical_date(end - timedelta(microseconds=1))
    manifest = _index_manifest(root)
    product_paths = _product_paths(manifest)
    generation_manifest = manifest.get("schema_version") in {2, ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION}
    if generation_manifest and manifest.get("transform_recipe") != activitywatch_event_index_recipe():
        raise SourceUnavailableError("activitywatch_event_index", reason="transformation rules changed or were not recorded; an explicit full rebuild is required")
    if manifest.get("schema_version") == ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION:
        bounds = manifest.get("max_end_ns_by_day")
        if (not isinstance(bounds, dict) or set(bounds) != set(product_paths)
                or any(not isinstance(value, int) or isinstance(value, bool) for value in bounds.values())):
            raise SourceUnavailableError("activitywatch_event_index", reason="partition interval bounds missing or invalid; an explicit full rebuild is required")
        since_ns = int(start.timestamp() * 1_000_000_000)
        selected_days = sorted(day for day, maximum in bounds.items()
                               if date.fromisoformat(day) <= last and maximum >= since_ns)
    elif product_paths:
        # Retained older packages have no interval bounds. Read all preceding
        # partitions conservatively rather than silently losing long overlaps.
        selected_days = sorted(day for day in product_paths if date.fromisoformat(day) <= last)
    elif generation_manifest:
        selected_days = []
    else:
        selected_days = sorted(path.stem for path in activitywatch_event_index_dir(root).glob("*.ndjson")
                               if date.fromisoformat(path.stem) <= last)
    rows: list[AWEvent] = []
    for raw_day in selected_days:
        path = product_paths.get(raw_day)
        if path is None and not generation_manifest:
            path = activitywatch_event_index_path(date.fromisoformat(raw_day), root)
        if path is None:
            continue
        from ..core.input_snapshot import read_input_path

        read_path = read_input_path(path)
        if read_path.is_file():
            rows.extend(_read_day(read_path, bucket_prefix=bucket_prefix, start=start, end=end))
        else:
            raise SourceUnavailableError("activitywatch_event_index", path=str(path),
                                         reason=f"manifest names missing partition for logical day {raw_day}")
    rows.sort(key=lambda event: event.start.timestamp())
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
