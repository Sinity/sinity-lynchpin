from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from lynchpin.personal_evidence.daily_life import (
    Activity,
    Agency,
    BoundaryMethod,
    CoverageInterval,
    DayStatus,
    EvidenceEvent,
    how_i_spend_my_days,
    reconstruct_90_day_daily_life,
    reconstruct_current_daily_life,
    reconstruct_daily_life,
    what_did_i_do,
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
    )


def test_exact_daily_life_vocabularies_are_constructible() -> None:
    agency_values = {
        "direct_operator",
        "active_supervision_or_review",
        "delegated_agent",
        "automated_system",
        "passive_or_consumptive",
        "offline_observed",
        "mixed",
        "unknown",
    }
    activity_values = {
        "sleep",
        "project_work",
        "project_supervision",
        "communication",
        "therapy_or_health",
        "administration",
        "education_or_research",
        "media_or_reading",
        "social",
        "mobility_or_outside",
        "domestic_or_self_maintenance",
        "substance_or_medication_event",
        "rest_or_low_observed_activity",
        "unknown",
    }

    assert {member.value for member in Agency} == agency_values
    assert {Agency(value).value for value in agency_values} == agency_values
    assert {member.value for member in Activity} == activity_values
    assert {Activity(value).value for value in activity_values} == activity_values


def test_mixed_overlap_is_one_segment_classification_without_ratio_double_counting() -> None:
    summary = reconstruct_daily_life(
        [
            event(dt(22, 8), dt(22, 10), Activity.PROJECT_WORK, Agency.DIRECT_OPERATOR, refs=("direct",)),
            event(
                dt(22, 8),
                dt(22, 10),
                Activity.PROJECT_SUPERVISION,
                Agency.ACTIVE_SUPERVISION_OR_REVIEW,
                refs=("review",),
            ),
            event(dt(22, 8), dt(22, 10), Activity.PROJECT_WORK, Agency.DELEGATED_AGENT, refs=("agent",)),
        ],
        [CoverageInterval(dt(22, 6), dt(23, 6), "activity")],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 7),
    )[0]

    segment = next(segment for segment in summary.segments if segment.start == dt(22, 8))
    assert segment.agency is Agency.MIXED
    assert segment.component_agencies == (
        Agency.ACTIVE_SUPERVISION_OR_REVIEW,
        Agency.DELEGATED_AGENT,
        Agency.DIRECT_OPERATOR,
    )
    assert summary.agency_seconds.for_agency(Agency.MIXED) == pytest.approx(2 * 3600)
    assert sum(summary.agency_seconds.for_agency(agency) for agency in Agency) == pytest.approx(
        (summary.end - summary.start).total_seconds()
    )
    assert summary.operator_attention_seconds == 0
    assert summary.day_type_inputs.counterexample_candidate_refs == ("agent", "direct", "review")


def test_passive_offline_and_unknown_are_distinct_and_not_attention() -> None:
    summary = reconstruct_daily_life(
        [
            event(dt(22, 8), dt(22, 9), Activity.MEDIA_OR_READING, Agency.PASSIVE_OR_CONSUMPTIVE),
            event(dt(22, 9), dt(22, 10), Activity.MOBILITY_OR_OUTSIDE, Agency.OFFLINE_OBSERVED),
        ],
        [CoverageInterval(dt(22, 8), dt(22, 11), "activity")],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 7),
    )[0]

    assert summary.agency_seconds.for_agency(Agency.PASSIVE_OR_CONSUMPTIVE) == pytest.approx(3600)
    assert summary.agency_seconds.for_agency(Agency.OFFLINE_OBSERVED) == pytest.approx(3600)
    assert summary.agency_seconds.for_agency(Agency.UNKNOWN) == pytest.approx(22 * 3600)
    assert summary.operator_attention_seconds == 0
    assert summary.seconds_for_activity(Activity.UNKNOWN) == pytest.approx(22 * 3600)
    assert summary.unknown_gap_seconds == pytest.approx(21 * 3600)


def test_sleep_boundary_wake_latency_and_boundary_evidence_are_explicit() -> None:
    summary = reconstruct_daily_life(
        [
            event(dt(22, 0), dt(22, 8), Activity.SLEEP, Agency.UNKNOWN, source="wearable", refs=("sleep",)),
            event(
                dt(22, 9),
                dt(22, 10),
                Activity.PROJECT_WORK,
                Agency.DIRECT_OPERATOR,
                refs=("prompt",),
                purposeful=True,
                anchor="meeting",
            ),
            event(dt(23, 0), dt(23, 8), Activity.SLEEP, Agency.UNKNOWN, source="wearable", refs=("sleep-next",)),
        ],
        [CoverageInterval(dt(22, 8), dt(23, 8), "activity")],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 9),
    )[0]

    assert summary.start == dt(22, 8)
    assert summary.start_boundary.method is BoundaryMethod.SLEEP
    assert summary.start_boundary.source == "wearable"
    assert summary.start_boundary.confidence == 1.0
    assert summary.start_boundary.ambiguity is None
    assert summary.wake_time == dt(22, 8)
    assert summary.wake_to_first_purposeful_direct_action_seconds == pytest.approx(3600)
    assert summary.first_purposeful_direct_action is not None
    assert summary.first_purposeful_direct_action.evidence_refs == ("prompt",)


def test_missing_sleep_keeps_wake_metrics_unknown_and_records_boundary_ambiguity() -> None:
    summary = reconstruct_daily_life(
        [event(dt(22, 7), dt(22, 8), Activity.PROJECT_WORK, Agency.DIRECT_OPERATOR, purposeful=True)],
        [CoverageInterval(dt(22, 6), dt(23, 6), "activity")],
        start=date(2026, 8, 22),
        end=date(2026, 8, 22),
        as_of=dt(23, 7),
    )[0]

    assert summary.start_boundary.method is BoundaryMethod.ACTIVITY
    assert summary.start_boundary.ambiguity == "sleep_unavailable"
    assert summary.wake_time is None
    assert summary.wake_to_first_purposeful_direct_action_seconds is None


def test_current_window_has_28_completed_days_partial_day_and_explicit_shortfall() -> None:
    as_of = dt(31, 12)
    current_day = date(2026, 8, 31)
    events = [
        event(
            datetime.combine(current_day - timedelta(days=offset), datetime.min.time(), tzinfo=UTC).replace(hour=8),
            datetime.combine(current_day - timedelta(days=offset), datetime.min.time(), tzinfo=UTC).replace(hour=9),
            Activity.PROJECT_WORK,
            Agency.DIRECT_OPERATOR,
            refs=(f"day-{offset}",),
        )
        for offset in range(1, 21)
    ]
    answer = reconstruct_current_daily_life(events, [], as_of=as_of)

    assert len(answer.completed_days) == 28
    assert answer.partial_day.status is DayStatus.PARTIAL
    assert answer.reconstruction.window_days == 28
    assert answer.reconstruction.reconstructed_day_count == 20
    assert answer.reconstruction.required_reconstructed_day_count == 21
    assert answer.reconstruction.shortfall == 1


def test_90_day_window_and_date_lookup_are_direct_and_deterministic() -> None:
    as_of = datetime(2026, 11, 30, 12, tzinfo=UTC)
    target = date(2026, 8, 31)
    observed = event(
        datetime(2026, 8, 31, 8, tzinfo=UTC),
        datetime(2026, 8, 31, 9, tzinfo=UTC),
        Activity.COMMUNICATION,
        Agency.DIRECT_OPERATOR,
        refs=("message",),
    )
    window = reconstruct_90_day_daily_life([observed], [], as_of=as_of)
    lookup = what_did_i_do([observed], [], day=target, as_of=as_of)

    assert len(window.completed_days) == 90
    assert window.partial_day.status is DayStatus.PARTIAL
    assert lookup.logical_day == target
    assert lookup.seconds_for_activity(Activity.COMMUNICATION) == pytest.approx(3600)


def test_how_i_spend_my_days_returns_data_not_narrative_and_excludes_partial_day() -> None:
    answer = how_i_spend_my_days(
        [
            event(dt(30, 8), dt(30, 10), Activity.PROJECT_WORK, Agency.DIRECT_OPERATOR),
            event(dt(31, 8), dt(31, 10), Activity.MEDIA_OR_READING, Agency.PASSIVE_OR_CONSUMPTIVE),
        ],
        [],
        as_of=dt(31, 12),
    )

    assert answer.window.partial_day.logical_day == date(2026, 8, 31)
    assert answer.seconds_for_activity(Activity.PROJECT_WORK) == pytest.approx(2 * 3600)
    assert answer.seconds_for_activity(Activity.MEDIA_OR_READING) == 0
    assert answer.agency_seconds.for_agency(Agency.DIRECT_OPERATOR) == pytest.approx(2 * 3600)


def test_current_partial_day_clips_an_ongoing_event_at_as_of() -> None:
    summary = what_did_i_do(
        [event(dt(31, 8), dt(31, 14), Activity.PROJECT_WORK, Agency.DIRECT_OPERATOR)],
        [CoverageInterval(dt(31, 6), dt(31, 18), "activity")],
        day=date(2026, 8, 31),
        as_of=dt(31, 12),
    )

    assert summary.status is DayStatus.PARTIAL
    assert summary.end == dt(31, 12)
    assert summary.seconds_for_activity(Activity.PROJECT_WORK) == pytest.approx(4 * 3600)
