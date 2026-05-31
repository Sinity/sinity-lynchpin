"""Machine context spine — "what was the machine at time *t*" resolver.

Layer-1 primitive for the machine causal-attribution stack. Given a timestamp,
resolve the machine's *context vector* by joining the substrate's already-
promoted facts:

- **software_revision** — the NixOS generation active at *t* (latest
  ``sinnix_generation`` with ``activated_at <= t``): generation, sinnix git
  revision, store path, NixOS label. (Per-repo git revision is attached by the
  attribution layer from ``commit_fact``; it is intentionally not resolved here
  because it is per-project, not a single machine-wide fact.)
- **hardware_regime** — the nearest ``machine_metric_sample``: GPU PCIe
  link gen/width, GPU pstate, CPU package power (a power/thermal proxy), and
  ``boot_id`` (the boot epoch identity).
- **contention_state** — the PSI pressure vector (cpu/io/memory, some+full) at
  the nearest sample.
- **cache_state** — resolved per *observation* at Layer 2, not ambient here.

Coverage honesty: hardware/contention come from the nearest telemetry sample.
If that sample is further than ``max_sample_gap_s`` from *t*, the regime/
contention are returned with their real age and a caveat rather than silently
imputed — missing telemetry is missing, never coerced to zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "SoftwareRevision",
    "HardwareRegime",
    "ContentionState",
    "MachineContext",
    "resolve_machine_context",
]

# Telemetry cadence is ~10s; a sample within 5 min of t still characterizes the
# regime/contention. Beyond that we flag the gap instead of trusting the sample.
DEFAULT_MAX_SAMPLE_GAP_S = 300.0


@dataclass(frozen=True)
class SoftwareRevision:
    """The NixOS generation active at the resolved instant."""

    generation: str | None
    sinnix_revision: str | None
    nixos_label: str | None
    store_path: str | None
    activated_at: datetime | None


@dataclass(frozen=True)
class HardwareRegime:
    """Hardware link/power identity from the nearest telemetry sample."""

    boot_id: str | None
    gpu_pcie_gen: int | None
    gpu_pcie_width: int | None
    gpu_pstate: str | None
    cpu_package_w: float | None
    sample_observed_at: datetime | None
    sample_age_seconds: float | None


@dataclass(frozen=True)
class ContentionState:
    """PSI pressure vector (cpu/io/memory, some+full) at the nearest sample."""

    cpu_psi_some_avg60: float | None
    io_psi_some_avg10: float | None
    io_psi_full_avg10: float | None
    io_psi_some_avg60: float | None
    io_psi_full_avg60: float | None
    memory_psi_some_avg60: float | None
    memory_psi_full_avg60: float | None
    sample_observed_at: datetime | None
    sample_age_seconds: float | None


@dataclass(frozen=True)
class MachineContext:
    """Point-in-time machine context vector for ``host`` at ``at``."""

    at: datetime
    host: str
    software: SoftwareRevision
    hardware: HardwareRegime
    contention: ContentionState
    caveats: tuple[str, ...]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _software_revision(conn: Any, *, at: datetime, host: str) -> SoftwareRevision:
    row = conn.execute(
        """
        SELECT generation, sinnix_revision, nixos_label, store_path, activated_at
        FROM sinnix_generation
        WHERE host = ? AND activated_at <= ?
        ORDER BY activated_at DESC
        LIMIT 1
        """,
        [host, at],
    ).fetchone()
    if row is None:
        return SoftwareRevision(None, None, None, None, None)
    return SoftwareRevision(
        generation=row[0],
        sinnix_revision=row[1],
        nixos_label=row[2],
        store_path=row[3],
        activated_at=row[4],
    )


def _nearest_metric_sample(conn: Any, *, at: datetime, host: str) -> tuple[Any, ...] | None:
    """Return the metric_sample row closest in time to ``at`` (either side)."""
    return conn.execute(
        """
        SELECT
            observed_at, boot_id, gpu_pcie_gen, gpu_pcie_width, gpu_pstate,
            cpu_package_w,
            cpu_psi_some_avg60, io_psi_some_avg10, io_psi_full_avg10,
            io_psi_some_avg60, io_psi_full_avg60,
            memory_psi_some_avg60, memory_psi_full_avg60,
            abs(epoch(observed_at) - epoch(CAST(? AS TIMESTAMPTZ))) AS age_s
        FROM machine_metric_sample
        WHERE host = ?
        ORDER BY age_s ASC
        LIMIT 1
        """,
        [at, host],
    ).fetchone()


def resolve_machine_context(
    conn: Any,
    *,
    at: datetime,
    host: str = "sinnix-prime",
    max_sample_gap_s: float = DEFAULT_MAX_SAMPLE_GAP_S,
) -> MachineContext:
    """Resolve the machine context vector for ``host`` at instant ``at``.

    ``conn`` is a DuckDB substrate connection (read path). Returns a
    :class:`MachineContext`; fields are ``None`` where the underlying fact is
    not present, and a caveat is added when the nearest telemetry sample is
    older than ``max_sample_gap_s`` (so callers never mistake an out-of-coverage
    regime for a measured one).
    """
    at = _aware(at)
    caveats: list[str] = []

    software = _software_revision(conn, at=at, host=host)
    if software.generation is None:
        caveats.append("software_revision.no_generation_at_or_before_t")

    row = _nearest_metric_sample(conn, at=at, host=host)
    if row is None:
        hardware = HardwareRegime(None, None, None, None, None, None, None)
        contention = ContentionState(*([None] * 7), None, None)  # type: ignore[arg-type]
        caveats.append("telemetry.no_metric_sample_for_host")
        return MachineContext(at=at, host=host, software=software,
                              hardware=hardware, contention=contention,
                              caveats=tuple(caveats))

    observed_at, boot_id = row[0], row[1]
    age_s = float(row[13]) if row[13] is not None else None
    if age_s is not None and age_s > max_sample_gap_s:
        caveats.append(
            f"telemetry.nearest_sample_gap_s={age_s:.0f}>max={max_sample_gap_s:.0f}"
        )

    hardware = HardwareRegime(
        boot_id=boot_id,
        gpu_pcie_gen=row[2],
        gpu_pcie_width=row[3],
        gpu_pstate=row[4],
        cpu_package_w=row[5],
        sample_observed_at=observed_at,
        sample_age_seconds=age_s,
    )
    contention = ContentionState(
        cpu_psi_some_avg60=row[6],
        io_psi_some_avg10=row[7],
        io_psi_full_avg10=row[8],
        io_psi_some_avg60=row[9],
        io_psi_full_avg60=row[10],
        memory_psi_some_avg60=row[11],
        memory_psi_full_avg60=row[12],
        sample_observed_at=observed_at,
        sample_age_seconds=age_s,
    )
    return MachineContext(at=at, host=host, software=software,
                          hardware=hardware, contention=contention,
                          caveats=tuple(caveats))
