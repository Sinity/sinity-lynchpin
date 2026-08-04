"""Materialize canonical ActivityWatch event products."""

from __future__ import annotations

import argparse
import heapq
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import date_to_dt_range, logical_date
from ..sources.activitywatch_dedup import dedup_and_merge
from ..sources.activitywatch_raw import (
    AWEvent,
    canonical_activitywatch_events_path,
    events_from_activitywatch_dbs,
)
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import atomic_write_ndjson, guard_incremental_shrinkage, write_manifest

BUCKET_PREFIXES = ("aw-watcher-window_", "aw-watcher-afk_", "aw-watcher-web-")
ACTIVITYWATCH_EVENTS_SCHEMA_VERSION = 1


def materialize_activitywatch_events(
    *,
    output: Path | None = None,
    dedupe: bool = True,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Build the canonical AW events NDJSON.

    When ``dedupe`` (default), the raw events are cleaned via
    ``dedup_and_merge`` to repair two upstream defects: window/chrome
    zero-duration heartbeat spam (awatcher poll/pulsetime mismatch) and
    AFK duplicate-starttime cluster bug (PR #555 fix incomplete). See
    ``lynchpin/sources/activitywatch_dedup.py`` for the full rationale.

    Set ``dedupe=False`` to emit raw rows untouched (useful when
    diagnosing upstream bugs).
    """
    output = output or canonical_activitywatch_events_path()
    cfg = get_config()
    input_files = activitywatch_input_files(cfg)
    output.parent.mkdir(parents=True, exist_ok=True)

    window = _exclusive_window(start, end)

    # Request bucket order so deduplication can consume one bucket at a time.
    kwargs = {"order": "bucket", "dedupe": False}
    if window is not None:
        kwargs.update(start=window[0], end=window[1])
    raw = events_from_activitywatch_dbs(BUCKET_PREFIXES, **kwargs)
    cleaned = dedup_and_merge(raw) if dedupe else raw
    existing = (
        _iter_existing_rows(output, start=start, end=end)
        if start is not None and end is not None and output.exists()
        else iter(())
    )
    ordered = _merge_existing_rows(existing, cleaned)
    row_count = 0
    observed_dates: set[date] = set()
    first_start: datetime | None = None
    last_start: datetime | None = None

    def tracked_rows():
        nonlocal row_count, first_start, last_start
        for row in ordered:
            try:
                start_dt = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                continue
            row_count += 1
            first_start = start_dt if first_start is None else min(first_start, start_dt)
            last_start = start_dt if last_start is None else max(last_start, start_dt)
            observed_dates.add(logical_date(start_dt))
            yield row

    temporary_output = output.with_name(f".{output.name}.materialize-tmp")
    try:
        _write_ndjson(temporary_output, tracked_rows())
        if start is not None and end is not None:
            guard_incremental_shrinkage(
                output.with_suffix(".manifest.json"),
                row_count,
                dataset="activitywatch.events",
            )
        temporary_output.replace(output)
    except Exception:
        temporary_output.unlink(missing_ok=True)
        raise
    verified_bounds = (
        (min(observed_dates), max(observed_dates)) if observed_dates else None
    )
    covered_dates = _merge_covered_dates(
        manifest=output.with_suffix(".manifest.json"),
        observed_dates=observed_dates,
        start=start,
        end=end,
        verified_bounds=verified_bounds,
    )
    manifest = {
        "dataset": "activitywatch.events",
        "schema_version": ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "row_count": row_count,
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": first_start.date().isoformat() if first_start else None,
        "last_timestamp_date": last_start.date().isoformat() if last_start else None,
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "date_boundary": "logical_06:00_local",
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive" if start is not None and end is not None else None,
        "bucket_prefixes": list(BUCKET_PREFIXES),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def _event_row(event: AWEvent) -> dict[str, Any]:
    return {
        "bucket": event.bucket,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "data": event.data,
    }


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    data = row.get("data")
    data_json = json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=False, sort_keys=True)
    return (
        str(row.get("bucket") or ""),
        str(row.get("start") or ""),
        str(row.get("end") or ""),
        data_json,
    )


def _iter_existing_rows(path: Path, *, start: date, end: date):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                day = _row_logical_date(payload)
                if day is not None and not (start <= day < end):
                    yield payload


def _merge_existing_rows(existing, cleaned):
    rows = heapq.merge(
        existing,
        (_event_row(event) for event in cleaned),
        key=_row_key,
    )
    previous_key = None
    for row in rows:
        key = _row_key(row)
        if key == previous_key:
            continue
        previous_key = key
        yield row


def _write_ndjson(path: Path, rows) -> None:
    atomic_write_ndjson(path, rows)


def _row_logical_date(row: dict[str, Any]) -> date | None:
    try:
        return logical_date(datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00")))
    except (KeyError, ValueError, TypeError):
        return None


def _exclusive_window(start: date | None, end: date | None) -> tuple[datetime, datetime] | None:
    if start is None or end is None:
        return None
    if end <= start:
        raise MaterializationError("activitywatch_materialize", reason="ActivityWatch event materialization end must be after start")
    return date_to_dt_range(start, end - timedelta(days=1))


def _merge_covered_dates(
    *,
    manifest: Path,
    observed_dates: set[date],
    start: date | None,
    end: date | None,
    verified_bounds: tuple[date, date] | None = None,
) -> tuple[date, ...]:
    if start is None or end is None:
        return tuple(sorted(observed_dates))
    return merge_manifest_covered_dates(
        manifest=manifest,
        observed_dates=observed_dates,
        start=start,
        end=end,
        verified_bounds=verified_bounds,
    )


def activitywatch_input_files(cfg: Any) -> tuple[Path, ...]:
    paths: list[Path] = []
    live = Path(cfg.activitywatch_db)
    if live.exists():
        paths.append(live)
    archive_dir = getattr(cfg, "activitywatch_archive_db_dir", None)
    if isinstance(archive_dir, Path) and archive_dir.exists():
        paths.extend(path for path in archive_dir.glob("*.db") if path.is_file())
        paths.extend(path for path in archive_dir.glob("*.sqlite") if path.is_file())
    return tuple(sorted(dict.fromkeys(paths)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical ActivityWatch events")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = materialize_activitywatch_events(output=args.output)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
