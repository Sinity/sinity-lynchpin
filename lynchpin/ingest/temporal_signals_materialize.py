"""Materialize deterministic temporal signal events."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.temporal_signals import (
    ANOMALY_BASELINE_DAYS,
    detect_temporal_signals,
    temporal_signals_path,
)
from ._manifest import (
    atomic_write_indexed_ndjson,
    guard_incremental_shrinkage,
    replace_indexed_ndjson_tail,
    write_manifest,
)
from .manifest_windows import merge_manifest_covered_dates


SignalRow = dict[str, Any]
TEMPORAL_SIGNALS_SCHEMA_VERSION = 1


def materialize_temporal_signals(
    *,
    start: date | None = None,
    end: date | None = None,
    output: Path | None = None,
    refresh_id: str | None = None,
) -> dict[str, Any]:
    output = output or temporal_signals_path()
    start, end = _default_window(start, end)
    if end <= start:
        raise MaterializationError("temporal_signals_materialize", reason="temporal signal materialization end must be after start")
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
    previous_manifest = _read_manifest(output.with_suffix(".manifest.json"))
    bounded_tail = _can_replace_tail(output, previous_manifest, start=start, end=end)
    if output.exists() and not bounded_tail:
        legacy_index = _index_legacy_tail(output, previous_manifest, end=end)
        if legacy_index is not None:
            offsets, counts = legacy_index
            previous_manifest = {
                **previous_manifest,
                "row_offsets": offsets,
                "row_counts": counts,
            }
            bounded_tail = True
    if output.exists() and not bounded_tail:
        raise MaterializationError(
            "lynchpin.temporal_signals",
            reason="incremental temporal-signal materialization requires an indexed append-compatible tail",
        )
    rows = window_rows
    rows.sort(key=lambda row: (row["event_date"], row["kind"], row["signal"], json.dumps(row["payload"], sort_keys=True)))

    output.parent.mkdir(parents=True, exist_ok=True)
    if bounded_tail:
        old_counts = {
            str(day): int(value)
            for day, value in (previous_manifest.get("row_counts") or {}).items()
            if isinstance(value, int) and str(day) < start.isoformat()
        }
        for row in rows:
            day = str(row["event_date"])
            old_counts[day] = old_counts.get(day, 0) + 1
        total_row_count = sum(old_counts.values())
    else:
        total_row_count = len(rows)
    guard_incremental_shrinkage(output.with_suffix(".manifest.json"), total_row_count, dataset="lynchpin.temporal_signals")
    if bounded_tail:
        row_offsets = replace_indexed_ndjson_tail(
            output,
            rows,
            start=start,
            date_getter=lambda row: date.fromisoformat(str(row["event_date"])),
            offsets=previous_manifest.get("row_offsets"),
        )
        row_counts = {
            str(day): int(value)
            for day, value in (previous_manifest.get("row_counts") or {}).items()
            if isinstance(value, int) and str(day) < start.isoformat()
        }
        for row in rows:
            day = str(row["event_date"])
            row_counts[day] = row_counts.get(day, 0) + 1
    else:
        row_offsets = atomic_write_indexed_ndjson(
            output,
            rows,
            date_getter=lambda row: date.fromisoformat(str(row["event_date"])),
        )
        total_row_count = len(rows)
        row_counts = {}
        for row in rows:
            day = str(row["event_date"])
            row_counts[day] = row_counts.get(day, 0) + 1
    input_files = _temporal_input_files(start, end)
    event_dates = [date.fromisoformat(str(row["event_date"])) for row in rows]
    verified_dates = list(event_dates)
    if bounded_tail:
        for key in ("first_date", "last_date"):
            raw = previous_manifest.get(key)
            if isinstance(raw, str):
                try:
                    verified_dates.append(date.fromisoformat(raw))
                except ValueError:
                    pass
    covered_dates = _merge_covered_dates(
        manifest=output.with_suffix(".manifest.json"),
        start=start,
        end=end,
        verified_bounds=(min(verified_dates), max(verified_dates)) if verified_dates else None,
    )
    counts = Counter(str(row["kind"]) for row in rows)
    # window_semantics: [start, end) — start is inclusive, end is exclusive.
    # inclusive_end = end - 1 day is passed to detect_temporal_signals() so the
    # last analysed date is end - 1.  The manifest records the half-open
    # [start, end) interval that is the canonical convention across lynchpin.
    manifest = {
        "dataset": "lynchpin.temporal_signals",
        "schema_version": TEMPORAL_SIGNALS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_semantics": "[start, end) — start inclusive, end exclusive",
        "baseline_days": ANOMALY_BASELINE_DAYS,
        "row_count": total_row_count,
        "kind_counts": dict(sorted(counts.items())),
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "row_offsets": row_offsets,
        "row_counts": dict(sorted(row_counts.items())),
        "row_order": "logical_date",
        "refresh_id": refresh_id,
    }
    write_manifest(output.with_suffix(".manifest.json"), manifest)
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
        raise MaterializationError("temporal_signals_materialize", reason="temporal signal materialization requires both start and end")
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


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _can_replace_tail(
    output: Path,
    manifest: dict[str, Any],
    *,
    start: date,
    end: date,
) -> bool:
    if not output.exists() or not isinstance(manifest.get("row_offsets"), dict):
        return False
    last = manifest.get("last_date")
    try:
        return isinstance(last, str) and end >= date.fromisoformat(last) + timedelta(days=1)
    except ValueError:
        return False


def _index_legacy_tail(
    output: Path,
    manifest: dict[str, Any],
    *,
    end: date,
) -> tuple[dict[str, int], dict[str, int]] | None:
    """Prove and index a legacy sorted carrier once, without rewriting history."""
    last = manifest.get("last_date")
    try:
        last_day = date.fromisoformat(last) if isinstance(last, str) else None
        if last_day is None or end < last_day + timedelta(days=1):
            return None
    except ValueError:
        return None
    offsets: dict[str, int] = {}
    counts: dict[str, int] = {}
    previous_day: date | None = None
    try:
        with output.open("rb") as handle:
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    return None
                raw_day = payload.get("event_date")
                if not isinstance(raw_day, str):
                    return None
                day = date.fromisoformat(raw_day)
                if previous_day is not None and day < previous_day:
                    return None
                previous_day = day
                offsets.setdefault(raw_day, offset)
                counts[raw_day] = counts.get(raw_day, 0) + 1
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    # Coverage may legitimately end on a day with no emitted signal, so the
    # final row can precede the manifest's last verified date.
    if previous_day is None or previous_day > last_day:
        return None
    return offsets, counts


def _merge_covered_dates(
    *,
    manifest: Path,
    start: date,
    end: date,
    verified_bounds: tuple[date, date] | None = None,
) -> tuple[date, ...]:
    return merge_manifest_covered_dates(
        manifest=manifest,
        start=start,
        end=end,
        fallback_to_bounds=False,
        verified_bounds=verified_bounds,
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
    from ..materialization import audit_materialization, ensure_materialized

    rows = {row.name: row for row in audit_materialization()}
    activitywatch_window = (start, end + timedelta(days=1))
    activitywatch = rows.get("activitywatch_derived")
    if (
        activitywatch is not None
        and activitywatch.tail_stale
        and activitywatch.last_date is not None
    ):
        # ActivityWatch is append-oriented on the live host. Recompute the
        # last materialized logical day as well as any new tail, while keeping
        # the already verified history out of the high-memory source path.
        activitywatch_window = (
            max(start, activitywatch.last_date),
            end + timedelta(days=1),
        )

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
        window = activitywatch_window if name == "activitywatch_derived" else (start, end + timedelta(days=1))
        row = rows.get(name)
        if row is not None and (row.status == "ready" or row.tail_stale) and row.first_date is not None:
            # An input product can legitimately begin or end outside the
            # aggregate temporal window, which is the union of every source's
            # history. Signal detection has no observation to recover beyond a
            # source's verified span, so convergence uses the intersection.
            # A live stale tail may still advance through today, but never to a
            # future timestamp supplied by a different source.
            window_start = max(window[0], row.first_date)
            if row.tail_stale:
                window_end = min(window[1], date.today() + timedelta(days=1))
            elif row.last_date is not None:
                window_end = min(window[1], row.last_date + timedelta(days=1))
            else:
                window_end = window[1]
            window = (window_start, window_end)
        if window[0] >= window[1]:
            continue
        ensure_materialized(name, window=window)


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
