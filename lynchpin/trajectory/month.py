"""Month-level trajectory rollup.

Groups TrajectoryDay summaries into calendar months with chat session
metadata, episode detection, and coverage quality summaries.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .coverage import SignalCoverage, compute_coverage
from .day import TrajectoryDay
from .episode import TrajectoryEpisode, detect_episodes
from .signal import TrajectorySignal
from .week import summarize_weeks


@dataclass(frozen=True)
class TrajectoryMonth:
    month: str  # "2026-03"
    start_date: date
    end_date: date
    total_days: int
    active_days: int
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    source_counts: dict[str, int]
    coverage_summary: dict[str, int]  # quality tier -> day count
    highlights: tuple[str, ...]
    chat_session_count: int
    chat_work_events: dict[str, int]
    chat_cost_usd: float
    episode_count: int
    episode_labels: tuple[str, ...]
    week_count: int
    day_patterns: tuple[str, ...]

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "month": self.month,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "total_days": self.total_days,
            "active_days": self.active_days,
            "active_seconds": round(self.active_seconds, 3),
            "recovery_seconds": round(self.recovery_seconds, 3),
            "observed_seconds": round(self.observed_seconds, 3),
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "command_count": self.command_count,
            "transcript_count": self.transcript_count,
            "commit_count": self.commit_count,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "top_modes": [[m, round(s, 3)] for m, s in self.top_modes],
            "top_projects": [[p, round(s, 3)] for p, s in self.top_projects],
            "top_topics": [[t, round(s, 3)] for t, s in self.top_topics],
            "source_counts": self.source_counts,
            "coverage_summary": self.coverage_summary,
            "highlights": list(self.highlights),
            "chat_session_count": self.chat_session_count,
            "chat_work_events": self.chat_work_events,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "episode_labels": list(self.episode_labels),
            "week_count": self.week_count,
            "day_patterns": list(self.day_patterns),
        }


def summarize_months(
    days: Sequence[TrajectoryDay],
    *,
    signals: Optional[Sequence[TrajectorySignal]] = None,
) -> list[TrajectoryMonth]:
    """Group days into calendar months and produce monthly summaries.

    If signals are provided, extracts chat session metadata from
    polylogue.session signals. Otherwise chat fields default to zero.
    """
    if not days:
        return []

    # Group by month
    grouped: dict[str, list[TrajectoryDay]] = defaultdict(list)
    for day in days:
        grouped[day.date.strftime("%Y-%m")].append(day)

    # Pre-compute chat metadata from signals if available
    chat_by_month: dict[str, tuple[int, dict[str, int], float]] = {}
    if signals:
        for month_key in grouped:
            session_ids: set[str] = set()
            work_events: Counter[str] = Counter()
            total_cost = 0.0
            for sig in signals:
                if sig.source != "polylogue.session":
                    continue
                sig_month = sig.start.strftime("%Y-%m")
                if sig_month != month_key:
                    continue
                ev = sig.evidence
                conv_id = ev.get("conversation_id")
                if conv_id:
                    session_ids.add(str(conv_id))
                kind = ev.get("work_event_kind")
                if kind:
                    work_events[str(kind)] += 1
                cost = ev.get("total_cost_usd")
                if isinstance(cost, (int, float)):
                    total_cost += cost
            chat_by_month[month_key] = (len(session_ids), dict(work_events), total_cost)

    months: list[TrajectoryMonth] = []
    for month_key in sorted(grouped):
        month_days = sorted(grouped[month_key], key=lambda d: d.date)

        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        source_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0
        active_days = 0

        for day in month_days:
            active_seconds += day.active_seconds
            recovery_seconds += day.recovery_seconds
            chain_count += day.chain_count
            signal_count += day.signal_count
            command_count += day.command_count
            transcript_count += day.transcript_count
            commit_count += day.commit_count
            if day.active_seconds > 0:
                active_days += 1
            for mode, seconds in day.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in day.top_projects:
                project_counter[project] += seconds
            for topic, seconds in day.top_topics:
                topic_counter[topic] += seconds
            source_counter.update(day.source_counts)

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])

        # Coverage summary
        coverage_tiers: Counter[str] = Counter()
        for day in month_days:
            cov = compute_coverage(day)
            coverage_tiers[cov.quality] += 1

        # Episodes for this month
        month_episodes = detect_episodes(month_days)

        # Weeks for this month
        month_weeks = summarize_weeks(month_days)

        # Chat metadata
        chat_session_count, chat_work_events, chat_cost = chat_by_month.get(month_key, (0, {}, 0.0))

        # Highlights
        highlights: list[str] = []
        if top_modes:
            highlights.append(f"mode:{top_modes[0][0]} {top_modes[0][1] / 3600.0:.1f}h")
        if top_projects:
            highlights.append(f"project:{top_projects[0][0]} {top_projects[0][1] / 3600.0:.1f}h")
        if command_count:
            highlights.append(f"commands:{command_count}")
        if commit_count:
            highlights.append(f"commits:{commit_count}")
        if chat_session_count:
            highlights.append(f"chat_sessions:{chat_session_count}")

        months.append(TrajectoryMonth(
            month=month_key,
            start_date=month_days[0].date,
            end_date=month_days[-1].date,
            total_days=len(month_days),
            active_days=active_days,
            active_seconds=round(active_seconds, 3),
            recovery_seconds=round(recovery_seconds, 3),
            chain_count=chain_count,
            signal_count=signal_count,
            command_count=command_count,
            transcript_count=transcript_count,
            commit_count=commit_count,
            dominant_mode=top_modes[0][0] if top_modes else None,
            dominant_project=top_projects[0][0] if top_projects else None,
            dominant_topic=top_topics[0][0] if top_topics else None,
            top_modes=top_modes,
            top_projects=top_projects,
            top_topics=top_topics,
            source_counts=dict(source_counter),
            coverage_summary=dict(coverage_tiers),
            highlights=tuple(highlights[:5]),
            chat_session_count=chat_session_count,
            chat_work_events=chat_work_events,
            chat_cost_usd=chat_cost,
            episode_count=len(month_episodes),
            episode_labels=tuple(ep.label for ep in month_episodes),
            week_count=len(month_weeks),
            day_patterns=tuple(w.day_pattern for w in month_weeks),
        ))

    return months
