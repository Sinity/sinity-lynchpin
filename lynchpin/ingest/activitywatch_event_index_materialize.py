"""Materialize a logical-day index over canonical ActivityWatch events."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import logical_date
from ..sources.activitywatch_event_index import (
    ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
    activitywatch_event_index_dir,
    activitywatch_event_index_manifest_path,
    activitywatch_event_index_path,
)
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from ._manifest import write_manifest


def activitywatch_event_index_input_files() -> tuple[Path, ...]:
    canonical = canonical_activitywatch_events_path()
    manifest = canonical.with_suffix(".manifest.json")
    return tuple(path for path in (canonical, manifest) if path.exists())


def materialize_activitywatch_event_index(
    *,
    root: Path | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    canonical = canonical_activitywatch_events_path()
    if not canonical.exists():
        raise FileNotFoundError(
            "canonical ActivityWatch events are missing; run "
            "python -m lynchpin.ingest.activitywatch_materialize first"
        )

    output_dir = activitywatch_event_index_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    window_dates = _exclusive_window_dates(start, end)

    previous: dict[str, Any] = {}
    if window_dates is None:
        paths: dict[str, str] = {}
        row_counts: dict[str, int] = {}
    else:
        previous = _read_existing_manifest(activitywatch_event_index_manifest_path(root))
        paths = _string_dict(previous.get("product_paths"))
        row_counts = _int_dict(previous.get("row_counts"))
        for day in window_dates:
            raw_day = day.isoformat()
            paths.pop(raw_day, None)
            row_counts.pop(raw_day, None)

    temporary_dir = output_dir / ".materialize-tmp"
    shutil.rmtree(temporary_dir, ignore_errors=True)
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary_paths: dict[str, Path] = {}
    temporary_handles: dict[str, Any] = {}
    observed_counts: dict[str, int] = {}
    try:
        with canonical.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
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
                temporary_path = temporary_dir / f"{raw_day}.ndjson"
                output = temporary_handles.get(raw_day)
                if output is None:
                    temporary_paths[raw_day] = temporary_path
                    output = temporary_path.open("w", encoding="utf-8")
                    temporary_handles[raw_day] = output
                output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                observed_counts[raw_day] = observed_counts.get(raw_day, 0) + 1
    finally:
        for output in temporary_handles.values():
            output.close()

    for raw_day, temporary_path in temporary_paths.items():
        day = datetime.strptime(raw_day, "%Y-%m-%d").date()
        path = activitywatch_event_index_path(day, root)
        temporary_path.replace(path)
        paths[raw_day] = str(path)
        row_counts[raw_day] = observed_counts[raw_day]

    if window_dates is not None:
        for day in window_dates:
            raw_day = day.isoformat()
            if raw_day in temporary_paths:
                continue
            path = activitywatch_event_index_path(day, root)
            path.unlink(missing_ok=True)
            paths.pop(raw_day, None)
            row_counts.pop(raw_day, None)
    else:
        for stale in output_dir.glob("*.ndjson"):
            if stale.name not in {path.name for path in temporary_paths.values()}:
                stale.unlink()
    shutil.rmtree(temporary_dir, ignore_errors=True)

    covered_dates = tuple(sorted(row_counts))
    input_files = activitywatch_event_index_input_files()
    manifest = {
        "dataset": "lynchpin.activitywatch_event_index",
        "schema_version": ACTIVITYWATCH_EVENT_INDEX_SCHEMA_VERSION,
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


def _write_ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("bucket") or ""),
        str(row.get("start") or ""),
        str(row.get("end") or ""),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize ActivityWatch logical-day event index")
    parser.parse_args(argv)
    report = materialize_activitywatch_event_index()
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
