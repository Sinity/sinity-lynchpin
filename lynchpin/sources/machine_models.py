"""Typed records emitted by the live machine telemetry source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class MachineTelemetrySchemaError(RuntimeError):
    """Live SQLite telemetry does not match the Sinnix producer contract."""


@dataclass(frozen=True)
class MachineSourceReadiness:
    status: str
    reason: str
    live_db: Path
    live_rows: int

    def __post_init__(self) -> None:
        if self.live_rows < 0:
            raise ValueError(
                f"MachineSourceReadiness.live_rows ({self.live_rows}) must be >= 0"
            )


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
    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    mem_avail_mb: int | None = None
    mem_anon_mb: int | None = None
    mem_file_cache_mb: int | None = None
    mem_slab_reclaimable_mb: int | None = None
    mem_slab_unreclaimable_mb: int | None = None
    mem_dirty_mb: int | None = None
    mem_writeback_mb: int | None = None
    mem_shmem_mb: int | None = None
    swap_used_mb: int | None = None
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
    memory_psi_some_avg10: float | None = None
    memory_psi_some_avg60: float | None = None
    memory_psi_some_avg300: float | None = None
    memory_psi_some_total_us: float | None = None
    memory_psi_full_avg10: float | None = None
    memory_psi_full_avg60: float | None = None
    memory_psi_full_avg300: float | None = None
    memory_psi_full_total_us: float | None = None
    latency_oversleep_ms: float | None = None
    dstate_task_count: int | None = None
    gap_codes: tuple[str, ...] = ()
    # sinnix-fjq (schema v5): raw cumulative /proc/vmstat reclaim/OOM
    # counters. Consumers compute deltas, same convention as the PSI
    # *_total_us fields above. None on pre-v5 captures.
    vmstat_workingset_refault_file: int | None = None
    vmstat_workingset_refault_anon: int | None = None
    vmstat_workingset_activate_file: int | None = None
    vmstat_workingset_activate_anon: int | None = None
    vmstat_pgscan_kswapd: int | None = None
    vmstat_pgscan_direct: int | None = None
    vmstat_pgsteal_kswapd: int | None = None
    vmstat_pgsteal_direct: int | None = None
    vmstat_pswpin: int | None = None
    vmstat_pswpout: int | None = None
    vmstat_allocstall_normal: int | None = None
    vmstat_allocstall_movable: int | None = None
    vmstat_oom_kill: int | None = None


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
    memory_anon_bytes: int | None = None
    memory_file_bytes: int | None = None
    memory_kernel_bytes: int | None = None
    memory_slab_bytes: int | None = None
    memory_sock_bytes: int | None = None
    memory_shmem_bytes: int | None = None
    memory_swapcached_bytes: int | None = None
    memory_zswap_bytes: int | None = None
    memory_zswapped_bytes: int | None = None
    cpu_usage_nsec: int | None = None
    io_read_bytes: int | None = None
    io_write_bytes: int | None = None


@dataclass(frozen=True)
class MachineBlockDeviceSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    major: int | None
    minor: int | None
    device: str
    reads_completed: int | None = None
    reads_merged: int | None = None
    sectors_read: int | None = None
    read_time_ms: int | None = None
    writes_completed: int | None = None
    writes_merged: int | None = None
    sectors_written: int | None = None
    write_time_ms: int | None = None
    ios_in_progress: int | None = None
    io_time_ms: int | None = None
    weighted_io_time_ms: int | None = None
    discards_completed: int | None = None
    discards_merged: int | None = None
    sectors_discarded: int | None = None
    discard_time_ms: int | None = None
    flushes_completed: int | None = None
    flush_time_ms: int | None = None


@dataclass(frozen=True)
class MachineServiceCgroupIOSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    unit: str
    scope: str
    control_group: str | None
    major: int | None
    minor: int | None
    rbytes: int | None = None
    wbytes: int | None = None
    rios: int | None = None
    wios: int | None = None
    dbytes: int | None = None
    dios: int | None = None


@dataclass(frozen=True)
class MachineServiceCgroupPressureSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    unit: str
    scope: str
    control_group: str | None
    cpu_some_avg10: float | None = None
    cpu_some_avg60: float | None = None
    cpu_some_avg300: float | None = None
    cpu_some_total_us: float | None = None
    io_some_avg10: float | None = None
    io_some_avg60: float | None = None
    io_some_avg300: float | None = None
    io_some_total_us: float | None = None
    io_full_avg10: float | None = None
    io_full_avg60: float | None = None
    io_full_avg300: float | None = None
    io_full_total_us: float | None = None
    memory_some_avg10: float | None = None
    memory_some_avg60: float | None = None
    memory_some_avg300: float | None = None
    memory_some_total_us: float | None = None
    memory_full_avg10: float | None = None
    memory_full_avg60: float | None = None
    memory_full_avg300: float | None = None
    memory_full_total_us: float | None = None


@dataclass(frozen=True)
class MachineProcessIODeltaSample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    interval_s: float
    pid: int
    process_start_time_ticks: int
    comm: str | None
    exe: str | None
    cgroup: str | None
    unit: str | None
    scope: str | None
    read_bytes_delta: int
    write_bytes_delta: int
    cancelled_write_bytes_delta: int
    read_chars_delta: int
    write_chars_delta: int
    read_syscalls_delta: int
    write_syscalls_delta: int
    total_bytes_delta: int
    total_syscalls_delta: int
    command_line: str | None = None


@dataclass(frozen=True)
class MachineProcessMemorySample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    pid: int
    process_start_time_ticks: int | None
    comm: str | None
    exe: str | None
    cgroup: str | None
    unit: str | None
    scope: str | None
    command_line: str | None
    rss_kb: int
    pss_kb: int
    pss_anon_kb: int | None
    pss_file_kb: int | None
    pss_shmem_kb: int | None
    private_clean_kb: int
    private_dirty_kb: int
    shared_clean_kb: int
    shared_dirty_kb: int
    swap_kb: int


@dataclass(frozen=True)
class MachineCgroupMemorySample:
    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    label: str
    scope: str
    control_group: str
    memory_current_bytes: int | None
    memory_peak_bytes: int | None
    memory_swap_current_bytes: int | None
    memory_swap_peak_bytes: int | None
    memory_high_bytes: int | None
    memory_max_bytes: int | None
    memory_anon_bytes: int | None
    memory_file_bytes: int | None
    memory_kernel_bytes: int | None
    memory_slab_bytes: int | None
    memory_sock_bytes: int | None
    memory_shmem_bytes: int | None
    memory_swapcached_bytes: int | None
    memory_zswap_bytes: int | None
    memory_zswapped_bytes: int | None
    cgroup_populated: int | None
    cgroup_frozen: int | None
    cgroup_freeze: int | None
    # sinnix-fjq (schema v5): cumulative cgroup v2 memory.events *counts*
    # (distinct from memory_high_bytes/memory_max_bytes above, which are
    # configured byte limits, not event counts). None on pre-v5 captures.
    memory_events_high: int | None = None
    memory_events_max: int | None = None
    memory_events_oom: int | None = None
    memory_events_oom_kill: int | None = None


@dataclass(frozen=True)
class MachineKillEvent:
    """A single OOM/earlyoom kill event parsed from the journal.

    ``killer`` distinguishes the actuator: ``earlyoom`` is well-populated on
    this host; ``kernel-oom``, ``memcg-oom``, and ``systemd-oomd`` are
    schema-supported but may have zero rows if never observed. ``raw_line``
    always carries the full original journal message even when structured
    field extraction was only best-effort.

    ``source_row_id`` is the live SQLite table's own autoincrement ``id`` —
    the only genuinely unique-per-row field. earlyoom emits repeated
    escalating SIGTERM warnings against the SAME victim pid within the same
    observed_at second (with identical or near-identical killer/victim_pid/
    oom_score/raw_line as the victim's RSS shrinks between warnings), so
    ``(observed_at, host, killer, victim_pid)`` alone is not a safe dedup key.
    """

    observed_at: datetime
    host: str
    boot_id: str | None
    source_schema_version: int
    killer: str
    victim_comm: str | None
    victim_pid: int | None
    victim_rss_mib: int | None
    cgroup_path: str | None
    oom_score: int | None
    raw_line: str
    source_row_id: int
    journal_cursor: str | None = None


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
