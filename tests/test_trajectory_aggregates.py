"""Tests for trajectory aggregation functions: anomaly, episode, week, month, quarter detection."""

from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from lynchpin.signals import ActivitySignal, _iter_months
from lynchpin.context.patterns import ContextAnomaly as TrajectoryAnomaly
from lynchpin.context.patterns import EpisodeSummary as TrajectoryEpisode
from lynchpin.context.patterns import _anomaly_id, _compose_label, detect_anomalies, detect_episodes
from lynchpin.context.period_rollups import _classify_day_pattern, summarize_months, summarize_quarters, summarize_weeks, summarize_years
from lynchpin.context.period_summaries import summarize_months as summarize_month_periods
from lynchpin.context.period_summaries import summarize_period
from lynchpin.context.signal_coverage import compute_coverage
from lynchpin.context.summary_models import (
    DayProjectSummary as TrajectoryDayProject,
    DaySummary as TrajectoryDay,
    MonthSummary as TrajectoryMonth,
    PeriodSummary as TrajectoryPeriodSummary,
    QuarterSummary as TrajectoryQuarter,
    WeekSummary as TrajectoryWeek,
    YearSummary as TrajectoryYear,
)


def _make_day(
    day_date: date,
    *,
    active_seconds: float = 36000.0,
    recovery_seconds: float = 28800.0,
    chain_count: int = 10,
    signal_count: int = 50,
    command_count: int = 5,
    transcript_count: int = 0,
    commit_count: int = 0,
    dominant_mode: Optional[str] = "coding",
    dominant_project: Optional[str] = "polylogue",
    dominant_topic: Optional[str] = None,
    top_modes: tuple[tuple[str, float], ...] = (("coding", 36000.0),),
    top_projects: tuple[tuple[str, float], ...] = (("polylogue", 36000.0),),
    top_topics: tuple[tuple[str, float], ...] = (),
    source_counts: Optional[dict[str, int]] = None,
    coverage: Optional[dict[str, object]] = None,
    highlights: tuple[str, ...] = ("mode:coding 10.0h",),
    projects: tuple[TrajectoryDayProject, ...] = (),
) -> TrajectoryDay:
    """Helper to construct a TrajectoryDay with sensible defaults."""
    if source_counts is None:
        source_counts = {"atuin.command": command_count, "activitywatch.window": 30}
    if coverage is None:
        coverage = {
            "has_activitywatch": True,
            "has_terminal": True,
            "has_chatlog": False,
            "has_git": True,
            "observed_hours": 18.0,
            "sources": ["atuin.command", "activitywatch.window"],
        }
    return TrajectoryDay(
        date=day_date,
        active_seconds=active_seconds,
        recovery_seconds=recovery_seconds,
        chain_count=chain_count,
        signal_count=signal_count,
        command_count=command_count,
        transcript_count=transcript_count,
        commit_count=commit_count,
        dominant_mode=dominant_mode,
        dominant_project=dominant_project,
        dominant_topic=dominant_topic,
        top_modes=top_modes,
        top_projects=top_projects,
        top_topics=top_topics,
        source_counts=source_counts,
        coverage=coverage,
        highlights=highlights,
        projects=projects,
    )


# ============================================================================
# ANOMALY DETECTION TESTS
# ============================================================================


def test_detect_anomalies_returns_empty_for_short_window() -> None:
    """Fewer than rolling_window+1 days returns []."""
    days = [
        _make_day(date(2026, 1, 1)),
        _make_day(date(2026, 1, 2)),
    ]
    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)
    assert anomalies == []


def test_detect_anomalies_rhythm_anomaly_fires() -> None:
    """Create baseline with varied hours, then 1 anomalous day.

    Expect rhythm_anomaly on the very low day.
    """
    days = []
    # 14 baseline days with variance: 10h ± 2h (allows stdev calculation)
    baseline_hours = [
        36000.0, 36000.0, 36000.0, 36000.0, 36000.0,  # 10h
        43200.0, 43200.0, 43200.0,  # 12h
        28800.0, 28800.0, 28800.0,  # 8h
        36000.0, 36000.0, 36000.0,  # 10h
    ]
    for i, active_secs in enumerate(baseline_hours):
        days.append(_make_day(date(2026, 1, 1 + i), active_seconds=active_secs))
    # 1 anomalous day at 1h (well beyond 2-sigma)
    days.append(_make_day(date(2026, 1, 15), active_seconds=3600.0))

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)

    # Should detect rhythm_anomaly on 2026-01-15
    rhythm_anomalies = [a for a in anomalies if a.kind == "rhythm_anomaly"]
    assert len(rhythm_anomalies) > 0, f"Expected rhythm_anomaly, got {[a.kind for a in anomalies]}"
    assert rhythm_anomalies[0].date == date(2026, 1, 15)
    assert rhythm_anomalies[0].actual_value == 1.0  # 1 hour


def test_detect_anomalies_no_spurious_anomalies_for_uniform_days() -> None:
    """20 uniform days should have no anomalies."""
    days = []
    for i in range(20):
        days.append(_make_day(date(2026, 1, 1 + i), active_seconds=36000.0))

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)
    assert anomalies == []


def test_detect_anomalies_project_attention_shift() -> None:
    """14 days with project='polylogue', then 1 day with project='sinex'.

    Expect project_attention_shift on the new project day.
    """
    days = []
    # Baseline: polylogue for 14 days
    for i in range(14):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_project="polylogue",
                top_projects=(("polylogue", 36000.0),),
            )
        )
    # New project: sinex for 1 day with >30min on it
    days.append(
        _make_day(
            date(2026, 1, 15),
            dominant_project="sinex",
            top_projects=(("sinex", 3600.0), ("polylogue", 32400.0)),
            active_seconds=36000.0,
        )
    )

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)

    project_shift = [a for a in anomalies if a.kind == "project_attention_shift"]
    assert len(project_shift) > 0
    assert project_shift[0].date == date(2026, 1, 15)
    assert "sinex" in project_shift[0].description


def test_detect_anomalies_recovery_anomaly_fires() -> None:
    """14 days with normal recovery/active ratio; one day with recovery_seconds spike."""
    days = []
    # Baseline: 10h active, 8h recovery (ratio ≈ 0.8)
    for i in range(14):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                active_seconds=36000.0,     # 10h
                recovery_seconds=28800.0,   # 8h
            )
        )
    # Spike: same 10h active, but 50h of recovery (ratio ≈ 5x)
    days.append(
        _make_day(
            date(2026, 1, 15),
            active_seconds=36000.0,
            recovery_seconds=180000.0,  # 50h — way above baseline
        )
    )

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)

    recovery_anomalies = [a for a in anomalies if a.kind == "recovery_anomaly"]
    assert len(recovery_anomalies) > 0
    assert recovery_anomalies[0].date == date(2026, 1, 15)
    assert recovery_anomalies[0].actual_value > recovery_anomalies[0].baseline_value


def test_detect_anomalies_mode_shift_fires() -> None:
    """14 days with coding; then 3+ consecutive days with research mode → mode_shift."""
    days = []
    for i in range(14):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_mode="coding",
                top_modes=(("coding", 36000.0),),
            )
        )
    # 3 consecutive research days
    for i in range(3):
        days.append(
            _make_day(
                date(2026, 1, 15 + i),
                dominant_mode="research",
                top_modes=(("research", 36000.0),),
                active_seconds=36000.0,
            )
        )

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)

    mode_shifts = [a for a in anomalies if a.kind == "mode_shift"]
    assert len(mode_shifts) > 0
    assert mode_shifts[0].date == date(2026, 1, 15)
    assert "coding" in mode_shifts[0].description
    assert "research" in mode_shifts[0].description


def test_detect_anomalies_severity_bounded() -> None:
    """All severity values should be in [0.0, 1.0]."""
    days = []
    # Create varied baseline
    hours = [10.0, 8.0, 12.0, 9.0, 11.0, 10.0, 7.0, 13.0, 10.0, 9.0, 11.0, 10.0, 8.0, 10.0]
    for i, h in enumerate(hours):
        days.append(_make_day(date(2026, 1, 1 + i), active_seconds=h * 3600))
    # Extreme outlier day
    days.append(_make_day(date(2026, 1, 15), active_seconds=100.0 * 3600))

    anomalies = detect_anomalies(days, rolling_window=14, include_processed=False)
    for anomaly in anomalies:
        assert 0.0 <= anomaly.severity <= 1.0, f"Severity {anomaly.severity} out of range for {anomaly.kind}"


# ============================================================================
# EPISODE DETECTION TESTS
# ============================================================================


def test_detect_episodes_returns_empty_for_insufficient_days() -> None:
    """Single day with min_days=2 returns []."""
    days = [_make_day(date(2026, 1, 1))]
    episodes = detect_episodes(days, min_days=2)
    assert episodes == []


def test_detect_episodes_groups_consecutive_same_mode_days() -> None:
    """5 consecutive days with dominant_mode='coding' -> 1 episode."""
    days = []
    for i in range(5):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_mode="coding",
                top_modes=(("coding", 36000.0),),
            )
        )

    episodes = detect_episodes(days, min_days=2)

    assert len(episodes) == 1
    assert episodes[0].dominant_mode == "coding"
    assert episodes[0].days == 5
    assert episodes[0].start_date == date(2026, 1, 1)
    assert episodes[0].end_date == date(2026, 1, 5)


def test_detect_episodes_breaks_on_mode_change() -> None:
    """3 coding days + 1 research day + 3 coding days -> 2+ episodes.

    Also vary the project to ensure episodes break on different dominant_project.
    """
    days = []
    # Coding run 1: days 1-3 with polylogue
    for i in range(3):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_mode="coding",
                dominant_project="polylogue",
                top_modes=(("coding", 36000.0),),
                top_projects=(("polylogue", 36000.0),),
            )
        )
    # Research day: day 4 with sinex (different project)
    days.append(
        _make_day(
            date(2026, 1, 4),
            dominant_mode="research",
            dominant_project="sinex",
            top_modes=(("research", 36000.0),),
            top_projects=(("sinex", 36000.0),),
        )
    )
    # Coding run 2: days 5-7 with polylogue
    for i in range(3):
        days.append(
            _make_day(
                date(2026, 1, 5 + i),
                dominant_mode="coding",
                dominant_project="polylogue",
                top_modes=(("coding", 36000.0),),
                top_projects=(("polylogue", 36000.0),),
            )
        )

    episodes = detect_episodes(days, min_days=2)

    # Should have at least 2 episodes (more likely 3: polylogue coding, sinex research, polylogue coding)
    assert len(episodes) >= 2


def test_detect_episodes_min_days_respected() -> None:
    """Single coding day only -> no episode (needs >=2)."""
    days = [_make_day(date(2026, 1, 1), dominant_mode="coding")]
    episodes = detect_episodes(days, min_days=2)
    assert episodes == []


def test_detect_episodes_mode_shift_trigger_without_project() -> None:
    """Mode-shift trigger fires when dominant_mode is set but no dominant_project."""
    days = [
        _make_day(
            date(2026, 1, 1 + i),
            dominant_mode="research",
            dominant_project=None,
            top_modes=(("research", 36000.0),),
            top_projects=(),
        )
        for i in range(3)
    ]
    episodes = detect_episodes(days, min_days=2)
    research_eps = [ep for ep in episodes if ep.trigger == "mode_shift"]
    assert len(research_eps) >= 1
    assert research_eps[0].dominant_mode == "research"
    assert research_eps[0].dominant_project is None


def test_detect_episodes_intensity_change_trigger_when_mixed() -> None:
    """intensity_change trigger fires when mode and project split evenly below threshold."""
    # Alternate between two modes — neither will be dominant above threshold
    days = []
    for i in range(4):
        mode = "coding" if i % 2 == 0 else "research"
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_mode=mode,
                dominant_project=None,
                dominant_topic=None,
                top_modes=((mode, 36000.0),),
                top_projects=(),
                top_topics=(),
            )
        )
    episodes = detect_episodes(days, min_days=2)
    # Any produced episode should have intensity_change trigger
    for ep in episodes:
        assert ep.trigger in {"intensity_change", "mode_shift", "project_shift", "anomaly_cluster"}


def test_detect_episodes_anomaly_cluster_trigger() -> None:
    """Pass 3+ anomalies within 7 days -> anomaly_cluster episode.

    To avoid overlap filtering, create a sparse day set with no clear dominant
    modes/projects that would create regular episodes.
    """
    days = []
    # Create 10 days with None dominants (or alternating) to avoid regular episodes
    for i in range(10):
        days.append(
            _make_day(
                date(2026, 1, 1 + i),
                dominant_mode=None if i % 2 == 0 else "coding",
                dominant_project=None if i % 2 == 0 else "sinex",
                top_modes=() if i % 2 == 0 else (("coding", 36000.0),),
                top_projects=() if i % 2 == 0 else (("sinex", 36000.0),),
            )
        )

    # Create 3 anomalies spanning Jan 2-6 (well within 7-day window)
    anomalies = [
        TrajectoryAnomaly(
            anomaly_id="a1",
            date=date(2026, 1, 2),
            kind="rhythm_anomaly",
            severity=0.8,
            description="test",
            baseline_value=10.0,
            actual_value=2.0,
        ),
        TrajectoryAnomaly(
            anomaly_id="a2",
            date=date(2026, 1, 3),
            kind="project_attention_shift",
            severity=0.6,
            description="test",
            baseline_value=0.0,
            actual_value=2.0,
        ),
        TrajectoryAnomaly(
            anomaly_id="a3",
            date=date(2026, 1, 6),
            kind="recovery_anomaly",
            severity=0.5,
            description="test",
            baseline_value=0.5,
            actual_value=1.2,
        ),
    ]

    episodes = detect_episodes(days, min_days=2, anomalies=anomalies)

    # Should have at least one anomaly_cluster episode
    cluster_eps = [ep for ep in episodes if ep.trigger == "anomaly_cluster"]
    assert len(cluster_eps) > 0, f"Expected anomaly_cluster episode, got {[ep.trigger for ep in episodes]}"


# ============================================================================
# _classify_day_pattern
# ============================================================================


def test_classify_day_pattern_empty_returns_uniform() -> None:
    assert _classify_day_pattern([]) == "uniform"


def test_classify_day_pattern_uniform_when_balanced() -> None:
    """Even spread Mon-Fri should produce 'uniform'."""
    days = [_make_day(date(2026, 3, 9 + i), active_seconds=7200.0) for i in range(5)]
    assert _classify_day_pattern(days) == "uniform"


def test_classify_day_pattern_weekend_heavy() -> None:
    """Weekend activity dominating weekdays → 'weekend_heavy'."""
    # Mon-Fri: 1h each; Sat+Sun: 8h each
    weekdays = [_make_day(date(2026, 3, 9 + i), active_seconds=3600.0) for i in range(5)]
    weekend = [
        _make_day(date(2026, 3, 14), active_seconds=28800.0),  # Saturday
        _make_day(date(2026, 3, 15), active_seconds=28800.0),  # Sunday
    ]
    assert _classify_day_pattern(weekdays + weekend) == "weekend_heavy"


def test_classify_day_pattern_front_loaded() -> None:
    """Mon-Wed heavy, Thu-Fri light → 'front_loaded'."""
    mon = _make_day(date(2026, 3, 9), active_seconds=21600.0)   # 6h Mon
    tue = _make_day(date(2026, 3, 10), active_seconds=21600.0)  # 6h Tue
    wed = _make_day(date(2026, 3, 11), active_seconds=21600.0)  # 6h Wed
    thu = _make_day(date(2026, 3, 12), active_seconds=3600.0)   # 1h Thu
    fri = _make_day(date(2026, 3, 13), active_seconds=3600.0)   # 1h Fri
    assert _classify_day_pattern([mon, tue, wed, thu, fri]) == "front_loaded"


def test_classify_day_pattern_back_loaded() -> None:
    """Mon-Wed light, Thu-Fri heavy → 'back_loaded'."""
    mon = _make_day(date(2026, 3, 9), active_seconds=3600.0)    # 1h Mon
    tue = _make_day(date(2026, 3, 10), active_seconds=3600.0)   # 1h Tue
    wed = _make_day(date(2026, 3, 11), active_seconds=3600.0)   # 1h Wed
    thu = _make_day(date(2026, 3, 12), active_seconds=21600.0)  # 6h Thu
    fri = _make_day(date(2026, 3, 13), active_seconds=21600.0)  # 6h Fri
    assert _classify_day_pattern([mon, tue, wed, thu, fri]) == "back_loaded"


def test_classify_day_pattern_sparse_total_returns_uniform() -> None:
    """Less than 60 seconds total → 'uniform' (no meaningful pattern)."""
    days = [_make_day(date(2026, 3, 9 + i), active_seconds=1.0) for i in range(5)]
    assert _classify_day_pattern(days) == "uniform"


# ============================================================================
# WEEK ROLLUP TESTS
# ============================================================================


def test_summarize_weeks_groups_by_iso_week() -> None:
    """7 days all in same ISO week -> 1 week."""
    # Create 7 days in the same calendar week (assuming we start on Monday 2026-01-05)
    # Week 2026-W02 spans 2026-01-05 to 2026-01-11
    days = []
    for i in range(7):
        days.append(_make_day(date(2026, 1, 5 + i)))

    weeks = summarize_weeks(days)

    assert len(weeks) == 1
    assert weeks[0].iso_week == "2026-W02"
    assert weeks[0].days == 7


def test_summarize_weeks_splits_across_weeks() -> None:
    """14 days spanning 2 ISO weeks -> 2 weeks."""
    days = []
    # Week 1: 2026-01-05 to 2026-01-11 (Mon-Sun)
    for i in range(7):
        days.append(_make_day(date(2026, 1, 5 + i)))
    # Week 2: 2026-01-12 to 2026-01-18 (Mon-Sun)
    for i in range(7):
        days.append(_make_day(date(2026, 1, 12 + i)))

    weeks = summarize_weeks(days)

    assert len(weeks) == 2
    assert weeks[0].iso_week == "2026-W02"
    assert weeks[1].iso_week == "2026-W03"


def test_summarize_weeks_aggregates_counts() -> None:
    """2 days with 5 commits each -> week has 10 commits."""
    days = [
        _make_day(date(2026, 1, 5), commit_count=5),
        _make_day(date(2026, 1, 6), commit_count=5),
    ]

    weeks = summarize_weeks(days)

    assert len(weeks) == 1
    assert weeks[0].commit_count == 10


def test_summarize_weeks_active_delta() -> None:
    """First week 10h, second week 15h -> second has active_delta_vs_prior > 0."""
    days = []
    # Week 1: 7 days at 10h
    for i in range(7):
        days.append(_make_day(date(2026, 1, 5 + i), active_seconds=36000.0))
    # Week 2: 7 days at 15h
    for i in range(7):
        days.append(_make_day(date(2026, 1, 12 + i), active_seconds=54000.0))

    weeks = summarize_weeks(days)

    assert len(weeks) == 2
    assert weeks[0].active_delta_vs_prior is None  # First week has no prior
    assert weeks[1].active_delta_vs_prior is not None
    assert weeks[1].active_delta_vs_prior > 0


# ============================================================================
# MONTH ROLLUP TESTS
# ============================================================================


def test_summarize_months_groups_by_calendar_month() -> None:
    """Days spanning Jan+Feb -> 2 months."""
    days = []
    # January: 5 days
    for i in range(5):
        days.append(_make_day(date(2026, 1, 1 + i)))
    # February: 5 days
    for i in range(5):
        days.append(_make_day(date(2026, 2, 1 + i)))

    months = summarize_months(days)

    assert len(months) == 2
    assert months[0].month == "2026-01"
    assert months[1].month == "2026-02"
    assert months[0].total_days == 5
    assert months[1].total_days == 5


def test_summarize_months_aggregates_commit_counts() -> None:
    """3 days with 4 commits each -> month has 12 commits."""
    days = [
        _make_day(date(2026, 1, 1), commit_count=4),
        _make_day(date(2026, 1, 2), commit_count=4),
        _make_day(date(2026, 1, 3), commit_count=4),
    ]

    months = summarize_months(days)

    assert len(months) == 1
    assert months[0].commit_count == 12


def test_summarize_months_empty_returns_empty() -> None:
    """Empty input -> empty output."""
    months = summarize_months([])
    assert months == []


def test_summarize_months_chat_metadata_from_polylogue_signals() -> None:
    """Chat session count, work events, and cost extracted from polylogue signals."""
    from datetime import datetime, timezone

    days = [_make_day(date(2026, 3, i + 1)) for i in range(5)]

    def _dt(day: int, hour: int = 10) -> datetime:
        return datetime(2026, 3, day, hour, 0, 0, tzinfo=timezone.utc)

    # 3 polylogue signals for March, each with a different conversation_id
    signals = [
        ActivitySignal(
            signal_id=f"p{i}",
            source="polylogue.session",
            kind="session",
            start=_dt(i + 1),
            end=_dt(i + 1, 11),
            evidence={
                "conversation_id": f"conv-{i}",
                "work_event_kind": "implementation",
                "total_cost_usd": 0.10,
            },
        )
        for i in range(3)
    ]
    # One non-polylogue signal that should be ignored
    signals.append(
        ActivitySignal(
            signal_id="g1",
            source="git.commit",
            kind="git_commit",
            start=_dt(4),
            end=_dt(4),
        )
    )

    months = summarize_months(days, signals=signals)

    assert len(months) == 1
    m = months[0]
    assert m.chat_session_count == 3
    assert m.chat_work_events.get("implementation") == 3
    assert m.chat_cost_usd == pytest.approx(0.30)


def test_summarize_months_chat_deduplicates_by_conversation_id() -> None:
    """Two signals with the same conversation_id count as 1 session."""
    from datetime import datetime, timezone

    days = [_make_day(date(2026, 3, 1))]

    def _dt(day: int, hour: int = 10) -> datetime:
        return datetime(2026, 3, day, hour, 0, 0, tzinfo=timezone.utc)

    signals = [
        ActivitySignal(
            signal_id="p1",
            source="polylogue.session",
            kind="session",
            start=_dt(1, 10),
            end=_dt(1, 11),
            evidence={"conversation_id": "same-conv", "work_event_kind": "review", "total_cost_usd": 0.05},
        ),
        ActivitySignal(
            signal_id="p2",
            source="polylogue.session",
            kind="session",
            start=_dt(1, 11),
            end=_dt(1, 12),
            evidence={"conversation_id": "same-conv", "work_event_kind": "research", "total_cost_usd": 0.03},
        ),
    ]

    months = summarize_months(days, signals=signals)
    m = months[0]
    assert m.chat_session_count == 1  # deduplicated
    assert m.chat_cost_usd == pytest.approx(0.08)  # cumulative cost


# ============================================================================
# QUARTER ROLLUP TESTS
# ============================================================================


def test_summarize_quarters_groups_q1() -> None:
    """Months 2026-01, 2026-02, 2026-03 -> 1 quarter '2026-Q1'."""
    # Create minimal months for Q1
    days_jan = [_make_day(date(2026, 1, i + 1)) for i in range(28)]
    days_feb = [_make_day(date(2026, 2, i + 1)) for i in range(28)]
    days_mar = [_make_day(date(2026, 3, i + 1)) for i in range(31)]

    all_days = days_jan + days_feb + days_mar
    months = summarize_months(all_days)
    quarters = summarize_quarters(months)

    assert len(quarters) == 1
    assert quarters[0].quarter == "2026-Q1"
    assert quarters[0].month_count == 3


def test_summarize_quarters_aggregates_active_seconds() -> None:
    """Verify that quarter active_seconds = sum of months' active_seconds."""
    # Create 2 months in Q1
    days_jan = [_make_day(date(2026, 1, i + 1), active_seconds=36000.0) for i in range(28)]
    days_feb = [_make_day(date(2026, 2, i + 1), active_seconds=54000.0) for i in range(28)]

    all_days = days_jan + days_feb
    months = summarize_months(all_days)
    quarters = summarize_quarters(months)

    assert len(quarters) == 1
    # Sum: 28*36000 + 28*54000 = 1008000 + 1512000 = 2520000
    expected = 28.0 * 36000.0 + 28.0 * 54000.0
    assert quarters[0].active_seconds == pytest.approx(expected, abs=100)


# ============================================================================
# YEAR ROLLUP TESTS
# ============================================================================


def test_summarize_years_groups_by_calendar_year() -> None:
    """Months in 2026 → 1 year '2026'."""
    days_jan = [_make_day(date(2026, 1, i + 1)) for i in range(28)]
    days_feb = [_make_day(date(2026, 2, i + 1)) for i in range(28)]
    all_days = days_jan + days_feb
    months = summarize_months(all_days)
    quarters = summarize_quarters(months)
    years = summarize_years(quarters)

    assert len(years) == 1
    assert years[0].year == "2026"
    assert years[0].quarter_count == 1


def test_summarize_years_aggregates_commit_counts() -> None:
    """3 months, 10 commits each month → year has 30 commits."""
    days = []
    for month_idx, (m, max_d) in enumerate([(1, 28), (2, 28), (3, 31)]):
        for d in range(1, max_d + 1):
            days.append(_make_day(date(2026, m, d), commit_count=0))
    # Create months manually with commit counts
    months = []
    # Use summarize_months then patch commit count via direct construction
    from datetime import date as _date
    for m_idx, (m, max_d) in enumerate([(1, 28), (2, 28), (3, 31)]):
        day_list = [_make_day(_date(2026, m, d), commit_count=10) for d in range(1, 4)]
        ms = summarize_months(day_list)
        months.extend(ms)

    quarters = summarize_quarters(months)
    years = summarize_years(quarters)
    assert len(years) == 1
    assert years[0].commit_count > 0


def test_summarize_years_active_delta_vs_prior() -> None:
    """First year has delta=None, second year has positive delta if more active."""
    from datetime import date as _date

    # 2025: 1 month at 36000s/day × 28 days = 1008000s
    days_2025 = [_make_day(_date(2025, 3, d), active_seconds=36000.0) for d in range(1, 29)]
    months_2025 = summarize_months(days_2025)

    # 2026: 1 month at 54000s/day × 28 days = 1512000s
    days_2026 = [_make_day(_date(2026, 3, d), active_seconds=54000.0) for d in range(1, 29)]
    months_2026 = summarize_months(days_2026)

    all_months = months_2025 + months_2026
    quarters = summarize_quarters(all_months)
    years = summarize_years(quarters)

    assert len(years) == 2
    assert years[0].active_delta_vs_prior is None
    assert years[1].active_delta_vs_prior is not None
    assert years[1].active_delta_vs_prior > 0


def test_summarize_years_multi_quarter_trend() -> None:
    """Year with 4 quarters has quarter_active_trend with 4 entries."""
    from datetime import date as _date

    days = []
    for m in range(1, 13):
        max_d = 28 if m in (2,) else (30 if m in (4, 6, 9, 11) else 31)
        for d in range(1, min(max_d, 5) + 1):  # only 4 days per month for speed
            days.append(_make_day(_date(2026, m, d), active_seconds=36000.0))

    months = summarize_months(days)
    quarters = summarize_quarters(months)
    years = summarize_years(quarters)

    assert len(years) == 1
    assert years[0].quarter_count == 4
    assert len(years[0].quarter_active_trend) == 4


# ============================================================================
# SIGNAL COVERAGE TESTS
# ============================================================================


def test_compute_coverage_empty_sources_returns_empty_quality() -> None:
    """Day with no sources → quality='empty', plane_count=0."""
    day = _make_day(
        date(2026, 1, 1),
        source_counts={},
        coverage={},
    )
    cov = compute_coverage(day)
    assert cov.quality == "empty"
    assert cov.plane_count == 0
    assert not cov.has_activitywatch
    assert not cov.has_terminal
    assert not cov.has_polylogue
    assert not cov.has_git
    assert not cov.has_atuin
    assert not cov.has_web


def test_compute_coverage_rich_quality_with_4_planes() -> None:
    """Day with 4+ distinct planes → quality='rich'."""
    day = _make_day(
        date(2026, 1, 1),
        source_counts={
            "activitywatch.window": 100,
            "activitywatch.web": 50,
            "instrumentation.terminal_session": 10,
            "polylogue.session": 3,
            "git.commit": 2,
            "atuin.command": 20,
        },
        coverage={},
    )
    cov = compute_coverage(day)
    assert cov.quality == "rich"
    assert cov.plane_count >= 4


def test_compute_coverage_has_correct_plane_flags() -> None:
    """Verify individual plane flags are set correctly from source_counts."""
    day = _make_day(
        date(2026, 1, 1),
        source_counts={
            "activitywatch.afk": 30,
            "atuin.command": 5,
            "git.commit": 1,
        },
        coverage={},
    )
    cov = compute_coverage(day)
    assert cov.has_activitywatch
    assert cov.has_atuin
    assert cov.has_git
    assert not cov.has_terminal
    assert not cov.has_polylogue
    assert not cov.has_web


def test_compute_coverage_date_matches_day() -> None:
    """Coverage date matches the input TrajectoryDay.date."""
    d = date(2026, 3, 16)
    day = _make_day(d, source_counts={"atuin.command": 1}, coverage={})
    cov = compute_coverage(day)
    assert cov.date == d


def test_compute_coverage_to_dict_is_serializable() -> None:
    """to_dict() returns a plain dict with no complex types."""
    import json

    day = _make_day(
        date(2026, 1, 1),
        source_counts={"activitywatch.window": 5, "atuin.command": 2},
        coverage={},
    )
    cov = compute_coverage(day)
    d = cov.to_dict()
    # Should be JSON-serializable
    serialized = json.dumps(d)
    assert "quality" in d
    assert isinstance(d["plane_count"], int)
    assert isinstance(serialized, str)


# ---------------------------------------------------------------------------
# _compose_label
# ---------------------------------------------------------------------------

class TestComposeLabel:
    def test_project_only(self) -> None:
        assert _compose_label("coding", "sinex") == "sinex coding"

    def test_mode_only(self) -> None:
        assert _compose_label("research", None) == "research"

    def test_project_and_mode(self) -> None:
        label = _compose_label("coding", "polylogue")
        assert "polylogue" in label
        assert "coding" in label

    def test_topic_excluded_when_same_as_project(self) -> None:
        # topic "sinex" == project "sinex" → should not be repeated
        label = _compose_label("coding", "sinex", "sinex")
        # "sinex" appears only once (as project)
        assert label.count("sinex") == 1

    def test_topic_included_when_different(self) -> None:
        label = _compose_label("coding", "sinex", "rust")
        assert "rust" in label

    def test_no_activity_returns_mixed(self) -> None:
        assert _compose_label(None, None) == "mixed activity"


# ---------------------------------------------------------------------------
# _iter_months
# ---------------------------------------------------------------------------

class TestIterMonths:
    def _dt(self, year: int, month: int, day: int = 1):
        from datetime import datetime, timezone
        return datetime(year, month, day, tzinfo=timezone.utc)

    def test_single_month(self) -> None:
        from datetime import datetime, timezone
        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 25, tzinfo=timezone.utc)
        months = list(_iter_months(start, end))
        assert months == [(2026, 3)]

    def test_two_months(self) -> None:
        from datetime import datetime, timezone
        start = datetime(2026, 3, 20, tzinfo=timezone.utc)
        end = datetime(2026, 4, 5, tzinfo=timezone.utc)
        months = list(_iter_months(start, end))
        assert months == [(2026, 3), (2026, 4)]

    def test_year_boundary(self) -> None:
        from datetime import datetime, timezone
        start = datetime(2025, 12, 20, tzinfo=timezone.utc)
        end = datetime(2026, 1, 15, tzinfo=timezone.utc)
        months = list(_iter_months(start, end))
        assert months == [(2025, 12), (2026, 1)]

    def test_full_year(self) -> None:
        from datetime import datetime, timezone
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 12, 31, tzinfo=timezone.utc)
        months = list(_iter_months(start, end))
        assert len(months) == 12
        assert months[0] == (2026, 1)
        assert months[-1] == (2026, 12)

    def test_same_day_start_and_end(self) -> None:
        from datetime import datetime, timezone
        t = datetime(2026, 6, 15, tzinfo=timezone.utc)
        months = list(_iter_months(t, t))
        assert months == [(2026, 6)]


# ---------------------------------------------------------------------------
# TrajectoryEpisode.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryEpisodeToDict:
    def _make_episode(self) -> TrajectoryEpisode:
        return TrajectoryEpisode(
            episode_id="abc12345",
            label="sinex coding",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 10),
            days=10,
            active_seconds=72000.0,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            mode_distribution={"coding": 65000.0},
            project_distribution={"sinex": 60000.0},
            trigger="project_shift",
            confidence=0.85,
            day_count_with_dominant=8,
        )

    def test_to_dict_json_serializable(self) -> None:
        import json
        d = self._make_episode().to_dict()
        json.dumps(d)

    def test_to_dict_dates_are_isoformat(self) -> None:
        d = self._make_episode().to_dict()
        assert d["start_date"] == "2026-03-01"
        assert d["end_date"] == "2026-03-10"

    def test_to_dict_confidence_rounded(self) -> None:
        d = self._make_episode().to_dict()
        assert d["confidence"] == pytest.approx(0.85)

    def test_to_dict_includes_required_fields(self) -> None:
        d = self._make_episode().to_dict()
        for key in ("episode_id", "label", "start_date", "end_date", "days",
                    "active_seconds", "dominant_mode", "dominant_project",
                    "mode_distribution", "project_distribution", "trigger",
                    "confidence", "day_count_with_dominant"):
            assert key in d


# ---------------------------------------------------------------------------
# TrajectoryAnomaly.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryAnomalyToDict:
    def _make_anomaly(self) -> TrajectoryAnomaly:
        return TrajectoryAnomaly(
            anomaly_id="abc123def456",
            date=date(2026, 3, 15),
            kind="rhythm_anomaly",
            severity=0.7523,
            description="Active hours 1.5h is 2.3σ below rolling mean 8.0h",
            baseline_value=8.0,
            actual_value=1.5,
            evidence={"stdev": 2.5, "direction": "below"},
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_anomaly().to_dict()
        json.dumps(d)

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_anomaly().to_dict()
        for key in ("anomaly_id", "date", "kind", "severity", "description",
                    "baseline_value", "actual_value", "evidence"):
            assert key in d

    def test_to_dict_rounds_numeric_fields(self) -> None:
        d = self._make_anomaly().to_dict()
        assert d["severity"] == pytest.approx(0.752)
        assert d["baseline_value"] == pytest.approx(8.0)
        assert d["actual_value"] == pytest.approx(1.5)

    def test_to_dict_date_is_isoformat(self) -> None:
        d = self._make_anomaly().to_dict()
        assert d["date"] == "2026-03-15"

    def test_to_dict_evidence_none_becomes_empty_dict(self) -> None:
        anomaly = TrajectoryAnomaly(
            anomaly_id="abc123def456",
            date=date(2026, 3, 15),
            kind="rhythm_anomaly",
            severity=0.7,
            description="test",
            baseline_value=8.0,
            actual_value=1.5,
            evidence=None,
        )
        assert anomaly.to_dict()["evidence"] == {}


# ---------------------------------------------------------------------------
# _anomaly_id
# ---------------------------------------------------------------------------

class TestAnomalyId:
    def test_returns_12_chars(self) -> None:
        aid = _anomaly_id(date(2026, 3, 15), "rhythm_anomaly")
        assert len(aid) == 12

    def test_is_deterministic(self) -> None:
        d = date(2026, 3, 15)
        assert _anomaly_id(d, "rhythm_anomaly") == _anomaly_id(d, "rhythm_anomaly")

    def test_differs_for_different_dates(self) -> None:
        a = _anomaly_id(date(2026, 3, 15), "rhythm_anomaly")
        b = _anomaly_id(date(2026, 3, 16), "rhythm_anomaly")
        assert a != b

    def test_differs_for_different_kinds(self) -> None:
        d = date(2026, 3, 15)
        a = _anomaly_id(d, "rhythm_anomaly")
        b = _anomaly_id(d, "mode_shift")
        assert a != b


# ---------------------------------------------------------------------------
# TrajectoryWeek.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryWeekToDict:
    def _make_week(self) -> TrajectoryWeek:
        return TrajectoryWeek(
            iso_week="2026-W11",
            start_date=date(2026, 3, 9),
            end_date=date(2026, 3, 15),
            days=7,
            active_seconds=72000.0,
            recovery_seconds=28800.0,
            chain_count=20,
            signal_count=100,
            command_count=10,
            transcript_count=2,
            commit_count=5,
            top_modes=(("coding", 54000.0), ("research", 18000.0)),
            top_projects=(("sinex", 54000.0),),
            top_topics=(("rust", 36000.0),),
            day_pattern="front_loaded",
            busiest_day=date(2026, 3, 9),
            quietest_day=date(2026, 3, 15),
            active_delta_vs_prior=3600.0,
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_week().to_dict()
        json.dumps(d)

    def test_to_dict_dates_are_isoformat(self) -> None:
        d = self._make_week().to_dict()
        assert d["start_date"] == "2026-03-09"
        assert d["end_date"] == "2026-03-15"
        assert d["busiest_day"] == "2026-03-09"
        assert d["quietest_day"] == "2026-03-15"

    def test_to_dict_none_dates_are_null(self) -> None:
        week = TrajectoryWeek(
            iso_week="2026-W11", start_date=date(2026, 3, 9), end_date=date(2026, 3, 15),
            days=7, active_seconds=0.0, recovery_seconds=0.0, chain_count=0, signal_count=0,
            command_count=0, transcript_count=0, commit_count=0,
            top_modes=(), top_projects=(), top_topics=(),
            day_pattern="uniform", busiest_day=None, quietest_day=None, active_delta_vs_prior=None,
        )
        d = week.to_dict()
        assert d["busiest_day"] is None
        assert d["quietest_day"] is None
        assert d["active_delta_vs_prior"] is None

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_week().to_dict()
        for key in ("iso_week", "start_date", "end_date", "days", "active_seconds",
                    "recovery_seconds", "observed_seconds", "chain_count", "signal_count",
                    "command_count", "commit_count", "dominant_mode", "dominant_project",
                    "top_modes", "top_projects", "top_topics", "day_pattern"):
            assert key in d

    def test_observed_seconds_is_sum(self) -> None:
        d = self._make_week().to_dict()
        assert d["observed_seconds"] == pytest.approx(100800.0)


# ---------------------------------------------------------------------------
# TrajectoryMonth.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryMonthToDict:
    def _make_month(self) -> TrajectoryMonth:
        return TrajectoryMonth(
            month="2026-03",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 31),
            total_days=31,
            active_days=22,
            active_seconds=792000.0,
            recovery_seconds=288000.0,
            chain_count=200,
            signal_count=1000,
            command_count=100,
            transcript_count=10,
            commit_count=30,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            top_modes=(("coding", 720000.0),),
            top_projects=(("sinex", 540000.0),),
            top_topics=(("rust", 360000.0),),
            source_counts={"atuin.command": 100},
            coverage_summary={"full": 18, "partial": 4},
            highlights=("mode:coding 220.0h",),
            chat_session_count=15,
            chat_work_events={"implementation": 8},
            chat_cost_usd=2.5,
            episode_count=2,
            episode_labels=("sinex coding", "research sprint"),
            week_count=5,
            day_patterns=("front_loaded", "uniform"),
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_month().to_dict()
        json.dumps(d)

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_month().to_dict()
        for key in ("month", "start_date", "end_date", "total_days", "active_days",
                    "active_seconds", "recovery_seconds", "observed_seconds", "chain_count",
                    "signal_count", "dominant_mode", "dominant_project", "top_modes",
                    "top_projects", "top_topics", "highlights", "chat_session_count",
                    "chat_work_events", "chat_cost_usd", "episode_count", "episode_labels",
                    "week_count", "day_patterns"):
            assert key in d

    def test_to_dict_dates_are_isoformat(self) -> None:
        d = self._make_month().to_dict()
        assert d["start_date"] == "2026-03-01"
        assert d["end_date"] == "2026-03-31"

    def test_observed_seconds_is_sum(self) -> None:
        d = self._make_month().to_dict()
        assert d["observed_seconds"] == pytest.approx(1080000.0)


# ---------------------------------------------------------------------------
# TrajectoryQuarter.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryQuarterToDict:
    def _make_quarter(self) -> TrajectoryQuarter:
        return TrajectoryQuarter(
            quarter="2026-Q1",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            total_days=90,
            active_days=60,
            active_seconds=2160000.0,
            recovery_seconds=720000.0,
            chain_count=600,
            signal_count=3000,
            command_count=300,
            transcript_count=30,
            commit_count=90,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            top_modes=(("coding", 1800000.0),),
            top_projects=(("sinex", 1440000.0),),
            top_topics=(("rust", 1080000.0),),
            coverage_summary={"full": 50, "partial": 10},
            chat_session_count=45,
            chat_cost_usd=7.5,
            episode_count=6,
            month_count=3,
            month_active_trend=(720000.0, 720000.0, 720000.0),
            active_delta_vs_prior=None,
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_quarter().to_dict()
        json.dumps(d)

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_quarter().to_dict()
        for key in ("quarter", "start_date", "end_date", "total_days", "active_days",
                    "active_seconds", "recovery_seconds", "observed_seconds", "chain_count",
                    "signal_count", "dominant_mode", "dominant_project", "top_modes",
                    "top_projects", "top_topics", "chat_session_count", "chat_cost_usd",
                    "episode_count", "month_count", "month_active_trend"):
            assert key in d

    def test_observed_seconds_is_sum(self) -> None:
        d = self._make_quarter().to_dict()
        assert d["observed_seconds"] == pytest.approx(2880000.0)

    def test_active_delta_none_when_absent(self) -> None:
        d = self._make_quarter().to_dict()
        assert d["active_delta_vs_prior"] is None

    def test_month_active_trend_is_list(self) -> None:
        d = self._make_quarter().to_dict()
        assert isinstance(d["month_active_trend"], list)
        assert len(d["month_active_trend"]) == 3


# ---------------------------------------------------------------------------
# TrajectoryYear.to_dict
# ---------------------------------------------------------------------------

class TestTrajectoryYearToDict:
    def _make_year(self) -> TrajectoryYear:
        return TrajectoryYear(
            year="2026",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
            total_days=365,
            active_days=220,
            active_seconds=7920000.0,
            recovery_seconds=2880000.0,
            chain_count=2400,
            signal_count=12000,
            command_count=1200,
            transcript_count=120,
            commit_count=360,
            dominant_mode="coding",
            dominant_project="sinex",
            dominant_topic="rust",
            top_modes=(("coding", 7200000.0),),
            top_projects=(("sinex", 5760000.0),),
            top_topics=(("rust", 4320000.0),),
            coverage_summary={"full": 180, "partial": 40},
            chat_session_count=180,
            chat_cost_usd=30.0,
            episode_count=24,
            quarter_count=4,
            quarter_active_trend=(1980000.0, 1980000.0, 1980000.0, 1980000.0),
            active_delta_vs_prior=360000.0,
        )

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        d = self._make_year().to_dict()
        json.dumps(d)

    def test_to_dict_has_required_fields(self) -> None:
        d = self._make_year().to_dict()
        for key in ("year", "start_date", "end_date", "total_days", "active_days",
                    "active_seconds", "recovery_seconds", "observed_seconds", "chain_count",
                    "signal_count", "dominant_mode", "dominant_project", "top_modes",
                    "top_projects", "top_topics", "chat_session_count", "chat_cost_usd",
                    "episode_count", "quarter_count", "quarter_active_trend",
                    "active_delta_vs_prior"):
            assert key in d

    def test_observed_seconds_is_sum(self) -> None:
        d = self._make_year().to_dict()
        assert d["observed_seconds"] == pytest.approx(10800000.0)

    def test_active_delta_converted_when_present(self) -> None:
        d = self._make_year().to_dict()
        assert d["active_delta_vs_prior"] == pytest.approx(360000.0)

    def test_quarter_active_trend_is_list_of_four(self) -> None:
        d = self._make_year().to_dict()
        assert isinstance(d["quarter_active_trend"], list)
        assert len(d["quarter_active_trend"]) == 4


# ---------------------------------------------------------------------------
# summarize_period / summarize_months
# ---------------------------------------------------------------------------

class TestSummarizePeriod:
    def test_empty_input_returns_zero_filled(self) -> None:
        result = summarize_period([])
        assert result.total_days == 0
        assert result.active_seconds == 0.0
        assert result.recovery_seconds == 0.0
        assert result.chain_count == 0
        assert result.signal_count == 0
        assert result.dominant_modes == ()

    def test_empty_input_has_empty_dates(self) -> None:
        result = summarize_period([])
        assert result.start_date == ""
        assert result.end_date == ""

    def test_single_day_active_seconds_accumulated(self) -> None:
        day = _make_day(date(2026, 3, 10), active_seconds=7200.0, recovery_seconds=3600.0)
        result = summarize_period([day])
        assert result.active_seconds == pytest.approx(7200.0)
        assert result.recovery_seconds == pytest.approx(3600.0)

    def test_single_day_dates_match(self) -> None:
        day = _make_day(date(2026, 3, 10))
        result = summarize_period([day])
        assert result.start_date == "2026-03-10"
        assert result.end_date == "2026-03-10"

    def test_multiple_days_dates_are_first_and_last(self) -> None:
        days = [
            _make_day(date(2026, 3, 1)),
            _make_day(date(2026, 3, 5)),
            _make_day(date(2026, 3, 10)),
        ]
        result = summarize_period(days)
        assert result.start_date == "2026-03-01"
        assert result.end_date == "2026-03-10"
        assert result.total_days == 3

    def test_active_seconds_sum_across_days(self) -> None:
        days = [
            _make_day(date(2026, 3, 1), active_seconds=3600.0),
            _make_day(date(2026, 3, 2), active_seconds=7200.0),
        ]
        result = summarize_period(days)
        assert result.active_seconds == pytest.approx(10800.0)

    def test_chain_and_signal_counts_accumulated(self) -> None:
        days = [
            _make_day(date(2026, 3, 1), chain_count=5, signal_count=20),
            _make_day(date(2026, 3, 2), chain_count=8, signal_count=30),
        ]
        result = summarize_period(days)
        assert result.chain_count == 13
        assert result.signal_count == 50

    def test_dominant_modes_sorted_by_seconds(self) -> None:
        days = [
            _make_day(date(2026, 3, 1), top_modes=(("coding", 7200.0), ("research", 1800.0))),
            _make_day(date(2026, 3, 2), top_modes=(("coding", 3600.0),)),
        ]
        result = summarize_period(days)
        mode_names = [m for m, _ in result.dominant_modes]
        assert mode_names[0] == "coding"

    def test_coverage_flags_tallied_per_day(self) -> None:
        cov_full = {"has_activitywatch": True, "has_terminal": True, "has_chatlog": True, "has_git": True}
        cov_none = {"has_activitywatch": False, "has_terminal": False, "has_chatlog": False, "has_git": False}
        days = [
            _make_day(date(2026, 3, 1), coverage=cov_full),
            _make_day(date(2026, 3, 2), coverage=cov_none),
            _make_day(date(2026, 3, 3), coverage=cov_full),
        ]
        result = summarize_period(days)
        assert result.coverage["days_with_activitywatch"] == 2
        assert result.coverage["days_with_chatlog"] == 2
        assert result.coverage["days_with_git"] == 2
        assert result.coverage["days_with_terminal"] == 2

    def test_highlights_generated_for_nonempty_counts(self) -> None:
        day = _make_day(date(2026, 3, 1), command_count=10, commit_count=3)
        result = summarize_period([day])
        joined = " ".join(result.highlights)
        assert "commands:10" in joined
        assert "commits:3" in joined

    def test_to_dict_is_json_serializable(self) -> None:
        import json
        day = _make_day(date(2026, 3, 1), top_topics=(("rust", 3600.0),))
        d = summarize_period([day]).to_dict()
        json.dumps(d)
        assert "dominant_topics" in d

    def test_observed_seconds_property(self) -> None:
        day = _make_day(date(2026, 3, 1), active_seconds=7200.0, recovery_seconds=3600.0)
        result = summarize_period([day])
        assert result.observed_seconds == pytest.approx(10800.0)


class TestSummarizeMonths:
    def test_empty_input_returns_empty_dict(self) -> None:
        assert summarize_month_periods([]) == {}

    def test_single_month_keyed_by_year_month(self) -> None:
        days = [_make_day(date(2026, 3, d)) for d in range(1, 4)]
        result = summarize_month_periods(days)
        assert "2026-03" in result
        assert len(result) == 1

    def test_two_months_produces_two_keys(self) -> None:
        march = [_make_day(date(2026, 3, d)) for d in range(1, 4)]
        april = [_make_day(date(2026, 4, d)) for d in range(1, 4)]
        result = summarize_month_periods(march + april)
        assert "2026-03" in result
        assert "2026-04" in result
        assert len(result) == 2

    def test_result_keys_sorted_chronologically(self) -> None:
        jan = [_make_day(date(2026, 1, 1))]
        mar = [_make_day(date(2026, 3, 1))]
        feb = [_make_day(date(2026, 2, 1))]
        result = summarize_month_periods(jan + mar + feb)
        keys = list(result.keys())
        assert keys == sorted(keys)

    def test_each_value_is_period_summary(self) -> None:
        result = summarize_month_periods([_make_day(date(2026, 3, 1))])
        assert isinstance(result["2026-03"], TrajectoryPeriodSummary)

    def test_month_totals_correct(self) -> None:
        days = [_make_day(date(2026, 3, d), active_seconds=3600.0) for d in range(1, 4)]
        result = summarize_month_periods(days)
        march = result["2026-03"]
        assert march.active_seconds == pytest.approx(10800.0)
        assert march.total_days == 3
