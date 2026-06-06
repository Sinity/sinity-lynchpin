"""Rank slow xtask invocations and attribute their host contention windows."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

from lynchpin.analysis.machine.service_io import (
    MachineServiceIOWindowTarget,
    analyze_machine_service_io_window,
)
from lynchpin.sources.xtask_history import XtaskInvocation, iter_all_invocations


@dataclass(frozen=True)
class XtaskContentionProcessSummary:
    comm: str
    estimated_total_mib: float
    sample_presence_pct: float | None
    max_rw_mib_s: float | None


@dataclass(frozen=True)
class XtaskContentionProcessDeltaSummary:
    comm: str | None
    unit: str | None
    total_mib: float
    total_syscalls: int
    avg_total_mib_s: float | None


@dataclass(frozen=True)
class XtaskContentionServiceSummary:
    unit: str
    scope: str
    total_mib: float
    read_mib: float
    write_mib: float


@dataclass(frozen=True)
class XtaskContentionBlockDeviceSummary:
    device: str
    total_mib: float
    avg_mib_s: float | None
    read_iops: float | None
    write_iops: float | None
    weighted_io_time_ms_per_s: float | None


@dataclass(frozen=True)
class XtaskInvocationBlockIOSummary:
    read_mib_delta: float | None
    write_mib_delta: float | None
    read_iops_avg: float | None
    write_iops_avg: float | None
    busiest_device: str | None
    busiest_device_total_mib_delta: float | None
    busiest_device_read_iops_avg: float | None
    busiest_device_write_iops_avg: float | None
    busiest_device_weighted_io_ms_per_s: float | None


@dataclass(frozen=True)
class XtaskContentionRow:
    source_id: str
    command: tuple[str, ...]
    args_json: str
    started_at: datetime
    ended_at: datetime
    duration_s: float
    status: str
    exit_code: int | None
    xtask_io_full_max: float | None
    xtask_memory_full_max: float | None
    machine_io_full_avg: float | None
    machine_io_full_max: float | None
    machine_memory_full_avg: float | None
    machine_memory_full_max: float | None
    load_shape_label: str | None
    load_shape_reason: str | None
    xtask_block_io: XtaskInvocationBlockIOSummary
    top_services: tuple[XtaskContentionServiceSummary, ...]
    top_block_devices: tuple[XtaskContentionBlockDeviceSummary, ...]
    top_process_deltas: tuple[XtaskContentionProcessDeltaSummary, ...]
    top_processes: tuple[XtaskContentionProcessSummary, ...]
    sustained_processes: tuple[XtaskContentionProcessSummary, ...]
    interpretation: str


@dataclass(frozen=True)
class XtaskContentionReport:
    generated_at: datetime
    start: datetime
    end: datetime
    inspected_invocation_count: int
    row_count: int
    rows: tuple[XtaskContentionRow, ...]
    retrospective_fact_classes: tuple[str, ...]
    not_proven_retrospectively: tuple[str, ...]
    forward_capture_gaps: tuple[str, ...]
    caveats: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_xtask_contention(
    *,
    start: datetime,
    end: datetime,
    command: str | None = None,
    limit: int = 10,
    min_duration_s: float = 30.0,
    min_io_full_max: float = 0.0,
    include_failures: bool = True,
    machine_path: Path | None = None,
    include_below_processes: bool = False,
    below_top_per_sample: int = 12,
) -> XtaskContentionReport:
    """Rank recent xtask invocations by duration and host pressure, with attribution."""
    start = _as_utc(start)
    end = _as_utc(end)
    candidates = [
        invocation
        for invocation in iter_all_invocations(start=start, end=end)
        if _matches_invocation(
            invocation,
            command=command,
            min_duration_s=min_duration_s,
            min_io_full_max=min_io_full_max,
            include_failures=include_failures,
        )
    ]
    ranked = sorted(candidates, key=_rank_key)[:limit]
    rows = tuple(
        _contention_row(
            invocation,
            machine_path=machine_path,
            include_below_processes=include_below_processes,
            below_top_per_sample=below_top_per_sample,
        )
        for invocation in ranked
        if invocation.ended_at is not None and invocation.duration_s is not None
    )
    return XtaskContentionReport(
        generated_at=datetime.now(timezone.utc),
        start=start,
        end=end,
        inspected_invocation_count=len(candidates),
        row_count=len(rows),
        rows=rows,
        retrospective_fact_classes=_retrospective_fact_classes(include_below_processes),
        not_proven_retrospectively=_not_proven_retrospectively(include_below_processes),
        forward_capture_gaps=_forward_capture_gaps(),
        caveats=(
            "xtask history supplies invocation intervals, per-invocation PSI/resource maxima, and xtask-sampled aggregate block-device counters when the source schema has those columns",
            "machine service, cgroup, and block-device counters are sampled cumulative counters; deltas use nearest bracketing samples and describe the sample envelope around each invocation",
            "xtask-sampled block counters quantify host aggregate device load during the invocation; they do not partition unrelated service ownership",
            "persisted process I/O deltas are bounded top-N heartbeat samples scaled by overlap with each invocation window",
            "Below process rows are sampled top-N rate estimates; use sample_presence_pct to distinguish sustained contributors from brief spikes",
        ),
    )


def _retrospective_fact_classes(include_below_processes: bool) -> tuple[str, ...]:
    facts = [
        "xtask_history.invocations: exact invocation start/end timestamps, duration, command tuple, args_json, status/exit_code, xtask-sampled host PSI/resource maxima, and aggregate whole-device block counters when present",
        "machine.metric_sample: sampled host PSI totals/avg windows, load, memory/swap availability, D-state task count/wchan summary, scheduler oversleep, power/thermal/GPU fields at the collector cadence",
        "machine.block_device_sample: sampled /proc/diskstats counters by block device; adjacent deltas prove device throughput, IOPS, io_time_ms, and weighted_io_time_ms growth inside the interval when the table is present",
        "machine.service_state: sampled systemd unit ActiveState/SubState/MainPID/ControlGroup plus cumulative MemoryCurrent, CPUUsageNSec, IOReadBytes, and IOWriteBytes; positive adjacent counter deltas prove unit counter growth in the interval",
        "machine.service_cgroup_io_sample: sampled cgroup v2 io.stat rows for configured services; adjacent deltas prove per-service per-device bytes and I/O counts when the table is present",
        "machine.process_io_delta_sample: bounded top-N /proc/<pid>/io deltas persisted by machine telemetry; proves retained per-process byte and syscall deltas when the table is present",
    ]
    if include_below_processes:
        facts.append(
            "below dump process: sampled top-N per-process I/O rates; integrated estimates and sample_presence_pct prove which process names repeatedly appeared in retained top-N samples"
        )
    return tuple(facts)


def _not_proven_retrospectively(include_below_processes: bool) -> tuple[str, ...]:
    limits = [
        "per-process cumulative I/O for processes absent from persisted process_io_delta_sample top-N rows",
        "whether a unit's byte-counter growth caused cargo latency rather than merely overlapping it",
        "page-cache hit/miss rates, writeback ownership, or kernel worker work attribution below process/cgroup names",
        "block-device deltas before the block_device_sample table was deployed",
        "service cgroup I/O deltas before the service_cgroup_io_sample table was deployed",
        "process I/O deltas before the process_io_delta_sample table was deployed, and process activity outside retained top-N rows after deployment",
        "service ownership of xtask-sampled block counters; those counters are aggregate host/device shape only",
    ]
    if not include_below_processes:
        limits.append(
            "Below process-rate corroboration; rerun with --below-processes when persisted process deltas need an independent sampled-rate cross-check"
        )
    return tuple(limits)


def _forward_capture_gaps() -> tuple[str, ...]:
    return (
        "persist per-cgroup io.stat recursive deltas for all relevant slices, not only configured systemd units",
        "expand process I/O attribution beyond bounded top-N rows if retained samples still miss important short-lived rustc/cargo/postgres contributors",
    )


def _matches_invocation(
    invocation: XtaskInvocation,
    *,
    command: str | None,
    min_duration_s: float,
    min_io_full_max: float,
    include_failures: bool,
) -> bool:
    if invocation.ended_at is None or invocation.duration_s is None:
        return False
    if command is not None and command not in invocation.command:
        return False
    if not include_failures and invocation.status != "success":
        return False
    if invocation.duration_s < min_duration_s:
        return False
    io_full = invocation.host_io_pressure_full_avg10_max or 0.0
    return io_full >= min_io_full_max


def _rank_key(invocation: XtaskInvocation) -> tuple[float, float, datetime]:
    duration = invocation.duration_s or 0.0
    io_full = invocation.host_io_pressure_full_avg10_max or 0.0
    return (-duration, -io_full, invocation.started_at)


def _contention_row(
    invocation: XtaskInvocation,
    *,
    machine_path: Path | None,
    include_below_processes: bool,
    below_top_per_sample: int,
) -> XtaskContentionRow:
    assert invocation.ended_at is not None
    assert invocation.duration_s is not None
    attribution = analyze_machine_service_io_window(
        start=invocation.started_at,
        end=invocation.ended_at,
        path=machine_path,
        limit=8,
        min_total_mib=0.1,
        target=MachineServiceIOWindowTarget(
            source="xtask_history",
            source_id=invocation.source_id,
            command=invocation.command,
            status=invocation.status,
            duration_s=invocation.duration_s,
            host_io_pressure_full_avg10_max=invocation.host_io_pressure_full_avg10_max,
            host_memory_pressure_full_avg10_max=invocation.host_memory_pressure_full_avg10_max,
        ),
        include_below_processes=include_below_processes,
        below_top_per_sample=below_top_per_sample,
    )
    top_services = tuple(
        XtaskContentionServiceSummary(
            unit=row.unit,
            scope=row.scope,
            total_mib=row.total_mib,
            read_mib=row.read_mib,
            write_mib=row.write_mib,
        )
        for row in attribution.services[:3]
    )
    top_devices = tuple(
        XtaskContentionBlockDeviceSummary(
            device=row.device,
            total_mib=row.total_mib,
            avg_mib_s=row.avg_mib_s,
            read_iops=row.read_iops,
            write_iops=row.write_iops,
            weighted_io_time_ms_per_s=row.weighted_io_time_ms_per_s,
        )
        for row in attribution.block_devices[:3]
    )
    top_processes = tuple(
        XtaskContentionProcessSummary(
            comm=row.comm,
            estimated_total_mib=row.estimated_total_mib,
            sample_presence_pct=row.sample_presence_pct,
            max_rw_mib_s=row.max_rw_mib_s,
        )
        for row in attribution.below_processes[:5]
    )
    top_process_deltas = tuple(
        XtaskContentionProcessDeltaSummary(
            comm=row.comm,
            unit=row.unit,
            total_mib=row.total_mib,
            total_syscalls=row.total_syscalls,
            avg_total_mib_s=row.avg_total_mib_s,
        )
        for row in attribution.process_io_deltas[:5]
    )
    sustained_processes = tuple(
        row
        for row in top_processes
        if row.sample_presence_pct is not None and row.sample_presence_pct >= 50.0
    )
    return XtaskContentionRow(
        source_id=invocation.source_id,
        command=invocation.command,
        args_json=invocation.args_json,
        started_at=_as_utc(invocation.started_at),
        ended_at=_as_utc(invocation.ended_at),
        duration_s=round(invocation.duration_s, 1),
        status=invocation.status,
        exit_code=invocation.exit_code,
        xtask_io_full_max=invocation.host_io_pressure_full_avg10_max,
        xtask_memory_full_max=invocation.host_memory_pressure_full_avg10_max,
        machine_io_full_avg=attribution.pressure.avg_io_psi_full_avg10,
        machine_io_full_max=attribution.pressure.max_io_psi_full_avg10,
        machine_memory_full_avg=attribution.pressure.avg_memory_psi_full_avg10,
        machine_memory_full_max=attribution.pressure.max_memory_psi_full_avg10,
        load_shape_label=attribution.load_shape.label
        if attribution.load_shape
        else None,
        load_shape_reason=attribution.load_shape.reason
        if attribution.load_shape
        else None,
        xtask_block_io=_xtask_block_summary(invocation),
        top_services=top_services,
        top_block_devices=top_devices,
        top_process_deltas=top_process_deltas,
        top_processes=top_processes,
        sustained_processes=sustained_processes,
        interpretation=_interpret(
            top_services,
            top_devices,
            attribution.load_shape.label if attribution.load_shape else None,
            _xtask_block_summary(invocation),
            top_process_deltas,
            sustained_processes,
            top_processes,
        ),
    )


def _xtask_block_summary(invocation: XtaskInvocation) -> XtaskInvocationBlockIOSummary:
    return XtaskInvocationBlockIOSummary(
        read_mib_delta=invocation.host_block_read_mib_delta,
        write_mib_delta=invocation.host_block_write_mib_delta,
        read_iops_avg=invocation.host_block_read_iops_avg,
        write_iops_avg=invocation.host_block_write_iops_avg,
        busiest_device=invocation.host_block_busiest_device,
        busiest_device_total_mib_delta=invocation.host_block_busiest_device_total_mib_delta,
        busiest_device_read_iops_avg=invocation.host_block_busiest_device_read_iops_avg,
        busiest_device_write_iops_avg=invocation.host_block_busiest_device_write_iops_avg,
        busiest_device_weighted_io_ms_per_s=invocation.host_block_busiest_device_weighted_io_ms_per_s,
    )


def _interpret(
    services: tuple[XtaskContentionServiceSummary, ...],
    devices: tuple[XtaskContentionBlockDeviceSummary, ...],
    load_shape_label: str | None,
    xtask_block_io: XtaskInvocationBlockIOSummary,
    process_deltas: tuple[XtaskContentionProcessDeltaSummary, ...],
    sustained_processes: tuple[XtaskContentionProcessSummary, ...],
    top_processes: tuple[XtaskContentionProcessSummary, ...],
) -> str:
    parts: list[str] = []
    if load_shape_label:
        parts.append(f"load shape: {load_shape_label}")
    if devices:
        iops = (devices[0].read_iops or 0.0) + (devices[0].write_iops or 0.0)
        parts.append(
            f"top device: {devices[0].device} {devices[0].avg_mib_s or 0.0:.1f} MiB/s {iops:.1f} IOPS"
        )
    if xtask_block_io.busiest_device:
        total_mib = xtask_block_io.busiest_device_total_mib_delta or 0.0
        iops = (xtask_block_io.busiest_device_read_iops_avg or 0.0) + (
            xtask_block_io.busiest_device_write_iops_avg or 0.0
        )
        parts.append(
            f"xtask-sampled device: {xtask_block_io.busiest_device} {total_mib:.1f} MiB {iops:.1f} IOPS"
        )
    if services:
        parts.append(
            f"top service counter delta: {services[0].unit} {services[0].total_mib:.1f} MiB"
        )
    if process_deltas:
        label = process_deltas[0].comm or process_deltas[0].unit or "unknown"
        parts.append(
            f"top persisted process delta: {label} {process_deltas[0].total_mib:.1f} MiB {process_deltas[0].total_syscalls} syscalls"
        )
    if sustained_processes:
        names = ", ".join(
            f"{row.comm} {row.sample_presence_pct:.1f}%"
            for row in sustained_processes[:3]
        )
        parts.append(f"sustained Below top-N processes: {names}")
    elif top_processes:
        parts.append(
            "Below contributors were low-presence spikes, not sustained top-N processes"
        )
    if not parts:
        return "no service/process attribution above thresholds for this window"
    return "; ".join(parts)


def _render_human(report: XtaskContentionReport) -> str:
    lines = [
        f"Window: {report.start.isoformat()} -> {report.end.isoformat()}",
        f"Rows: {report.row_count} of {report.inspected_invocation_count} matching invocations",
        "",
        "Retrospective facts this report can use:",
        *[f"- {fact}" for fact in report.retrospective_fact_classes],
        "",
        "Not proven retrospectively by these sources:",
        *[f"- {limit}" for limit in report.not_proven_retrospectively],
        "",
        (
            f"{'invocation':18} {'cmd':8} {'status':8} {'duration':>9} "
            f"{'x_io':>7} {'x_blk_dev':>10} {'m_io_avg/max':>15} {'load shape':28} {'top service':32} interpretation"
        ),
    ]
    for row in report.rows:
        service = row.top_services[0] if row.top_services else None
        service_text = (
            f"{service.unit[:20]} {service.total_mib:.0f}MiB" if service else "-"
        )
        load_shape = row.load_shape_label or "-"
        xtask_block = row.xtask_block_io.busiest_device or "-"
        lines.append(
            f"{row.source_id[-18:]:18} {' '.join(row.command)[:8]:8} {row.status[:8]:8} "
            f"{row.duration_s:9.1f} {_fmt(row.xtask_io_full_max):>7} {xtask_block[:10]:>10} "
            f"{_fmt(row.machine_io_full_avg)}/{_fmt(row.machine_io_full_max):<7} "
            f"{load_shape[:28]:28} "
            f"{service_text[:32]:32} {row.interpretation}"
        )
    if not report.rows:
        lines.append("(no matching completed invocations)")
    lines.append("")
    lines.extend(f"Caveat: {caveat}" for caveat in report.caveats)
    return "\n".join(lines)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid ISO timestamp: {value!r}") from exc


def _fmt(value: float | int | None) -> str:
    return "-" if value is None else str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", type=_parse_datetime, default=None, help="Window start ISO timestamp"
    )
    parser.add_argument(
        "--end", type=_parse_datetime, default=None, help="Window end ISO timestamp"
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Lookback hours when --start is omitted",
    )
    parser.add_argument(
        "--command",
        default=None,
        help="Restrict to invocations whose command tuple contains this value",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Maximum invocations to attribute"
    )
    parser.add_argument(
        "--min-duration-s", type=float, default=30.0, help="Minimum invocation duration"
    )
    parser.add_argument(
        "--min-io-full-max", type=float, default=0.0, help="Minimum xtask io.full max"
    )
    parser.add_argument(
        "--success-only", action="store_true", help="Exclude failed invocations"
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="Machine telemetry SQLite path"
    )
    parser.add_argument(
        "--below-processes",
        action="store_true",
        help="Include Below top-process I/O attribution",
    )
    parser.add_argument(
        "--below-top-per-sample",
        type=int,
        default=12,
        help="Below process rows retained per sample",
    )
    parser.add_argument("--json", action="store_true", help="Render structured JSON")
    args = parser.parse_args(argv)

    end = args.end or datetime.now(timezone.utc)
    start = args.start or (end - timedelta(hours=args.hours))
    report = analyze_xtask_contention(
        start=start,
        end=end,
        command=args.command,
        limit=args.limit,
        min_duration_s=args.min_duration_s,
        min_io_full_max=args.min_io_full_max,
        include_failures=not args.success_only,
        machine_path=args.path,
        include_below_processes=args.below_processes,
        below_top_per_sample=args.below_top_per_sample,
    )
    if args.json:
        print(json.dumps(report.to_dict(), default=str, indent=2, sort_keys=True))
    else:
        print(_render_human(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
