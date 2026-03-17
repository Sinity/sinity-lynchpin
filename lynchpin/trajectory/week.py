"""Week-level trajectory rollup.

Groups TrajectoryDay summaries into ISO weeks, computing aggregate
activity, mode/project/topic distributions, and day-pattern classification.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from .day import TrajectoryDay


@dataclass(frozen=True)
class TrajectoryWeek:
    iso_week: str  # "2026-W11"
    start_date: date
    end_date: date
    days: int
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    day_pattern: str  # "front_loaded", "back_loaded", "uniform", "weekend_heavy"
    busiest_day: Optional[date]
    quietest_day: Optional[date]
    active_delta_vs_prior: Optional[float]  # seconds

    @property
    def dominant_mode(self) -> Optional[str]:
        return self.top_modes[0][0] if self.top_modes else None

    @property
    def dominant_project(self) -> Optional[str]:
        return self.top_projects[0][0] if self.top_projects else None

    @property
    def dominant_topic(self) -> Optional[str]:
        return self.top_topics[0][0] if self.top_topics else None

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "iso_week": self.iso_week,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "days": self.days,
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
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
            "top_projects": [[project, round(seconds, 3)] for project, seconds in self.top_projects],
            "top_topics": [[topic, round(seconds, 3)] for topic, seconds in self.top_topics],
            "day_pattern": self.day_pattern,
            "busiest_day": self.busiest_day.isoformat() if self.busiest_day else None,
            "quietest_day": self.quietest_day.isoformat() if self.quietest_day else None,
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


def _iso_week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _classify_day_pattern(days: Sequence[TrajectoryDay]) -> str:
    """Classify the distribution of activity across a week."""
    if not days:
        return "uniform"
    by_weekday = {d.date.weekday(): d.active_seconds for d in days}
    weekday_total = sum(by_weekday.get(i, 0.0) for i in range(5))  # Mon-Fri
    weekend_total = sum(by_weekday.get(i, 0.0) for i in range(5, 7))  # Sat-Sun
    total = weekday_total + weekend_total
    if total < 60:
        return "uniform"
    if weekend_total > weekday_total * 0.8:
        return "weekend_heavy"
    # Front-loaded: Mon-Wed heavier than Thu-Fri
    front = sum(by_weekday.get(i, 0.0) for i in range(3))
    back = sum(by_weekday.get(i, 0.0) for i in range(3, 5))
    if front > back * 1.5:
        return "front_loaded"
    if back > front * 1.5:
        return "back_loaded"
    return "uniform"


def summarize_weeks(
    days: Sequence[TrajectoryDay],
) -> list[TrajectoryWeek]:
    """Group days into ISO weeks and produce weekly summaries."""
    if not days:
        return []

    # Group by ISO week
    grouped: dict[str, list[TrajectoryDay]] = {}
    for day in days:
        key = _iso_week_key(day.date)
        grouped.setdefault(key, []).append(day)

    weeks: list[TrajectoryWeek] = []
    prior_active: Optional[float] = None

    for week_key in sorted(grouped):
        week_days = sorted(grouped[week_key], key=lambda d: d.date)
        mode_counter: Counter[str] = Counter()
        project_counter: Counter[str] = Counter()
        topic_counter: Counter[str] = Counter()
        active_seconds = 0.0
        recovery_seconds = 0.0
        chain_count = 0
        signal_count = 0
        command_count = 0
        transcript_count = 0
        commit_count = 0

        for day in week_days:
            active_seconds += day.active_seconds
            recovery_seconds += day.recovery_seconds
            chain_count += day.chain_count
            signal_count += day.signal_count
            command_count += day.command_count
            transcript_count += day.transcript_count
            commit_count += day.commit_count
            for mode, seconds in day.top_modes:
                mode_counter[mode] += seconds
            for project, seconds in day.top_projects:
                project_counter[project] += seconds
            for topic, seconds in day.top_topics:
                topic_counter[topic] += seconds

        busiest = max(week_days, key=lambda d: d.active_seconds) if week_days else None
        quietest = min(week_days, key=lambda d: d.active_seconds) if week_days else None
        delta = (active_seconds - prior_active) if prior_active is not None else None

        weeks.append(TrajectoryWeek(
            iso_week=week_key,
            start_date=week_days[0].date,
            end_date=week_days[-1].date,
            days=len(week_days),
            active_seconds=round(active_seconds, 3),
            recovery_seconds=round(recovery_seconds, 3),
            chain_count=chain_count,
            signal_count=signal_count,
            command_count=command_count,
            transcript_count=transcript_count,
            commit_count=commit_count,
            top_modes=tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5]),
            top_projects=tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5]),
            top_topics=tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5]),
            day_pattern=_classify_day_pattern(week_days),
            busiest_day=busiest.date if busiest else None,
            quietest_day=quietest.date if quietest else None,
            active_delta_vs_prior=delta,
        ))

        prior_active = active_seconds

    return weeks
