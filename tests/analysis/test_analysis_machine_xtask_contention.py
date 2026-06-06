from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from lynchpin.analysis.machine import xtask_contention
from lynchpin.analysis.machine.service_io import (
    MachineBelowProcessIORate,
    MachineServiceIOAttribution,
    MachineServiceIODelta,
    MachineWindowPressureSummary,
)
from lynchpin.sources.xtask_history import XtaskInvocation


def _ts(second: int) -> datetime:
    return datetime(2026, 6, 6, 12, 0, second, tzinfo=timezone.utc)


def _invocation(
    source_id: str,
    *,
    command: tuple[str, ...] = ("test",),
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    duration_s: float | None = 60.0,
    status: str = "success",
    io_full: float | None = 10.0,
    mem_full: float | None = 1.0,
) -> XtaskInvocation:
    return XtaskInvocation(
        source_id=source_id,
        command=command,
        cwd="/realm/project/sinex",
        started_at=started_at or _ts(0),
        ended_at=ended_at if ended_at is not None else _ts(30),
        duration_s=duration_s,
        status=status,
        exit_code=0 if status == "success" else 1,
        host="host",
        project="sinex",
        git_commit=None,
        git_dirty=False,
        live_stage=None,
        args_json='["--scope=packages:xtask"]',
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
        host_io_pressure_full_avg10_max=io_full,
        host_memory_pressure_some_avg10_max=None,
        host_memory_pressure_full_avg10_max=mem_full,
        shm_free_min_mb=None,
        shm_used_max_mb=None,
        process_count_max=None,
        resource_sample_count=None,
        host_block_read_mib_delta=128.0,
        host_block_write_mib_delta=64.0,
        host_block_read_iops_avg=10.0,
        host_block_write_iops_avg=20.0,
        host_block_busiest_device="nvme0n1",
        host_block_busiest_device_total_mib_delta=192.0,
        host_block_busiest_device_read_iops_avg=8.0,
        host_block_busiest_device_write_iops_avg=16.0,
        host_block_busiest_device_weighted_io_ms_per_s=120.0,
    )


def _attribution(*, below: bool = False) -> MachineServiceIOAttribution:
    return MachineServiceIOAttribution(
        start=_ts(0),
        end=_ts(30),
        pressure=MachineWindowPressureSummary(
            sample_count=3,
            first_observed_at=_ts(0),
            last_observed_at=_ts(30),
            avg_io_psi_full_avg10=12.0,
            max_io_psi_full_avg10=30.0,
            avg_io_psi_some_avg10=14.0,
            max_io_psi_some_avg10=33.0,
            avg_memory_psi_full_avg10=2.0,
            max_memory_psi_full_avg10=7.0,
            avg_dstate_task_count=1.0,
            max_dstate_task_count=3,
        ),
        services=(
            MachineServiceIODelta(
                unit="transmission.service",
                scope="system",
                sample_count=3,
                first_observed_at=_ts(0),
                last_observed_at=_ts(30),
                read_bytes_delta=512 * 1024 * 1024,
                write_bytes_delta=0,
                total_bytes_delta=512 * 1024 * 1024,
                read_mib=512.0,
                write_mib=0.0,
                total_mib=512.0,
                active_states=("active",),
                sub_states=("running",),
            ),
        ),
        caveats=("sampled service counters",),
        below_processes=(
            (
                MachineBelowProcessIORate(
                    key="codex",
                    comm="codex",
                    cgroup="/user.slice",
                    sample_count=9,
                    sample_presence_pct=90.0,
                    first_observed_at=_ts(0),
                    last_observed_at=_ts(30),
                    estimated_read_bytes=100,
                    estimated_write_bytes=200,
                    estimated_total_bytes=300,
                    estimated_read_mib=1.0,
                    estimated_write_mib=2.0,
                    estimated_total_mib=3.0,
                    max_rw_mib_s=4.0,
                    cmdline="codex",
                ),
            )
            if below
            else ()
        ),
    )


def test_xtask_contention_ranks_slow_invocations_and_attributes_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = [
        _invocation("xtask:live:1", duration_s=20.0),
        _invocation("xtask:live:2", duration_s=120.0, io_full=40.0),
        _invocation("xtask:live:3", duration_s=90.0, io_full=80.0),
    ]
    calls: list[tuple[datetime, datetime]] = []
    monkeypatch.setattr(
        xtask_contention, "iter_all_invocations", lambda **_: iter(invocations)
    )

    def fake_attribution(**kwargs: Any) -> MachineServiceIOAttribution:
        calls.append((kwargs["start"], kwargs["end"]))
        return _attribution()

    monkeypatch.setattr(
        xtask_contention, "analyze_machine_service_io_window", fake_attribution
    )

    report = xtask_contention.analyze_xtask_contention(
        start=_ts(0),
        end=_ts(40),
        min_duration_s=30.0,
        limit=2,
    )

    assert [row.source_id for row in report.rows] == ["xtask:live:2", "xtask:live:3"]
    assert calls == [(_ts(0), _ts(30)), (_ts(0), _ts(30))]
    assert report.rows[0].top_services[0].unit == "transmission.service"
    assert report.rows[0].machine_io_full_max == 30.0
    assert report.rows[0].xtask_block_io.busiest_device == "nvme0n1"
    assert "xtask-sampled device: nvme0n1" in report.rows[0].interpretation
    assert "top service counter delta" in report.rows[0].interpretation
    assert any(
        "machine.service_state" in fact for fact in report.retrospective_fact_classes
    )
    assert any(
        "block-device deltas before" in limit
        for limit in report.not_proven_retrospectively
    )
    assert any("per-cgroup io.stat" in gap for gap in report.forward_capture_gaps)


def test_xtask_contention_filters_failures_when_success_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = [
        _invocation("xtask:live:1", duration_s=120.0, status="failed"),
        _invocation("xtask:live:2", duration_s=90.0, status="success"),
    ]
    monkeypatch.setattr(
        xtask_contention, "iter_all_invocations", lambda **_: iter(invocations)
    )
    monkeypatch.setattr(
        xtask_contention,
        "analyze_machine_service_io_window",
        lambda **_: _attribution(),
    )

    report = xtask_contention.analyze_xtask_contention(
        start=_ts(0),
        end=_ts(40),
        include_failures=False,
    )

    assert [row.source_id for row in report.rows] == ["xtask:live:2"]


def test_xtask_contention_interprets_sustained_below_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        xtask_contention,
        "iter_all_invocations",
        lambda **_: iter([_invocation("xtask:live:1", duration_s=120.0)]),
    )
    monkeypatch.setattr(
        xtask_contention,
        "analyze_machine_service_io_window",
        lambda **_: _attribution(below=True),
    )

    report = xtask_contention.analyze_xtask_contention(
        start=_ts(0),
        end=_ts(40),
        include_below_processes=True,
    )

    assert report.rows[0].sustained_processes[0].comm == "codex"
    assert "codex 90.0%" in report.rows[0].interpretation
    assert any(
        "below dump process" in fact for fact in report.retrospective_fact_classes
    )
