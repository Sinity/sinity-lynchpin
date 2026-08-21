"""Materialize graph-facing ActivityWatch derived products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..core.parse import local_tz
from ..core.primitives import logical_date
from ..sources.activitywatch import (
    attention,
    circadian,
    daily_activity,
    deep_work,
    focus_spans,
    fragmentation,
    loops,
    project_focus_days,
)
from ..sources.activitywatch_derived import (
    PRODUCT_KINDS,
    activitywatch_derived_dir,
    activitywatch_derived_generation_dir,
    activitywatch_derived_manifest_path,
    activitywatch_derived_path,
)
from ..sources.activitywatch_event_index import activitywatch_event_index_manifest_path
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from .activitywatch_event_index_materialize import activitywatch_event_index_input_files
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import atomic_write_ndjson, guard_incremental_shrinkage, write_manifest


ACTIVITYWATCH_DERIVED_SCHEMA_VERSION = 3

# Bound on how many days of source events a single pass may hold in memory.
# The heavy allocations are the AWEvent objects and span intermediates inside
# the source generators, which scale with the requested window; chunking keeps
# the footprint window-length-independent (lynchpin-soa: a 760-day window
# reached ~5.6G resident and livelocked under a 2G cgroup cap).
DEFAULT_CHUNK_DAYS = 1


def materialize_activitywatch_derived(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
    chunk_days: int = DEFAULT_CHUNK_DAYS,
) -> dict[str, Any]:
    start, end = _default_window(start, end)
    if end <= start:
        raise MaterializationError("activitywatch_derived_materialize", reason="ActivityWatch derived materialization end must be after start")
    if chunk_days < 1:
        raise MaterializationError("activitywatch_derived_materialize", reason="chunk_days must be at least 1")
    clipped_start, clipped_end = _clip_to_real_coverage(start, end)
    if clipped_end <= clipped_start:
        # The requested window has no overlap with the canonical AW events
        # product's real coverage (lynchpin-3yp: an ensure-cascade from a
        # dependent materializer forwarded a window reaching back to 2013,
        # and the generators dutifully computed "outages" for days
        # ActivityWatch never ran on). There is nothing to materialize —
        # return the existing product unchanged rather than fabricate rows.
        existing_path = activitywatch_derived_manifest_path(root)
        if existing_path.exists():
            return json.loads(existing_path.read_text(encoding="utf-8"))
        raise MaterializationError(
            "activitywatch_derived_materialize",
            reason=f"requested window {start}..{end} has no overlap with canonical ActivityWatch coverage",
        )
    manifest: dict[str, Any] | None = None
    chunk_start = clipped_start
    while chunk_start < clipped_end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), clipped_end)
        manifest = _materialize_window(start=chunk_start, end=chunk_end, root=root)
        chunk_start = chunk_end
    assert manifest is not None  # loop runs at least once (clipped_end > clipped_start)
    return manifest


def _clip_to_real_coverage(start: date, end: date) -> tuple[date, date]:
    """Clip the half-open ``[start, end)`` request to indexed AW coverage.

    The logical-day event index is the active bounded product. The legacy
    canonical NDJSON remains an offline recovery carrier, so it is only a
    fallback when no index manifest has been published yet. Outside the real
    source span there is no event data, and asking generators to infer a day
    with no ActivityWatch coverage fabricates outages and other derived rows.
    """
    manifest = activitywatch_event_index_manifest_path()
    if not manifest.exists():
        manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not manifest.exists():
        # No materialized carrier to clip against. This is the normal test
        # state when source generators are monkeypatched directly.
        return start, end
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    first_raw = payload.get("first_date")
    last_raw = payload.get("last_date")
    if not first_raw or not last_raw:
        return start, end
    floor = date.fromisoformat(str(first_raw))
    ceiling = date.fromisoformat(str(last_raw)) + timedelta(days=1)  # end is exclusive
    return max(start, floor), min(end, ceiling)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _materialize_window(
    *,
    start: date,
    end: date,
    root: Path | None,
) -> dict[str, Any]:
    output_dir = activitywatch_derived_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.combine(start, time.min, tzinfo=local_tz())
    end_dt = datetime.combine(end, time.min, tzinfo=local_tz())
    end_inclusive = end - timedelta(days=1)

    window_rows = {
        "focus_spans": [
            _focus_span_row(span)
            for span in focus_spans(start=start_dt, end=end_dt, min_duration_s=60.0, enrich_polylogue=True)
        ],
        "project_focus_days": [_project_focus_day_row(row) for row in project_focus_days(start=start_dt, end=end_dt)],
        "daily_activity": [_daily_activity_row(row) for row in daily_activity(start=start, end=end_inclusive)],
        "deep_work": [_deep_work_row(row) for row in deep_work(start=start_dt, end=end_dt)],
        "circadian": [_circadian_row(row) for row in circadian(start=start, end=end_inclusive)],
        "loops": [_loop_row(row) for row in loops(start=start_dt, end=end_dt)],
        "fragmentation": [_fragmentation_row(row) for row in fragmentation(start=start, end=end_inclusive)],
        "attention": [_attention_row(row) for row in attention(start=start, end=end_inclusive)],
    }

    previous = _load_json(activitywatch_derived_manifest_path(root))
    paths, partition_counts = _existing_partitions(previous)
    migration = previous.get("schema_version") != ACTIVITYWATCH_DERIVED_SCHEMA_VERSION
    if migration:
        # The one-time v2-to-v3 conversion copies the small persisted derived
        # products into immutable logical-day files. It does not re-run raw
        # ActivityWatch history. Later tail refreshes never read these files.
        for kind in PRODUCT_KINDS:
            for row in _read_existing_rows(activitywatch_derived_path(kind, root)):
                day = _row_logical_date(kind, row).isoformat()
                paths[kind].setdefault(day, [])
                paths[kind][day].append(row)

    generation = f"generation-{uuid4().hex}"
    generation_dir = activitywatch_derived_generation_dir(generation, root)
    staging_dir = generation_dir.with_name(f".{generation}.staging")
    staging_dir.mkdir(parents=True)
    next_paths: dict[str, dict[str, str]] = {
        kind: {day: str(path) for day, path in path_map.items() if isinstance(path, Path)}
        for kind, path_map in paths.items()
    }
    next_counts: dict[str, dict[str, int]] = {
        kind: dict(counts) for kind, counts in partition_counts.items()
    }

    for kind in PRODUCT_KINDS:
        rows_by_day: dict[str, list[dict[str, object]]] = {}
        if migration:
            for day, legacy_rows in paths[kind].items():
                assert isinstance(legacy_rows, list)
                rows_by_day[day] = [
                    row for row in legacy_rows if not (start <= _row_logical_date(kind, row) < end)
                ]
        for row in window_rows[kind]:
            day = _row_logical_date(kind, row).isoformat()
            if start <= date.fromisoformat(day) < end:
                rows_by_day.setdefault(day, []).append(row)
        for day in tuple(next_paths[kind]):
            if start <= date.fromisoformat(day) < end:
                next_paths[kind].pop(day, None)
                next_counts[kind].pop(day, None)
        for day, rows in rows_by_day.items():
            if not rows:
                continue
            target = staging_dir / kind / f"{day}.ndjson"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_ndjson(target, sorted(rows, key=lambda row: _row_sort_key(kind, row)))
            next_paths[kind][day] = str(generation_dir / kind / target.name)
            next_counts[kind][day] = len(rows)

    generation_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.replace(generation_dir)
    row_counts = {kind: sum(next_counts[kind].values()) for kind in PRODUCT_KINDS}
    guard_incremental_shrinkage(
        activitywatch_derived_manifest_path(root),
        sum(row_counts.values()),
        dataset="lynchpin.activitywatch_derived",
    )

    input_files = activitywatch_derived_input_files()
    covered_dates = _merge_covered_dates(root=root, start=start, end=end)
    manifest = {
        "dataset": "lynchpin.activitywatch_derived",
        "schema_version": ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
        "generation": generation,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "date_boundary": "logical_06:00_local",
        "product_paths": next_paths,
        "partition_row_counts": next_counts,
        "row_counts": row_counts,
        "row_count": sum(row_counts.values()),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(activitywatch_derived_manifest_path(root), manifest)
    return manifest


def activitywatch_derived_input_files() -> tuple[Path, ...]:
    indexed = activitywatch_event_index_input_files()
    if indexed:
        return indexed
    return tuple(path for path in (canonical_activitywatch_events_path(),) if path.exists())


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    manifest = canonical_activitywatch_events_path().with_suffix(".manifest.json")
    if not manifest.exists():
        raise FileNotFoundError(
            "canonical ActivityWatch events manifest is missing; run "
            "python -m lynchpin.ingest.activitywatch_materialize first"
        )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    first = start or date.fromisoformat(str(payload["first_date"]))
    last_inclusive = date.fromisoformat(str(payload["last_date"]))
    return first, end or (last_inclusive + timedelta(days=1))


def _existing_partitions(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Path]], dict[str, dict[str, int]]]:
    raw_paths = manifest.get("product_paths")
    raw_counts = manifest.get("partition_row_counts")
    paths: dict[str, dict[str, Path]] = {kind: {} for kind in PRODUCT_KINDS}
    counts: dict[str, dict[str, int]] = {kind: {} for kind in PRODUCT_KINDS}
    if not isinstance(raw_paths, dict) or not isinstance(raw_counts, dict):
        return paths, counts
    for kind in PRODUCT_KINDS:
        product_paths = raw_paths.get(kind)
        product_counts = raw_counts.get(kind)
        if not isinstance(product_paths, dict) or not isinstance(product_counts, dict):
            return {name: {} for name in PRODUCT_KINDS}, {name: {} for name in PRODUCT_KINDS}
        for day, value in product_paths.items():
            path = Path(str(value))
            count = product_counts.get(day)
            if path.exists() and isinstance(count, int):
                paths[kind][str(day)] = path
                counts[kind][str(day)] = count
    return paths, counts


def _merge_covered_dates(
    *,
    root: Path | None,
    start: date,
    end: date,
    verified_bounds: tuple[date, date] | None = None,
) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=activitywatch_derived_manifest_path(root),
        start=start,
        end=end,
        verified_bounds=verified_bounds,
    )


def _read_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
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


def _row_logical_date(kind: str, row: dict[str, object]) -> date:
    if kind in {"focus_spans", "deep_work", "loops"}:
        return logical_date(datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00")))
    return date.fromisoformat(str(row["date"]))


def _row_sort_key(kind: str, row: dict[str, object]) -> tuple[str, str, str]:
    day = _row_logical_date(kind, row).isoformat()
    start = str(row.get("start") or "")
    return day, start, json.dumps(row, ensure_ascii=False, sort_keys=True)


def _write_ndjson(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_ndjson(path, rows)


def _focus_span_row(span: Any) -> dict[str, object]:
    return {
        "start": span.start.isoformat(),
        "end": span.end.isoformat(),
        "kind": span.kind,
        "app": span.app,
        "title": span.title,
        "mode": span.mode,
        "project": span.project,
        "duration_s": round(float(span.duration_s), 3),
        "keypress_count": int(getattr(span, "keypress_count", 0)),
        "keylog_state": getattr(span, "keylog_state", "not_requested"),
    }


def _project_focus_day_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "project": row.project,
        "duration_s": round(float(row.duration_s), 3),
    }


def _daily_activity_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "active_hours": round(float(row.active_hours), 3),
        "deep_work_min": round(float(row.deep_work_min), 3),
        "fragmentation_score": round(float(row.fragmentation_score), 6),
        "project_count": int(row.project_count),
        "dominant_mode": row.dominant_mode,
        "dominant_project": row.dominant_project,
        "hourly_active": [round(float(value), 3) for value in row.hourly_active],
        "outage_hours": round(float(row.outage_hours), 3),
        "presence_active_hours": round(float(row.presence_active_hours), 3),
        "presence_typing_hours": round(float(row.presence_typing_hours), 3),
        "presence_data_gap_hours": round(float(row.presence_data_gap_hours), 3),
    }


def _deep_work_row(row: Any) -> dict[str, object]:
    return {
        "start": row.start.isoformat(),
        "end": row.end.isoformat(),
        "duration_min": round(float(row.duration_min), 3),
        "project": row.project,
        "mode": row.mode,
        "focus_ratio": round(float(row.focus_ratio), 6),
        "app_switches": int(row.app_switches),
    }


def _circadian_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "hour": int(row.hour),
        "active_min": round(float(row.active_min), 3),
        "recovery_min": round(float(row.recovery_min), 3),
        "dominant_mode": row.dominant_mode,
        "dominant_project": row.dominant_project,
    }


def _loop_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "start": row.start.isoformat(),
        "end": row.end.isoformat(),
        "duration_min": round(float(row.duration_min), 3),
        "span_count": int(row.span_count),
        "switch_count": int(row.switch_count),
        "context_a": row.context_a,
        "context_b": row.context_b,
        "dominant_project": row.dominant_project,
    }


def _fragmentation_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "total_switches": int(row.total_switches),
        "avg_focus_min": round(float(row.avg_focus_min), 3),
        "longest_focus_min": round(float(row.longest_focus_min), 3),
        "fragmentation": round(float(row.fragmentation), 6),
    }


def _attention_row(row: Any) -> dict[str, object]:
    return {
        "date": row.date.isoformat(),
        "entropy": round(float(row.entropy), 6),
        "gini": round(float(row.gini), 6),
        "top_project": row.top_project,
        "project_count": int(row.project_count),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize graph-facing ActivityWatch derived products")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    args = parser.parse_args(argv)
    report = materialize_activitywatch_derived(
        start=args.start, end=args.end, root=args.root, chunk_days=args.chunk_days
    )
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
