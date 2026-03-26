"""Circadian profiles from canonical focus spans with explicit AFK override."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator

from ._activitywatch import split_by_hour
from .focus_spans import iter_focus_spans
from .git_commit_facts import iter_git_commit_facts
from ..captures.atuin import iter_commands


@dataclass(frozen=True)
class CircadianProfile:
    date: date
    hour: int
    active_minutes: float
    recovery_minutes: float
    git_lines_changed: int
    git_files_changed: int
    command_count: int
    app_switches: int
    dominant_mode: str | None
    dominant_project: str | None


def iter_circadian(*, start: date, end: date) -> Iterator[CircadianProfile]:
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    focus_spans = list(
        iter_focus_spans(
            start=start_dt,
            end=end_dt,
            min_duration_seconds=30,
            include_keyboard=False,
        )
    )

    active_by_hour: dict[tuple[date, int], float] = defaultdict(float)
    recovery_by_hour: dict[tuple[date, int], float] = defaultdict(float)
    mode_durations: dict[tuple[date, int], Counter[str]] = defaultdict(Counter)
    project_durations: dict[tuple[date, int], Counter[str]] = defaultdict(Counter)
    app_sequences: dict[tuple[date, int], list[tuple[datetime, str]]] = defaultdict(list)

    for span in focus_spans:
        for seg_start, seg_end in split_by_hour(span.start, span.end):
            bucket = (seg_start.date(), seg_start.hour)
            minutes = (seg_end - seg_start).total_seconds() / 60.0
            if span.span_kind == "afk":
                recovery_by_hour[bucket] += minutes
                continue
            active_by_hour[bucket] += minutes
            if span.mode:
                mode_durations[bucket][span.mode] += minutes
            if span.project:
                project_durations[bucket][span.project] += minutes
            if span.app:
                app_sequences[bucket].append((seg_start, span.app))

    command_counts: dict[tuple[date, int], int] = defaultdict(int)
    for command in iter_commands(start=start_dt, end=end_dt):
        bucket = (command.timestamp.date(), command.timestamp.hour)
        command_counts[bucket] += 1

    git_lines_changed: dict[tuple[date, int], int] = defaultdict(int)
    git_files_changed: dict[tuple[date, int], int] = defaultdict(int)
    for fact in iter_git_commit_facts(start=start, end=end):
        bucket = (fact.authored_at.date(), fact.authored_at.hour)
        git_lines_changed[bucket] += fact.lines_changed
        git_files_changed[bucket] += fact.files_changed

    current = start
    while current <= end:
        for hour in range(24):
            bucket = (current, hour)
            active_minutes = active_by_hour.get(bucket, 0.0)
            recovery_minutes = recovery_by_hour.get(bucket, 0.0)
            if (
                active_minutes <= 0
                and recovery_minutes <= 0
                and command_counts.get(bucket, 0) == 0
                and git_lines_changed.get(bucket, 0) == 0
                and git_files_changed.get(bucket, 0) == 0
            ):
                continue
            apps = [app for _, app in sorted(app_sequences.get(bucket, ()), key=lambda item: item[0])]
            switches = sum(1 for left, right in zip(apps, apps[1:]) if left != right)
            yield CircadianProfile(
                date=current,
                hour=hour,
                active_minutes=active_minutes,
                recovery_minutes=recovery_minutes,
                git_lines_changed=git_lines_changed.get(bucket, 0),
                git_files_changed=git_files_changed.get(bucket, 0),
                command_count=command_counts.get(bucket, 0),
                app_switches=switches,
                dominant_mode=mode_durations[bucket].most_common(1)[0][0] if mode_durations.get(bucket) else None,
                dominant_project=project_durations[bucket].most_common(1)[0][0] if project_durations.get(bucket) else None,
            )
        current += timedelta(days=1)


@dataclass(frozen=True)
class CircadianBaseline:
    hour: int
    avg_active_minutes: float
    stddev_active_minutes: float
    peak_project: str | None
    peak_mode: str | None


def compute_circadian_baseline(
    *,
    as_of: date,
    lookback_days: int = 14,
) -> list[CircadianBaseline]:
    start = as_of - timedelta(days=lookback_days)
    profiles = list(iter_circadian(start=start, end=as_of))

    hour_data: dict[int, list[float]] = defaultdict(list)
    hour_projects: dict[int, Counter[str]] = defaultdict(Counter)
    hour_modes: dict[int, Counter[str]] = defaultdict(Counter)

    for profile in profiles:
        hour_data[profile.hour].append(profile.active_minutes)
        if profile.dominant_project:
            hour_projects[profile.hour][profile.dominant_project] += 1
        if profile.dominant_mode:
            hour_modes[profile.hour][profile.dominant_mode] += 1

    result: list[CircadianBaseline] = []
    for hour in range(24):
        values = hour_data.get(hour, [0.0])
        average = sum(values) / len(values)
        variance = sum((value - average) ** 2 for value in values) / max(len(values), 1)
        result.append(
            CircadianBaseline(
                hour=hour,
                avg_active_minutes=average,
                stddev_active_minutes=math.sqrt(variance),
                peak_project=hour_projects[hour].most_common(1)[0][0] if hour_projects[hour] else None,
                peak_mode=hour_modes[hour].most_common(1)[0][0] if hour_modes[hour] else None,
            )
        )
    return result
