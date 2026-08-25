"""Materialize canonical machine telemetry products."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.machine import (
    block_device_samples,
    canonical_machine_table_path,
    cgroup_memory_samples,
    gpu_samples,
    kill_events,
    metric_samples,
    network_samples,
    process_io_delta_samples,
    process_memory_samples,
    sample_to_json,
    service_cgroup_io_samples,
    service_cgroup_pressure_samples,
    service_states,
)
from ._manifest import atomic_text_writer, write_manifest

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
    "kill_event",
)
_UNIQUE_STAGING = re.compile(r"^\.(?P<name>[a-z0-9_]+\.ndjson)\.[0-9a-f]{32}\.tmp$")


def _path_is_open(path: Path) -> bool:
    """Ask the kernel-facing owner probe whether the exact candidate is open."""
    completed = subprocess.run(
        ["fuser", "-s", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise RuntimeError(f"fuser could not inspect {path}: {completed.stderr.strip()}")


def cleanup_machine_staging(
    *,
    grace_period_s: float = 24 * 60 * 60,
    apply: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Preview or remove abandoned machine-carrier staging files.

    Candidates are derived only from canonical table destinations. The legacy
    fixed ``<table>.ndjson.tmp`` shape and current unique hidden staging shape
    are recognized; serving files, unknown names, links, open files, and files
    inside the grace period are never removed.
    """
    if grace_period_s < 0:
        raise ValueError("machine staging grace period must be non-negative")
    current_time = time.time() if now is None else now
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for table in MACHINE_TABLES:
        serving = canonical_machine_table_path(table)
        parent = serving.parent
        candidates = (
            serving.with_name(f"{serving.name}.tmp"),
            *parent.glob(f".{serving.name}.*.tmp"),
        )
        for candidate in sorted(candidates):
            if candidate in seen or not candidate.exists():
                continue
            seen.add(candidate)
            expected_legacy = candidate.name == f"{serving.name}.tmp"
            unique_match = _UNIQUE_STAGING.fullmatch(candidate.name)
            expected_unique = (
                unique_match is not None and unique_match.group("name") == serving.name
            )
            try:
                before = candidate.lstat()
            except OSError as exc:
                entries.append(
                    {
                        "path": str(candidate),
                        "disposition": "unreadable",
                        "detail": type(exc).__name__,
                    }
                )
                continue
            base = {
                "path": str(candidate),
                "table": table,
                "size_bytes": before.st_size,
                "age_seconds": max(0.0, current_time - before.st_mtime),
                "shape": "legacy"
                if expected_legacy
                else "unique"
                if expected_unique
                else "unknown",
            }
            if (
                not (expected_legacy or expected_unique)
                or stat.S_ISLNK(before.st_mode)
                or not stat.S_ISREG(before.st_mode)
            ):
                entries.append({**base, "disposition": "unsafe"})
                continue
            identity = (before.st_dev, before.st_ino)
            if _path_is_open(candidate):
                entries.append({**base, "disposition": "active"})
                continue
            if base["age_seconds"] < grace_period_s:
                entries.append({**base, "disposition": "grace"})
                continue
            if not apply:
                entries.append({**base, "disposition": "stale"})
                continue
            try:
                current = candidate.lstat()
                if (current.st_dev, current.st_ino) != identity or not stat.S_ISREG(
                    current.st_mode
                ):
                    raise RuntimeError("candidate identity changed before deletion")
                if _path_is_open(candidate):
                    entries.append({**base, "disposition": "active"})
                    continue
                candidate.unlink()
            except (OSError, RuntimeError) as exc:
                entries.append(
                    {**base, "disposition": "deletion-failed", "detail": str(exc)}
                )
            else:
                entries.append({**base, "disposition": "deleted"})
    return {
        "schema_version": 1,
        "dry_run": not apply,
        "grace_period_s": grace_period_s,
        "deleted_bytes": sum(
            entry.get("size_bytes", 0)
            for entry in entries
            if entry["disposition"] == "deleted"
        ),
        "reclaimable_bytes": sum(
            entry.get("size_bytes", 0)
            for entry in entries
            if entry["disposition"] in {"stale", "deleted"}
        ),
        "entries": entries,
    }


def materialize_machine_telemetry(
    *, start: date | None = None, end: date | None = None
) -> dict[str, Any]:
    if (start is None) != (end is None):
        raise MaterializationError(
            "machine_materialize",
            reason="machine materialization requires both start and end",
        )
    if start is not None and end is not None and end <= start:
        raise MaterializationError(
            "machine_materialize",
            reason="machine materialization end must be after start",
        )
    cfg = get_config()
    input_files = machine_input_files(cfg)
    source_end = end - timedelta(days=1) if end is not None else None
    reports = {
        "metric_sample": _materialize_table(
            "metric_sample",
            lambda: metric_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "gpu_sample": _materialize_table(
            "gpu_sample",
            lambda: gpu_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "network_sample": _materialize_table(
            "network_sample",
            lambda: network_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "service_state": _materialize_table(
            "service_state",
            lambda: service_states(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "block_device_sample": _materialize_table(
            "block_device_sample",
            lambda: block_device_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "service_cgroup_io_sample": _materialize_table(
            "service_cgroup_io_sample",
            lambda: service_cgroup_io_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "service_cgroup_pressure_sample": _materialize_table(
            "service_cgroup_pressure_sample",
            lambda: service_cgroup_pressure_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "process_io_delta_sample": _materialize_table(
            "process_io_delta_sample",
            lambda: process_io_delta_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "process_memory_sample": _materialize_table(
            "process_memory_sample",
            lambda: process_memory_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "cgroup_memory_sample": _materialize_table(
            "cgroup_memory_sample",
            lambda: cgroup_memory_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
        ),
        "kill_event": _materialize_table(
            "kill_event",
            lambda: kill_events(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
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
        "window_semantics": "start inclusive, end exclusive"
        if start is not None and end is not None
        else None,
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
    row_count = 0
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    observed_dates: set[date] = set()

    def write_row(handle: Any, row: MachineRow) -> None:
        nonlocal first_timestamp, last_timestamp, row_count
        row_count += 1
        timestamp = _row_timestamp(row)
        if first_timestamp is None or timestamp < first_timestamp:
            first_timestamp = timestamp
        if last_timestamp is None or timestamp > last_timestamp:
            last_timestamp = timestamp
        observed_dates.add(timestamp.date())
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    with atomic_text_writer(output) as handle:
        if start is not None and end is not None:
            existing_rows = iter(_iter_existing_rows(output))
            first_after_window: MachineRow | None = None
            for row in existing_rows:
                row_date = _row_date(row)
                if row_date < start:
                    write_row(handle, row)
                elif row_date >= end:
                    first_after_window = row
                    break

            for sample in rows_fn():
                write_row(handle, sample_to_json(sample))

            if first_after_window is not None:
                write_row(handle, first_after_window)
            for row in existing_rows:
                write_row(handle, row)
        else:
            for sample in rows_fn():
                write_row(handle, sample_to_json(sample))

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
        "first_timestamp_date": first_timestamp.date().isoformat()
        if first_timestamp
        else None,
        "last_timestamp_date": last_timestamp.date().isoformat()
        if last_timestamp
        else None,
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
    return datetime.fromisoformat(str(row["observed_at"]))


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
            tables = (
                payload.get("tables") if isinstance(payload.get("tables"), dict) else {}
            )
            table_meta = tables.get(name) if isinstance(tables.get(name), dict) else {}
            for raw in table_meta.get("covered_dates", []):
                try:
                    day = date.fromisoformat(str(raw))
                except ValueError:
                    continue
                if not (start <= day < end):
                    covered.add(day)
        covered.update(
            start + timedelta(days=offset) for offset in range((end - start).days)
        )
    return tuple(sorted(covered))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize canonical machine telemetry"
    )
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--cleanup-staging", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--grace-period-s", type=float, default=24 * 60 * 60)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.cleanup_staging:
        if args.start is not None or args.end is not None:
            parser.error(
                "--cleanup-staging cannot be combined with a materialization window"
            )
        report = cleanup_machine_staging(
            grace_period_s=args.grace_period_s, apply=args.apply
        )
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.receipt is not None:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            with atomic_text_writer(args.receipt) as handle:
                handle.write(rendered)
        sys.stdout.write(rendered)
        return 0
    if args.apply or args.receipt is not None:
        parser.error("--apply and --receipt require --cleanup-staging")
    report = materialize_machine_telemetry(start=args.start, end=args.end)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
