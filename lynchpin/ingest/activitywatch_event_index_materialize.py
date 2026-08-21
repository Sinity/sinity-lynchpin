"""Materialize a logical-day index over canonical ActivityWatch events."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from uuid import uuid4
from pathlib import Path
from typing import Any, Iterable

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import date_to_dt_range, logical_date
from ..sources.activitywatch_dedup import dedup_and_merge
from ..sources.activitywatch_event_index import (
    ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
    activitywatch_event_index_dir,
    activitywatch_event_index_generation_dir,
    activitywatch_event_index_manifest_path,
)
from ..sources.activitywatch_raw import (
    canonical_activitywatch_events_path,
    events_from_activitywatch_dbs,
)
from .activitywatch_materialize import BUCKET_PREFIXES, activitywatch_input_files
from ._manifest import write_manifest


def activitywatch_event_index_input_files() -> tuple[Path, ...]:
    """Return the bounded raw inputs used to build logical-day partitions."""
    return activitywatch_input_files(get_config())


def materialize_activitywatch_event_index(
    *,
    root: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    canonical = canonical_activitywatch_events_path()
    output_dir = activitywatch_event_index_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    window_dates = _exclusive_window_dates(start, end)

    previous: dict[str, Any] = {}
    if window_dates is None:
        paths: dict[str, str] = {}
        row_counts: dict[str, int] = {}
        if not canonical.exists():
            raise FileNotFoundError(
                "canonical ActivityWatch events are missing; run "
                "python -m lynchpin.ingest.activitywatch_materialize first"
            )
        rows = _iter_canonical_rows(canonical)
    else:
        previous = _read_existing_manifest(activitywatch_event_index_manifest_path(root))
        paths = _string_dict(previous.get("product_paths"))
        row_counts = _int_dict(previous.get("row_counts"))
        for day in window_dates:
            raw_day = day.isoformat()
            paths.pop(raw_day, None)
            row_counts.pop(raw_day, None)
        rows = _iter_tail_rows(start=start, end=end)

    # A refresh writes only its affected day files into a new immutable
    # generation. The single manifest move below is the visibility boundary:
    # readers retain the predecessor's complete path set until this candidate
    # is fully written, then see either all old paths or all new paths.
    generation = f"generation-{uuid4().hex}"
    generation_dir = activitywatch_event_index_generation_dir(generation, root)
    staging_dir = generation_dir.with_name(f".{generation}.staging")
    staging_dir.mkdir(parents=True)
    generation_paths: dict[str, Path] = {}
    temporary_handles: dict[str, Any] = {}
    observed_counts: dict[str, int] = {}
    try:
        for payload in rows:
            if not isinstance(payload, dict) or not payload.get("bucket"):
                continue
            try:
                event_start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            day = logical_date(event_start)
            raw_day = day.isoformat()
            if window_dates is not None and day not in window_dates:
                continue
            output_path = staging_dir / f"{raw_day}.ndjson"
            output = temporary_handles.get(raw_day)
            if output is None:
                generation_paths[raw_day] = output_path
                output = output_path.open("w", encoding="utf-8")
                temporary_handles[raw_day] = output
            output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            observed_counts[raw_day] = observed_counts.get(raw_day, 0) + 1
    finally:
        for output in temporary_handles.values():
            output.close()

    generation_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.replace(generation_dir)
    for raw_day, staging_path in generation_paths.items():
        path = generation_dir / staging_path.name
        paths[raw_day] = str(path)
        row_counts[raw_day] = observed_counts[raw_day]

    if window_dates is not None:
        for day in window_dates:
            raw_day = day.isoformat()
            if raw_day not in generation_paths:
                paths.pop(raw_day, None)
                row_counts.pop(raw_day, None)

    covered_dates = tuple(sorted(row_counts))
    canonical_row_count_verified = bool(previous.get("canonical_row_count_verified"))
    if window_dates is None:
        canonical_meta = _read_existing_manifest(canonical.with_suffix(".manifest.json"))
        expected_rows = canonical_meta.get("row_count")
        actual_rows = sum(row_counts.values())
        if isinstance(expected_rows, int) and actual_rows != expected_rows:
            raise MaterializationError(
                "activitywatch_event_index_materialize",
                reason=(
                    "full ActivityWatch index row count does not match the "
                    f"canonical recovery carrier ({actual_rows} != {expected_rows})"
                ),
            )
        canonical_row_count_verified = isinstance(expected_rows, int)
    input_files = activitywatch_event_index_input_files()
    manifest = {
        "dataset": "lynchpin.activitywatch_event_index",
        "schema_version": ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
        "generation": generation,
        "canonical_row_count_verified": canonical_row_count_verified,
        "date_boundary": "logical_06:00_local",
        "partition": "logical_date(event.start)",
        "product_paths": paths,
        "row_counts": row_counts,
        "row_count": sum(row_counts.values()),
        "covered_dates": list(covered_dates),
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0] if covered_dates else None,
        "last_date": covered_dates[-1] if covered_dates else None,
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive"
        if start is not None and end is not None
        else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(activitywatch_event_index_manifest_path(root), manifest)
    return manifest


def _iter_canonical_rows(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def _iter_tail_rows(*, start: date | None, end: date | None) -> Iterable[dict[str, Any]]:
    assert start is not None and end is not None
    source_start, source_end = date_to_dt_range(start, end - date.resolution)
    raw = events_from_activitywatch_dbs(
        BUCKET_PREFIXES,
        start=source_start,
        end=source_end,
        order="bucket",
        dedupe=False,
    )
    for event in dedup_and_merge(raw):
        yield {
            "bucket": event.bucket,
            "start": event.start.isoformat(),
            "end": event.end.isoformat(),
            "data": event.data,
        }


def _exclusive_window_dates(start: date | None, end: date | None) -> frozenset[date] | None:
    if start is None or end is None:
        return None
    if end <= start:
        raise MaterializationError("activitywatch_event_index_materialize", reason="ActivityWatch event index materialization end must be after start")
    days: set[date] = set()
    cursor = start
    while cursor < end:
        days.add(cursor)
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return frozenset(days)


def _read_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items()}


def _int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    rows: dict[str, int] = {}
    for key, val in value.items():
        if isinstance(val, int):
            rows[str(key)] = val
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ActivityWatch logical-day event index")
    parser.parse_args(argv)
    report = materialize_activitywatch_event_index()
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
