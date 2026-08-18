"""Known-signature detectors for 'machine explain' (sinnix-6o2 §3).

The host's failure-mode library encoded as testable predicates over a window
of telemetry: each detector returns a :class:`SignatureFinding` — fires or
not, a confidence, the evidence rows behind the claim (metric, value, and the
baseline it was measured against), the date this incident class was last
seen (from the 2026-07-06 incident-timeline synthesis this bead cites), and a
playbook string naming the known mitigation.

Calibration: thresholds are read from the materialized observational
baselines product (:mod:`lynchpin.analysis.machine.baselines`) when one
exists, and otherwise fall back to a hardcoded floor marked
``TODO-recalibrate`` (never a live DuckDB connection — that would break the
report's <5s live-read AC) or, where a taxonomy constant already exists in
:mod:`lynchpin.analysis.machine.explain` (shared with the hub's ``/pressure/``
page), that constant. Missing baseline data is reported as missing, never
silently treated as "no signal": every finding carries the baseline note it
was actually evaluated against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from lynchpin.sources.machine_models import (
    MachineKillEvent,
    MachineMetricSample,
    MachineProcessMemoryGrowth,
)

# ── evidence / finding shapes ────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceRow:
    observed_at: datetime
    metric: str
    value: float
    baseline: float | None


@dataclass(frozen=True)
class SignatureFinding:
    name: str
    fires: bool
    confidence: float  # 0..1; 0 when the detector could not evaluate at all
    evidence: tuple[EvidenceRow, ...]
    last_seen_incident: date | None
    playbook: str
    threshold_note: str
    caveats: tuple[str, ...] = field(default_factory=tuple)


# ── baseline lookup (materialized artifact only, never live DuckDB) ─────────


def _baseline_band(metric: str) -> tuple[float | None, float | None, str]:
    """(median, mad, note) for ``metric`` from the materialized baselines
    artifact if one exists on disk. Reads the file directly — never calls
    ``analyze_machine_observational_baselines`` or otherwise opens the
    DuckDB substrate, since that would blow the live-read report's 5s AC.
    """
    from lynchpin.core.io import load_json_if_exists, resolve_analysis_path

    payload = load_json_if_exists(resolve_analysis_path("machine_observational_baselines.json"))
    if not isinstance(payload, dict):
        return None, None, "signal unavailable: machine_observational_baselines.json not materialized"
    groups = [g for g in payload.get("by_source", []) if isinstance(g, dict)]
    groups.sort(key=lambda g: g.get("sample_count", 0), reverse=True)
    for group in groups:
        for band in group.get("metrics", []):
            if band.get("metric") == metric and band.get("median") is not None:
                return (
                    band["median"],
                    band.get("mad"),
                    f"baseline median {band['median']:.3g} (source={group.get('key')}, n={group.get('sample_count')})",
                )
    return None, None, f"signal unavailable: no baseline band for {metric!r} in materialized artifact"


def _threshold(
    metric: str,
    *,
    k: float,
    direction: str,
    floor: float,
    floor_note: str,
) -> tuple[float, str]:
    """A high/low threshold for ``metric``: baseline-derived when a
    materialized band exists, else the caller's hardcoded floor.
    """
    median, mad, note = _baseline_band(metric)
    if median is not None:
        mad_value = mad if mad is not None else 0.0
        spread = mad_value if mad_value > 1e-9 else max(abs(median) * 0.1, 1e-9)
        value = median + k * spread if direction == "high" else median - k * spread
        return value, note
    return floor, floor_note


# ── shared taxonomy constants (lazy import breaks the explain<->signatures cycle) ──


def _explain_constants() -> Any:
    from lynchpin.analysis.machine import explain as _explain

    return _explain


# ── per-sample delta helpers ─────────────────────────────────────────────────


def _rate_series(
    samples: Sequence[MachineMetricSample], extractor: Any
) -> list[tuple[datetime, float]]:
    """Hourly rate of a monotone cumulative counter between consecutive samples.

    A negative delta (counter reset, e.g. a reboot) is dropped rather than
    reported as a negative rate.
    """
    ordered = sorted(samples, key=lambda s: s.observed_at)
    out: list[tuple[datetime, float]] = []
    prev_ts: datetime | None = None
    prev_val: float | None = None
    for sample in ordered:
        value = extractor(sample)
        if value is None:
            prev_ts, prev_val = None, None
            continue
        if prev_ts is not None and prev_val is not None:
            hours = (sample.observed_at - prev_ts).total_seconds() / 3600.0
            if hours > 0:
                delta = value - prev_val
                if delta >= 0:
                    out.append((sample.observed_at, delta / hours))
        prev_ts, prev_val = sample.observed_at, value
    return out


def _by_ts(series: Sequence[tuple[datetime, float]]) -> dict[datetime, float]:
    return dict(series)


def _sample_by_ts(samples: Sequence[MachineMetricSample]) -> dict[datetime, MachineMetricSample]:
    return {s.observed_at: s for s in samples}


# ── 1. cache-thrash ───────────────────────────────────────────────────────────

CACHE_THRASH_REFAULT_FLOOR_PER_HOUR = 5_000_000.0  # pages/hour; TODO-recalibrate
CACHE_THRASH_SWAP_QUIET_FLOOR_PER_HOUR = 50.0  # pages/hour; TODO-recalibrate


def detect_cache_thrash(
    samples: Sequence[MachineMetricSample], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """workingset_refault_file rate >> baseline AND pswpin/out ~0 AND mem PSI some elevated."""
    explain = _explain_constants()
    refault = _rate_series(samples, lambda s: s.vmstat_workingset_refault_file)
    pswpin = _by_ts(_rate_series(samples, lambda s: s.vmstat_pswpin))
    pswpout = _by_ts(_rate_series(samples, lambda s: s.vmstat_pswpout))
    by_ts = _sample_by_ts(samples)
    threshold, note = _threshold(
        "vmstat_workingset_refault_file_rate",
        k=3.0,
        direction="high",
        floor=CACHE_THRASH_REFAULT_FLOOR_PER_HOUR,
        floor_note=f"TODO-recalibrate: hardcoded floor {CACHE_THRASH_REFAULT_FLOOR_PER_HOUR:.0f} pages/h, no materialized baseline yet",
    )
    evidence: list[EvidenceRow] = []
    for ts, rate in refault:
        if rate < threshold:
            continue
        swap_rate = pswpin.get(ts, 0.0) + pswpout.get(ts, 0.0)
        sample_at_ts = by_ts.get(ts)
        mem_some = (sample_at_ts.memory_psi_some_avg10 if sample_at_ts is not None else None) or 0.0
        if swap_rate <= CACHE_THRASH_SWAP_QUIET_FLOOR_PER_HOUR and mem_some >= explain.EPISODE_MEM_SOME:
            evidence.append(EvidenceRow(ts, "vmstat_workingset_refault_file_rate_per_hour", rate, threshold))
    return SignatureFinding(
        name="cache-thrash",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 3.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="file-cache refault storm: check for a working set larger than page cache (large build/index job); consider bumping vfs_cache_pressure down, not swap (bead sinnix-6o2)",
        threshold_note=note,
    )


# ── 2. swap-thrash ────────────────────────────────────────────────────────────

SWAP_THRASH_CYCLE_FLOOR_PER_HOUR = 50_000.0  # pages/hour (~200MB/h) on BOTH pswpin and pswpout; TODO-recalibrate


def detect_swap_thrash(
    samples: Sequence[MachineMetricSample], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """Sustained pswpin AND pswpout cycling + PSI mem/io full elevated (the 2026-06-07 class)."""
    explain = _explain_constants()
    pswpin = _rate_series(samples, lambda s: s.vmstat_pswpin)
    pswpout = _by_ts(_rate_series(samples, lambda s: s.vmstat_pswpout))
    by_ts = _sample_by_ts(samples)
    evidence: list[EvidenceRow] = []
    for ts, in_rate in pswpin:
        out_rate = pswpout.get(ts)
        if out_rate is None or in_rate < SWAP_THRASH_CYCLE_FLOOR_PER_HOUR or out_rate < SWAP_THRASH_CYCLE_FLOOR_PER_HOUR:
            continue
        sample = by_ts.get(ts)
        mem_full = (sample.memory_psi_full_avg10 if sample else None) or 0.0
        io_full = (sample.io_psi_full_avg10 if sample else None) or 0.0
        if mem_full >= explain.PSI_FULL_DEGRADED or io_full >= explain.PSI_FULL_DEGRADED:
            evidence.append(EvidenceRow(ts, "vmstat_pswpin_rate_per_hour", in_rate, SWAP_THRASH_CYCLE_FLOOR_PER_HOUR))
            evidence.append(EvidenceRow(ts, "vmstat_pswpout_rate_per_hour", out_rate, SWAP_THRASH_CYCLE_FLOOR_PER_HOUR))
    return SignatureFinding(
        name="swap-thrash",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 6.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="swap cycling under memory/IO stall: this is the 2026-06-07-class signature that showed swappiness=10 was too aggressive on this host — recommend swappiness=5, check for zram/swapfile priority inversion",
        threshold_note=f"cycle floor {SWAP_THRASH_CYCLE_FLOOR_PER_HOUR:.0f} pages/h both directions (TODO-recalibrate, no baseline for pswpin/pswpout rate yet); PSI floor: {explain.PSI_FULL_DEGRADED:.0f} (taxonomy-calibrated, shared with hub /pressure/)",
    )


# ── 3. io-livelock risk ───────────────────────────────────────────────────────

DSTATE_FLOOR_TASKS = 3.0  # TODO-recalibrate: no baseline band exists for a "livelock" dstate reading


def detect_io_livelock_risk(
    samples: Sequence[MachineMetricSample], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """D-state count high + PSI io full high + journal write stall (the 2026-06-03 class).

    Journal write stall has no dedicated telemetry column on this host yet —
    reported as an explicit caveat rather than folded silently into the
    predicate (attribution-spec: missing data is missing, never zero).
    """
    explain = _explain_constants()
    dstate_threshold, dstate_note = _threshold(
        "dstate_task_count",
        k=4.0,
        direction="high",
        floor=DSTATE_FLOOR_TASKS,
        floor_note=f"TODO-recalibrate: hardcoded floor {DSTATE_FLOOR_TASKS:.0f} D-state tasks",
    )
    evidence: list[EvidenceRow] = []
    for sample in sorted(samples, key=lambda s: s.observed_at):
        dstate = sample.dstate_task_count
        io_full = sample.io_psi_full_avg10
        if dstate is None or io_full is None:
            continue
        if dstate >= dstate_threshold and io_full >= explain.SEVERE_IO_PSI_FULL:
            evidence.append(EvidenceRow(sample.observed_at, "dstate_task_count", float(dstate), dstate_threshold))
            evidence.append(EvidenceRow(sample.observed_at, "io_psi_full_avg10", io_full, explain.SEVERE_IO_PSI_FULL))
    return SignatureFinding(
        name="io-livelock-risk",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 6.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="D-state pileup under IO full-stall (2026-06-03 class): check for a slow block device or a filesystem write stall; earlyoom cannot help here since victims are not the mechanism",
        threshold_note=f"{dstate_note}; io_psi_full floor {explain.SEVERE_IO_PSI_FULL:.0f} (taxonomy-calibrated)",
        caveats=("journal write-stall signal unavailable: no dedicated telemetry column captured on this host yet",),
    )


# ── 4. kill-storm ─────────────────────────────────────────────────────────────

KILL_STORM_RATE_FLOOR = 3  # kills within KILL_STORM_WINDOW; TODO-recalibrate
KILL_STORM_WINDOW = timedelta(minutes=10)
KILL_STORM_TINY_VICTIM_MIB = 50  # TODO-recalibrate: "tiny" victim floor


def detect_kill_storm(
    kills: Sequence[MachineKillEvent], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """kill_event rate; victim-size histogram (many tiny victims = degenerate victim choice)."""
    from lynchpin.analysis.machine.explain import _dedup_kills

    deduped = _dedup_kills(kills)
    evidence: list[EvidenceRow] = []
    ordered = sorted(deduped, key=lambda k: k.observed_at)
    for idx, kill in enumerate(ordered):
        window = [k for k in ordered[idx:] if k.observed_at - kill.observed_at <= KILL_STORM_WINDOW]
        if len(window) >= KILL_STORM_RATE_FLOOR:
            evidence.append(EvidenceRow(kill.observed_at, "kill_event_rate_10m", float(len(window)), float(KILL_STORM_RATE_FLOOR)))
    tiny_count = sum(1 for k in deduped if (k.victim_rss_mib or 0) < KILL_STORM_TINY_VICTIM_MIB)
    caveats: tuple[str, ...] = ()
    if evidence and deduped and tiny_count / len(deduped) >= 0.5:
        caveats = (f"degenerate victim choice: {tiny_count}/{len(deduped)} victims under {KILL_STORM_TINY_VICTIM_MIB} MiB RSS",)
    return SignatureFinding(
        name="kill-storm",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 3.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="repeated kills in a short window (2026-07-04 class): check earlyoom victim selection and the process actually driving the pressure, not just the smallest/most-killable one",
        threshold_note=f"TODO-recalibrate: hardcoded floor {KILL_STORM_RATE_FLOOR} kills / {int(KILL_STORM_WINDOW.total_seconds() // 60)}m, no baseline for kill rate yet",
        caveats=caveats,
    )


# ── 5. burst-alloc starvation ─────────────────────────────────────────────────

ALLOCSTALL_FLOOR_PER_HOUR = 5_000.0  # TODO-recalibrate: no baseline for allocstall rate yet


def detect_burst_alloc_starvation(
    samples: Sequence[MachineMetricSample], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """allocstall rate high while MemAvailable high (the 2026-06-29 class)."""
    explain = _explain_constants()
    allocstall = _rate_series(
        samples,
        lambda s: None
        if s.vmstat_allocstall_normal is None and s.vmstat_allocstall_movable is None
        else (s.vmstat_allocstall_normal or 0) + (s.vmstat_allocstall_movable or 0),
    )
    by_ts = _sample_by_ts(samples)
    avail_threshold, avail_note = _threshold(
        "mem_avail_mb",
        k=1.0,
        direction="low",
        floor=explain.MEM_HEALTHY_AVAIL_MB,
        floor_note=f"taxonomy-calibrated floor {explain.MEM_HEALTHY_AVAIL_MB} MB (shared with hub /pressure/)",
    )
    evidence: list[EvidenceRow] = []
    for ts, rate in allocstall:
        if rate < ALLOCSTALL_FLOOR_PER_HOUR:
            continue
        sample = by_ts.get(ts)
        avail = sample.mem_avail_mb if sample else None
        if avail is not None and avail >= avail_threshold:
            evidence.append(EvidenceRow(ts, "vmstat_allocstall_rate_per_hour", rate, ALLOCSTALL_FLOOR_PER_HOUR))
            evidence.append(EvidenceRow(ts, "mem_avail_mb", float(avail), avail_threshold))
    return SignatureFinding(
        name="burst-alloc-starvation",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 6.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="allocation stalls while memory looks free (2026-06-29 class): a fragmentation/zone-imbalance signature, not a genuine shortage — check kswapd behavior and huge-page reservations before blaming a workload for using 'too much' memory",
        threshold_note=f"allocstall floor {ALLOCSTALL_FLOOR_PER_HOUR:.0f}/h (TODO-recalibrate); mem_avail 'high' threshold: {avail_note}",
    )


# ── 6. leak candidates ────────────────────────────────────────────────────────

LEAK_RATE_MB_PER_DAY_FLOOR = 200.0  # bead-specified, not a TODO-recalibrate floor
LEAK_MIN_SPAN = timedelta(hours=6)  # below this, a short burst (e.g. a build job) reads as an absurd GB/day rate


def detect_leak_candidates(
    growth: Sequence[MachineProcessMemoryGrowth], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """Per-process pss_anon monotone growth fit; >200MB/day sustained flags a leak candidate.

    ``growth`` is the first/last pss_anon_kb observation of each process
    lifetime in the window (see
    ``lynchpin.sources.machine.process_memory_growth_candidates``), not raw
    per-sample rows — the two endpoints are exactly what a monotone-growth
    rate needs, and computing them in SQL keeps a 48h leak scan far under the
    per-row cost that a full ``process_memory_samples`` fetch would pay.
    """
    evidence: list[EvidenceRow] = []
    for obs in growth:
        span = obs.last_observed_at - obs.first_observed_at
        if span < LEAK_MIN_SPAN:
            continue
        delta_mb = (obs.last_pss_anon_kb - obs.first_pss_anon_kb) / 1024.0
        if delta_mb <= 0:
            continue
        rate_mb_per_day = delta_mb / (span.total_seconds() / 86400.0)
        if rate_mb_per_day < LEAK_RATE_MB_PER_DAY_FLOOR:
            continue
        label = obs.comm or f"pid {obs.pid}"
        if obs.unit:
            label += f" [{obs.unit}]"
        evidence.append(
            EvidenceRow(
                obs.last_observed_at,
                f"pss_anon_growth_mb_per_day[{label}]",
                round(rate_mb_per_day, 1),
                LEAK_RATE_MB_PER_DAY_FLOOR,
            )
        )
    return SignatureFinding(
        name="leak-candidates",
        fires=bool(evidence),
        confidence=min(1.0, len(evidence) / 2.0) if evidence else 0.0,
        evidence=tuple(evidence),
        last_seen_incident=last_seen_incident,
        playbook="monotone anonymous-PSS growth (the detector that would have named kitty automatically at 45MB->2.9GB): restart the offending process/unit; if it recurs, file the leak upstream",
        threshold_note=f"bead-specified floor {LEAK_RATE_MB_PER_DAY_FLOOR:.0f} MB/day sustained over >= {int(LEAK_MIN_SPAN.total_seconds() // 3600)}h span (not baseline-derived: this is a fixed spec value, not a host-calibrated band)",
    )


# ── 7. writeback pressure ─────────────────────────────────────────────────────

DIRTY_MB_FLOOR = 500  # TODO-recalibrate: the true vm.dirty_bytes limit is not captured telemetry
WRITEBACK_REPEAT_FLOOR = 3


def detect_writeback_pressure(
    samples: Sequence[MachineMetricSample], *, last_seen_incident: date | None = None
) -> SignatureFinding:
    """dirty_kb near the dirty_bytes limit, repeatedly.

    The configured ``vm.dirty_bytes``/``vm.dirty_ratio`` limit is not itself
    captured telemetry on this host, so "near the limit" is a hardcoded
    floor on ``mem_dirty_mb`` rather than a true percentage-of-limit — stated
    as a caveat rather than silently presented as a calibrated fraction.
    """
    evidence: list[EvidenceRow] = []
    for sample in sorted(samples, key=lambda s: s.observed_at):
        if sample.mem_dirty_mb is not None and sample.mem_dirty_mb >= DIRTY_MB_FLOOR:
            evidence.append(EvidenceRow(sample.observed_at, "mem_dirty_mb", float(sample.mem_dirty_mb), float(DIRTY_MB_FLOOR)))
    fires = len(evidence) >= WRITEBACK_REPEAT_FLOOR
    return SignatureFinding(
        name="writeback-pressure",
        fires=fires,
        confidence=min(1.0, len(evidence) / (WRITEBACK_REPEAT_FLOOR * 2)) if fires else 0.0,
        evidence=tuple(evidence) if fires else (),
        last_seen_incident=last_seen_incident,
        playbook="dirty pages repeatedly near the writeback floor: check for a large sequential write competing with fsync-heavy workloads (Postgres, borg); consider vm.dirty_bytes tuning",
        threshold_note=f"TODO-recalibrate: hardcoded floor {DIRTY_MB_FLOOR} MB dirty, >= {WRITEBACK_REPEAT_FLOOR} occurrences (the configured vm.dirty_bytes limit itself is not captured telemetry)",
        caveats=("vm.dirty_bytes/vm.dirty_ratio configured limit unavailable — this floor is an absolute MB guess, not a fraction of the real limit",),
    )


# ── library ───────────────────────────────────────────────────────────────────

SIGNATURE_NAMES: tuple[str, ...] = (
    "cache-thrash",
    "swap-thrash",
    "io-livelock-risk",
    "kill-storm",
    "burst-alloc-starvation",
    "leak-candidates",
    "writeback-pressure",
)


def classify_signatures(
    *,
    samples: Sequence[MachineMetricSample],
    kills: Sequence[MachineKillEvent],
    growth: Sequence[MachineProcessMemoryGrowth],
    last_seen_incidents: dict[str, date] | None = None,
) -> tuple[SignatureFinding, ...]:
    """Evaluate the full known-signature library over one window, fixed order."""
    seen = last_seen_incidents or {}
    return (
        detect_cache_thrash(samples, last_seen_incident=seen.get("cache-thrash")),
        detect_swap_thrash(samples, last_seen_incident=seen.get("swap-thrash")),
        detect_io_livelock_risk(samples, last_seen_incident=seen.get("io-livelock-risk")),
        detect_kill_storm(kills, last_seen_incident=seen.get("kill-storm")),
        detect_burst_alloc_starvation(samples, last_seen_incident=seen.get("burst-alloc-starvation")),
        detect_leak_candidates(growth, last_seen_incident=seen.get("leak-candidates")),
        detect_writeback_pressure(samples, last_seen_incident=seen.get("writeback-pressure")),
    )


def headline_signature(findings: Sequence[SignatureFinding]) -> SignatureFinding | None:
    """The highest-confidence firing signature, or None if the library is calm."""
    fired = [f for f in findings if f.fires]
    if not fired:
        return None
    return max(fired, key=lambda f: f.confidence)


__all__ = [
    "EvidenceRow",
    "SignatureFinding",
    "SIGNATURE_NAMES",
    "classify_signatures",
    "detect_burst_alloc_starvation",
    "detect_cache_thrash",
    "detect_io_livelock_risk",
    "detect_kill_storm",
    "detect_leak_candidates",
    "detect_swap_thrash",
    "detect_writeback_pressure",
    "headline_signature",
]
