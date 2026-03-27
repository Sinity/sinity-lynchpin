"""Period summaries derived from context day models."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Sequence

from .summary_models import DaySummary, PeriodSummary


def summarize_period(days: Sequence[DaySummary]) -> PeriodSummary:
    if not days:
        return PeriodSummary(
            start_date="",
            end_date="",
            total_days=0,
            active_seconds=0.0,
            recovery_seconds=0.0,
            chain_count=0,
            signal_count=0,
            command_count=0,
            transcript_count=0,
            commit_count=0,
            dominant_modes=(),
            dominant_projects=(),
            source_counts={},
            coverage={
                "days_with_activitywatch": 0,
                "days_with_terminal": 0,
                "days_with_chatlog": 0,
                "days_with_git": 0,
            },
            highlights=(),
        )

    mode_counter: Counter[str] = Counter()
    project_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    active_seconds = 0.0
    recovery_seconds = 0.0
    chain_count = 0
    signal_count = 0
    command_count = 0
    transcript_count = 0
    commit_count = 0
    coverage = {
        "days_with_activitywatch": 0,
        "days_with_terminal": 0,
        "days_with_chatlog": 0,
        "days_with_git": 0,
    }

    for day in days:
        active_seconds += day.active_seconds
        recovery_seconds += day.recovery_seconds
        chain_count += day.chain_count
        signal_count += day.signal_count
        command_count += day.command_count
        transcript_count += day.transcript_count
        commit_count += day.commit_count
        for mode, seconds in day.top_modes:
            mode_counter[mode] += seconds
        for project, seconds in day.top_projects:
            project_counter[project] += seconds
        for topic, seconds in day.top_topics:
            topic_counter[topic] += seconds
        source_counter.update(day.source_counts)
        if day.coverage.get("has_activitywatch"):
            coverage["days_with_activitywatch"] += 1
        if day.coverage.get("has_terminal"):
            coverage["days_with_terminal"] += 1
        if day.coverage.get("has_chatlog") or day.coverage.get("has_polylogue"):
            coverage["days_with_chatlog"] += 1
        if day.coverage.get("has_git"):
            coverage["days_with_git"] += 1

    dominant_modes = tuple(sorted(mode_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
    dominant_projects = tuple(sorted(project_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
    dominant_topics = tuple(sorted(topic_counter.items(), key=lambda item: (-item[1], item[0]))[:5])
    highlights: list[str] = []
    if dominant_modes:
        highlights.append(f"mode:{dominant_modes[0][0]} {dominant_modes[0][1] / 3600.0:.1f}h")
    if dominant_projects:
        highlights.append(f"project:{dominant_projects[0][0]} {dominant_projects[0][1] / 3600.0:.1f}h")
    if command_count:
        highlights.append(f"commands:{command_count}")
    if commit_count:
        highlights.append(f"commits:{commit_count}")
    if transcript_count:
        highlights.append(f"transcripts:{transcript_count}")

    return PeriodSummary(
        start_date=days[0].date.isoformat(),
        end_date=days[-1].date.isoformat(),
        total_days=len(days),
        active_seconds=round(active_seconds, 3),
        recovery_seconds=round(recovery_seconds, 3),
        chain_count=chain_count,
        signal_count=signal_count,
        command_count=command_count,
        transcript_count=transcript_count,
        commit_count=commit_count,
        dominant_modes=dominant_modes,
        dominant_projects=dominant_projects,
        dominant_topics=dominant_topics,
        source_counts=dict(source_counter),
        coverage=coverage,
        highlights=tuple(highlights[:5]),
    )


def summarize_months(days: Sequence[DaySummary]) -> dict[str, PeriodSummary]:
    grouped: dict[str, list[DaySummary]] = defaultdict(list)
    for day in days:
        grouped[day.date.strftime("%Y-%m")].append(day)
    return {
        month: summarize_period(grouped_days)
        for month, grouped_days in sorted(grouped.items())
    }
