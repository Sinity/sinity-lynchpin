"""Materialize canonical ActivityWatch event products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
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
from ._manifest import write_manifest

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

    # Collect raw events first, then apply dedup per bucket. The dedup
    # function expects events to be grouped by bucket; sorting by
    # (bucket, start) achieves that.
    kwargs = {"start": window[0], "end": window[1]} if window is not None else {}
    raw = list(events_from_activitywatch_dbs(BUCKET_PREFIXES, **kwargs))
    raw.sort(key=lambda e: (e.bucket, e.start, e.end))

    if dedupe:
        cleaned = list(dedup_and_merge(raw))
    else:
        cleaned = raw

    # Sort the cleaned events by (bucket, start) for stable output, then
    # dedupe via dict-by-key in case dedup_and_merge left any logical
    # duplicates (it shouldn't, but keep the defensive layer).
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    if start is not None and end is not None and output.exists():
        for row in _read_existing_rows(output):
            day = _row_logical_date(row)
            if day is not None and not (start <= day < end):
                _add_row(rows, row)
    for event in cleaned:
        _add_event(rows, event)

    ordered = [rows[key] for key in sorted(rows)]
    _write_ndjson(output, ordered)

    starts = [
        datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
        for row in ordered
    ]
    logical_starts = [logical_date(start_dt) for start_dt in starts]
    covered_dates = _merge_covered_dates(
        manifest=output.with_suffix(".manifest.json"),
        observed_dates=set(logical_starts),
        start=start,
        end=end,
    )
    manifest = {
        "dataset": "activitywatch.events",
        "schema_version": ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "row_count": len(ordered),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": min(starts).date().isoformat() if starts else None,
        "last_timestamp_date": max(starts).date().isoformat() if starts else None,
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


def _add_event(rows: dict[tuple[str, str, str, str], dict[str, Any]], event: AWEvent) -> None:
    data_json = json.dumps(event.data, ensure_ascii=False, sort_keys=True)
    key = (
        event.bucket,
        event.start.isoformat(),
        event.end.isoformat(),
        data_json,
    )
    rows[key] = {
        "bucket": event.bucket,
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "data": event.data,
    }


def _add_row(rows: dict[tuple[str, str, str, str], dict[str, Any]], row: dict[str, Any]) -> None:
    data = row.get("data")
    data_json = json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=False, sort_keys=True)
    key = (
        str(row.get("bucket") or ""),
        str(row.get("start") or ""),
        str(row.get("end") or ""),
        data_json,
    )
    rows[key] = {
        "bucket": key[0],
        "start": key[1],
        "end": key[2],
        "data": data if isinstance(data, dict) else {},
    }


def _read_existing_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
) -> tuple[date, ...]:
    if start is None or end is None:
        return tuple(sorted(observed_dates))
    return merge_manifest_covered_dates(
        manifest=manifest,
        observed_dates=observed_dates,
        start=start,
        end=end,
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
