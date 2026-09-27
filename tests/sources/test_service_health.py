"""Tests for ServiceDowntime detection.

Goal: distinguish "no AW data because operator AFK" from "no AW data
because activitywatch.service was failed". Without this distinction
lynchpin attributes every capture gap to the operator.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lynchpin.sources.machine_models import MachineServiceState
from lynchpin.sources.service_health import (
    CAPTURE_SERVICE_UNITS,
    downtime_intervals,
    service_uptime_summary,
)

UTC = timezone.utc


def S(unit: str, ts_s: float, active: str = "active", sub: str = "running") -> MachineServiceState:
    """Build a MachineServiceState at H+ts_s seconds with given state."""
    return MachineServiceState(
        observed_at=datetime(2026, 5, 25, 10, tzinfo=UTC) + timedelta(seconds=ts_s),
        host="sinnix-prime",
        boot_id=None,
        unit=unit,
        scope="system",
        active_state=active,
        sub_state=sub,
    )


W_START = datetime(2026, 5, 25, 10, tzinfo=UTC)
W_END = datetime(2026, 5, 25, 11, tzinfo=UTC)


def test_continuous_uptime_yields_no_downtime() -> None:
    """All observations are active+running → uptime_fraction = 1.0,
    no downtime intervals emitted."""
    unit = "activitywatch.service"
    states = [S(unit, t) for t in range(0, 3601, 30)]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END,
        units=(unit,),
    ))
    assert intervals == []
    summary = service_uptime_summary(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    )
    assert summary[unit]["uptime_fraction"] == 1.0


def test_midday_observation_only_proves_bounded_sample_window() -> None:
    unit = "activitywatch.service"
    day_start = datetime(2026, 5, 25, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    observed = MachineServiceState(
        observed_at=day_start + timedelta(hours=12), host="host", boot_id=None,
        unit=unit, scope="system", active_state="active", sub_state="running",
    )

    intervals = list(downtime_intervals(
        [observed], window_start=day_start, window_end=day_end, units=(unit,),
    ))
    summary = service_uptime_summary(
        [observed], window_start=day_start, window_end=day_end, units=(unit,),
    )

    assert [(i.start, i.end, i.kind) for i in intervals] == [
        (day_start, observed.observed_at, "unobserved"),
        (observed.observed_at + timedelta(seconds=30), day_end, "unobserved"),
    ]
    assert 0 < summary[unit]["uptime_fraction"] < 1


def test_midnight_boundary_includes_start_and_excludes_next_day() -> None:
    unit = "activitywatch.service"
    day_start = datetime(2026, 5, 25, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    rows = [
        MachineServiceState(
            observed_at=timestamp, host="host", boot_id=None, unit=unit,
            scope="system", active_state=active, sub_state=sub,
        )
        for timestamp, active, sub in (
            (day_start - timedelta(seconds=10), "active", "running"),
            (day_start, "active", "running"),
            (day_end, "failed", "failed"),
        )
    ]

    intervals = list(downtime_intervals(
        rows, window_start=day_start, window_end=day_end, units=(unit,),
    ))

    assert [(interval.start, interval.end, interval.kind) for interval in intervals] == [
        (day_start + timedelta(seconds=30), day_end, "unobserved"),
    ]


def test_intraday_window_uses_predecessor_and_clips_surrounding_rows() -> None:
    unit = "activitywatch.service"
    start = W_START + timedelta(minutes=10)
    end = W_START + timedelta(minutes=20)
    rows = [
        S(unit, 590),                         # bounded predecessor at start
        S(unit, 620, active="failed", sub="failed"),  # 20s into window
        S(unit, 650),                         # recovery 50s in
        S(unit, 1200, active="failed", sub="failed"),  # exactly at end
    ]

    intervals = list(downtime_intervals(
        rows, window_start=start, window_end=end, units=(unit,),
    ))

    assert all(start <= interval.start < interval.end <= end for interval in intervals)
    assert [(i.start, i.end, i.kind) for i in intervals] == [
        (start + timedelta(seconds=20), start + timedelta(seconds=50), "inactive"),
        (start + timedelta(seconds=80), end, "unobserved"),
    ]


def test_failed_state_opens_downtime_interval() -> None:
    """A 'failed' or 'inactive' observation opens an interval; the next
    active+running observation closes it."""
    unit = "activitywatch.service"
    states = [S(unit, t) for t in range(0, 600, 30)] + [
        S(unit, t, active="failed", sub="failed")
        for t in range(600, 1801, 30)
    ] + [S(unit, t) for t in range(1830, 3601, 30)]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    ))
    assert len(intervals) == 1
    assert intervals[0].kind == "inactive"
    assert intervals[0].start == W_START + timedelta(seconds=600)
    assert intervals[0].end == W_START + timedelta(seconds=1830)


def test_no_observations_yields_unobserved_interval() -> None:
    """A unit with no telemetry → single 'unobserved' interval covering
    the window. Caller decides how to treat it."""
    unit = "polylogued.service"
    intervals = list(downtime_intervals(
        [], window_start=W_START, window_end=W_END, units=(unit,),
    ))
    assert len(intervals) == 1
    assert intervals[0].kind == "unobserved"
    assert intervals[0].start == W_START
    assert intervals[0].end == W_END


def test_long_failure_without_samples_becomes_unobserved() -> None:
    """A state is held for at most the declared sampling-gap threshold."""
    unit = "activitywatch.service"
    states = [S(unit, t) for t in range(0, 600, 30)] + [
        S(unit, 600, active="failed", sub="failed"),  # fails at 600s, never recovers
    ]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    ))
    assert [(i.kind, i.start, i.end) for i in intervals] == [
        ("inactive", W_START + timedelta(seconds=600), W_START + timedelta(seconds=630)),
        ("unobserved", W_START + timedelta(seconds=630), W_END),
    ]


def test_distinct_units_tracked_separately() -> None:
    """Failure of one unit must not affect another."""
    aw, pl = "activitywatch.service", "polylogued.service"
    states = [S(aw, t) for t in range(0, 1800, 30)] + [
        S(aw, 1800, active="failed", sub="failed"),
    ] + [S(pl, t) for t in range(0, 3601, 30)]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END, units=(aw, pl),
    ))
    by_unit = {i.unit: i for i in intervals}
    assert aw in by_unit
    assert pl not in by_unit  # polylogued was fine, no interval


def test_capture_service_units_includes_expected_set() -> None:
    """Defends against a refactor that drops one of the canonical units."""
    expected = {
        "activitywatch.service",
        "activitywatch-watcher-awatcher.service",
        "polylogued.service",
    }
    assert expected.issubset(set(CAPTURE_SERVICE_UNITS))


def test_uptime_summary_counts_unobserved_as_downtime() -> None:
    """Conservatively treat unobserved as not-provably-up. Caller can
    distinguish 'inactive' vs 'unobserved' from the interval kind if
    they want a different policy."""
    summary = service_uptime_summary(
        [], window_start=W_START, window_end=W_END,
        units=("polylogued.service",),
    )
    assert summary["polylogued.service"]["uptime_fraction"] == 0.0
