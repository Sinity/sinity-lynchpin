"""Project attention metrics derived from the canonical focus timeline."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator

from ._activitywatch import active_seconds_by_date
from .focus_spans import iter_focus_spans


@dataclass(frozen=True)
class ProjectAttentionMetrics:
    date: date
    entropy: float
    gini: float
    top_project: str
    top_project_share: float
    project_count: int
    rotation_speed: float
    new_projects: tuple[str, ...]
    dropped_projects: tuple[str, ...]


def _gini(values: list[float]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    sorted_values = sorted(values)
    total = sum(sorted_values)
    count = len(sorted_values)
    weighted = sum((index + 1) * value for index, value in enumerate(sorted_values))
    return (2 * weighted) / (count * total) - (count + 1) / count


def iter_project_attention(
    *,
    start: date,
    end: date,
) -> Iterator[ProjectAttentionMetrics]:
    start_dt = datetime.combine(start - timedelta(days=7), datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    spans = list(
        iter_focus_spans(
            start=start_dt,
            end=end_dt,
            min_duration_seconds=60,
            include_keyboard=False,
        )
    )

    duration_by_day: dict[date, dict[str, float]] = {}
    for span in spans:
        if span.span_kind != "focused" or not span.project:
            continue
        day_bucket = duration_by_day.setdefault(span.start.date(), {})
        day_bucket[span.project] = day_bucket.get(span.project, 0.0) + span.duration_seconds

    active_hours = {
        day: seconds / 3600.0
        for day, seconds in active_seconds_by_date(start=start, end=end).items()
    }

    prior_projects: dict[date, set[str]] = {}
    for offset in range(7, 0, -1):
        prior_day = start - timedelta(days=offset)
        prior_projects[prior_day] = set(duration_by_day.get(prior_day, {}))

    d = start
    while d <= end:
        project_seconds = duration_by_day.get(d, {})
        if not project_seconds:
            d += timedelta(days=1)
            continue

        total_seconds = sum(project_seconds.values())
        fractions = [seconds / total_seconds for seconds in project_seconds.values()]
        sorted_projects = sorted(project_seconds.items(), key=lambda item: (-item[1], item[0]))
        top_project, top_seconds = sorted_projects[0]

        prior_union = set().union(*prior_projects.values()) if prior_projects else set()
        prior_non_empty = [projects for projects in prior_projects.values() if projects]
        prior_intersection = set.intersection(*prior_non_empty) if prior_non_empty else set()
        today_projects = set(project_seconds)

        yield ProjectAttentionMetrics(
            date=d,
            entropy=-sum(fraction * math.log2(fraction) for fraction in fractions if fraction > 0),
            gini=_gini(list(project_seconds.values())),
            top_project=top_project,
            top_project_share=top_seconds / total_seconds,
            project_count=len(project_seconds),
            rotation_speed=len(project_seconds) / max(active_hours.get(d, 0.0), 0.1),
            new_projects=tuple(sorted(today_projects - prior_union)),
            dropped_projects=tuple(sorted(prior_intersection - today_projects)),
        )

        prior_projects[d] = today_projects
        while len(prior_projects) > 7:
            oldest = min(prior_projects)
            del prior_projects[oldest]
        d += timedelta(days=1)
