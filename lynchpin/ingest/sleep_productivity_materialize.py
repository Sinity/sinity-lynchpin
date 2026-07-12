"""Materialize the sleep-to-productivity join."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.sleep import sleep_productivity
from ..sources.sleep_productivity import sleep_productivity_path
from .manifest_windows import merge_manifest_covered_dates
from ._manifest import write_manifest


ProductivityRow = dict[str, Any]
SLEEP_PRODUCTIVITY_SCHEMA_VERSION = 1


def materialize_sleep_productivity(
    *,
    start: date | None = None,
    end: date | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    output = output or sleep_productivity_path()
    start, end = _default_window(start, end)
    if end <= start:
        raise MaterializationError("sleep_productivity_materialize", reason="sleep-productivity materialization end must be after start")
    inclusive_end = end - timedelta(days=1)
    window_rows = [
        _productivity_row(row)
        for row in sleep_productivity(start=start, end=inclusive_end)
    ]
    rows = _merge_existing_rows(output=output, start=start, end=end, window_rows=window_rows)
    rows.sort(key=lambda row: row["sleep_date"])

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    input_files = _sleep_productivity_input_files(start, end)
    covered_dates = _merge_covered_dates(manifest=output.with_suffix(".manifest.json"), start=start, end=end)
    manifest = {
        "dataset": "lynchpin.sleep_productivity",
        "schema_version": SLEEP_PRODUCTIVITY_SCHEMA_VERSION,
        "materialized_path": str(output),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "row_count": len(rows),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
    return manifest


def _productivity_row(row: Any) -> ProductivityRow:
    return {
        "sleep_date": row.sleep_date.isoformat(),
        "sleep_hours": row.sleep_hours,
        "sleep_score": row.sleep_score,
        "sleep_quality": row.sleep_quality,
        "workday_active_hours": row.workday_active_hours,
        "workday_deep_work_min": row.workday_deep_work_min,
        "productivity_vs_baseline": row.productivity_vs_baseline,
    }


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if (start is None) != (end is None):
        raise MaterializationError("sleep_productivity_materialize", reason="sleep-productivity materialization requires both start and end")
    if start is not None and end is not None:
        return start, end

    from ..materialization import audit_materialization

    rows = {row.name: row for row in audit_materialization()}
    sleep = rows.get("sleep")
    if sleep is None or sleep.first_date is None or sleep.last_date is None:
        today = date.today()
        return today, today + timedelta(days=1)
    return sleep.first_date, sleep.last_date + timedelta(days=1)


def _merge_existing_rows(
    *,
    output: Path,
    start: date,
    end: date,
    window_rows: list[ProductivityRow],
) -> list[ProductivityRow]:
    outside_window = [
        row for row in _read_existing_rows(output)
        if not (start <= date.fromisoformat(str(row["sleep_date"])) < end)
    ]
    return [*outside_window, *window_rows]


def _read_existing_rows(path: Path) -> list[ProductivityRow]:
    if not path.exists():
        return []
    rows: list[ProductivityRow] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _merge_covered_dates(*, manifest: Path, start: date, end: date) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=manifest,
        start=start,
        end=end,
        fallback_to_bounds=False,
    )


def _sleep_productivity_input_files(start: date, end: date) -> tuple[Path, ...]:
    from ..materialization import audit_materialization, materialized_dataset_overlaps

    paths: list[Path] = []
    for row in audit_materialization():
        if row.name not in {"sleep", "activitywatch", "activitywatch_derived"}:
            continue
        if not materialized_dataset_overlaps(row, start=start, end=end + timedelta(days=1)):
            continue
        for path in tuple(row.materialized_paths) or tuple(row.raw_roots):
            if path.exists() and not (path.suffix == ".json" and "manifest" in path.name):
                paths.append(path)
    return tuple(dict.fromkeys(paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize sleep-productivity rows")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    report = materialize_sleep_productivity(start=args.start, end=args.end)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
