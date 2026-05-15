"""Machine telemetry source.

Sinnix owns live host capture. Lynchpin reads those files and promotes them
into the DuckDB substrate for analysis. The current live edge is SQLite because
it is append-safe for a long-running systemd daemon; DuckDB remains the
analytical substrate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from ..core.config import get_config

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

BASE_METRIC_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "cpu_package_w",
    "cpu_core_w",
    "cpu_pkg_c",
    "cpu_max_core_c",
    "gpu_power_w",
    "gpu_fan_pct",
    "gpu_temp_c",
    "gpu_util_pct",
    "gpu_pstate",
    "gpu_pcie_gen",
    "gpu_pcie_width",
    "load_1m",
    "mem_avail_mb",
    "io_psi_some_avg10",
    "io_psi_full_avg10",
    "latency_oversleep_ms",
    "dstate_task_count",
    "gap_codes_json",
)

OPTIONAL_METRIC_COLUMNS = (
    "cpu_psi_some_avg60",
    "cpu_psi_some_avg300",
    "cpu_psi_some_total_us",
    "io_psi_some_avg60",
    "io_psi_some_avg300",
    "io_psi_some_total_us",
    "io_psi_full_avg60",
    "io_psi_full_avg300",
    "io_psi_full_total_us",
    "memory_psi_some_avg60",
    "memory_psi_some_avg300",
    "memory_psi_some_total_us",
    "memory_psi_full_avg60",
    "memory_psi_full_avg300",
    "memory_psi_full_total_us",
)

EXPECTED_SERVICE_STATE_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "unit",
    "scope",
    "active_state",
    "sub_state",
    "main_pid",
    "control_group",
    "memory_current_bytes",
    "cpu_usage_nsec",
    "io_read_bytes",
    "io_write_bytes",
)

EXPECTED_NETWORK_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "interface",
    "gateway_ip",
    "ping_json",
    "bloat_json",
    "iface_json",
    "nic_json",
    "tcp_json",
    "dns_ms",
    "pmtu_1492",
    "conntrack_json",
    "gap_codes_json",
)

EXPECTED_GPU_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "gpu_power_w",
    "gpu_power_limit_w",
    "gpu_temp_c",
    "gpu_fan_pct",
    "gpu_util_pct",
    "gpu_mem_util_pct",
    "gpu_clock_mhz",
    "gpu_mem_clock_mhz",
    "gpu_pstate",
    "gpu_pcie_gen",
    "gpu_pcie_width",
)


class MachineTelemetrySchemaError(RuntimeError):
    """Live SQLite telemetry does not match the Sinnix producer contract."""


@dataclass(frozen=True)
class MachineSourceReadiness:
    status: str
    reason: str
    live_db: Path
    live_rows: int


@dataclass(frozen=True)
class MachineMetricSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source: str
    source_schema_version: int
    cpu_package_w: float | None = None
    cpu_core_w: float | None = None
    cpu_pkg_c: float | None = None
    cpu_max_core_c: float | None = None
    gpu_power_w: float | None = None
    gpu_fan_pct: float | None = None
    gpu_temp_c: float | None = None
    gpu_util_pct: float | None = None
    gpu_pstate: str | None = None
    gpu_pcie_gen: int | None = None
    gpu_pcie_width: int | None = None
    load_1m: float | None = None
    mem_avail_mb: int | None = None
    io_psi_some_avg10: float | None = None
    io_psi_some_avg60: float | None = None
    io_psi_some_avg300: float | None = None
    io_psi_some_total_us: float | None = None
    io_psi_full_avg10: float | None = None
    io_psi_full_avg60: float | None = None
    io_psi_full_avg300: float | None = None
    io_psi_full_total_us: float | None = None
    cpu_psi_some_avg60: float | None = None
    cpu_psi_some_avg300: float | None = None
    cpu_psi_some_total_us: float | None = None
    memory_psi_some_avg60: float | None = None
    memory_psi_some_avg300: float | None = None
    memory_psi_some_total_us: float | None = None
    memory_psi_full_avg60: float | None = None
    memory_psi_full_avg300: float | None = None
    memory_psi_full_total_us: float | None = None
    latency_oversleep_ms: float | None = None
    dstate_task_count: int | None = None
    gap_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineGpuSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source: str
    gpu_power_w: float | None = None
    gpu_power_limit_w: float | None = None
    gpu_temp_c: float | None = None
    gpu_fan_pct: float | None = None
    gpu_util_pct: float | None = None
    gpu_mem_util_pct: float | None = None
    gpu_clock_mhz: float | None = None
    gpu_mem_clock_mhz: float | None = None
    gpu_pstate: str | None = None
    gpu_pcie_gen: int | None = None
    gpu_pcie_width: int | None = None


@dataclass(frozen=True)
class MachineServiceState:
    observed_at: datetime
    host: str
    boot_id: str | None
    unit: str
    scope: str
    active_state: str | None
    sub_state: str | None
    main_pid: int | None = None
    control_group: str | None = None
    memory_current_bytes: int | None = None
    cpu_usage_nsec: int | None = None
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None


@dataclass(frozen=True)
class MachineNetworkSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    interface: str
    gateway_ip: str
    ping: dict[str, object]
    bloat: dict[str, object] | None
    iface: dict[str, object]
    nic: dict[str, object]
    tcp: dict[str, object]
    dns_ms: int | None
    pmtu_1492: bool | None
    conntrack: dict[str, object]
    gap_codes: tuple[str, ...] = ()


def _as_utc(value: str) -> datetime | None:
    try:
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _default_route_interface() -> str | None:
    route = Path("/proc/net/route")
    try:
        for line in route.read_text().splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
                return fields[0]
    except (OSError, ValueError):
        return None
    return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        [table],
    ).fetchone() is not None


def _validate_metric_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(metric_sample)").fetchall()
    }
    missing = tuple(column for column in BASE_METRIC_COLUMNS if column not in columns)
    if missing:
        raise MachineTelemetrySchemaError(
            "metric_sample is missing expected columns: " + ", ".join(missing)
        )


def _metric_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(metric_sample)").fetchall()
    }
    return BASE_METRIC_COLUMNS + tuple(
        column for column in OPTIONAL_METRIC_COLUMNS if column in columns
    )


def _validate_service_state_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(service_state)").fetchall()
    }
    missing = tuple(column for column in EXPECTED_SERVICE_STATE_COLUMNS if column not in columns)
    if missing:
        raise MachineTelemetrySchemaError(
            "service_state is missing expected columns: " + ", ".join(missing)
        )


def _validate_network_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(network_sample)").fetchall()
    }
    missing = tuple(column for column in EXPECTED_NETWORK_COLUMNS if column not in columns)
    if missing:
        raise MachineTelemetrySchemaError(
            "network_sample is missing expected columns: " + ", ".join(missing)
        )


def _validate_gpu_schema(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(gpu_sample)").fetchall()
    }
    missing = tuple(column for column in EXPECTED_GPU_COLUMNS if column not in columns)
    if missing:
        raise MachineTelemetrySchemaError(
            "gpu_sample is missing expected columns: " + ", ".join(missing)
        )


def _count_sqlite_rows(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    try:
        with _connect_readonly(path) as conn:
            if table == "metric_sample":
                _validate_metric_schema(conn)
            elif table == "service_state":
                _validate_service_state_schema(conn)
            elif table == "network_sample":
                _validate_network_schema(conn)
            elif table == "gpu_sample":
                _validate_gpu_schema(conn)
            if table == "network_sample":
                default_interface = _default_route_interface()
                if default_interface:
                    return int(
                        conn.execute(
                            "SELECT COUNT(*) FROM network_sample WHERE interface = ?",
                            [default_interface],
                        ).fetchone()[0]
                    )
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except (sqlite3.Error, MachineTelemetrySchemaError):
        return 0


def readiness() -> MachineSourceReadiness:
    cfg = get_config()
    live_rows = _count_sqlite_rows(cfg.machine_telemetry_db, "metric_sample")
    network_rows = _count_sqlite_rows(cfg.machine_telemetry_db, "network_sample")
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
    with _connect_readonly(db) as conn:
        _validate_metric_schema(conn)
        sql = "SELECT " + ", ".join(_metric_columns(conn)) + " FROM metric_sample"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY observed_at"
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = _as_utc(row["observed_at"])
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
    with _connect_readonly(db) as conn:
        _validate_service_state_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = _as_utc(row["observed_at"])
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
    with _connect_readonly(db) as conn:
        if not _table_exists(conn, "gpu_sample"):
            return
        _validate_gpu_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = _as_utc(row["observed_at"])
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


def _json_obj(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {"_parse_error": True}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


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
    default_interface = _default_route_interface()
    if default_interface is not None:
        where.append("interface = ?")
        params.append(default_interface)
    sql = "SELECT " + ", ".join(EXPECTED_NETWORK_COLUMNS) + " FROM network_sample"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY observed_at"
    with _connect_readonly(db) as conn:
        _validate_network_schema(conn)
        conn.row_factory = sqlite3.Row
        for row in conn.execute(sql, params):
            observed_at = _as_utc(row["observed_at"])
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
                ping=_json_obj(row["ping_json"]),
                bloat=_json_obj(row["bloat_json"]) if row["bloat_json"] else None,
                iface=_json_obj(row["iface_json"]),
                nic=_json_obj(row["nic_json"]),
                tcp=_json_obj(row["tcp_json"]),
                dns_ms=row["dns_ms"],
                pmtu_1492=None if row["pmtu_1492"] is None else bool(row["pmtu_1492"]),
                conntrack=_json_obj(row["conntrack_json"]),
                gap_codes=gaps,
            )
