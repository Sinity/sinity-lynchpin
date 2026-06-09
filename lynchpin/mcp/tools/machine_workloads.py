"""Machine workload analysis MCP tools.

Exposes semantic workload classification, orphan detection, agent-session
IO attribution, and co-presence analysis over the raw process telemetry.

NOTE: do NOT add ``from __future__ import annotations`` here.
FastMCP inspects annotations at decoration time and cannot handle postponed
string annotations for tool parameters.
"""

from datetime import date
from typing import Any

from lynchpin.mcp.server import app


@app.tool()
def machine_workload_summary(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Daily IO breakdown by semantic workload kind.

    Returns per-day IO (MB) for: agent_session, agent_build, rust_build,
    nix_build, sinex_stack, polylogue_daemon, test_suite, backup, browser,
    other. Also includes system IO pressure (PSI full avg300) and the count
    of simultaneous heavy workloads per day.

    Args:
        start: ISO date string (YYYY-MM-DD). Defaults to 7 days ago.
        end: ISO date string (YYYY-MM-DD). Defaults to today.
    """
    from lynchpin.analysis.machine.workloads import daily_workload_summary

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    rows = daily_workload_summary(start=start_d, end=end_d)
    return {
        "days": [r.to_dict() for r in rows],
        "day_count": len(rows),
    }


@app.tool()
def machine_orphan_processes(
    min_duration_hours: float = 4.0,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Find long-running processes that look like abandoned workloads.

    Identifies process instances (by pid + start_ticks) that accumulate IO
    samples spanning more than min_duration_hours. Transient processes (nix
    check, pytest, cargo, claude) running for this long are likely orphaned
    and consuming IO without any active user session.

    Args:
        min_duration_hours: Minimum continuous runtime to flag as orphan (default 4h).
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.workloads import detect_orphans

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    orphans = detect_orphans(start=start_d, end=end_d, min_duration_hours=min_duration_hours)
    return {
        "orphans": [o.to_dict() for o in orphans],
        "count": len(orphans),
        "total_io_mb": round(sum(o.total_io_mb for o in orphans), 1),
        "min_duration_hours": min_duration_hours,
    }


@app.tool()
def machine_agent_sessions(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Per-agent-session IO attribution with direct children.

    Groups process IO by the sinnix-agent scope unit in the cgroup path.
    Processes sharing the same scope were spawned by the same agent invocation.
    Direct children (rust-analyzer, cargo, python, etc. inside the same scope)
    are reported separately from the agent process itself.

    Useful for answering: which agent session caused 200 GB of IO? What did
    it spawn? Does the session correspond to a known polylogue session?

    Args:
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.workloads import agent_session_attribution

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    sessions = agent_session_attribution(start=start_d, end=end_d)
    return {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
        "total_agent_io_mb": round(sum(s.agent_io_mb for s in sessions), 1),
        "total_io_mb": round(sum(s.total_io_mb for s in sessions), 1),
    }


@app.tool()
def machine_co_presence(
    min_kinds: int = 2,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Find time windows where multiple heavy workloads overlapped.

    A co-presence event is a period where at least min_kinds distinct heavy
    workload kinds (agent_session, agent_build, rust_build, nix_build,
    test_suite, backup) had simultaneous IO activity. These windows are when
    the system cannot prioritize reasonably — every kind is competing for NVMe
    bandwidth and the page cache.

    Args:
        min_kinds: Minimum number of simultaneous heavy workloads (default 2).
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.workloads import co_presence_windows

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    windows = co_presence_windows(start=start_d, end=end_d, min_kinds=min_kinds)
    total_minutes = sum(w.duration_minutes for w in windows)
    return {
        "windows": [w.to_dict() for w in windows],
        "count": len(windows),
        "total_overlap_minutes": round(total_minutes, 1),
        "min_kinds": min_kinds,
    }


@app.tool()
def machine_session_profiles(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Causal footprint of each agent session: what it triggered, total machine cost.

    For each sinnix-agent-* scope, reports:
    - agent_io_mb: IO by the agent process itself
    - triggered_build_count / triggered_nix_builds / triggered_rust_builds
    - triggered_build_io_mb: IO from build scopes caused by this agent
    - total_machine_io_mb: agent + all triggered builds
    - builds_by_comm: breakdown of triggered builds by primary process comm
    - concurrent_agent_count: how many agents overlapped at peak
    - polylogue_session_id / polylogue_project when matched
    - is_high_impact: >10 GB total or >20 triggered builds

    Also returns per-day summaries with contention windows — periods where
    multiple heavy workloads overlapped and likely caused thrashing.

    Use this to answer: which agent session caused the June 8 IO spike? How
    many nix rebuilds did yesterday's work session trigger?

    Args:
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.session_profile import machine_session_days

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    days = machine_session_days(start=start_d, end=end_d)
    all_sessions = [s for d in days for s in d.sessions]
    return {
        "days": [d.to_dict() for d in days],
        "day_count": len(days),
        "total_sessions": len(all_sessions),
        "high_impact_sessions": sum(1 for s in all_sessions if s.is_high_impact),
        "total_machine_io_gb": round(sum(s.total_machine_io_mb for s in all_sessions) / 1024, 1),
    }


@app.tool()
def machine_scope_timeline(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Per-day breakdown of all sinnix scope runs with temporal attribution.

    Each scope unit (sinnix-agent-*, sinnix-build-*, sinnix-nix-build-*,
    sinnix-background-*) is one invocation of sinnix-scope. Build and nix-build
    scopes that started while exactly one agent scope was active are attributed
    to that agent.

    Per-day summary includes: agent_session_count, build_count, nix_build_count,
    orphan_count (builds >4h), peak_simultaneous_agents, peak_simultaneous_builds,
    total_io_gb, carryover_scope_count.

    Args:
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.timeline import machine_timeline

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    days = machine_timeline(start=start_d, end=end_d)
    return {
        "days": [d.to_dict() for d in days],
        "day_count": len(days),
    }


@app.tool()
def machine_day_report(
    target_date: str,
) -> dict[str, Any]:
    """Detailed scope-unit report for a single calendar day.

    Returns all agent sessions, build runs, and nix-build runs that were
    active at any point during the day. Build/nix-build scopes are annotated
    with their attributed agent (if unambiguous). Carryover scopes (started
    previous day) are listed separately.

    Useful for post-mortem: 'what ran on June 8 and why was IO so high?'

    Args:
        target_date: ISO date string (YYYY-MM-DD) for the day to inspect.
    """
    from lynchpin.analysis.machine.timeline import day_report

    d = date.fromisoformat(target_date)
    report = day_report(d)
    return report.to_dict()


@app.tool()
def machine_hourly_heatmap(
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """Hourly active-scope counts and IO pressure over a date range.

    Each entry covers one UTC hour and reports the number of concurrent agent,
    build, and nix-build scopes plus the average IO PSI full avg300 (fraction
    of time all processes stalled waiting for IO). Hours with io_pressure > 0.5
    indicate severe thrashing.

    Args:
        start: ISO date string for window start.
        end: ISO date string for window end.
    """
    from lynchpin.analysis.machine.timeline import hourly_heatmap

    start_d = date.fromisoformat(start) if start else None
    end_d = date.fromisoformat(end) if end else None
    slices = hourly_heatmap(start=start_d, end=end_d)
    return {
        "hours": [s.to_dict() for s in slices],
        "hour_count": len(slices),
    }
