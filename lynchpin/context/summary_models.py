"""Context-owned summary models built from warehouse evidence surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
    chat_session_count: int = 0
    chat_work_events: dict[str, int] | None = None
    chat_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})


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
    active_delta_vs_prior: float | None
    dominant_mode: str | None
    dominant_project: str | None
    dominant_topic: str | None


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


@dataclass(frozen=True)
class QuarterSummary:
    quarter: str
    active_seconds: float
    recovery_seconds: float
    active_days: int
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


@dataclass(frozen=True)
class YearSummary:
    year: str
    active_seconds: float
    recovery_seconds: float
    active_days: int
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


@dataclass(frozen=True)
class ChainSummary:
    chain_id: str
    project: str | None
    mode: str | None
    duration_seconds: float
