"""Materialize deterministic temporal signal events."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..core.io import latest_mtime_iso
from ..graph.temporal_signals import ANOMALY_BASELINE_DAYS, detect_temporal_signals
from ..sources.temporal_signals import temporal_signals_path
from .manifest_windows import merge_manifest_covered_dates


SignalRow = dict[str, Any]
TEMPORAL_SIGNALS_SCHEMA_VERSION = 1


def materialize_temporal_signals(
    *,
    start: date | None = None,
    end: date | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    output = output or temporal_signals_path()
    start, end = _default_window(start, end)
    if end <= start:
        raise ValueError("temporal signal materialization end must be after start")
    inclusive_end = end - timedelta(days=1)
    history_start = start - timedelta(days=ANOMALY_BASELINE_DAYS)
    _ensure_temporal_inputs(history_start, inclusive_end)
    window_rows = [
        _event_row(event)
        for event in detect_temporal_signals(
            start=start,
            end=inclusive_end,
            ensure_inputs=False,
        )
    ]
    rows = _merge_existing_rows(output=output, start=start, end=end, window_rows=window_rows)
    rows.sort(key=lambda row: (row["event_date"], row["kind"], row["signal"], json.dumps(row["payload"], sort_keys=True)))

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    input_files = _temporal_input_files(start, end)
    covered_dates = _merge_covered_dates(manifest=output.with_suffix(".manifest.json"), start=start, end=end)
    counts = Counter(str(row["kind"]) for row in rows)
    manifest = {
        "dataset": "lynchpin.temporal_signals",
        "schema_version": TEMPORAL_SIGNALS_SCHEMA_VERSION,
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "start inclusive, end exclusive",
        "baseline_days": ANOMALY_BASELINE_DAYS,
        "row_count": len(rows),
        "kind_counts": dict(sorted(counts.items())),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _event_row(event: Any) -> SignalRow:
    return {
        "kind": str(event.kind),
        "signal": str(event.signal),
        "event_date": event.event_date.isoformat(),
        "summary": str(event.summary),
        "payload": dict(event.payload),
    }


def _default_window(start: date | None, end: date | None) -> tuple[date, date]:
    if (start is None) != (end is None):
        raise ValueError("temporal signal materialization requires both start and end")
    if start is not None and end is not None:
        return start, end

    from ..materialization import audit_materialization

    bounds = [
        (row.first_date, row.last_date)
        for row in audit_materialization()
        if row.name != "temporal_signals"
        and row.status == "ready"
        and row.first_date is not None
        and row.last_date is not None
    ]
    if not bounds:
        today = date.today()
        return today, today + timedelta(days=1)
    first = min(first for first, _last in bounds if first is not None)
    last = max(last for _first, last in bounds if last is not None)
    return first, last + timedelta(days=1)


def _merge_existing_rows(
    *,
    output: Path,
    start: date,
    end: date,
    window_rows: list[SignalRow],
) -> list[SignalRow]:
    outside_window = [
        row for row in _read_existing_rows(output)
        if not (start <= date.fromisoformat(str(row["event_date"])) < end)
    ]
    return [*outside_window, *window_rows]


def _read_existing_rows(path: Path) -> list[SignalRow]:
    if not path.exists():
        return []
    rows: list[SignalRow] = []
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


def _temporal_input_files(start: date, end: date) -> tuple[Path, ...]:
    from ..materialization import audit_materialization, materialized_dataset_overlaps

    history_start = start - timedelta(days=ANOMALY_BASELINE_DAYS)
    paths: list[Path] = []
    for row in audit_materialization():
        if row.name == "temporal_signals":
            continue
        if not materialized_dataset_overlaps(row, start=history_start, end=end):
            continue
        for path in tuple(row.materialized_paths) or tuple(row.raw_roots):
            if path.exists() and not (path.suffix == ".json" and "manifest" in path.name):
                paths.append(path)
    return tuple(dict.fromkeys(paths))


def _ensure_temporal_inputs(start: date, end: date) -> None:
    from ..materialization import ensure_materialized

    for name in (
        "activitywatch_derived",
        "atuin",
        "polylogue",
        "webhistory",
        "browser_bookmarks",
        "communications",
        "arbtt",
        "google_takeout",
        "sleep",
        "health",
    ):
        ensure_materialized(name, window=(start, end + timedelta(days=1)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize temporal signal events")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    report = materialize_temporal_signals(start=args.start, end=args.end)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
