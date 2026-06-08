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
    host_block_read_mib_delta: float | None = None
    host_block_write_mib_delta: float | None = None
    host_block_read_iops_avg: float | None = None
    host_block_write_iops_avg: float | None = None
    host_block_busiest_device: str | None = None
    host_block_busiest_device_total_mib_delta: float | None = None
    host_block_busiest_device_read_iops_avg: float | None = None
    host_block_busiest_device_write_iops_avg: float | None = None
    host_block_busiest_device_weighted_io_ms_per_s: float | None = None


@dataclass(frozen=True)
class XtaskStageTiming:
    source_id: str
    invocation_source_id: str
    stage_name: str
    started_at: datetime
    duration_s: float | None
    success: bool | None
    # End-of-stage PSI (pressure-stall) snapshot, avg10 (10s decaying average).
    # None when /proc/pressure was unavailable or the DB predates PSI columns.
    io_full_avg10: float | None
    cpu_some_avg10: float | None
    memory_some_avg10: float | None


@dataclass(frozen=True)
class XtaskTestResult:
    source_id: str
    invocation_source_id: str
    test_name: str
    package: str | None
    status: str
    duration_s: float | None
    attempt: int | None
    slot_name: str | None
    slot_wait_ms: int | None
    cleanup_ms: int | None
    failure_type: str | None
    test_mode: str | None
    nats_context: str | None


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


def iter_all_stage_timings(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskStageTiming]:
    for label, path in xtask_history_paths():
        if not path.exists():
            continue
        yield from iter_stage_timings(
            path=path,
            start=start,
            end=end,
            source_prefix=f"xtask:{label}",
        )


def iter_all_test_results(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskTestResult]:
    for label, path in xtask_history_paths():
        if not path.exists():
            continue
        yield from iter_test_results(
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

    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = _table_columns(conn, "invocations")
        sql = f"""
            SELECT {_select_list(columns, _INVOCATION_COLUMNS)}
            FROM invocations
            {where}
            ORDER BY started_at, id
        """
        for row in conn.execute(sql, params):
            yield _row_to_invocation(row, source_prefix=source_prefix)
    finally:
        conn.close()


def iter_stage_timings(
    *,
    path: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source_prefix: str = "xtask",
) -> Iterator[XtaskStageTiming]:
    target = xtask_history_path(path)
    if not target.exists():
        raise FileNotFoundError(f"xtask history database not found: {target}")
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "stage_timings"):
            return
        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("started_at >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("started_at < ?")
            params.append(end.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        columns = _table_columns(conn, "stage_timings")
        sql = f"""
            SELECT {_select_list(columns, _STAGE_COLUMNS)}
            FROM stage_timings
            {where}
            ORDER BY started_at, id
        """
        for row in conn.execute(sql, params):
            yield _row_to_stage_timing(row, source_prefix=source_prefix)
    finally:
        conn.close()


def iter_test_results(
    *,
    path: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source_prefix: str = "xtask",
) -> Iterator[XtaskTestResult]:
    target = xtask_history_path(path)
    if not target.exists():
        raise FileNotFoundError(f"xtask history database not found: {target}")
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "test_results"):
            return
        clauses: list[str] = []
        params: list[Any] = []
        if start is not None:
            clauses.append("i.started_at >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("i.started_at < ?")
            params.append(end.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        columns = _table_columns(conn, "test_results")
        exprs = _select_list(columns, _TEST_RESULT_COLUMNS, prefix="tr")
        sql = f"""
            SELECT {exprs}, i.started_at AS invocation_started_at
            FROM test_results tr
            LEFT JOIN invocations i ON i.id = tr.invocation_id
            {where}
            ORDER BY COALESCE(i.started_at, ''), tr.id
        """
        for row in conn.execute(sql, params):
            yield _row_to_test_result(row, source_prefix=source_prefix)
    finally:
        conn.close()


def _row_to_invocation(row: sqlite3.Row, *, source_prefix: str) -> XtaskInvocation:
    command_parts = tuple(
        part
        for part in (row["command"], row["subcommand"], row["profile"])
        if part is not None and str(part)
    )
    args_json = (
        row["args_json"]
        if isinstance(row["args_json"], str) and row["args_json"].strip()
        else "[]"
    )
    try:
        json.loads(args_json)
    except json.JSONDecodeError:
        args_json = json.dumps([args_json])
    cwd = str(row["cwd"] or "")
    return XtaskInvocation(
        source_id=f"{source_prefix}:{row['id']}",
        command=tuple(str(part) for part in command_parts),
        cwd=cwd,
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_optional_dt(row["finished_at"]),
        duration_s=_float(row["duration_secs"]),
        exit_code=_int(row["exit_code"]),
        status=str(row["status"] or "unknown"),
        host=str(row["host"] or ""),
        project=resolve_project(cwd, row["command"], row["subcommand"], row["profile"]),
        git_commit=str(row["git_commit"]) if row["git_commit"] else None,
        git_dirty=bool(row["git_dirty"]),
        live_stage=str(row["live_stage"]) if row["live_stage"] else None,
        args_json=args_json,
        cpu_usage_avg=_float(row["cpu_usage_avg"]),
        memory_usage_max_mb=_float(row["memory_usage_max_mb"]),
        process_cpu_usage_avg=_float(row["process_cpu_usage_avg"]),
        process_memory_usage_max_mb=_float(row["process_memory_usage_max_mb"]),
        root_process_cpu_usage_avg=_float(row["root_process_cpu_usage_avg"]),
        root_process_memory_usage_max_mb=_float(
            row["root_process_memory_usage_max_mb"]
        ),
        shared_nix_daemon_cpu_usage_avg=_float(row["shared_nix_daemon_cpu_usage_avg"]),
        shared_nix_daemon_memory_usage_max_mb=_float(
            row["shared_nix_daemon_memory_usage_max_mb"]
        ),
        shared_nix_build_slice_cpu_usage_avg=_float(
            row["shared_nix_build_slice_cpu_usage_avg"]
        ),
        shared_nix_build_slice_memory_usage_max_mb=_float(
            row["shared_nix_build_slice_memory_usage_max_mb"]
        ),
        shared_background_slice_cpu_usage_avg=_float(
            row["shared_background_slice_cpu_usage_avg"]
        ),
        shared_background_slice_memory_usage_max_mb=_float(
            row["shared_background_slice_memory_usage_max_mb"]
        ),
        host_cpu_pressure_some_avg10_max=_float(
            row["host_cpu_pressure_some_avg10_max"]
        ),
        host_io_pressure_some_avg10_max=_float(row["host_io_pressure_some_avg10_max"]),
        host_io_pressure_full_avg10_max=_float(row["host_io_pressure_full_avg10_max"]),
        host_memory_pressure_some_avg10_max=_float(
            row["host_memory_pressure_some_avg10_max"]
        ),
        host_memory_pressure_full_avg10_max=_float(
            row["host_memory_pressure_full_avg10_max"]
        ),
        shm_free_min_mb=_float(row["shm_free_min_mb"]),
        shm_used_max_mb=_float(row["shm_used_max_mb"]),
        process_count_max=_int(row["process_count_max"]),
        resource_sample_count=_int(row["resource_sample_count"]),
        host_block_read_mib_delta=_float(row["host_block_read_mib_delta"]),
        host_block_write_mib_delta=_float(row["host_block_write_mib_delta"]),
        host_block_read_iops_avg=_float(row["host_block_read_iops_avg"]),
        host_block_write_iops_avg=_float(row["host_block_write_iops_avg"]),
        host_block_busiest_device=str(row["host_block_busiest_device"])
        if row["host_block_busiest_device"]
        else None,
        host_block_busiest_device_total_mib_delta=_float(
            row["host_block_busiest_device_total_mib_delta"]
        ),
        host_block_busiest_device_read_iops_avg=_float(
            row["host_block_busiest_device_read_iops_avg"]
        ),
        host_block_busiest_device_write_iops_avg=_float(
            row["host_block_busiest_device_write_iops_avg"]
        ),
        host_block_busiest_device_weighted_io_ms_per_s=_float(
            row["host_block_busiest_device_weighted_io_ms_per_s"]
        ),
    )


def _row_to_stage_timing(row: sqlite3.Row, *, source_prefix: str) -> XtaskStageTiming:
    return XtaskStageTiming(
        source_id=f"{source_prefix}:stage:{row['id']}",
        invocation_source_id=f"{source_prefix}:{row['invocation_id']}",
        stage_name=str(row["stage_name"] or "unknown"),
        started_at=_parse_dt(row["started_at"]),
        duration_s=_float(row["duration_secs"]),
        success=_bool(row["success"]),
        io_full_avg10=_float(row["io_full_avg10"]),
        cpu_some_avg10=_float(row["cpu_some_avg10"]),
        memory_some_avg10=_float(row["memory_some_avg10"]),
    )


def _row_to_test_result(row: sqlite3.Row, *, source_prefix: str) -> XtaskTestResult:
    return XtaskTestResult(
        source_id=f"{source_prefix}:test:{row['id']}",
        invocation_source_id=f"{source_prefix}:{row['invocation_id']}",
        test_name=str(row["test_name"] or ""),
        package=str(row["package"]) if row["package"] else None,
        status=str(row["status"] or "unknown"),
        duration_s=_float(row["duration_secs"]),
        attempt=_int(row["attempt"]),
        slot_name=str(row["slot_name"]) if row["slot_name"] else None,
        slot_wait_ms=_int(row["slot_wait_ms"]),
        cleanup_ms=_int(row["cleanup_ms"]),
        failure_type=str(row["failure_type"]) if row["failure_type"] else None,
        test_mode=str(row["test_mode"]) if row["test_mode"] else None,
        nats_context=str(row["nats_context"]) if row["nats_context"] else None,
    )


_INVOCATION_COLUMNS = (
    "id",
    "command",
    "subcommand",
    "profile",
    "args_json",
    "git_commit",
    "git_dirty",
    "started_at",
    "finished_at",
    "duration_secs",
    "exit_code",
    "status",
    "host",
    "cwd",
    "live_stage",
    "cpu_usage_avg",
    "memory_usage_max_mb",
    "process_cpu_usage_avg",
    "process_memory_usage_max_mb",
    "root_process_cpu_usage_avg",
    "root_process_memory_usage_max_mb",
    "shared_nix_daemon_cpu_usage_avg",
    "shared_nix_daemon_memory_usage_max_mb",
    "shared_nix_build_slice_cpu_usage_avg",
    "shared_nix_build_slice_memory_usage_max_mb",
    "shared_background_slice_cpu_usage_avg",
    "shared_background_slice_memory_usage_max_mb",
    "host_cpu_pressure_some_avg10_max",
    "host_io_pressure_some_avg10_max",
    "host_io_pressure_full_avg10_max",
    "host_memory_pressure_some_avg10_max",
    "host_memory_pressure_full_avg10_max",
    "shm_free_min_mb",
    "shm_used_max_mb",
    "process_count_max",
    "resource_sample_count",
    "host_block_read_mib_delta",
    "host_block_write_mib_delta",
    "host_block_read_iops_avg",
    "host_block_write_iops_avg",
    "host_block_busiest_device",
    "host_block_busiest_device_total_mib_delta",
    "host_block_busiest_device_read_iops_avg",
    "host_block_busiest_device_write_iops_avg",
    "host_block_busiest_device_weighted_io_ms_per_s",
)

_STAGE_COLUMNS = (
    "id",
    "invocation_id",
    "stage_name",
    "started_at",
    "duration_secs",
    "success",
    "io_full_avg10",
    "cpu_some_avg10",
    "memory_some_avg10",
)

_TEST_RESULT_COLUMNS = (
    "id",
    "invocation_id",
    "test_name",
    "package",
    "status",
    "duration_secs",
    "attempt",
    "slot_name",
    "slot_wait_ms",
    "cleanup_ms",
    "failure_type",
    "test_mode",
    "nats_context",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            [table],
        ).fetchone()
        is not None
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _select_list(
    available: set[str],
    wanted: tuple[str, ...],
    *,
    prefix: str | None = None,
) -> str:
    qualified = f"{prefix}." if prefix else ""
    return ", ".join(
        f"{qualified}{name} AS {name}" if name in available else f"NULL AS {name}"
        for name in wanted
    )


def _parse_dt(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_optional_dt(value: Any) -> datetime | None:
    return _parse_dt(value) if value else None


def _float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _bool(value: Any) -> bool | None:
    return bool(value) if value is not None else None


__all__ = [
    "XtaskInvocation",
    "XtaskStageTiming",
    "XtaskTestResult",
    "iter_all_invocations",
    "iter_all_stage_timings",
    "iter_all_test_results",
    "iter_invocations",
    "iter_stage_timings",
    "iter_test_results",
    "xtask_history_archive_paths",
    "xtask_history_path",
    "xtask_history_paths",
]
