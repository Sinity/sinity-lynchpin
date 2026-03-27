"""Context-owned summary models built from warehouse evidence surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .signal_coverage import SignalCoverage


@dataclass(frozen=True)
class DayProjectSummary:
    date: date
    project: str
    duration_seconds: float
    chain_count: int
    top_modes: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "project": self.project,
            "duration_seconds": round(self.duration_seconds, 3),
            "chain_count": self.chain_count,
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
        }


@dataclass(frozen=True)
class DaySummary:
    date: date
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: list[str]
    projects: tuple[str, ...] = ()
    project_summaries: tuple[DayProjectSummary, ...] = ()
    chat_session_count: int = 0
    chat_work_events: dict[str, int] | None = None
    chat_cost_usd: float = 0.0
    signal_coverage: "SignalCoverage | None" = None
    anomalies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
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
            "source_counts": self.source_counts,
            "coverage": self.coverage,
            "highlights": list(self.highlights),
            "projects": [project.to_dict() for project in self.project_summaries],
            "signal_coverage": self.signal_coverage.to_dict() if self.signal_coverage else None,
            "anomalies": list(self.anomalies),
        }


@dataclass(frozen=True)
class PeriodSummary:
    start_date: str
    end_date: str
    total_days: int
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_modes: tuple[tuple[str, float], ...]
    dominant_projects: tuple[tuple[str, float], ...]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: tuple[str, ...]
    dominant_topics: tuple[tuple[str, float], ...] = ()

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_days": self.total_days,
            "active_seconds": round(self.active_seconds, 3),
            "recovery_seconds": round(self.recovery_seconds, 3),
            "observed_seconds": round(self.observed_seconds, 3),
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "command_count": self.command_count,
            "transcript_count": self.transcript_count,
            "commit_count": self.commit_count,
            "dominant_modes": [[mode, round(seconds, 3)] for mode, seconds in self.dominant_modes],
            "dominant_projects": [[project, round(seconds, 3)] for project, seconds in self.dominant_projects],
            "dominant_topics": [[topic, round(seconds, 3)] for topic, seconds in self.dominant_topics],
            "source_counts": self.source_counts,
            "coverage": self.coverage,
            "highlights": list(self.highlights),
        }


@dataclass(frozen=True)
class WeekSummary:
    iso_week: str
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
    day_pattern: str
    busiest_day: date | None
    quietest_day: date | None
    active_delta_vs_prior: float | None = None
    dominant_mode: str | None = None
    dominant_project: str | None = None
    dominant_topic: str | None = None

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


@dataclass(frozen=True)
class MonthSummary:
    month: str
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
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    source_counts: dict[str, int]
    coverage_summary: dict[str, object]
    highlights: list[str]
    chat_session_count: int
    chat_work_events: dict[str, int]
    chat_cost_usd: float
    episode_count: int
    episode_labels: list[str]
    week_count: int
    day_patterns: list[str]
    active_delta_vs_prior: float | None = None

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
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
            "top_projects": [[project, round(seconds, 3)] for project, seconds in self.top_projects],
            "top_topics": [[topic, round(seconds, 3)] for topic, seconds in self.top_topics],
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
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class QuarterSummary:
    quarter: str
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    chat_session_count: int
    chat_cost_usd: float
    episode_count: int
    month_count: int
    month_active_trend: list[float]
    active_delta_vs_prior: float | None
    start_date: date | None = None
    end_date: date | None = None
    total_days: int = 0
    active_days: int = 0
    command_count: int = 0
    transcript_count: int = 0
    commit_count: int = 0
    coverage_summary: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.coverage_summary is None:
            object.__setattr__(self, "coverage_summary", {})

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "quarter": self.quarter,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
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
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
            "top_projects": [[project, round(seconds, 3)] for project, seconds in self.top_projects],
            "top_topics": [[topic, round(seconds, 3)] for topic, seconds in self.top_topics],
            "coverage_summary": self.coverage_summary,
            "chat_session_count": self.chat_session_count,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "month_count": self.month_count,
            "month_active_trend": [round(seconds, 3) for seconds in self.month_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class YearSummary:
    year: str
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    top_topics: tuple[tuple[str, float], ...]
    chat_session_count: int
    chat_cost_usd: float
    episode_count: int
    quarter_count: int
    quarter_active_trend: list[float]
    active_delta_vs_prior: float | None
    start_date: date | None = None
    end_date: date | None = None
    total_days: int = 0
    active_days: int = 0
    command_count: int = 0
    transcript_count: int = 0
    commit_count: int = 0
    coverage_summary: dict[str, int] | None = None

    def __post_init__(self) -> None:
        if self.coverage_summary is None:
            object.__setattr__(self, "coverage_summary", {})

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "year": self.year,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
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
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
            "top_projects": [[project, round(seconds, 3)] for project, seconds in self.top_projects],
            "top_topics": [[topic, round(seconds, 3)] for topic, seconds in self.top_topics],
            "coverage_summary": self.coverage_summary,
            "chat_session_count": self.chat_session_count,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "quarter_count": self.quarter_count,
            "quarter_active_trend": [round(seconds, 3) for seconds in self.quarter_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 3) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class EpisodeSummary:
    episode_id: str
    label: str
    start_date: date
    end_date: date
    days: int
    active_seconds: float
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None
    trigger: str
    confidence: float
    mode_distribution: dict[str, float] | None = None
    project_distribution: dict[str, float] | None = None
    day_count_with_dominant: int = 0

    def __post_init__(self) -> None:
        if self.mode_distribution is None:
            object.__setattr__(self, "mode_distribution", {})
        if self.project_distribution is None:
            object.__setattr__(self, "project_distribution", {})

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "label": self.label,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "days": self.days,
            "active_seconds": round(self.active_seconds, 3),
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "mode_distribution": self.mode_distribution,
            "project_distribution": self.project_distribution,
            "trigger": self.trigger,
            "confidence": round(self.confidence, 3),
            "day_count_with_dominant": self.day_count_with_dominant,
        }


@dataclass(frozen=True)
class ChainSummary:
    chain_id: str
    project: str | None
    mode: str | None
    duration_seconds: float
