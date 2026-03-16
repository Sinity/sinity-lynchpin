"""Typed context packet schemas for LLM consumption.

Defines frozen dataclasses for day, week, month, episode, project,
and coverage packets at three budget tiers: compact (~200 tok/day),
standard (~500), and full (~2000).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class ContextPacketMeta:
    schema: str
    generated_at: str
    budget_tier: str  # "compact", "standard", "full"


@dataclass(frozen=True)
class DayPacket:
    meta: ContextPacketMeta
    date: str
    active_hours: float
    recovery_hours: float
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    top_modes: list[tuple[str, float]]
    top_projects: list[tuple[str, float]]
    top_topics: list[tuple[str, float]]
    highlights: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "date": self.date,
            "active_hours": self.active_hours,
            "recovery_hours": self.recovery_hours,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "command_count": self.command_count,
            "transcript_count": self.transcript_count,
            "commit_count": self.commit_count,
            "top_modes": [[m, round(s, 2)] for m, s in self.top_modes],
            "top_projects": [[p, round(s, 2)] for p, s in self.top_projects],
            "top_topics": [[t, round(s, 2)] for t, s in self.top_topics],
            "highlights": self.highlights,
        }


@dataclass(frozen=True)
class WeekPacket:
    meta: ContextPacketMeta
    iso_week: str
    start_date: str
    end_date: str
    active_hours: float
    recovery_hours: float
    day_pattern: str
    chain_count: int
    top_modes: list[tuple[str, float]]
    top_projects: list[tuple[str, float]]
    top_topics: list[tuple[str, float]]
    active_delta_vs_prior: Optional[float]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "iso_week": self.iso_week,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "active_hours": self.active_hours,
            "recovery_hours": self.recovery_hours,
            "day_pattern": self.day_pattern,
            "chain_count": self.chain_count,
            "top_modes": [[m, round(s, 2)] for m, s in self.top_modes],
            "top_projects": [[p, round(s, 2)] for p, s in self.top_projects],
            "top_topics": [[t, round(s, 2)] for t, s in self.top_topics],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 2) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class MonthPacket:
    meta: ContextPacketMeta
    month: str
    active_hours: float
    recovery_hours: float
    active_days: int
    chain_count: int
    signal_count: int
    dominant_modes: list[tuple[str, float]]
    dominant_projects: list[tuple[str, float]]
    dominant_topics: list[tuple[str, float]]
    highlights: list[str]
    chat_session_count: int = 0
    chat_work_events: dict[str, int] = None  # type: ignore[assignment]
    chat_cost_usd: float = 0.0
    episode_count: int = 0
    episode_labels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chat_work_events is None:
            object.__setattr__(self, "chat_work_events", {})
        if self.episode_labels is None:
            object.__setattr__(self, "episode_labels", [])

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "month": self.month,
            "active_hours": self.active_hours,
            "recovery_hours": self.recovery_hours,
            "active_days": self.active_days,
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "dominant_modes": [[m, round(s, 2)] for m, s in self.dominant_modes],
            "dominant_projects": [[p, round(s, 2)] for p, s in self.dominant_projects],
            "dominant_topics": [[t, round(s, 2)] for t, s in self.dominant_topics],
            "highlights": self.highlights,
            "chat_session_count": self.chat_session_count,
            "chat_work_events": self.chat_work_events,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "episode_labels": self.episode_labels,
        }


@dataclass(frozen=True)
class EpisodePacket:
    meta: ContextPacketMeta
    episode_id: str
    label: str
    start_date: str
    end_date: str
    days: int
    active_hours: float
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    trigger: str
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "episode_id": self.episode_id,
            "label": self.label,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "days": self.days,
            "active_hours": self.active_hours,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "trigger": self.trigger,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ProjectPacket:
    meta: ContextPacketMeta
    project: str
    total_hours: float
    day_count: int
    chain_count: int
    top_modes: list[tuple[str, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "project": self.project,
            "total_hours": self.total_hours,
            "day_count": self.day_count,
            "chain_count": self.chain_count,
            "top_modes": [[m, round(s, 2)] for m, s in self.top_modes],
        }


@dataclass(frozen=True)
class ThreadPacket:
    meta: ContextPacketMeta
    thread_id: str
    depth: int
    session_count: int
    start_date: str
    end_date: str
    dominant_project: Optional[str]
    work_event_breakdown: dict[str, int]
    total_cost_usd: float

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "thread_id": self.thread_id,
            "depth": self.depth,
            "session_count": self.session_count,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "dominant_project": self.dominant_project,
            "work_event_breakdown": self.work_event_breakdown,
            "total_cost_usd": round(self.total_cost_usd, 4),
        }


@dataclass(frozen=True)
class CoveragePacket:
    meta: ContextPacketMeta
    day_count: int
    signal_count: int
    chain_count: int
    source_breakdown: dict[str, int]
    days_with_activitywatch: int
    days_with_terminal: int
    days_with_chatlog: int
    days_with_git: int

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "day_count": self.day_count,
            "signal_count": self.signal_count,
            "chain_count": self.chain_count,
            "source_breakdown": self.source_breakdown,
            "days_with_activitywatch": self.days_with_activitywatch,
            "days_with_terminal": self.days_with_terminal,
            "days_with_chatlog": self.days_with_chatlog,
            "days_with_git": self.days_with_git,
        }


@dataclass(frozen=True)
class QuarterPacket:
    meta: ContextPacketMeta
    quarter: str
    active_hours: float
    recovery_hours: float
    active_days: int
    chain_count: int
    signal_count: int
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    top_modes: list[tuple[str, float]]
    top_projects: list[tuple[str, float]]
    top_topics: list[tuple[str, float]]
    chat_session_count: int
    chat_cost_usd: float
    episode_count: int
    month_count: int
    month_active_trend: list[float]
    active_delta_vs_prior: Optional[float]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "quarter": self.quarter,
            "active_hours": self.active_hours,
            "recovery_hours": self.recovery_hours,
            "active_days": self.active_days,
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "top_modes": [[m, round(s, 2)] for m, s in self.top_modes],
            "top_projects": [[p, round(s, 2)] for p, s in self.top_projects],
            "top_topics": [[t, round(s, 2)] for t, s in self.top_topics],
            "chat_session_count": self.chat_session_count,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "month_count": self.month_count,
            "month_active_trend": [round(s, 2) for s in self.month_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 2) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class YearPacket:
    meta: ContextPacketMeta
    year: str
    active_hours: float
    recovery_hours: float
    active_days: int
    chain_count: int
    signal_count: int
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    dominant_topic: Optional[str]
    top_modes: list[tuple[str, float]]
    top_projects: list[tuple[str, float]]
    top_topics: list[tuple[str, float]]
    chat_session_count: int
    chat_cost_usd: float
    episode_count: int
    quarter_count: int
    quarter_active_trend: list[float]
    active_delta_vs_prior: Optional[float]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "year": self.year,
            "active_hours": self.active_hours,
            "recovery_hours": self.recovery_hours,
            "active_days": self.active_days,
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "dominant_topic": self.dominant_topic,
            "top_modes": [[m, round(s, 2)] for m, s in self.top_modes],
            "top_projects": [[p, round(s, 2)] for p, s in self.top_projects],
            "top_topics": [[t, round(s, 2)] for t, s in self.top_topics],
            "chat_session_count": self.chat_session_count,
            "chat_cost_usd": round(self.chat_cost_usd, 4),
            "episode_count": self.episode_count,
            "quarter_count": self.quarter_count,
            "quarter_active_trend": [round(s, 2) for s in self.quarter_active_trend],
            "active_delta_vs_prior": round(self.active_delta_vs_prior, 2) if self.active_delta_vs_prior is not None else None,
        }


@dataclass(frozen=True)
class ThemePacket:
    meta: ContextPacketMeta
    name: str
    kind: str
    total_hours: float
    month_count: int
    trend: str
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "name": self.name,
            "kind": self.kind,
            "total_hours": self.total_hours,
            "month_count": self.month_count,
            "trend": self.trend,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass(frozen=True)
class ClaimPacket:
    statement: str
    confidence: float
    evidence_refs: tuple[str, ...]
    category: str

    def to_dict(self) -> dict[str, object]:
        return {
            "statement": self.statement,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "category": self.category,
        }


@dataclass(frozen=True)
class ClaimsPacket:
    meta: ContextPacketMeta
    claims: tuple[ClaimPacket, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "claims": [c.to_dict() for c in self.claims],
        }


@dataclass(frozen=True)
class ProjectArcPacket:
    meta: ContextPacketMeta
    project: str
    total_hours: float
    active_months: int
    velocity_trend: str
    cost_usd: float
    active_episodes: int
    momentum: str

    def to_dict(self) -> dict[str, object]:
        return {
            "meta": {"schema": self.meta.schema, "generated_at": self.meta.generated_at, "budget_tier": self.meta.budget_tier},
            "project": self.project,
            "total_hours": self.total_hours,
            "active_months": self.active_months,
            "velocity_trend": self.velocity_trend,
            "cost_usd": round(self.cost_usd, 2),
            "active_episodes": self.active_episodes,
            "momentum": self.momentum,
        }
