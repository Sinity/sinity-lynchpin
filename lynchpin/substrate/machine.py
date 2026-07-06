"""Machine table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


def _add_observed_at_filter(
    clauses: list[str],
    params: list[Any],
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
) -> None:
    """Append observed_at filters, preserving exact datetime windows."""
    if isinstance(start, datetime):
        clauses.append("observed_at >= ?")
        params.append(start)
    elif start is not None:
        clauses.append("observed_at::DATE >= ?")
        params.append(start)
    if isinstance(end, datetime):
        clauses.append("observed_at <= ?")
        params.append(end)
    elif end is not None:
        clauses.append("observed_at::DATE <= ?")
        params.append(end)


def load_machine_metric_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:
    """SELECT and hydrate machine telemetry rows from ``machine_metric_sample``."""
    from lynchpin.sources.machine import MachineMetricSample

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    rows = conn.execute(
        f"""
        SELECT
            observed_at, host, boot_id, source, source_schema_version,
            cpu_package_w, cpu_core_w, cpu_pkg_c, cpu_max_core_c,
            gpu_power_w, gpu_fan_pct, gpu_temp_c, gpu_util_pct,
            gpu_pstate, gpu_pcie_gen, gpu_pcie_width,
            load_1m,
            mem_total_mb, mem_used_mb, mem_avail_mb, mem_anon_mb,
            mem_file_cache_mb, mem_slab_reclaimable_mb,
            mem_slab_unreclaimable_mb, mem_dirty_mb, mem_writeback_mb,
            mem_shmem_mb, swap_used_mb,
            io_psi_some_avg10, io_psi_full_avg10,
            io_psi_some_avg60, io_psi_some_avg300, io_psi_some_total_us,
            io_psi_full_avg60, io_psi_full_avg300, io_psi_full_total_us,
            cpu_psi_some_avg60, cpu_psi_some_avg300, cpu_psi_some_total_us,
            memory_psi_some_avg60, memory_psi_some_avg300, memory_psi_some_total_us,
            memory_psi_full_avg60, memory_psi_full_avg300, memory_psi_full_total_us,
            latency_oversleep_ms, dstate_task_count, gap_codes,
            vmstat_workingset_refault_file, vmstat_workingset_refault_anon,
            vmstat_workingset_activate_file, vmstat_workingset_activate_anon,
            vmstat_pgscan_kswapd, vmstat_pgscan_direct,
            vmstat_pgsteal_kswapd, vmstat_pgsteal_direct,
            vmstat_pswpin, vmstat_pswpout,
            vmstat_allocstall_normal, vmstat_allocstall_movable,
            vmstat_oom_kill,
            memory_psi_some_avg10, memory_psi_full_avg10
        FROM machine_metric_sample
        {where}
        ORDER BY observed_at
        """,
        params,
    ).fetchall()

    return [
        MachineMetricSample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source=row[3],
            source_schema_version=int(row[4]),
            cpu_package_w=row[5],
            cpu_core_w=row[6],
            cpu_pkg_c=row[7],
            cpu_max_core_c=row[8],
            gpu_power_w=row[9],
            gpu_fan_pct=row[10],
            gpu_temp_c=row[11],
            gpu_util_pct=row[12],
            gpu_pstate=row[13],
            gpu_pcie_gen=row[14],
            gpu_pcie_width=row[15],
            load_1m=row[16],
            mem_total_mb=row[17],
            mem_used_mb=row[18],
            mem_avail_mb=row[19],
            mem_anon_mb=row[20],
            mem_file_cache_mb=row[21],
            mem_slab_reclaimable_mb=row[22],
            mem_slab_unreclaimable_mb=row[23],
            mem_dirty_mb=row[24],
            mem_writeback_mb=row[25],
            mem_shmem_mb=row[26],
            swap_used_mb=row[27],
            io_psi_some_avg10=row[28],
            io_psi_full_avg10=row[29],
            io_psi_some_avg60=row[30],
            io_psi_some_avg300=row[31],
            io_psi_some_total_us=row[32],
            io_psi_full_avg60=row[33],
            io_psi_full_avg300=row[34],
            io_psi_full_total_us=row[35],
            cpu_psi_some_avg60=row[36],
            cpu_psi_some_avg300=row[37],
            cpu_psi_some_total_us=row[38],
            memory_psi_some_avg60=row[39],
            memory_psi_some_avg300=row[40],
            memory_psi_some_total_us=row[41],
            memory_psi_full_avg60=row[42],
            memory_psi_full_avg300=row[43],
            memory_psi_full_total_us=row[44],
            latency_oversleep_ms=row[45],
            dstate_task_count=row[46],
            gap_codes=tuple(row[47] or []),
            vmstat_workingset_refault_file=row[48],
            vmstat_workingset_refault_anon=row[49],
            vmstat_workingset_activate_file=row[50],
            vmstat_workingset_activate_anon=row[51],
            vmstat_pgscan_kswapd=row[52],
            vmstat_pgscan_direct=row[53],
            vmstat_pgsteal_kswapd=row[54],
            vmstat_pgsteal_direct=row[55],
            vmstat_pswpin=row[56],
            vmstat_pswpout=row[57],
            vmstat_allocstall_normal=row[58],
            vmstat_allocstall_movable=row[59],
            vmstat_oom_kill=row[60],
            memory_psi_some_avg10=row[61],
            memory_psi_full_avg10=row[62],
        )
        for row in rows
    ]


def load_machine_gpu_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:
    """SELECT and hydrate 1 Hz GPU telemetry from ``machine_gpu_sample``."""
    from lynchpin.sources.machine import MachineGpuSample

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    rows = conn.execute(
        f"""
        SELECT
            observed_at, host, boot_id, source,
            gpu_power_w, gpu_power_limit_w, gpu_temp_c, gpu_fan_pct,
            gpu_util_pct, gpu_mem_util_pct, gpu_clock_mhz, gpu_mem_clock_mhz,
            gpu_pstate, gpu_pcie_gen, gpu_pcie_width
        FROM machine_gpu_sample
        {where}
        ORDER BY observed_at
        """,
        params,
    ).fetchall()

    return [
        MachineGpuSample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source=row[3],
            gpu_power_w=row[4],
            gpu_power_limit_w=row[5],
            gpu_temp_c=row[6],
            gpu_fan_pct=row[7],
            gpu_util_pct=row[8],
            gpu_mem_util_pct=row[9],
            gpu_clock_mhz=row[10],
            gpu_mem_clock_mhz=row[11],
            gpu_pstate=row[12],
            gpu_pcie_gen=row[13],
            gpu_pcie_width=row[14],
        )
        for row in rows
    ]


def load_machine_experiment_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str | None = None,
) -> list[dict[str, Any]]:
    """SELECT machine experiment manifest rows from ``machine_experiment_run``."""
    where = ""
    params: list[Any] = []
    if refresh_id is not None:
        where = "WHERE refresh_id = ?"
        params.append(refresh_id)

    result = conn.execute(
        f"""
        SELECT
            run_id, run_group_id, host, workload, command, cwd,
            started_at, ended_at, monotonic_started_ns, monotonic_ended_ns,
            exit_status, execution_outcome,
            service_profile, cache_profile, measurement_context, planned_treatment,
            nix_internal_json_path,
            git_root, git_head, git_branch, git_dirty,
            pre_state, post_state, notes,
            validation_status, validation_issues, validation_warnings,
            manifest_validation, manifest_path, refresh_id
        FROM machine_experiment_run
        {where}
        ORDER BY started_at, run_id
        """,
        params,
    ).fetchall()
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in result]


def load_machine_network_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:
    """SELECT and hydrate integrated network probes from ``machine_network_sample``."""
    from lynchpin.sources.machine import MachineNetworkSample

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    rows = conn.execute(
        f"""
        SELECT
            observed_at, host, boot_id, source_schema_version,
            interface, gateway_ip, ping, bloat, iface, nic, tcp,
            dns_ms, pmtu_1492, conntrack, gap_codes
        FROM machine_network_sample
        {where}
        ORDER BY observed_at
        """,
        params,
    ).fetchall()

    def json_obj(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value in (None, ""):
            return {}
        if isinstance(value, str):
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        return {"value": value}

    return [
        MachineNetworkSample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source_schema_version=int(row[3]),
            interface=row[4],
            gateway_ip=row[5],
            ping=json_obj(row[6]),
            bloat=json_obj(row[7]) if row[7] is not None else None,
            iface=json_obj(row[8]),
            nic=json_obj(row[9]),
            tcp=json_obj(row[10]),
            dns_ms=row[11],
            pmtu_1492=row[12],
            conntrack=json_obj(row[13]),
            gap_codes=tuple(row[14] or []),
        )
        for row in rows
    ]


def load_machine_service_states(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    units: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
) -> list[Any]:
    """SELECT and hydrate systemd/user-unit samples from ``machine_service_state``."""
    from lynchpin.sources.machine import MachineServiceState

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    add_in_filter("unit", units, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    rows = conn.execute(
        f"""
        SELECT
            observed_at, host, boot_id, unit, scope,
            active_state, sub_state, main_pid, control_group,
            memory_current_bytes, memory_anon_bytes, memory_file_bytes,
            memory_kernel_bytes, memory_slab_bytes, memory_sock_bytes,
            memory_shmem_bytes, memory_swapcached_bytes, memory_zswap_bytes,
            memory_zswapped_bytes, cpu_usage_nsec, io_read_bytes, io_write_bytes
        FROM machine_service_state
        {where}
        ORDER BY observed_at, scope, unit
        """,
        params,
    ).fetchall()

    return [
        MachineServiceState(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            unit=row[3],
            scope=row[4],
            active_state=row[5],
            sub_state=row[6],
            main_pid=row[7],
            control_group=row[8],
            memory_current_bytes=row[9],
            memory_anon_bytes=row[10],
            memory_file_bytes=row[11],
            memory_kernel_bytes=row[12],
            memory_slab_bytes=row[13],
            memory_sock_bytes=row[14],
            memory_shmem_bytes=row[15],
            memory_swapcached_bytes=row[16],
            memory_zswap_bytes=row[17],
            memory_zswapped_bytes=row[18],
            cpu_usage_nsec=row[19],
            io_read_bytes=row[20],
            io_write_bytes=row[21],
        )
        for row in rows
    ]


def load_machine_process_io_delta_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """SELECT and hydrate bounded per-process I/O delta samples."""
    from lynchpin.sources.machine import MachineProcessIODeltaSample

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            observed_at, host, boot_id, source_schema_version, interval_s,
            pid, process_start_time_ticks, comm, exe, cgroup, unit, scope,
            command_line, read_bytes_delta, write_bytes_delta,
            cancelled_write_bytes_delta, read_chars_delta, write_chars_delta,
            read_syscalls_delta, write_syscalls_delta, total_bytes_delta,
            total_syscalls_delta
        FROM machine_process_io_delta_sample
        {where}
        ORDER BY observed_at, total_bytes_delta DESC, total_syscalls_delta DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(int(limit), 0))
    rows = conn.execute(sql, params).fetchall()
    return [
        MachineProcessIODeltaSample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source_schema_version=int(row[3]),
            interval_s=float(row[4]),
            pid=int(row[5]),
            process_start_time_ticks=row[6],
            comm=row[7],
            exe=row[8],
            cgroup=row[9],
            unit=row[10],
            scope=row[11],
            command_line=row[12],
            read_bytes_delta=int(row[13]),
            write_bytes_delta=int(row[14]),
            cancelled_write_bytes_delta=int(row[15]),
            read_chars_delta=int(row[16]),
            write_chars_delta=int(row[17]),
            read_syscalls_delta=int(row[18]),
            write_syscalls_delta=int(row[19]),
            total_bytes_delta=int(row[20]),
            total_syscalls_delta=int(row[21]),
        )
        for row in rows
    ]


def load_machine_process_memory_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """SELECT and hydrate bounded per-process PSS/private memory samples."""
    from lynchpin.sources.machine import MachineProcessMemorySample

    clauses: list[str] = []
    params: list[Any] = []

    _add_observed_at_filter(clauses, params, start=start, end=end)
    add_in_filter("host", hosts, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            observed_at, host, boot_id, source_schema_version,
            pid, process_start_time_ticks, comm, exe, cgroup, unit, scope,
            command_line, rss_kb, pss_kb, pss_anon_kb, pss_file_kb,
            pss_shmem_kb, private_clean_kb, private_dirty_kb,
            shared_clean_kb, shared_dirty_kb, swap_kb
        FROM machine_process_memory_sample
        {where}
        ORDER BY observed_at, pss_kb DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(int(limit), 0))
    rows = conn.execute(sql, params).fetchall()
    return [
        MachineProcessMemorySample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source_schema_version=int(row[3]),
            pid=int(row[4]),
            process_start_time_ticks=row[5],
            comm=row[6],
            exe=row[7],
            cgroup=row[8],
            unit=row[9],
            scope=row[10],
            command_line=row[11],
            rss_kb=int(row[12]),
            pss_kb=int(row[13]),
            pss_anon_kb=row[14],
            pss_file_kb=row[15],
            pss_shmem_kb=row[16],
            private_clean_kb=int(row[17]),
            private_dirty_kb=int(row[18]),
            shared_clean_kb=int(row[19]),
            shared_dirty_kb=int(row[20]),
            swap_kb=int(row[21]),
        )
        for row in rows
    ]


def load_machine_cgroup_memory_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
    labels: tuple[str, ...] | None = None,
    limit: int | None = None,
) -> list[Any]:
    """SELECT and hydrate aggregate cgroup memory samples."""
    from lynchpin.sources.machine import MachineCgroupMemorySample

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    add_in_filter("label", labels, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            observed_at, host, boot_id, source_schema_version, label, scope,
            control_group, memory_current_bytes, memory_peak_bytes,
            memory_swap_current_bytes, memory_swap_peak_bytes, memory_high_bytes,
            memory_max_bytes, memory_anon_bytes, memory_file_bytes,
            memory_kernel_bytes, memory_slab_bytes, memory_sock_bytes,
            memory_shmem_bytes, memory_swapcached_bytes, memory_zswap_bytes,
            memory_zswapped_bytes, cgroup_populated, cgroup_frozen,
            cgroup_freeze,
            memory_events_high, memory_events_max,
            memory_events_oom, memory_events_oom_kill
        FROM machine_cgroup_memory_sample
        {where}
        ORDER BY observed_at, label
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(int(limit), 0))
    rows = conn.execute(sql, params).fetchall()
    return [
        MachineCgroupMemorySample(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source_schema_version=int(row[3]),
            label=row[4],
            scope=row[5],
            control_group=row[6],
            memory_current_bytes=row[7],
            memory_peak_bytes=row[8],
            memory_swap_current_bytes=row[9],
            memory_swap_peak_bytes=row[10],
            memory_high_bytes=row[11],
            memory_max_bytes=row[12],
            memory_anon_bytes=row[13],
            memory_file_bytes=row[14],
            memory_kernel_bytes=row[15],
            memory_slab_bytes=row[16],
            memory_sock_bytes=row[17],
            memory_shmem_bytes=row[18],
            memory_swapcached_bytes=row[19],
            memory_zswap_bytes=row[20],
            memory_zswapped_bytes=row[21],
            cgroup_populated=row[22],
            cgroup_frozen=row[23],
            cgroup_freeze=row[24],
            memory_events_high=row[25],
            memory_events_max=row[26],
            memory_events_oom=row[27],
            memory_events_oom_kill=row[28],
        )
        for row in rows
    ]


_METRIC_SAMPLE_COLUMNS = (
    "observed_at", "host", "boot_id", "source", "source_schema_version",
    "cpu_package_w", "cpu_core_w", "cpu_pkg_c", "cpu_max_core_c",
    "gpu_power_w", "gpu_fan_pct", "gpu_temp_c", "gpu_util_pct",
    "gpu_pstate", "gpu_pcie_gen", "gpu_pcie_width",
    "load_1m",
    "mem_total_mb", "mem_used_mb", "mem_avail_mb", "mem_anon_mb",
    "mem_file_cache_mb", "mem_slab_reclaimable_mb",
    "mem_slab_unreclaimable_mb", "mem_dirty_mb", "mem_writeback_mb",
    "mem_shmem_mb", "swap_used_mb",
    "io_psi_some_avg10", "io_psi_full_avg10",
    "io_psi_some_avg60", "io_psi_some_avg300", "io_psi_some_total_us",
    "io_psi_full_avg60", "io_psi_full_avg300", "io_psi_full_total_us",
    "cpu_psi_some_avg60", "cpu_psi_some_avg300", "cpu_psi_some_total_us",
    "memory_psi_some_avg60", "memory_psi_some_avg300", "memory_psi_some_total_us",
    "memory_psi_full_avg60", "memory_psi_full_avg300", "memory_psi_full_total_us",
    "latency_oversleep_ms", "dstate_task_count", "gap_codes",
    "vmstat_workingset_refault_file", "vmstat_workingset_refault_anon",
    "vmstat_workingset_activate_file", "vmstat_workingset_activate_anon",
    "vmstat_pgscan_kswapd", "vmstat_pgscan_direct",
    "vmstat_pgsteal_kswapd", "vmstat_pgsteal_direct",
    "vmstat_pswpin", "vmstat_pswpout",
    "vmstat_allocstall_normal", "vmstat_allocstall_movable",
    "vmstat_oom_kill",
    "memory_psi_some_avg10", "memory_psi_full_avg10",
)


def promote_machine_metric_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_metric_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_metric_sample",
        columns=_METRIC_SAMPLE_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, s.source, int(s.source_schema_version),
            s.cpu_package_w, s.cpu_core_w, s.cpu_pkg_c, s.cpu_max_core_c,
            s.gpu_power_w, s.gpu_fan_pct, s.gpu_temp_c, s.gpu_util_pct,
            s.gpu_pstate, s.gpu_pcie_gen, s.gpu_pcie_width,
            s.load_1m,
            s.mem_total_mb, s.mem_used_mb, s.mem_avail_mb, s.mem_anon_mb,
            s.mem_file_cache_mb, s.mem_slab_reclaimable_mb,
            s.mem_slab_unreclaimable_mb, s.mem_dirty_mb, s.mem_writeback_mb,
            s.mem_shmem_mb, s.swap_used_mb,
            s.io_psi_some_avg10, s.io_psi_full_avg10,
            s.io_psi_some_avg60, s.io_psi_some_avg300, s.io_psi_some_total_us,
            s.io_psi_full_avg60, s.io_psi_full_avg300, s.io_psi_full_total_us,
            s.cpu_psi_some_avg60, s.cpu_psi_some_avg300, s.cpu_psi_some_total_us,
            s.memory_psi_some_avg60, s.memory_psi_some_avg300, s.memory_psi_some_total_us,
            s.memory_psi_full_avg60, s.memory_psi_full_avg300, s.memory_psi_full_total_us,
            s.latency_oversleep_ms, s.dstate_task_count, list(s.gap_codes),
            s.vmstat_workingset_refault_file, s.vmstat_workingset_refault_anon,
            s.vmstat_workingset_activate_file, s.vmstat_workingset_activate_anon,
            s.vmstat_pgscan_kswapd, s.vmstat_pgscan_direct,
            s.vmstat_pgsteal_kswapd, s.vmstat_pgsteal_direct,
            s.vmstat_pswpin, s.vmstat_pswpout,
            s.vmstat_allocstall_normal, s.vmstat_allocstall_movable,
            s.vmstat_oom_kill,
            s.memory_psi_some_avg10, s.memory_psi_full_avg10,
        ),
        batch_size=10_000,
    )


_SERVICE_STATE_COLUMNS = (
    "observed_at", "host", "boot_id", "unit", "scope",
    "active_state", "sub_state", "main_pid", "control_group",
    "memory_current_bytes", "memory_anon_bytes", "memory_file_bytes",
    "memory_kernel_bytes", "memory_slab_bytes", "memory_sock_bytes",
    "memory_shmem_bytes", "memory_swapcached_bytes", "memory_zswap_bytes",
    "memory_zswapped_bytes", "cpu_usage_nsec", "io_read_bytes", "io_write_bytes",
)


_PROCESS_IO_DELTA_COLUMNS = (
    "observed_at", "host", "boot_id", "source_schema_version",
    "interval_s", "pid", "process_start_time_ticks", "comm", "exe",
    "cgroup", "unit", "scope", "command_line",
    "read_bytes_delta", "write_bytes_delta", "cancelled_write_bytes_delta",
    "read_chars_delta", "write_chars_delta", "read_syscalls_delta",
    "write_syscalls_delta", "total_bytes_delta", "total_syscalls_delta",
)


_PROCESS_MEMORY_COLUMNS = (
    "observed_at", "host", "boot_id", "source_schema_version",
    "pid", "process_start_time_ticks", "comm", "exe", "cgroup", "unit",
    "scope", "command_line", "rss_kb", "pss_kb", "pss_anon_kb",
    "pss_file_kb", "pss_shmem_kb", "private_clean_kb",
    "private_dirty_kb", "shared_clean_kb", "shared_dirty_kb", "swap_kb",
)

_CGROUP_MEMORY_COLUMNS = (
    "observed_at", "host", "boot_id", "source_schema_version", "label",
    "scope", "control_group", "memory_current_bytes", "memory_peak_bytes",
    "memory_swap_current_bytes", "memory_swap_peak_bytes", "memory_high_bytes",
    "memory_max_bytes", "memory_anon_bytes", "memory_file_bytes",
    "memory_kernel_bytes", "memory_slab_bytes", "memory_sock_bytes",
    "memory_shmem_bytes", "memory_swapcached_bytes", "memory_zswap_bytes",
    "memory_zswapped_bytes", "cgroup_populated", "cgroup_frozen",
    "cgroup_freeze",
    "memory_events_high", "memory_events_max",
    "memory_events_oom", "memory_events_oom_kill",
)

_KILL_EVENT_COLUMNS = (
    "observed_at", "host", "boot_id", "source_schema_version", "killer",
    "victim_comm", "victim_pid", "victim_rss_mib", "cgroup_path",
    "oom_score", "raw_line", "source_row_id", "journal_cursor",
)


def promote_machine_process_memory_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_process_memory_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_process_memory_sample",
        columns=_PROCESS_MEMORY_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, int(s.source_schema_version),
            s.pid, s.process_start_time_ticks, s.comm, s.exe, s.cgroup,
            s.unit, s.scope, s.command_line, s.rss_kb, s.pss_kb,
            s.pss_anon_kb, s.pss_file_kb, s.pss_shmem_kb,
            s.private_clean_kb, s.private_dirty_kb,
            s.shared_clean_kb, s.shared_dirty_kb, s.swap_kb,
        ),
        batch_size=10_000,
    )


def promote_machine_cgroup_memory_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_cgroup_memory_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_cgroup_memory_sample",
        columns=_CGROUP_MEMORY_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, int(s.source_schema_version),
            s.label, s.scope, s.control_group, s.memory_current_bytes,
            s.memory_peak_bytes, s.memory_swap_current_bytes,
            s.memory_swap_peak_bytes, s.memory_high_bytes, s.memory_max_bytes,
            s.memory_anon_bytes, s.memory_file_bytes, s.memory_kernel_bytes,
            s.memory_slab_bytes, s.memory_sock_bytes, s.memory_shmem_bytes,
            s.memory_swapcached_bytes, s.memory_zswap_bytes,
            s.memory_zswapped_bytes, s.cgroup_populated, s.cgroup_frozen,
            s.cgroup_freeze,
            s.memory_events_high, s.memory_events_max,
            s.memory_events_oom, s.memory_events_oom_kill,
        ),
        batch_size=10_000,
    )


def promote_machine_kill_events(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    events: Iterable[Any],
) -> int:
    """INSERT machine_kill_event rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_kill_event",
        columns=_KILL_EVENT_COLUMNS,
        refresh_id=refresh_id,
        rows=events,
        extractor=lambda e: (
            e.observed_at, e.host, e.boot_id, int(e.source_schema_version),
            e.killer, e.victim_comm, e.victim_pid, e.victim_rss_mib,
            e.cgroup_path, e.oom_score, e.raw_line, int(e.source_row_id),
            e.journal_cursor,
        ),
        batch_size=10_000,
    )


def load_machine_kill_events(
    conn: "duckdb.DuckDBPyConnection",
    *,
    start: date | None = None,
    end: date | None = None,
    hosts: tuple[str, ...] | None = None,
    killers: tuple[str, ...] | None = None,
    refresh_id: str | None = None,
    limit: int | None = None,
) -> list[Any]:
    """SELECT and hydrate OOM/earlyoom kill events from ``machine_kill_event``."""
    from lynchpin.sources.machine import MachineKillEvent

    clauses: list[str] = []
    params: list[Any] = []

    add_date_filter("observed_at", start, end, clauses, params)
    add_in_filter("host", hosts, clauses, params)
    add_in_filter("killer", killers, clauses, params)
    if refresh_id is not None:
        clauses.append("refresh_id = ?")
        params.append(refresh_id)

    where = build_where(clauses, params)
    sql = f"""
        SELECT
            observed_at, host, boot_id, source_schema_version, killer,
            victim_comm, victim_pid, victim_rss_mib, cgroup_path,
            oom_score, raw_line, source_row_id, journal_cursor
        FROM machine_kill_event
        {where}
        ORDER BY observed_at
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(int(limit), 0))
    rows = conn.execute(sql, params).fetchall()
    return [
        MachineKillEvent(
            observed_at=row[0],
            host=row[1],
            boot_id=row[2],
            source_schema_version=int(row[3]),
            killer=row[4],
            victim_comm=row[5],
            victim_pid=row[6],
            victim_rss_mib=row[7],
            cgroup_path=row[8],
            oom_score=row[9],
            raw_line=row[10],
            source_row_id=int(row[11]),
            journal_cursor=row[12],
        )
        for row in rows
    ]


def promote_machine_process_io_delta_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_process_io_delta_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_process_io_delta_sample",
        columns=_PROCESS_IO_DELTA_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, int(s.source_schema_version),
            s.interval_s, s.pid, s.process_start_time_ticks, s.comm, s.exe,
            s.cgroup, s.unit, s.scope, s.command_line,
            s.read_bytes_delta, s.write_bytes_delta,
            s.cancelled_write_bytes_delta, s.read_chars_delta,
            s.write_chars_delta, s.read_syscalls_delta,
            s.write_syscalls_delta, s.total_bytes_delta,
            s.total_syscalls_delta,
        ),
        batch_size=10_000,
    )


def promote_machine_service_states(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    states: Iterable[Any],
) -> int:
    """INSERT machine_service_state rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_service_state",
        columns=_SERVICE_STATE_COLUMNS,
        refresh_id=refresh_id,
        rows=states,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, s.unit, s.scope,
            s.active_state, s.sub_state, s.main_pid, s.control_group,
            s.memory_current_bytes, s.memory_anon_bytes, s.memory_file_bytes,
            s.memory_kernel_bytes, s.memory_slab_bytes, s.memory_sock_bytes,
            s.memory_shmem_bytes, s.memory_swapcached_bytes, s.memory_zswap_bytes,
            s.memory_zswapped_bytes, s.cpu_usage_nsec, s.io_read_bytes,
            s.io_write_bytes,
        ),
        batch_size=50_000,
    )


_GPU_SAMPLE_COLUMNS = (
    "observed_at", "host", "boot_id", "source",
    "gpu_power_w", "gpu_power_limit_w", "gpu_temp_c", "gpu_fan_pct",
    "gpu_util_pct", "gpu_mem_util_pct", "gpu_clock_mhz", "gpu_mem_clock_mhz",
    "gpu_pstate", "gpu_pcie_gen", "gpu_pcie_width",
)


def promote_machine_gpu_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_gpu_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_gpu_sample",
        columns=_GPU_SAMPLE_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, s.source,
            s.gpu_power_w, s.gpu_power_limit_w, s.gpu_temp_c, s.gpu_fan_pct,
            s.gpu_util_pct, s.gpu_mem_util_pct, s.gpu_clock_mhz, s.gpu_mem_clock_mhz,
            s.gpu_pstate, s.gpu_pcie_gen, s.gpu_pcie_width,
        ),
        batch_size=50_000,
    )


_NETWORK_SAMPLE_COLUMNS = (
    "observed_at", "host", "boot_id", "source_schema_version",
    "interface", "gateway_ip", "ping", "bloat", "iface", "nic", "tcp",
    "dns_ms", "pmtu_1492", "conntrack", "gap_codes",
)


def promote_machine_network_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_network_sample rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_network_sample",
        columns=_NETWORK_SAMPLE_COLUMNS,
        refresh_id=refresh_id,
        rows=samples,
        extractor=lambda s: (
            s.observed_at, s.host, s.boot_id, int(s.source_schema_version),
            s.interface, s.gateway_ip,
            json.dumps(s.ping),
            json.dumps(s.bloat) if s.bloat is not None else None,
            json.dumps(s.iface), json.dumps(s.nic), json.dumps(s.tcp),
            s.dns_ms, s.pmtu_1492,
            json.dumps(s.conntrack), list(s.gap_codes),
        ),
    )


_EXPERIMENT_RUN_COLUMNS = (
    "run_id", "run_group_id", "host", "workload", "command", "cwd",
    "started_at", "ended_at", "monotonic_started_ns", "monotonic_ended_ns",
    "exit_status", "execution_outcome",
    "service_profile", "cache_profile", "measurement_context", "planned_treatment",
    "nix_internal_json_path",
    "git_root", "git_head", "git_branch", "git_dirty",
    "pre_state", "post_state", "notes",
    "validation_status", "validation_issues", "validation_warnings",
    "manifest_validation", "manifest_path",
)


def promote_machine_experiment_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    runs: Iterable[Any],
) -> int:
    """INSERT machine_experiment_run rows, idempotent on refresh_id."""
    return promote_rows(
        conn,
        table="machine_experiment_run",
        columns=_EXPERIMENT_RUN_COLUMNS,
        refresh_id=refresh_id,
        rows=runs,
        extractor=lambda r: (
            r.run_id, r.run_group_id, r.host, r.workload, list(r.command), r.cwd,
            r.started_at, r.ended_at, r.monotonic_started_ns, r.monotonic_ended_ns,
            r.exit_status, json.dumps(r.execution_outcome),
            r.service_profile, r.cache_profile,
            json.dumps(r.measurement_context), json.dumps(r.planned_treatment),
            r.nix_internal_json_path,
            r.git_root, r.git_head, r.git_branch, r.git_dirty,
            json.dumps(r.pre_state), json.dumps(r.post_state),
            list(r.notes),
            r.validation_status, list(r.validation_issues), list(r.validation_warnings),
            json.dumps(r.manifest_validation), str(r.manifest_path),
        ),
    )


def load_machine_metric_daily(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    host: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return daily aggregated machine telemetry rows.

    Returns (day, host, samples, avg_cpu_package_w, max_cpu_package_w,
    avg_gpu_power_w, max_gpu_power_w, avg_io_psi_some_avg10,
    max_io_psi_some_avg10, avg_latency_oversleep_ms,
    max_latency_oversleep_ms, max_dstate_task_count) tuples.
    """
    sql = """
        SELECT
            observed_at::DATE AS day,
            host,
            COUNT(*) AS samples,
            AVG(cpu_package_w) AS avg_cpu_package_w,
            MAX(cpu_package_w) AS max_cpu_package_w,
            AVG(gpu_power_w) AS avg_gpu_power_w,
            MAX(gpu_power_w) AS max_gpu_power_w,
            AVG(io_psi_some_avg10) AS avg_io_psi_some_avg10,
            MAX(io_psi_some_avg10) AS max_io_psi_some_avg10,
            AVG(latency_oversleep_ms) AS avg_latency_oversleep_ms,
            MAX(latency_oversleep_ms) AS max_latency_oversleep_ms,
            MAX(dstate_task_count) AS max_dstate_task_count
        FROM machine_metric_sample
        WHERE refresh_id = ?
    """
    params: list[Any] = [refresh_id]
    if start:
        sql += " AND observed_at::DATE >= ?"
        params.append(start)
    if end:
        sql += " AND observed_at::DATE <= ?"
        params.append(end)
    if host:
        sql += " AND host = ?"
        params.append(host)
    sql += " GROUP BY day, host ORDER BY day, host"
    return conn.execute(sql, params).fetchall()


def load_machine_metric_series_by_context(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    generations_refresh_id: str | None = None,
    start: date | None = None,
    end: date | None = None,
    host: str | None = None,
) -> list[tuple[Any, ...]]:
    """Daily machine-metric series SEGMENTED by the Layer-1 context vector.

    Each (logical) day is split by **software_revision** (the NixOS generation
    active at each sample, resolved with an ASOF join: greatest
    ``activated_at <= observed_at``) and **hardware_regime** (GPU PCIe link
    gen/width). This is the set-based companion to
    ``analysis.machine.context_spine.resolve_machine_context`` and answers the
    Phase-1 question "show this metric series segmented by generation /
    hardware-regime". Generation is ``NULL`` for samples that predate any
    activation record (honest: missing generation telemetry, never imputed).

    Returns (day, generation, sinnix_revision, gpu_pcie_gen, gpu_pcie_width,
    samples, avg_cpu_package_w, avg_gpu_power_w, avg_io_psi_full_avg10,
    max_io_psi_full_avg10, avg_cpu_psi_some_avg60) tuples, ordered by
    (day, generation, gpu_pcie_gen).

    ``generations_refresh_id`` pins the generation timeline to one promote batch
    (sinnix_generation is re-promoted each refresh); pass the best generation
    refresh_id. When ``None``, all generation rows are considered (duplicates
    across batches share activated_at, so the ASOF match is unchanged).
    """
    gen_where = "WHERE refresh_id = ?" if generations_refresh_id else ""
    sql = f"""
        WITH gens AS (
            SELECT host, generation, sinnix_revision, activated_at
            FROM sinnix_generation
            {gen_where}
        )
        SELECT
            m.observed_at::DATE AS day,
            g.generation,
            g.sinnix_revision,
            m.gpu_pcie_gen,
            m.gpu_pcie_width,
            COUNT(*) AS samples,
            AVG(m.cpu_package_w) AS avg_cpu_package_w,
            AVG(m.gpu_power_w) AS avg_gpu_power_w,
            AVG(m.io_psi_full_avg10) AS avg_io_psi_full_avg10,
            MAX(m.io_psi_full_avg10) AS max_io_psi_full_avg10,
            AVG(m.cpu_psi_some_avg60) AS avg_cpu_psi_some_avg60
        FROM machine_metric_sample m
        ASOF LEFT JOIN gens g
            ON m.host = g.host AND m.observed_at >= g.activated_at
        WHERE m.refresh_id = ?
    """
    params: list[Any] = []
    if generations_refresh_id:
        params.append(generations_refresh_id)
    params.append(refresh_id)
    if start:
        sql += " AND m.observed_at::DATE >= ?"
        params.append(start)
    if end:
        sql += " AND m.observed_at::DATE <= ?"
        params.append(end)
    if host:
        sql += " AND m.host = ?"
        params.append(host)
    sql += (
        " GROUP BY day, g.generation, g.sinnix_revision,"
        " m.gpu_pcie_gen, m.gpu_pcie_width"
        " ORDER BY day, g.generation, m.gpu_pcie_gen"
    )
    return conn.execute(sql, params).fetchall()


def load_machine_memory_breakdown(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    host: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return recent decomposed memory samples from ``machine_metric_sample``.

    The promoted Sinnix schema-v4 columns split memory into anonymous process
    memory, reclaimable file/slab cache, unreclaimable slab/kernel-ish memory,
    shmem, dirty/writeback, and swap. This reader intentionally returns sample
    rows rather than a single "used" number so callers can distinguish pressure
    from reclaimable cache.
    """
    sql = """
        SELECT
            observed_at, host, source_schema_version,
            mem_total_mb, mem_used_mb, mem_avail_mb,
            mem_anon_mb, mem_file_cache_mb,
            mem_slab_reclaimable_mb, mem_slab_unreclaimable_mb,
            mem_shmem_mb, mem_dirty_mb, mem_writeback_mb,
            swap_used_mb, memory_psi_some_avg60, memory_psi_full_avg60
        FROM machine_metric_sample
        WHERE refresh_id = ?
    """
    params: list[Any] = [refresh_id]
    clauses: list[str] = []
    _add_observed_at_filter(clauses, params, start=start, end=end)
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    if host:
        sql += " AND host = ?"
        params.append(host)
    sql += " ORDER BY observed_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(int(limit), 0))
    rows = conn.execute(sql, params).fetchall()
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _rows_as_dicts(conn: "duckdb.DuckDBPyConnection") -> list[dict[str, Any]]:
    columns = [desc[0] for desc in (conn.description or [])]
    return [dict(zip(columns, row, strict=True)) for row in conn.fetchall()]


def _pressure_sort_sql(focus: str) -> str:
    if focus == "memory":
        return "mem_avail_mb ASC NULLS LAST, mem_used_mb DESC NULLS LAST"
    if focus == "swap":
        return "swap_used_mb DESC NULLS LAST, mem_avail_mb ASC NULLS LAST"
    if focus == "cache":
        return (
            "mem_file_cache_mb DESC NULLS LAST,"
            " mem_slab_reclaimable_mb DESC NULLS LAST"
        )
    if focus == "io":
        return (
            "COALESCE(io_psi_full_avg60, io_psi_full_avg10, 0) DESC,"
            " COALESCE(io_psi_some_avg60, io_psi_some_avg10, 0) DESC,"
            " mem_used_mb DESC NULLS LAST"
        )
    msg = f"unknown pressure focus {focus!r}; expected io, memory, swap, or cache"
    raise ValueError(msg)


def _pressure_notes(metric: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    anon = metric.get("mem_anon_mb") or 0
    file_cache = metric.get("mem_file_cache_mb") or 0
    slab_reclaimable = metric.get("mem_slab_reclaimable_mb") or 0
    slab_unreclaimable = metric.get("mem_slab_unreclaimable_mb") or 0
    swap = metric.get("swap_used_mb") or 0
    io_full = metric.get("io_psi_full_avg60") or metric.get("io_psi_full_avg10") or 0
    mem_full = metric.get("memory_psi_full_avg60") or 0

    if file_cache + slab_reclaimable > anon:
        notes.append("reclaimable cache/slab exceeds anonymous process memory")
    if slab_unreclaimable > 1024:
        notes.append("unreclaimable slab/kernel memory is above 1 GiB")
    if swap > 0:
        notes.append("swap is occupied; this may be retained historical pressure, not live demand")
    if io_full >= 1.0:
        notes.append("IO full PSI is elevated in this window")
    if mem_full >= 1.0:
        notes.append("memory full PSI is elevated in this window")
    if not notes:
        notes.append("no single promoted pressure signal dominates this sample")
    return notes


def load_machine_pressure_explainer(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    host: str | None = None,
    focus: str = "io",
    limit: int = 5,
    window_minutes: int = 5,
    top_n: int = 8,
) -> list[dict[str, Any]]:
    """Join memory, PSI, service RSS split, and process I/O around pressure windows."""
    metric_sql = f"""
        SELECT
            observed_at, host, source_schema_version,
            mem_total_mb, mem_used_mb, mem_avail_mb,
            mem_anon_mb, mem_file_cache_mb,
            mem_slab_reclaimable_mb, mem_slab_unreclaimable_mb,
            mem_shmem_mb, mem_dirty_mb, mem_writeback_mb,
            swap_used_mb,
            memory_psi_some_avg60,
            memory_psi_full_avg60,
            io_psi_some_avg10, io_psi_some_avg60,
            io_psi_full_avg10, io_psi_full_avg60
        FROM machine_metric_sample
        WHERE refresh_id = ?
    """
    params: list[Any] = [refresh_id]
    clauses: list[str] = []
    _add_observed_at_filter(clauses, params, start=start, end=end)
    if clauses:
        metric_sql += " AND " + " AND ".join(clauses)
    if host:
        metric_sql += " AND host = ?"
        params.append(host)
    metric_sql += f" ORDER BY {_pressure_sort_sql(focus)} LIMIT ?"
    params.append(max(int(limit), 0))

    conn.execute(metric_sql, params)
    metrics = _rows_as_dicts(conn)
    windows: list[dict[str, Any]] = []
    half_window = timedelta(minutes=max(int(window_minutes), 0))
    top_limit = max(int(top_n), 0)

    for metric in metrics:
        observed_at = metric["observed_at"]
        if not isinstance(observed_at, datetime):
            continue
        window_start = observed_at - half_window
        window_end = observed_at + half_window

        service_sql = """
            SELECT
                unit, scope,
                COUNT(*) AS samples,
                MAX(memory_current_bytes) / 1048576.0 AS max_current_mib,
                MAX(memory_anon_bytes) / 1048576.0 AS max_anon_mib,
                MAX(memory_file_bytes) / 1048576.0 AS max_file_mib,
                MAX(memory_kernel_bytes) / 1048576.0 AS max_kernel_mib,
                MIN(observed_at) AS first_observed_at,
                MAX(observed_at) AS last_observed_at
            FROM machine_service_state
            WHERE refresh_id = ?
              AND host = ?
              AND observed_at >= ?
              AND observed_at <= ?
            GROUP BY unit, scope
            ORDER BY max_current_mib DESC NULLS LAST
            LIMIT ?
        """
        conn.execute(
            service_sql,
            [refresh_id, metric["host"], window_start, window_end, top_limit],
        )
        services = _rows_as_dicts(conn)

        process_sql = """
            SELECT
                observed_at, pid, comm, unit, scope,
                total_bytes_delta / 1048576.0 AS total_mib_delta,
                read_bytes_delta / 1048576.0 AS read_mib_delta,
                write_bytes_delta / 1048576.0 AS write_mib_delta,
                total_syscalls_delta,
                LEFT(command_line, 180) AS command_line
            FROM machine_process_io_delta_sample
            WHERE refresh_id = ?
              AND host = ?
              AND observed_at >= ?
              AND observed_at <= ?
            ORDER BY total_bytes_delta DESC NULLS LAST
            LIMIT ?
        """
        conn.execute(
            process_sql,
            [refresh_id, metric["host"], window_start, window_end, top_limit],
        )
        processes = _rows_as_dicts(conn)

        memory_sql = """
            SELECT
                observed_at, pid, comm, unit, scope,
                pss_kb / 1024.0 AS pss_mib,
                rss_kb / 1024.0 AS rss_mib,
                (private_clean_kb + private_dirty_kb) / 1024.0 AS private_mib,
                pss_anon_kb / 1024.0 AS pss_anon_mib,
                pss_file_kb / 1024.0 AS pss_file_mib,
                pss_shmem_kb / 1024.0 AS pss_shmem_mib,
                swap_kb / 1024.0 AS swap_mib,
                LEFT(command_line, 180) AS command_line
            FROM machine_process_memory_sample
            WHERE refresh_id = ?
              AND host = ?
              AND observed_at >= ?
              AND observed_at <= ?
            ORDER BY pss_kb DESC NULLS LAST
            LIMIT ?
        """
        conn.execute(
            memory_sql,
            [refresh_id, metric["host"], window_start, window_end, top_limit],
        )
        memory = _rows_as_dicts(conn)

        windows.append(
            {
                "center": observed_at,
                "window_start": window_start,
                "window_end": window_end,
                "metric": metric,
                "top_services_by_memory": services,
                "top_processes_by_pss": memory,
                "top_process_io_deltas": processes,
                "notes": _pressure_notes(metric),
            }
        )

    return windows


def load_machine_service_state_summary(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    host: str | None = None,
    unit: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return aggregated service state rows.

    Returns (host, unit, scope, samples, active_samples,
    max_memory_current_bytes, max_memory_anon_bytes, max_memory_file_bytes,
    max_memory_kernel_bytes, cpu_usage_delta_nsec, io_read_delta_bytes,
    io_write_delta_bytes, first_observed_at, last_observed_at,
    last_cpu_usage_nsec, last_io_read_bytes, last_io_write_bytes) tuples.
    """
    sql = """
        SELECT
            host,
            unit,
            scope,
            COUNT(*) AS samples,
            SUM(CASE WHEN active_state = 'active' THEN 1 ELSE 0 END) AS active_samples,
            MAX(memory_current_bytes) AS max_memory_current_bytes,
            MAX(memory_anon_bytes) AS max_memory_anon_bytes,
            MAX(memory_file_bytes) AS max_memory_file_bytes,
            MAX(memory_kernel_bytes) AS max_memory_kernel_bytes,
            CASE
                WHEN MIN(cpu_usage_nsec) IS NULL OR MAX(cpu_usage_nsec) IS NULL THEN NULL
                ELSE GREATEST(MAX(cpu_usage_nsec) - MIN(cpu_usage_nsec), 0)
            END AS cpu_usage_delta_nsec,
            CASE
                WHEN MIN(io_read_bytes) IS NULL OR MAX(io_read_bytes) IS NULL THEN NULL
                ELSE GREATEST(MAX(io_read_bytes) - MIN(io_read_bytes), 0)
            END AS io_read_delta_bytes,
            CASE
                WHEN MIN(io_write_bytes) IS NULL OR MAX(io_write_bytes) IS NULL THEN NULL
                ELSE GREATEST(MAX(io_write_bytes) - MIN(io_write_bytes), 0)
            END AS io_write_delta_bytes,
            MIN(observed_at) AS first_observed_at,
            MAX(observed_at) AS last_observed_at,
            MAX(cpu_usage_nsec) AS last_cpu_usage_nsec,
            MAX(io_read_bytes) AS last_io_read_bytes,
            MAX(io_write_bytes) AS last_io_write_bytes
        FROM machine_service_state
        WHERE refresh_id = ?
    """
    params: list[Any] = [refresh_id]
    if start:
        sql += " AND observed_at::DATE >= ?"
        params.append(start)
    if end:
        sql += " AND observed_at::DATE <= ?"
        params.append(end)
    if host:
        sql += " AND host = ?"
        params.append(host)
    if unit:
        sql += " AND unit = ?"
        params.append(unit)
    sql += " GROUP BY host, unit, scope ORDER BY host, scope, unit"
    return conn.execute(sql, params).fetchall()


def load_borg_drill_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    limit: int = 50,
    status: str | None = None,
    repo: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return borg_drill_run rows ordered by started_at DESC.

    Returns (repo, archive, started_at, ended_at, duration_s, exit_code,
    status, within_days) tuples.
    """
    where_clauses: list[str] = []
    params: list[Any] = []
    if status:
        where_clauses.append("status = ?")
        params.append(status)
    if repo:
        where_clauses.append("repo = ?")
        params.append(repo)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT repo, archive, started_at, ended_at, duration_s, exit_code,
               status, within_days
        FROM borg_drill_run
        {where_sql}
        ORDER BY started_at DESC
        LIMIT ?
    """
    params.append(max(int(limit), 0))
    return conn.execute(sql, params).fetchall()


def load_borg_drill_summary(
    conn: "duckdb.DuckDBPyConnection",
) -> tuple[Any, ...] | None:
    """Return (total, ok_count, failed_count, last_started_at) summary."""
    return conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'ok') AS ok_count,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
            MAX(started_at) AS last_started_at
        FROM borg_drill_run
        """
    ).fetchone()


def load_sinnix_generation_rows(
    conn: "duckdb.DuckDBPyConnection",
    *,
    limit: int = 50,
    host: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return sinnix_generation rows ordered by activated_at DESC.

    Returns (host, generation, activated_at, store_path, sinnix_revision,
    nixos_label) tuples.
    """
    where = ""
    params: list[Any] = []
    if host:
        where = "WHERE host = ?"
        params.append(host)
    sql = f"""
        SELECT host, generation, activated_at, store_path, sinnix_revision, nixos_label
        FROM sinnix_generation
        {where}
        ORDER BY activated_at DESC
        LIMIT ?
    """
    params.append(max(int(limit), 0))
    return conn.execute(sql, params).fetchall()


def load_bufferbloat_daily(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    start: date | None = None,
    end: date | None = None,
    interface: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return per-day bufferbloat aggregates from machine_network_sample.

    Returns (day, interface, sample_count, avg_ms_p50, avg_ms_p95,
    avg_ms_max, loss_p50, loss_p95, loss_max) tuples.
    """
    where_clauses: list[str] = [
        "refresh_id = ?",
        "bloat IS NOT NULL",
        "json_extract_string(bloat, '$.avg_ms') IS NOT NULL",
    ]
    params: list[Any] = [refresh_id]
    if start:
        where_clauses.append("CAST(observed_at AS DATE) >= ?")
        params.append(start)
    if end:
        where_clauses.append("CAST(observed_at AS DATE) <= ?")
        params.append(end)
    if interface:
        where_clauses.append("interface = ?")
        params.append(interface)
    where_sql = " AND ".join(where_clauses)

    sql = f"""
        WITH parsed AS (
            SELECT
                CAST(observed_at AS DATE) AS day,
                interface,
                CAST(json_extract_string(bloat, '$.avg_ms') AS DOUBLE) AS avg_ms,
                CAST(json_extract_string(bloat, '$.min_ms') AS DOUBLE) AS min_ms,
                CAST(json_extract_string(bloat, '$.max_ms') AS DOUBLE) AS max_ms,
                CAST(json_extract_string(bloat, '$.loss')   AS DOUBLE) AS loss
            FROM machine_network_sample
            WHERE {where_sql}
        )
        SELECT
            day,
            interface,
            COUNT(*)                                         AS sample_count,
            quantile_cont(avg_ms, 0.50)                      AS avg_ms_p50,
            quantile_cont(avg_ms, 0.95)                      AS avg_ms_p95,
            MAX(avg_ms)                                      AS avg_ms_max,
            quantile_cont(loss,   0.50)                      AS loss_p50,
            quantile_cont(loss,   0.95)                      AS loss_p95,
            MAX(loss)                                        AS loss_max
        FROM parsed
        GROUP BY day, interface
        ORDER BY day, interface
    """
    return conn.execute(sql, params).fetchall()


MACHINE_PROMOTION_FRESHNESS_TABLES: tuple[tuple[str, str], ...] = (
    ("machine_metric_sample", "metric_sample"),
    ("machine_service_state", "service_state"),
    ("machine_gpu_sample", "gpu_sample"),
    ("machine_network_sample", "network_sample"),
    ("machine_cgroup_memory_sample", "cgroup_memory_sample"),
    ("machine_process_io_delta_sample", "process_io_delta_sample"),
    ("machine_process_memory_sample", "process_memory_sample"),
    ("machine_kill_event", "kill_event"),
)


def load_machine_promotion_freshness(
    conn: "duckdb.DuckDBPyConnection",
    *,
    max_lag_hours: float = 24.0,
    live_db_path: Any = None,
) -> list[dict[str, Any]]:
    """Compare each machine substrate table's newest row against the live source.

    Returns one entry per table in ``MACHINE_PROMOTION_FRESHNESS_TABLES`` with
    the live SQLite source's ``MAX(observed_at)``, the DuckDB substrate's
    ``MAX(observed_at)`` across ALL refresh_ids (i.e. whatever is actually
    persisted right now, not scoped to one refresh attempt), the lag in
    hours, and a ``stale`` flag when the lag exceeds ``max_lag_hours``. A
    table missing from the live source (not yet captured on this host/schema
    version) is skipped entirely rather than reported stale — missing is
    missing, never zero (see ``lynchpin/core/coverage.py``).

    This exists because substrate promotion can fail silently per-table
    (sinnix-kx4): a promotion crash (e.g. an OOM kill mid-run, or a DuckDB
    write corruption) can leave one table stale for days while sibling
    tables keep refreshing, with no error ever recorded in
    ``substrate_source_status`` — the crash happens before any status write.
    Comparing the substrate directly against the live source catches that
    even when the promotion pipeline itself never got a chance to record a
    failure.
    """
    from pathlib import Path as _Path

    from lynchpin.core.config import get_config
    from lynchpin.sources.machine_sqlite import as_utc, connect_readonly

    cfg = get_config()
    db_path = _Path(live_db_path) if live_db_path is not None else cfg.machine_telemetry_db
    results: list[dict[str, Any]] = []
    if db_path is None or not db_path.exists():
        return results

    sconn = connect_readonly(db_path)
    try:
        live_tables = {
            row[0]
            for row in sconn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for duck_table, live_table in MACHINE_PROMOTION_FRESHNESS_TABLES:
            if live_table not in live_tables:
                continue
            try:
                live_max_raw = sconn.execute(
                    f"SELECT MAX(observed_at) FROM {live_table}"
                ).fetchone()[0]
            except sqlite3.Error as exc:
                results.append(
                    {
                        "table": duck_table,
                        "live_table": live_table,
                        "error": str(exc),
                        "stale": False,
                    }
                )
                continue
            live_max = as_utc(live_max_raw) if live_max_raw else None
            if live_max is None:
                continue

            try:
                duck_row = conn.execute(f"SELECT MAX(observed_at) FROM {duck_table}").fetchone()
            except Exception as exc:  # e.g. table not yet created by apply_schema
                results.append(
                    {
                        "table": duck_table,
                        "live_table": live_table,
                        "error": str(exc),
                        "stale": False,
                    }
                )
                continue
            duck_max = duck_row[0] if duck_row else None
            if isinstance(duck_max, datetime) and duck_max.tzinfo is None:
                duck_max = duck_max.replace(tzinfo=timezone.utc)

            lag_hours: float | None
            if duck_max is None:
                lag_hours = None
                stale = True
            else:
                lag_value = (live_max - duck_max).total_seconds() / 3600.0
                lag_hours = lag_value
                stale = lag_value > max_lag_hours

            results.append(
                {
                    "table": duck_table,
                    "live_table": live_table,
                    "live_max_observed_at": live_max.isoformat(),
                    "substrate_max_observed_at": duck_max.isoformat() if duck_max else None,
                    "lag_hours": lag_hours,
                    "stale": stale,
                }
            )
    finally:
        sconn.close()
    return results


__all__ = [
    "load_bufferbloat_daily",
    "load_borg_drill_runs",
    "load_borg_drill_summary",
    "load_machine_experiment_runs",
    "load_machine_gpu_samples",
    "load_machine_metric_daily",
    "load_machine_metric_samples",
    "load_machine_memory_breakdown",
    "load_machine_metric_series_by_context",
    "load_machine_network_samples",
    "load_machine_pressure_explainer",
    "load_machine_process_io_delta_samples",
    "load_machine_process_memory_samples",
    "load_machine_cgroup_memory_samples",
    "load_machine_kill_events",
    "load_machine_promotion_freshness",
    "load_machine_service_state_summary",
    "load_machine_service_states",
    "load_sinnix_generation_rows",
    "MACHINE_PROMOTION_FRESHNESS_TABLES",
    "promote_machine_experiment_runs",
    "promote_machine_gpu_samples",
    "promote_machine_kill_events",
    "promote_machine_metric_samples",
    "promote_machine_network_samples",
    "promote_machine_process_io_delta_samples",
    "promote_machine_process_memory_samples",
    "promote_machine_cgroup_memory_samples",
    "promote_machine_service_states",
]
