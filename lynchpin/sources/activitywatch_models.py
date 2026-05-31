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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"AWEvent.end ({self.end}) precedes start ({self.start})"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"FocusSpan.end ({self.end}) precedes start ({self.start})"
            )
        if self.keypress_count < 0:
            raise ValueError(
                f"FocusSpan.keypress_count ({self.keypress_count}) must be >= 0"
            )

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

    def __post_init__(self) -> None:
        if self.duration_s < 0:
            raise ValueError(
                f"ProjectFocusDay.duration_s ({self.duration_s}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"FocusTimelineSpan.end ({self.end}) precedes start ({self.start})"
            )
        if self.keypress_count < 0:
            raise ValueError(
                f"FocusTimelineSpan.keypress_count ({self.keypress_count}) must be >= 0"
            )

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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"_WindowSpan.end ({self.end}) precedes start ({self.start})"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"AppSession.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_s < 0:
            raise ValueError(
                f"AppSession.duration_s ({self.duration_s}) must be >= 0"
            )
        if self.interruptions < 0:
            raise ValueError(
                f"AppSession.interruptions ({self.interruptions}) must be >= 0"
            )


@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_min: float
    project: str | None
    mode: str
    focus_ratio: float
    app_switches: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"DeepWorkBlock.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"DeepWorkBlock.duration_min ({self.duration_min}) must be >= 0"
            )
        if not (0.0 <= self.focus_ratio <= 1.0):
            raise ValueError(
                f"DeepWorkBlock.focus_ratio ({self.focus_ratio}) must be in [0.0, 1.0]"
            )
        if self.app_switches < 0:
            raise ValueError(
                f"DeepWorkBlock.app_switches ({self.app_switches}) must be >= 0"
            )


@dataclass(frozen=True)
class CircadianProfile:
    date: date
    hour: int
    active_min: float
    recovery_min: float
    dominant_mode: str | None
    dominant_project: str | None

    def __post_init__(self) -> None:
        if not (0 <= self.hour <= 23):
            raise ValueError(
                f"CircadianProfile.hour ({self.hour}) must be in [0, 23]"
            )
        if self.active_min < 0:
            raise ValueError(
                f"CircadianProfile.active_min ({self.active_min}) must be >= 0"
            )
        if self.recovery_min < 0:
            raise ValueError(
                f"CircadianProfile.recovery_min ({self.recovery_min}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"FocusLoop.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"FocusLoop.duration_min ({self.duration_min}) must be >= 0"
            )
        if self.span_count < 0:
            raise ValueError(
                f"FocusLoop.span_count ({self.span_count}) must be >= 0"
            )
        if self.switch_count < 0:
            raise ValueError(
                f"FocusLoop.switch_count ({self.switch_count}) must be >= 0"
            )


@dataclass(frozen=True)
class FragmentationMetrics:
    date: date
    total_switches: int
    avg_focus_min: float
    longest_focus_min: float
    fragmentation: float  # 0=focused, 1=scattered

    def __post_init__(self) -> None:
        if self.total_switches < 0:
            raise ValueError(
                f"FragmentationMetrics.total_switches ({self.total_switches}) must be >= 0"
            )
        if self.avg_focus_min < 0:
            raise ValueError(
                f"FragmentationMetrics.avg_focus_min ({self.avg_focus_min}) must be >= 0"
            )
        if self.longest_focus_min < 0:
            raise ValueError(
                f"FragmentationMetrics.longest_focus_min ({self.longest_focus_min}) must be >= 0"
            )
        if not (0.0 <= self.fragmentation <= 1.0):
            raise ValueError(
                f"FragmentationMetrics.fragmentation ({self.fragmentation}) must be in [0.0, 1.0]"
            )


@dataclass(frozen=True)
class AttentionMetrics:
    date: date
    entropy: float
    gini: float
    top_project: str | None
    project_count: int

    def __post_init__(self) -> None:
        if self.entropy < 0:
            raise ValueError(
                f"AttentionMetrics.entropy ({self.entropy}) must be >= 0"
            )
        if not (0.0 <= self.gini <= 1.0):
            raise ValueError(
                f"AttentionMetrics.gini ({self.gini}) must be in [0.0, 1.0]"
            )
        if self.project_count < 0:
            raise ValueError(
                f"AttentionMetrics.project_count ({self.project_count}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"SustainedFocus.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"SustainedFocus.duration_min ({self.duration_min}) must be >= 0"
            )
        if self.app_switches < 0:
            raise ValueError(
                f"SustainedFocus.app_switches ({self.app_switches}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if self.active_hours < 0:
            raise ValueError(
                f"AWDayActivity.active_hours ({self.active_hours}) must be >= 0"
            )
        if self.deep_work_min < 0:
            raise ValueError(
                f"AWDayActivity.deep_work_min ({self.deep_work_min}) must be >= 0"
            )
        if self.project_count < 0:
            raise ValueError(
                f"AWDayActivity.project_count ({self.project_count}) must be >= 0"
            )
        if self.outage_hours < 0:
            raise ValueError(
                f"AWDayActivity.outage_hours ({self.outage_hours}) must be >= 0"
            )
        if len(self.hourly_active) != 24:
            raise ValueError(
                f"AWDayActivity.hourly_active must have exactly 24 elements, got {len(self.hourly_active)}"
            )
