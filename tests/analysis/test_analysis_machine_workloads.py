from __future__ import annotations

from datetime import datetime, timezone

from lynchpin.analysis.machine import workloads
from lynchpin.analysis.machine.workloads import WorkloadKind, daily_workload_summary
from lynchpin.sources.machine_models import MachineProcessIODeltaSample


def _sample(
    comm: str,
    cgroup: str,
    *,
    read_bytes: int = 0,
    write_bytes: int = 0,
) -> MachineProcessIODeltaSample:
    return MachineProcessIODeltaSample(
        observed_at=datetime(2026, 6, 12, 6, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        boot_id="boot",
        source_schema_version=3,
        interval_s=10.0,
        pid=123,
        process_start_time_ticks=456,
        comm=comm,
        exe=f"/proc/{comm}",
        cgroup=cgroup,
        unit=None,
        scope=None,
        read_bytes_delta=read_bytes,
        write_bytes_delta=write_bytes,
        cancelled_write_bytes_delta=0,
        read_chars_delta=0,
        write_chars_delta=0,
        read_syscalls_delta=0,
        write_syscalls_delta=0,
        total_bytes_delta=read_bytes + write_bytes,
        total_syscalls_delta=0,
    )


def test_workload_classifier_names_runtime_services() -> None:
    assert workloads._classify("stash", "/system.slice/stashbox.service") == WorkloadKind.MediaService
    assert workloads._classify("ffmpeg", "/system.slice/stashbox.service") == WorkloadKind.MediaService
    assert workloads._classify("transmission-da", "/system.slice/transmission.service") == WorkloadKind.MediaService
    assert workloads._classify("btrfs", "/system.slice/btrfs-scrub--.service") == WorkloadKind.StorageMaintenance
    assert workloads._classify("btrfs", "/system.slice/mx500-balance.service") == WorkloadKind.StorageMaintenance
    assert workloads._classify("python3.13", "/system.slice/system-critical.slice/machine-telemetry.service") == WorkloadKind.Observability
    assert workloads._classify("systemd-journal", "/system.slice/systemd-journald.service") == WorkloadKind.SystemIO
    assert workloads._classify("btrfs-transaction", None) == WorkloadKind.SystemIO


def test_daily_summary_keeps_current_io_out_of_other(monkeypatch) -> None:
    mib = 1024 * 1024
    rows = [
        _sample("stash", "/system.slice/stashbox.service", read_bytes=10 * mib),
        _sample("btrfs", "/system.slice/btrfs-scrub--.service", read_bytes=20 * mib),
        _sample("python3.13", "/system.slice/system-critical.slice/machine-telemetry.service", write_bytes=3 * mib),
        _sample("btrfs-transaction", "", write_bytes=4 * mib),
        _sample("mystery", "/system.slice/mystery.service", write_bytes=1 * mib),
    ]
    monkeypatch.setattr(workloads, "process_io_delta_samples", lambda **_: iter(rows))
    monkeypatch.setattr(workloads, "metric_samples", lambda **_: iter(()))

    [summary] = daily_workload_summary()

    assert summary.io_mb[WorkloadKind.MediaService.value] == 10
    assert summary.io_mb[WorkloadKind.StorageMaintenance.value] == 20
    assert summary.io_mb[WorkloadKind.Observability.value] == 3
    assert summary.io_mb[WorkloadKind.SystemIO.value] == 4
    assert summary.io_mb[WorkloadKind.Other.value] == 1
