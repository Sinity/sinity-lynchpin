"""Development run history: xtask checks and tests recorded by devkit.

Devkit writes every project's runs (`cargo xtask check`, `test`, `compile` ...)
into one store, `~/.local/state/devkit/history.db`, tables `runs`, `stages` and
`tests`. Rows carry their project and checkout. Devkit records no per-run
resource or pressure samples, so those fields are always None here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from lynchpin.core.config import get_config

_SETTLED_STATUSES = frozenset({"success", "failed", "cancelled"})
#: devkit status -> the status vocabulary Lynchpin consumers use.
_STATUS = {"passed": "success", "failed": "failed", "running": "running"}


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
    git_dirty: bool | None
    live_stage: str | None
    args_json: str
    cpu_usage_avg: float | None = None
    memory_usage_max_mb: float | None = None
    process_cpu_usage_avg: float | None = None
    process_memory_usage_max_mb: float | None = None
    root_process_cpu_usage_avg: float | None = None
    root_process_memory_usage_max_mb: float | None = None
    shared_nix_daemon_cpu_usage_avg: float | None = None
    shared_nix_daemon_memory_usage_max_mb: float | None = None
    shared_nix_build_slice_cpu_usage_avg: float | None = None
    shared_nix_build_slice_memory_usage_max_mb: float | None = None
    shared_background_slice_cpu_usage_avg: float | None = None
    shared_background_slice_memory_usage_max_mb: float | None = None
    host_cpu_pressure_some_avg10_max: float | None = None
    host_io_pressure_some_avg10_max: float | None = None
    host_io_pressure_full_avg10_max: float | None = None
    host_memory_pressure_some_avg10_max: float | None = None
    host_memory_pressure_full_avg10_max: float | None = None
    shm_free_min_mb: float | None = None
    shm_used_max_mb: float | None = None
    process_count_max: int | None = None
    resource_sample_count: int | None = None
    host_block_read_mib_delta: float | None = None
    host_block_write_mib_delta: float | None = None
    host_block_read_iops_avg: float | None = None
    host_block_write_iops_avg: float | None = None
    host_block_busiest_device: str | None = None
    host_block_busiest_device_total_mib_delta: float | None = None
    host_block_busiest_device_read_iops_avg: float | None = None
    host_block_busiest_device_write_iops_avg: float | None = None
    host_block_busiest_device_weighted_io_ms_per_s: float | None = None
    #: Absolute root of the checkout or worktree that ran the command, and its
    #: directory name. None on rows recorded before the ledger was unified whose
    #: cwd matched no known workspace root.
    workspace_root: str | None = None
    workspace_name: str | None = None
    #: Branch checked out in that workspace, or None on a detached HEAD.
    git_branch: str | None = None

    @property
    def operation(self) -> str | None:
        """The xtask verb (``check``, ``test nextest ci``), the tier of the run."""
        return " ".join(self.command) or None

    @property
    def outcome_known(self) -> bool | None:
        """True once the invocation settled; a running row has no outcome yet."""
        return True if self.status in _SETTLED_STATUSES else None


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
    io_full_avg10: float | None = None
    cpu_some_avg10: float | None = None
    memory_some_avg10: float | None = None


@dataclass(frozen=True)
class XtaskTestResult:
    source_id: str
    invocation_source_id: str
    test_name: str
    package: str | None
    status: str
    duration_s: float | None
    attempt: int | None = None
    slot_name: str | None = None
    slot_wait_ms: int | None = None
    cleanup_ms: int | None = None
    failure_type: str | None = None
    test_mode: str | None = None
    nats_context: str | None = None


def xtask_history_path(path: Path | None = None) -> Path:
    return path or get_config().xtask_history_db


def iter_all_invocations(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskInvocation]:
    path = xtask_history_path()
    if not path.exists():
        return
    yield from iter_invocations(path=path, start=start, end=end)


def iter_all_stage_timings(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskStageTiming]:
    path = xtask_history_path()
    if not path.exists():
        return
    yield from iter_stage_timings(path=path, start=start, end=end)


def iter_all_test_results(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[XtaskTestResult]:
    path = xtask_history_path()
    if not path.exists():
        return
    yield from iter_test_results(path=path, start=start, end=end)


def _window(column: str, start: datetime | None, end: datetime | None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if start is not None:
        clauses.append(f"{column} >= ?")
        params.append(_ms(start))
    if end is not None:
        clauses.append(f"{column} < ?")
        params.append(_ms(end))
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _connect(path: Path | None) -> sqlite3.Connection:
    target = xtask_history_path(path)
    if not target.exists():
        raise FileNotFoundError(f"devkit run history not found: {target}")
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def iter_invocations(
    *,
    path: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source_prefix: str = "xtask",
) -> Iterator[XtaskInvocation]:
    where, params = _window("started_ms", start, end)
    conn = _connect(path)
    try:
        sql = f"""
            SELECT id, project, checkout, command, args, head, dirty, host,
                   started_ms, duration_ms, status
            FROM runs {where}
            ORDER BY started_ms, id
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
    where, params = _window("r.started_ms", start, end)
    conn = _connect(path)
    try:
        # Stages record durations only; each starts where the previous one ended.
        sql = f"""
            SELECT s.run, s.position, s.name, s.duration_ms, s.ok, r.started_ms,
                   SUM(s.duration_ms) OVER (
                     PARTITION BY s.run ORDER BY s.position
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS offset_ms
            FROM stages s JOIN runs r ON r.id = s.run
            {where}
            ORDER BY r.started_ms, s.run, s.position
        """
        for row in conn.execute(sql, params):
            yield XtaskStageTiming(
                source_id=f"{source_prefix}:stage:{row['run']}:{row['position']}",
                invocation_source_id=f"{source_prefix}:{row['run']}",
                stage_name=str(row["name"]),
                started_at=_from_ms(row["started_ms"] + (row["offset_ms"] or 0)),
                duration_s=row["duration_ms"] / 1000.0,
                success=bool(row["ok"]),
            )
    finally:
        conn.close()


def iter_test_results(
    *,
    path: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    source_prefix: str = "xtask",
) -> Iterator[XtaskTestResult]:
    where, params = _window("r.started_ms", start, end)
    conn = _connect(path)
    try:
        sql = f"""
            SELECT t.rowid AS id, t.run, t.binary, t.name, t.status, t.duration_ms
            FROM tests t JOIN runs r ON r.id = t.run
            {where}
            ORDER BY r.started_ms, t.rowid
        """
        for row in conn.execute(sql, params):
            yield XtaskTestResult(
                source_id=f"{source_prefix}:test:{row['id']}",
                invocation_source_id=f"{source_prefix}:{row['run']}",
                test_name=str(row["name"]),
                package=str(row["binary"]) if row["binary"] else None,
                status=str(row["status"]),
                duration_s=row["duration_ms"] / 1000.0,
            )
    finally:
        conn.close()


def _row_to_invocation(row: sqlite3.Row, *, source_prefix: str) -> XtaskInvocation:
    started = _from_ms(row["started_ms"])
    duration = row["duration_ms"] / 1000.0 if row["duration_ms"] is not None else None
    args = str(row["args"] or "").split()
    checkout = str(row["checkout"])
    return XtaskInvocation(
        source_id=f"{source_prefix}:{row['id']}",
        command=(str(row["command"]), *args),
        cwd=checkout,
        started_at=started,
        ended_at=_from_ms(row["started_ms"] + row["duration_ms"]) if duration is not None else None,
        duration_s=duration,
        exit_code=None,
        status=_STATUS.get(str(row["status"]), str(row["status"])),
        host=str(row["host"] or ""),
        project=str(row["project"]),
        git_commit=str(row["head"]) if row["head"] else None,
        git_dirty=bool(row["dirty"]) if row["dirty"] is not None else None,
        live_stage=None,
        args_json="[]",
        workspace_root=checkout,
        workspace_name=Path(checkout).name,
    )


def _ms(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, timezone.utc)


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
    "xtask_history_path",
]
