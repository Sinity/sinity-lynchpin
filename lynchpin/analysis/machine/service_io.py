"""Retrospective service I/O attribution for exact machine windows."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import statistics
import subprocess
from typing import Any, Callable, Iterable, Protocol, Sequence, TypeVar

from lynchpin.core.errors import DataCoverageError
from lynchpin.sources import machine
from lynchpin.sources.machine_models import (
    MachineBlockDeviceSample,
    MachineMetricSample,
    MachineProcessIODeltaSample,
    MachineServiceCgroupIOSample,
    MachineServiceCgroupPressureSample,
    MachineServiceState,
)
from lynchpin.sources.xtask_history import (
    XtaskInvocation,
    iter_all_invocations,
    iter_invocations,
)

_MIB = 1024 * 1024


class _ObservedSample(Protocol):
    observed_at: datetime


_ObservedT = TypeVar("_ObservedT", bound=_ObservedSample)


@dataclass(frozen=True)
class MachineWindowPressureSummary:
    sample_count: int
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    avg_io_psi_full_avg10: float | None
    max_io_psi_full_avg10: float | None
    avg_io_psi_some_avg10: float | None
    max_io_psi_some_avg10: float | None
    avg_memory_psi_full_avg10: float | None
    max_memory_psi_full_avg10: float | None
    avg_dstate_task_count: float | None
    max_dstate_task_count: int | None


@dataclass(frozen=True)
class MachineServiceIODelta:
    unit: str
    scope: str
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    read_bytes_delta: int
    write_bytes_delta: int
    total_bytes_delta: int
    read_mib: float
    write_mib: float
    total_mib: float
    active_states: tuple[str, ...]
    sub_states: tuple[str, ...]
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineServiceIOWindowTarget:
    source: str
    source_id: str
    command: tuple[str, ...]
    status: str
    duration_s: float | None
    host_io_pressure_full_avg10_max: float | None
    host_memory_pressure_full_avg10_max: float | None


@dataclass(frozen=True)
class MachineBelowProcessIORate:
    key: str
    comm: str
    cgroup: str
    sample_count: int
    sample_presence_pct: float | None
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    estimated_read_bytes: int
    estimated_write_bytes: int
    estimated_total_bytes: int
    estimated_read_mib: float
    estimated_write_mib: float
    estimated_total_mib: float
    max_rw_mib_s: float | None
    cmdline: str


@dataclass(frozen=True)
class MachineProcessIODeltaSummary:
    pid: int
    process_start_time_ticks: int
    comm: str | None
    exe: str | None
    cgroup: str | None
    unit: str | None
    scope: str | None
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    read_mib: float
    write_mib: float
    total_mib: float
    read_syscalls: int
    write_syscalls: int
    total_syscalls: int
    avg_total_mib_s: float | None


@dataclass(frozen=True)
class MachineBlockDeviceIODelta:
    device: str
    major: int | None
    minor: int | None
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    read_mib: float
    write_mib: float
    total_mib: float
    avg_mib_s: float | None
    read_iops: float | None
    write_iops: float | None
    io_time_ms_per_s: float | None
    weighted_io_time_ms_per_s: float | None


@dataclass(frozen=True)
class MachineServiceCgroupIODelta:
    unit: str
    scope: str
    major: int | None
    minor: int | None
    device: str | None
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    read_mib: float
    write_mib: float
    total_mib: float
    avg_mib_s: float | None
    read_iops: float | None
    write_iops: float | None
    total_iops: float | None
    device_total_mib_pct: float | None
    disk_completed_iops_pct: float | None
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineDeviceUnattributedIO:
    """Device-level IO not covered by any observed unit cgroup.

    The residual (diskstats total minus the sum of observed cgroup deltas on
    that device) makes attribution blind spots visible: kernel writeback,
    btrfs workers, and units missing from the observed inventory all land
    here instead of silently vanishing.
    """

    device: str
    major: int | None
    minor: int | None
    device_total_mib: float
    attributed_total_mib: float
    unattributed_mib: float
    unattributed_pct: float | None


@dataclass(frozen=True)
class MachineServiceCgroupPressureSummary:
    unit: str
    scope: str
    sample_count: int
    first_observed_at: datetime
    last_observed_at: datetime
    avg_io_full_avg10: float | None
    max_io_full_avg10: float | None
    avg_memory_full_avg10: float | None
    max_memory_full_avg10: float | None
    avg_cpu_some_avg10: float | None
    max_cpu_some_avg10: float | None


@dataclass(frozen=True)
class MachineWindowLoadShape:
    label: str
    reason: str
    busiest_device: str | None
    busiest_device_mib_s: float | None
    busiest_device_iops: float | None
    busiest_device_io_time_ms_per_s: float | None
    busiest_device_weighted_io_time_ms_per_s: float | None


@dataclass(frozen=True)
class MachineServiceIOAttribution:
    start: datetime
    end: datetime
    pressure: MachineWindowPressureSummary
    services: tuple[MachineServiceIODelta, ...]
    caveats: tuple[str, ...]
    target: MachineServiceIOWindowTarget | None = None
    block_devices: tuple[MachineBlockDeviceIODelta, ...] = ()
    service_cgroup_io: tuple[MachineServiceCgroupIODelta, ...] = ()
    service_cgroup_pressure: tuple[MachineServiceCgroupPressureSummary, ...] = ()
    load_shape: MachineWindowLoadShape | None = None
    device_unattributed: tuple[MachineDeviceUnattributedIO, ...] = ()
    process_io_deltas: tuple[MachineProcessIODeltaSummary, ...] = ()
    below_processes: tuple[MachineBelowProcessIORate, ...] = ()
    below_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_service_io_window(
    *,
    start: datetime,
    end: datetime,
    path: Path | None = None,
    limit: int = 20,
    min_total_mib: float = 0.0,
    target: MachineServiceIOWindowTarget | None = None,
    include_below_processes: bool = False,
    below_binary: str = "below",
    below_top_per_sample: int = 20,
) -> MachineServiceIOAttribution:
    """Rank systemd units by sampled I/O counter growth inside a time window."""
    if end < start:
        raise DataCoverageError("machine_service_io", requested=f"{start}–{end}", available="")
    start = _as_utc(start)
    end = _as_utc(end)

    metrics = [
        sample
        for sample in machine.metric_samples(
            start=start.date(), end=end.date(), path=path
        )
        if start <= _as_utc(sample.observed_at) <= end
    ]
    source_start = (start - timedelta(days=1)).date()
    source_end = (end + timedelta(days=1)).date()
    states = list(
        machine.service_states(start=source_start, end=source_end, path=path)
    )
    block_devices = list(
        machine.block_device_samples(start=source_start, end=source_end, path=path)
    )
    cgroup_io = list(
        machine.service_cgroup_io_samples(start=source_start, end=source_end, path=path)
    )
    cgroup_pressure = [
        sample
        for sample in machine.service_cgroup_pressure_samples(
            start=start.date(), end=end.date(), path=path
        )
        if start <= _as_utc(sample.observed_at) <= end
    ]
    process_io = [
        scaled
        for sample in machine.process_io_delta_samples(
            start=source_start, end=source_end, path=path
        )
        if (scaled := _scale_process_delta_to_window(sample, start, end)) is not None
    ]
    pressure = _pressure_summary(metrics)
    device_deltas = tuple(
        row
        for row in sorted(
            (
                _block_device_delta(rows)
                for rows in _group_block_device_samples(
                    _sampled_counter_envelopes(block_devices, start, end, _block_key)
                ).values()
                if _is_whole_block_device(rows[0].device)
            ),
            key=lambda item: (-item.total_mib, item.device),
        )
    )
    services = tuple(
        row
        for row in sorted(
            (
                _service_delta(rows)
                for rows in _group_service_states(
                    _sampled_counter_envelopes(states, start, end, _service_key)
                ).values()
            ),
            key=lambda item: (-item.total_bytes_delta, item.scope, item.unit),
        )
        if row.total_mib >= min_total_mib
    )[:limit]
    below_processes: tuple[MachineBelowProcessIORate, ...] = ()
    below_errors: tuple[str, ...] = ()
    if include_below_processes:
        below_processes, below_errors = _below_process_io_rates(
            start,
            end,
            below_binary=below_binary,
            top_per_sample=below_top_per_sample,
            limit=limit,
        )
    cgroup_io_deltas = tuple(
        sorted(
            (
                _service_cgroup_io_delta(rows, device_deltas)
                for rows in _group_service_cgroup_io(
                    _sampled_counter_envelopes(cgroup_io, start, end, _service_cgroup_io_key)
                ).values()
            ),
            key=lambda item: (
                -item.total_mib,
                -(item.total_iops or 0.0),
                item.scope,
                item.unit,
            ),
        )
    )
    return MachineServiceIOAttribution(
        start=start,
        end=end,
        pressure=pressure,
        services=services,
        caveats=(
            "Service I/O counters are sampled cumulative systemd unit counters; short windows can miss bursts between samples.",
            "Cumulative service, cgroup, and block-device deltas use nearest bracketing samples around the requested window when available, so reported counter growth covers the sample envelope rather than exact nanosecond boundaries.",
            "Counter resets are treated as service restarts and only positive adjacent increments are counted.",
            "Bytes attribute recorded unit activity, not storage-device queue contention; use below/device telemetry for device-level causality.",
            "Cgroup I/O counts and diskstats completed I/O counts are different kernel layers; the disk-completed IOPS ratio can exceed 100% and is a scale check, not an ownership partition.",
            "Cgroup pressure rows show stalls charged to the service cgroup itself; they still do not prove which backing device or kernel worker caused the stall.",
            "Process I/O delta rows are bounded top-N heartbeat samples; overlapping rows are scaled by interval overlap with the requested window, which estimates attribution under a uniform-rate assumption.",
        ),
        target=target,
        block_devices=device_deltas,
        service_cgroup_io=cgroup_io_deltas,
        service_cgroup_pressure=tuple(
            sorted(
                (
                    _service_cgroup_pressure_summary(rows)
                    for rows in _group_service_cgroup_pressure(cgroup_pressure).values()
                ),
                key=lambda item: (
                    -(item.max_io_full_avg10 or 0.0),
                    -(item.max_memory_full_avg10 or 0.0),
                    -(item.max_cpu_some_avg10 or 0.0),
                    item.scope,
                    item.unit,
                ),
            )
        ),
        load_shape=_classify_load_shape(pressure, device_deltas),
        device_unattributed=_device_unattributed_io(device_deltas, cgroup_io_deltas),
        process_io_deltas=tuple(
            row
            for row in sorted(
                (
                    _process_io_delta_summary(rows)
                    for rows in _group_process_io_deltas(process_io).values()
                ),
                key=lambda item: (
                    -item.total_mib,
                    -item.total_syscalls,
                    item.comm or "",
                ),
            )
        )[:limit],
        below_processes=below_processes,
        below_errors=below_errors,
    )


def analyze_machine_service_io_for_xtask_invocation(
    invocation_id: int,
    *,
    xtask_history_path: Path | None = None,
    machine_path: Path | None = None,
    limit: int = 20,
    min_total_mib: float = 0.0,
    include_below_processes: bool = False,
    below_binary: str = "below",
    below_top_per_sample: int = 20,
) -> MachineServiceIOAttribution:
    """Resolve a Sinex xtask invocation id and attribute its exact host window."""
    invocation = _find_xtask_invocation(invocation_id, path=xtask_history_path)
    if invocation.ended_at is None:
        raise ValueError(
            f"xtask invocation {invocation_id} has no finished_at timestamp"
        )
    return analyze_machine_service_io_window(
        start=invocation.started_at,
        end=invocation.ended_at,
        path=machine_path,
        limit=limit,
        min_total_mib=min_total_mib,
        target=_xtask_target(invocation),
        include_below_processes=include_below_processes,
        below_binary=below_binary,
        below_top_per_sample=below_top_per_sample,
    )


def _pressure_summary(
    samples: list[MachineMetricSample],
) -> MachineWindowPressureSummary:
    ordered = sorted(samples, key=lambda sample: sample.observed_at)
    return MachineWindowPressureSummary(
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at if ordered else None,
        last_observed_at=ordered[-1].observed_at if ordered else None,
        avg_io_psi_full_avg10=_mean(_values(ordered, "io_psi_full_avg10")),
        max_io_psi_full_avg10=_max_float(_values(ordered, "io_psi_full_avg10")),
        avg_io_psi_some_avg10=_mean(_values(ordered, "io_psi_some_avg10")),
        max_io_psi_some_avg10=_max_float(_values(ordered, "io_psi_some_avg10")),
        avg_memory_psi_full_avg10=_mean(_values(ordered, "memory_psi_full_avg10")),
        max_memory_psi_full_avg10=_max_float(_values(ordered, "memory_psi_full_avg10")),
        avg_dstate_task_count=_mean(_values(ordered, "dstate_task_count")),
        max_dstate_task_count=_max_int(_values(ordered, "dstate_task_count")),
    )


def _sampled_counter_envelopes(
    samples: Iterable[_ObservedT],
    start: datetime,
    end: datetime,
    key_fn: Callable[[_ObservedT], Any],
) -> list[_ObservedT]:
    grouped: dict[Any, list[_ObservedT]] = {}
    for sample in samples:
        grouped.setdefault(key_fn(sample), []).append(sample)

    selected: list[_ObservedT] = []
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: _as_utc(row.observed_at))
        before = [
            row
            for row in ordered
            if _as_utc(row.observed_at) < start
        ]
        inside = [
            row
            for row in ordered
            if start <= _as_utc(row.observed_at) <= end
        ]
        after = [
            row
            for row in ordered
            if _as_utc(row.observed_at) > end
        ]
        if before:
            selected.append(before[-1])
        selected.extend(inside)
        if after:
            selected.append(after[0])
    return selected


def _service_key(row: MachineServiceState) -> tuple[str, str]:
    return row.scope, row.unit


def _block_key(row: MachineBlockDeviceSample) -> str:
    return row.device


def _service_cgroup_io_key(
    row: MachineServiceCgroupIOSample,
) -> tuple[str, str, int | None, int | None]:
    return row.scope, row.unit, row.major, row.minor


def _scale_process_delta_to_window(
    row: MachineProcessIODeltaSample,
    start: datetime,
    end: datetime,
) -> MachineProcessIODeltaSample | None:
    observed_at = _as_utc(row.observed_at)
    interval_s = max(row.interval_s, 0.0)
    if interval_s <= 0.0:
        return row if start <= observed_at <= end else None

    sample_start = observed_at - timedelta(seconds=interval_s)
    overlap_start = max(sample_start, start)
    overlap_end = min(observed_at, end)
    overlap_s = (overlap_end - overlap_start).total_seconds()
    if overlap_s <= 0.0:
        return None
    ratio = min(max(overlap_s / interval_s, 0.0), 1.0)

    read_bytes = round(row.read_bytes_delta * ratio)
    write_bytes = round(row.write_bytes_delta * ratio)
    cancelled_write_bytes = round(row.cancelled_write_bytes_delta * ratio)
    read_chars = round(row.read_chars_delta * ratio)
    write_chars = round(row.write_chars_delta * ratio)
    read_syscalls = round(row.read_syscalls_delta * ratio)
    write_syscalls = round(row.write_syscalls_delta * ratio)
    return replace(
        row,
        interval_s=overlap_s,
        read_bytes_delta=read_bytes,
        write_bytes_delta=write_bytes,
        cancelled_write_bytes_delta=cancelled_write_bytes,
        read_chars_delta=read_chars,
        write_chars_delta=write_chars,
        read_syscalls_delta=read_syscalls,
        write_syscalls_delta=write_syscalls,
        total_bytes_delta=read_bytes + write_bytes,
        total_syscalls_delta=read_syscalls + write_syscalls,
    )


def _group_service_states(
    states: Iterable[MachineServiceState],
) -> dict[tuple[str, str], list[MachineServiceState]]:
    grouped: dict[tuple[str, str], list[MachineServiceState]] = {}
    for state in states:
        grouped.setdefault((state.scope, state.unit), []).append(state)
    return grouped


def _service_delta(rows: list[MachineServiceState]) -> MachineServiceIODelta:
    ordered = sorted(rows, key=lambda row: row.observed_at)
    read_delta, read_reset = _positive_counter_delta(
        [row.io_read_bytes for row in ordered]
    )
    write_delta, write_reset = _positive_counter_delta(
        [row.io_write_bytes for row in ordered]
    )
    total = read_delta + write_delta
    caveats: list[str] = []
    if read_reset or write_reset:
        caveats.append("counter_reset_detected")
    return MachineServiceIODelta(
        unit=ordered[0].unit,
        scope=ordered[0].scope,
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at,
        last_observed_at=ordered[-1].observed_at,
        read_bytes_delta=read_delta,
        write_bytes_delta=write_delta,
        total_bytes_delta=total,
        read_mib=round(read_delta / _MIB, 1),
        write_mib=round(write_delta / _MIB, 1),
        total_mib=round(total / _MIB, 1),
        active_states=_ordered_distinct(row.active_state for row in ordered),
        sub_states=_ordered_distinct(row.sub_state for row in ordered),
        caveats=tuple(caveats),
    )


def _group_block_device_samples(
    samples: Iterable[MachineBlockDeviceSample],
) -> dict[str, list[MachineBlockDeviceSample]]:
    grouped: dict[str, list[MachineBlockDeviceSample]] = {}
    for sample in samples:
        grouped.setdefault(sample.device, []).append(sample)
    return grouped


def _block_device_delta(
    rows: list[MachineBlockDeviceSample],
) -> MachineBlockDeviceIODelta:
    ordered = sorted(rows, key=lambda row: row.observed_at)
    read_sectors, _read_reset = _positive_counter_delta(
        [row.sectors_read for row in ordered]
    )
    write_sectors, _write_reset = _positive_counter_delta(
        [row.sectors_written for row in ordered]
    )
    reads, _reads_reset = _positive_counter_delta(
        [row.reads_completed for row in ordered]
    )
    writes, _writes_reset = _positive_counter_delta(
        [row.writes_completed for row in ordered]
    )
    io_time_ms, _io_reset = _positive_counter_delta([row.io_time_ms for row in ordered])
    weighted_io_time_ms, _weighted_reset = _positive_counter_delta(
        [row.weighted_io_time_ms for row in ordered]
    )
    elapsed_s = max(
        (ordered[-1].observed_at - ordered[0].observed_at).total_seconds(), 0.0
    )
    read_bytes = read_sectors * 512
    write_bytes = write_sectors * 512
    total_bytes = read_bytes + write_bytes
    return MachineBlockDeviceIODelta(
        device=ordered[0].device,
        major=ordered[0].major,
        minor=ordered[0].minor,
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at,
        last_observed_at=ordered[-1].observed_at,
        read_mib=round(read_bytes / _MIB, 1),
        write_mib=round(write_bytes / _MIB, 1),
        total_mib=round(total_bytes / _MIB, 1),
        avg_mib_s=round((total_bytes / _MIB) / elapsed_s, 1) if elapsed_s > 0 else None,
        read_iops=round(reads / elapsed_s, 1) if elapsed_s > 0 else None,
        write_iops=round(writes / elapsed_s, 1) if elapsed_s > 0 else None,
        io_time_ms_per_s=round(io_time_ms / elapsed_s, 1) if elapsed_s > 0 else None,
        weighted_io_time_ms_per_s=round(weighted_io_time_ms / elapsed_s, 1)
        if elapsed_s > 0
        else None,
    )


def _is_whole_block_device(device: str) -> bool:
    if re.match(r"nvme\d+n\d+$", device):
        return True
    if re.match(r"sd[a-z]+$", device):
        return True
    if re.match(r"vd[a-z]+$", device):
        return True
    if re.match(r"xvd[a-z]+$", device):
        return True
    if re.match(r"dm-\d+$", device):
        return True
    return False


def _classify_load_shape(
    pressure: MachineWindowPressureSummary,
    devices: tuple[MachineBlockDeviceIODelta, ...],
) -> MachineWindowLoadShape:
    busiest = max(devices, key=lambda row: row.avg_mib_s or 0.0, default=None)
    busiest_iops = None
    if busiest is not None:
        busiest_iops = (busiest.read_iops or 0.0) + (busiest.write_iops or 0.0)
    io_full = pressure.avg_io_psi_full_avg10 or pressure.max_io_psi_full_avg10 or 0.0
    mib_s = busiest.avg_mib_s if busiest and busiest.avg_mib_s is not None else 0.0
    weighted = (
        busiest.weighted_io_time_ms_per_s
        if busiest and busiest.weighted_io_time_ms_per_s is not None
        else 0.0
    )
    io_time = (
        busiest.io_time_ms_per_s
        if busiest and busiest.io_time_ms_per_s is not None
        else 0.0
    )
    if pressure.sample_count == 0:
        label = "unclassified_no_pressure_samples"
        reason = "no machine metric samples fell inside the requested window"
    elif not devices:
        label = "unclassified_no_block_device_samples"
        reason = (
            "pressure samples exist, but no block-device samples fell inside the window"
        )
    elif all(
        device.avg_mib_s is None
        and device.read_iops is None
        and device.write_iops is None
        and device.io_time_ms_per_s is None
        and device.weighted_io_time_ms_per_s is None
        for device in devices
    ):
        label = "unclassified_insufficient_block_device_deltas"
        reason = (
            "pressure samples exist, but block-device rows lack enough bracketing "
            "samples to compute throughput, IOPS, or wait-time deltas"
        )
    elif io_full < 10.0:
        label = "low_contention"
        reason = f"io.full avg10 average is {io_full:.1f}, below the 10% contention threshold"
    elif mib_s >= 150.0:
        label = "high_throughput_saturation"
        reason = f"io.full is high and busiest device throughput is {mib_s:.1f} MiB/s"
    elif mib_s < 50.0 and (weighted >= 1000.0 or (busiest_iops or 0.0) >= 100.0):
        label = "low_throughput_high_wait_contention"
        reason = (
            f"io.full is high while busiest device throughput is only {mib_s:.1f} MiB/s; "
            f"iops={busiest_iops or 0.0:.1f}, weighted_io_ms/s={weighted:.1f}"
        )
    else:
        label = "moderate_device_load_contention"
        reason = (
            f"io.full is high with busiest device throughput {mib_s:.1f} MiB/s, "
            f"iops={busiest_iops or 0.0:.1f}, io_time_ms/s={io_time:.1f}"
        )
    return MachineWindowLoadShape(
        label=label,
        reason=reason,
        busiest_device=busiest.device if busiest else None,
        busiest_device_mib_s=busiest.avg_mib_s if busiest else None,
        busiest_device_iops=round(busiest_iops, 1)
        if busiest_iops is not None
        else None,
        busiest_device_io_time_ms_per_s=busiest.io_time_ms_per_s if busiest else None,
        busiest_device_weighted_io_time_ms_per_s=busiest.weighted_io_time_ms_per_s
        if busiest
        else None,
    )


def _group_service_cgroup_io(
    samples: Iterable[MachineServiceCgroupIOSample],
) -> dict[tuple[str, str, int | None, int | None], list[MachineServiceCgroupIOSample]]:
    grouped: dict[
        tuple[str, str, int | None, int | None], list[MachineServiceCgroupIOSample]
    ] = {}
    for sample in samples:
        grouped.setdefault(
            (sample.scope, sample.unit, sample.major, sample.minor), []
        ).append(sample)
    return grouped


def _service_cgroup_io_delta(
    rows: list[MachineServiceCgroupIOSample],
    devices: tuple[MachineBlockDeviceIODelta, ...],
) -> MachineServiceCgroupIODelta:
    ordered = sorted(rows, key=lambda row: row.observed_at)
    read_bytes, read_reset = _positive_counter_delta([row.rbytes for row in ordered])
    write_bytes, write_reset = _positive_counter_delta([row.wbytes for row in ordered])
    read_ios, rios_reset = _positive_counter_delta([row.rios for row in ordered])
    write_ios, wios_reset = _positive_counter_delta([row.wios for row in ordered])
    caveats: list[str] = []
    if read_reset or write_reset or rios_reset or wios_reset:
        # Mirror _service_delta: unit restarts reset cgroup counters mid-window;
        # the totals stay correct (positive-delta summation) but the consumer
        # should know the window includes at least one restart.
        caveats.append("counter_reset_detected")
    elapsed_s = max(
        (ordered[-1].observed_at - ordered[0].observed_at).total_seconds(), 0.0
    )
    total_bytes = read_bytes + write_bytes
    total_ios = read_ios + write_ios
    device = _matching_device_delta(ordered[0].major, ordered[0].minor, devices)
    device_total_iops = None
    if (
        device is not None
        and device.read_iops is not None
        and device.write_iops is not None
    ):
        device_total_iops = device.read_iops + device.write_iops
    return MachineServiceCgroupIODelta(
        unit=ordered[0].unit,
        scope=ordered[0].scope,
        major=ordered[0].major,
        minor=ordered[0].minor,
        device=device.device if device else None,
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at,
        last_observed_at=ordered[-1].observed_at,
        read_mib=round(read_bytes / _MIB, 1),
        write_mib=round(write_bytes / _MIB, 1),
        total_mib=round(total_bytes / _MIB, 1),
        avg_mib_s=round((total_bytes / _MIB) / elapsed_s, 1) if elapsed_s > 0 else None,
        read_iops=round(read_ios / elapsed_s, 1) if elapsed_s > 0 else None,
        write_iops=round(write_ios / elapsed_s, 1) if elapsed_s > 0 else None,
        total_iops=round(total_ios / elapsed_s, 1) if elapsed_s > 0 else None,
        device_total_mib_pct=_pct(
            total_bytes / _MIB, device.total_mib if device else None
        ),
        disk_completed_iops_pct=_pct(
            total_ios / elapsed_s if elapsed_s > 0 else None,
            device_total_iops,
        ),
        caveats=tuple(caveats),
    )


def _group_service_cgroup_pressure(
    samples: Iterable[MachineServiceCgroupPressureSample],
) -> dict[tuple[str, str], list[MachineServiceCgroupPressureSample]]:
    grouped: dict[tuple[str, str], list[MachineServiceCgroupPressureSample]] = {}
    for sample in samples:
        grouped.setdefault((sample.scope, sample.unit), []).append(sample)
    return grouped


def _service_cgroup_pressure_summary(
    rows: list[MachineServiceCgroupPressureSample],
) -> MachineServiceCgroupPressureSummary:
    ordered = sorted(rows, key=lambda row: row.observed_at)
    return MachineServiceCgroupPressureSummary(
        unit=ordered[0].unit,
        scope=ordered[0].scope,
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at,
        last_observed_at=ordered[-1].observed_at,
        avg_io_full_avg10=_mean(_values(ordered, "io_full_avg10")),
        max_io_full_avg10=_max_float(_values(ordered, "io_full_avg10")),
        avg_memory_full_avg10=_mean(_values(ordered, "memory_full_avg10")),
        max_memory_full_avg10=_max_float(_values(ordered, "memory_full_avg10")),
        avg_cpu_some_avg10=_mean(_values(ordered, "cpu_some_avg10")),
        max_cpu_some_avg10=_max_float(_values(ordered, "cpu_some_avg10")),
    )


def _group_process_io_deltas(
    samples: Iterable[MachineProcessIODeltaSample],
) -> dict[tuple[int, int], list[MachineProcessIODeltaSample]]:
    grouped: dict[tuple[int, int], list[MachineProcessIODeltaSample]] = {}
    for sample in samples:
        grouped.setdefault(
            (sample.pid, sample.process_start_time_ticks), []
        ).append(sample)
    return grouped


def _process_io_delta_summary(
    rows: list[MachineProcessIODeltaSample],
) -> MachineProcessIODeltaSummary:
    ordered = sorted(rows, key=lambda row: row.observed_at)
    read_bytes = sum(row.read_bytes_delta for row in ordered)
    write_bytes = sum(row.write_bytes_delta for row in ordered)
    read_syscalls = sum(row.read_syscalls_delta for row in ordered)
    write_syscalls = sum(row.write_syscalls_delta for row in ordered)
    elapsed_s = sum(max(row.interval_s, 0.0) for row in ordered)
    total_bytes = read_bytes + write_bytes
    return MachineProcessIODeltaSummary(
        pid=ordered[0].pid,
        process_start_time_ticks=ordered[0].process_start_time_ticks,
        comm=ordered[0].comm,
        exe=ordered[0].exe,
        cgroup=ordered[0].cgroup,
        unit=ordered[0].unit,
        scope=ordered[0].scope,
        sample_count=len(ordered),
        first_observed_at=ordered[0].observed_at,
        last_observed_at=ordered[-1].observed_at,
        read_mib=round(read_bytes / _MIB, 1),
        write_mib=round(write_bytes / _MIB, 1),
        total_mib=round(total_bytes / _MIB, 1),
        read_syscalls=read_syscalls,
        write_syscalls=write_syscalls,
        total_syscalls=read_syscalls + write_syscalls,
        avg_total_mib_s=round((total_bytes / _MIB) / elapsed_s, 1)
        if elapsed_s > 0
        else None,
    )


def _matching_device_delta(
    major: int | None,
    minor: int | None,
    devices: tuple[MachineBlockDeviceIODelta, ...],
) -> MachineBlockDeviceIODelta | None:
    if major is None or minor is None:
        return None
    for device in devices:
        if device.major == major and device.minor == minor:
            return device
    return None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round((numerator / denominator) * 100.0, 1)


def _device_unattributed_io(
    devices: tuple[MachineBlockDeviceIODelta, ...],
    cgroup_rows: tuple[MachineServiceCgroupIODelta, ...],
) -> tuple[MachineDeviceUnattributedIO, ...]:
    attributed: dict[tuple[int | None, int | None], float] = {}
    for row in cgroup_rows:
        if row.major is None or row.minor is None:
            continue
        key = (row.major, row.minor)
        attributed[key] = attributed.get(key, 0.0) + row.total_mib
    results = []
    for device in devices:
        if device.major is None or device.minor is None:
            continue
        attributed_mib = round(attributed.get((device.major, device.minor), 0.0), 1)
        # Cgroup and diskstats counters live at different kernel layers, so
        # the residual can be slightly negative; clamp to zero because only
        # the positive gap means unobserved IO.
        unattributed = round(max(device.total_mib - attributed_mib, 0.0), 1)
        results.append(
            MachineDeviceUnattributedIO(
                device=device.device,
                major=device.major,
                minor=device.minor,
                device_total_mib=device.total_mib,
                attributed_total_mib=attributed_mib,
                unattributed_mib=unattributed,
                unattributed_pct=_pct(unattributed, device.total_mib),
            )
        )
    return tuple(sorted(results, key=lambda item: -item.unattributed_mib))


def _positive_counter_delta(values: Iterable[int | None]) -> tuple[int, bool]:
    previous: int | None = None
    total = 0
    reset = False
    for value in values:
        if value is None:
            continue
        current = int(value)
        if previous is None:
            previous = current
            continue
        delta = current - previous
        if delta >= 0:
            total += delta
        else:
            reset = True
            total += current
        previous = current
    return total, reset


def _values(rows: Iterable[Any], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = getattr(row, field)
        if value is not None:
            values.append(float(value))
    return values


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _max_float(values: list[float]) -> float | None:
    return round(max(values), 2) if values else None


def _max_int(values: list[float]) -> int | None:
    return int(max(values)) if values else None


def _ordered_distinct(values: Iterable[str | None]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


@dataclass(frozen=True)
class _BelowProcessPoint:
    observed_at: datetime | None
    key: str
    comm: str
    cgroup: str
    read_bps: float
    write_bps: float
    rw_bps: float
    cmdline: str


def _below_process_io_rates(
    start: datetime,
    end: datetime,
    *,
    below_binary: str,
    top_per_sample: int,
    limit: int,
) -> tuple[tuple[MachineBelowProcessIORate, ...], tuple[str, ...]]:
    command = [
        below_binary,
        "dump",
        "process",
        "-b",
        _below_time_arg(start),
        "-e",
        _below_time_arg(end),
        "-f",
        "datetime",
        "pid",
        "comm",
        "cgroup",
        "io.rbytes_per_sec",
        "io.wbytes_per_sec",
        "io.rwbytes_per_sec",
        "cmdline",
        "-O",
        "tsv",
        "--raw",
        "--disable-title",
        "-s",
        "io.rwbytes_per_sec",
        "--rsort",
        "--top",
        str(top_per_sample),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (), (f"below process dump failed: {exc}",)
    if proc.returncode != 0:
        detail = (
            proc.stderr.strip().splitlines()[-1]
            if proc.stderr.strip()
            else f"exit {proc.returncode}"
        )
        return (), (f"below process dump failed: {detail}",)
    grouped: dict[str, list[_BelowProcessPoint]] = {}
    rows = _parse_below_process_rows(proc.stdout)
    observed_sample_count = len(
        {row.observed_at for row in rows if row.observed_at is not None}
    )
    for row in rows:
        grouped.setdefault(row.key, []).append(row)
    summaries = [
        _below_process_summary(points, observed_sample_count)
        for points in grouped.values()
    ]
    summaries.sort(key=lambda row: (-row.estimated_total_bytes, row.key))
    caveats = (
        "below process rows are per-sample rates integrated over adjacent sample spacing; they are not cumulative counters",
        "below process rows are grouped by process comm, so multiple same-name processes are intentionally summed",
        f"below process dump used top {top_per_sample} processes per sample, so lower-rate contributors may be absent",
    )
    return tuple(summaries[:limit]), caveats


def _parse_below_process_rows(text: str) -> list[_BelowProcessPoint]:
    points: list[_BelowProcessPoint] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.rstrip("\t").split("\t")
        if len(cols) < 8:
            continue
        observed_at = _parse_below_datetime(cols[0])
        comm = cols[2].strip()
        cgroup = cols[3].strip()
        cmdline = cols[7].strip()
        key = comm or cmdline or cgroup
        points.append(
            _BelowProcessPoint(
                observed_at=observed_at,
                key=key,
                comm=comm,
                cgroup=cgroup,
                read_bps=_float_or_zero(cols[4]),
                write_bps=_float_or_zero(cols[5]),
                rw_bps=_float_or_zero(cols[6]),
                cmdline=cmdline,
            )
        )
    return points


def _below_process_summary(
    points: list[_BelowProcessPoint],
    observed_sample_count: int,
) -> MachineBelowProcessIORate:
    ordered = sorted(
        points,
        key=lambda row: row.observed_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    step_s = _median_step_seconds(ordered)
    read_bytes = round(sum(row.read_bps * step_s for row in ordered))
    write_bytes = round(sum(row.write_bps * step_s for row in ordered))
    total_bytes = round(sum(row.rw_bps * step_s for row in ordered))
    timestamps = [row.observed_at for row in ordered if row.observed_at is not None]
    first = ordered[0]
    max_rw = max((row.rw_bps for row in ordered), default=0.0)
    return MachineBelowProcessIORate(
        key=first.key,
        comm=first.comm,
        cgroup=first.cgroup,
        sample_count=len(ordered),
        sample_presence_pct=_below_sample_presence_pct(ordered, observed_sample_count),
        first_observed_at=min(timestamps) if timestamps else None,
        last_observed_at=max(timestamps) if timestamps else None,
        estimated_read_bytes=read_bytes,
        estimated_write_bytes=write_bytes,
        estimated_total_bytes=total_bytes,
        estimated_read_mib=round(read_bytes / _MIB, 1),
        estimated_write_mib=round(write_bytes / _MIB, 1),
        estimated_total_mib=round(total_bytes / _MIB, 1),
        max_rw_mib_s=round(max_rw / _MIB, 1),
        cmdline=first.cmdline,
    )


def _below_sample_presence_pct(
    points: Sequence[_BelowProcessPoint],
    observed_sample_count: int,
) -> float | None:
    if observed_sample_count <= 0:
        return None
    seen = {row.observed_at for row in points if row.observed_at is not None}
    return round((len(seen) / observed_sample_count) * 100.0, 1)


def _median_step_seconds(points: Sequence[_BelowProcessPoint]) -> float:
    timestamps = sorted(
        row.observed_at for row in points if row.observed_at is not None
    )
    deltas = [
        (right - left).total_seconds()
        for left, right in zip(timestamps, timestamps[1:], strict=False)
        if right > left
    ]
    return statistics.median(deltas) if deltas else 5.0


def _below_time_arg(value: datetime) -> str:
    return _as_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _parse_below_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _float_or_zero(value: str) -> float:
    try:
        return float(value)
    except ValueError:
        return 0.0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO timestamp: {value!r}") from exc


def _find_xtask_invocation(invocation_id: int, *, path: Path | None) -> XtaskInvocation:
    wanted_suffix = f":{invocation_id}"
    invocations = (
        iter_invocations(path=path) if path is not None else iter_all_invocations()
    )
    for invocation in invocations:
        if invocation.source_id.endswith(wanted_suffix):
            return invocation
    target = f" in {path}" if path is not None else ""
    raise ValueError(f"xtask invocation {invocation_id} not found{target}")


def _xtask_target(invocation: XtaskInvocation) -> MachineServiceIOWindowTarget:
    return MachineServiceIOWindowTarget(
        source="xtask_history",
        source_id=invocation.source_id,
        command=invocation.command,
        status=invocation.status,
        duration_s=invocation.duration_s,
        host_io_pressure_full_avg10_max=invocation.host_io_pressure_full_avg10_max,
        host_memory_pressure_full_avg10_max=invocation.host_memory_pressure_full_avg10_max,
    )


def _render_human(report: MachineServiceIOAttribution) -> str:
    pressure = report.pressure
    lines = [
        f"Window: {report.start.isoformat()} -> {report.end.isoformat()}",
        (
            "Pressure: "
            f"samples={pressure.sample_count} "
            f"io.full avg/max={_fmt(pressure.avg_io_psi_full_avg10)}/{_fmt(pressure.max_io_psi_full_avg10)} "
            f"io.some avg/max={_fmt(pressure.avg_io_psi_some_avg10)}/{_fmt(pressure.max_io_psi_some_avg10)} "
            f"mem.full avg/max={_fmt(pressure.avg_memory_psi_full_avg10)}/{_fmt(pressure.max_memory_psi_full_avg10)} "
            f"dstate avg/max={_fmt(pressure.avg_dstate_task_count)}/{_fmt(pressure.max_dstate_task_count)}"
        ),
    ]
    if report.target is not None:
        lines.append(
            "Target: "
            f"{report.target.source_id} "
            f"command={' '.join(report.target.command) or '-'} "
            f"status={report.target.status} "
            f"duration_s={_fmt(report.target.duration_s)} "
            f"xtask_io.full_max={_fmt(report.target.host_io_pressure_full_avg10_max)} "
            f"xtask_mem.full_max={_fmt(report.target.host_memory_pressure_full_avg10_max)}"
        )
    if report.load_shape is not None:
        lines.append(
            "Load shape: "
            f"{report.load_shape.label} "
            f"busiest_device={report.load_shape.busiest_device or '-'} "
            f"mib_s={_fmt(report.load_shape.busiest_device_mib_s)} "
            f"iops={_fmt(report.load_shape.busiest_device_iops)} "
            f"io_ms_s={_fmt(report.load_shape.busiest_device_io_time_ms_per_s)} "
            f"weighted_io_ms_s={_fmt(report.load_shape.busiest_device_weighted_io_time_ms_per_s)}"
        )
        lines.append(f"Load-shape reason: {report.load_shape.reason}")
    if report.block_devices:
        lines.extend(
            (
                "",
                f"{'block device':14} {'samples':>7} {'read MiB':>10} {'write MiB':>10} {'MiB/s':>8} {'r IOPS':>8} {'w IOPS':>8} {'io ms/s':>9} {'weighted ms/s':>13}",
            )
        )
        for device in report.block_devices[:8]:
            lines.append(
                f"{device.device[:14]:14} {device.sample_count:7d} "
                f"{device.read_mib:10.1f} {device.write_mib:10.1f} {_fmt(device.avg_mib_s):>8} "
                f"{_fmt(device.read_iops):>8} {_fmt(device.write_iops):>8} "
                f"{_fmt(device.io_time_ms_per_s):>9} {_fmt(device.weighted_io_time_ms_per_s):>13}"
            )
    lines.extend(
        (
            "",
            f"{'unit':42} {'scope':8} {'samples':>7} {'read MiB':>10} {'write MiB':>10} {'total MiB':>10} states",
        )
    )
    for service in report.services:
        states = "/".join(service.active_states) or "-"
        lines.append(
            f"{service.unit[:42]:42} {service.scope[:8]:8} {service.sample_count:7d} "
            f"{service.read_mib:10.1f} {service.write_mib:10.1f} {service.total_mib:10.1f} {states}"
        )
    if not report.services:
        lines.append("(no service counter growth above threshold)")
    if report.service_cgroup_io:
        lines.extend(
            (
                "",
                f"{'service cgroup io':36} {'dev':>14} {'maj:min':>9} {'samples':>7} {'read MiB':>10} {'write MiB':>10} {'MiB/s':>8} {'IOPS':>8} {'%dev MiB':>8} {'%disk ops':>9}",
            )
        )
        for row in report.service_cgroup_io[:12]:
            major_minor = f"{row.major}:{row.minor}"
            device = row.device or "-"
            lines.append(
                f"{row.unit[:36]:36} {device[:14]:>14} {major_minor:>9} {row.sample_count:7d} "
                f"{row.read_mib:10.1f} {row.write_mib:10.1f} {_fmt(row.avg_mib_s):>8} "
                f"{_fmt(row.total_iops):>8} {_fmt(row.device_total_mib_pct):>8} {_fmt(row.disk_completed_iops_pct):>9}"
            )
    if report.service_cgroup_pressure:
        lines.extend(
            (
                "",
                f"{'service cgroup pressure':36} {'scope':8} {'samples':>7} {'io.full avg/max':>16} {'mem.full avg/max':>17} {'cpu.some avg/max':>17}",
            )
        )
        for row in report.service_cgroup_pressure[:12]:
            lines.append(
                f"{row.unit[:36]:36} {row.scope[:8]:8} {row.sample_count:7d} "
                f"{_fmt(row.avg_io_full_avg10)}/{_fmt(row.max_io_full_avg10):>16} "
                f"{_fmt(row.avg_memory_full_avg10)}/{_fmt(row.max_memory_full_avg10):>17} "
                f"{_fmt(row.avg_cpu_some_avg10)}/{_fmt(row.max_cpu_some_avg10):>17}"
            )
    if report.process_io_deltas:
        lines.extend(
            (
                "",
                f"{'process delta':32} {'unit':32} {'samples':>7} {'read MiB':>10} {'write MiB':>10} {'total MiB':>10} {'syscalls':>9} {'MiB/s':>8}",
            )
        )
        for process in report.process_io_deltas[:12]:
            label = process.comm or process.exe or str(process.pid)
            unit = process.unit or "-"
            lines.append(
                f"{label[:32]:32} {unit[:32]:32} {process.sample_count:7d} "
                f"{process.read_mib:10.1f} {process.write_mib:10.1f} "
                f"{process.total_mib:10.1f} {process.total_syscalls:9d} "
                f"{_fmt(process.avg_total_mib_s):>8}"
            )
    if report.below_processes:
        lines.extend(
            (
                "",
                f"{'below process':42} {'samples':>7} {'seen %':>7} {'read MiB':>10} {'write MiB':>10} {'total MiB':>10} {'max MiB/s':>10}",
            )
        )
        for process in report.below_processes:
            lines.append(
                f"{process.comm[:42]:42} {process.sample_count:7d} {_fmt(process.sample_presence_pct):>7} "
                f"{process.estimated_read_mib:10.1f} {process.estimated_write_mib:10.1f} "
                f"{process.estimated_total_mib:10.1f} {_fmt(process.max_rw_mib_s):>10}"
            )
    lines.append("")
    lines.extend(f"Caveat: {caveat}" for caveat in report.caveats)
    lines.extend(f"Below caveat: {error}" for error in report.below_errors)
    return "\n".join(lines)


def _fmt(value: float | int | None) -> str:
    return "-" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", type=_parse_datetime, help="Window start ISO timestamp"
    )
    parser.add_argument("--end", type=_parse_datetime, help="Window end ISO timestamp")
    parser.add_argument(
        "--xtask-invocation",
        type=int,
        help="Resolve start/end from a Sinex xtask invocation id",
    )
    parser.add_argument(
        "--xtask-history-path",
        type=Path,
        default=None,
        help="Sinex xtask history SQLite path",
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="Machine telemetry SQLite path"
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Maximum services to display"
    )
    parser.add_argument(
        "--min-total-mib", type=float, default=0.0, help="Minimum total service I/O MiB"
    )
    parser.add_argument(
        "--below-processes",
        action="store_true",
        help="Include Below top-process I/O-rate aggregation",
    )
    parser.add_argument(
        "--below-binary", default="below", help="Below binary to execute"
    )
    parser.add_argument(
        "--below-top-per-sample",
        type=int,
        default=20,
        help="Below process rows retained per sample",
    )
    parser.add_argument("--json", action="store_true", help="Render structured JSON")
    args = parser.parse_args(argv)

    if args.xtask_invocation is not None:
        report = analyze_machine_service_io_for_xtask_invocation(
            args.xtask_invocation,
            xtask_history_path=args.xtask_history_path,
            machine_path=args.path,
            limit=args.limit,
            min_total_mib=args.min_total_mib,
            include_below_processes=args.below_processes,
            below_binary=args.below_binary,
            below_top_per_sample=args.below_top_per_sample,
        )
    else:
        if args.start is None or args.end is None:
            parser.error(
                "either --xtask-invocation or both --start and --end are required"
            )
        report = analyze_machine_service_io_window(
            start=args.start,
            end=args.end,
            path=args.path,
            limit=args.limit,
            min_total_mib=args.min_total_mib,
            include_below_processes=args.below_processes,
            below_binary=args.below_binary,
            below_top_per_sample=args.below_top_per_sample,
        )
    if args.json:
        print(json.dumps(report.to_dict(), default=str, indent=2, sort_keys=True))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
