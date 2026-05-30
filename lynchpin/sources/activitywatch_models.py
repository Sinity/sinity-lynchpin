"""Dataclasses for the ActivityWatch source API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict


@dataclass(frozen=True)
class AWEvent:
    bucket: str
    start: datetime
    end: datetime
    data: Dict[str, object]


@dataclass(frozen=True)
class FocusSpan:
    start: datetime
    end: datetime
    kind: str  # "focused" | "afk" | "active_unknown"
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    keypress_count: int = 0
    keylog_state: str = "not_requested"

    @property
    def duration_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 0)

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class ProjectFocusDay:
    date: date
    project: str
    duration_s: float


@dataclass(frozen=True)
class FocusTimelineSpan:
    start: datetime
    end: datetime
    kind: str  # "focused" | "afk" | "active_unknown" | "coverage_gap"
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    source: str
    keypress_count: int = 0
    keylog_state: str = "not_requested"

    @property
    def duration_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 0)

    @property
    def date(self) -> date:
        return self.start.date()


@dataclass(frozen=True)
class _WindowSpan:
    start: datetime
    end: datetime
    app: str
    title: str
    mode: str | None
    project: str | None


@dataclass(frozen=True)
class AppSession:
    app: str
    start: datetime
    end: datetime
    duration_s: float
    title_dominant: str
    titles: tuple[str, ...]
    mode: str | None
    project: str | None
    interruptions: int


@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_min: float
    project: str | None
    mode: str
    focus_ratio: float
    app_switches: int


@dataclass(frozen=True)
class CircadianProfile:
    date: date
    hour: int
    active_min: float
    recovery_min: float
    dominant_mode: str | None
    dominant_project: str | None


@dataclass(frozen=True)
class FocusLoop:
    date: date
    start: datetime
    end: datetime
    duration_min: float
    span_count: int
    switch_count: int
    context_a: str
    context_b: str
    dominant_project: str | None


@dataclass(frozen=True)
class FragmentationMetrics:
    date: date
    total_switches: int
    avg_focus_min: float
    longest_focus_min: float
    fragmentation: float  # 0=focused, 1=scattered


@dataclass(frozen=True)
class AttentionMetrics:
    date: date
    entropy: float
    gini: float
    top_project: str | None
    project_count: int


@dataclass(frozen=True)
class SustainedFocus:
    """A sustained period of computer activity without significant AFK breaks.

    Unlike deep_work, this doesn't filter by mode — it measures ANY sustained
    active period. The mode/project are informational, not filtering criteria.
    """

    start: datetime
    end: datetime
    duration_min: float
    dominant_mode: str | None
    dominant_project: str | None
    app_switches: int


@dataclass(frozen=True)
class AWDayActivity:
    date: date
    active_hours: float
    deep_work_min: float
    fragmentation_score: float
    project_count: int
    dominant_mode: str | None
    dominant_project: str | None
    hourly_active: tuple[float, ...]  # 24 floats: active minutes per hour
    outage_hours: float = 0.0  # hours where AW data was unavailable (not operator AFK)
    presence_active_hours: float = 0.0  # cross-source presence: hours with confirmed operator activity
    presence_typing_hours: float = 0.0  # subset of presence_active_hours with active typing
    presence_data_gap_hours: float = 0.0  # hours where neither AW nor keylog had data
