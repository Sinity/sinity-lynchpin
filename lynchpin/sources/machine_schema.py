"""SQLite schema contract for Sinnix machine telemetry."""

from __future__ import annotations

import sqlite3

from .machine_models import MachineTelemetrySchemaError

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
    "swap_used_mb",
    "io_psi_some_avg10",
    "io_psi_full_avg10",
    "latency_oversleep_ms",
    "dstate_task_count",
    "gap_codes_json",
)

OPTIONAL_METRIC_COLUMNS = (
    "mem_total_mb",
    "mem_used_mb",
    "mem_anon_mb",
    "mem_file_cache_mb",
    "mem_slab_reclaimable_mb",
    "mem_slab_unreclaimable_mb",
    "mem_dirty_mb",
    "mem_writeback_mb",
    "mem_shmem_mb",
    "cpu_psi_some_avg60",
    "cpu_psi_some_avg300",
    "cpu_psi_some_total_us",
    "io_psi_some_avg60",
    "io_psi_some_avg300",
    "io_psi_some_total_us",
    "io_psi_full_avg60",
    "io_psi_full_avg300",
    "io_psi_full_total_us",
    "memory_psi_some_avg10",
    "memory_psi_some_avg60",
    "memory_psi_some_avg300",
    "memory_psi_some_total_us",
    "memory_psi_full_avg10",
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

OPTIONAL_SERVICE_STATE_COLUMNS = (
    "memory_anon_bytes",
    "memory_file_bytes",
    "memory_kernel_bytes",
    "memory_slab_bytes",
    "memory_sock_bytes",
    "memory_shmem_bytes",
    "memory_swapcached_bytes",
    "memory_zswap_bytes",
    "memory_zswapped_bytes",
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

EXPECTED_BLOCK_DEVICE_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "major",
    "minor",
    "device",
    "reads_completed",
    "reads_merged",
    "sectors_read",
    "read_time_ms",
    "writes_completed",
    "writes_merged",
    "sectors_written",
    "write_time_ms",
    "ios_in_progress",
    "io_time_ms",
    "weighted_io_time_ms",
    "discards_completed",
    "discards_merged",
    "sectors_discarded",
    "discard_time_ms",
    "flushes_completed",
    "flush_time_ms",
)

EXPECTED_SERVICE_CGROUP_IO_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "unit",
    "scope",
    "control_group",
    "major",
    "minor",
    "rbytes",
    "wbytes",
    "rios",
    "wios",
    "dbytes",
    "dios",
)

EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "unit",
    "scope",
    "control_group",
    "cpu_some_avg10",
    "cpu_some_avg60",
    "cpu_some_avg300",
    "cpu_some_total_us",
    "io_some_avg10",
    "io_some_avg60",
    "io_some_avg300",
    "io_some_total_us",
    "io_full_avg10",
    "io_full_avg60",
    "io_full_avg300",
    "io_full_total_us",
    "memory_some_avg10",
    "memory_some_avg60",
    "memory_some_avg300",
    "memory_some_total_us",
    "memory_full_avg10",
    "memory_full_avg60",
    "memory_full_avg300",
    "memory_full_total_us",
)

EXPECTED_PROCESS_IO_DELTA_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "interval_s",
    "pid",
    "process_start_time_ticks",
    "comm",
    "exe",
    "cgroup",
    "unit",
    "scope",
    "read_bytes_delta",
    "write_bytes_delta",
    "cancelled_write_bytes_delta",
    "read_chars_delta",
    "write_chars_delta",
    "read_syscalls_delta",
    "write_syscalls_delta",
    "total_bytes_delta",
    "total_syscalls_delta",
)

OPTIONAL_PROCESS_IO_DELTA_COLUMNS = (
    "command_line",
)

EXPECTED_PROCESS_MEMORY_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "pid",
    "process_start_time_ticks",
    "comm",
    "exe",
    "cgroup",
    "unit",
    "scope",
    "command_line",
    "rss_kb",
    "pss_kb",
    "pss_anon_kb",
    "pss_file_kb",
    "pss_shmem_kb",
    "private_clean_kb",
    "private_dirty_kb",
    "shared_clean_kb",
    "shared_dirty_kb",
    "swap_kb",
)

EXPECTED_CGROUP_MEMORY_COLUMNS = (
    "observed_at",
    "host",
    "boot_id",
    "schema_version",
    "label",
    "scope",
    "control_group",
    "memory_current_bytes",
    "memory_peak_bytes",
    "memory_swap_current_bytes",
    "memory_swap_peak_bytes",
    "memory_high_bytes",
    "memory_max_bytes",
    "memory_anon_bytes",
    "memory_file_bytes",
    "memory_kernel_bytes",
    "memory_slab_bytes",
    "memory_sock_bytes",
    "memory_shmem_bytes",
    "memory_swapcached_bytes",
    "memory_zswap_bytes",
    "memory_zswapped_bytes",
    "cgroup_populated",
    "cgroup_frozen",
    "cgroup_freeze",
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


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            [table],
        ).fetchone()
        is not None
    )


def validate_metric_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(conn, "metric_sample", BASE_METRIC_COLUMNS)


def metric_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    columns = _table_columns(conn, "metric_sample")
    return BASE_METRIC_COLUMNS + tuple(
        column for column in OPTIONAL_METRIC_COLUMNS if column in columns
    )


def validate_service_state_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(conn, "service_state", EXPECTED_SERVICE_STATE_COLUMNS)


def service_state_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    columns = _table_columns(conn, "service_state")
    return EXPECTED_SERVICE_STATE_COLUMNS + tuple(
        column for column in OPTIONAL_SERVICE_STATE_COLUMNS if column in columns
    )


def validate_network_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(conn, "network_sample", EXPECTED_NETWORK_COLUMNS)


def validate_block_device_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(conn, "block_device_sample", EXPECTED_BLOCK_DEVICE_COLUMNS)


def validate_service_cgroup_io_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(
        conn, "service_cgroup_io_sample", EXPECTED_SERVICE_CGROUP_IO_COLUMNS
    )


def validate_service_cgroup_pressure_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(
        conn,
        "service_cgroup_pressure_sample",
        EXPECTED_SERVICE_CGROUP_PRESSURE_COLUMNS,
    )


def validate_process_io_delta_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(
        conn,
        "process_io_delta_sample",
        EXPECTED_PROCESS_IO_DELTA_COLUMNS,
    )


def process_io_delta_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    columns = _table_columns(conn, "process_io_delta_sample")
    return EXPECTED_PROCESS_IO_DELTA_COLUMNS + tuple(
        column for column in OPTIONAL_PROCESS_IO_DELTA_COLUMNS if column in columns
    )


def validate_process_memory_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(
        conn,
        "process_memory_sample",
        EXPECTED_PROCESS_MEMORY_COLUMNS,
    )


def process_memory_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    _table_columns(conn, "process_memory_sample")
    return EXPECTED_PROCESS_MEMORY_COLUMNS


def validate_cgroup_memory_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(
        conn,
        "cgroup_memory_sample",
        EXPECTED_CGROUP_MEMORY_COLUMNS,
    )


def cgroup_memory_columns(conn: sqlite3.Connection) -> tuple[str, ...]:
    _table_columns(conn, "cgroup_memory_sample")
    return EXPECTED_CGROUP_MEMORY_COLUMNS


def validate_gpu_schema(conn: sqlite3.Connection) -> None:
    _validate_columns(conn, "gpu_sample", EXPECTED_GPU_COLUMNS)


def _validate_columns(
    conn: sqlite3.Connection, table: str, expected: tuple[str, ...]
) -> None:
    columns = _table_columns(conn, table)
    missing = tuple(column for column in expected if column not in columns)
    if missing:
        raise MachineTelemetrySchemaError(
            f"{table} is missing expected columns: " + ", ".join(missing)
        )


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
