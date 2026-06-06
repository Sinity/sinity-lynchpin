"""Typed machine-pressure status for compact materialization/status payloads."""

from __future__ import annotations

import importlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MachinePressurePolicy:
    io_psi_full_avg10_high: float = 0.1
    latency_oversleep_ms_high: float = 50.0
    swap_used_mb_high: int = 1024


@dataclass(frozen=True)
class MachinePressureSnapshot:
    state: str
    pressure: str | None = None
    blockers: tuple[str, ...] = ()
    observed_at_utc: str | None = None
    age_seconds: int | None = None
    host: str | None = None
    load_1m: float | None = None
    mem_avail_mb: int | None = None
    swap_used_mb: int | None = None
    io_psi_full_avg10: float | None = None
    latency_oversleep_ms: float | None = None
    dstate_task_count: int | None = None
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["blockers"] = list(self.blockers)
        return payload


DEFAULT_MACHINE_PRESSURE_POLICY = MachinePressurePolicy()


def machine_pressure_snapshot(
    *,
    sample: Any | None = None,
    policy: MachinePressurePolicy = DEFAULT_MACHINE_PRESSURE_POLICY,
    sample_loader: Any | None = None,
) -> MachinePressureSnapshot:
    if sample is None and sample_loader is not None:
        try:
            sample = sample_loader()
        except Exception as exc:  # pragma: no cover - defensive status surface
            return MachinePressureSnapshot(
                state="error",
                reason=f"{type(exc).__name__}: {exc}",
            )
    elif sample is None:
        try:
            sample = _default_sample_loader()
        except Exception as exc:  # pragma: no cover - defensive status surface
            return MachinePressureSnapshot(
                state="error",
                reason=f"{type(exc).__name__}: {exc}",
            )
    if sample is None:
        return MachinePressureSnapshot(
            state="unavailable",
            reason="no machine metric samples",
        )
    now = datetime.now(timezone.utc)
    observed_at = sample.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    observed_at = observed_at.astimezone(timezone.utc)
    age_seconds = max(0, int((now - observed_at).total_seconds()))
    blockers = []
    if (sample.dstate_task_count or 0) > 0:
        blockers.append("dstate")
    if (sample.io_psi_full_avg10 or 0) > policy.io_psi_full_avg10_high:
        blockers.append("io_pressure")
    if (sample.latency_oversleep_ms or 0) > policy.latency_oversleep_ms_high:
        blockers.append("latency")
    if (sample.swap_used_mb or 0) > policy.swap_used_mb_high:
        blockers.append("swap")
    pressure = "high" if blockers else "normal"
    return MachinePressureSnapshot(
        state="ready",
        pressure=pressure,
        blockers=tuple(blockers),
        observed_at_utc=observed_at.isoformat(),
        age_seconds=age_seconds,
        host=sample.host,
        load_1m=sample.load_1m,
        mem_avail_mb=sample.mem_avail_mb,
        swap_used_mb=sample.swap_used_mb,
        io_psi_full_avg10=sample.io_psi_full_avg10,
        latency_oversleep_ms=sample.latency_oversleep_ms,
        dstate_task_count=sample.dstate_task_count,
    )


def _default_sample_loader() -> Any | None:
    source = importlib.import_module("lynchpin.sources.machine")
    return source.latest_metric_sample()


__all__ = [
    "DEFAULT_MACHINE_PRESSURE_POLICY",
    "MachinePressurePolicy",
    "MachinePressureSnapshot",
    "machine_pressure_snapshot",
]
