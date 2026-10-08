from __future__ import annotations

import subprocess
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from lynchpin.analysis.machine import wear
from lynchpin.analysis.machine.wear import (
    DeviceWearBudgetPolicy,
    machine_wear_status,
    storage_canary_status,
)
from lynchpin.sources import machine as machine_source
from lynchpin.sources.machine_models import MachineBlockDeviceSample


def _ts(second: int) -> datetime:
    return datetime(2026, 6, 12, 12, 0, second, tzinfo=timezone.utc)


def _sample(device: str, second: int, sectors_written: int) -> MachineBlockDeviceSample:
    return MachineBlockDeviceSample(
        observed_at=_ts(second),
        host="host",
        boot_id="boot",
        source_schema_version=2,
        major=8,
        minor=0,
        device=device,
        sectors_read=0,
        sectors_written=sectors_written,
        reads_completed=0,
        writes_completed=0,
        io_time_ms=0,
        weighted_io_time_ms=0,
    )


def test_wear_status_flags_over_budget_device(monkeypatch: pytest.MonkeyPatch) -> None:
    gb_sectors = (1000**3) // 512
    samples = [
        # sda writes 70 GB today against a 60 GB/day budget.
        _sample("sda", 0, 0),
        _sample("sda", 30, 70 * gb_sectors),
        # nvme0n1 stays far under budget.
        _sample("nvme0n1", 0, 0),
        _sample("nvme0n1", 30, 10 * gb_sectors),
        # unbudgeted devices are ignored entirely.
        _sample("sdb", 0, 0),
        _sample("sdb", 30, 500 * gb_sectors),
    ]
    monkeypatch.setattr(
        machine_source, "block_device_samples", lambda **_: iter(samples)
    )

    status = machine_wear_status(day=date(2026, 6, 12))

    assert status["state"] == "ready"
    assert status["over_budget"] == ["sda"]
    by_device = {row["device"]: row for row in status["devices"]}
    assert set(by_device) == {"sda", "nvme0n1"}
    assert by_device["sda"]["written_gb_today"] == 70.0
    assert by_device["sda"]["over_budget"] is True
    assert by_device["nvme0n1"]["over_budget"] is False


def test_wear_status_survives_counter_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    gb_sectors = (1000**3) // 512
    samples = [
        _sample("sda", 0, 40 * gb_sectors),
        _sample("sda", 10, 50 * gb_sectors),
        # reboot mid-day: counter resets, then accumulates again
        _sample("sda", 20, 2 * gb_sectors),
        _sample("sda", 30, 5 * gb_sectors),
    ]
    monkeypatch.setattr(
        machine_source, "block_device_samples", lambda **_: iter(samples)
    )

    status = machine_wear_status(
        day=date(2026, 6, 12),
        policy=DeviceWearBudgetPolicy(budgets_gb_per_day={"sda": 60.0}),
    )

    row = status["devices"][0]
    # 10 GB pre-reset + 2 GB post-reset baseline + 3 GB tail = 15 GB
    assert row["written_gb_today"] == 15.0
    assert row["counter_reset_detected"] is True
    assert status["over_budget"] == []


def test_wear_status_unavailable_without_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(machine_source, "block_device_samples", lambda **_: iter(()))
    status = machine_wear_status(day=date(2026, 6, 12))
    assert status["state"] == "unavailable"


def test_storage_canary_counts_lock_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(
        returncode=0,
        stdout=(
            "pam_lastlog2(login:session): SQL error: database is locked\n"
            "unrelated error line\n"
            "pam_lastlog2(login:session): SQL error: database is locked\n"
        ),
        stderr="",
    )
    monkeypatch.setattr(wear.subprocess, "run", lambda *a, **k: fake)

    status = storage_canary_status()

    assert status["state"] == "ready"
    assert status["lastlog2_lock_matches"] == 2
    assert status["triggered"] is True


def test_storage_canary_quiet_when_no_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(returncode=0, stdout="benign error\n", stderr="")
    monkeypatch.setattr(wear.subprocess, "run", lambda *a, **k: fake)

    status = storage_canary_status()

    assert status["triggered"] is False
    assert status["lastlog2_lock_matches"] == 0


def test_storage_canary_degrades_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="journalctl", timeout=10.0)

    monkeypatch.setattr(wear.subprocess, "run", _boom)
    status = storage_canary_status()
    assert status["state"] == "error"
