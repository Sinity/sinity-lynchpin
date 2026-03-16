"""Year-level trajectory rollup.

Groups TrajectoryQuarter summaries into calendar years, computing aggregate
activity, distributions, and quarter-over-quarter active trends for sparklines.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .quarter import TrajectoryQuarter


@dataclass(frozen=True)
class TrajectoryYear:
    year: str  # "2026"
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
    coverage_summary: dict[str, int]
    chat_session_count: int
    chat_cost_usd: float
    episode_count: int
    quarter_count: int
    quarter_active_trend: tuple[float, ...]  # active_seconds per quarter for sparkline
    active_delta_vs_prior: Optional[float]

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
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
            "coverage_summary": self.coverage_summary,
            "chat_session_count": self.chat_session_count,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "quarter_count": self.quarter_count,
            "quarter_active_trend": [round(s, 3) for s in self.quarter_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


def summarize_years(
    quarters: Sequence[TrajectoryQuarter],
) -> list[TrajectoryYear]:
    """Group quarters into calendar years and produce yearly summaries."""
    if not quarters:
        return []

    grouped: dict[str, list[TrajectoryQuarter]] = {}
    for quarter in quarters:
        year_key = quarter.quarter.split("-")[0]
        grouped.setdefault(year_key, []).append(quarter)

    years: list[TrajectoryYear] = []
    prior_active: Optional[float] = None

    for year_key in sorted(grouped):
        y_quarters = sorted(grouped[year_key], key=lambda q: q.quarter)

        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        coverage_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0
        total_days = 0
        active_days = 0
        chat_session_count = 0
        chat_cost_usd = 0.0
        episode_count = 0
        quarter_active_trend: list[float] = []

        for quarter in y_quarters:
            active_seconds += quarter.active_seconds
            recovery_seconds += quarter.recovery_seconds
            chain_count += quarter.chain_count
            signal_count += quarter.signal_count
            command_count += quarter.command_count
            transcript_count += quarter.transcript_count
            commit_count += quarter.commit_count
            total_days += quarter.total_days
            active_days += quarter.active_days
            chat_session_count += quarter.chat_session_count
            chat_cost_usd += quarter.chat_cost_usd
            episode_count += quarter.episode_count
            quarter_active_trend.append(quarter.active_seconds)
            for mode, seconds in quarter.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in quarter.top_projects:
                project_counter[project] += seconds
            for topic, seconds in quarter.top_topics:
                topic_counter[topic] += seconds
            for tier, count in quarter.coverage_summary.items():
                coverage_counter[tier] += count

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])

        delta = (active_seconds - prior_active) if prior_active is not None else None

        years.append(TrajectoryYear(
            year=year_key,
            start_date=y_quarters[0].start_date,
            end_date=y_quarters[-1].end_date,
            total_days=total_days,
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
            coverage_summary=dict(coverage_counter),
            chat_session_count=chat_session_count,
            chat_cost_usd=chat_cost_usd,
            episode_count=episode_count,
            quarter_count=len(y_quarters),
            quarter_active_trend=tuple(quarter_active_trend),
            active_delta_vs_prior=delta,
        ))

        prior_active = active_seconds

    return years
