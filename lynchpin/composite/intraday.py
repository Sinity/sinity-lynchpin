"""Intraday performance curves: hourly activity/productivity patterns.

Answers: "When in the day are you most productive?" by building
per-clock-hour and per-wake-hour profiles from AW + git + sleep data.

Uses the 6AM logical day boundary.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as time_cls
from typing import Sequence

from ..core.primitives import date_to_dt_range, logical_date

__all__ = [
    "HourlyProfile",
    "IntradayProfile",
    "clock_hour_profile",
    "wake_hour_profile",
    "intraday_profile",
]


@dataclass(frozen=True)
class HourlyProfile:
    """Activity metrics for one hour bucket."""
    hour: int           # 0-23 for clock hours, 0-23 for wake-relative hours
    active_min: float   # AW active minutes
    focus_min: float    # sustained focus minutes (≥25min blocks)
    commit_count: int   # git commits
    n_days: int         # how many days contributed to this bucket


@dataclass(frozen=True)
class IntradayProfile:
    """Complete intraday view: clock hours + wake-relative hours."""
    period: str                          # e.g., "2025-01 → 2025-07"
    by_clock_hour: tuple[HourlyProfile, ...]   # 24 entries, indexed by clock hour
    by_wake_hour: tuple[HourlyProfile, ...]    # up to 24 entries, relative to wake time
    peak_clock_hour: int
    peak_wake_hour: int
    avg_wake_hour: int                   # typical wake hour (clock)


def clock_hour_profile(*, start: date, end: date) -> list[HourlyProfile]:
    """Hourly activity profile by absolute clock hour (0-23)."""
    from ..sources.activitywatch import active_intervals, app_sessions
    from ..sources.git import commit_facts

    s_dt, e_dt = date_to_dt_range(start, end)

    # AW active time per clock hour
    active = active_intervals(start=s_dt, end=e_dt)
    hourly_active: dict[int, list[float]] = defaultdict(list)  # hour → [minutes per day]
    daily_hour_active: dict[tuple[date, int], float] = defaultdict(float)

    def strip(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    for a_start, a_end in active:
        a_s = strip(a_start)
        a_e = strip(a_end)
        cursor = a_s
        while cursor < a_e:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            segment_end = min(a_e, next_hour)
            mins = (segment_end - cursor).total_seconds() / 60
            d = logical_date(cursor)
            daily_hour_active[(d, cursor.hour)] += mins
            cursor = segment_end

    # Aggregate per hour
    hour_days: dict[int, set[date]] = defaultdict(set)
    hour_total: dict[int, float] = defaultdict(float)
    for (d, h), mins in daily_hour_active.items():
        if start <= d <= end:
            hour_days[h].add(d)
            hour_total[h] += mins

    # Git commits per clock hour
    hour_commits: dict[int, int] = defaultdict(int)
    for f in commit_facts(start=start, end=end):
        h = f.authored_at.hour
        hour_commits[h] += 1

    # Sustained focus per clock hour
    from ..sources.activitywatch import sustained_focus
    sf_blocks = sustained_focus(start=s_dt, end=e_dt)
    hour_focus: dict[int, float] = defaultdict(float)
    for b in sf_blocks:
        b_s = strip(b.start)
        b_e = strip(b.end)
        cursor = b_s
        while cursor < b_e:
            next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            seg_end = min(b_e, next_hour)
            hour_focus[cursor.hour] += (seg_end - cursor).total_seconds() / 60
            cursor = seg_end

    result = []
    for h in range(24):
        n = len(hour_days.get(h, set()))
        result.append(HourlyProfile(
            hour=h,
            active_min=round(hour_total.get(h, 0) / max(n, 1), 1),
            focus_min=round(hour_focus.get(h, 0) / max(n, 1), 1),
            commit_count=hour_commits.get(h, 0),
            n_days=n,
        ))
    return result


def wake_hour_profile(*, start: date, end: date) -> list[HourlyProfile]:
    """Hourly profile relative to wake time (hour 0 = just woke up)."""
    from ..sources.activitywatch import active_intervals, sustained_focus
    from ..sources.git import commit_facts
    from ..sources.sleep import entries_in_range

    s_dt, e_dt = date_to_dt_range(start, end)

    def strip(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    # Get wake times from sleep data
    wake_times: dict[date, datetime] = {}
    for e in entries_in_range(start - timedelta(days=1), end):
        if e.total_minutes < 60:
            continue
        if e.segments and e.segments[-1].end != datetime.min:
            wake_dt = strip(e.segments[-1].end)
            ld = logical_date(wake_dt)
            if start <= ld <= end:
                # Keep latest wake time for each logical date
                if ld not in wake_times or wake_dt > wake_times[ld]:
                    wake_times[ld] = wake_dt

    if not wake_times:
        return []

    # AW active intervals
    active = active_intervals(start=s_dt, end=e_dt)
    active_naive = sorted((strip(a), strip(b)) for a, b in active)

    # Sustained focus blocks
    sf_blocks = sustained_focus(start=s_dt, end=e_dt)

    # Git commit times
    commit_times = [strip(f.authored_at) for f in commit_facts(start=start, end=end)]

    # For each day with a known wake time, compute hourly metrics relative to waking
    hourly: dict[int, dict] = defaultdict(lambda: {"active": [], "focus": [], "commits": 0, "days": 0})

    for ld, wake in wake_times.items():
        hourly_bucket = hourly  # reference to shared accumulator
        for hour_offset in range(20):  # up to 20h after waking
            window_start = wake + timedelta(hours=hour_offset)
            window_end = window_start + timedelta(hours=1)

            # Active time in this hour
            active_min = 0
            for a_s, a_e in active_naive:
                o_s = max(a_s, window_start)
                o_e = min(a_e, window_end)
                if o_e > o_s:
                    active_min += (o_e - o_s).total_seconds() / 60

            # Focus time
            focus_min = 0
            for b in sf_blocks:
                b_s = strip(b.start)
                b_e = strip(b.end)
                o_s = max(b_s, window_start)
                o_e = min(b_e, window_end)
                if o_e > o_s:
                    focus_min += (o_e - o_s).total_seconds() / 60

            # Commits
            commits = sum(1 for ct in commit_times if window_start <= ct < window_end)

            if active_min > 1:  # at least 1 min active
                hourly[hour_offset]["active"].append(active_min)
                hourly[hour_offset]["focus"].append(focus_min)
                hourly[hour_offset]["commits"] += commits
                hourly[hour_offset]["days"] = len(set(
                    [ld] + [d for d in hourly[hour_offset].get("_dates", [])]
                ))

    result = []
    for h in range(20):
        data = hourly.get(h)
        if not data or not data["active"]:
            continue
        n = len(data["active"])
        result.append(HourlyProfile(
            hour=h,
            active_min=round(sum(data["active"]) / n, 1),
            focus_min=round(sum(data["focus"]) / n, 1),
            commit_count=data["commits"],
            n_days=n,
        ))
    return result


def intraday_profile(*, start: date, end: date) -> IntradayProfile:
    """Complete intraday profile combining clock and wake-relative views."""
    clock = clock_hour_profile(start=start, end=end)
    wake = wake_hour_profile(start=start, end=end)

    peak_clock = max(range(24), key=lambda h: clock[h].active_min) if clock else 0
    peak_wake = max(range(len(wake)), key=lambda h: wake[h].active_min) if wake else 0

    # Average wake hour from sleep data
    from ..sources.sleep import entries_in_range
    wake_hours = []
    for e in entries_in_range(start, end):
        if e.total_minutes >= 60 and e.segments and e.segments[-1].end != datetime.min:
            wh = e.segments[-1].end
            wake_hours.append(wh.hour if not wh.tzinfo else wh.replace(tzinfo=None).hour)
    avg_wake = round(sum(wake_hours) / len(wake_hours)) if wake_hours else 12

    return IntradayProfile(
        period=f"{start} → {end}",
        by_clock_hour=tuple(clock),
        by_wake_hour=tuple(wake),
        peak_clock_hour=peak_clock,
        peak_wake_hour=peak_wake,
        avg_wake_hour=avg_wake,
    )
