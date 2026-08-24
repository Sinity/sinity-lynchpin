"""Materialize canonical ActivityWatch event products."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from itertools import chain
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.primitives import date_to_dt_range, logical_date
from ..sources.activitywatch_dedup import dedup_and_merge
from ..sources.activitywatch_raw import (
    canonical_activitywatch_events_path,
    events_from_activitywatch_dbs,
)
from ..sources.activitywatch_models import AWEvent
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import (
    atomic_write_indexed_ndjson,
    guard_incremental_shrinkage,
    replace_indexed_ndjson_tail,
    write_manifest,
)

BUCKET_PREFIXES = ("aw-watcher-window_", "aw-watcher-afk_", "aw-watcher-web-")
ACTIVITYWATCH_EVENTS_SCHEMA_VERSION = 1


def materialize_activitywatch_events(
    *,
    output: Path | None = None,
    dedupe: bool = True,
    start: date | None = None,
    end: date | None = None,
    refresh_id: str | None = None,
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
    if window is None:
        raw = events_from_activitywatch_dbs(BUCKET_PREFIXES, order="bucket", dedupe=False)
    else:
        raw = events_from_activitywatch_dbs(
            BUCKET_PREFIXES,
            start=window[0],
            end=window[1],
            order="bucket",
            dedupe=False,
        )
    cleaned = dedup_and_merge(raw) if dedupe else raw
    manifest_path = output.with_suffix(".manifest.json")
    previous_manifest = _read_manifest(manifest_path)
    bounded_tail = (
        start is not None
        and end is not None
        and _can_replace_tail(output, previous_manifest, end=end)
    )
    if start is not None and end is not None and output.exists() and not bounded_tail:
        raise MaterializationError(
            "activitywatch.events",
            reason=(
                "incremental ActivityWatch materialization only accepts an indexed "
                "append-compatible tail; run an explicit full rebuild for a middle "
                "window or an unindexed carrier"
            ),
        )
    carrier_start = start - timedelta(days=1) if start is not None else None
    ordered: Iterable[dict[str, Any]]
    if bounded_tail:
        assert carrier_start is not None and start is not None
        boundary_rows = _iter_indexed_rows(
            output,
            previous_manifest.get("row_offsets"),
            start=carrier_start,
            stop=start,
        )
        ordered = chain(boundary_rows, (_event_row(event) for event in cleaned))
    else:
        ordered = (_event_row(event) for event in cleaned)
    ordered = list(sorted(ordered, key=lambda item: (_row_logical_date(item) or date.min, _row_key(item))))
    valid_rows: list[tuple[dict[str, Any], datetime]] = []
    row_counts: dict[str, int] = {}
    for row in ordered:
        try:
            start_dt = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        valid_rows.append((row, start_dt))
        day = logical_date(start_dt).isoformat()
        row_counts[day] = row_counts.get(day, 0) + 1
    row_count = len(valid_rows)
    first_start = min((item[1] for item in valid_rows), default=None)
    last_start = max((item[1] for item in valid_rows), default=None)
    observed_dates = {logical_date(item[1]) for item in valid_rows}
    def tracked_rows() -> Iterator[dict[str, Any]]:
        for row, _start_dt in valid_rows:
            yield row

    try:
        combined_count = _combined_row_count(
            previous_manifest, row_count, bounded_tail=bounded_tail, start=carrier_start
        )
        if start is not None and end is not None:
            guard_incremental_shrinkage(
                manifest_path,
                combined_count,
                dataset="activitywatch.events",
            )
        if bounded_tail:
            assert carrier_start is not None
            row_offsets = replace_indexed_ndjson_tail(
                output,
                tracked_rows(),
                start=carrier_start,
                date_getter=_row_logical_date,
                offsets=previous_manifest.get("row_offsets"),
            )
            row_offsets = {
                **{
                    day: offset
                    for day, offset in (
                        previous_manifest.get("row_offsets", {})
                        if isinstance(previous_manifest.get("row_offsets"), dict)
                        else {}
                    ).items()
                    if isinstance(offset, int) and carrier_start is not None and _safe_date(day) < carrier_start
                },
                **row_offsets,
            }
        else:
            row_offsets = atomic_write_indexed_ndjson(
                output,
                tracked_rows(),
                date_getter=_row_logical_date,
            )
    except Exception:
        raise
    verified_bounds = (
        (min(observed_dates), max(observed_dates)) if observed_dates else None
    )
    covered_dates = _merge_covered_dates(
        manifest=manifest_path,
        observed_dates=observed_dates,
        start=start,
        end=end,
        verified_bounds=verified_bounds,
    )
    manifest = {
        "dataset": "activitywatch.events",
        "schema_version": ACTIVITYWATCH_EVENTS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "row_count": _combined_row_count(previous_manifest, row_count, bounded_tail=bounded_tail, start=carrier_start),
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
        "row_order": "logical_date",
        "row_offsets": row_offsets,
        "row_counts": _combined_row_counts(previous_manifest, row_counts, bounded_tail=bounded_tail),
        "bucket_prefixes": list(BUCKET_PREFIXES),
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "refresh_id": refresh_id,
    }
    write_manifest(manifest_path, manifest)
    return manifest


def _event_row(event: AWEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
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


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_indexed_rows(
    path: Path,
    offsets: object,
    *,
    start: date,
    stop: date,
) -> Iterator[dict[str, Any]]:
    if not isinstance(offsets, dict):
        return
    parsed: list[tuple[date, int]] = []
    for raw_day, raw_offset in offsets.items():
        if not isinstance(raw_offset, int):
            continue
        try:
            parsed.append((date.fromisoformat(str(raw_day)), raw_offset))
        except ValueError:
            continue
    if not parsed:
        return
    seek = max((offset for day, offset in parsed if day <= start), default=min(offset for _, offset in parsed))
    with path.open("rb") as handle:
        handle.seek(seek)
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
                day = _row_logical_date(row)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if day is None:
                continue
            if day >= stop:
                break
            if day >= start and isinstance(row, dict):
                yield row


def _safe_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.max


def _can_replace_tail(path: Path, manifest: dict[str, Any], *, end: date) -> bool:
    if not path.exists() or manifest.get("row_order") != "logical_date":
        return False
    offsets = manifest.get("row_offsets")
    if not isinstance(offsets, dict) or not offsets:
        return False
    last = _safe_date(manifest.get("last_date"))
    return last != date.max and end >= last + timedelta(days=1)


def _combined_row_count(
    previous_manifest: dict[str, Any],
    tail_count: int,
    *,
    bounded_tail: bool,
    start: date | None,
) -> int:
    if not bounded_tail:
        return tail_count
    previous = previous_manifest.get("row_count")
    counts = previous_manifest.get("row_counts")
    if not isinstance(previous, int) or not isinstance(counts, dict):
        return tail_count
    old_tail = sum(
        value
        for day, value in counts.items()
        if isinstance(value, int) and start is not None and _safe_date(day) >= start
    )
    return previous - old_tail + tail_count


def _combined_row_counts(
    previous_manifest: dict[str, Any],
    tail_counts: dict[str, int],
    *,
    bounded_tail: bool,
) -> dict[str, int]:
    if not bounded_tail:
        return tail_counts
    previous = previous_manifest.get("row_counts")
    combined = dict(previous) if isinstance(previous, dict) else {}
    start = min((_safe_date(day) for day in tail_counts), default=date.max)
    for day in tuple(combined):
        if _safe_date(day) >= start:
            combined.pop(day, None)
    combined.update(tail_counts)
    return {str(day): int(value) for day, value in combined.items() if isinstance(value, int)}


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
