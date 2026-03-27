"""Context packet composition from summaries and signals."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from .claims import generate_claims
from .packet_types import (
    ClaimPacket,
    ClaimsPacket,
    ContextPacketMeta,
    CoveragePacket,
    DayPacket,
    EpisodePacket,
    MonthPacket,
    ProjectArcPacket,
    ProjectPacket,
    QuarterPacket,
    ThemePacket,
    ThreadPacket,
    WeekPacket,
    YearPacket,
)
from .project_arcs import build_project_arcs
from .summary_models import ChainSummary, DaySummary, EpisodeSummary, MonthSummary, QuarterSummary, WeekSummary, YearSummary
from .themes import detect_themes

_SCHEMA_VERSION = "lynchpin-context-state-v3"


def _meta(tier: str) -> ContextPacketMeta:
    return ContextPacketMeta(
        schema=_SCHEMA_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        budget_tier=tier,
    )


def _top_n(items: tuple[tuple[str, float], ...], tier: str) -> list[tuple[str, float]]:
    limit = {"compact": 3, "standard": 5, "full": 10}.get(tier, 5)
    return [(key, round(value / 3600.0, 2)) for key, value in items[:limit]]


def build_day_packet(day: DaySummary, tier: str = "standard") -> DayPacket:
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


def build_week_packet(week: WeekSummary, tier: str = "standard") -> WeekPacket:
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
        active_delta_vs_prior=round(week.active_delta_vs_prior / 3600.0, 2)
        if week.active_delta_vs_prior is not None
        else None,
    )


def build_month_packet(month_model: MonthSummary, tier: str = "standard") -> MonthPacket:
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


def build_episode_packet(episode: EpisodeSummary, tier: str = "standard") -> EpisodePacket:
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
    days: Sequence[DaySummary],
    chains: Sequence[ChainSummary],
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
        top_modes=[(mode, round(seconds / 3600.0, 2)) for mode, seconds in top_modes_raw],
    )


def build_thread_packets(signals, *, n: int = 5, tier: str = "standard") -> list[ThreadPacket]:
    from collections import defaultdict

    threads: dict[str, dict] = defaultdict(
        lambda: {
            "session_ids": set(),
            "start": None,
            "end": None,
            "projects": Counter(),
            "work_events": Counter(),
            "cost": 0.0,
            "depth": 0,
        }
    )

    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        evidence = signal.evidence if isinstance(signal.evidence, dict) else {}
        thread_id = evidence.get("thread_id")
        if not thread_id:
            thread_id = evidence.get("conversation_id", signal.signal_id)
        thread_id = str(thread_id)
        thread = threads[thread_id]
        conversation_id = evidence.get("conversation_id", signal.signal_id)
        thread["session_ids"].add(str(conversation_id))
        thread["start"] = min(thread["start"], signal.start) if thread["start"] else signal.start
        thread["end"] = max(thread["end"], signal.end) if thread["end"] else signal.end
        project = evidence.get("project_hint") or evidence.get("canonical_project")
        if project:
            thread["projects"][str(project)] += 1
        work_kind = evidence.get("work_event_kind")
        if work_kind:
            thread["work_events"][str(work_kind)] += 1
        cost = evidence.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            thread["cost"] += cost
        depth = evidence.get("continuation_depth", 0)
        if isinstance(depth, int):
            thread["depth"] = max(thread["depth"], depth)

    packets = []
    sorted_threads = sorted(threads.items(), key=lambda item: (item[1]["end"] or datetime.min), reverse=True)
    now = datetime.now(timezone.utc)
    now_str = now.isoformat()
    for thread_id, thread in sorted_threads[:n]:
        dominant_project = thread["projects"].most_common(1)[0][0] if thread["projects"] else None
        start = thread["start"] or now
        end = thread["end"] or now
        packets.append(
            ThreadPacket(
                meta=ContextPacketMeta(schema=_SCHEMA_VERSION, generated_at=now_str, budget_tier=tier),
                thread_id=thread_id,
                depth=thread["depth"],
                session_count=len(thread["session_ids"]),
                start_date=start.date().isoformat(),
                end_date=end.date().isoformat(),
                dominant_project=dominant_project,
                work_event_breakdown=dict(thread["work_events"]),
                total_cost_usd=thread["cost"],
            )
        )
    return packets


def build_coverage_packet(days: Sequence[DaySummary], tier: str = "standard", anomaly_count: int = 0) -> CoveragePacket:
    source_counter: Counter[str] = Counter()
    total_signals = 0
    total_chains = 0
    activitywatch_days = 0
    terminal_days = 0
    chatlog_days = 0
    git_days = 0

    for day in days:
        total_signals += day.signal_count
        total_chains += day.chain_count
        source_counter.update(day.source_counts)
        if day.coverage.get("has_activitywatch"):
            activitywatch_days += 1
        if day.coverage.get("has_terminal"):
            terminal_days += 1
        if day.coverage.get("has_chatlog"):
            chatlog_days += 1
        if day.coverage.get("has_git"):
            git_days += 1

    return CoveragePacket(
        meta=_meta(tier),
        day_count=len(days),
        signal_count=total_signals,
        chain_count=total_chains,
        source_breakdown=dict(source_counter),
        days_with_activitywatch=activitywatch_days,
        days_with_terminal=terminal_days,
        days_with_chatlog=chatlog_days,
        days_with_git=git_days,
        anomaly_count=anomaly_count,
    )


def build_quarter_packet(quarter: QuarterSummary, tier: str = "standard") -> QuarterPacket:
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
        month_active_trend=[round(seconds / 3600.0, 2) for seconds in quarter.month_active_trend],
        active_delta_vs_prior=round(quarter.active_delta_vs_prior / 3600.0, 2)
        if quarter.active_delta_vs_prior is not None
        else None,
    )


def build_year_packet(year_model: YearSummary, tier: str = "standard") -> YearPacket:
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
        quarter_active_trend=[round(seconds / 3600.0, 2) for seconds in year_model.quarter_active_trend],
        active_delta_vs_prior=round(year_model.active_delta_vs_prior / 3600.0, 2)
        if year_model.active_delta_vs_prior is not None
        else None,
    )


def build_theme_packets(months, weeks, tier: str = "standard") -> list[dict]:
    themes = detect_themes(months, weeks)
    return [
        ThemePacket(
            meta=_meta(tier),
            name=theme.name,
            kind=theme.kind,
            total_hours=theme.total_hours,
            month_count=theme.month_count,
            trend=theme.trend,
            first_seen=theme.first_seen,
            last_seen=theme.last_seen,
        ).to_dict()
        for theme in themes
    ]


def build_claims_packet(months, weeks, days, tier: str = "standard") -> dict:
    claims = generate_claims(months, weeks, days)
    return ClaimsPacket(
        meta=_meta(tier),
        claims=tuple(
            ClaimPacket(
                statement=claim.statement,
                confidence=claim.confidence,
                evidence_refs=claim.evidence_refs,
                category=claim.category,
            )
            for claim in claims
        ),
    ).to_dict()


def build_project_arc_packets(months, weeks, episodes, tier: str = "standard") -> list[dict]:
    arcs = build_project_arcs(months, weeks, episodes)
    return [
        ProjectArcPacket(
            meta=_meta(tier),
            project=arc.project,
            total_hours=arc.total_hours,
            active_months=arc.active_months,
            velocity_trend=arc.velocity_trend,
            cost_usd=arc.cost_usd,
            active_episodes=arc.active_episodes,
            momentum=arc.momentum,
        ).to_dict()
        for arc in arcs
    ]


def _aggregate_chat_work_events(signals) -> dict[str, object]:
    work_event_counter: Counter[str] = Counter()
    session_ids: set[str] = set()
    total_cost = 0.0
    for signal in signals:
        if signal.source != "polylogue.session":
            continue
        evidence = signal.evidence
        conversation_id = evidence.get("conversation_id")
        if conversation_id:
            session_ids.add(str(conversation_id))
        kind = evidence.get("work_event_kind")
        if kind:
            work_event_counter[str(kind)] += 1
        cost = evidence.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            total_cost += cost
    return {
        "session_count": len(session_ids),
        "work_event_breakdown": dict(work_event_counter),
        "total_cost_usd": round(total_cost, 4),
    }
