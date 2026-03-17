"""Context packet composition from trajectory models.

Builds typed packets at three budget tiers from trajectory day/week/period/episode
models. The top-level ``build_current_state`` composes packets into a single
context payload for LLM consumption.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from ..trajectory import chains as trajectory_chains
from ..trajectory import day as trajectory_day
from ..trajectory import period as trajectory_period
from ..trajectory import signal as trajectory_signal
from ..trajectory.anomaly import detect_anomalies
from ..trajectory.episode import TrajectoryEpisode, detect_episodes
from ..trajectory.month import summarize_months as trajectory_summarize_months
from ..trajectory.quarter import TrajectoryQuarter, summarize_quarters
from ..trajectory.week import TrajectoryWeek, summarize_weeks
from ..trajectory.year import summarize_years
from .packet_types import (
    ContextPacketMeta,
    CoveragePacket,
    DayPacket,
    EpisodePacket,
    MonthPacket,
    ProjectPacket,
    QuarterPacket,
    ThreadPacket,
    WeekPacket,
    YearPacket,
    ThemePacket,
    ClaimPacket,
    ClaimsPacket,
    ProjectArcPacket,
)
from .themes import detect_themes
from .claims import generate_claims
from .project_arcs import build_project_arcs
from .memory import load_memory, build_memory_packet

_SCHEMA_VERSION = "lynchpin-context-state-v2"


def _meta(tier: str) -> ContextPacketMeta:
    return ContextPacketMeta(
        schema=_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        budget_tier=tier,
    )


def _top_n(items: tuple[tuple[str, float], ...], tier: str) -> list[tuple[str, float]]:
    """Trim top-N lists by budget tier."""
    limit = {"compact": 3, "standard": 5, "full": 10}.get(tier, 5)
    return [(k, round(v / 3600.0, 2)) for k, v in items[:limit]]


def build_day_packet(
    day: trajectory_day.TrajectoryDay,
    tier: str = "standard",
) -> DayPacket:
    return DayPacket(
        meta=_meta(tier),
        date=day.date.isoformat(),
        active_hours=round(day.active_seconds / 3600.0, 2),
        recovery_hours=round(day.recovery_seconds / 3600.0, 2),
        dominant_mode=day.dominant_mode,
        dominant_project=day.dominant_project,
        dominant_topic=day.dominant_topic,
        chain_count=day.chain_count,
        signal_count=day.signal_count,
        command_count=day.command_count,
        transcript_count=day.transcript_count,
        commit_count=day.commit_count,
        top_modes=_top_n(day.top_modes, tier),
        top_projects=_top_n(day.top_projects, tier),
        top_topics=_top_n(day.top_topics, tier),
        highlights=list(day.highlights),
    )


def build_week_packet(
    week: TrajectoryWeek,
    tier: str = "standard",
) -> WeekPacket:
    return WeekPacket(
        meta=_meta(tier),
        iso_week=week.iso_week,
        start_date=week.start_date.isoformat(),
        end_date=week.end_date.isoformat(),
        active_hours=round(week.active_seconds / 3600.0, 2),
        recovery_hours=round(week.recovery_seconds / 3600.0, 2),
        dominant_mode=week.dominant_mode,
        dominant_project=week.dominant_project,
        dominant_topic=week.dominant_topic,
        day_pattern=week.day_pattern,
        chain_count=week.chain_count,
        top_modes=_top_n(week.top_modes, tier),
        top_projects=_top_n(week.top_projects, tier),
        top_topics=_top_n(week.top_topics, tier),
        active_delta_vs_prior=round(week.active_delta_vs_prior / 3600.0, 2) if week.active_delta_vs_prior is not None else None,
    )


def build_month_packet(
    month_model,
    tier: str = "standard",
) -> MonthPacket:
    """Build a month packet from a TrajectoryMonth or TrajectoryPeriodSummary."""
    # Support both TrajectoryMonth (preferred) and legacy TrajectoryPeriodSummary
    if hasattr(month_model, "month"):
        # TrajectoryMonth
        return MonthPacket(
            meta=_meta(tier),
            month=month_model.month,
            active_hours=round(month_model.active_seconds / 3600.0, 2),
            recovery_hours=round(month_model.recovery_seconds / 3600.0, 2),
            active_days=month_model.active_days,
            chain_count=month_model.chain_count,
            signal_count=month_model.signal_count,
            dominant_modes=_top_n(month_model.top_modes, tier),
            dominant_projects=_top_n(month_model.top_projects, tier),
            dominant_topics=_top_n(month_model.top_topics, tier),
            highlights=list(month_model.highlights),
            chat_session_count=month_model.chat_session_count,
            chat_work_events=dict(month_model.chat_work_events),
            chat_cost_usd=month_model.chat_cost_usd,
            episode_count=month_model.episode_count,
            episode_labels=list(month_model.episode_labels),
        )
    # Legacy TrajectoryPeriodSummary
    return MonthPacket(
        meta=_meta(tier),
        month=getattr(month_model, "start_date", "unknown"),
        active_hours=round(month_model.active_seconds / 3600.0, 2),
        recovery_hours=round(month_model.recovery_seconds / 3600.0, 2),
        active_days=0,
        chain_count=month_model.chain_count,
        signal_count=month_model.signal_count,
        dominant_modes=_top_n(month_model.dominant_modes, tier),
        dominant_projects=_top_n(month_model.dominant_projects, tier),
        dominant_topics=_top_n(month_model.dominant_topics, tier),
        highlights=list(month_model.highlights),
    )


def build_episode_packet(
    episode: TrajectoryEpisode,
    tier: str = "standard",
) -> EpisodePacket:
    return EpisodePacket(
        meta=_meta(tier),
        episode_id=episode.episode_id,
        label=episode.label,
        start_date=episode.start_date.isoformat(),
        end_date=episode.end_date.isoformat(),
        days=episode.days,
        active_hours=round(episode.active_seconds / 3600.0, 2),
        dominant_mode=episode.dominant_mode,
        dominant_project=episode.dominant_project,
        dominant_topic=episode.dominant_topic,
        trigger=episode.trigger,
        confidence=episode.confidence,
    )


def build_project_packet(
    project: str,
    days: Sequence[trajectory_day.TrajectoryDay],
    chains: Sequence[trajectory_chains.TrajectoryChain],
    tier: str = "standard",
) -> ProjectPacket:
    total_seconds = 0.0
    day_count = 0
    mode_counter: Counter[str] = Counter()
    chain_ids: set[str] = set()

    for day in days:
        for proj, seconds in day.top_projects:
            if proj == project:
                total_seconds += seconds
                day_count += 1
                break
    for chain in chains:
        if chain.project == project:
            chain_ids.add(chain.chain_id)
            mode_counter[chain.mode] += chain.duration_seconds

    top_modes_raw = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
    return ProjectPacket(
        meta=_meta(tier),
        project=project,
        total_hours=round(total_seconds / 3600.0, 2),
        day_count=day_count,
        chain_count=len(chain_ids),
        top_modes=[(m, round(s / 3600.0, 2)) for m, s in top_modes_raw],
    )


def build_thread_packets(
    signals,
    *,
    n: int = 5,
    tier: str = "standard",
) -> list[ThreadPacket]:
    """Build thread packets from polylogue session signals grouped by thread_id."""
    from collections import defaultdict
    from datetime import datetime, timezone

    threads: dict[str, dict] = defaultdict(lambda: {
        "session_ids": set(),
        "start": None,
        "end": None,
        "projects": Counter(),
        "work_events": Counter(),
        "cost": 0.0,
        "depth": 0,
    })

    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        ev = signal.evidence if isinstance(signal.evidence, dict) else {}
        tid = ev.get("thread_id")
        if not tid:
            # Sessions without thread_id are their own single-session thread
            tid = ev.get("conversation_id", signal.signal_id)
        tid = str(tid)
        t = threads[tid]
        conv_id = ev.get("conversation_id", signal.signal_id)
        t["session_ids"].add(str(conv_id))
        t["start"] = min(t["start"], signal.start) if t["start"] else signal.start
        t["end"] = max(t["end"], signal.end) if t["end"] else signal.end
        proj = ev.get("project_hint") or ev.get("canonical_project")
        if proj:
            t["projects"][str(proj)] += 1
        we_kind = ev.get("work_event_kind")
        if we_kind:
            t["work_events"][str(we_kind)] += 1
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            t["cost"] += cost
        depth = ev.get("continuation_depth", 0)
        if isinstance(depth, int):
            t["depth"] = max(t["depth"], depth)

    now_str = datetime.now(timezone.utc).isoformat()
    packets = []
    sorted_threads = sorted(threads.items(), key=lambda item: (item[1]["end"] or datetime.min), reverse=True)
    for tid, t in sorted_threads[:n]:
        dominant_proj = t["projects"].most_common(1)[0][0] if t["projects"] else None
        start = t["start"] or datetime.now(timezone.utc)
        end = t["end"] or datetime.now(timezone.utc)
        packets.append(ThreadPacket(
            meta=ContextPacketMeta(schema=_SCHEMA_VERSION, generated_at=now_str, budget_tier=tier),
            thread_id=tid,
            depth=t["depth"],
            session_count=len(t["session_ids"]),
            start_date=start.date().isoformat(),
            end_date=end.date().isoformat(),
            dominant_project=dominant_proj,
            work_event_breakdown=dict(t["work_events"]),
            total_cost_usd=t["cost"],
        ))
    return packets


def build_coverage_packet(
    days: Sequence[trajectory_day.TrajectoryDay],
    tier: str = "standard",
    anomaly_count: int = 0,
) -> CoveragePacket:
    source_counter: Counter[str] = Counter()
    total_signals = 0
    total_chains = 0
    aw_days = 0
    term_days = 0
    chat_days = 0
    git_days = 0

    for day in days:
        total_signals += day.signal_count
        total_chains += day.chain_count
        source_counter.update(day.source_counts)
        if day.coverage.get("has_activitywatch"):
            aw_days += 1
        if day.coverage.get("has_terminal"):
            term_days += 1
        if day.coverage.get("has_chatlog"):
            chat_days += 1
        if day.coverage.get("has_git"):
            git_days += 1

    return CoveragePacket(
        meta=_meta(tier),
        day_count=len(days),
        signal_count=total_signals,
        chain_count=total_chains,
        source_breakdown=dict(source_counter),
        days_with_activitywatch=aw_days,
        days_with_terminal=term_days,
        days_with_chatlog=chat_days,
        days_with_git=git_days,
        anomaly_count=anomaly_count,
    )


def build_quarter_packet(
    quarter: TrajectoryQuarter,
    tier: str = "standard",
) -> QuarterPacket:
    return QuarterPacket(
        meta=_meta(tier),
        quarter=quarter.quarter,
        active_hours=round(quarter.active_seconds / 3600.0, 2),
        recovery_hours=round(quarter.recovery_seconds / 3600.0, 2),
        active_days=quarter.active_days,
        chain_count=quarter.chain_count,
        signal_count=quarter.signal_count,
        dominant_mode=quarter.dominant_mode,
        dominant_project=quarter.dominant_project,
        dominant_topic=quarter.dominant_topic,
        top_modes=_top_n(quarter.top_modes, tier),
        top_projects=_top_n(quarter.top_projects, tier),
        top_topics=_top_n(quarter.top_topics, tier),
        chat_session_count=quarter.chat_session_count,
        chat_cost_usd=quarter.chat_cost_usd,
        episode_count=quarter.episode_count,
        month_count=quarter.month_count,
        month_active_trend=[round(s / 3600.0, 2) for s in quarter.month_active_trend],
        active_delta_vs_prior=round(quarter.active_delta_vs_prior / 3600.0, 2) if quarter.active_delta_vs_prior is not None else None,
    )


def build_year_packet(
    year_model,
    tier: str = "standard",
) -> YearPacket:
    return YearPacket(
        meta=_meta(tier),
        year=year_model.year,
        active_hours=round(year_model.active_seconds / 3600.0, 2),
        recovery_hours=round(year_model.recovery_seconds / 3600.0, 2),
        active_days=year_model.active_days,
        chain_count=year_model.chain_count,
        signal_count=year_model.signal_count,
        dominant_mode=year_model.dominant_mode,
        dominant_project=year_model.dominant_project,
        dominant_topic=year_model.dominant_topic,
        top_modes=_top_n(year_model.top_modes, tier),
        top_projects=_top_n(year_model.top_projects, tier),
        top_topics=_top_n(year_model.top_topics, tier),
        chat_session_count=year_model.chat_session_count,
        chat_cost_usd=year_model.chat_cost_usd,
        episode_count=year_model.episode_count,
        quarter_count=year_model.quarter_count,
        quarter_active_trend=[round(s / 3600.0, 2) for s in year_model.quarter_active_trend],
        active_delta_vs_prior=round(year_model.active_delta_vs_prior / 3600.0, 2) if year_model.active_delta_vs_prior is not None else None,
    )


def build_theme_packets(months, weeks, tier: str = "standard") -> list[dict]:
    """Build theme packets from recurring projects and topics across months."""
    themes = detect_themes(months, weeks)
    return [
        ThemePacket(
            meta=_meta(tier),
            name=t.name,
            kind=t.kind,
            total_hours=t.total_hours,
            month_count=t.month_count,
            trend=t.trend,
            first_seen=t.first_seen,
            last_seen=t.last_seen,
        ).to_dict()
        for t in themes
    ]


def build_claims_packet(months, weeks, days, tier: str = "standard") -> dict:
    """Build claims packet from quantified activity patterns."""
    claims = generate_claims(months, weeks, days)
    return ClaimsPacket(
        meta=_meta(tier),
        claims=tuple(
            ClaimPacket(
                statement=c.statement,
                confidence=c.confidence,
                evidence_refs=c.evidence_refs,
                category=c.category,
            )
            for c in claims
        ),
    ).to_dict()


def build_project_arc_packets(months, weeks, episodes, tier: str = "standard") -> list[dict]:
    """Build project arc packets tracking velocity and momentum per top-5 project."""
    arcs = build_project_arcs(months, weeks, episodes)
    return [
        ProjectArcPacket(
            meta=_meta(tier),
            project=a.project,
            total_hours=a.total_hours,
            active_months=a.active_months,
            velocity_trend=a.velocity_trend,
            cost_usd=a.cost_usd,
            active_episodes=a.active_episodes,
            momentum=a.momentum,
        ).to_dict()
        for a in arcs
    ]


def build_current_state(
    *,
    days: int = 14,
    end: Optional[datetime] = None,
    tier: str = "standard",
) -> dict[str, object]:
    """Build a complete current-state context packet.

    Composes day, week, episode, and coverage packets into a single
    payload suitable for LLM context injection.
    """
    window_start, window_end = trajectory_signal.resolve_window(end=end, days=days)
    signals = trajectory_signal.load_signals(start=window_start, end=window_end, days=days)
    chains = trajectory_chains.build_chains(signals)
    day_summaries = trajectory_day.summarize_days(
        signals=signals,
        chains=chains,
        start=window_start,
        end=window_end,
        days=days,
    )
    period = trajectory_period.summarize_period(day_summaries)
    weeks = summarize_weeks(day_summaries)
    episodes = detect_episodes(day_summaries)

    # Recent chains
    recent_chain_cutoff = window_end - timedelta(days=min(days, 3))
    recent_chains = [
        _chain_packet(chain)
        for chain in sorted(chains, key=lambda c: (c.start, c.chain_id), reverse=True)
        if chain.end >= recent_chain_cutoff
    ][:15]

    # Day packets
    day_limit = {"compact": 3, "standard": 7, "full": days}.get(tier, 7)
    day_packets = [build_day_packet(d, tier).to_dict() for d in day_summaries[-day_limit:]]

    # Week packets
    week_packets = [build_week_packet(w, tier).to_dict() for w in weeks[-4:]]

    # Episode packets
    episode_packets = [build_episode_packet(ep, tier).to_dict() for ep in episodes[-5:]]

    # Month packets
    trajectory_months = trajectory_summarize_months(day_summaries, signals=signals)
    month_packets = [build_month_packet(m, tier).to_dict() for m in trajectory_months[-3:]]

    # Quarter packets
    trajectory_quarters = summarize_quarters(trajectory_months)
    quarter_packets = [build_quarter_packet(q, tier).to_dict() for q in trajectory_quarters[-4:]]

    # Year packets
    trajectory_years = summarize_years(trajectory_quarters)
    year_packets = [build_year_packet(y, tier).to_dict() for y in trajectory_years[-2:]]

    # Coverage (includes anomaly count)
    anomalies = detect_anomalies(day_summaries)
    coverage_packet = build_coverage_packet(day_summaries, tier, anomaly_count=len(anomalies)).to_dict()

    # Chat work events from polylogue signals
    chat_work_events = _aggregate_chat_work_events(signals)

    # Work threads from polylogue session signals
    thread_packets = [t.to_dict() for t in build_thread_packets(signals, n=5, tier=tier)]

    # Theme packets from trajectory months; fall back to persistent memory store
    # when the window is too short to detect themes (< 2 months of data)
    theme_packets = build_theme_packets(trajectory_months, weeks, tier=tier)
    memory_store = load_memory()
    if not theme_packets and memory_store.themes:
        theme_packets = [
            {
                "name": t.name,
                "kind": t.kind,
                "total_hours": t.total_hours,
                "trend": t.trend,
                "first_seen": t.first_seen,
                "last_seen": t.last_seen,
                "months_active": t.months_active,
                "source": "memory",
            }
            for t in sorted(memory_store.themes, key=lambda t: -t.total_hours)
        ]

    # Claims packet from all trajectory data
    claims_packet = build_claims_packet(trajectory_months, weeks, day_summaries, tier=tier)

    # Project arc packets from trajectory months and episodes
    project_arc_packets = build_project_arc_packets(trajectory_months, weeks, episodes, tier=tier)

    # Memory: persistent claims from prior runs
    memory_packet = build_memory_packet(memory_store)

    return {
        "schema": _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "budget_tier": tier,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "days": days,
        },
        "coverage": coverage_packet,
        "period": period.to_dict(),
        "current": day_summaries[-1].to_dict() if day_summaries else None,
        "days": day_packets,
        "weeks": week_packets,
        "months": month_packets,
        "quarters": quarter_packets,
        "years": year_packets,
        "episodes": episode_packets,
        "recent_chains": recent_chains,
        "chat_work_events": chat_work_events,
        "threads": thread_packets,
        "themes": theme_packets,
        "claims": claims_packet,
        "project_arcs": project_arc_packets,
        "memory": memory_packet,
    }


def _aggregate_chat_work_events(signals) -> dict[str, object]:
    """Extract work event metadata from polylogue session signals."""
    work_event_counter: Counter[str] = Counter()
    session_ids: set[str] = set()
    total_cost = 0.0
    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        ev = signal.evidence
        conv_id = ev.get("conversation_id")
        if conv_id:
            session_ids.add(str(conv_id))
        kind = ev.get("work_event_kind")
        if kind:
            work_event_counter[str(kind)] += 1
        cost = ev.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += cost
    return {
        "session_count": len(session_ids),
        "work_event_breakdown": dict(work_event_counter),
        "total_cost_usd": round(total_cost, 4),
    }


def _chain_packet(chain) -> dict[str, object]:
    return {
        "chain_id": chain.chain_id,
        "start": chain.start.isoformat(),
        "end": chain.end.isoformat(),
        "duration_minutes": round(chain.duration_seconds / 60.0, 2),
        "mode": chain.mode,
        "project": chain.project,
        "topic": chain.topic,
        "sources": list(chain.sources),
        "apps": list(chain.apps),
        "domains": list(chain.domains),
        "titles": list(chain.titles[:3]),
        "reasons": list(chain.reasons),
        "quality_flags": list(chain.quality_flags),
    }
