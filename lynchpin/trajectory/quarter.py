"""Quarter-level trajectory rollup.

Groups TrajectoryMonth summaries into calendar quarters (Q1–Q4),
computing aggregate activity, mode/project/topic distributions,
coverage quality, and month-over-month active trends.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .month import TrajectoryMonth


@dataclass(frozen=True)
class TrajectoryQuarter:
    quarter: str  # "2026-Q1"
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
    month_count: int
    month_active_trend: tuple[float, ...]  # active_seconds per month for sparkline
    active_delta_vs_prior: Optional[float]

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "quarter": self.quarter,
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
            "month_count": self.month_count,
            "month_active_trend": [round(s, 3) for s in self.month_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


def _quarter_key(month_key: str) -> str:
    """Convert 'YYYY-MM' to 'YYYY-Q1' through 'YYYY-Q4'."""
    year, month = month_key.split("-")
    q = (int(month) - 1) // 3 + 1
    return f"{year}-Q{q}"


def summarize_quarters(
    months: Sequence[TrajectoryMonth],
) -> list[TrajectoryQuarter]:
    """Group months into calendar quarters and produce quarterly summaries."""
    if not months:
        return []

    grouped: dict[str, list[TrajectoryMonth]] = {}
    for month in months:
        key = _quarter_key(month.month)
        grouped.setdefault(key, []).append(month)

    quarters: list[TrajectoryQuarter] = []
    prior_active: Optional[float] = None

    for qkey in sorted(grouped):
        q_months = sorted(grouped[qkey], key=lambda m: m.month)

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
        month_active_trend: list[float] = []

        for month in q_months:
            active_seconds += month.active_seconds
            recovery_seconds += month.recovery_seconds
            chain_count += month.chain_count
            signal_count += month.signal_count
            command_count += month.command_count
            transcript_count += month.transcript_count
            commit_count += month.commit_count
            total_days += month.total_days
            active_days += month.active_days
            chat_session_count += month.chat_session_count
            chat_cost_usd += month.chat_cost_usd
            episode_count += month.episode_count
            month_active_trend.append(month.active_seconds)
            for mode, seconds in month.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in month.top_projects:
                project_counter[project] += seconds
            for topic, seconds in month.top_topics:
                topic_counter[topic] += seconds
            for tier, count in month.coverage_summary.items():
                coverage_counter[tier] += count

        top_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])

        delta = (active_seconds - prior_active) if prior_active is not None else None

        quarters.append(TrajectoryQuarter(
            quarter=qkey,
            start_date=q_months[0].start_date,
            end_date=q_months[-1].end_date,
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
            month_count=len(q_months),
            month_active_trend=tuple(month_active_trend),
            active_delta_vs_prior=delta,
        ))

        prior_active = active_seconds

    return quarters
