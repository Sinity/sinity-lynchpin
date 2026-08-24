"""Pure, coverage-aware reconstruction of recent daily life evidence.

This module intentionally has no source, substrate, CLI, or narrative
dependency. Callers supply typed observations and the intervals for which a
source was actually observing. The result is answer-card data, not a claim
about unobserved time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Iterable

from lynchpin.core.parse import as_local
from lynchpin.core.primitives import date_to_dt_range, logical_date


class Agency(StrEnum):
    """Who performed the activity represented by an observation."""

    DIRECT = "direct"
    SUPERVISORY = "supervisory"
    DELEGATED = "delegated"
    AUTOMATED = "automated"


class Activity(StrEnum):
    """Stable, intentionally broad daily-life activity vocabulary."""

    SLEEP = "sleep"
    PERSONAL_CARE = "personal_care"
    MEAL = "meal"
    HOUSEHOLD = "household"
    MAINTENANCE = "maintenance"
    MOVEMENT = "movement"
    HEALTH = "health"
    WORK = "work"
    LEARNING = "learning"
    ADMINISTRATION = "administration"
    COMMUNICATION = "communication"
    SOCIAL = "social"
    LEISURE = "leisure"
    ENTERTAINMENT = "entertainment"
    CONSUMPTION = "consumption"
    TRAVEL = "travel"
    REST = "rest"
    UNKNOWN = "unknown"


class BoundaryMethod(StrEnum):
    SLEEP = "sleep"
    ACTIVITY = "activity"
    CANONICAL = "canonical"


class DayStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class EvidenceEvent:
    """One neutral, already-classified observation.

    ``DELEGATED`` and ``AUTOMATED`` events remain useful evidence but are never
    promoted to operator attention. ``maintenance_evidenced`` has to be true
    before a maintenance label contributes to maintenance totals.
    """

    start: datetime
    end: datetime
    activity: Activity
    agency: Agency
    source: str
    evidence_refs: tuple[str, ...] = ()
    purposeful: bool = False
    external_anchor: str | None = None
    maintenance_evidenced: bool = False

    def __post_init__(self) -> None:
        start, end = as_local(self.start), as_local(self.end)
        if end <= start:
            raise ValueError("evidence event end must be after start")
        if not self.source:
            raise ValueError("evidence event source must be non-empty")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


DailyLifeEvent = EvidenceEvent


@dataclass(frozen=True)
class CoverageInterval:
    """An interval for which a named source could have observed activity."""

    start: datetime
    end: datetime
    source: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        start, end = as_local(self.start), as_local(self.end)
        if end <= start:
            raise ValueError("coverage interval end must be after start")
        if not self.source:
            raise ValueError("coverage interval source must be non-empty")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


@dataclass(frozen=True)
class DailyBoundary:
    logical_day: date
    at: datetime
    method: BoundaryMethod
    confidence: float
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class DailySegment:
    start: datetime
    end: datetime
    activities: tuple[Activity, ...]
    agencies: tuple[Agency, ...]
    covered_sources: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def is_unknown_gap(self) -> bool:
        return not self.covered_sources


@dataclass(frozen=True)
class EvidenceAnchor:
    label: str
    at: datetime
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    seconds: float
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class AgencySeconds:
    direct: float = 0.0
    supervisory: float = 0.0
    delegated: float = 0.0
    automated: float = 0.0

    def for_agency(self, agency: Agency) -> float:
        return getattr(self, agency.value)


@dataclass(frozen=True)
class DayTypeInputs:
    """Measured inputs for a later, interpretable day-type decision."""

    coverage_ratio: float
    unknown_gap_ratio: float
    direct_ratio: float
    supervisory_ratio: float
    delegated_ratio: float
    automated_ratio: float
    operator_attention_ratio: float
    sleep_ratio: float
    maintenance_ratio: float
    external_anchor_count: int
    has_purposeful_direct_action: bool


@dataclass(frozen=True)
class DailyLifeSummary:
    logical_day: date
    start: datetime
    end: datetime
    status: DayStatus
    start_boundary: DailyBoundary
    end_boundary: DailyBoundary
    coverage_seconds: float
    unknown_gap_seconds: float
    agency_seconds: AgencySeconds
    operator_attention_seconds: float
    activity_seconds: tuple[tuple[Activity, float], ...]
    maintenance_seconds: float
    first_purposeful_direct_action: EvidenceAnchor | None
    external_anchors: tuple[EvidenceAnchor, ...]
    source_coverage: tuple[SourceCoverage, ...]
    unknown_gaps: tuple[tuple[datetime, datetime], ...]
    evidence_refs: tuple[str, ...]
    segments: tuple[DailySegment, ...]
    day_type_inputs: DayTypeInputs

    def seconds_for_activity(self, activity: Activity) -> float:
        return dict(self.activity_seconds).get(activity, 0.0)


@dataclass(frozen=True)
class CurrentDailyLife:
    """Answer-card-ready yesterday plus current logical-day evidence."""

    as_of: datetime
    yesterday: DailyLifeSummary
    current_day: DailyLifeSummary
    complete_days: tuple[DailyLifeSummary, ...]


def _canonical_boundary(day: date) -> datetime:
    start, _ = date_to_dt_range(day, day)
    return as_local(start)


def _overlaps(start: datetime, end: datetime, other_start: datetime, other_end: datetime) -> bool:
    return start < other_end and other_start < end


def _clip(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> tuple[datetime, datetime] | None:
    clipped_start, clipped_end = max(start, window_start), min(end, window_end)
    return (clipped_start, clipped_end) if clipped_end > clipped_start else None


def _ordered_refs(refs: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(refs)))


def _merge_adjacent(intervals: Iterable[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    merged: list[list[datetime]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _boundary_for_day(day: date, events: tuple[EvidenceEvent, ...]) -> DailyBoundary:
    canonical = _canonical_boundary(day)
    sleep_ends = [
        event
        for event in events
        if event.activity is Activity.SLEEP and logical_date(event.end) == day
    ]
    if sleep_ends:
        selected = min(sleep_ends, key=lambda event: event.end)
        return DailyBoundary(
            logical_day=day,
            at=selected.end,
            method=BoundaryMethod.SLEEP,
            confidence=1.0,
            evidence_refs=selected.evidence_refs,
        )

    activity_starts = [
        event
        for event in events
        if event.activity is not Activity.SLEEP
        and event.agency in {Agency.DIRECT, Agency.SUPERVISORY}
        and logical_date(event.start) == day
        and event.start < canonical + timedelta(hours=12)
    ]
    if activity_starts:
        selected = min(activity_starts, key=lambda event: event.start)
        return DailyBoundary(
            logical_day=day,
            at=selected.start,
            method=BoundaryMethod.ACTIVITY,
            confidence=0.6,
            evidence_refs=selected.evidence_refs,
        )

    return DailyBoundary(
        logical_day=day,
        at=canonical,
        method=BoundaryMethod.CANONICAL,
        confidence=0.25,
        evidence_refs=(),
    )


def _daily_boundaries(
    start: date,
    end: date,
    events: tuple[EvidenceEvent, ...],
) -> dict[date, DailyBoundary]:
    boundaries: dict[date, DailyBoundary] = {}
    cursor = start
    while cursor <= end + timedelta(days=1):
        boundaries[cursor] = _boundary_for_day(cursor, events)
        cursor += timedelta(days=1)
    return boundaries


def _segments(
    start: datetime,
    end: datetime,
    events: tuple[EvidenceEvent, ...],
    coverage: tuple[CoverageInterval, ...],
) -> tuple[DailySegment, ...]:
    relevant_events = tuple(event for event in events if _overlaps(start, end, event.start, event.end))
    relevant_coverage = tuple(item for item in coverage if _overlaps(start, end, item.start, item.end))
    points = {start, end}
    for event in relevant_events:
        clipped = _clip(event.start, event.end, start, end)
        if clipped:
            points.update(clipped)
    for interval in relevant_coverage:
        clipped = _clip(interval.start, interval.end, start, end)
        if clipped:
            points.update(clipped)

    ordered_points = sorted(points)
    segments: list[DailySegment] = []
    for segment_start, segment_end in zip(ordered_points, ordered_points[1:]):
        active_events = tuple(
            event
            for event in relevant_events
            if _overlaps(segment_start, segment_end, event.start, event.end)
        )
        active_coverage = tuple(
            interval
            for interval in relevant_coverage
            if _overlaps(segment_start, segment_end, interval.start, interval.end)
        )
        activities = {
            event.activity
            for event in active_events
            if event.activity is not Activity.MAINTENANCE or event.maintenance_evidenced
        }
        if any(event.activity is Activity.MAINTENANCE and not event.maintenance_evidenced for event in active_events):
            activities.add(Activity.UNKNOWN)
        refs = _ordered_refs(
            ref
            for item in (*active_events, *active_coverage)
            for ref in item.evidence_refs
        )
        segments.append(
            DailySegment(
                start=segment_start,
                end=segment_end,
                activities=tuple(sorted(activities, key=str)),
                agencies=tuple(sorted({event.agency for event in active_events}, key=str)),
                covered_sources=tuple(sorted({item.source for item in active_coverage})),
                evidence_refs=refs,
            )
        )
    return tuple(segments)


def _summarize_day(
    day: date,
    start_boundary: DailyBoundary,
    end_boundary: DailyBoundary,
    *,
    as_of: datetime,
    events: tuple[EvidenceEvent, ...],
    coverage: tuple[CoverageInterval, ...],
) -> DailyLifeSummary:
    full_end = max(end_boundary.at, start_boundary.at + timedelta(microseconds=1))
    status = DayStatus.PARTIAL if as_of < full_end else DayStatus.COMPLETE
    end = min(full_end, as_of)
    if end <= start_boundary.at:
        end = start_boundary.at
    segments = _segments(start_boundary.at, end, events, coverage) if end > start_boundary.at else ()

    agency_totals = {agency: 0.0 for agency in Agency}
    activity_totals = {activity: 0.0 for activity in Activity}
    coverage_total = 0.0
    operator_attention_total = 0.0
    unknown_gaps: list[tuple[datetime, datetime]] = []
    source_seconds: dict[str, float] = {}
    source_refs: dict[str, set[str]] = {}

    for interval in coverage:
        if _overlaps(start_boundary.at, end, interval.start, interval.end):
            source_refs.setdefault(interval.source, set()).update(interval.evidence_refs)

    for segment in segments:
        seconds = segment.seconds
        if segment.covered_sources:
            coverage_total += seconds
            for source in segment.covered_sources:
                source_seconds[source] = source_seconds.get(source, 0.0) + seconds
        else:
            unknown_gaps.append((segment.start, segment.end))
        for agency in segment.agencies:
            if Activity.SLEEP not in segment.activities:
                agency_totals[agency] += seconds
        if Activity.SLEEP not in segment.activities and any(
            agency in {Agency.DIRECT, Agency.SUPERVISORY} for agency in segment.agencies
        ):
            operator_attention_total += seconds
        for activity in segment.activities:
            activity_totals[activity] += seconds

    purposeful = [
        event
        for event in events
        if event.agency is Agency.DIRECT
        and event.purposeful
        and event.activity is not Activity.SLEEP
        and _overlaps(start_boundary.at, end, event.start, event.end)
    ]
    first_action = None
    if purposeful:
        event = min(purposeful, key=lambda item: item.start)
        first_action = EvidenceAnchor(
            label=event.activity.value,
            at=max(event.start, start_boundary.at),
            evidence_refs=event.evidence_refs,
        )

    anchors = sorted(
        (
            EvidenceAnchor(
                label=event.external_anchor or "",
                at=max(event.start, start_boundary.at),
                evidence_refs=event.evidence_refs,
            )
            for event in events
            if event.external_anchor and _overlaps(start_boundary.at, end, event.start, event.end)
        ),
        key=lambda anchor: (anchor.at, anchor.label),
    )
    duration = max((end - start_boundary.at).total_seconds(), 0.0)
    merged_unknown_gaps = _merge_adjacent(unknown_gaps)
    unknown_total = sum((gap_end - gap_start).total_seconds() for gap_start, gap_end in merged_unknown_gaps)
    ratio = lambda value: value / duration if duration else 0.0
    inputs = DayTypeInputs(
        coverage_ratio=ratio(coverage_total),
        unknown_gap_ratio=ratio(unknown_total),
        direct_ratio=ratio(agency_totals[Agency.DIRECT]),
        supervisory_ratio=ratio(agency_totals[Agency.SUPERVISORY]),
        delegated_ratio=ratio(agency_totals[Agency.DELEGATED]),
        automated_ratio=ratio(agency_totals[Agency.AUTOMATED]),
        operator_attention_ratio=ratio(operator_attention_total),
        sleep_ratio=ratio(activity_totals[Activity.SLEEP]),
        maintenance_ratio=ratio(activity_totals[Activity.MAINTENANCE]),
        external_anchor_count=len(anchors),
        has_purposeful_direct_action=first_action is not None,
    )
    refs = _ordered_refs(
        ref
        for segment in segments
        for ref in segment.evidence_refs
    )
    return DailyLifeSummary(
        logical_day=day,
        start=start_boundary.at,
        end=end,
        status=status,
        start_boundary=start_boundary,
        end_boundary=end_boundary,
        coverage_seconds=coverage_total,
        unknown_gap_seconds=unknown_total,
        agency_seconds=AgencySeconds(**{agency.value: agency_totals[agency] for agency in Agency}),
        operator_attention_seconds=operator_attention_total,
        activity_seconds=tuple(
            (activity, seconds)
            for activity, seconds in activity_totals.items()
            if seconds > 0
        ),
        maintenance_seconds=activity_totals[Activity.MAINTENANCE],
        first_purposeful_direct_action=first_action,
        external_anchors=tuple(anchors),
        source_coverage=tuple(
            SourceCoverage(
                source=source,
                seconds=seconds,
                evidence_refs=tuple(sorted(source_refs.get(source, set()))),
            )
            for source, seconds in sorted(source_seconds.items())
        ),
        unknown_gaps=merged_unknown_gaps,
        evidence_refs=refs,
        segments=segments,
        day_type_inputs=inputs,
    )


def reconstruct_daily_life(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    start: date,
    end: date,
    as_of: datetime,
) -> tuple[DailyLifeSummary, ...]:
    """Reconstruct inclusive logical days without inferring over coverage gaps."""

    if end < start:
        raise ValueError("end must not precede start")
    local_as_of = as_local(as_of)
    normalized_events = tuple(
        sorted((event for event in events if event.start < local_as_of), key=lambda event: event.start)
    )
    normalized_coverage = tuple(sorted(coverage, key=lambda interval: interval.start))
    boundaries = _daily_boundaries(start, end, normalized_events)
    summaries: list[DailyLifeSummary] = []
    cursor = start
    while cursor <= end:
        summaries.append(
            _summarize_day(
                cursor,
                boundaries[cursor],
                boundaries[cursor + timedelta(days=1)],
                as_of=local_as_of,
                events=normalized_events,
                coverage=normalized_coverage,
            )
        )
        cursor += timedelta(days=1)
    return tuple(summaries)


def reconstruct_current_daily_life(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    as_of: datetime,
) -> CurrentDailyLife:
    """Return an answer-card-ready yesterday and current-day reconstruction."""

    local_as_of = as_local(as_of)
    current_day = logical_date(local_as_of)
    yesterday = current_day - timedelta(days=1)
    summaries = reconstruct_daily_life(
        events,
        coverage,
        start=yesterday,
        end=current_day,
        as_of=local_as_of,
    )
    return CurrentDailyLife(
        as_of=local_as_of,
        yesterday=summaries[0],
        current_day=summaries[1],
        complete_days=tuple(summary for summary in summaries if summary.status is DayStatus.COMPLETE),
    )


__all__ = [
    "Activity",
    "Agency",
    "AgencySeconds",
    "BoundaryMethod",
    "CoverageInterval",
    "CurrentDailyLife",
    "DailyBoundary",
    "DailyLifeEvent",
    "DailyLifeSummary",
    "DailySegment",
    "DayStatus",
    "DayTypeInputs",
    "EvidenceAnchor",
    "EvidenceEvent",
    "SourceCoverage",
    "reconstruct_current_daily_life",
    "reconstruct_daily_life",
]
