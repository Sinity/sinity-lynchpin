"""Pure, coverage-aware reconstruction of daily-life evidence.

Callers supply neutral observations and explicit source-coverage intervals. This
module produces deterministic answer-card data only: it does not infer motives,
diagnoses, or activity during unobserved gaps.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from lynchpin.core.parse import as_local
from lynchpin.core.primitives import date_to_dt_range, logical_date


class Agency(StrEnum):
    """Who performed, supervised, or was observed in an activity interval."""

    DIRECT_OPERATOR = "direct_operator"
    ACTIVE_SUPERVISION_OR_REVIEW = "active_supervision_or_review"
    DELEGATED_AGENT = "delegated_agent"
    AUTOMATED_SYSTEM = "automated_system"
    PASSIVE_OR_CONSUMPTIVE = "passive_or_consumptive"
    OFFLINE_OBSERVED = "offline_observed"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class Activity(StrEnum):
    """Neutral behavioral classes for daily-life reconstruction."""

    SLEEP = "sleep"
    PROJECT_WORK = "project_work"
    PROJECT_SUPERVISION = "project_supervision"
    COMMUNICATION = "communication"
    THERAPY_OR_HEALTH = "therapy_or_health"
    ADMINISTRATION = "administration"
    EDUCATION_OR_RESEARCH = "education_or_research"
    MEDIA_OR_READING = "media_or_reading"
    SOCIAL = "social"
    MOBILITY_OR_OUTSIDE = "mobility_or_outside"
    DOMESTIC_OR_SELF_MAINTENANCE = "domestic_or_self_maintenance"
    SUBSTANCE_OR_MEDICATION_EVENT = "substance_or_medication_event"
    REST_OR_LOW_OBSERVED_ACTIVITY = "rest_or_low_observed_activity"
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
    """One neutral, already-classified observation from a named source."""

    start: datetime
    end: datetime
    activity: Activity
    agency: Agency
    source: str
    evidence_refs: tuple[str, ...] = ()
    purposeful: bool = False
    external_anchor: str | None = None

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
    source: str | None
    confidence: float
    ambiguity: str | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class DailySegment:
    """One non-overlapping classified interval.

    Agency is intentionally exclusive. Concurrent incompatible agency
    observations become mixed so agency ratios cannot double-count a wall clock
    interval. Component classifications retain source-level evidence.
    """

    start: datetime
    end: datetime
    activity: Activity
    agency: Agency
    component_activities: tuple[Activity, ...]
    component_agencies: tuple[Agency, ...]
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
    direct_operator: float = 0.0
    active_supervision_or_review: float = 0.0
    delegated_agent: float = 0.0
    automated_system: float = 0.0
    passive_or_consumptive: float = 0.0
    offline_observed: float = 0.0
    mixed: float = 0.0
    unknown: float = 0.0

    def for_agency(self, agency: Agency) -> float:
        return getattr(self, agency.value)


@dataclass(frozen=True)
class DayTypeInputs:
    """Measured inputs that retain evidence for later day-type evaluation."""

    coverage_ratio: float
    unknown_gap_ratio: float
    agency_ratios: tuple[tuple[Agency, float], ...]
    sleep_ratio: float
    external_anchor_count: int
    has_purposeful_direct_action: bool
    representative_evidence_refs: tuple[str, ...]
    counterexample_candidate_refs: tuple[str, ...]

    def ratio_for_agency(self, agency: Agency) -> float:
        return dict(self.agency_ratios).get(agency, 0.0)


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
    wake_time: datetime | None
    first_purposeful_direct_action: EvidenceAnchor | None
    wake_to_first_purposeful_direct_action_seconds: float | None
    external_anchors: tuple[EvidenceAnchor, ...]
    source_coverage: tuple[SourceCoverage, ...]
    unknown_gaps: tuple[tuple[datetime, datetime], ...]
    evidence_refs: tuple[str, ...]
    segments: tuple[DailySegment, ...]
    day_type_inputs: DayTypeInputs

    def seconds_for_activity(self, activity: Activity) -> float:
        return dict(self.activity_seconds).get(activity, 0.0)

    @property
    def is_reconstructed(self) -> bool:
        """Whether a completed day has at least one classified observation."""

        return self.status is DayStatus.COMPLETE and any(
            segment.activity is not Activity.UNKNOWN or segment.agency is not Agency.UNKNOWN
            for segment in self.segments
        )


@dataclass(frozen=True)
class ReconstructionProgress:
    window_days: int
    reconstructed_day_count: int
    required_reconstructed_day_count: int
    shortfall: int


@dataclass(frozen=True)
class DailyLifeWindow:
    """A requested run of completed logical days plus the current partial day."""

    as_of: datetime
    completed_days: tuple[DailyLifeSummary, ...]
    partial_day: DailyLifeSummary
    reconstruction: ReconstructionProgress

    @property
    def yesterday(self) -> DailyLifeSummary:
        return self.completed_days[-1]

    @property
    def current_day(self) -> DailyLifeSummary:
        return self.partial_day


CurrentDailyLife = DailyLifeWindow


@dataclass(frozen=True)
class HowISpendMyDays:
    """Deterministic current answer-card data without narrative inference."""

    window: DailyLifeWindow
    coverage_seconds: float
    unknown_gap_seconds: float
    agency_seconds: AgencySeconds
    activity_seconds: tuple[tuple[Activity, float], ...]
    wake_times: tuple[datetime, ...]
    wake_to_first_purposeful_direct_action_seconds: tuple[float, ...]
    day_type_inputs: tuple[DayTypeInputs, ...]

    def seconds_for_activity(self, activity: Activity) -> float:
        return dict(self.activity_seconds).get(activity, 0.0)


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
    sleep_ends = sorted(
        (event for event in events if event.activity is Activity.SLEEP and logical_date(event.end) == day),
        key=lambda event: event.end,
    )
    if len(sleep_ends) == 1:
        selected = sleep_ends[0]
        return DailyBoundary(day, selected.end, BoundaryMethod.SLEEP, selected.source, 1.0, None, selected.evidence_refs)

    activity_starts = sorted(
        (
            event
            for event in events
            if event.activity is not Activity.SLEEP
            and event.agency in {Agency.DIRECT_OPERATOR, Agency.ACTIVE_SUPERVISION_OR_REVIEW}
            and logical_date(event.start) == day
            and event.start < canonical + timedelta(hours=12)
        ),
        key=lambda event: event.start,
    )
    sleep_ambiguity = "multiple_sleep_wake_candidates" if sleep_ends else "sleep_unavailable"
    if activity_starts:
        selected = activity_starts[0]
        return DailyBoundary(
            day,
            selected.start,
            BoundaryMethod.ACTIVITY,
            selected.source,
            0.6 if not sleep_ends else 0.4,
            sleep_ambiguity,
            selected.evidence_refs,
        )

    return DailyBoundary(day, canonical, BoundaryMethod.CANONICAL, None, 0.25, sleep_ambiguity, ())


def _daily_boundaries(start: date, end: date, events: tuple[EvidenceEvent, ...]) -> dict[date, DailyBoundary]:
    boundaries: dict[date, DailyBoundary] = {}
    cursor = start
    while cursor <= end + timedelta(days=1):
        boundaries[cursor] = _boundary_for_day(cursor, events)
        cursor += timedelta(days=1)
    return boundaries


def _classify_agency(components: tuple[Agency, ...]) -> Agency:
    meaningful = set(components) - {Agency.UNKNOWN}
    if Agency.MIXED in meaningful or len(meaningful) > 1:
        return Agency.MIXED
    return next(iter(meaningful), Agency.UNKNOWN)


def _classify_activity(components: tuple[Activity, ...]) -> Activity:
    meaningful = set(components) - {Activity.UNKNOWN}
    return next(iter(meaningful), Activity.UNKNOWN) if len(meaningful) == 1 else Activity.UNKNOWN


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
        if clipped := _clip(event.start, event.end, start, end):
            points.update(clipped)
    for interval in relevant_coverage:
        if clipped := _clip(interval.start, interval.end, start, end):
            points.update(clipped)

    ordered_points = sorted(points)
    segments: list[DailySegment] = []
    for segment_start, segment_end in zip(ordered_points, ordered_points[1:]):
        active_events = tuple(event for event in relevant_events if _overlaps(segment_start, segment_end, event.start, event.end))
        active_coverage = tuple(item for item in relevant_coverage if _overlaps(segment_start, segment_end, item.start, item.end))
        component_activities = tuple(sorted({event.activity for event in active_events}, key=str))
        component_agencies = tuple(sorted({event.agency for event in active_events}, key=str))
        segments.append(
            DailySegment(
                segment_start,
                segment_end,
                _classify_activity(component_activities),
                _classify_agency(component_agencies),
                component_activities,
                component_agencies,
                tuple(sorted({item.source for item in active_coverage})),
                _ordered_refs(ref for item in (*active_events, *active_coverage) for ref in item.evidence_refs),
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
        activity_totals[segment.activity] += seconds
        if segment.activity is not Activity.SLEEP:
            agency_totals[segment.agency] += seconds
            if segment.agency in {Agency.DIRECT_OPERATOR, Agency.ACTIVE_SUPERVISION_OR_REVIEW}:
                operator_attention_total += seconds

    purposeful = sorted(
        (
            event
            for event in events
            if event.agency is Agency.DIRECT_OPERATOR
            and event.purposeful
            and event.activity is not Activity.SLEEP
            and start_boundary.at <= event.start < end
        ),
        key=lambda event: event.start,
    )
    first_action = (
        EvidenceAnchor(purposeful[0].activity.value, purposeful[0].start, purposeful[0].evidence_refs) if purposeful else None
    )
    wake_time = start_boundary.at if start_boundary.method is BoundaryMethod.SLEEP else None
    wake_latency = (first_action.at - wake_time).total_seconds() if wake_time is not None and first_action is not None else None
    anchors = tuple(
        sorted(
            (
                EvidenceAnchor(event.external_anchor or "", max(event.start, start_boundary.at), event.evidence_refs)
                for event in events
                if event.external_anchor and _overlaps(start_boundary.at, end, event.start, event.end)
            ),
            key=lambda anchor: (anchor.at, anchor.label),
        )
    )
    duration = max((end - start_boundary.at).total_seconds(), 0.0)
    merged_unknown_gaps = _merge_adjacent(unknown_gaps)
    unknown_total = sum((gap_end - gap_start).total_seconds() for gap_start, gap_end in merged_unknown_gaps)
    refs = _ordered_refs(ref for segment in segments for ref in segment.evidence_refs)
    ambiguous_refs = _ordered_refs(
        ref
        for segment in segments
        if segment.agency is Agency.MIXED or len(set(segment.component_activities) - {Activity.UNKNOWN}) > 1
        for ref in segment.evidence_refs
    )
    ratio = lambda value: value / duration if duration else 0.0
    inputs = DayTypeInputs(
        ratio(coverage_total),
        ratio(unknown_total),
        tuple((agency, ratio(agency_totals[agency])) for agency in Agency),
        ratio(activity_totals[Activity.SLEEP]),
        len(anchors),
        first_action is not None,
        refs,
        ambiguous_refs,
    )
    return DailyLifeSummary(
        day,
        start_boundary.at,
        end,
        status,
        start_boundary,
        end_boundary,
        coverage_total,
        unknown_total,
        AgencySeconds(**{agency.value: agency_totals[agency] for agency in Agency}),
        operator_attention_total,
        tuple((activity, seconds) for activity, seconds in activity_totals.items() if seconds > 0),
        wake_time,
        first_action,
        wake_latency,
        anchors,
        tuple(
            SourceCoverage(source, seconds, tuple(sorted(source_refs.get(source, set()))))
            for source, seconds in sorted(source_seconds.items())
        ),
        merged_unknown_gaps,
        refs,
        segments,
        inputs,
    )


def reconstruct_daily_life(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    start: date,
    end: date,
    as_of: datetime,
) -> tuple[DailyLifeSummary, ...]:
    """Reconstruct inclusive logical days without filling coverage gaps."""

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


def reconstruct_daily_life_window(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    as_of: datetime,
    completed_days: int,
    required_reconstructed_days: int = 0,
) -> DailyLifeWindow:
    """Request completed logical days plus the current partial logical day."""

    if completed_days < 1:
        raise ValueError("completed_days must be positive")
    if required_reconstructed_days < 0:
        raise ValueError("required_reconstructed_days must not be negative")
    local_as_of = as_local(as_of)
    current_day = logical_date(local_as_of)
    summaries = reconstruct_daily_life(
        events,
        coverage,
        start=current_day - timedelta(days=completed_days),
        end=current_day,
        as_of=local_as_of,
    )
    complete = tuple(summary for summary in summaries[:-1] if summary.status is DayStatus.COMPLETE)
    reconstructed_count = sum(summary.is_reconstructed for summary in complete)
    return DailyLifeWindow(
        local_as_of,
        complete,
        summaries[-1],
        ReconstructionProgress(
            completed_days,
            reconstructed_count,
            required_reconstructed_days,
            max(required_reconstructed_days - reconstructed_count, 0),
        ),
    )


def reconstruct_current_daily_life(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    as_of: datetime,
) -> CurrentDailyLife:
    """Return the latest 28 completed days, current partial day, and 21-day QA."""

    return reconstruct_daily_life_window(
        events,
        coverage,
        as_of=as_of,
        completed_days=28,
        required_reconstructed_days=21,
    )


def reconstruct_90_day_daily_life(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    as_of: datetime,
) -> DailyLifeWindow:
    """Return a directly requestable 90-completed-day window plus partial day."""

    return reconstruct_daily_life_window(events, coverage, as_of=as_of, completed_days=90)


def what_did_i_do(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    day: date,
    as_of: datetime,
) -> DailyLifeSummary:
    """Return deterministic evidence data for one requested logical day."""

    return reconstruct_daily_life(events, coverage, start=day, end=day, as_of=as_of)[0]


def how_i_spend_my_days(
    events: Iterable[EvidenceEvent],
    coverage: Iterable[CoverageInterval],
    *,
    as_of: datetime,
) -> HowISpendMyDays:
    """Return current answer-card aggregates without narrative inference."""

    window = reconstruct_current_daily_life(events, coverage, as_of=as_of)
    agency_totals = {agency: 0.0 for agency in Agency}
    activity_totals = {activity: 0.0 for activity in Activity}
    for summary in window.completed_days:
        for agency in Agency:
            agency_totals[agency] += summary.agency_seconds.for_agency(agency)
        for activity, seconds in summary.activity_seconds:
            activity_totals[activity] += seconds
    return HowISpendMyDays(
        window,
        sum(summary.coverage_seconds for summary in window.completed_days),
        sum(summary.unknown_gap_seconds for summary in window.completed_days),
        AgencySeconds(**{agency.value: agency_totals[agency] for agency in Agency}),
        tuple((activity, seconds) for activity, seconds in activity_totals.items() if seconds > 0),
        tuple(summary.wake_time for summary in window.completed_days if summary.wake_time is not None),
        tuple(
            summary.wake_to_first_purposeful_direct_action_seconds
            for summary in window.completed_days
            if summary.wake_to_first_purposeful_direct_action_seconds is not None
        ),
        tuple(summary.day_type_inputs for summary in window.completed_days),
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
    "DailyLifeWindow",
    "DailySegment",
    "DayStatus",
    "DayTypeInputs",
    "EvidenceAnchor",
    "EvidenceEvent",
    "HowISpendMyDays",
    "ReconstructionProgress",
    "SourceCoverage",
    "how_i_spend_my_days",
    "reconstruct_90_day_daily_life",
    "reconstruct_current_daily_life",
    "reconstruct_daily_life",
    "reconstruct_daily_life_window",
    "what_did_i_do",
]
