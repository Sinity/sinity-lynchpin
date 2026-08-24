from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lynchpin.personal_evidence.daily_life import (
    Activity,
    Agency,
    BoundaryMethod,
    CoverageInterval,
    DayStatus,
    EvidenceEvent,
    reconstruct_current_daily_life,
    reconstruct_daily_life,
)


UTC = timezone.utc


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC)


def event(
    start: datetime,
    end: datetime,
    activity: Activity,
    agency: Agency,
    *,
    source: str = "fixture",
    refs: tuple[str, ...] = (),
    purposeful: bool = False,
    anchor: str | None = None,
    maintenance_evidenced: bool = False,
) -> EvidenceEvent:
    return EvidenceEvent(
        start=start,
        end=end,
        activity=activity,
        agency=agency,
        source=source,
        evidence_refs=refs,
        purposeful=purposeful,
        external_anchor=anchor,
        maintenance_evidenced=maintenance_evidenced,
    )


def test_sleep_boundaries_keep_21_of_28_hours_of_coverage() -> None:
    events = [
        event(dt(21, 0), dt(21, 8), Activity.SLEEP, Agency.AUTOMATED, refs=("sleep-21",)),
        event(dt(21, 9), dt(21, 10), Activity.WORK, Agency.DIRECT, refs=("work-1",), purposeful=True),
        event(dt(21, 11), dt(21, 12), Activity.WORK, Agency.DIRECT, refs=("work-2",), anchor="meeting"),
        event(dt(22, 2), dt(22, 12), Activity.SLEEP, Agency.AUTOMATED, refs=("sleep-22",)),
    ]
    coverage = [
        CoverageInterval(dt(21, 8), dt(21, 20), "activity", ("coverage-a",)),
        CoverageInterval(dt(21, 23), dt(22, 8), "activity", ("coverage-b",)),
    ]

    answer = reconstruct_current_daily_life(events, coverage, as_of=dt(22, 13))

    yesterday = answer.yesterday
    assert yesterday.start == dt(21, 8)
    assert yesterday.end == dt(22, 12)
    assert yesterday.start_boundary.method is BoundaryMethod.SLEEP
    assert yesterday.coverage_seconds == pytest.approx(21 * 3600)
    assert yesterday.day_type_inputs.coverage_ratio == pytest.approx(21 / 28)
    assert yesterday.first_purposeful_direct_action is not None
    assert yesterday.first_purposeful_direct_action.evidence_refs == ("work-1",)
    assert yesterday.external_anchors[0].label == "meeting"


def test_current_partial_day_is_not_in_complete_days() -> None:
    answer = reconstruct_current_daily_life(
        [
            event(dt(22, 23), dt(23, 7), Activity.SLEEP, Agency.AUTOMATED),
            event(dt(23, 8), dt(23, 9), Activity.WORK, Agency.DIRECT),
        ],
        [CoverageInterval(dt(22, 6), dt(23, 12), "activity")],
        as_of=dt(23, 10),
    )

    assert answer.yesterday.logical_day == date(2026, 8, 22)
    assert answer.yesterday.status is DayStatus.COMPLETE
    assert answer.current_day.logical_day == date(2026, 8, 23)
    assert answer.current_day.status is DayStatus.PARTIAL
    assert answer.complete_days == (answer.yesterday,)


def test_unknown_coverage_is_preserved_as_gaps_not_absence() -> None:
    summary = reconstruct_daily_life(
        [
            event(dt(22, 0), dt(22, 4), Activity.SLEEP, Agency.AUTOMATED),
            event(dt(22, 8), dt(22, 9), Activity.WORK, Agency.DIRECT, refs=("work",)),
        ],
        [CoverageInterval(dt(22, 4), dt(22, 6), "activity", ("coverage",))],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 6),
    )[0]

    assert summary.coverage_seconds == pytest.approx(2 * 3600)
    assert summary.unknown_gap_seconds == pytest.approx(22 * 3600)
    assert summary.unknown_gaps == ((dt(22, 6), dt(23, 4)),)
    assert summary.operator_attention_seconds == pytest.approx(3600)
    assert summary.evidence_refs == ("coverage", "work")


def test_agency_overlap_does_not_turn_agents_or_processes_into_attention() -> None:
    summary = reconstruct_daily_life(
        [
            event(dt(22, 0), dt(22, 4), Activity.SLEEP, Agency.AUTOMATED),
            event(dt(22, 8), dt(22, 10), Activity.WORK, Agency.DIRECT, refs=("direct",), purposeful=True),
            event(dt(22, 8), dt(22, 10), Activity.WORK, Agency.SUPERVISORY, refs=("supervise",)),
            event(dt(22, 8), dt(22, 12), Activity.WORK, Agency.DELEGATED, refs=("agent-output",)),
            event(dt(22, 8), dt(22, 12), Activity.MAINTENANCE, Agency.AUTOMATED, refs=("process",)),
            event(dt(22, 12), dt(22, 13), Activity.MAINTENANCE, Agency.DIRECT, maintenance_evidenced=True),
        ],
        [CoverageInterval(dt(22, 6), dt(23, 6), "activity")],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 6),
    )[0]

    assert summary.agency_seconds.for_agency(Agency.DIRECT) == pytest.approx(3 * 3600)
    assert summary.agency_seconds.for_agency(Agency.SUPERVISORY) == pytest.approx(2 * 3600)
    assert summary.agency_seconds.for_agency(Agency.DELEGATED) == pytest.approx(4 * 3600)
    assert summary.agency_seconds.for_agency(Agency.AUTOMATED) == pytest.approx(4 * 3600)
    assert summary.operator_attention_seconds == pytest.approx(3 * 3600)
    assert summary.maintenance_seconds == pytest.approx(3600)
    assert summary.seconds_for_activity(Activity.UNKNOWN) == pytest.approx(4 * 3600)
