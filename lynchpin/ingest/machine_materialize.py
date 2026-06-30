"""Materialize canonical machine telemetry products."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.machine import (
    block_device_samples,
    canonical_machine_table_path,
    gpu_samples,
    metric_samples,
    network_samples,
    process_io_delta_samples,
    process_memory_samples,
    cgroup_memory_samples,
    sample_to_json,
    service_cgroup_io_samples,
    service_cgroup_pressure_samples,
    service_states,
)
from ._manifest import write_manifest


MACHINE_TELEMETRY_SCHEMA_VERSION = 1
MachineRow = dict[str, Any]
MACHINE_TABLES = (
    "metric_sample",
    "gpu_sample",
    "network_sample",
    "service_state",
    "block_device_sample",
    "service_cgroup_io_sample",
    "service_cgroup_pressure_sample",
    "process_io_delta_sample",
    "process_memory_sample",
    "cgroup_memory_sample",
)


def materialize_machine_telemetry(
    *, start: date | None = None, end: date | None = None
) -> dict[str, Any]:
    if (start is None) != (end is None):
        raise MaterializationError("machine_materialize", reason="machine materialization requires both start and end")
    if start is not None and end is not None and end <= start:
        raise MaterializationError("machine_materialize", reason="machine materialization end must be after start")
    cfg = get_config()
    input_files = machine_input_files(cfg)
    source_end = end - timedelta(days=1) if end is not None else None
    reports = {
        "metric_sample": _materialize_table(
            "metric_sample",
            lambda: metric_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "gpu_sample": _materialize_table(
            "gpu_sample",
            lambda: gpu_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "network_sample": _materialize_table(
            "network_sample",
            lambda: network_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "service_state": _materialize_table(
            "service_state",
            lambda: service_states(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "block_device_sample": _materialize_table(
            "block_device_sample",
            lambda: block_device_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "service_cgroup_io_sample": _materialize_table(
            "service_cgroup_io_sample",
            lambda: service_cgroup_io_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "service_cgroup_pressure_sample": _materialize_table(
            "service_cgroup_pressure_sample",
            lambda: service_cgroup_pressure_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "process_io_delta_sample": _materialize_table(
            "process_io_delta_sample",
            lambda: process_io_delta_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "process_memory_sample": _materialize_table(
            "process_memory_sample",
            lambda: process_memory_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
        "cgroup_memory_sample": _materialize_table(
            "cgroup_memory_sample",
            lambda: cgroup_memory_samples(start=start, end=source_end, path=cfg.machine_telemetry_db),
            start=start,
            end=end,
        ),
    }
    covered_dates = tuple(
        sorted(
            {
                date.fromisoformat(str(raw))
                for report in reports.values()
                for raw in report.get("covered_dates", [])
            }
        )
    )
    manifest_path = canonical_machine_table_path("manifest").with_suffix(".json")
    manifest = {
        "dataset": "machine.telemetry",
        "schema_version": MACHINE_TELEMETRY_SCHEMA_VERSION,
        "tables": reports,
        "row_count": sum(int(report["row_count"]) for report in reports.values()),
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "window_start": start.isoformat() if start is not None else None,
        "window_end": end.isoformat() if end is not None else None,
        "window_semantics": "start inclusive, end exclusive" if start is not None and end is not None else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest_path, manifest)
    return manifest


def machine_input_files(cfg: Any) -> tuple[Path, ...]:
    db = Path(cfg.machine_telemetry_db)
    return (db,) if db.exists() else ()


def _materialize_table(
    name: str,
    rows_fn: Callable[[], Iterable[object]],
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    output = canonical_machine_table_path(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = output.with_name(output.name + ".tmp")
    row_count = 0
    timestamps: list[datetime] = []
    observed_dates: set[date] = set()

    def write_row(handle: Any, row: MachineRow) -> None:
        nonlocal row_count
        row_count += 1
        timestamp = _row_timestamp(row)
        timestamps.append(timestamp)
        observed_dates.add(timestamp.date())
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    with tmp_output.open("w", encoding="utf-8") as handle:
        if start is not None and end is not None:
            for row in _iter_existing_rows(output):
                if _row_date(row) < start:
                    write_row(handle, row)

        for sample in rows_fn():
            write_row(handle, sample_to_json(sample))

        if start is not None and end is not None:
            for row in _iter_existing_rows(output):
                if _row_date(row) >= end:
                    write_row(handle, row)

    tmp_output.replace(output)
    covered_dates = _covered_dates_for_table(
        name,
        observed_dates=observed_dates,
        start=start,
        end=end,
    )
    return {
        "path": str(output),
        "row_count": row_count,
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": min(timestamps).date().isoformat() if timestamps else None,
        "last_timestamp_date": max(timestamps).date().isoformat() if timestamps else None,
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
    }


def _read_existing_rows(path: Path) -> list[MachineRow]:
    return list(_iter_existing_rows(path))


def _iter_existing_rows(path: Path) -> Iterable[MachineRow]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict) and payload.get("observed_at"):
                yield payload


def _row_timestamp(row: MachineRow) -> datetime:
    return datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))


def _row_date(row: MachineRow) -> date:
    return _row_timestamp(row).date()


def _covered_dates_for_table(
    name: str,
    *,
    observed_dates: set[date],
    start: date | None,
    end: date | None,
) -> tuple[date, ...]:
    covered = set(observed_dates)
    if start is not None and end is not None:
        manifest = canonical_machine_table_path("manifest").with_suffix(".json")
        if manifest.exists():
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            tables = payload.get("tables") if isinstance(payload.get("tables"), dict) else {}
            table_meta = tables.get(name) if isinstance(tables.get(name), dict) else {}
            for raw in table_meta.get("covered_dates", []):
                try:
                    day = date.fromisoformat(str(raw))
                except ValueError:
                    continue
                if not (start <= day < end):
                    covered.add(day)
        covered.update(start + timedelta(days=offset) for offset in range((end - start).days))
    return tuple(sorted(covered))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical machine telemetry")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    report = materialize_machine_telemetry(start=args.start, end=args.end)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
