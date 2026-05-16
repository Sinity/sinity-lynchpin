"""Machine table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)


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
            load_1m, mem_avail_mb, swap_used_mb, io_psi_some_avg10, io_psi_full_avg10,
            io_psi_some_avg60, io_psi_some_avg300, io_psi_some_total_us,
            io_psi_full_avg60, io_psi_full_avg300, io_psi_full_total_us,
            cpu_psi_some_avg60, cpu_psi_some_avg300, cpu_psi_some_total_us,
            memory_psi_some_avg60, memory_psi_some_avg300, memory_psi_some_total_us,
            memory_psi_full_avg60, memory_psi_full_avg300, memory_psi_full_total_us,
            latency_oversleep_ms, dstate_task_count, gap_codes
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
            mem_avail_mb=row[17],
            swap_used_mb=row[18],
            io_psi_some_avg10=row[19],
            io_psi_full_avg10=row[20],
            io_psi_some_avg60=row[21],
            io_psi_some_avg300=row[22],
            io_psi_some_total_us=row[23],
            io_psi_full_avg60=row[24],
            io_psi_full_avg300=row[25],
            io_psi_full_total_us=row[26],
            cpu_psi_some_avg60=row[27],
            cpu_psi_some_avg300=row[28],
            cpu_psi_some_total_us=row[29],
            memory_psi_some_avg60=row[30],
            memory_psi_some_avg300=row[31],
            memory_psi_some_total_us=row[32],
            memory_psi_full_avg60=row[33],
            memory_psi_full_avg300=row[34],
            memory_psi_full_total_us=row[35],
            latency_oversleep_ms=row[36],
            dstate_task_count=row[37],
            gap_codes=tuple(row[38] or []),
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
            run_id, host, workload, command, cwd,
            started_at, ended_at, exit_status,
            service_profile, cache_profile, planned_treatment,
            git_root, git_head, git_branch, git_dirty,
            pre_state, post_state, notes, manifest_path, refresh_id
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
            memory_current_bytes, cpu_usage_nsec, io_read_bytes, io_write_bytes
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
            cpu_usage_nsec=row[10],
            io_read_bytes=row[11],
            io_write_bytes=row[12],
        )
        for row in rows
    ]


def promote_machine_metric_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_metric_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_metric_sample WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_metric_sample (
                observed_at, host, boot_id, source, source_schema_version,
                cpu_package_w, cpu_core_w, cpu_pkg_c, cpu_max_core_c,
                gpu_power_w, gpu_fan_pct, gpu_temp_c, gpu_util_pct,
                gpu_pstate, gpu_pcie_gen, gpu_pcie_width,
                load_1m, mem_avail_mb, swap_used_mb, io_psi_some_avg10, io_psi_full_avg10,
                io_psi_some_avg60, io_psi_some_avg300, io_psi_some_total_us,
                io_psi_full_avg60, io_psi_full_avg300, io_psi_full_total_us,
                cpu_psi_some_avg60, cpu_psi_some_avg300, cpu_psi_some_total_us,
                memory_psi_some_avg60, memory_psi_some_avg300, memory_psi_some_total_us,
                memory_psi_full_avg60, memory_psi_full_avg300, memory_psi_full_total_us,
                latency_oversleep_ms, dstate_task_count, gap_codes, refresh_id
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            sample.source,
            int(sample.source_schema_version),
            sample.cpu_package_w,
            sample.cpu_core_w,
            sample.cpu_pkg_c,
            sample.cpu_max_core_c,
            sample.gpu_power_w,
            sample.gpu_fan_pct,
            sample.gpu_temp_c,
            sample.gpu_util_pct,
            sample.gpu_pstate,
            sample.gpu_pcie_gen,
            sample.gpu_pcie_width,
            sample.load_1m,
            sample.mem_avail_mb,
            sample.swap_used_mb,
            sample.io_psi_some_avg10,
            sample.io_psi_full_avg10,
            sample.io_psi_some_avg60,
            sample.io_psi_some_avg300,
            sample.io_psi_some_total_us,
            sample.io_psi_full_avg60,
            sample.io_psi_full_avg300,
            sample.io_psi_full_total_us,
            sample.cpu_psi_some_avg60,
            sample.cpu_psi_some_avg300,
            sample.cpu_psi_some_total_us,
            sample.memory_psi_some_avg60,
            sample.memory_psi_some_avg300,
            sample.memory_psi_some_total_us,
            sample.memory_psi_full_avg60,
            sample.memory_psi_full_avg300,
            sample.memory_psi_full_total_us,
            sample.latency_oversleep_ms,
            sample.dstate_task_count,
            list(sample.gap_codes),
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_metric_samples: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_service_states(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    states: Iterable[Any],
) -> int:
    """INSERT machine_service_state rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_service_state WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_service_state (
                observed_at, host, boot_id, unit, scope,
                active_state, sub_state, main_pid, control_group,
                memory_current_bytes, cpu_usage_nsec, io_read_bytes, io_write_bytes,
                refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for state in states:
        rows.append((
            state.observed_at,
            state.host,
            state.boot_id,
            state.unit,
            state.scope,
            state.active_state,
            state.sub_state,
            state.main_pid,
            state.control_group,
            state.memory_current_bytes,
            state.cpu_usage_nsec,
            state.io_read_bytes,
            state.io_write_bytes,
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_service_states: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_gpu_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_gpu_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_gpu_sample WHERE refresh_id = ?", [refresh_id])

    total = 0
    rows: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal total
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO machine_gpu_sample (
                observed_at, host, boot_id, source,
                gpu_power_w, gpu_power_limit_w, gpu_temp_c, gpu_fan_pct,
                gpu_util_pct, gpu_mem_util_pct, gpu_clock_mhz, gpu_mem_clock_mhz,
                gpu_pstate, gpu_pcie_gen, gpu_pcie_width, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        total += len(rows)
        rows.clear()

    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            sample.source,
            sample.gpu_power_w,
            sample.gpu_power_limit_w,
            sample.gpu_temp_c,
            sample.gpu_fan_pct,
            sample.gpu_util_pct,
            sample.gpu_mem_util_pct,
            sample.gpu_clock_mhz,
            sample.gpu_mem_clock_mhz,
            sample.gpu_pstate,
            sample.gpu_pcie_gen,
            sample.gpu_pcie_width,
            refresh_id,
        ))
        if len(rows) >= 50_000:
            flush()

    flush()
    log.debug("promote_machine_gpu_samples: %d rows for refresh_id=%s", total, refresh_id)
    return total


def promote_machine_network_samples(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    samples: Iterable[Any],
) -> int:
    """INSERT machine_network_sample rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_network_sample WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for sample in samples:
        rows.append((
            sample.observed_at,
            sample.host,
            sample.boot_id,
            int(sample.source_schema_version),
            sample.interface,
            sample.gateway_ip,
            json.dumps(sample.ping),
            json.dumps(sample.bloat) if sample.bloat is not None else None,
            json.dumps(sample.iface),
            json.dumps(sample.nic),
            json.dumps(sample.tcp),
            sample.dns_ms,
            sample.pmtu_1492,
            json.dumps(sample.conntrack),
            list(sample.gap_codes),
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO machine_network_sample (
                observed_at, host, boot_id, source_schema_version,
                interface, gateway_ip, ping, bloat, iface, nic, tcp,
                dns_ms, pmtu_1492, conntrack, gap_codes, refresh_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    log.debug("promote_machine_network_samples: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


def promote_machine_experiment_runs(
    conn: "duckdb.DuckDBPyConnection",
    *,
    refresh_id: str,
    runs: Iterable[Any],
) -> int:
    """INSERT machine_experiment_run rows, idempotent on refresh_id."""
    conn.execute("DELETE FROM machine_experiment_run WHERE refresh_id = ?", [refresh_id])

    rows: list[tuple[Any, ...]] = []
    for run in runs:
        rows.append((
            run.run_id,
            run.host,
            run.workload,
            list(run.command),
            run.cwd,
            run.started_at,
            run.ended_at,
            run.exit_status,
            run.service_profile,
            run.cache_profile,
            json.dumps(run.planned_treatment),
            run.git_root,
            run.git_head,
            run.git_branch,
            run.git_dirty,
            json.dumps(run.pre_state),
            json.dumps(run.post_state),
            list(run.notes),
            str(run.manifest_path),
            refresh_id,
        ))

    if rows:
        conn.executemany(
            """
            INSERT INTO machine_experiment_run (
                run_id, host, workload, command, cwd,
                started_at, ended_at, exit_status,
                service_profile, cache_profile, planned_treatment,
                git_root, git_head, git_branch, git_dirty,
                pre_state, post_state, notes, manifest_path, refresh_id
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            rows,
        )
    log.debug("promote_machine_experiment_runs: %d rows for refresh_id=%s", len(rows), refresh_id)
    return len(rows)


__all__ = [
    "load_machine_experiment_runs",
    "load_machine_gpu_samples",
    "load_machine_metric_samples",
    "load_machine_network_samples",
    "load_machine_service_states",
    "promote_machine_experiment_runs",
    "promote_machine_gpu_samples",
    "promote_machine_metric_samples",
    "promote_machine_network_samples",
    "promote_machine_service_states",
]
