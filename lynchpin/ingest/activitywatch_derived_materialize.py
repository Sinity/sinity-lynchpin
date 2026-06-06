"""Materialize graph-facing ActivityWatch derived products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.io import latest_mtime_iso
from ..core.parse import local_tz
from ..core.primitives import logical_date
from ..sources.activitywatch import (
    attention,
    circadian,
    deep_work,
    focus_spans,
    fragmentation,
    loops,
    project_focus_days,
)
from ..sources.activitywatch_derived import (
    PRODUCT_KINDS,
    activitywatch_derived_dir,
    activitywatch_derived_manifest_path,
    activitywatch_derived_path,
)
from ..sources.activitywatch_raw import canonical_activitywatch_events_path
from .activitywatch_event_index_materialize import activitywatch_event_index_input_files
from .manifest_windows import merge_manifest_covered_dates


ACTIVITYWATCH_DERIVED_SCHEMA_VERSION = 1


def materialize_activitywatch_derived(
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    start, end = _default_window(start, end)
    if end <= start:
        raise ValueError("ActivityWatch derived materialization end must be after start")
    output_dir = activitywatch_derived_dir(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_dt = datetime.combine(start, time.min, tzinfo=local_tz())
    end_dt = datetime.combine(end, time.min, tzinfo=local_tz())
    end_inclusive = end - timedelta(days=1)

    window_rows = {
        "focus_spans": [
            _focus_span_row(span)
            for span in focus_spans(start=start_dt, end=end_dt, min_duration_s=60.0, enrich_polylogue=False)
        ],
        "project_focus_days": [
            _project_focus_day_row(row)
            for row in project_focus_days(start=start_dt, end=end_dt)
        ],
        "deep_work": [_deep_work_row(row) for row in deep_work(start=start_dt, end=end_dt)],
        "circadian": [_circadian_row(row) for row in circadian(start=start, end=end_inclusive)],
        "loops": [_loop_row(row) for row in loops(start=start_dt, end=end_dt)],
        "fragmentation": [_fragmentation_row(row) for row in fragmentation(start=start, end=end_inclusive)],
        "attention": [_attention_row(row) for row in attention(start=start, end=end_inclusive)],
    }
    rows = {
        kind: _merge_existing_rows(
            kind=kind,
            root=root,
            start=start,
            end=end,
            window_rows=window_rows[kind],
        )
        for kind in PRODUCT_KINDS
    }

    row_counts: dict[str, int] = {}
    paths: dict[str, str] = {}
    for kind in PRODUCT_KINDS:
        path = activitywatch_derived_path(kind, root)
        _write_ndjson(path, rows[kind])
        row_counts[kind] = len(rows[kind])
        paths[kind] = str(path)

    input_files = activitywatch_derived_input_files()
    covered_dates = _merge_covered_dates(root=root, start=start, end=end)
    manifest = {
        "dataset": "lynchpin.activitywatch_derived",
        "schema_version": ACTIVITYWATCH_DERIVED_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "date_boundary": "logical_06:00_local",
        "product_paths": paths,
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
    activitywatch_derived_manifest_path(root).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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


def _merge_existing_rows(
    *,
    kind: str,
    root: Path | None,
    start: date,
    end: date,
    window_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    path = activitywatch_derived_path(kind, root)
    existing = _read_existing_rows(path)
    outside_window = [
        row for row in existing
        if not (start <= _row_logical_date(kind, row) < end)
    ]
    return sorted(
        [*outside_window, *window_rows],
        key=lambda row: _row_sort_key(kind, row),
    )


def _merge_covered_dates(*, root: Path | None, start: date, end: date) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=activitywatch_derived_manifest_path(root),
        start=start,
        end=end,
    )


def _read_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
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
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
    args = parser.parse_args(argv)
    report = materialize_activitywatch_derived(start=args.start, end=args.end, root=args.root)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
