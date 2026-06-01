"""Machine table readers and promoters for the DuckDB substrate."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING, Any

from lynchpin.substrate._filters import add_date_filter, add_in_filter, build_where
from lynchpin.substrate._helpers import promote_rows

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
            run_id, run_group_id, host, workload, command, cwd,
            started_at, ended_at, monotonic_started_ns, monotonic_ended_ns,
            exit_status, execution_outcome,
            service_profile, cache_profile, measurement_context, planned_treatment,
            nix_internal_json_path,
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


_METRIC_SAMPLE_COLUMNS = (
    "observed_at", "host", "boot_id", "source", "source_schema_version",
    "cpu_package_w", "cpu_core_w", "cpu_pkg_c", "cpu_max_core_c",
    "gpu_power_w", "gpu_fan_pct", "gpu_temp_c", "gpu_util_pct",
    "gpu_pstate", "gpu_pcie_gen", "gpu_pcie_width",
    "load_1m", "mem_avail_mb", "swap_used_mb",
    "io_psi_some_avg10", "io_psi_full_avg10",
    "io_psi_some_avg60", "io_psi_some_avg300", "io_psi_some_total_us",
    "io_psi_full_avg60", "io_psi_full_avg300", "io_psi_full_total_us",
    "cpu_psi_some_avg60", "cpu_psi_some_avg300", "cpu_psi_some_total_us",
    "memory_psi_some_avg60", "memory_psi_some_avg300", "memory_psi_some_total_us",
    "memory_psi_full_avg60", "memory_psi_full_avg300", "memory_psi_full_total_us",
    "latency_oversleep_ms", "dstate_task_count", "gap_codes",
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
            s.load_1m, s.mem_avail_mb, s.swap_used_mb,
            s.io_psi_some_avg10, s.io_psi_full_avg10,
            s.io_psi_some_avg60, s.io_psi_some_avg300, s.io_psi_some_total_us,
            s.io_psi_full_avg60, s.io_psi_full_avg300, s.io_psi_full_total_us,
            s.cpu_psi_some_avg60, s.cpu_psi_some_avg300, s.cpu_psi_some_total_us,
            s.memory_psi_some_avg60, s.memory_psi_some_avg300, s.memory_psi_some_total_us,
            s.memory_psi_full_avg60, s.memory_psi_full_avg300, s.memory_psi_full_total_us,
            s.latency_oversleep_ms, s.dstate_task_count, list(s.gap_codes),
        ),
        batch_size=50_000,
    )


_SERVICE_STATE_COLUMNS = (
    "observed_at", "host", "boot_id", "unit", "scope",
    "active_state", "sub_state", "main_pid", "control_group",
    "memory_current_bytes", "cpu_usage_nsec", "io_read_bytes", "io_write_bytes",
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
            s.memory_current_bytes, s.cpu_usage_nsec, s.io_read_bytes, s.io_write_bytes,
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
    "pre_state", "post_state", "notes", "manifest_path",
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
            list(r.notes), str(r.manifest_path),
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
    max_memory_current_bytes, max_cpu_usage_nsec, max_io_read_bytes,
    max_io_write_bytes, first_observed_at, last_observed_at) tuples.
    """
    sql = """
        SELECT
            host,
            unit,
            scope,
            COUNT(*) AS samples,
            SUM(CASE WHEN active_state = 'active' THEN 1 ELSE 0 END) AS active_samples,
            MAX(memory_current_bytes) AS max_memory_current_bytes,
            MAX(cpu_usage_nsec) AS max_cpu_usage_nsec,
            MAX(io_read_bytes) AS max_io_read_bytes,
            MAX(io_write_bytes) AS max_io_write_bytes,
            MIN(observed_at) AS first_observed_at,
            MAX(observed_at) AS last_observed_at
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
    start: date | None = None,
    end: date | None = None,
    interface: str | None = None,
) -> list[tuple[Any, ...]]:
    """Return per-day bufferbloat aggregates from machine_network_sample.

    Returns (day, interface, sample_count, avg_ms_p50, avg_ms_p95,
    avg_ms_max, loss_p50, loss_p95, loss_max) tuples.
    """
    where_clauses: list[str] = [
        "bloat IS NOT NULL",
        "json_extract_string(bloat, '$.avg_ms') IS NOT NULL",
    ]
    params: list[Any] = []
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


__all__ = [
    "load_bufferbloat_daily",
    "load_borg_drill_runs",
    "load_borg_drill_summary",
    "load_machine_experiment_runs",
    "load_machine_gpu_samples",
    "load_machine_metric_daily",
    "load_machine_metric_samples",
    "load_machine_metric_series_by_context",
    "load_machine_network_samples",
    "load_machine_service_state_summary",
    "load_machine_service_states",
    "load_sinnix_generation_rows",
    "promote_machine_experiment_runs",
    "promote_machine_gpu_samples",
    "promote_machine_metric_samples",
    "promote_machine_network_samples",
    "promote_machine_service_states",
]
