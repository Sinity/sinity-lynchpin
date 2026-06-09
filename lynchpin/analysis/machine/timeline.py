"""Machine workload timeline with temporal attribution.

Reconstructs a chronological view of all sinnix-scoped activity on the
machine: agent sessions, user builds, nix builds, and background tasks.

Temporal attribution: build/nix-build scopes that started while exactly
one agent scope was active are attributed to that agent. When multiple
agents overlap at scope-start time the attribution is ambiguous.

Core concepts:
  ScopeRun     one sinnix-*-TIMESTAMP-PID.scope lifetime
  DayReport    per-day summary with all scope runs + contention + pressure
  HourSlice    hourly active-scope counts for heatmap rendering
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ...core.parse import as_local
from ...sources.machine import metric_samples, process_io_delta_samples

__all__ = [
    "ScopeKind",
    "ScopeRun",
    "DayReport",
    "HourSlice",
    "scope_runs",
    "day_report",
    "machine_timeline",
    "hourly_heatmap",
]

_MB = 1024 * 1024

_SCOPE_RE = re.compile(
    r"(sinnix-(agent|build|nix-build|background))-(\d{15,})-(\d+)\.scope"
)

_KIND_MAP = {
    "agent": "agent",
    "build": "build",
    "nix-build": "nix_build",
    "background": "background",
}


class ScopeKind(str):
    AGENT = "agent"
    BUILD = "build"
    NIX_BUILD = "nix_build"
    BACKGROUND = "background"


@dataclass
class ScopeRun:
    """One sinnix scope unit lifetime — all processes that ran inside it."""

    unit: str               # full unit name without .scope
    kind: str               # agent | build | nix_build | background
    first_seen: datetime
    last_seen: datetime
    total_io_mb: float
    processes: dict[str, float]  # comm -> io_mb
    attributed_agent: str | None = None  # unit name of the agent that spawned it

    @property
    def duration_minutes(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds() / 60

    @property
    def duration_hours(self) -> float:
        return self.duration_minutes / 60

    @property
    def is_orphan(self) -> bool:
        return self.kind != "agent" and self.duration_hours > 4

    @property
    def primary_comm(self) -> str:
        if not self.processes:
            return "unknown"
        return max(self.processes, key=lambda k: self.processes[k])

    def to_dict(self) -> dict:
        return {
            "unit": self.unit,
            "kind": self.kind,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "duration_minutes": round(self.duration_minutes, 1),
            "total_io_mb": round(self.total_io_mb, 1),
            "primary_comm": self.primary_comm,
            "processes": {k: round(v, 1) for k, v in
                          sorted(self.processes.items(), key=lambda x: -x[1])[:8]},
            "attributed_agent": self.attributed_agent,
            "is_orphan": self.is_orphan,
        }


def scope_runs(
    start: date | None = None,
    end: date | None = None,
) -> list[ScopeRun]:
    """Return all sinnix scope runs sorted by first_seen.

    Each ScopeRun represents one transient systemd scope — one invocation of
    sinnix-scope. Temporal attribution assigns build/nix-build scopes to the
    agent scope that was active at their start time (unambiguous only when
    exactly one agent scope is active).
    """
    raw: dict[str, dict] = {}

    for s in process_io_delta_samples(start=start, end=end):
        if not s.cgroup:
            continue
        m = _SCOPE_RE.search(s.cgroup)
        if not m:
            continue
        unit = m.group(1) + "-" + m.group(3) + "-" + m.group(4)
        kind = _KIND_MAP.get(m.group(2), "other")
        if unit not in raw:
            raw[unit] = {
                "kind": kind,
                "first": s.observed_at,
                "last": s.observed_at,
                "io": 0,
                "procs": defaultdict(float),
            }
        r = raw[unit]
        if s.observed_at < r["first"]:
            r["first"] = s.observed_at
        if s.observed_at > r["last"]:
            r["last"] = s.observed_at
        r["io"] += s.total_bytes_delta
        if s.comm:
            r["procs"][s.comm] += s.total_bytes_delta / _MB

    runs = [
        ScopeRun(
            unit=unit,
            kind=r["kind"],
            first_seen=r["first"],
            last_seen=r["last"],
            total_io_mb=r["io"] / _MB,
            processes=dict(r["procs"]),
        )
        for unit, r in raw.items()
    ]
    runs.sort(key=lambda r: r.first_seen)

    # Temporal attribution: for each non-agent scope, find agent scopes
    # that were active when it started.
    agent_runs = [r for r in runs if r.kind == "agent"]
    for run in runs:
        if run.kind == "agent":
            continue
        t = run.first_seen
        active_agents = [
            a for a in agent_runs
            if a.first_seen <= t <= a.last_seen
        ]
        if len(active_agents) == 1:
            run.attributed_agent = active_agents[0].unit

    return runs


@dataclass
class HourSlice:
    """Per-hour activity counts for a heatmap view."""

    hour: datetime   # UTC hour start
    active_agents: int = 0
    active_builds: int = 0
    active_nix_builds: int = 0
    io_pressure: float | None = None  # PSI full avg300

    def to_dict(self) -> dict:
        return {
            "hour": self.hour.isoformat(),
            "active_agents": self.active_agents,
            "active_builds": self.active_builds,
            "active_nix_builds": self.active_nix_builds,
            "io_pressure": self.io_pressure,
        }


def hourly_heatmap(
    start: date | None = None,
    end: date | None = None,
) -> list[HourSlice]:
    """Per-hour count of active scopes + IO pressure.

    Useful for spotting when the machine is regularly overloaded — e.g.
    'every evening 3+ agent scopes and 20+ build scopes are active
    simultaneously.'
    """
    runs = scope_runs(start=start, end=end)
    if not runs:
        return []

    window_start = runs[0].first_seen.replace(minute=0, second=0, microsecond=0)
    window_end = runs[-1].last_seen.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    hours: list[HourSlice] = []
    cur = window_start
    while cur < window_end:
        nxt = cur + timedelta(hours=1)
        sl = HourSlice(hour=cur)
        for r in runs:
            if r.first_seen < nxt and r.last_seen >= cur:
                if r.kind == "agent":
                    sl.active_agents += 1
                elif r.kind == "build":
                    sl.active_builds += 1
                elif r.kind == "nix_build":
                    sl.active_nix_builds += 1
        hours.append(sl)
        cur = nxt

    # Attach IO pressure from metric_samples
    pressure: dict[datetime, list[float]] = defaultdict(list)
    for s in metric_samples(start=start, end=end):
        if s.io_psi_full_avg300 is not None:
            h = s.observed_at.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            pressure[h].append(s.io_psi_full_avg300)
    for sl in hours:
        vals = pressure.get(sl.hour)
        if vals:
            sl.io_pressure = round(sum(vals) / len(vals), 4)

    return hours


@dataclass
class DayReport:
    """Complete picture of one day's machine activity."""

    date: date
    agent_sessions: list[ScopeRun] = field(default_factory=list)
    build_runs: list[ScopeRun] = field(default_factory=list)
    nix_build_runs: list[ScopeRun] = field(default_factory=list)
    # Scopes that were still alive at midnight (started previous day)
    carryover_scopes: list[str] = field(default_factory=list)

    @property
    def orphan_count(self) -> int:
        return sum(1 for r in self.build_runs + self.nix_build_runs if r.is_orphan)

    @property
    def peak_simultaneous_agents(self) -> int:
        return _peak_simultaneous([r for r in self.agent_sessions])

    @property
    def peak_simultaneous_builds(self) -> int:
        return _peak_simultaneous(self.build_runs + self.nix_build_runs)

    @property
    def total_io_gb(self) -> float:
        all_runs = self.agent_sessions + self.build_runs + self.nix_build_runs
        return sum(r.total_io_mb for r in all_runs) / 1024

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "agent_sessions": [r.to_dict() for r in self.agent_sessions],
            "build_runs": [r.to_dict() for r in
                           sorted(self.build_runs, key=lambda r: -r.total_io_mb)[:20]],
            "nix_build_runs": [r.to_dict() for r in
                                sorted(self.nix_build_runs, key=lambda r: -r.total_io_mb)[:20]],
            "summary": {
                "agent_session_count": len(self.agent_sessions),
                "build_count": len(self.build_runs),
                "nix_build_count": len(self.nix_build_runs),
                "orphan_count": self.orphan_count,
                "peak_simultaneous_agents": self.peak_simultaneous_agents,
                "peak_simultaneous_builds": self.peak_simultaneous_builds,
                "total_io_gb": round(self.total_io_gb, 1),
                "carryover_scope_count": len(self.carryover_scopes),
            },
        }


def _peak_simultaneous(runs: list[ScopeRun]) -> int:
    """Maximum number of overlapping runs at any point in time."""
    events: list[tuple[datetime, int]] = []
    for r in runs:
        events.append((r.first_seen, 1))
        events.append((r.last_seen, -1))
    events.sort()
    peak, cur = 0, 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def day_report(target_date: date) -> DayReport:
    """Build a complete DayReport for one calendar day (local time).

    Includes scopes that were active at any point during the day, even if
    they started the previous day or end the next.
    """
    # Fetch a wider window to catch carryover scopes
    fetch_start = target_date - timedelta(days=2)
    fetch_end = target_date + timedelta(days=1)
    runs = scope_runs(start=fetch_start, end=fetch_end)

    # Day boundaries in UTC (approximate: use midnight UTC as proxy)
    day_start = datetime(target_date.year, target_date.month, target_date.day,
                         tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    def active_during_day(r: ScopeRun) -> bool:
        return r.first_seen < day_end and r.last_seen >= day_start

    report = DayReport(date=target_date)
    for r in runs:
        if not active_during_day(r):
            continue
        if r.kind == "agent":
            report.agent_sessions.append(r)
        elif r.kind == "build":
            report.build_runs.append(r)
        elif r.kind == "nix_build":
            report.nix_build_runs.append(r)
        if r.first_seen < day_start:
            report.carryover_scopes.append(r.unit)

    return report


def machine_timeline(
    start: date | None = None,
    end: date | None = None,
) -> list[DayReport]:
    """Day-by-day machine activity reports over a date range."""
    runs = scope_runs(start=start, end=end)
    if not runs:
        return []

    first_date = as_local(runs[0].first_seen).date()
    last_date = as_local(runs[-1].last_seen).date()

    days = []
    cur = first_date
    while cur <= last_date:
        days.append(day_report(cur))
        cur += timedelta(days=1)
    return days
