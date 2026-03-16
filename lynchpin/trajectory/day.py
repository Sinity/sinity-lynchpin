from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, Optional

from .chains import TrajectoryChain, build_chains
from .signal import TrajectorySignal, load_signals, resolve_window


@dataclass(frozen=True)
class TrajectoryDayProject:
    date: date
    project: str
    duration_seconds: float
    chain_count: int
    top_modes: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "project": self.project,
            "duration_seconds": round(self.duration_seconds, 3),
            "chain_count": self.chain_count,
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
        }


@dataclass(frozen=True)
class TrajectoryDay:
    date: date
    active_seconds: float
    recovery_seconds: float
    chain_count: int
    signal_count: int
    command_count: int
    transcript_count: int
    commit_count: int
    dominant_mode: Optional[str]
    dominant_project: Optional[str]
    top_modes: tuple[tuple[str, float], ...]
    top_projects: tuple[tuple[str, float], ...]
    source_counts: dict[str, int]
    coverage: dict[str, object]
    highlights: tuple[str, ...]
    projects: tuple[TrajectoryDayProject, ...]

    @property
    def observed_seconds(self) -> float:
        return self.active_seconds + self.recovery_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "active_seconds": round(self.active_seconds, 3),
            "recovery_seconds": round(self.recovery_seconds, 3),
            "observed_seconds": round(self.observed_seconds, 3),
            "chain_count": self.chain_count,
            "signal_count": self.signal_count,
            "command_count": self.command_count,
            "transcript_count": self.transcript_count,
            "commit_count": self.commit_count,
            "dominant_mode": self.dominant_mode,
            "dominant_project": self.dominant_project,
            "top_modes": [[mode, round(seconds, 3)] for mode, seconds in self.top_modes],
            "top_projects": [[project, round(seconds, 3)] for project, seconds in self.top_projects],
            "source_counts": self.source_counts,
            "coverage": self.coverage,
            "highlights": list(self.highlights),
            "projects": [project.to_dict() for project in self.projects],
        }


def summarize_days(
    *,
    signals: Optional[Iterable[TrajectorySignal]] = None,
    chains: Optional[Iterable[TrajectoryChain]] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: int = 14,
) -> list[TrajectoryDay]:
    window_start, window_end = resolve_window(start=start, end=end, days=days)
    signal_list = list(signals) if signals is not None else load_signals(start=window_start, end=window_end, days=days)
    chain_list = list(chains) if chains is not None else build_chains(signal_list)
    dates = _date_range(window_start.date(), (window_end - timedelta(microseconds=1)).date())

    signal_counts = {target: 0 for target in dates}
    command_counts = {target: 0 for target in dates}
    transcript_counts = {target: 0 for target in dates}
    commit_counts = {target: 0 for target in dates}
    source_counts: dict[date, Counter[str]] = {target: Counter() for target in dates}
    mode_seconds: dict[date, Counter[str]] = {target: Counter() for target in dates}
    project_seconds: dict[date, Counter[str]] = {target: Counter() for target in dates}
    active_seconds = {target: 0.0 for target in dates}
    recovery_seconds = {target: 0.0 for target in dates}
    chain_ids: dict[date, set[str]] = {target: set() for target in dates}
    day_project_chain_ids: dict[date, dict[str, set[str]]] = {target: defaultdict(set) for target in dates}
    day_project_modes: dict[date, dict[str, Counter[str]]] = {target: defaultdict(Counter) for target in dates}

    for signal in signal_list:
        target = signal.start.date()
        if target not in signal_counts:
            continue
        signal_counts[target] += 1
        source_counts[target][signal.source] += 1
        if signal.source == "atuin.command":
            command_counts[target] += 1
        elif signal.source == "chatlog.transcript":
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
            if chain.mode == "recovery":
                recovery_seconds[target] += seconds
            else:
                active_seconds[target] += seconds

    summaries: list[TrajectoryDay] = []
    for target in dates:
        top_modes = tuple(sorted(mode_seconds[target].items(), key=lambda item: (-item[1], item[0]))[:5])
        top_projects = tuple(sorted(project_seconds[target].items(), key=lambda item: (-item[1], item[0]))[:5])
        dominant_mode = top_modes[0][0] if top_modes else None
        dominant_project = top_projects[0][0] if top_projects else None
        coverage = {
            "has_activitywatch": any(source.startswith("activitywatch.") for source in source_counts[target]),
            "has_terminal": any(source.startswith("instrumentation.") for source in source_counts[target]),
            "has_chatlog": "chatlog.transcript" in source_counts[target],
            "has_git": "git.commit" in source_counts[target],
            "observed_hours": round((active_seconds[target] + recovery_seconds[target]) / 3600.0, 2),
            "sources": sorted(source_counts[target]),
        }
        summaries.append(
            TrajectoryDay(
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
                top_modes=top_modes,
                top_projects=top_projects,
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
                projects=tuple(
                    TrajectoryDayProject(
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
                ),
            )
        )
    return summaries


def _highlights(
    *,
    dominant_mode: Optional[str],
    dominant_project: Optional[str],
    top_modes: tuple[tuple[str, float], ...],
    top_projects: tuple[tuple[str, float], ...],
    command_count: int,
    transcript_count: int,
    commit_count: int,
) -> tuple[str, ...]:
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
    return tuple(highlights[:5])


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
    dates: list[date] = []
    while current <= end:
        dates.append(current)
        current += timedelta(days=1)
    return dates
