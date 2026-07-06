"""Machine telemetry source.

Sinnix owns live host capture. Lynchpin reads those files and promotes them
into the DuckDB substrate for analysis. The current live edge is SQLite because
it is append-safe for a long-running systemd daemon; DuckDB remains the
analytical substrate.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import socket
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from .machine_models import (
    MachineBlockDeviceSample,
    MachineCgroupMemorySample,
    MachineGpuSample,
    MachineKillEvent,
    MachineMetricSample,
    MachineNetworkSample,
    MachineProcessIODeltaSample,
    MachineProcessMemorySample,
    MachineServiceCgroupIOSample,
    MachineServiceCgroupPressureSample,
    MachineServiceState,
    MachineSourceReadiness,
    MachineTelemetrySchemaError,
)
from .machine_schema import (
    EXPECTED_BLOCK_DEVICE_COLUMNS,
    EXPECTED_GPU_COLUMNS,
    EXPECTED_NETWORK_COLUMNS,
    EXPECTED_SERVICE_CGROUP_IO_COLUMNS,
    EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS,
    kill_event_columns,
    metric_columns,
    process_io_delta_columns,
    process_memory_columns,
    cgroup_memory_columns,
    service_state_columns,
    table_exists,
    validate_block_device_schema,
    validate_gpu_schema,
    validate_kill_event_schema,
    validate_metric_schema,
    validate_network_schema,
    validate_process_io_delta_schema,
    validate_process_memory_schema,
    validate_cgroup_memory_schema,
    validate_service_cgroup_io_schema,
    validate_service_cgroup_pressure_schema,
    validate_service_state_schema,
)
from .machine_sqlite import (
    as_utc,
    connect_readonly,
    count_sqlite_rows,
    default_route_interface,
    json_obj,
)

__all__ = [
    "MachineBlockDeviceSample",
    "MachineCgroupMemorySample",
    "MachineGpuSample",
    "MachineKillEvent",
    "MachineMetricSample",
    "MachineNetworkSample",
    "MachineProcessIODeltaSample",
    "MachineProcessMemorySample",
    "MachineServiceCgroupIOSample",
    "MachineServiceCgroupPressureSample",
    "MachineServiceState",
    "MachineSourceReadiness",
    "MachineTelemetrySchemaError",
    "gpu_samples",
    "kill_events",
    "latest_metric_sample",
    "block_device_samples",
    "readiness",
    "metric_samples",
    "network_samples",
    "service_states",
    "service_cgroup_io_samples",
    "service_cgroup_pressure_samples",
    "process_io_delta_samples",
    "process_memory_samples",
    "cgroup_memory_samples",
    "canonical_machine_table_path",
]


def canonical_machine_table_path(table: str) -> Path:
    return get_config().captures_root / f"machine/processed/{table}.ndjson"


def _default_machine_db() -> Path | None:
    db = get_config().machine_telemetry_db
    return db if db.exists() else None


def readiness() -> MachineSourceReadiness:
    cfg = get_config()
    live_rows = count_sqlite_rows(cfg.machine_telemetry_db, "metric_sample")
    network_rows = count_sqlite_rows(cfg.machine_telemetry_db, "network_sample")
    block_device_rows = count_sqlite_rows(
        cfg.machine_telemetry_db, "block_device_sample"
    )
    cgroup_io_rows = count_sqlite_rows(
        cfg.machine_telemetry_db, "service_cgroup_io_sample"
    )
    cgroup_pressure_rows = count_sqlite_rows(
        cfg.machine_telemetry_db, "service_cgroup_pressure_sample"
    )
    process_io_delta_rows = count_sqlite_rows(
        cfg.machine_telemetry_db, "process_io_delta_sample"
    )
    process_memory_rows = count_sqlite_rows(
        cfg.machine_telemetry_db, "process_memory_sample"
    )
    kill_event_rows = count_sqlite_rows(cfg.machine_telemetry_db, "kill_event")
    if live_rows:
        status = "ready"
        reason = (
            "live machine telemetry SQLite has samples; "
            f"network_samples={network_rows}; "
            f"block_device_samples={block_device_rows}; "
            f"service_cgroup_io_samples={cgroup_io_rows}; "
            f"service_cgroup_pressure_samples={cgroup_pressure_rows}; "
            f"process_io_delta_samples={process_io_delta_rows}; "
            f"process_memory_samples={process_memory_rows}; "
            f"kill_events={kill_event_rows}"
        )
    else:
        status = "unavailable"
        reason = "no live machine telemetry samples found"
    return MachineSourceReadiness(
        status=status,
        reason=reason,
        live_db=cfg.machine_telemetry_db,
        live_rows=live_rows,
    )


def metric_samples(
    *, start: date | None = None, end: date | None = None, path: Path | None = None
) -> Iterator[MachineMetricSample]:
    if path is None:
        if db := _default_machine_db():
            yield from metric_samples(start=start, end=end, path=db)
            return
        yield from _metric_samples_from_ndjson(
            canonical_machine_table_path("metric_sample"), start=start, end=end
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    with connect_readonly(db) as conn:
        validate_metric_schema(conn)
        sql = "SELECT " + ", ".join(metric_columns(conn)) + " FROM metric_sample"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            if sample := _metric_sample_from_sqlite_row(row):
                yield sample


def latest_metric_sample(*, path: Path | None = None) -> MachineMetricSample | None:
    if path is None:
        if db := _default_machine_db():
            return latest_metric_sample(path=db)
        latest: MachineMetricSample | None = None
        for latest in _metric_samples_from_ndjson(
            canonical_machine_table_path("metric_sample"),
            start=None,
            end=None,
        ):
            pass
        return latest
    db = path
    if not db.exists():
        return None
    with connect_readonly(db) as conn:
        validate_metric_schema(conn)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT "
            + ", ".join(metric_columns(conn))
            + " FROM metric_sample ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return _metric_sample_from_sqlite_row(row)


def _metric_sample_from_sqlite_row(row: sqlite3.Row) -> MachineMetricSample | None:
    observed_at = as_utc(row["observed_at"])
    if observed_at is None:
        return None
    row_keys = row.keys()
    try:
        gaps = tuple(json.loads(row["gap_codes_json"] or "[]"))
    except (json.JSONDecodeError, TypeError):
        gaps = ("gap_codes_json.invalid",)
    return MachineMetricSample(
        observed_at=observed_at,
        host=row["host"],
        boot_id=row["boot_id"],
        source="machine.telemetry",
        source_schema_version=int(row["schema_version"]),
        cpu_package_w=row["cpu_package_w"],
        cpu_core_w=row["cpu_core_w"],
        cpu_pkg_c=row["cpu_pkg_c"],
        cpu_max_core_c=row["cpu_max_core_c"],
        gpu_power_w=row["gpu_power_w"],
        gpu_fan_pct=row["gpu_fan_pct"],
        gpu_temp_c=row["gpu_temp_c"],
        gpu_util_pct=row["gpu_util_pct"],
        gpu_pstate=row["gpu_pstate"],
        gpu_pcie_gen=row["gpu_pcie_gen"],
        gpu_pcie_width=row["gpu_pcie_width"],
        load_1m=row["load_1m"],
        mem_total_mb=row["mem_total_mb"] if "mem_total_mb" in row_keys else None,
        mem_used_mb=row["mem_used_mb"] if "mem_used_mb" in row_keys else None,
        mem_avail_mb=row["mem_avail_mb"],
        mem_anon_mb=row["mem_anon_mb"] if "mem_anon_mb" in row_keys else None,
        mem_file_cache_mb=row["mem_file_cache_mb"]
        if "mem_file_cache_mb" in row_keys
        else None,
        mem_slab_reclaimable_mb=row["mem_slab_reclaimable_mb"]
        if "mem_slab_reclaimable_mb" in row_keys
        else None,
        mem_slab_unreclaimable_mb=row["mem_slab_unreclaimable_mb"]
        if "mem_slab_unreclaimable_mb" in row_keys
        else None,
        mem_dirty_mb=row["mem_dirty_mb"] if "mem_dirty_mb" in row_keys else None,
        mem_writeback_mb=row["mem_writeback_mb"]
        if "mem_writeback_mb" in row_keys
        else None,
        mem_shmem_mb=row["mem_shmem_mb"] if "mem_shmem_mb" in row_keys else None,
        swap_used_mb=row["swap_used_mb"],
        io_psi_some_avg10=row["io_psi_some_avg10"],
        io_psi_some_avg60=row["io_psi_some_avg60"]
        if "io_psi_some_avg60" in row_keys
        else None,
        io_psi_some_avg300=row["io_psi_some_avg300"]
        if "io_psi_some_avg300" in row_keys
        else None,
        io_psi_some_total_us=row["io_psi_some_total_us"]
        if "io_psi_some_total_us" in row_keys
        else None,
        io_psi_full_avg10=row["io_psi_full_avg10"],
        io_psi_full_avg60=row["io_psi_full_avg60"]
        if "io_psi_full_avg60" in row_keys
        else None,
        io_psi_full_avg300=row["io_psi_full_avg300"]
        if "io_psi_full_avg300" in row_keys
        else None,
        io_psi_full_total_us=row["io_psi_full_total_us"]
        if "io_psi_full_total_us" in row_keys
        else None,
        cpu_psi_some_avg60=row["cpu_psi_some_avg60"]
        if "cpu_psi_some_avg60" in row_keys
        else None,
        cpu_psi_some_avg300=row["cpu_psi_some_avg300"]
        if "cpu_psi_some_avg300" in row_keys
        else None,
        cpu_psi_some_total_us=row["cpu_psi_some_total_us"]
        if "cpu_psi_some_total_us" in row_keys
        else None,
        memory_psi_some_avg10=row["memory_psi_some_avg10"]
        if "memory_psi_some_avg10" in row_keys
        else None,
        memory_psi_some_avg60=row["memory_psi_some_avg60"]
        if "memory_psi_some_avg60" in row_keys
        else None,
        memory_psi_some_avg300=row["memory_psi_some_avg300"]
        if "memory_psi_some_avg300" in row_keys
        else None,
        memory_psi_some_total_us=row["memory_psi_some_total_us"]
        if "memory_psi_some_total_us" in row_keys
        else None,
        memory_psi_full_avg10=row["memory_psi_full_avg10"]
        if "memory_psi_full_avg10" in row_keys
        else None,
        memory_psi_full_avg60=row["memory_psi_full_avg60"]
        if "memory_psi_full_avg60" in row_keys
        else None,
        memory_psi_full_avg300=row["memory_psi_full_avg300"]
        if "memory_psi_full_avg300" in row_keys
        else None,
        memory_psi_full_total_us=row["memory_psi_full_total_us"]
        if "memory_psi_full_total_us" in row_keys
        else None,
        latency_oversleep_ms=row["latency_oversleep_ms"],
        dstate_task_count=row["dstate_task_count"],
        gap_codes=gaps,
        vmstat_workingset_refault_file=row["vmstat_workingset_refault_file"]
        if "vmstat_workingset_refault_file" in row_keys
        else None,
        vmstat_workingset_refault_anon=row["vmstat_workingset_refault_anon"]
        if "vmstat_workingset_refault_anon" in row_keys
        else None,
        vmstat_workingset_activate_file=row["vmstat_workingset_activate_file"]
        if "vmstat_workingset_activate_file" in row_keys
        else None,
        vmstat_workingset_activate_anon=row["vmstat_workingset_activate_anon"]
        if "vmstat_workingset_activate_anon" in row_keys
        else None,
        vmstat_pgscan_kswapd=row["vmstat_pgscan_kswapd"]
        if "vmstat_pgscan_kswapd" in row_keys
        else None,
        vmstat_pgscan_direct=row["vmstat_pgscan_direct"]
        if "vmstat_pgscan_direct" in row_keys
        else None,
        vmstat_pgsteal_kswapd=row["vmstat_pgsteal_kswapd"]
        if "vmstat_pgsteal_kswapd" in row_keys
        else None,
        vmstat_pgsteal_direct=row["vmstat_pgsteal_direct"]
        if "vmstat_pgsteal_direct" in row_keys
        else None,
        vmstat_pswpin=row["vmstat_pswpin"] if "vmstat_pswpin" in row_keys else None,
        vmstat_pswpout=row["vmstat_pswpout"] if "vmstat_pswpout" in row_keys else None,
        vmstat_allocstall_normal=row["vmstat_allocstall_normal"]
        if "vmstat_allocstall_normal" in row_keys
        else None,
        vmstat_allocstall_movable=row["vmstat_allocstall_movable"]
        if "vmstat_allocstall_movable" in row_keys
        else None,
        vmstat_oom_kill=row["vmstat_oom_kill"] if "vmstat_oom_kill" in row_keys else None,
    )


def service_states(
    *, start: date | None = None, end: date | None = None, path: Path | None = None
) -> Iterator[MachineServiceState]:
    if path is None:
        if db := _default_machine_db():
            yield from service_states(start=start, end=end, path=db)
            return
        yield from _service_states_from_ndjson(
            canonical_machine_table_path("service_state"), start=start, end=end
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    with connect_readonly(db) as conn:
        validate_service_state_schema(conn)
        columns = service_state_columns(conn)
        sql = "SELECT " + ", ".join(columns) + " FROM service_state"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at, scope, unit"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            row_keys = row.keys()
            yield MachineServiceState(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                unit=row["unit"],
                scope=row["scope"],
                active_state=row["active_state"],
                sub_state=row["sub_state"],
                main_pid=row["main_pid"],
                control_group=row["control_group"],
                memory_current_bytes=row["memory_current_bytes"],
                memory_anon_bytes=row["memory_anon_bytes"]
                if "memory_anon_bytes" in row_keys
                else None,
                memory_file_bytes=row["memory_file_bytes"]
                if "memory_file_bytes" in row_keys
                else None,
                memory_kernel_bytes=row["memory_kernel_bytes"]
                if "memory_kernel_bytes" in row_keys
                else None,
                memory_slab_bytes=row["memory_slab_bytes"]
                if "memory_slab_bytes" in row_keys
                else None,
                memory_sock_bytes=row["memory_sock_bytes"]
                if "memory_sock_bytes" in row_keys
                else None,
                memory_shmem_bytes=row["memory_shmem_bytes"]
                if "memory_shmem_bytes" in row_keys
                else None,
                memory_swapcached_bytes=row["memory_swapcached_bytes"]
                if "memory_swapcached_bytes" in row_keys
                else None,
                memory_zswap_bytes=row["memory_zswap_bytes"]
                if "memory_zswap_bytes" in row_keys
                else None,
                memory_zswapped_bytes=row["memory_zswapped_bytes"]
                if "memory_zswapped_bytes" in row_keys
                else None,
                cpu_usage_nsec=row["cpu_usage_nsec"],
                io_read_bytes=row["io_read_bytes"],
                io_write_bytes=row["io_write_bytes"],
            )


def block_device_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineBlockDeviceSample]:
    if path is None:
        if db := _default_machine_db():
            yield from block_device_samples(start=start, end=end, path=db)
            return
        yield from _block_device_samples_from_ndjson(
            canonical_machine_table_path("block_device_sample"),
            start=start,
            end=end,
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    sql = (
        "SELECT "
        + ", ".join(EXPECTED_BLOCK_DEVICE_COLUMNS)
        + " FROM block_device_sample"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at, device"
    with connect_readonly(db) as conn:
        if not table_exists(conn, "block_device_sample"):
            return
        validate_block_device_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineBlockDeviceSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                major=row["major"],
                minor=row["minor"],
                device=row["device"],
                reads_completed=row["reads_completed"],
                reads_merged=row["reads_merged"],
                sectors_read=row["sectors_read"],
                read_time_ms=row["read_time_ms"],
                writes_completed=row["writes_completed"],
                writes_merged=row["writes_merged"],
                sectors_written=row["sectors_written"],
                write_time_ms=row["write_time_ms"],
                ios_in_progress=row["ios_in_progress"],
                io_time_ms=row["io_time_ms"],
                weighted_io_time_ms=row["weighted_io_time_ms"],
                discards_completed=row["discards_completed"],
                discards_merged=row["discards_merged"],
                sectors_discarded=row["sectors_discarded"],
                discard_time_ms=row["discard_time_ms"],
                flushes_completed=row["flushes_completed"],
                flush_time_ms=row["flush_time_ms"],
            )


def service_cgroup_io_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineServiceCgroupIOSample]:
    if path is None:
        if db := _default_machine_db():
            yield from service_cgroup_io_samples(start=start, end=end, path=db)
            return
        yield from _service_cgroup_io_samples_from_ndjson(
            canonical_machine_table_path("service_cgroup_io_sample"),
            start=start,
            end=end,
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    sql = (
        "SELECT "
        + ", ".join(EXPECTED_SERVICE_CGROUP_IO_COLUMNS)
        + " FROM service_cgroup_io_sample"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at, scope, unit, major, minor"
    with connect_readonly(db) as conn:
        if not table_exists(conn, "service_cgroup_io_sample"):
            return
        validate_service_cgroup_io_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineServiceCgroupIOSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                unit=row["unit"],
                scope=row["scope"],
                control_group=row["control_group"],
                major=row["major"],
                minor=row["minor"],
                rbytes=row["rbytes"],
                wbytes=row["wbytes"],
                rios=row["rios"],
                wios=row["wios"],
                dbytes=row["dbytes"],
                dios=row["dios"],
            )


def service_cgroup_pressure_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineServiceCgroupPressureSample]:
    if path is None:
        if db := _default_machine_db():
            yield from service_cgroup_pressure_samples(start=start, end=end, path=db)
            return
        yield from _service_cgroup_pressure_samples_from_ndjson(
            canonical_machine_table_path("service_cgroup_pressure_sample"),
            start=start,
            end=end,
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    sql = (
        "SELECT "
        + ", ".join(EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS)
        + " FROM service_cgroup_pressure_sample"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at, scope, unit"
    with connect_readonly(db) as conn:
        if not table_exists(conn, "service_cgroup_pressure_sample"):
            return
        validate_service_cgroup_pressure_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineServiceCgroupPressureSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                unit=row["unit"],
                scope=row["scope"],
                control_group=row["control_group"],
                cpu_some_avg10=row["cpu_some_avg10"],
                cpu_some_avg60=row["cpu_some_avg60"],
                cpu_some_avg300=row["cpu_some_avg300"],
                cpu_some_total_us=row["cpu_some_total_us"],
                io_some_avg10=row["io_some_avg10"],
                io_some_avg60=row["io_some_avg60"],
                io_some_avg300=row["io_some_avg300"],
                io_some_total_us=row["io_some_total_us"],
                io_full_avg10=row["io_full_avg10"],
                io_full_avg60=row["io_full_avg60"],
                io_full_avg300=row["io_full_avg300"],
                io_full_total_us=row["io_full_total_us"],
                memory_some_avg10=row["memory_some_avg10"],
                memory_some_avg60=row["memory_some_avg60"],
                memory_some_avg300=row["memory_some_avg300"],
                memory_some_total_us=row["memory_some_total_us"],
                memory_full_avg10=row["memory_full_avg10"],
                memory_full_avg60=row["memory_full_avg60"],
                memory_full_avg300=row["memory_full_avg300"],
                memory_full_total_us=row["memory_full_total_us"],
            )


def process_io_delta_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineProcessIODeltaSample]:
    if path is None:
        if db := _default_machine_db():
            yield from process_io_delta_samples(start=start, end=end, path=db)
            return
        yield from _process_io_delta_samples_from_ndjson(
            canonical_machine_table_path("process_io_delta_sample"),
            start=start,
            end=end,
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    with connect_readonly(db) as conn:
        if not table_exists(conn, "process_io_delta_sample"):
            return
        validate_process_io_delta_schema(conn)
        columns = process_io_delta_columns(conn)
        sql = (
            "SELECT " + ", ".join(columns) + " FROM process_io_delta_sample"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at, total_bytes_delta DESC, total_syscalls_delta DESC"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            row_keys = row.keys()
            yield MachineProcessIODeltaSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                interval_s=float(row["interval_s"]),
                pid=int(row["pid"]),
                process_start_time_ticks=int(row["process_start_time_ticks"]),
                comm=row["comm"],
                exe=row["exe"],
                cgroup=row["cgroup"],
                unit=row["unit"],
                scope=row["scope"],
                command_line=row["command_line"]
                if "command_line" in row_keys
                else None,
                read_bytes_delta=int(row["read_bytes_delta"]),
                write_bytes_delta=int(row["write_bytes_delta"]),
                cancelled_write_bytes_delta=int(
                    row["cancelled_write_bytes_delta"]
                ),
                read_chars_delta=int(row["read_chars_delta"]),
                write_chars_delta=int(row["write_chars_delta"]),
                read_syscalls_delta=int(row["read_syscalls_delta"]),
                write_syscalls_delta=int(row["write_syscalls_delta"]),
                total_bytes_delta=int(row["total_bytes_delta"]),
                total_syscalls_delta=int(row["total_syscalls_delta"]),
            )


def process_memory_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    limit: int | None = None,
) -> Iterator[MachineProcessMemorySample]:
    if path is None:
        if db := _default_machine_db():
            yielded = False
            for sample in process_memory_samples(
                start=start,
                end=end,
                path=db,
                limit=limit,
            ):
                yielded = True
                yield sample
            if yielded:
                return
        ndjson = canonical_machine_table_path("process_memory_sample")
        if ndjson.exists():
            yield from _process_memory_samples_from_ndjson(
                ndjson, start=start, end=end
            )
            return
        yield from _live_process_memory_samples(start=start, end=end, limit=limit)
        return
    db = path
    if not db.exists():
        return
    with connect_readonly(db) as conn:
        if not table_exists(conn, "process_memory_sample"):
            return
        validate_process_memory_schema(conn)
        columns = process_memory_columns(conn)
        where: list[str] = []
        params: list[object] = []
        if start is not None:
            where.append("date(observed_at) >= ?")
            params.append(start.isoformat())
        if end is not None:
            where.append("date(observed_at) <= ?")
            params.append(end.isoformat())
        sql = "SELECT " + ", ".join(columns) + " FROM process_memory_sample"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at, pss_kb DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(int(limit), 0))
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineProcessMemorySample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                pid=int(row["pid"]),
                process_start_time_ticks=row["process_start_time_ticks"],
                comm=row["comm"],
                exe=row["exe"],
                cgroup=row["cgroup"],
                unit=row["unit"],
                scope=row["scope"],
                command_line=row["command_line"],
                rss_kb=int(row["rss_kb"]),
                pss_kb=int(row["pss_kb"]),
                pss_anon_kb=row["pss_anon_kb"],
                pss_file_kb=row["pss_file_kb"],
                pss_shmem_kb=row["pss_shmem_kb"],
                private_clean_kb=int(row["private_clean_kb"]),
                private_dirty_kb=int(row["private_dirty_kb"]),
                shared_clean_kb=int(row["shared_clean_kb"]),
                shared_dirty_kb=int(row["shared_dirty_kb"]),
                swap_kb=int(row["swap_kb"]),
            )


def cgroup_memory_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineCgroupMemorySample]:
    if path is None:
        if db := _default_machine_db():
            yield from cgroup_memory_samples(start=start, end=end, path=db)
            return
        ndjson = canonical_machine_table_path("cgroup_memory_sample")
        if ndjson.exists():
            yield from _cgroup_memory_samples_from_ndjson(ndjson, start=start, end=end)
        return
    db = path
    if not db.exists():
        return
    with connect_readonly(db) as conn:
        if not table_exists(conn, "cgroup_memory_sample"):
            return
        validate_cgroup_memory_schema(conn)
        columns = cgroup_memory_columns(conn)
        where: list[str] = []
        params: list[object] = []
        if start is not None:
            where.append("date(observed_at) >= ?")
            params.append(start.isoformat())
        if end is not None:
            where.append("date(observed_at) <= ?")
            params.append(end.isoformat())
        sql = "SELECT " + ", ".join(columns) + " FROM cgroup_memory_sample"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at, label"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            row_keys = row.keys()
            yield MachineCgroupMemorySample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                label=row["label"],
                scope=row["scope"],
                control_group=row["control_group"],
                memory_current_bytes=row["memory_current_bytes"],
                memory_peak_bytes=row["memory_peak_bytes"],
                memory_swap_current_bytes=row["memory_swap_current_bytes"],
                memory_swap_peak_bytes=row["memory_swap_peak_bytes"],
                memory_high_bytes=row["memory_high_bytes"],
                memory_max_bytes=row["memory_max_bytes"],
                memory_anon_bytes=row["memory_anon_bytes"],
                memory_file_bytes=row["memory_file_bytes"],
                memory_kernel_bytes=row["memory_kernel_bytes"],
                memory_slab_bytes=row["memory_slab_bytes"],
                memory_sock_bytes=row["memory_sock_bytes"],
                memory_shmem_bytes=row["memory_shmem_bytes"],
                memory_swapcached_bytes=row["memory_swapcached_bytes"],
                memory_zswap_bytes=row["memory_zswap_bytes"],
                memory_zswapped_bytes=row["memory_zswapped_bytes"],
                cgroup_populated=row["cgroup_populated"],
                cgroup_frozen=row["cgroup_frozen"],
                cgroup_freeze=row["cgroup_freeze"],
                memory_events_high=row["memory_events_high"]
                if "memory_events_high" in row_keys
                else None,
                memory_events_max=row["memory_events_max"]
                if "memory_events_max" in row_keys
                else None,
                memory_events_oom=row["memory_events_oom"]
                if "memory_events_oom" in row_keys
                else None,
                memory_events_oom_kill=row["memory_events_oom_kill"]
                if "memory_events_oom_kill" in row_keys
                else None,
            )


def kill_events(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineKillEvent]:
    """Iterate OOM/earlyoom kill events (sinnix-fjq, schema v5, new table).

    ``killer`` in {``earlyoom``, ``kernel-oom``, ``memcg-oom``,
    ``systemd-oomd``}; only ``earlyoom`` has been observed with real rows on
    this host so far — the others are schema-supported, not yet populated.
    """
    if path is None:
        if db := _default_machine_db():
            yield from kill_events(start=start, end=end, path=db)
            return
        ndjson = canonical_machine_table_path("kill_event")
        if ndjson.exists():
            yield from _kill_events_from_ndjson(ndjson, start=start, end=end)
        return
    db = path
    if not db.exists():
        return
    with connect_readonly(db) as conn:
        if not table_exists(conn, "kill_event"):
            return
        validate_kill_event_schema(conn)
        columns = kill_event_columns(conn)
        where: list[str] = []
        params: list[object] = []
        if start is not None:
            where.append("date(observed_at) >= ?")
            params.append(start.isoformat())
        if end is not None:
            where.append("date(observed_at) <= ?")
            params.append(end.isoformat())
        sql = "SELECT " + ", ".join(columns) + " FROM kill_event"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineKillEvent(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                killer=row["killer"],
                victim_comm=row["victim_comm"],
                victim_pid=row["victim_pid"],
                victim_rss_mib=row["victim_rss_mib"],
                cgroup_path=row["cgroup_path"],
                oom_score=row["oom_score"],
                raw_line=row["raw_line"],
                source_row_id=int(row["id"]),
                journal_cursor=row["journal_cursor"],
            )


def _live_process_memory_samples(
    *,
    start: date | None,
    end: date | None,
    limit: int | None,
) -> Iterator[MachineProcessMemorySample]:
    observed_at = datetime.now(timezone.utc)
    if start is not None and observed_at.date() < start:
        return
    if end is not None and observed_at.date() > end:
        return

    host = socket.gethostname()
    boot_id = _read_text(Path("/proc/sys/kernel/random/boot_id")).strip() or None
    samples: list[MachineProcessMemorySample] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        sample = _process_memory_sample_from_proc(
            proc,
            observed_at=observed_at,
            host=host,
            boot_id=boot_id,
        )
        if sample is not None:
            samples.append(sample)

    max_rows = 50 if limit is None else max(int(limit), 0)
    samples.sort(key=lambda row: row.pss_kb, reverse=True)
    yield from samples[:max_rows]


def _process_memory_sample_from_proc(
    proc: Path,
    *,
    observed_at: datetime,
    host: str,
    boot_id: str | None,
) -> MachineProcessMemorySample | None:
    try:
        pid = int(proc.name)
        rollup = _parse_smaps_rollup(proc / "smaps_rollup")
        if not rollup or rollup.get("Pss", 0) <= 0:
            return None
        comm = _read_text(proc / "comm").strip() or None
        cmdline = _read_cmdline(proc / "cmdline")
        stat = _read_text(proc / "stat")
        start_ticks = _process_start_ticks(stat)
        exe = os.readlink(proc / "exe") if (proc / "exe").exists() else None
        cgroup = _read_process_cgroup(proc / "cgroup")
        unit = _unit_from_cgroup(cgroup)
        scope = _scope_from_cgroup(cgroup)
        return MachineProcessMemorySample(
            observed_at=observed_at,
            host=host,
            boot_id=boot_id,
            source_schema_version=1,
            pid=pid,
            process_start_time_ticks=start_ticks,
            comm=comm,
            exe=exe,
            cgroup=cgroup,
            unit=unit,
            scope=scope,
            command_line=cmdline,
            rss_kb=rollup.get("Rss", 0),
            pss_kb=rollup.get("Pss", 0),
            pss_anon_kb=rollup.get("Pss_Anon"),
            pss_file_kb=rollup.get("Pss_File"),
            pss_shmem_kb=rollup.get("Pss_Shmem"),
            private_clean_kb=rollup.get("Private_Clean", 0),
            private_dirty_kb=rollup.get("Private_Dirty", 0),
            shared_clean_kb=rollup.get("Shared_Clean", 0),
            shared_dirty_kb=rollup.get("Shared_Dirty", 0),
            swap_kb=rollup.get("Swap", 0),
        )
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
        return None


def _parse_smaps_rollup(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in _read_text(path).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                values[parts[0][:-1]] = int(parts[1])
            except ValueError:
                continue
    return values


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _read_cmdline(path: Path) -> str | None:
    data = path.read_bytes().replace(b"\0", b" ").strip()
    return data.decode("utf-8", errors="ignore") or None


def _process_start_ticks(stat: str) -> int | None:
    # /proc/<pid>/stat wraps comm in parentheses; fields after the last ") "
    # start at field 3. starttime is field 22, index 19 in the suffix.
    if ") " not in stat:
        return None
    fields = stat.rsplit(") ", 1)[1].split()
    if len(fields) <= 19:
        return None
    return int(fields[19])


def _read_process_cgroup(path: Path) -> str | None:
    for line in _read_text(path).splitlines():
        if "::" in line:
            return line.split("::", 1)[1]
        parts = line.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return None


def _unit_from_cgroup(cgroup: str | None) -> str | None:
    if not cgroup:
        return None
    units = [
        part
        for part in cgroup.split("/")
        if part.endswith((".service", ".scope"))
    ]
    return _systemd_unescape_fragment(units[-1]) if units else None


def _systemd_unescape_fragment(fragment: str) -> str:
    return re.sub(
        r"\\x([0-9A-Fa-f]{2})",
        lambda match: chr(int(match.group(1), 16)),
        fragment,
    )


def _scope_from_cgroup(cgroup: str | None) -> str | None:
    if not cgroup:
        return None
    if "/user.slice/" in cgroup:
        return "user"
    if cgroup.startswith("/system.slice") or ".service" in cgroup:
        return "system"
    return None


def gpu_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineGpuSample]:
    if path is None:
        if db := _default_machine_db():
            yield from gpu_samples(start=start, end=end, path=db)
            return
        yield from _gpu_samples_from_ndjson(
            canonical_machine_table_path("gpu_sample"), start=start, end=end
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    sql = "SELECT " + ", ".join(EXPECTED_GPU_COLUMNS) + " FROM gpu_sample"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at"
    with connect_readonly(db) as conn:
        if not table_exists(conn, "gpu_sample"):
            return
        validate_gpu_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            yield MachineGpuSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source="machine.telemetry.gpu",
                gpu_power_w=row["gpu_power_w"],
                gpu_power_limit_w=row["gpu_power_limit_w"],
                gpu_temp_c=row["gpu_temp_c"],
                gpu_fan_pct=row["gpu_fan_pct"],
                gpu_util_pct=row["gpu_util_pct"],
                gpu_mem_util_pct=row["gpu_mem_util_pct"],
                gpu_clock_mhz=row["gpu_clock_mhz"],
                gpu_mem_clock_mhz=row["gpu_mem_clock_mhz"],
                gpu_pstate=row["gpu_pstate"],
                gpu_pcie_gen=row["gpu_pcie_gen"],
                gpu_pcie_width=row["gpu_pcie_width"],
            )


def network_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineNetworkSample]:
    if path is None:
        if db := _default_machine_db():
            yield from network_samples(start=start, end=end, path=db)
            return
        yield from _network_samples_from_ndjson(
            canonical_machine_table_path("network_sample"), start=start, end=end
        )
        return
    db = path
    if not db.exists():
        return
    where: list[str] = []
    params: list[object] = []
    if start is not None:
        where.append("date(observed_at) >= ?")
        params.append(start.isoformat())
    if end is not None:
        where.append("date(observed_at) <= ?")
        params.append(end.isoformat())
    default_interface = default_route_interface()
    if default_interface is not None:
        where.append("interface = ?")
        params.append(default_interface)
    sql = "SELECT " + ", ".join(EXPECTED_NETWORK_COLUMNS) + " FROM network_sample"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at"
    with connect_readonly(db) as conn:
        validate_network_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            try:
                gaps = tuple(json.loads(row["gap_codes_json"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                gaps = ("gap_codes_json.invalid",)
            yield MachineNetworkSample(
                observed_at=observed_at,
                host=row["host"],
                boot_id=row["boot_id"],
                source_schema_version=int(row["schema_version"]),
                interface=row["interface"],
                gateway_ip=row["gateway_ip"],
                ping=json_obj(row["ping_json"]),
                bloat=json_obj(row["bloat_json"]) if row["bloat_json"] else None,
                iface=json_obj(row["iface_json"]),
                nic=json_obj(row["nic_json"]),
                tcp=json_obj(row["tcp_json"]),
                dns_ms=row["dns_ms"],
                pmtu_1492=None if row["pmtu_1492"] is None else bool(row["pmtu_1492"]),
                conntrack=json_obj(row["conntrack_json"]),
                gap_codes=gaps,
            )


def sample_to_json(sample: Any) -> dict[str, Any]:
    payload = asdict(sample)
    observed_at = payload.get("observed_at")
    if observed_at is not None and hasattr(observed_at, "isoformat"):
        payload["observed_at"] = observed_at.isoformat()
    return payload


def _load_machine_rows(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[dict[str, Any]]:
    from ..materialization import ensure_materialized

    ensure_materialized("machine", window=_inclusive_date_window(start, end))
    if not path.exists():
        raise FileNotFoundError(
            f"canonical machine telemetry materialization is missing: {path}. "
            "Run python -m lynchpin.ingest.machine_materialize."
        )
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            observed_at = as_utc(row.get("observed_at"))
            if observed_at is None:
                continue
            day = observed_at.date()
            if start is not None and day < start:
                continue
            if end is not None and day > end:
                continue
            row["observed_at"] = observed_at
            yield row


def _inclusive_date_window(start: date | None, end: date | None) -> tuple[date, date] | None:
    if start is None or end is None:
        return None
    return (start, end + timedelta(days=1))


def _metric_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineMetricSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineMetricSample(**row)


def _service_states_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineServiceState]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineServiceState(**row)


def _block_device_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineBlockDeviceSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineBlockDeviceSample(**row)


def _service_cgroup_io_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineServiceCgroupIOSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineServiceCgroupIOSample(**row)


def _service_cgroup_pressure_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineServiceCgroupPressureSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineServiceCgroupPressureSample(**row)


def _process_io_delta_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineProcessIODeltaSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineProcessIODeltaSample(**row)


def _process_memory_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineProcessMemorySample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineProcessMemorySample(**row)


def _cgroup_memory_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineCgroupMemorySample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineCgroupMemorySample(**row)


def _kill_events_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineKillEvent]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineKillEvent(**row)


def _gpu_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineGpuSample]:
    for row in _load_machine_rows(path, start=start, end=end):
        yield MachineGpuSample(**row)


def _network_samples_from_ndjson(
    path: Path, *, start: date | None, end: date | None
) -> Iterator[MachineNetworkSample]:
    default_interface = default_route_interface()
    for row in _load_machine_rows(path, start=start, end=end):
        if default_interface is not None and row.get("interface") != default_interface:
            continue
        yield MachineNetworkSample(**row)
