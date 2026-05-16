"""Machine telemetry source.

Sinnix owns live host capture. Lynchpin reads those files and promotes them
into the DuckDB substrate for analysis. The current live edge is SQLite because
it is append-safe for a long-running systemd daemon; DuckDB remains the
analytical substrate.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from .machine_models import (
    MachineGpuSample,
    MachineMetricSample,
    MachineNetworkSample,
    MachineServiceState,
    MachineSourceReadiness,
    MachineTelemetrySchemaError,
)
from .machine_schema import (
    EXPECTED_GPU_COLUMNS,
    EXPECTED_NETWORK_COLUMNS,
    EXPECTED_SERVICE_STATE_COLUMNS,
    metric_columns,
    table_exists,
    validate_gpu_schema,
    validate_metric_schema,
    validate_network_schema,
    validate_service_state_schema,
)
from .machine_sqlite import as_utc, connect_readonly, count_sqlite_rows, default_route_interface, json_obj

__all__ = [
    "MachineGpuSample",
    "MachineMetricSample",
    "MachineNetworkSample",
    "MachineServiceState",
    "MachineSourceReadiness",
    "MachineTelemetrySchemaError",
    "gpu_samples",
    "readiness",
    "metric_samples",
    "network_samples",
    "service_states",
]

def readiness() -> MachineSourceReadiness:
    cfg = get_config()
    live_rows = count_sqlite_rows(cfg.machine_telemetry_db, "metric_sample")
    network_rows = count_sqlite_rows(cfg.machine_telemetry_db, "network_sample")
    if live_rows:
        status = "ready"
        reason = f"live machine telemetry SQLite has samples; network_samples={network_rows}"
    else:
        status = "unavailable"
        reason = "no live machine telemetry samples found"
    return MachineSourceReadiness(
        status=status,
        reason=reason,
        live_db=cfg.machine_telemetry_db,
        live_rows=live_rows,
    )


def metric_samples(*, start: date | None = None, end: date | None = None, path: Path | None = None) -> Iterator[MachineMetricSample]:
    db = path or get_config().machine_telemetry_db
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
            observed_at = as_utc(row["observed_at"])
            if observed_at is None:
                continue
            row_keys = row.keys()
            try:
                gaps = tuple(json.loads(row["gap_codes_json"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                gaps = ("gap_codes_json.invalid",)
            yield MachineMetricSample(
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
                io_psi_some_avg60=row["io_psi_some_avg60"] if "io_psi_some_avg60" in row_keys else None,
                io_psi_some_avg300=row["io_psi_some_avg300"] if "io_psi_some_avg300" in row_keys else None,
                io_psi_some_total_us=(
                    row["io_psi_some_total_us"] if "io_psi_some_total_us" in row_keys else None
                ),
                io_psi_full_avg10=row["io_psi_full_avg10"],
                io_psi_full_avg60=row["io_psi_full_avg60"] if "io_psi_full_avg60" in row_keys else None,
                io_psi_full_avg300=row["io_psi_full_avg300"] if "io_psi_full_avg300" in row_keys else None,
                io_psi_full_total_us=(
                    row["io_psi_full_total_us"] if "io_psi_full_total_us" in row_keys else None
                ),
                cpu_psi_some_avg60=row["cpu_psi_some_avg60"] if "cpu_psi_some_avg60" in row_keys else None,
                cpu_psi_some_avg300=row["cpu_psi_some_avg300"] if "cpu_psi_some_avg300" in row_keys else None,
                cpu_psi_some_total_us=(
                    row["cpu_psi_some_total_us"] if "cpu_psi_some_total_us" in row_keys else None
                ),
                memory_psi_some_avg60=(
                    row["memory_psi_some_avg60"] if "memory_psi_some_avg60" in row_keys else None
                ),
                memory_psi_some_avg300=(
                    row["memory_psi_some_avg300"] if "memory_psi_some_avg300" in row_keys else None
                ),
                memory_psi_some_total_us=(
                    row["memory_psi_some_total_us"] if "memory_psi_some_total_us" in row_keys else None
                ),
                memory_psi_full_avg60=(
                    row["memory_psi_full_avg60"] if "memory_psi_full_avg60" in row_keys else None
                ),
                memory_psi_full_avg300=(
                    row["memory_psi_full_avg300"] if "memory_psi_full_avg300" in row_keys else None
                ),
                memory_psi_full_total_us=(
                    row["memory_psi_full_total_us"] if "memory_psi_full_total_us" in row_keys else None
                ),
                latency_oversleep_ms=row["latency_oversleep_ms"],
                dstate_task_count=row["dstate_task_count"],
                gap_codes=gaps,
            )


def service_states(*, start: date | None = None, end: date | None = None, path: Path | None = None) -> Iterator[MachineServiceState]:
    db = path or get_config().machine_telemetry_db
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


def gpu_samples(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
) -> Iterator[MachineGpuSample]:
    db = path or get_config().machine_telemetry_db
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
    db = path or get_config().machine_telemetry_db
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
