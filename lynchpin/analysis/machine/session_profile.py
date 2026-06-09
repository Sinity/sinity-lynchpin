"""Per-agent-session causal machine footprint.

For each agent session (one sinnix-agent-* scope), this module answers:
  - How long did it run?
  - How much IO did the agent process itself generate?
  - Which build scopes did it causally trigger (temporal attribution)?
  - What was the total machine cost (agent IO + all triggered build IO)?
  - Which projects was it working on (via polylogue enrichment)?
  - Was this session contributing to a contention event?

The distinction from workloads.py:
  workloads.py   → IO accounting by kind (what ran, how much)
  session_profile → causal chains (what agent caused what, total impact)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from .timeline import ScopeRun, scope_runs
from .workloads import WorkloadCoPresence, co_presence_windows

__all__ = [
    "SessionProfile",
    "MachineSessionDay",
    "session_profiles",
    "machine_session_days",
]

_HEAVY_BUILD_KINDS = {"build", "nix_build"}


@dataclass
class SessionProfile:
    """Full causal footprint of one agent session.

    triggered_builds are ScopeRun objects whose temporal attribution
    points to this session (started while this was the only active agent).
    unattributed_builds are scopes active during this session's window
    that could not be unambiguously assigned (multiple agents overlapping).
    """

    scope_unit: str
    first_seen: datetime
    last_seen: datetime
    agent_io_mb: float
    polylogue_session_id: str | None
    polylogue_project: str | None

    triggered_builds: list[ScopeRun] = field(default_factory=list)
    concurrent_agent_count: int = 1  # how many agents overlapped at any point

    @property
    def duration_minutes(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds() / 60

    @property
    def triggered_build_io_mb(self) -> float:
        return sum(r.total_io_mb for r in self.triggered_builds)

    @property
    def triggered_nix_build_count(self) -> int:
        return sum(1 for r in self.triggered_builds if r.kind == "nix_build")

    @property
    def triggered_rust_build_count(self) -> int:
        # Build scopes with cargo/rustc as primary comm
        return sum(
            1 for r in self.triggered_builds
            if r.kind == "build" and r.primary_comm in {"cargo", "rustc", "sccache", "xtask"}
        )

    @property
    def triggered_test_count(self) -> int:
        return sum(
            1 for r in self.triggered_builds
            if r.kind == "build" and r.primary_comm in {"pytest", ".pytest-wrapped", "cargo-nextest"}
        )

    @property
    def total_machine_io_mb(self) -> float:
        return self.agent_io_mb + self.triggered_build_io_mb

    @property
    def is_high_impact(self) -> bool:
        return self.total_machine_io_mb > 10_000 or len(self.triggered_builds) > 20

    def to_dict(self) -> dict:
        # Summarize triggered builds by primary comm rather than listing all
        build_by_comm: dict[str, int] = defaultdict(int)
        for r in self.triggered_builds:
            build_by_comm[r.primary_comm] += 1

        return {
            "scope_unit": self.scope_unit,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "duration_minutes": round(self.duration_minutes, 1),
            "agent_io_mb": round(self.agent_io_mb, 1),
            "triggered_build_count": len(self.triggered_builds),
            "triggered_nix_builds": self.triggered_nix_build_count,
            "triggered_rust_builds": self.triggered_rust_build_count,
            "triggered_tests": self.triggered_test_count,
            "triggered_build_io_mb": round(self.triggered_build_io_mb, 1),
            "total_machine_io_mb": round(self.total_machine_io_mb, 1),
            "builds_by_comm": dict(build_by_comm),
            "concurrent_agent_count": self.concurrent_agent_count,
            "polylogue_session_id": self.polylogue_session_id,
            "polylogue_project": self.polylogue_project,
            "is_high_impact": self.is_high_impact,
        }


@dataclass
class MachineSessionDay:
    """All agent sessions and their causal footprints for one calendar day."""

    date: date
    sessions: list[SessionProfile] = field(default_factory=list)
    contention_windows: list[WorkloadCoPresence] = field(default_factory=list)

    @property
    def total_agent_io_mb(self) -> float:
        return sum(s.agent_io_mb for s in self.sessions)

    @property
    def total_triggered_io_mb(self) -> float:
        return sum(s.triggered_build_io_mb for s in self.sessions)

    @property
    def total_machine_io_mb(self) -> float:
        return sum(s.total_machine_io_mb for s in self.sessions)

    @property
    def peak_concurrent_agents(self) -> int:
        if not self.sessions:
            return 0
        return max(s.concurrent_agent_count for s in self.sessions)

    @property
    def high_impact_sessions(self) -> list[SessionProfile]:
        return [s for s in self.sessions if s.is_high_impact]

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "session_count": len(self.sessions),
            "peak_concurrent_agents": self.peak_concurrent_agents,
            "total_agent_io_mb": round(self.total_agent_io_mb, 1),
            "total_triggered_io_mb": round(self.total_triggered_io_mb, 1),
            "total_machine_io_mb": round(self.total_machine_io_mb, 1),
            "contention_window_count": len(self.contention_windows),
            "contention_total_minutes": round(
                sum(w.duration_minutes for w in self.contention_windows), 1
            ),
            "high_impact_session_count": len(self.high_impact_sessions),
            "sessions": [s.to_dict() for s in self.sessions],
            "contention_windows": [w.to_dict() for w in self.contention_windows],
        }


def _polylogue_enrich(sessions: list[SessionProfile]) -> None:
    """Enrich sessions with polylogue session IDs and project names."""
    try:
        from ...sources.polylogue import iter_session_profiles  # noqa: PLC0415
        profiles = list(iter_session_profiles())
    except Exception:
        return

    for rec in sessions:
        best_overlap = 0.0
        rec_s = rec.first_seen.timestamp()
        rec_e = rec.last_seen.timestamp()
        for p in profiles:
            p_start = getattr(p, "session_start", None) or getattr(p, "started_at", None)
            p_end = getattr(p, "session_end", None) or getattr(p, "ended_at", None)
            if p_start is None:
                continue
            ps = p_start.timestamp() if hasattr(p_start, "timestamp") else float(p_start)
            pe = (p_end.timestamp() if hasattr(p_end, "timestamp") else float(p_end)) if p_end else ps + 3600
            overlap = max(0.0, min(rec_e, pe) - max(rec_s, ps))
            if overlap > best_overlap:
                best_overlap = overlap
                if overlap > 60:
                    rec.polylogue_session_id = str(getattr(p, "session_id", None) or getattr(p, "conversation_id", ""))
                    rec.polylogue_project = str(getattr(p, "project", None) or "")


def session_profiles(
    start: date | None = None,
    end: date | None = None,
) -> list[SessionProfile]:
    """Build a causal SessionProfile for every agent session in the window.

    Uses temporal attribution from scope_runs(): build scopes started while
    exactly one agent scope was active are counted as triggered by that agent.
    """
    # Fetch a slightly wider window for carryover build scopes
    fetch_start = (start - timedelta(days=1)) if start else None
    runs = scope_runs(start=fetch_start, end=end)

    agent_runs = [r for r in runs if r.kind == "agent"]
    if not agent_runs:
        return []

    # Build a map from scope unit → ScopeRun for agents
    agent_by_unit: dict[str, ScopeRun] = {r.unit: r for r in agent_runs}

    # Collect builds attributed to each agent
    triggered: dict[str, list[ScopeRun]] = defaultdict(list)
    for r in runs:
        if r.kind not in _HEAVY_BUILD_KINDS:
            continue
        if r.attributed_agent and r.attributed_agent in agent_by_unit:
            triggered[r.attributed_agent].append(r)

    # Compute peak concurrent agents for each session window using sweep line
    # (how many other agent scopes overlapped with this one)
    events: list[tuple[datetime, int, str]] = []
    for r in agent_runs:
        events.append((r.first_seen, +1, r.unit))
        events.append((r.last_seen, -1, r.unit))
    events.sort(key=lambda x: x[0])

    # For each agent, find the max concurrent count during its window
    peak_concurrent: dict[str, int] = {}
    for ar in agent_runs:
        active = sum(
            1 for r in agent_runs
            if r.first_seen <= ar.last_seen and r.last_seen >= ar.first_seen
        )
        peak_concurrent[ar.unit] = active

    profiles = [
        SessionProfile(
            scope_unit=ar.unit,
            first_seen=ar.first_seen,
            last_seen=ar.last_seen,
            agent_io_mb=ar.total_io_mb,
            polylogue_session_id=None,
            polylogue_project=None,
            triggered_builds=triggered.get(ar.unit, []),
            concurrent_agent_count=peak_concurrent.get(ar.unit, 1),
        )
        for ar in agent_runs
        # Filter to requested start window after carryover fetch
        if start is None or ar.last_seen.date() >= start
    ]

    _polylogue_enrich(profiles)
    return sorted(profiles, key=lambda p: p.first_seen)


def machine_session_days(
    start: date | None = None,
    end: date | None = None,
) -> list[MachineSessionDay]:
    """Day-by-day view of agent sessions with causal build chains and contention."""
    profiles = session_profiles(start=start, end=end)
    contention = co_presence_windows(start=start, end=end, min_kinds=2)

    # Group sessions by day of first_seen
    by_day: dict[date, list[SessionProfile]] = defaultdict(list)
    for p in profiles:
        by_day[p.first_seen.date()].append(p)

    # Group contention windows by day
    contention_by_day: dict[date, list[WorkloadCoPresence]] = defaultdict(list)
    for w in contention:
        contention_by_day[w.start.date()].append(w)

    all_days = sorted(set(by_day) | set(contention_by_day))
    return [
        MachineSessionDay(
            date=d,
            sessions=by_day.get(d, []),
            contention_windows=contention_by_day.get(d, []),
        )
        for d in all_days
    ]
