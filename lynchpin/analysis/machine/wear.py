"""Device wear-budget tracking and storage-pressure canaries.

The 2026-06-12 storage overhaul established that the root MX500 has a
~21 TB host-write budget left (SMART P/E wear vs host TBW implies ~1.9x
internal write amplification). One-off audits decay; these helpers turn the
wear question into a standing guardrail surfaced by observability_status.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from lynchpin.sources import machine as machine_source

_SECTOR_BYTES = 512
_GB = 1000**3


@dataclass(frozen=True)
class DeviceWearBudgetPolicy:
    """Per-device daily host-write budgets in GB/day.

    sda: Crucial MX500, 11% rated life left => ~21 TB host budget; 60 GB/day
    sustains roughly a year of headroom. nvme0n1: Crucial P3 4TB, 800 TBW at
    20% used => far larger budget; the cap mostly flags runaway writers.
    """

    budgets_gb_per_day: dict[str, float] = field(
        default_factory=lambda: {"sda": 60.0, "nvme0n1": 400.0}
    )


DEFAULT_WEAR_BUDGET_POLICY = DeviceWearBudgetPolicy()


def machine_wear_status(
    *,
    day: date | None = None,
    policy: DeviceWearBudgetPolicy = DEFAULT_WEAR_BUDGET_POLICY,
) -> dict[str, Any]:
    """Written-bytes-so-far today per budgeted device, vs its daily budget."""
    from lynchpin.analysis.machine.service_io import _positive_counter_delta

    target_day = day or datetime.now(timezone.utc).date()
    try:
        samples = list(
            machine_source.block_device_samples(start=target_day, end=target_day)
        )
    except Exception as exc:  # noqa: BLE001 - status surface must degrade, not raise
        return {"state": "error", "reason": f"{type(exc).__name__}: {exc}"}

    by_device: dict[str, list[Any]] = {}
    for sample in samples:
        if sample.device in policy.budgets_gb_per_day:
            by_device.setdefault(sample.device, []).append(sample)

    devices = []
    over_budget = []
    for device, rows in sorted(by_device.items()):
        ordered = sorted(rows, key=lambda row: row.observed_at)
        sectors, reset = _positive_counter_delta(
            [row.sectors_written for row in ordered]
        )
        written_gb = round(sectors * _SECTOR_BYTES / _GB, 2)
        budget = policy.budgets_gb_per_day[device]
        over = written_gb > budget
        if over:
            over_budget.append(device)
        devices.append(
            {
                "device": device,
                "written_gb_today": written_gb,
                "budget_gb_per_day": budget,
                "over_budget": over,
                "sample_count": len(ordered),
                "counter_reset_detected": reset,
            }
        )
    if not devices:
        return {
            "state": "unavailable",
            "reason": "no block-device samples for budgeted devices today",
        }
    return {
        "state": "ready",
        "day": target_day.isoformat(),
        "devices": devices,
        "over_budget": over_budget,
    }


_CANARY_PATTERN = "pam_lastlog2"
_CANARY_MATCH = "database is locked"


def storage_canary_status(*, since: str = "-6 hours") -> dict[str, Any]:
    """Login-path SQLite lock canary.

    `pam_lastlog2 ... database is locked` only appears when storage is so
    IO-starved that logins break — an effect-level signal independent of all
    telemetry plumbing. Zero matches is the healthy state.
    """
    try:
        proc = subprocess.run(
            ["journalctl", "-p", "err", "--since", since, "-o", "cat", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - status surface must degrade, not raise
        return {"state": "error", "reason": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {
            "state": "error",
            "reason": f"journalctl exited {proc.returncode}: {proc.stderr.strip()[:200]}",
        }
    matches = sum(
        1
        for line in proc.stdout.splitlines()
        if _CANARY_PATTERN in line and _CANARY_MATCH in line
    )
    return {
        "state": "ready",
        "since": since,
        "lastlog2_lock_matches": matches,
        "triggered": matches > 0,
    }


__all__ = [
    "DEFAULT_WEAR_BUDGET_POLICY",
    "DeviceWearBudgetPolicy",
    "machine_wear_status",
    "storage_canary_status",
]
