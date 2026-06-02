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
    states = [S(unit, t) for t in (0, 60, 120, 180, 3600)]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END,
        units=(unit,),
    ))
    assert intervals == []
    summary = service_uptime_summary(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    )
    assert summary[unit]["uptime_fraction"] == 1.0


def test_failed_state_opens_downtime_interval() -> None:
    """A 'failed' or 'inactive' observation opens an interval; the next
    active+running observation closes it."""
    unit = "activitywatch.service"
    states = [
        S(unit, 0),                                  # active running
        S(unit, 600, active="failed", sub="failed"),  # 10min in: failed
        S(unit, 1200, active="failed", sub="failed"),
        S(unit, 1800),                                # 30min in: back up
        S(unit, 3600),
    ]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    ))
    assert len(intervals) == 1
    assert intervals[0].kind == "inactive"
    # 600s → 1800s = 20 minutes of downtime
    assert (intervals[0].end - intervals[0].start).total_seconds() == 1200


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


def test_failure_until_window_end_held_open() -> None:
    """If the unit fails mid-window and never recovers, the downtime
    interval extends to window_end."""
    unit = "activitywatch.service"
    states = [
        S(unit, 0),
        S(unit, 600, active="failed", sub="failed"),  # fails at 600s, never recovers
    ]
    intervals = list(downtime_intervals(
        states, window_start=W_START, window_end=W_END, units=(unit,),
    ))
    assert len(intervals) == 1
    assert intervals[0].end == W_END


def test_distinct_units_tracked_separately() -> None:
    """Failure of one unit must not affect another."""
    aw, pl = "activitywatch.service", "polylogued.service"
    states = [
        S(aw, 0),
        S(aw, 1800, active="failed", sub="failed"),
        S(pl, 0),
        S(pl, 1800),
        S(pl, 3600),
    ]
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
