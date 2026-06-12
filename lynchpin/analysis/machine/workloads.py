"""Semantic workload presence analysis for sinnix-prime.

Maps raw process IO delta samples to named workload kinds, identifies
co-presence windows where multiple heavy workloads compete for NVMe IO,
and characterises the idle baseline so pressure under load can be measured
as a ratio rather than an absolute.

Workload classification uses two signals:
- comm: process name (primary)
- cgroup: slice path (disambiguates agent-launched from user-launched work)

WorkloadKind taxonomy:
  Always-on daemons:
    SinexStack        sinexd + postgres + nats-server
    PolylogueDaemon   polylogued
  Agent sessions (the agent process itself):
    AgentSession      claude / codex processes
  Agent-spawned transient work (runs inside agent.slice):
    AgentBuild        cargo/rustc/pytest/nix inside agent.slice
  User-initiated transient work (build.slice or interactive):
    RustBuild         cargo/rustc/sccache/xtask compilation
    NixBuild          nix-daemon, nix store operations, nixos rebuilds
    TestSuite         pytest, cargo-nextest
  Background/scheduled:
    Backup            borg
    Browser           chrome, kitty scrollback
    MediaService      stashbox/transmission/media processing
    StorageMaintenance btrfs scrub/balance/fstrim
    Observability     machine telemetry/below
    SystemIO          journald/kernel writeback/system managers
  Other:
    Other             everything else (includes orphan candidates)
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Iterator

from ...core.parse import as_local
from ...sources.machine import metric_samples, process_io_delta_samples

__all__ = [
    "WorkloadKind",
    "WorkloadWindow",
    "DailyWorkloadSummary",
    "WorkloadCoPresence",
    "OrphanProcess",
    "AgentSessionRecord",
    "workload_windows",
    "daily_workload_summary",
    "idle_baseline",
    "co_presence_windows",
    "detect_orphans",
    "agent_session_attribution",
]

_MB = 1024 * 1024


class WorkloadKind(str, Enum):
    AgentSession = "agent_session"
    AgentBuild = "agent_build"       # agent-spawned cargo/pytest/nix (in agent.slice)
    RustBuild = "rust_build"
    NixBuild = "nix_build"
    SinexStack = "sinex_stack"
    PolylogueDaemon = "polylogue_daemon"
    TestSuite = "test_suite"
    Backup = "backup"
    Browser = "browser"
    MediaService = "media_service"
    StorageMaintenance = "storage_maintenance"
    Observability = "observability"
    SystemIO = "system_io"
    Other = "other"


_BUILD_COMMS = re.compile(
    r"^(cargo|rustc|sccache|clippy-driver|cargo-nextest|xtask|rust-analyzer|linker|cc1plus|g\+\+|pytest|\.pytest-wrapped|\[pytest-xdist|nix|nix-daemon|nix-store)$"
)
_AGENT_SLICE = re.compile(r"agent\.slice")

# Maps comm (process name) patterns to WorkloadKind (cgroup-agnostic).
# _classify uses cgroup to promote BUILD_COMMS in agent.slice to AgentBuild.
_COMM_RULES: list[tuple[re.Pattern[str], WorkloadKind]] = [
    (re.compile(r"^(claude|claude\.exe|codex)$"), WorkloadKind.AgentSession),
    (re.compile(r"^(cargo|rustc|sccache|clippy-driver|cargo-nextest|xtask|rust-analyzer|linker|cc1plus|g\+\+)$"), WorkloadKind.RustBuild),
    (re.compile(r"^(nix|nix-daemon|nix-store|nix-collect-garbage)$"), WorkloadKind.NixBuild),
    (re.compile(r"^(pytest|\.pytest-wrapped|\[pytest-xdist)$"), WorkloadKind.TestSuite),
    (re.compile(r"^(sinexd|nats-server|\.postgres-wrapp|postgres|psql)$"), WorkloadKind.SinexStack),
    (re.compile(r"^(\.polylogued-wra|polylogued)$"), WorkloadKind.PolylogueDaemon),
    (re.compile(r"^(\.borg-wrapped|borgbackup|borgbackup-chec|borgbackup-job-)"), WorkloadKind.Backup),
    (re.compile(r"^(chrome|chromium|\.chromium-brows|qutebrowser|firefox|kitty)$"), WorkloadKind.Browser),
    (re.compile(r"^(stash|ffmpeg|transmission-da)$"), WorkloadKind.MediaService),
    (re.compile(r"^(machine-telemetry|below)$"), WorkloadKind.Observability),
    (re.compile(r"^(btrfs|fstrim)$"), WorkloadKind.StorageMaintenance),
    (re.compile(r"^(systemd-journal|btrfs-transaction|kworker/.*)$"), WorkloadKind.SystemIO),
]

# These comms are session-manager parents that double-count child IO via cgroup
# aggregation in /proc/PID/io — exclude to avoid inflation.
_EXCLUDED_COMMS: frozenset[str] = frozenset({"systemd", "init.scope", "bash", "zsh", "sh"})


def _classify(comm: str | None, cgroup: str | None) -> WorkloadKind | None:
    """Return the workload kind for a process, or None to exclude."""
    if not comm:
        return WorkloadKind.Other
    if comm in _EXCLUDED_COMMS:
        return None  # session-manager parents double-count child IO
    if cgroup:
        if "stashbox.service" in cgroup or "transmission.service" in cgroup:
            return WorkloadKind.MediaService
        if (
            "btrfs-scrub" in cgroup
            or "mx500-balance.service" in cgroup
            or "sinnix-fstrim.service" in cgroup
        ):
            return WorkloadKind.StorageMaintenance
        if "machine-telemetry.service" in cgroup or "below.service" in cgroup:
            return WorkloadKind.Observability
        if "systemd-journald.service" in cgroup:
            return WorkloadKind.SystemIO
    # Build-related comms running inside agent.slice are agent-launched work
    if cgroup and _AGENT_SLICE.search(cgroup) and _BUILD_COMMS.match(comm):
        return WorkloadKind.AgentBuild
    for pattern, kind in _COMM_RULES:
        if pattern.match(comm):
            return kind
    return WorkloadKind.Other


@dataclass(frozen=True)
class WorkloadWindow:
    """A time window where a workload kind was active."""

    kind: WorkloadKind
    start: datetime
    end: datetime
    total_io_mb: float
    sample_count: int

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60


@dataclass
class DailyWorkloadSummary:
    """Per-day breakdown of workload IO and presence."""

    date: date
    # IO in MB by workload kind
    io_mb: dict[str, float] = field(default_factory=dict)
    # Number of samples per kind (proxy for presence duration)
    sample_count: dict[str, int] = field(default_factory=dict)
    # Active kinds (those with any IO)
    active_kinds: list[str] = field(default_factory=list)
    # Total IO across all kinds
    total_io_mb: float = 0.0
    # System IO pressure (avg300 from PSI, 0..1)
    io_pressure_avg300: float | None = None
    # Number of co-present heavy workloads (kinds with >100 MB IO)
    heavy_workload_count: int = 0

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "io_mb": self.io_mb,
            "sample_count": self.sample_count,
            "active_kinds": self.active_kinds,
            "total_io_mb": round(self.total_io_mb, 1),
            "io_pressure_avg300": self.io_pressure_avg300,
            "heavy_workload_count": self.heavy_workload_count,
        }


@dataclass(frozen=True)
class WorkloadCoPresence:
    """A period where multiple heavy workloads overlapped."""

    start: datetime
    end: datetime
    kinds: frozenset[str]
    total_io_mb: float

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_minutes": round(self.duration_minutes, 1),
            "kinds": sorted(self.kinds),
            "total_io_mb": round(self.total_io_mb, 1),
        }


def workload_windows(
    *,
    start: date | None = None,
    end: date | None = None,
    gap_s: float = 120.0,
) -> Iterator[WorkloadWindow]:
    """Yield contiguous time windows where a given workload kind had activity.

    Samples are grouped by kind; a new window is started when the inter-sample
    gap exceeds ``gap_s`` seconds.
    """
    # Accumulate samples per kind, then emit windows
    kind_samples: dict[WorkloadKind, list[tuple[datetime, int]]] = defaultdict(list)
    for s in process_io_delta_samples(start=start, end=end):
        kind = _classify(s.comm, s.cgroup)
        if kind is None or kind is WorkloadKind.Other:
            continue
        if s.total_bytes_delta > 0:
            kind_samples[kind].append((s.observed_at, s.total_bytes_delta))

    for kind, samples in kind_samples.items():
        samples.sort(key=lambda x: x[0])
        if not samples:
            continue
        win_start, win_io, win_count = samples[0][0], 0, 0
        prev_ts = samples[0][0]
        for ts, io_bytes in samples:
            gap = (ts - prev_ts).total_seconds()
            if gap > gap_s:
                yield WorkloadWindow(
                    kind=kind,
                    start=win_start,
                    end=prev_ts,
                    total_io_mb=win_io / _MB,
                    sample_count=win_count,
                )
                win_start = ts
                win_io = 0
                win_count = 0
            win_io += io_bytes
            win_count += 1
            prev_ts = ts
        yield WorkloadWindow(
            kind=kind,
            start=win_start,
            end=prev_ts,
            total_io_mb=win_io / _MB,
            sample_count=win_count,
        )


def daily_workload_summary(
    start: date | None = None,
    end: date | None = None,
) -> list[DailyWorkloadSummary]:
    """Return per-day workload IO breakdown across all workload kinds."""
    by_date: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_date_count: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for s in process_io_delta_samples(start=start, end=end):
        kind = _classify(s.comm, s.cgroup)
        if kind is None:
            continue
        d = as_local(s.observed_at).date()
        by_date[d][kind.value] += s.total_bytes_delta / _MB
        by_date_count[d][kind.value] += 1

    # IO pressure per day from system-level PSI
    pressure_by_date: dict[date, list[float]] = defaultdict(list)
    for s in metric_samples(start=start, end=end):
        if s.io_psi_full_avg300 is not None:
            d = as_local(s.observed_at).date()
            pressure_by_date[d].append(s.io_psi_full_avg300)

    result: list[DailyWorkloadSummary] = []
    all_dates = sorted(set(by_date) | set(pressure_by_date))
    for d in all_dates:
        io_mb = dict(by_date[d])
        counts = dict(by_date_count[d])
        total = sum(io_mb.values())
        active = [k for k, v in io_mb.items() if v > 0]
        pressure_vals = pressure_by_date.get(d)
        pressure = sum(pressure_vals) / len(pressure_vals) if pressure_vals else None
        heavy = sum(1 for v in io_mb.values() if v > 100)
        result.append(DailyWorkloadSummary(
            date=d,
            io_mb=io_mb,
            sample_count=counts,
            active_kinds=active,
            total_io_mb=round(total, 1),
            io_pressure_avg300=round(pressure, 4) if pressure is not None else None,
            heavy_workload_count=heavy,
        ))

    return result


def idle_baseline(
    start: date | None = None,
    end: date | None = None,
    heavy_threshold_mb: float = 200.0,
) -> dict:
    """Characterise the machine's IO footprint with no heavy transient workloads.

    'Idle' days are those where agent sessions, Rust builds, Nix builds, test
    suites, and borg backup together produce less than ``heavy_threshold_mb`` MB
    of IO. The always-on workloads (SinexStack, PolylogueDaemon, Browser) are
    included in the baseline.

    Returns a dict with baseline IO figures and a list of idle day examples.
    """
    _TRANSIENT = {
        WorkloadKind.AgentSession.value,
        WorkloadKind.RustBuild.value,
        WorkloadKind.NixBuild.value,
        WorkloadKind.TestSuite.value,
        WorkloadKind.Backup.value,
    }
    days = daily_workload_summary(start=start, end=end)
    idle_days = []
    for d in days:
        transient_io = sum(d.io_mb.get(k, 0.0) for k in _TRANSIENT)
        if transient_io < heavy_threshold_mb:
            idle_days.append(d)

    if not idle_days:
        return {"idle_day_count": 0, "caveats": ["no idle days found in window"]}

    def avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 1) if vals else None

    kinds = list({k for d in idle_days for k in d.io_mb})
    baseline_io: dict[str, float | None] = {}
    for k in kinds:
        vals = [d.io_mb.get(k, 0.0) for d in idle_days]
        baseline_io[k] = avg(vals)

    pressures = [d.io_pressure_avg300 for d in idle_days if d.io_pressure_avg300 is not None]
    totals = [d.total_io_mb for d in idle_days]

    return {
        "idle_day_count": len(idle_days),
        "idle_day_examples": [d.date.isoformat() for d in idle_days[:5]],
        "baseline_total_io_mb_avg": avg(totals),
        "baseline_io_by_kind_mb_avg": baseline_io,
        "baseline_io_pressure_avg300": avg(pressures) if pressures else None,
        "heavy_threshold_mb": heavy_threshold_mb,
    }


def co_presence_windows(
    start: date | None = None,
    end: date | None = None,
    min_kinds: int = 2,
    resolution_s: float = 60.0,
) -> list[WorkloadCoPresence]:
    """Find time windows where multiple heavy workloads overlapped.

    Uses a sliding bucket approach: round sample timestamps to ``resolution_s``
    seconds and find buckets where ≥ ``min_kinds`` distinct non-Other kinds
    had IO activity.
    """
    _HEAVY = {
        WorkloadKind.AgentSession,
        WorkloadKind.AgentBuild,
        WorkloadKind.RustBuild,
        WorkloadKind.NixBuild,
        WorkloadKind.TestSuite,
        WorkloadKind.Backup,
    }
    # bucket -> {kind -> total_io_bytes}
    buckets: dict[int, dict[WorkloadKind, int]] = defaultdict(lambda: defaultdict(int))
    for s in process_io_delta_samples(start=start, end=end):
        kind = _classify(s.comm, s.cgroup)
        if kind is None or kind not in _HEAVY:
            continue
        bucket = int(s.observed_at.timestamp() / resolution_s)
        buckets[bucket][kind] += s.total_bytes_delta

    # Collect buckets with ≥ min_kinds heavy workloads
    hot_buckets = {
        b: kinds for b, kinds in buckets.items() if len(kinds) >= min_kinds
    }
    if not hot_buckets:
        return []

    # Merge consecutive hot buckets into windows
    sorted_b = sorted(hot_buckets)
    windows: list[WorkloadCoPresence] = []
    w_start = sorted_b[0]
    w_kinds: dict[WorkloadKind, int] = dict(hot_buckets[sorted_b[0]])
    w_prev = sorted_b[0]

    for b in sorted_b[1:]:
        if b - w_prev <= 2:  # allow 1-bucket gap
            for k, io in hot_buckets[b].items():
                w_kinds[k] = w_kinds.get(k, 0) + io
            w_prev = b
        else:
            windows.append(WorkloadCoPresence(
                start=datetime.fromtimestamp(w_start * resolution_s, tz=timezone.utc),
                end=datetime.fromtimestamp(w_prev * resolution_s, tz=timezone.utc),
                kinds=frozenset(k.value for k in w_kinds),
                total_io_mb=sum(w_kinds.values()) / _MB,
            ))
            w_start = b
            w_kinds = dict(hot_buckets[b])
            w_prev = b

    windows.append(WorkloadCoPresence(
        start=datetime.fromtimestamp(w_start * resolution_s, tz=timezone.utc),
        end=datetime.fromtimestamp(w_prev * resolution_s, tz=timezone.utc),
        kinds=frozenset(k.value for k in w_kinds),
        total_io_mb=sum(w_kinds.values()) / _MB,
    ))

    return sorted(windows, key=lambda w: w.start)


@dataclass(frozen=True)
class OrphanProcess:
    """A process that has been running for an unusually long time with IO activity.

    These are "zombie workloads" — nix checks, pytest sessions, agent sessions,
    etc. that were never cleaned up and continue to consume IO without any
    visible terminal session.
    """

    comm: str
    cgroup: str | None
    kind: str
    first_seen: datetime
    last_seen: datetime
    total_io_mb: float
    sample_count: int

    @property
    def duration_hours(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds() / 3600

    def to_dict(self) -> dict:
        return {
            "comm": self.comm,
            "cgroup": self.cgroup,
            "kind": self.kind,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "duration_hours": round(self.duration_hours, 1),
            "total_io_mb": round(self.total_io_mb, 1),
            "sample_count": self.sample_count,
        }


def detect_orphans(
    start: date | None = None,
    end: date | None = None,
    min_duration_hours: float = 4.0,
) -> list[OrphanProcess]:
    """Find long-running processes that look like abandoned/orphaned workloads.

    Identifies individual (pid, process_start_time_ticks) pairs that accumulate
    IO samples spanning more than ``min_duration_hours``. These represent single
    process instances (not a class of process) that have been running for hours,
    which for transient work (nix check, pytest, cargo, claude) signals orphaning.

    The always-on daemons (postgres, nats-server, sinexd, polylogued) are
    excluded since multi-hour runtime is expected for them.
    """
    _ALWAYS_ON_COMMS = frozenset({
        "sinexd", "nats-server", ".postgres-wrapp", "postgres",
        ".polylogued-wra", "polylogued", "transmission-da", "below",
        "machine-telemetry", "wireguard", "systemd-journal", "NetworkManager",
        "chrome", "kitty", "Hyprland", "noctalia", "quickshell", "aw-server",
    })
    # (pid, start_ticks) -> {first, last, io_bytes, samples, comm, cgroup}
    procs: dict[tuple[int, int], dict] = {}

    for s in process_io_delta_samples(start=start, end=end):
        if not s.comm or s.comm in _EXCLUDED_COMMS or s.comm in _ALWAYS_ON_COMMS:
            continue
        key = (s.pid, s.process_start_time_ticks)
        if key not in procs:
            procs[key] = {
                "comm": s.comm,
                "cgroup": s.cgroup,
                "first": s.observed_at,
                "last": s.observed_at,
                "io": 0,
                "n": 0,
            }
        p = procs[key]
        if s.observed_at > p["last"]:
            p["last"] = s.observed_at
        if s.observed_at < p["first"]:
            p["first"] = s.observed_at
        p["io"] += s.total_bytes_delta
        p["n"] += 1

    result: list[OrphanProcess] = []
    min_duration_s = min_duration_hours * 3600
    for p in procs.values():
        duration = (p["last"] - p["first"]).total_seconds()
        if duration < min_duration_s:
            continue
        kind = _classify(p["comm"], p["cgroup"])
        result.append(OrphanProcess(
            comm=p["comm"],
            cgroup=p["cgroup"],
            kind=kind.value if kind else "excluded",
            first_seen=p["first"],
            last_seen=p["last"],
            total_io_mb=p["io"] / _MB,
            sample_count=p["n"],
        ))

    return sorted(result, key=lambda o: -o.duration_hours)


_SCOPE_UNIT_RE = re.compile(r"(sinnix-agent-[^/]+)\.scope")
_AGENT_COMMS = frozenset({"claude", "codex", "claude.exe"})


@dataclass
class AgentSessionRecord:
    """One agent process instance plus any direct children sharing its cgroup scope.

    ``scope_unit`` is the systemd transient unit name extracted from the cgroup
    path — unique per ``sinnix-scope agent -- <cmd>`` invocation.
    Direct children are processes that ran inside the same scope (no explicit
    sinnix-scope call); processes that went through sinnix-scope themselves get
    their own build.slice scope and are *not* here.

    Polylogue fields are populated only when a session profile is available
    whose active window overlaps with ``[first_seen, last_seen]``.
    """

    scope_unit: str
    comm: str
    first_seen: datetime
    last_seen: datetime
    agent_io_mb: float
    direct_child_io_mb: dict[str, float] = field(default_factory=dict)
    polylogue_session_id: str | None = None
    polylogue_project: str | None = None

    @property
    def duration_hours(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds() / 3600

    @property
    def total_io_mb(self) -> float:
        return self.agent_io_mb + sum(self.direct_child_io_mb.values())

    def to_dict(self) -> dict:
        return {
            "scope_unit": self.scope_unit,
            "comm": self.comm,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "duration_hours": round(self.duration_hours, 1),
            "agent_io_mb": round(self.agent_io_mb, 1),
            "direct_child_io_mb": {k: round(v, 1) for k, v in self.direct_child_io_mb.items()},
            "total_io_mb": round(self.total_io_mb, 1),
            "polylogue_session_id": self.polylogue_session_id,
            "polylogue_project": self.polylogue_project,
        }


def agent_session_attribution(
    start: date | None = None,
    end: date | None = None,
) -> list[AgentSessionRecord]:
    """Attribute process IO to specific agent sessions via cgroup scope sharing.

    Correlates process-level IO samples by the scope unit embedded in the cgroup
    path (``sinnix-agent-{MONOTONIC_NS}-{PID}.scope``). Processes sharing the
    same scope were spawned by the same agent invocation without going through
    an explicit ``sinnix-scope`` call — their IO is directly attributable.

    Optionally enriches with polylogue session IDs by overlapping time windows.
    """
    # scope_unit -> {agent: {io, first, last, comm}, children: {comm: io}}
    scopes: dict[str, dict] = {}

    for s in process_io_delta_samples(start=start, end=end):
        if not s.cgroup:
            continue
        m = _SCOPE_UNIT_RE.search(s.cgroup)
        if not m:
            continue
        unit = m.group(1)
        if unit not in scopes:
            scopes[unit] = {
                "agent_comm": None,
                "agent_io": 0,
                "first": s.observed_at,
                "last": s.observed_at,
                "children": defaultdict(float),
            }
        sc = scopes[unit]
        if s.observed_at < sc["first"]:
            sc["first"] = s.observed_at
        if s.observed_at > sc["last"]:
            sc["last"] = s.observed_at
        if s.comm in _AGENT_COMMS:
            sc["agent_comm"] = s.comm
            sc["agent_io"] += s.total_bytes_delta
        elif s.comm and s.comm not in _EXCLUDED_COMMS:
            sc["children"][s.comm] += s.total_bytes_delta

    # Only emit scopes that contain a recognisable agent process
    records: list[AgentSessionRecord] = []
    for unit, sc in scopes.items():
        if not sc["agent_comm"]:
            continue
        records.append(AgentSessionRecord(
            scope_unit=unit,
            comm=sc["agent_comm"],
            first_seen=sc["first"],
            last_seen=sc["last"],
            agent_io_mb=sc["agent_io"] / _MB,
            direct_child_io_mb={k: v / _MB for k, v in sc["children"].items()},
        ))

    # Enrich with polylogue session profiles where available
    try:
        from ...sources.polylogue import iter_session_profiles  # noqa: PLC0415
        profiles = list(iter_session_profiles())
    except Exception:
        profiles = []

    if profiles:
        for rec in records:
            # Find the polylogue session whose window best overlaps this agent scope
            best: object = None
            best_overlap = 0.0
            rec_start_ts = rec.first_seen.timestamp()
            rec_end_ts = rec.last_seen.timestamp()
            for p in profiles:
                p_start = getattr(p, "session_start", None) or getattr(p, "started_at", None)
                p_end = getattr(p, "session_end", None) or getattr(p, "ended_at", None)
                if p_start is None:
                    continue
                ps = p_start.timestamp() if hasattr(p_start, "timestamp") else float(p_start)
                pe = (p_end.timestamp() if hasattr(p_end, "timestamp") else float(p_end)) if p_end else ps + 3600
                overlap = max(0.0, min(rec_end_ts, pe) - max(rec_start_ts, ps))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best = p
            if best is not None and best_overlap > 60:
                rec.polylogue_session_id = str(getattr(best, "session_id", None) or getattr(best, "conversation_id", ""))
                rec.polylogue_project = str(getattr(best, "project", None) or "")

    return sorted(records, key=lambda r: r.first_seen)
