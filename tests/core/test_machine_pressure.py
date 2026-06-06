from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from lynchpin.core.machine_pressure import MachinePressurePolicy, machine_pressure_snapshot


def test_machine_pressure_snapshot_classifies_high_pressure() -> None:
    sample = SimpleNamespace(
        observed_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        load_1m=4.2,
        mem_avail_mb=16384,
        swap_used_mb=2048,
        io_psi_full_avg10=0.2,
        latency_oversleep_ms=60.0,
        dstate_task_count=1,
    )

    snapshot = machine_pressure_snapshot(sample=sample)

    assert snapshot.state == "ready"
    assert snapshot.pressure == "high"
    assert snapshot.blockers == ("dstate", "io_pressure", "latency", "swap")
    assert snapshot.to_json()["blockers"] == ["dstate", "io_pressure", "latency", "swap"]


def test_machine_pressure_snapshot_uses_policy_thresholds() -> None:
    sample = SimpleNamespace(
        observed_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        load_1m=1.0,
        mem_avail_mb=32000,
        swap_used_mb=900,
        io_psi_full_avg10=0.12,
        latency_oversleep_ms=10.0,
        dstate_task_count=0,
    )

    default = machine_pressure_snapshot(sample=sample)
    relaxed = machine_pressure_snapshot(
        sample=sample,
        policy=MachinePressurePolicy(io_psi_full_avg10_high=0.5),
    )

    assert default.pressure == "high"
    assert default.blockers == ("io_pressure",)
    assert relaxed.pressure == "normal"
    assert relaxed.blockers == ()


def test_machine_pressure_snapshot_reports_unavailable_without_sample() -> None:
    snapshot = machine_pressure_snapshot(sample_loader=lambda: None)

    assert snapshot.state == "unavailable"


def test_machine_pressure_snapshot_accepts_loader() -> None:
    snapshot = machine_pressure_snapshot(sample_loader=lambda: None)

    assert snapshot.state == "unavailable"


def test_machine_pressure_snapshot_loads_latest_machine_sample_by_default(monkeypatch) -> None:
    sample = SimpleNamespace(
        observed_at=datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc),
        host="sinnix-prime",
        load_1m=1.0,
        mem_avail_mb=32000,
        swap_used_mb=0,
        io_psi_full_avg10=0.0,
        latency_oversleep_ms=0.0,
        dstate_task_count=0,
    )
    monkeypatch.setattr("lynchpin.sources.machine.latest_metric_sample", lambda: sample)

    snapshot = machine_pressure_snapshot()

    assert snapshot.state == "ready"
    assert snapshot.host == "sinnix-prime"
    assert snapshot.pressure == "normal"
