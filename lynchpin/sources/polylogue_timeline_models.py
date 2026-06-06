"""Typed time-composition models for Polylogue sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PolylogueTimelineSpan:
    """One timed or point-like row in a session timeline."""

    span_id: str
    session_id: str
    provider: str
    lane: str
    kind: str
    start: datetime
    end: datetime
    source: str
    role: str | None = None
    project: str | None = None
    app: str | None = None
    summary: str | None = None
    tool_names: tuple[str, ...] = ()
    fidelity: str = "observed"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"span {self.span_id} ends before it starts")
        if self.confidence < 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be in [0.0, 1.0]")

    @property
    def duration_s(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)


@dataclass(frozen=True)
class PolylogueCrossSourceOverlap:
    """Overlap between a Polylogue-native span and an external source span."""

    session_id: str
    primary_span_id: str
    other_span_id: str
    source: str
    lane: str
    kind: str
    start: datetime
    end: datetime
    duration_s: float
    project: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolylogueSessionComposition:
    """Per-session rollup over timeline spans and external overlaps."""

    session_id: str
    provider: str
    title: str
    start: datetime | None
    end: datetime | None
    status: str
    reason: str | None
    message_count: int
    wall_seconds: float
    engaged_seconds: float
    span_count: int
    overlap_count: int
    seconds_by_lane: dict[str, float]
    seconds_by_kind: dict[str, float]
    cross_source_seconds: dict[str, float]
    projects: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
