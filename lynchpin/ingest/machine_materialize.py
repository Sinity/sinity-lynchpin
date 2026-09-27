"""Materialize canonical machine telemetry products."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from itertools import groupby
from pathlib import Path
from threading import current_thread, main_thread
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
from ..sources.machine_package import load_machine_manifest, machine_table_files
from ._manifest import atomic_text_writer, write_manifest

MACHINE_TELEMETRY_SCHEMA_VERSION = 2
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
_PARTITION_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.[0-9a-f]{64}\.ndjson$")
_PARTITION_STAGING = re.compile(r"^\.\d{4}-\d{2}-\d{2}\.[a-z0-9_]+\.tmp$")


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

    Candidates are derived only from canonical table destinations. Legacy
    staging files and unselected content-addressed partitions are recognized;
    selected files, unknown names, links, open files, and files inside the
    grace period are never removed.
    """
    if grace_period_s < 0:
        raise ValueError("machine staging grace period must be non-negative")
    current_time = time.time() if now is None else now
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for table in MACHINE_TABLES:
        serving = canonical_machine_table_path(table)
        parent = serving.parent
        partition_dir = parent / "machine-partitions" / table
        selected = set(machine_table_files(serving, load_machine_manifest(serving)))
        candidates = (
            serving.with_name(f"{serving.name}.tmp"),
            *parent.glob(f".{serving.name}.*.tmp"),
            *(partition_dir.glob("*") if partition_dir.is_dir() else ()),
        )
        for candidate in sorted(candidates):
            if candidate in seen or candidate in selected or not candidate.exists():
                continue
            seen.add(candidate)
            expected_legacy = candidate.name == f"{serving.name}.tmp"
            unique_match = _UNIQUE_STAGING.fullmatch(candidate.name)
            expected_unique = (
                unique_match is not None and unique_match.group("name") == serving.name
            )
            expected_partition = candidate.parent == partition_dir and bool(
                _PARTITION_FILE.fullmatch(candidate.name)
            )
            expected_partition_tmp = candidate.parent == partition_dir and bool(
                _PARTITION_STAGING.fullmatch(candidate.name)
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
                else "partition"
                if expected_partition
                else "partition-temp"
                if expected_partition_tmp
                else "unknown",
            }
            if (
                not (expected_legacy or expected_unique or expected_partition or expected_partition_tmp)
                or stat.S_ISLNK(before.st_mode)
                or not stat.S_ISREG(before.st_mode)
            ):
                entries.append({**base, "disposition": "unsafe"})
                continue
            identity = (before.st_dev, before.st_ino)
            if _path_is_open(candidate):
                entries.append({**base, "disposition": "active"})
                continue
            if base["age_seconds"] < max(grace_period_s, 24 * 60 * 60 if expected_partition else 0):
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
                if expected_partition and candidate in machine_table_files(
                    serving, load_machine_manifest(serving)
                ):
                    entries.append({**base, "disposition": "selected"})
                    continue
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
    created: set[Path] = set()
    try:
        with _raise_on_termination():
            return _materialize_machine_telemetry(start=start, end=end, created=created)
    except BaseException:
        manifest_path = canonical_machine_table_path("manifest").with_suffix(".json")
        selected = load_machine_manifest(manifest_path.with_name("metric_sample.ndjson"))
        referenced = {
            path
            for name in MACHINE_TABLES
            for path in machine_table_files(canonical_machine_table_path(name), selected)
        }
        for path in created - referenced:
            path.unlink(missing_ok=True)
        raise


@contextmanager
def _raise_on_termination() -> Iterable[None]:
    if current_thread() is not main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(_number: int, _frame: Any) -> None:
        raise SystemExit(128 + signal.SIGTERM)

    signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _materialize_machine_telemetry(
    *, start: date | None, end: date | None, created: set[Path]
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
    if start is None and any(canonical_machine_table_path(name).is_file() for name in MACHINE_TABLES):
        raise MaterializationError(
            "machine_materialize",
            reason="full rebuild over a legacy monolith requires a separately verified migration; use a bounded window",
        )
    stage_started = time.monotonic()
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
            created=created,
        ),
        "gpu_sample": _materialize_table(
            "gpu_sample",
            lambda: gpu_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "network_sample": _materialize_table(
            "network_sample",
            lambda: network_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "service_state": _materialize_table(
            "service_state",
            lambda: service_states(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "block_device_sample": _materialize_table(
            "block_device_sample",
            lambda: block_device_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "service_cgroup_io_sample": _materialize_table(
            "service_cgroup_io_sample",
            lambda: service_cgroup_io_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "service_cgroup_pressure_sample": _materialize_table(
            "service_cgroup_pressure_sample",
            lambda: service_cgroup_pressure_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "process_io_delta_sample": _materialize_table(
            "process_io_delta_sample",
            lambda: process_io_delta_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "process_memory_sample": _materialize_table(
            "process_memory_sample",
            lambda: process_memory_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "cgroup_memory_sample": _materialize_table(
            "cgroup_memory_sample",
            lambda: cgroup_memory_samples(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
        ),
        "kill_event": _materialize_table(
            "kill_event",
            lambda: kill_events(
                start=start, end=source_end, path=cfg.machine_telemetry_db
            ),
            start=start,
            end=end,
            created=created,
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
        "row_count": sum(int(report["row_count"]) for report in reports.values())
        if all(report["row_count"] is not None for report in reports.values())
        else None,
        "window_output_bytes": sum(int(report.get("window_output_bytes", 0)) for report in reports.values()),
        "stage_elapsed_s": round(time.monotonic() - stage_started, 3),
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
    created: set[Path] | None = None,
) -> dict[str, Any]:
    output = canonical_machine_table_path(name)
    output.parent.mkdir(parents=True, exist_ok=True)
    prior = load_machine_manifest(output)
    tables = prior.get("tables")
    old = tables.get(name) if isinstance(tables, dict) else None
    old_table = old if isinstance(old, dict) else {}
    if start is None:
        base_path = None
        parts: dict[str, dict[str, Any]] = {}
        covered: set[date] = set()
    else:
        base_path = old_table.get("base_path") if prior.get("schema_version") == 2 else None
        if prior.get("schema_version") != 2 and base_path is None and output.is_file():
            base_path = output.name
        old_parts = old_table.get("partitions") if prior.get("schema_version") == 2 else None
        parts = dict(old_parts) if isinstance(old_parts, dict) else {}
        covered = {
            date.fromisoformat(str(raw))
            for raw in old_table.get("covered_dates", [])
            if isinstance(raw, str)
        }
        covered = {day for day in covered if not (start <= day < end)}
        covered.update(start + timedelta(days=offset) for offset in range((end - start).days))
    emitted: set[date] = set()
    rows = (sample_to_json(sample) for sample in rows_fn())
    previous_day: date | None = None
    for day_string, group in groupby(rows, key=lambda row: _row_date(row).isoformat()):
        day = date.fromisoformat(day_string)
        if previous_day is not None and day <= previous_day:
            raise MaterializationError("machine_materialize", reason=f"{name} rows are not date ordered")
        if start is not None and not (start <= day < end):
            raise MaterializationError("machine_materialize", reason=f"{name} emitted a row outside its window")
        part, path, was_created = _write_partition(output, day, group)
        parts[day_string] = part
        if was_created and created is not None:
            created.add(path)
        emitted.add(day)
        covered.add(day)
        previous_day = day
    if start is not None:
        for offset in range((end - start).days):
            day = start + timedelta(days=offset)
            if day not in emitted:
                parts[day.isoformat()] = {"path": None, "row_count": 0, "size_bytes": 0}
    covered_dates = tuple(sorted(covered))
    if base_path is None:
        row_count: int | None = sum(int(part["row_count"]) for part in parts.values())
    elif not parts:
        row_count = old_table.get("row_count")
    else:
        row_count = None
    observed = [day for day, part in parts.items() if int(part["row_count"]) > 0]
    return {
        "path": str(output),
        "base_path": base_path,
        "partitions": parts,
        "row_count": row_count,
        "first_date": covered_dates[0].isoformat() if covered_dates else None,
        "last_date": covered_dates[-1].isoformat() if covered_dates else None,
        "first_timestamp_date": min(observed) if observed and base_path is None else None,
        "last_timestamp_date": max(observed) if observed and base_path is None else None,
        "covered_dates": [day.isoformat() for day in covered_dates],
        "covered_date_count": len(covered_dates),
        "window_output_bytes": sum(
            int(parts[day.isoformat()]["size_bytes"]) for day in emitted
        ),
    }


def _write_partition(
    output: Path, day: date, rows: Iterable[MachineRow]
) -> tuple[dict[str, Any], Path, bool]:
    directory = output.parent / "machine-partitions" / output.stem
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    size = 0
    anonymous = getattr(os, "O_TMPFILE", None)
    if anonymous is None:
        raise MaterializationError("machine_materialize", reason="anonymous partition staging is unavailable")
    # Convergence runs this writer on a worker thread, where Python cannot
    # handle SIGTERM. An unnamed inode is released by the kernel on exit.
    fd = os.open(directory, anonymous | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        for row in rows:
            encoded = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            handle.write(encoded)
            digest.update(encoded)
            count += 1
            size += len(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        destination = directory / f"{day.isoformat()}.{digest.hexdigest()}.ndjson"
        created = False
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            linkat = ctypes.CDLL(None, use_errno=True).linkat
            linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
            linkat.restype = ctypes.c_int
            # AT_EMPTY_PATH publishes the fsynced anonymous inode by content hash.
            result = linkat(handle.fileno(), b"", directory_fd, destination.name.encode(), 0x1000)
            if result == 0:
                created = True
            elif ctypes.get_errno() != errno.EEXIST:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(destination))
            elif destination.stat().st_size != size:
                raise MaterializationError("machine_materialize", reason=f"partition hash collision: {destination}")
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            "path": str(destination.relative_to(output.parent)),
            "row_count": count,
            "size_bytes": size,
        }, destination, created


def _row_timestamp(row: MachineRow) -> datetime:
    return datetime.fromisoformat(str(row["observed_at"]))


def _row_date(row: MachineRow) -> date:
    return _row_timestamp(row).date()


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
