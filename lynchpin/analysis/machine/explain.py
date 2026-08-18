"""Operator-facing narrative over a window of machine telemetry (sinnix-6o2).

``machine explain`` answers "what has been happening and why" for a requested
window (default: the last 24 hours): pressure episodes classified into the
host's measured incident clusters, kill events with a justification verdict,
telemetry coverage gaps, health-ledger transitions, and week-over-week deltas.

Division of labor: the Sinnix hub (``/`` and ``/pressure/``, rendered live by
the ops-reducer on its 60 s tick) answers "what is happening right now"; this
module is the narrative over hours/days that a live page cannot hold. Both
surfaces deliberately share one calibration so they never disagree about what
counts as pressure: the thresholds and cluster names below come from the
2026-08-18 pressure-incident taxonomy measured on sinnix-prime's own telemetry
(98 days of metric samples, 832 kill events, cluster analysis C0-C6).

Evidence semantics: missing coverage is reported as a gap, never as calm;
signals whose source has no data in the window are reported as unavailable,
never silently omitted.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from lynchpin.sources.machine_models import (
    MachineKillEvent,
    MachineMetricSample,
    MachineProcessIODeltaSample,
    MachineProcessMemorySample,
)

CALIBRATION_PROVENANCE = "sinnix-prime pressure-incident taxonomy, 2026-08-18"

# ── Host calibration (taxonomy §2/§3; shared with the hub's /pressure/ page) ──
# Telemetry records swap_used_mb only; the host's swap total (12 GiB zram
# prio 100 + 8 GiB swapfile prio -2) is a calibration constant, stated in the
# rendered output rather than silently assumed.
SWAP_TOTAL_MB = 20_479
# Swap headroom is the ~10-minute early warning for the freeze regime (C1):
# median onset was preceded by 90% consumed while mem_avail looked healthy.
SWAP_CRITICAL_RATIO = 0.75
# memory/io psi_full regimes: >=20 degraded, >=50 frozen (all tasks stalled).
PSI_FULL_DEGRADED = 20.0
PSI_FULL_FROZEN = 50.0
# Episode membership (taxonomy §2): any of these crossed, split on >120 s gaps.
EPISODE_MEM_SOME = 20.0
EPISODE_IO_FULL = 20.0
EPISODE_AVAIL_FLOOR_MB = 2_048
EPISODE_SPLIT_GAP = timedelta(seconds=120)
# Severe-mechanism floors used by the cluster classifier (taxonomy §2.1).
SEVERE_MEM_PSI_SOME = 40.0
SEVERE_IO_PSI_FULL = 40.0
MEM_HEALTHY_AVAIL_MB = 4_096
# Kill justification buckets (taxonomy §2.3): memory PSI at the kill.
KILL_STALL_PSI = 40.0
KILL_CALM_PSI = 10.0
KILL_PSI_JOIN_WINDOW = timedelta(seconds=60)
KILL_DEDUP_GAP = timedelta(seconds=120)
# A hole in the ~10.5 s metric stream longer than this is a coverage gap.
COVERAGE_GAP_MIN = timedelta(minutes=10)
# Cap the interval credited to one sample when integrating stall hours, so a
# coverage gap is never silently counted as sustained pressure.
MAX_SAMPLE_INTERVAL = timedelta(seconds=60)

DEFAULT_HEALTH_LEDGER = Path("/run/sinnix/health-transitions.jsonl")


@dataclass(frozen=True)
class PressureEpisode:
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    cluster: str
    sample_count: int
    peak_memory_psi_some: float | None
    peak_memory_psi_full: float | None
    peak_io_psi_full: float | None
    min_mem_avail_mb: int | None
    max_swap_ratio: float | None
    kill_count: int
    culprits: tuple[str, ...]


@dataclass(frozen=True)
class KillAssessment:
    observed_at: datetime
    killer: str
    victim_comm: str | None
    victim_rss_mib: int | None
    memory_psi_some_at_kill: float | None
    verdict: str  # "machine-was-fine" | "borderline" | "justified-stall" | "no-nearby-sample"


@dataclass(frozen=True)
class CoverageGap:
    started_at: datetime
    ended_at: datetime
    seconds: float


@dataclass(frozen=True)
class HealthTransitionSummary:
    unit: str
    kind: str
    bad_count: int
    last_bad_at: datetime
    last_status: str


@dataclass(frozen=True)
class WindowAggregates:
    episode_count: int
    episode_hours_by_cluster: dict[str, float]
    memory_degraded_hours: float
    memory_frozen_hours: float
    io_frozen_hours: float
    kill_count: int
    kills_machine_was_fine: int


@dataclass(frozen=True)
class MachineExplainReport:
    host: str | None
    window_start: datetime
    window_end: datetime
    generated_at: datetime
    calibration: dict[str, Any]
    aggregates: WindowAggregates
    episodes: tuple[PressureEpisode, ...]
    kills: tuple[KillAssessment, ...]
    coverage_gaps: tuple[CoverageGap, ...]
    health_transitions: tuple[HealthTransitionSummary, ...]
    health_ledger_available: bool
    health_ledger_note: str
    prior_week: WindowAggregates | None
    prior_week_note: str
    caveats: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── episode detection and classification ─────────────────────────────────────


def _swap_ratio(sample: MachineMetricSample) -> float | None:
    if sample.swap_used_mb is None:
        return None
    return sample.swap_used_mb / SWAP_TOTAL_MB


def _in_episode(sample: MachineMetricSample) -> bool:
    if (sample.memory_psi_some_avg10 or 0.0) >= EPISODE_MEM_SOME:
        return True
    if (sample.io_psi_full_avg10 or 0.0) >= EPISODE_IO_FULL:
        return True
    return sample.mem_avail_mb is not None and sample.mem_avail_mb < EPISODE_AVAIL_FLOOR_MB


def _split_runs(samples: Sequence[MachineMetricSample]) -> list[list[MachineMetricSample]]:
    runs: list[list[MachineMetricSample]] = []
    current: list[MachineMetricSample] = []
    for sample in samples:
        if not _in_episode(sample):
            continue
        if current and sample.observed_at - current[-1].observed_at > EPISODE_SPLIT_GAP:
            runs.append(current)
            current = []
        current.append(sample)
    if current:
        runs.append(current)
    return runs


def _peak(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None


def _classify(
    *,
    peak_mem_some: float | None,
    peak_mem_full: float | None,
    peak_io_full: float | None,
    min_avail: int | None,
    max_swap_ratio: float | None,
) -> str:
    mem_some = peak_mem_some or 0.0
    mem_full = peak_mem_full or 0.0
    io_full = peak_io_full or 0.0
    avail_low = min_avail is not None and min_avail < EPISODE_AVAIL_FLOOR_MB
    avail_healthy = min_avail is None or min_avail >= MEM_HEALTHY_AVAIL_MB
    swap_critical = max_swap_ratio is not None and max_swap_ratio >= SWAP_CRITICAL_RATIO

    if swap_critical and mem_full >= PSI_FULL_DEGRADED:
        return "C1 swap-saturation thrash"
    if mem_some >= SEVERE_MEM_PSI_SOME and min_avail is not None and min_avail < MEM_HEALTHY_AVAIL_MB:
        return "MEM-CRUNCH"
    if avail_low and mem_full < PSI_FULL_DEGRADED:
        # The earlyoom-bait shape: free memory dives while nothing stalls.
        return "C2 big-job spike"
    if mem_some >= SEVERE_MEM_PSI_SOME:
        return "MEM-THRASH (refault-driven)"
    if io_full >= SEVERE_IO_PSI_FULL and avail_healthy and mem_some < EPISODE_MEM_SOME:
        return "C5 maintenance/backup IO stall"
    return "MINOR"


def _memory_culprits(
    process_samples: Sequence[MachineProcessMemorySample],
    *,
    start: datetime,
    end: datetime,
    top_n: int = 3,
) -> tuple[str, ...]:
    peak: dict[tuple[str | None, str | None], tuple[int, int]] = {}
    for sample in process_samples:
        if not start <= sample.observed_at <= end:
            continue
        key = (sample.comm, sample.unit or sample.scope)
        pss, swap = peak.get(key, (0, 0))
        peak[key] = (max(pss, sample.pss_kb), max(swap, sample.swap_kb))
    ranked = sorted(peak.items(), key=lambda item: item[1][0] + item[1][1], reverse=True)
    out: list[str] = []
    for (comm, unit), (pss_kb, swap_kb) in ranked[:top_n]:
        label = comm or "?"
        if unit:
            label += f" [{unit}]"
        out.append(f"{label} peak {pss_kb / 1048576:.1f} GiB PSS + {swap_kb / 1048576:.1f} GiB swap")
    return tuple(out)


def _io_culprits(
    io_samples: Sequence[MachineProcessIODeltaSample],
    *,
    start: datetime,
    end: datetime,
    top_n: int = 3,
) -> tuple[str, ...]:
    totals: dict[str, int] = {}
    for sample in io_samples:
        if not start <= sample.observed_at <= end:
            continue
        key = sample.unit or sample.comm or "?"
        totals[key] = totals.get(key, 0) + sample.total_bytes_delta
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return tuple(f"{unit} {total / 1073741824:.1f} GiB IO" for unit, total in ranked[:top_n])


def _build_episode(
    run: Sequence[MachineMetricSample],
    *,
    kills: Sequence[MachineKillEvent],
    process_samples: Sequence[MachineProcessMemorySample],
    io_samples: Sequence[MachineProcessIODeltaSample],
) -> PressureEpisode:
    started_at = run[0].observed_at
    ended_at = run[-1].observed_at
    peak_mem_some = _peak(s.memory_psi_some_avg10 for s in run)
    peak_mem_full = _peak(s.memory_psi_full_avg10 for s in run)
    peak_io_full = _peak(s.io_psi_full_avg10 for s in run)
    avails = [s.mem_avail_mb for s in run if s.mem_avail_mb is not None]
    min_avail = min(avails) if avails else None
    max_swap_ratio = _peak(_swap_ratio(s) for s in run)
    cluster = _classify(
        peak_mem_some=peak_mem_some,
        peak_mem_full=peak_mem_full,
        peak_io_full=peak_io_full,
        min_avail=min_avail,
        max_swap_ratio=max_swap_ratio,
    )
    pad_start = started_at - timedelta(minutes=2)
    pad_end = ended_at + timedelta(minutes=2)
    if cluster.startswith(("C5", "MINOR")) and (peak_io_full or 0.0) >= EPISODE_IO_FULL:
        culprits = _io_culprits(io_samples, start=pad_start, end=pad_end)
    else:
        culprits = _memory_culprits(process_samples, start=pad_start, end=pad_end)
    kill_count = sum(1 for k in kills if pad_start <= k.observed_at <= pad_end)
    return PressureEpisode(
        started_at=started_at,
        ended_at=ended_at,
        duration_seconds=(ended_at - started_at).total_seconds(),
        cluster=cluster,
        sample_count=len(run),
        peak_memory_psi_some=peak_mem_some,
        peak_memory_psi_full=peak_mem_full,
        peak_io_psi_full=peak_io_full,
        min_mem_avail_mb=min_avail,
        max_swap_ratio=max_swap_ratio,
        kill_count=kill_count,
        culprits=culprits,
    )


# ── kills ─────────────────────────────────────────────────────────────────────


def _dedup_kills(kills: Sequence[MachineKillEvent]) -> list[MachineKillEvent]:
    """Collapse earlyoom's escalating SIGTERM warning runs to one event each.

    Consecutive events against the same (victim_pid, victim_comm) within
    KILL_DEDUP_GAP are one kill; the last row (the final escalation) wins.
    """
    ordered = sorted(kills, key=lambda k: k.observed_at)
    out: list[MachineKillEvent] = []
    for event in ordered:
        if (
            out
            and out[-1].victim_pid == event.victim_pid
            and out[-1].victim_comm == event.victim_comm
            and event.observed_at - out[-1].observed_at <= KILL_DEDUP_GAP
        ):
            out[-1] = event
            continue
        out.append(event)
    return out


def _assess_kills(
    kills: Sequence[MachineKillEvent],
    samples: Sequence[MachineMetricSample],
) -> tuple[KillAssessment, ...]:
    assessments: list[KillAssessment] = []
    for event in _dedup_kills(kills):
        nearest: MachineMetricSample | None = None
        best = KILL_PSI_JOIN_WINDOW
        for sample in samples:
            distance = abs(sample.observed_at - event.observed_at)
            if distance <= best:
                nearest = sample
                best = distance
        psi = nearest.memory_psi_some_avg10 if nearest is not None else None
        if psi is None:
            verdict = "no-nearby-sample"
        elif psi >= KILL_STALL_PSI:
            verdict = "justified-stall"
        elif psi >= KILL_CALM_PSI:
            verdict = "borderline"
        else:
            verdict = "machine-was-fine"
        assessments.append(
            KillAssessment(
                observed_at=event.observed_at,
                killer=event.killer,
                victim_comm=event.victim_comm,
                victim_rss_mib=event.victim_rss_mib,
                memory_psi_some_at_kill=psi,
                verdict=verdict,
            )
        )
    return tuple(assessments)


# ── coverage, stall hours, health ledger ─────────────────────────────────────


def _coverage_gaps(
    samples: Sequence[MachineMetricSample],
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[CoverageGap, ...]:
    gaps: list[CoverageGap] = []

    def add(start: datetime, end: datetime) -> None:
        if end - start > COVERAGE_GAP_MIN:
            gaps.append(CoverageGap(started_at=start, ended_at=end, seconds=(end - start).total_seconds()))

    if not samples:
        add(window_start, window_end)
        return tuple(gaps)
    add(window_start, samples[0].observed_at)
    for previous, current in zip(samples, samples[1:]):
        add(previous.observed_at, current.observed_at)
    add(samples[-1].observed_at, window_end)
    return tuple(gaps)


def _stall_hours(samples: Sequence[MachineMetricSample]) -> tuple[float, float, float]:
    """(memory degraded, memory frozen, io frozen) hours, psi_full-based."""
    mem_degraded = mem_frozen = io_frozen = timedelta()
    for previous, current in zip(samples, samples[1:]):
        interval = min(current.observed_at - previous.observed_at, MAX_SAMPLE_INTERVAL)
        mem_full = current.memory_psi_full_avg10 or 0.0
        io_full = current.io_psi_full_avg10 or 0.0
        if mem_full >= PSI_FULL_DEGRADED:
            mem_degraded += interval
        if mem_full >= PSI_FULL_FROZEN:
            mem_frozen += interval
        if io_full >= PSI_FULL_FROZEN:
            io_frozen += interval
    hours = 3600.0
    return (
        mem_degraded.total_seconds() / hours,
        mem_frozen.total_seconds() / hours,
        io_frozen.total_seconds() / hours,
    )


def _read_health_transitions(
    ledger: Path,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[tuple[HealthTransitionSummary, ...], bool, str]:
    if not ledger.exists():
        return (), False, f"signal unavailable: {ledger} absent (ledger lives on tmpfs, current boot only)"
    grouped: dict[tuple[str, str], list[tuple[datetime, str]]] = {}
    parse_errors = 0
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if record.get("ok") is not False:
            continue
        ts_raw = record.get("ts")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            parse_errors += 1
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if not window_start <= ts <= window_end:
            continue
        key = (str(record.get("unit") or "?"), str(record.get("type") or "?"))
        grouped.setdefault(key, []).append((ts, str(record.get("status") or "?")))
    summaries = tuple(
        sorted(
            (
                HealthTransitionSummary(
                    unit=unit,
                    kind=kind,
                    bad_count=len(events),
                    last_bad_at=max(ts for ts, _ in events),
                    last_status=max(events)[1],
                )
                for (unit, kind), events in grouped.items()
            ),
            key=lambda s: s.last_bad_at,
            reverse=True,
        )
    )
    note = "current boot only (tmpfs ledger)"
    if parse_errors:
        note += f"; {parse_errors} unparseable line(s) skipped"
    return summaries, True, note


# ── report builder ───────────────────────────────────────────────────────────


def _aggregates(
    samples: Sequence[MachineMetricSample],
    episodes: Sequence[PressureEpisode],
    kills: Sequence[KillAssessment],
) -> WindowAggregates:
    hours_by_cluster: dict[str, float] = {}
    for episode in episodes:
        hours_by_cluster[episode.cluster] = (
            hours_by_cluster.get(episode.cluster, 0.0) + episode.duration_seconds / 3600.0
        )
    mem_degraded, mem_frozen, io_frozen = _stall_hours(samples)
    return WindowAggregates(
        episode_count=len(episodes),
        episode_hours_by_cluster={k: round(v, 2) for k, v in sorted(hours_by_cluster.items())},
        memory_degraded_hours=round(mem_degraded, 2),
        memory_frozen_hours=round(mem_frozen, 2),
        io_frozen_hours=round(io_frozen, 2),
        kill_count=len(kills),
        kills_machine_was_fine=sum(1 for k in kills if k.verdict == "machine-was-fine"),
    )


def build_machine_explain_report(
    *,
    window_start: datetime,
    window_end: datetime,
    samples: Sequence[MachineMetricSample],
    kills: Sequence[MachineKillEvent],
    process_samples: Sequence[MachineProcessMemorySample],
    io_samples: Sequence[MachineProcessIODeltaSample],
    prior_samples: Sequence[MachineMetricSample] | None = None,
    prior_kills: Sequence[MachineKillEvent] | None = None,
    health_ledger: Path = DEFAULT_HEALTH_LEDGER,
    generated_at: datetime | None = None,
) -> MachineExplainReport:
    generated = generated_at or datetime.now(timezone.utc)
    ordered = sorted(samples, key=lambda s: s.observed_at)
    caveats: list[str] = []
    if not ordered:
        caveats.append("machine_metric_sample has no rows in this window — the report can only state absence")

    deduped_kills = _dedup_kills(kills)
    episodes = tuple(
        _build_episode(run, kills=deduped_kills, process_samples=process_samples, io_samples=io_samples)
        for run in _split_runs(ordered)
    )
    kill_assessments = _assess_kills(kills, ordered)
    gaps = _coverage_gaps(ordered, window_start=window_start, window_end=window_end)
    if not process_samples:
        caveats.append("no process memory samples in window — memory-episode culprits unavailable")
    if not io_samples:
        caveats.append("no process IO delta samples in window — IO-episode culprits unavailable")

    health, health_available, health_note = _read_health_transitions(
        health_ledger, window_start=window_start, window_end=window_end
    )

    prior: WindowAggregates | None = None
    prior_note = "signal unavailable: no telemetry for the same window one week earlier"
    if prior_samples:
        prior_ordered = sorted(prior_samples, key=lambda s: s.observed_at)
        prior_episodes = tuple(
            _build_episode(run, kills=prior_kills or (), process_samples=(), io_samples=())
            for run in _split_runs(prior_ordered)
        )
        prior_kill_assessments = _assess_kills(prior_kills or (), prior_ordered)
        prior = _aggregates(prior_ordered, prior_episodes, prior_kill_assessments)
        prior_note = "same-length window ending 7 days earlier"

    hosts = {s.host for s in ordered}
    if len(hosts) > 1:
        caveats.append(f"samples span multiple hosts: {sorted(hosts)}")

    return MachineExplainReport(
        host=next(iter(hosts)) if len(hosts) == 1 else None,
        window_start=window_start,
        window_end=window_end,
        generated_at=generated,
        calibration={
            "provenance": CALIBRATION_PROVENANCE,
            "swap_total_mb": SWAP_TOTAL_MB,
            "swap_critical_ratio": SWAP_CRITICAL_RATIO,
            "psi_full_degraded": PSI_FULL_DEGRADED,
            "psi_full_frozen": PSI_FULL_FROZEN,
            "episode_thresholds": {
                "memory_psi_some_avg10": EPISODE_MEM_SOME,
                "io_psi_full_avg10": EPISODE_IO_FULL,
                "mem_avail_floor_mb": EPISODE_AVAIL_FLOOR_MB,
                "split_gap_seconds": EPISODE_SPLIT_GAP.total_seconds(),
            },
            "kill_verdict_psi": {"calm_below": KILL_CALM_PSI, "stall_at_or_above": KILL_STALL_PSI},
        },
        aggregates=_aggregates(ordered, episodes, kill_assessments),
        episodes=episodes,
        kills=kill_assessments,
        coverage_gaps=gaps,
        health_transitions=health,
        health_ledger_available=health_available,
        health_ledger_note=health_note,
        prior_week=prior,
        prior_week_note=prior_note,
        caveats=tuple(caveats),
    )


def machine_explain(
    *,
    window_hours: float = 24.0,
    end: datetime | None = None,
    telemetry_db: Path | None = None,
    health_ledger: Path = DEFAULT_HEALTH_LEDGER,
) -> MachineExplainReport:
    """Load the window from the live telemetry SQLite and build the report.

    Read-only; no substrate rebuild. ``telemetry_db=None`` uses the configured
    live database (``LYNCHPIN_MACHINE_TELEMETRY_DB`` / machine host root).
    """
    from lynchpin.sources import machine as source

    window_end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = window_end - timedelta(hours=window_hours)
    prior_end = window_end - timedelta(days=7)
    prior_start = window_start - timedelta(days=7)

    def clip(items: Iterable[Any], start: datetime, stop: datetime) -> list[Any]:
        return [item for item in items if start <= item.observed_at <= stop]

    samples = clip(
        source.metric_samples(start=window_start.date(), end=window_end.date(), path=telemetry_db),
        window_start,
        window_end,
    )
    prior_metric_samples = clip(
        source.metric_samples(start=prior_start.date(), end=prior_end.date(), path=telemetry_db),
        prior_start,
        prior_end,
    )
    kills = clip(
        source.kill_events(start=window_start.date(), end=window_end.date(), path=telemetry_db),
        window_start,
        window_end,
    )
    prior_kill_events = clip(
        source.kill_events(start=prior_start.date(), end=prior_end.date(), path=telemetry_db),
        prior_start,
        prior_end,
    )
    process_samples = clip(
        source.process_memory_samples(
            start=window_start.date(), end=window_end.date(), path=telemetry_db
        ),
        window_start,
        window_end,
    )
    io_samples = clip(
        source.process_io_delta_samples(
            start=window_start.date(), end=window_end.date(), path=telemetry_db
        ),
        window_start,
        window_end,
    )

    return build_machine_explain_report(
        window_start=window_start,
        window_end=window_end,
        samples=samples,
        kills=kills,
        process_samples=process_samples,
        io_samples=io_samples,
        prior_samples=prior_metric_samples,
        prior_kills=prior_kill_events,
        health_ledger=health_ledger,
    )


# ── text renderer ────────────────────────────────────────────────────────────


def _local(ts: datetime) -> str:
    return ts.astimezone().strftime("%m-%d %H:%M")


def _fmt_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60}h{minutes % 60:02d}m"
    return f"{minutes}m"


def render_machine_explain_text(report: MachineExplainReport, *, episode_limit: int = 8) -> str:
    lines: list[str] = []
    agg = report.aggregates
    host = report.host or "unknown-host"
    lines.append(
        f"MACHINE EXPLAIN — {host} — {_local(report.window_start)} → {_local(report.window_end)} "
        f"(local time; {((report.window_end - report.window_start).total_seconds() / 3600):.0f}h window)"
    )
    lines.append(
        f"Calibration: {report.calibration['provenance']}; swap total {SWAP_TOTAL_MB} MB, "
        f"swap-critical ≥{SWAP_CRITICAL_RATIO:.0%}, psi_full degraded ≥{PSI_FULL_DEGRADED:.0f} / "
        f"frozen ≥{PSI_FULL_FROZEN:.0f}; episode: mem_some ≥{EPISODE_MEM_SOME:.0f} | "
        f"io_full ≥{EPISODE_IO_FULL:.0f} | avail <{EPISODE_AVAIL_FLOOR_MB} MB"
    )
    lines.append("")
    lines.append(
        f"STALL TIME: memory degraded {agg.memory_degraded_hours:.1f}h "
        f"(frozen {agg.memory_frozen_hours:.1f}h) · io frozen {agg.io_frozen_hours:.1f}h"
    )

    lines.append("")
    if report.episodes:
        shown = sorted(report.episodes, key=lambda e: e.duration_seconds, reverse=True)[:episode_limit]
        shown = sorted(shown, key=lambda e: e.started_at)
        lines.append(
            f"PRESSURE EPISODES ({agg.episode_count} total"
            + (f", {episode_limit} longest shown" if agg.episode_count > episode_limit else "")
            + "):"
        )
        for episode in shown:
            peaks: list[str] = []
            if episode.peak_memory_psi_full is not None:
                peaks.append(f"mem_full {episode.peak_memory_psi_full:.0f}")
            if episode.peak_io_psi_full is not None:
                peaks.append(f"io_full {episode.peak_io_psi_full:.0f}")
            if episode.max_swap_ratio is not None:
                peaks.append(f"swap {episode.max_swap_ratio:.0%}")
            if episode.min_mem_avail_mb is not None:
                peaks.append(f"avail min {episode.min_mem_avail_mb} MB")
            head = (
                f"  {_local(episode.started_at)}–{episode.ended_at.astimezone().strftime('%H:%M')} "
                f"({_fmt_duration(episode.duration_seconds)}) {episode.cluster} — peak {', '.join(peaks)}"
            )
            if episode.kill_count:
                head += f" — {episode.kill_count} kill(s)"
            lines.append(head)
            if episode.culprits:
                lines.append("      culprits: " + "; ".join(episode.culprits))
    else:
        lines.append("PRESSURE EPISODES: none crossed the episode thresholds")

    lines.append("")
    if report.kills:
        fine = agg.kills_machine_was_fine
        lines.append(
            f"KILLS ({agg.kill_count} after warning-dedup; {fine} while the machine was not stalling):"
        )
        for kill in report.kills:
            psi = (
                f"mem PSI some {kill.memory_psi_some_at_kill:.1f}"
                if kill.memory_psi_some_at_kill is not None
                else "no PSI sample within 60s"
            )
            rss = f", {kill.victim_rss_mib} MiB" if kill.victim_rss_mib is not None else ""
            lines.append(
                f"  {_local(kill.observed_at)} {kill.killer} → {kill.victim_comm or '?'}{rss} — {psi} → {kill.verdict}"
            )
    else:
        lines.append("KILLS: none recorded in window")

    lines.append("")
    if report.coverage_gaps:
        total = sum(gap.seconds for gap in report.coverage_gaps)
        spans = ", ".join(
            f"{_local(gap.started_at)}–{gap.ended_at.astimezone().strftime('%H:%M')}"
            for gap in report.coverage_gaps
        )
        lines.append(
            f"TELEMETRY COVERAGE: {len(report.coverage_gaps)} gap(s) totaling "
            f"{_fmt_duration(total)} ({spans}) — gaps are missing coverage, not calm"
        )
    else:
        lines.append("TELEMETRY COVERAGE: continuous (no gap >10m in the metric stream)")

    if report.health_transitions:
        lines.append(f"HEALTH TRANSITIONS ({report.health_ledger_note}):")
        for item in report.health_transitions[:6]:
            lines.append(
                f"  {item.unit} {item.kind} went {item.last_status} "
                f"{item.bad_count}x, last {_local(item.last_bad_at)}"
            )
    elif report.health_ledger_available:
        lines.append(f"HEALTH TRANSITIONS: none unhealthy in window ({report.health_ledger_note})")
    else:
        lines.append(f"HEALTH TRANSITIONS: {report.health_ledger_note}")

    lines.append("")
    if report.prior_week is not None:
        prior = report.prior_week
        lines.append(
            f"WEEK-OVER-WEEK ({report.prior_week_note}): episodes {prior.episode_count}→{agg.episode_count}; "
            f"mem degraded {prior.memory_degraded_hours:.1f}h→{agg.memory_degraded_hours:.1f}h; "
            f"io frozen {prior.io_frozen_hours:.1f}h→{agg.io_frozen_hours:.1f}h; "
            f"kills {prior.kill_count}→{agg.kill_count}"
        )
        current_clusters = set(agg.episode_hours_by_cluster) | set(prior.episode_hours_by_cluster)
        interesting = [
            f"{cluster} {prior.episode_hours_by_cluster.get(cluster, 0.0):.1f}h→"
            f"{agg.episode_hours_by_cluster.get(cluster, 0.0):.1f}h"
            for cluster in sorted(current_clusters)
            if cluster != "MINOR"
        ]
        if interesting:
            lines.append("  by cluster: " + "; ".join(interesting))
    else:
        lines.append(f"WEEK-OVER-WEEK: {report.prior_week_note}")

    for caveat in report.caveats:
        lines.append(f"CAVEAT: {caveat}")
    return "\n".join(lines)


__all__ = [
    "CoverageGap",
    "HealthTransitionSummary",
    "KillAssessment",
    "MachineExplainReport",
    "PressureEpisode",
    "WindowAggregates",
    "build_machine_explain_report",
    "machine_explain",
    "render_machine_explain_text",
]
