"""Sinex xtask history source: timed development work invocations."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from lynchpin.core.classify import resolve_project
from lynchpin.core.config import get_config

_DEFAULT_ARCHIVE_PATHS = (
    Path(
        "/realm/recovery/borg-rotation-rescue-20260523/unrecovered-all/"
        "realm/project/sinex/.sinex/state/xtask-history.db"
    ),
)


@dataclass(frozen=True)
class XtaskInvocation:
    source_id: str
    command: tuple[str, ...]
    cwd: str
    started_at: datetime
    ended_at: datetime | None
    duration_s: float | None
    status: str
    exit_code: int | None
    host: str
    project: str | None
    git_commit: str | None
    git_dirty: bool
    live_stage: str | None
    args_json: str
    cpu_usage_avg: float | None
    memory_usage_max_mb: float | None
    process_cpu_usage_avg: float | None
    process_memory_usage_max_mb: float | None
    root_process_cpu_usage_avg: float | None
    root_process_memory_usage_max_mb: float | None
    shared_nix_daemon_cpu_usage_avg: float | None
    shared_nix_daemon_memory_usage_max_mb: float | None
    shared_nix_build_slice_cpu_usage_avg: float | None
    shared_nix_build_slice_memory_usage_max_mb: float | None
    shared_background_slice_cpu_usage_avg: float | None
    shared_background_slice_memory_usage_max_mb: float | None
    host_cpu_pressure_some_avg10_max: float | None
    host_io_pressure_some_avg10_max: float | None
    host_io_pressure_full_avg10_max: float | None
    host_memory_pressure_some_avg10_max: float | None
    host_memory_pressure_full_avg10_max: float | None
    shm_free_min_mb: float | None
    shm_used_max_mb: float | None
    process_count_max: int | None
    resource_sample_count: int | None


def xtask_history_path(path: Path | None = None) -> Path:
    return path or get_config().xtask_history_db


def xtask_history_archive_paths() -> tuple[Path, ...]:
    raw = os.environ.get("LYNCHPIN_XTASK_HISTORY_ARCHIVE_DBS")
    if raw is not None:
        return tuple(Path(item).expanduser() for item in raw.split(":") if item)
    return tuple(path for path in _DEFAULT_ARCHIVE_PATHS if path.exists())


def xtask_history_paths() -> tuple[tuple[str, Path], ...]:
    """Return labeled xtask ledgers, oldest recovered ledgers first."""
    result: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for idx, path in enumerate(xtask_history_archive_paths(), start=1):
        resolved = path.resolve()
        if resolved not in seen and path.exists():
            result.append((f"archive{idx}", path))
            seen.add(resolved)
    live_path = xtask_history_path()
    resolved_live = live_path.resolve()
    if resolved_live not in seen:
        result.append(("live", live_path))
    return tuple(result)


def iter_all_invocations(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskInvocation]:
    for label, path in xtask_history_paths():
        if not path.exists():
            continue
        yield from iter_invocations(
            path=path,
            start=start,
            end=end,
            source_prefix=f"xtask:{label}",
        )


def iter_invocations(
    *,
    path: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source_prefix: str = "xtask",
) -> Iterator[XtaskInvocation]:
    target = xtask_history_path(path)
    if not target.exists():
        raise FileNotFoundError(f"xtask history database not found: {target}")

    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append("started_at >= ?")
        params.append(start.isoformat())
    if end is not None:
        clauses.append("started_at < ?")
        params.append(end.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    sql = f"""
        SELECT
            id, command, subcommand, profile, args_json, git_commit, git_dirty,
            started_at, finished_at, duration_secs, exit_code, status, host, cwd,
            live_stage, cpu_usage_avg, memory_usage_max_mb,
            process_cpu_usage_avg, process_memory_usage_max_mb,
            root_process_cpu_usage_avg, root_process_memory_usage_max_mb,
            shared_nix_daemon_cpu_usage_avg, shared_nix_daemon_memory_usage_max_mb,
            shared_nix_build_slice_cpu_usage_avg, shared_nix_build_slice_memory_usage_max_mb,
            shared_background_slice_cpu_usage_avg, shared_background_slice_memory_usage_max_mb,
            host_cpu_pressure_some_avg10_max, host_io_pressure_some_avg10_max,
            host_io_pressure_full_avg10_max, host_memory_pressure_some_avg10_max,
            host_memory_pressure_full_avg10_max, shm_free_min_mb, shm_used_max_mb,
            process_count_max, resource_sample_count
        FROM invocations
        {where}
        ORDER BY started_at, id
    """
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        for row in conn.execute(sql, params):
            yield _row_to_invocation(row, source_prefix=source_prefix)
    finally:
        conn.close()


def _row_to_invocation(row: tuple[Any, ...], *, source_prefix: str) -> XtaskInvocation:
    command_parts = tuple(
        part for part in (row[1], row[2], row[3]) if part is not None and str(part)
    )
    args_json = row[4] if isinstance(row[4], str) and row[4].strip() else "[]"
    try:
        json.loads(args_json)
    except json.JSONDecodeError:
        args_json = json.dumps([args_json])
    cwd = str(row[13] or "")
    return XtaskInvocation(
        source_id=f"{source_prefix}:{row[0]}",
        command=tuple(str(part) for part in command_parts),
        cwd=cwd,
        started_at=_parse_dt(row[7]),
        ended_at=_parse_optional_dt(row[8]),
        duration_s=_float(row[9]),
        exit_code=_int(row[10]),
        status=str(row[11] or "unknown"),
        host=str(row[12] or ""),
        project=resolve_project(cwd, row[1], row[2], row[3]),
        git_commit=str(row[5]) if row[5] else None,
        git_dirty=bool(row[6]),
        live_stage=str(row[14]) if row[14] else None,
        args_json=args_json,
        cpu_usage_avg=_float(row[15]),
        memory_usage_max_mb=_float(row[16]),
        process_cpu_usage_avg=_float(row[17]),
        process_memory_usage_max_mb=_float(row[18]),
        root_process_cpu_usage_avg=_float(row[19]),
        root_process_memory_usage_max_mb=_float(row[20]),
        shared_nix_daemon_cpu_usage_avg=_float(row[21]),
        shared_nix_daemon_memory_usage_max_mb=_float(row[22]),
        shared_nix_build_slice_cpu_usage_avg=_float(row[23]),
        shared_nix_build_slice_memory_usage_max_mb=_float(row[24]),
        shared_background_slice_cpu_usage_avg=_float(row[25]),
        shared_background_slice_memory_usage_max_mb=_float(row[26]),
        host_cpu_pressure_some_avg10_max=_float(row[27]),
        host_io_pressure_some_avg10_max=_float(row[28]),
        host_io_pressure_full_avg10_max=_float(row[29]),
        host_memory_pressure_some_avg10_max=_float(row[30]),
        host_memory_pressure_full_avg10_max=_float(row[31]),
        shm_free_min_mb=_float(row[32]),
        shm_used_max_mb=_float(row[33]),
        process_count_max=_int(row[34]),
        resource_sample_count=_int(row[35]),
    )


def _parse_dt(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_optional_dt(value: Any) -> datetime | None:
    return _parse_dt(value) if value else None


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


__all__ = [
    "XtaskInvocation",
    "iter_all_invocations",
    "iter_invocations",
    "xtask_history_archive_paths",
    "xtask_history_path",
    "xtask_history_paths",
]
