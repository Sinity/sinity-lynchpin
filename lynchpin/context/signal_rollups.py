"""Signal-derived day summaries owned by the context layer."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from typing import Iterable

from ..signals import ActivitySignal, load_signals, resolve_window
from ..signals.chains import ActivityChain, build_chains
from .signal_coverage import compute_coverage
from .summary_models import DayProjectSummary, DaySummary


def summarize_days(
    *,
    signals: Iterable[ActivitySignal] | None = None,
    chains: Iterable[ActivityChain] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    days: int = 14,
) -> list[DaySummary]:
    window_start, window_end = resolve_window(start=start, end=end, days=days)
    signal_list = list(signals) if signals is not None else load_signals(start=window_start, end=window_end, days=days)
    chain_list = list(chains) if chains is not None else build_chains(signal_list)
    target_dates = _date_range(window_start.date(), (window_end - timedelta(microseconds=1)).date())

    signal_counts = {target: 0 for target in target_dates}
    command_counts = {target: 0 for target in target_dates}
    transcript_counts = {target: 0 for target in target_dates}
    commit_counts = {target: 0 for target in target_dates}
    source_counts: dict[date, Counter[str]] = {target: Counter() for target in target_dates}
    mode_seconds: dict[date, Counter[str]] = {target: Counter() for target in target_dates}
    project_seconds: dict[date, Counter[str]] = {target: Counter() for target in target_dates}
    topic_seconds: dict[date, Counter[str]] = {target: Counter() for target in target_dates}
    active_seconds = {target: 0.0 for target in target_dates}
    recovery_seconds = {target: 0.0 for target in target_dates}
    chain_ids: dict[date, set[str]] = {target: set() for target in target_dates}
    day_project_chain_ids: dict[date, dict[str, set[str]]] = {target: defaultdict(set) for target in target_dates}
    day_project_modes: dict[date, dict[str, Counter[str]]] = {target: defaultdict(Counter) for target in target_dates}

    for signal in signal_list:
        target = signal.start.date()
        if target not in signal_counts:
            continue
        signal_counts[target] += 1
        source_counts[target][signal.source] += 1
        if signal.source == "atuin.command":
            command_counts[target] += 1
        elif signal.source in {"chatlog.transcript", "polylogue.session"}:
            transcript_counts[target] += 1
        elif signal.source == "git.commit":
            commit_counts[target] += 1

    for chain in chain_list:
        for target, seconds in _split_span_by_day(chain.start, chain.end):
            if target not in mode_seconds:
                continue
            chain_ids[target].add(chain.chain_id)
            mode_seconds[target][chain.mode] += seconds
            if chain.project:
                project_seconds[target][chain.project] += seconds
                day_project_chain_ids[target][chain.project].add(chain.chain_id)
                day_project_modes[target][chain.project][chain.mode] += seconds
            if chain.topic:
                topic_seconds[target][chain.topic] += seconds
            if chain.mode == "recovery":
                recovery_seconds[target] += seconds
            else:
                active_seconds[target] += seconds

    summaries: list[DaySummary] = []
    for target in target_dates:
        top_modes = tuple(sorted(mode_seconds[target].items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_seconds[target].items(), key=lambda item: (-item[1], item[0]))[:5])
        top_topics = tuple(sorted(topic_seconds[target].items(), key=lambda item: (-item[1], item[0]))[:5])
        dominant_mode = top_modes[0][0] if top_modes else None
        dominant_project = top_projects[0][0] if top_projects else None
        dominant_topic = top_topics[0][0] if top_topics else None
        coverage = {
            "has_activitywatch": any(source.startswith("activitywatch.") for source in source_counts[target]),
            "has_terminal": any(source.startswith("instrumentation.") for source in source_counts[target]),
            "has_chatlog": bool(
                source_counts[target].get("chatlog.transcript")
                or source_counts[target].get("polylogue.session")
            ),
            "has_polylogue": bool(
                source_counts[target].get("chatlog.transcript")
                or source_counts[target].get("polylogue.session")
            ),
            "has_git": bool(source_counts[target].get("git.commit")),
            "observed_hours": round((active_seconds[target] + recovery_seconds[target]) / 3600.0, 2),
            "sources": sorted(source_counts[target]),
        }
        project_summaries = tuple(
            DayProjectSummary(
                date=target,
                project=project,
                duration_seconds=round(seconds, 3),
                chain_count=len(day_project_chain_ids[target][project]),
                top_modes=tuple(
                    sorted(
                        day_project_modes[target][project].items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:3]
                ),
            )
            for project, seconds in top_projects
        )
        day = DaySummary(
            date=target,
            active_seconds=round(active_seconds[target], 3),
            recovery_seconds=round(recovery_seconds[target], 3),
            chain_count=len(chain_ids[target]),
            signal_count=signal_counts[target],
            command_count=command_counts[target],
            transcript_count=transcript_counts[target],
            commit_count=commit_counts[target],
            dominant_mode=dominant_mode,
            dominant_project=dominant_project,
            dominant_topic=dominant_topic,
            top_modes=top_modes,
            top_projects=top_projects,
            top_topics=top_topics,
            source_counts=dict(source_counts[target]),
            coverage=coverage,
            highlights=_highlights(
                dominant_mode=dominant_mode,
                dominant_project=dominant_project,
                top_modes=top_modes,
                top_projects=top_projects,
                command_count=command_counts[target],
                transcript_count=transcript_counts[target],
                commit_count=commit_counts[target],
            ),
            projects=tuple(project for project, _seconds in top_projects),
            project_summaries=project_summaries,
        )
        summaries.append(
            DaySummary(
                date=day.date,
                active_seconds=day.active_seconds,
                recovery_seconds=day.recovery_seconds,
                chain_count=day.chain_count,
                signal_count=day.signal_count,
                command_count=day.command_count,
                transcript_count=day.transcript_count,
                commit_count=day.commit_count,
                dominant_mode=day.dominant_mode,
                dominant_project=day.dominant_project,
                dominant_topic=day.dominant_topic,
                top_modes=day.top_modes,
                top_projects=day.top_projects,
                top_topics=day.top_topics,
                source_counts=day.source_counts,
                coverage=day.coverage,
                highlights=day.highlights,
                projects=day.projects,
                project_summaries=day.project_summaries,
                chat_session_count=day.chat_session_count,
                chat_work_events=day.chat_work_events,
                chat_cost_usd=day.chat_cost_usd,
                signal_coverage=compute_coverage(day),
            )
        )
    return summaries


def _highlights(
    *,
    dominant_mode: str | None,
    dominant_project: str | None,
    top_modes: tuple[tuple[str, float], ...],
    top_projects: tuple[tuple[str, float], ...],
    command_count: int,
    transcript_count: int,
    commit_count: int,
) -> list[str]:
    highlights: list[str] = []
    if dominant_mode and top_modes:
        highlights.append(f"mode:{dominant_mode} {top_modes[0][1] / 3600.0:.1f}h")
    if dominant_project and top_projects:
        highlights.append(f"project:{dominant_project} {top_projects[0][1] / 3600.0:.1f}h")
    if command_count:
        highlights.append(f"commands:{command_count}")
    if commit_count:
        highlights.append(f"commits:{commit_count}")
    if transcript_count:
        highlights.append(f"transcripts:{transcript_count}")
    return highlights[:5]


def _split_span_by_day(start: datetime, end: datetime) -> list[tuple[date, float]]:
    if end <= start:
        return []
    cursor = start
    segments: list[tuple[date, float]] = []
    while cursor < end:
        day_end = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo)
        segment_end = min(end, day_end)
        segments.append((cursor.date(), max((segment_end - cursor).total_seconds(), 0.0)))
        cursor = segment_end
    return segments


def _date_range(start: date, end: date) -> list[date]:
    current = start
    target_dates: list[date] = []
    while current <= end:
        target_dates.append(current)
        current += timedelta(days=1)
    return target_dates
