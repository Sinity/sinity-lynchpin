"""Work-observation table readers and promoters."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb


_WORK_OBSERVATION_COLUMNS = (
    "source",
    "source_id",
    "work_kind",
    "project",
    "command",
    "cwd",
    "started_at",
    "ended_at",
    "duration_s",
    "status",
    "exit_code",
    "host",
    "git_commit",
    "git_dirty",
    "live_stage",
    "args",
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
    "host_block_read_mib_delta",
    "host_block_write_mib_delta",
    "host_block_read_iops_avg",
    "host_block_write_iops_avg",
    "host_block_busiest_device",
    "host_block_busiest_device_total_mib_delta",
    "host_block_busiest_device_read_iops_avg",
    "host_block_busiest_device_write_iops_avg",
    "host_block_busiest_device_weighted_io_ms_per_s",
    "shm_free_min_mb",
    "shm_used_max_mb",
    "process_count_max",
    "resource_sample_count",
)

_WORK_OBSERVATION_STAGE_COLUMNS = (
    "source",
    "source_id",
    "invocation_source_id",
    "stage_name",
    "started_at",
    "duration_s",
    "success",
)

_WORK_OBSERVATION_TEST_RESULT_COLUMNS = (
    "source",
    "source_id",
    "invocation_source_id",
    "test_name",
    "package",
    "status",
    "duration_s",
    "attempt",
    "slot_name",
    "slot_wait_ms",
    "cleanup_ms",
    "failure_type",
    "test_mode",
    "nats_context",
)


def promote_work_observations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    delete_existing: bool = True,
) -> int:
    return _promote_work_observation_rows(
        conn,
        refresh_id=refresh_id,
        rows=rows,
        source="xtask_history",
        work_kind="xtask_invocation",
        delete_existing=delete_existing,
    )


def promote_polylogue_devtools_observations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    delete_existing: bool = True,
) -> int:
    return _promote_work_observation_rows(
        conn,
        refresh_id=refresh_id,
        rows=rows,
        source=None,
        work_kind=None,
        delete_existing=delete_existing,
    )


def _promote_work_observation_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
    source: str | None,
    work_kind: str | None,
    delete_existing: bool = True,
) -> int:
    return promote_rows(
        conn,
        table="work_observation",
        columns=_WORK_OBSERVATION_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        delete_existing=delete_existing,
        extractor=lambda r: (
            source or r.source,
            r.source_id,
            work_kind or r.work_kind,
            r.project,
            list(r.command),
            r.cwd,
            r.started_at,
            r.ended_at,
            r.duration_s,
            r.status,
            r.exit_code,
            r.host,
            r.git_commit,
            r.git_dirty,
            r.live_stage,
            r.args_json,
            r.cpu_usage_avg,
            r.memory_usage_max_mb,
            r.process_cpu_usage_avg,
            r.process_memory_usage_max_mb,
            r.root_process_cpu_usage_avg,
            r.root_process_memory_usage_max_mb,
            r.shared_nix_daemon_cpu_usage_avg,
            r.shared_nix_daemon_memory_usage_max_mb,
            r.shared_nix_build_slice_cpu_usage_avg,
            r.shared_nix_build_slice_memory_usage_max_mb,
            r.shared_background_slice_cpu_usage_avg,
            r.shared_background_slice_memory_usage_max_mb,
            r.host_cpu_pressure_some_avg10_max,
            r.host_io_pressure_some_avg10_max,
            r.host_io_pressure_full_avg10_max,
            r.host_memory_pressure_some_avg10_max,
            r.host_memory_pressure_full_avg10_max,
            getattr(r, "host_block_read_mib_delta", None),
            getattr(r, "host_block_write_mib_delta", None),
            getattr(r, "host_block_read_iops_avg", None),
            getattr(r, "host_block_write_iops_avg", None),
            getattr(r, "host_block_busiest_device", None),
            getattr(r, "host_block_busiest_device_total_mib_delta", None),
            getattr(r, "host_block_busiest_device_read_iops_avg", None),
            getattr(r, "host_block_busiest_device_write_iops_avg", None),
            getattr(r, "host_block_busiest_device_weighted_io_ms_per_s", None),
            r.shm_free_min_mb,
            r.shm_used_max_mb,
            r.process_count_max,
            r.resource_sample_count,
        ),
        batch_size=10_000,
    )


def promote_work_observation_stages(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    return promote_rows(
        conn,
        table="work_observation_stage",
        columns=_WORK_OBSERVATION_STAGE_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda r: (
            "xtask_history",
            r.source_id,
            r.invocation_source_id,
            r.stage_name,
            r.started_at,
            r.duration_s,
            r.success,
        ),
        batch_size=10_000,
    )


def promote_work_observation_test_results(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    rows: Iterable[Any],
) -> int:
    return promote_rows(
        conn,
        table="work_observation_test_result",
        columns=_WORK_OBSERVATION_TEST_RESULT_COLUMNS,
        refresh_id=refresh_id,
        rows=rows,
        extractor=lambda r: (
            "xtask_history",
            r.source_id,
            r.invocation_source_id,
            r.test_name,
            r.package,
            r.status,
            r.duration_s,
            r.attempt,
            r.slot_name,
            r.slot_wait_ms,
            r.cleanup_ms,
            r.failure_type,
            r.test_mode,
            r.nats_context,
        ),
        batch_size=50_000,
    )


def load_work_observations(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    project: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)
    if start is not None:
        clauses.append("started_at::DATE >= ?")
        params.append(start)
    if end is not None:
        clauses.append("started_at::DATE < ?")
        params.append(end)
    if project is not None:
        clauses.append("project = ?")
        params.append(project)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(min(max(int(limit), 1), 10_000))
    rows = conn.execute(
        f"""
        SELECT source, source_id, work_kind, project, command, cwd,
               started_at, ended_at, duration_s, status, exit_code, host,
               git_commit, git_dirty, live_stage, args, refresh_id
        FROM work_observation
        {where}
        ORDER BY started_at DESC, source_id DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in rows]


__all__ = [
    "load_work_observations",
    "promote_polylogue_devtools_observations",
    "promote_work_observation_stages",
    "promote_work_observation_test_results",
    "promote_work_observations",
]
