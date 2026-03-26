"""Sleep-productivity correlations using processed focus and delivery surfaces."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterator

from ._activitywatch import active_seconds_by_date
from .deep_work import iter_deep_work
from .focus_spans import iter_focus_spans
from .git_commit_facts import iter_git_commit_facts
from ..exports.sleep import iter_sleep


@dataclass(frozen=True)
class SleepProductivityCorrelation:
    sleep_date: date
    sleep_hours: float
    sleep_score: float | None
    sleep_quality: str
    segment_count: int
    workday_active_hours: float
    workday_lines_changed: int
    workday_files_changed: int
    workday_dominant_mode: str | None
    workday_deep_work_minutes: float
    productivity_vs_baseline: float


def iter_sleep_correlations(
    *,
    start: date,
    end: date,
) -> Iterator[SleepProductivityCorrelation]:
    from ...metrics.health import sleep_summary

    sleep_entries: list[tuple[object, date, object | None]] = []
    for entry in iter_sleep():
        day_text = getattr(entry, "date", None)
        if not day_text:
            continue
        try:
            sleep_date = date.fromisoformat(str(day_text))
        except ValueError:
            continue
        if sleep_date < start or sleep_date > end:
            continue
        sleep_entries.append((entry, sleep_date, sleep_summary(entry)))

    if not sleep_entries:
        return

    analysis_days = sorted({sleep_date for _, sleep_date, _ in sleep_entries})
    analysis_start = analysis_days[0]
    analysis_end = analysis_days[-1]

    active_hours = {
        day: seconds / 3600.0
        for day, seconds in active_seconds_by_date(start=analysis_start, end=analysis_end).items()
    }
    baseline_average = sum(active_hours.values()) / len(active_hours) if active_hours else 8.0

    mode_durations_by_day: dict[date, Counter[str]] = defaultdict(Counter)
    focus_start = datetime.combine(analysis_start, time.min)
    focus_end = datetime.combine(analysis_end + timedelta(days=1), time.min)
    for span in iter_focus_spans(
        start=focus_start,
        end=focus_end,
        min_duration_seconds=60,
        include_keyboard=False,
    ):
        if span.span_kind == "focused" and span.mode:
            mode_durations_by_day[span.start.date()][span.mode] += span.duration_seconds

    deep_work_by_day: dict[date, float] = defaultdict(float)
    for block in iter_deep_work(start=focus_start, end=focus_end):
        deep_work_by_day[block.start.date()] += block.duration_minutes

    git_lines_by_day: dict[date, int] = defaultdict(int)
    git_files_by_day: dict[date, int] = defaultdict(int)
    for fact in iter_git_commit_facts(start=analysis_start, end=analysis_end):
        git_lines_by_day[fact.date] += fact.lines_changed
        git_files_by_day[fact.date] += fact.files_changed

    for entry, sleep_date, summary in sleep_entries:
        dominant_mode = (
            mode_durations_by_day[sleep_date].most_common(1)[0][0]
            if mode_durations_by_day.get(sleep_date)
            else None
        )
        workday_active_hours = active_hours.get(sleep_date, 0.0)
        yield SleepProductivityCorrelation(
            sleep_date=sleep_date,
            sleep_hours=(getattr(entry, "total_minutes", 0) or 0) / 60.0,
            sleep_score=getattr(entry, "avg_score", None),
            sleep_quality=summary.quality_label if summary else "unknown",
            segment_count=len(getattr(entry, "segments", ()) or ()),
            workday_active_hours=workday_active_hours,
            workday_lines_changed=git_lines_by_day.get(sleep_date, 0),
            workday_files_changed=git_files_by_day.get(sleep_date, 0),
            workday_dominant_mode=dominant_mode,
            workday_deep_work_minutes=deep_work_by_day.get(sleep_date, 0.0),
            productivity_vs_baseline=workday_active_hours / max(baseline_average, 0.1),
        )
