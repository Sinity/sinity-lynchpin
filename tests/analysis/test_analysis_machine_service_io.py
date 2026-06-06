from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.analysis.machine import service_io
from lynchpin.analysis.machine.service_io import (
    analyze_machine_service_io_for_xtask_invocation,
    analyze_machine_service_io_window,
)
from lynchpin.sources import machine as machine_source
from lynchpin.sources.machine_models import (
    MachineBlockDeviceSample,
    MachineMetricSample,
    MachineProcessIODeltaSample,
    MachineServiceCgroupIOSample,
    MachineServiceCgroupPressureSample,
    MachineServiceState,
)
from lynchpin.sources.xtask_history import XtaskInvocation


def _ts(second: int) -> datetime:
    return datetime(2026, 6, 6, 12, 0, second, tzinfo=timezone.utc)


def _metric(
    second: int,
    *,
    io_psi_full_avg10: float | None = None,
    io_psi_some_avg10: float | None = None,
    memory_psi_full_avg10: float | None = None,
    dstate_task_count: int | None = None,
) -> MachineMetricSample:
    return MachineMetricSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source="machine.telemetry",
        source_schema_version=2,
        io_psi_full_avg10=io_psi_full_avg10,
        io_psi_some_avg10=io_psi_some_avg10,
        memory_psi_full_avg10=memory_psi_full_avg10,
        dstate_task_count=dstate_task_count,
    )


def _state(
    unit: str, second: int, read: int | None, write: int | None = 0
) -> MachineServiceState:
    return MachineServiceState(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        unit=unit,
        scope="system",
        active_state="active",
        sub_state="running",
        io_read_bytes=read,
        io_write_bytes=write,
    )


def _block_device(
    device: str,
    second: int,
    *,
    sectors_read: int,
    sectors_written: int,
    reads_completed: int,
    writes_completed: int,
    io_time_ms: int,
    weighted_io_time_ms: int,
) -> MachineBlockDeviceSample:
    return MachineBlockDeviceSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source_schema_version=2,
        major=259,
        minor=0,
        device=device,
        sectors_read=sectors_read,
        sectors_written=sectors_written,
        reads_completed=reads_completed,
        writes_completed=writes_completed,
        io_time_ms=io_time_ms,
        weighted_io_time_ms=weighted_io_time_ms,
    )


def _cgroup_io(
    unit: str,
    second: int,
    *,
    rbytes: int,
    wbytes: int,
    rios: int,
    wios: int,
) -> MachineServiceCgroupIOSample:
    return MachineServiceCgroupIOSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source_schema_version=2,
        unit=unit,
        scope="system",
        control_group=f"/system.slice/{unit}",
        major=259,
        minor=0,
        rbytes=rbytes,
        wbytes=wbytes,
        rios=rios,
        wios=wios,
    )


def _cgroup_pressure(
    unit: str,
    second: int,
    *,
    io_full_avg10: float | None = None,
    memory_full_avg10: float | None = None,
    cpu_some_avg10: float | None = None,
) -> MachineServiceCgroupPressureSample:
    return MachineServiceCgroupPressureSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source_schema_version=2,
        unit=unit,
        scope="system",
        control_group=f"/system.slice/{unit}",
        cpu_some_avg10=cpu_some_avg10,
        io_full_avg10=io_full_avg10,
        memory_full_avg10=memory_full_avg10,
    )


def _process_io(
    comm: str,
    second: int,
    *,
    read_bytes_delta: int,
    write_bytes_delta: int,
    total_syscalls_delta: int,
    interval_s: float = 10.0,
) -> MachineProcessIODeltaSample:
    return MachineProcessIODeltaSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source_schema_version=3,
        interval_s=interval_s,
        pid=123,
        process_start_time_ticks=456,
        comm=comm,
        exe=f"/nix/store/{comm}/bin/{comm}",
        cgroup="/user.slice/user-1000.slice/session.scope",
        unit="session.scope",
        scope="user",
        read_bytes_delta=read_bytes_delta,
        write_bytes_delta=write_bytes_delta,
        cancelled_write_bytes_delta=0,
        read_chars_delta=0,
        write_chars_delta=0,
        read_syscalls_delta=0,
        write_syscalls_delta=total_syscalls_delta,
        total_bytes_delta=read_bytes_delta + write_bytes_delta,
        total_syscalls_delta=total_syscalls_delta,
    )


@pytest.fixture(autouse=True)
def _empty_new_machine_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(machine_source, "block_device_samples", lambda **_: iter(()))
    monkeypatch.setattr(
        machine_source, "service_cgroup_io_samples", lambda **_: iter(())
    )
    monkeypatch.setattr(
        machine_source, "service_cgroup_pressure_samples", lambda **_: iter(())
    )
    monkeypatch.setattr(machine_source, "process_io_delta_samples", lambda **_: iter(()))


def test_service_io_window_ranks_positive_deltas_and_handles_resets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [
        _metric(
            0,
            io_psi_full_avg10=10.0,
            io_psi_some_avg10=20.0,
            memory_psi_full_avg10=2.0,
            dstate_task_count=1,
        ),
        _metric(
            10,
            io_psi_full_avg10=30.0,
            io_psi_some_avg10=40.0,
            memory_psi_full_avg10=6.0,
            dstate_task_count=5,
        ),
    ]
    states = [
        _state("alpha.service", 0, 100, 0),
        _state("alpha.service", 10, 150, 10),
        _state("alpha.service", 20, 10, 2),
        _state("alpha.service", 30, 25, 5),
        _state("beta.service", 0, 0, 0),
        _state("beta.service", 30, 4 * 1024 * 1024, 0),
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))

    report = analyze_machine_service_io_window(
        start=_ts(0), end=_ts(30), min_total_mib=0.0
    )

    assert report.pressure.sample_count == 2
    assert report.pressure.avg_io_psi_full_avg10 == 20.0
    assert report.pressure.max_dstate_task_count == 5
    assert [row.unit for row in report.services] == ["beta.service", "alpha.service"]
    alpha = next(row for row in report.services if row.unit == "alpha.service")
    assert alpha.read_bytes_delta == 75
    assert alpha.write_bytes_delta == 15
    assert alpha.total_bytes_delta == 90
    assert alpha.caveats == ("counter_reset_detected",)


def test_service_io_classifies_low_throughput_high_wait_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [
        _metric(0, io_psi_full_avg10=35.0, io_psi_some_avg10=40.0),
        _metric(10, io_psi_full_avg10=45.0, io_psi_some_avg10=50.0),
    ]
    states = [
        _state("transmission.service", 0, 0),
        _state("transmission.service", 10, 1024),
    ]
    devices = [
        _block_device(
            "nvme0n1",
            0,
            sectors_read=0,
            sectors_written=0,
            reads_completed=0,
            writes_completed=0,
            io_time_ms=0,
            weighted_io_time_ms=0,
        ),
        _block_device(
            "nvme0n1",
            10,
            sectors_read=20_480,
            sectors_written=20_480,
            reads_completed=2000,
            writes_completed=2000,
            io_time_ms=8000,
            weighted_io_time_ms=25_000,
        ),
    ]
    cgroup_io = [
        _cgroup_io("transmission.service", 0, rbytes=0, wbytes=0, rios=0, wios=0),
        _cgroup_io(
            "transmission.service",
            10,
            rbytes=1024 * 1024,
            wbytes=0,
            rios=500,
            wios=0,
        ),
    ]
    cgroup_pressure = [
        _cgroup_pressure(
            "transmission.service",
            0,
            io_full_avg10=3.0,
            memory_full_avg10=1.0,
            cpu_some_avg10=6.0,
        ),
        _cgroup_pressure(
            "transmission.service",
            10,
            io_full_avg10=9.0,
            memory_full_avg10=5.0,
            cpu_some_avg10=12.0,
        ),
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))
    monkeypatch.setattr(
        machine_source, "block_device_samples", lambda **_: iter(devices)
    )
    monkeypatch.setattr(
        machine_source, "service_cgroup_io_samples", lambda **_: iter(cgroup_io)
    )
    monkeypatch.setattr(
        machine_source,
        "service_cgroup_pressure_samples",
        lambda **_: iter(cgroup_pressure),
    )

    report = analyze_machine_service_io_window(
        start=_ts(0), end=_ts(10), min_total_mib=0.0
    )

    assert report.load_shape is not None
    assert report.load_shape.label == "low_throughput_high_wait_contention"
    assert report.block_devices[0].avg_mib_s == 2.0
    assert report.block_devices[0].read_iops == 200.0
    assert report.service_cgroup_io[0].unit == "transmission.service"
    assert report.service_cgroup_io[0].device == "nvme0n1"
    assert report.service_cgroup_io[0].read_iops == 50.0
    assert report.service_cgroup_io[0].device_total_mib_pct == 5.0
    assert report.service_cgroup_io[0].disk_completed_iops_pct == 12.5
    assert report.service_cgroup_pressure[0].unit == "transmission.service"
    assert report.service_cgroup_pressure[0].avg_io_full_avg10 == 6.0
    assert report.service_cgroup_pressure[0].max_memory_full_avg10 == 5.0
    assert report.service_cgroup_pressure[0].max_cpu_some_avg10 == 12.0


def test_service_io_includes_persisted_process_delta_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_rows = [
        _process_io(
            "rustc",
            10,
            read_bytes_delta=2 * 1024 * 1024,
            write_bytes_delta=1 * 1024 * 1024,
            total_syscalls_delta=120,
        )
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(()))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(()))
    monkeypatch.setattr(
        machine_source, "process_io_delta_samples", lambda **_: iter(process_rows)
    )

    report = analyze_machine_service_io_window(start=_ts(0), end=_ts(30))

    assert report.process_io_deltas[0].comm == "rustc"
    assert report.process_io_deltas[0].unit == "session.scope"
    assert report.process_io_deltas[0].total_mib == 3.0
    assert report.process_io_deltas[0].total_syscalls == 120


def test_service_io_uses_bracketing_samples_for_short_counter_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [_metric(15, io_psi_full_avg10=20.0)]
    states = [
        _state("transmission.service", 10, 0, 0),
        _state("transmission.service", 20, 8 * 1024 * 1024, 0),
    ]
    devices = [
        _block_device(
            "nvme0n1",
            10,
            sectors_read=0,
            sectors_written=0,
            reads_completed=0,
            writes_completed=0,
            io_time_ms=0,
            weighted_io_time_ms=0,
        ),
        _block_device(
            "nvme0n1",
            20,
            sectors_read=16_384,
            sectors_written=0,
            reads_completed=100,
            writes_completed=0,
            io_time_ms=500,
            weighted_io_time_ms=900,
        ),
    ]
    cgroup_io = [
        _cgroup_io("transmission.service", 10, rbytes=0, wbytes=0, rios=0, wios=0),
        _cgroup_io(
            "transmission.service",
            20,
            rbytes=4 * 1024 * 1024,
            wbytes=0,
            rios=50,
            wios=0,
        ),
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))
    monkeypatch.setattr(
        machine_source, "block_device_samples", lambda **_: iter(devices)
    )
    monkeypatch.setattr(
        machine_source, "service_cgroup_io_samples", lambda **_: iter(cgroup_io)
    )

    report = analyze_machine_service_io_window(
        start=_ts(12), end=_ts(18), min_total_mib=0.0
    )

    assert report.services[0].unit == "transmission.service"
    assert report.services[0].sample_count == 2
    assert report.services[0].total_mib == 8.0
    assert report.block_devices[0].sample_count == 2
    assert report.block_devices[0].total_mib == 8.0
    assert report.block_devices[0].read_iops == 10.0
    assert report.service_cgroup_io[0].sample_count == 2
    assert report.service_cgroup_io[0].total_mib == 4.0
    assert report.service_cgroup_io[0].read_iops == 5.0


def test_service_io_does_not_classify_one_sample_devices_as_device_contention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [_metric(5, io_psi_full_avg10=40.0)]
    devices = [
        _block_device(
            "nvme0n1",
            5,
            sectors_read=100,
            sectors_written=100,
            reads_completed=10,
            writes_completed=10,
            io_time_ms=100,
            weighted_io_time_ms=100,
        )
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(()))
    monkeypatch.setattr(
        machine_source, "block_device_samples", lambda **_: iter(devices)
    )

    report = analyze_machine_service_io_window(
        start=_ts(0), end=_ts(10), min_total_mib=0.0
    )

    assert report.load_shape is not None
    assert report.load_shape.label == "unclassified_insufficient_block_device_deltas"
    assert "lack enough bracketing samples" in report.load_shape.reason
    assert report.block_devices[0].sample_count == 1
    assert report.block_devices[0].avg_mib_s is None


def test_service_io_scales_process_deltas_by_window_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_rows = [
        _process_io(
            "codex",
            20,
            read_bytes_delta=8 * 1024 * 1024,
            write_bytes_delta=2 * 1024 * 1024,
            total_syscalls_delta=100,
            interval_s=10.0,
        )
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(()))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(()))
    monkeypatch.setattr(
        machine_source, "process_io_delta_samples", lambda **_: iter(process_rows)
    )

    report = analyze_machine_service_io_window(start=_ts(15), end=_ts(18))

    assert report.process_io_deltas[0].comm == "codex"
    assert report.process_io_deltas[0].sample_count == 1
    assert report.process_io_deltas[0].total_mib == 3.0
    assert report.process_io_deltas[0].total_syscalls == 30
    assert report.process_io_deltas[0].avg_total_mib_s == 1.0


def test_service_io_window_filters_precise_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [_metric(0, io_psi_full_avg10=1.0), _metric(20, io_psi_full_avg10=99.0)]
    states = [
        _state("in.service", 10, 0),
        _state("in.service", 20, 1024 * 1024),
        _state("out.service", 40, 10 * 1024 * 1024),
    ]
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))

    report = analyze_machine_service_io_window(
        start=_ts(5), end=_ts(25), min_total_mib=0.5
    )

    assert report.pressure.sample_count == 1
    assert report.pressure.max_io_psi_full_avg10 == 99.0
    assert [row.unit for row in report.services] == ["in.service"]
    assert report.services[0].total_mib == 1.0


def test_service_io_resolves_xtask_invocation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = XtaskInvocation(
        source_id="xtask:live:42",
        command=("test",),
        cwd="/realm/project/sinex",
        started_at=_ts(10),
        ended_at=_ts(30),
        duration_s=20.0,
        status="success",
        exit_code=0,
        host="host",
        project="sinex",
        git_commit=None,
        git_dirty=False,
        live_stage=None,
        args_json="[]",
        cpu_usage_avg=None,
        memory_usage_max_mb=None,
        process_cpu_usage_avg=None,
        process_memory_usage_max_mb=None,
        root_process_cpu_usage_avg=None,
        root_process_memory_usage_max_mb=None,
        shared_nix_daemon_cpu_usage_avg=None,
        shared_nix_daemon_memory_usage_max_mb=None,
        shared_nix_build_slice_cpu_usage_avg=None,
        shared_nix_build_slice_memory_usage_max_mb=None,
        shared_background_slice_cpu_usage_avg=None,
        shared_background_slice_memory_usage_max_mb=None,
        host_cpu_pressure_some_avg10_max=None,
        host_io_pressure_some_avg10_max=None,
        host_io_pressure_full_avg10_max=12.5,
        host_memory_pressure_some_avg10_max=None,
        host_memory_pressure_full_avg10_max=3.5,
        shm_free_min_mb=None,
        shm_used_max_mb=None,
        process_count_max=None,
        resource_sample_count=None,
    )
    metrics = [_metric(20, io_psi_full_avg10=5.0)]
    states = [_state("svc.service", 10, 0), _state("svc.service", 30, 2 * 1024 * 1024)]
    monkeypatch.setattr(
        service_io, "iter_all_invocations", lambda **_: iter([invocation])
    )
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))

    report = analyze_machine_service_io_for_xtask_invocation(42, min_total_mib=0.1)

    assert report.start == _ts(10)
    assert report.end == _ts(30)
    assert report.target is not None
    assert report.target.source_id == "xtask:live:42"
    assert report.target.host_io_pressure_full_avg10_max == 12.5
    assert [row.unit for row in report.services] == ["svc.service"]


def test_service_io_rejects_unfinished_xtask_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocation = XtaskInvocation(
        source_id="xtask:live:7",
        command=("test",),
        cwd="/realm/project/sinex",
        started_at=_ts(10),
        ended_at=None,
        duration_s=None,
        status="running",
        exit_code=None,
        host="host",
        project="sinex",
        git_commit=None,
        git_dirty=False,
        live_stage=None,
        args_json="[]",
        cpu_usage_avg=None,
        memory_usage_max_mb=None,
        process_cpu_usage_avg=None,
        process_memory_usage_max_mb=None,
        root_process_cpu_usage_avg=None,
        root_process_memory_usage_max_mb=None,
        shared_nix_daemon_cpu_usage_avg=None,
        shared_nix_daemon_memory_usage_max_mb=None,
        shared_nix_build_slice_cpu_usage_avg=None,
        shared_nix_build_slice_memory_usage_max_mb=None,
        shared_background_slice_cpu_usage_avg=None,
        shared_background_slice_memory_usage_max_mb=None,
        host_cpu_pressure_some_avg10_max=None,
        host_io_pressure_some_avg10_max=None,
        host_io_pressure_full_avg10_max=None,
        host_memory_pressure_some_avg10_max=None,
        host_memory_pressure_full_avg10_max=None,
        shm_free_min_mb=None,
        shm_used_max_mb=None,
        process_count_max=None,
        resource_sample_count=None,
    )
    monkeypatch.setattr(
        service_io, "iter_all_invocations", lambda **_: iter([invocation])
    )

    with pytest.raises(ValueError, match="no finished_at"):
        analyze_machine_service_io_for_xtask_invocation(7)


def test_service_io_can_include_below_process_rate_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [_metric(0, io_psi_full_avg10=1.0), _metric(10, io_psi_full_avg10=2.0)]
    states = [_state("svc.service", 0, 0), _state("svc.service", 10, 1024)]
    stdout = "\n".join(
        (
            "2026-06-06 12:00:00\t1\talpha\t/system.slice/a.service\t1048576\t0\t1048576\talpha --run",
            "2026-06-06 12:00:05\t1\talpha\t/system.slice/a.service\t1048576\t2097152\t3145728\talpha --run",
            "2026-06-06 12:00:00\t2\tbeta\t/system.slice/b.service\t0\t1048576\t1048576\tbeta",
        )
    )
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    report = analyze_machine_service_io_window(
        start=_ts(0), end=_ts(10), include_below_processes=True
    )

    assert [row.comm for row in report.below_processes] == ["alpha", "beta"]
    alpha = report.below_processes[0]
    assert alpha.estimated_read_mib == 10.0
    assert alpha.estimated_write_mib == 10.0
    assert alpha.estimated_total_mib == 20.0
    assert alpha.max_rw_mib_s == 3.0
    assert alpha.sample_presence_pct == 100.0
    beta = report.below_processes[1]
    assert beta.sample_presence_pct == 50.0
    assert any("not cumulative counters" in caveat for caveat in report.below_errors)
    assert any("grouped by process comm" in caveat for caveat in report.below_errors)


def test_below_process_rates_group_same_comm_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = [_metric(0, io_psi_full_avg10=1.0), _metric(10, io_psi_full_avg10=2.0)]
    states = [_state("svc.service", 0, 0), _state("svc.service", 10, 1024)]
    stdout = "\n".join(
        (
            "2026-06-06 12:00:00\t1\tcodex\t/user.slice/a.scope\t1048576\t0\t1048576\tcodex a",
            "2026-06-06 12:00:00\t2\tcodex\t/user.slice/b.scope\t0\t2097152\t2097152\tcodex b",
            "2026-06-06 12:00:05\t1\tcodex\t/user.slice/a.scope\t1048576\t0\t1048576\tcodex a",
        )
    )
    monkeypatch.setattr(machine_source, "metric_samples", lambda **_: iter(metrics))
    monkeypatch.setattr(machine_source, "service_states", lambda **_: iter(states))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    report = analyze_machine_service_io_window(
        start=_ts(0), end=_ts(10), include_below_processes=True
    )

    assert [row.comm for row in report.below_processes] == ["codex"]
    codex = report.below_processes[0]
    assert codex.sample_count == 3
    assert codex.sample_presence_pct == 100.0
    assert codex.estimated_read_mib == 10.0
    assert codex.estimated_write_mib == 10.0
    assert codex.estimated_total_mib == 20.0
