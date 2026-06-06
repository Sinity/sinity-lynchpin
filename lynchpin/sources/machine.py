"""Machine telemetry source.

Sinnix owns live host capture. Lynchpin reads those files and promotes them
into the DuckDB substrate for analysis. The current live edge is SQLite because
it is append-safe for a long-running systemd daemon; DuckDB remains the
analytical substrate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterator

from ..core.config import get_config
from .machine_models import (
    MachineBlockDeviceSample,
    MachineGpuSample,
    MachineMetricSample,
    MachineNetworkSample,
    MachineProcessIODeltaSample,
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
    EXPECTED_PROCESS_IO_DELTA_COLUMNS,
    EXPECTED_SERVICE_CGROUP_IO_COLUMNS,
    EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS,
    EXPECTED_SERVICE_STATE_COLUMNS,
    metric_columns,
    table_exists,
    validate_block_device_schema,
    validate_gpu_schema,
    validate_metric_schema,
    validate_network_schema,
    validate_process_io_delta_schema,
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
    "MachineGpuSample",
    "MachineMetricSample",
    "MachineNetworkSample",
    "MachineProcessIODeltaSample",
    "MachineServiceCgroupIOSample",
    "MachineServiceCgroupPressureSample",
    "MachineServiceState",
    "MachineSourceReadiness",
    "MachineTelemetrySchemaError",
    "gpu_samples",
    "latest_metric_sample",
    "block_device_samples",
    "readiness",
    "metric_samples",
    "network_samples",
    "service_states",
    "service_cgroup_io_samples",
    "service_cgroup_pressure_samples",
    "process_io_delta_samples",
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
    if live_rows:
        status = "ready"
        reason = (
            "live machine telemetry SQLite has samples; "
            f"network_samples={network_rows}; "
            f"block_device_samples={block_device_rows}; "
            f"service_cgroup_io_samples={cgroup_io_rows}; "
            f"service_cgroup_pressure_samples={cgroup_pressure_rows}; "
            f"process_io_delta_samples={process_io_delta_rows}"
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
        mem_avail_mb=row["mem_avail_mb"],
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
    sql = "SELECT " + ", ".join(EXPECTED_SERVICE_STATE_COLUMNS) + " FROM service_state"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at, scope, unit"
    with connect_readonly(db) as conn:
        validate_service_state_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
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
    sql = (
        "SELECT "
        + ", ".join(EXPECTED_PROCESS_IO_DELTA_COLUMNS)
        + " FROM process_io_delta_sample"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at, total_bytes_delta DESC, total_syscalls_delta DESC"
    with connect_readonly(db) as conn:
        if not table_exists(conn, "process_io_delta_sample"):
            return
        validate_process_io_delta_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
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
