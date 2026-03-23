"""Circadian rhythm profiles: hourly activity buckets from trajectory signals."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, Optional


@dataclass(frozen=True)
class CircadianProfile:
    date: date
    hour: int  # 0-23
    active_minutes: float
    recovery_minutes: float
    commit_count: int
    command_count: int
    app_switches: int
    dominant_mode: str | None
    dominant_project: str | None


def iter_circadian(*, start: date, end: date) -> Iterator[CircadianProfile]:
    """Hourly activity profiles. Buckets trajectory signals into hours."""
    from ...trajectory.signal import load_signals

    d = start
    while d <= end:
        dt_start = datetime(d.year, d.month, d.day)
        dt_end = dt_start + timedelta(days=1)
        try:
            signals = load_signals(start=dt_start, end=dt_end)
        except Exception:
            d += timedelta(days=1)
            continue

        # Bucket by hour
        hours: dict[int, list] = defaultdict(list)
        for s in signals:
            hours[s.start.hour].append(s)

        for hour in range(24):
            sigs = hours.get(hour, [])
            if not sigs:
                continue

            active = sum(
                (s.end - s.start).total_seconds() / 60
                for s in sigs
                if s.source != "activitywatch.afk"
            )
            recovery = sum(
                (s.end - s.start).total_seconds() / 60
                for s in sigs
                if s.source == "activitywatch.afk"
            )
            commits = sum(1 for s in sigs if "git" in s.source)
            commands = sum(
                1 for s in sigs if "atuin" in s.source or s.kind == "command"
            )

            # App switches
            apps = [
                s.app
                for s in sigs
                if s.app and s.source.startswith("activitywatch")
            ]
            switches = sum(
                1 for i in range(1, len(apps)) if apps[i] != apps[i - 1]
            )

            # Dominant mode/project by duration
            mode_dur: Counter[str] = Counter()
            proj_dur: Counter[str] = Counter()
            for s in sigs:
                dur = (s.end - s.start).total_seconds()
                if s.mode_hint:
                    mode_dur[s.mode_hint] += dur
                if s.project_hint:
                    proj_dur[s.project_hint] += dur

            yield CircadianProfile(
                date=d,
                hour=hour,
                active_minutes=active,
                recovery_minutes=recovery,
                commit_count=commits,
                command_count=commands,
                app_switches=switches,
                dominant_mode=(
                    mode_dur.most_common(1)[0][0] if mode_dur else None
                ),
                dominant_project=(
                    proj_dur.most_common(1)[0][0] if proj_dur else None
                ),
            )

        d += timedelta(days=1)


@dataclass(frozen=True)
class CircadianBaseline:
    hour: int
    avg_active_minutes: float
    stddev_active_minutes: float
    peak_project: str | None
    peak_mode: str | None


def compute_circadian_baseline(
    *, as_of: date, lookback_days: int = 14
) -> list[CircadianBaseline]:
    """Rolling average circadian pattern."""
    start = as_of - timedelta(days=lookback_days)
    profiles = list(iter_circadian(start=start, end=as_of))

    hour_data: dict[int, list[float]] = defaultdict(list)
    hour_projects: dict[int, Counter] = defaultdict(Counter)
    hour_modes: dict[int, Counter] = defaultdict(Counter)

    for p in profiles:
        hour_data[p.hour].append(p.active_minutes)
        if p.dominant_project:
            hour_projects[p.hour][p.dominant_project] += 1
        if p.dominant_mode:
            hour_modes[p.hour][p.dominant_mode] += 1

    result = []
    for hour in range(24):
        vals = hour_data.get(hour, [0.0])
        avg = sum(vals) / len(vals)
        variance = sum((v - avg) ** 2 for v in vals) / max(len(vals), 1)
        result.append(
            CircadianBaseline(
                hour=hour,
                avg_active_minutes=avg,
                stddev_active_minutes=math.sqrt(variance),
                peak_project=(
                    hour_projects[hour].most_common(1)[0][0]
                    if hour_projects[hour]
                    else None
                ),
                peak_mode=(
                    hour_modes[hour].most_common(1)[0][0]
                    if hour_modes[hour]
                    else None
                ),
            )
        )
    return result
