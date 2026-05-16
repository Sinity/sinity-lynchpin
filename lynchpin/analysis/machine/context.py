"""Join machine-state episodes onto bounded work/activity windows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from lynchpin.analysis.core.io import save_json
from lynchpin.analysis.machine.episodes import MachineEpisode, analyze_machine_episodes
from lynchpin.core.parse import as_local


@dataclass(frozen=True)
class WorkloadWindow:
    source: str
    window_id: str
    started_at: datetime
    ended_at: datetime
    projects: tuple[str, ...]
    provider: str | None
    work_kind: str | None
    summary: str
    payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class MachineEpisodeOverlap:
    kind: str
    host: str
    started_at: datetime
    ended_at: datetime
    overlap_seconds: float
    severity: float
    confidence: float
    subject: str | None
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MachineContextWindow:
    source: str
    window_id: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    projects: tuple[str, ...]
    provider: str | None
    work_kind: str | None
    summary: str
    overlap_seconds: float
    episode_count: int
    episodes: tuple[MachineEpisodeOverlap, ...]
    interpretation: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class MachineContextAnalysis:
    window_count: int
    windows_with_machine_episodes: int
    source_counts: dict[str, int]
    episode_kind_counts: dict[str, int]
    windows: list[MachineContextWindow]
    caveats: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_machine_context_windows(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    windows: Iterable[WorkloadWindow] | None = None,
    max_windows: int = 500,
) -> MachineContextAnalysis:
    """Overlay typed machine episodes onto development/activity windows.

    This is observational context only. It preserves the source window and
    episode dimensions so downstream analyses can decide whether the evidence
    supports troubleshooting, experiment, or narrative use.
    """
    episode_analysis = analyze_machine_episodes(start=start, end=end, path=path)
    bounded_start, bounded_end = _analysis_dates(start, end, episode_analysis.coverage.first_observed_at, episode_analysis.coverage.last_observed_at)
    caveats = list(episode_analysis.caveats)
    if windows is None:
        workload_windows, source_caveats = _collect_workload_windows(start=bounded_start, end=bounded_end)
        caveats.extend(source_caveats)
    else:
        workload_windows = list(windows)

    joined = _join_windows(workload_windows, episode_analysis.episodes)
    joined.sort(key=lambda row: (row.started_at, row.source, row.window_id))
    if max_windows > 0 and len(joined) > max_windows:
        joined = joined[-max_windows:]
        caveats.append(f"machine context windows truncated to latest {max_windows} rows")

    return MachineContextAnalysis(
        window_count=len(joined),
        windows_with_machine_episodes=sum(1 for row in joined if row.episode_count),
        source_counts=_source_counts(joined),
        episode_kind_counts=_episode_kind_counts(joined),
        windows=joined,
        caveats=sorted(dict.fromkeys(caveats)),
    )


def write_machine_context_analysis(
    out: Path,
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    max_windows: int = 500,
) -> MachineContextAnalysis:
    analysis = analyze_machine_context_windows(start=start, end=end, path=path, max_windows=max_windows)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), **analysis.to_dict()}
    save_json(out, json.loads(json.dumps(payload, default=str)), sort_keys=True)
    return analysis


def _analysis_dates(
    start: date | None,
    end: date | None,
    first_observed_at: datetime | None,
    last_observed_at: datetime | None,
) -> tuple[date, date]:
    if start is not None and end is not None:
        return start, end
    if first_observed_at is not None and last_observed_at is not None:
        return start or first_observed_at.date(), end or last_observed_at.date()
    today = datetime.now(timezone.utc).date()
    return start or today, end or today


def _collect_workload_windows(*, start: date, end: date) -> tuple[list[WorkloadWindow], list[str]]:
    windows: list[WorkloadWindow] = []
    caveats: list[str] = []
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)

    for collector in (
        _polylogue_windows,
        _terminal_windows,
        _git_windows,
        _deep_work_windows,
    ):
        try:
            windows.extend(collector(start=start, end=end, start_dt=start_dt, end_dt=end_dt))
        except Exception as exc:
            caveats.append(f"{collector.__name__.removeprefix('_').removesuffix('_windows')} windows unavailable: {exc}")

    if not windows:
        caveats.append("no workload windows found for machine context join")
    return windows, caveats


def _polylogue_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime) -> list[WorkloadWindow]:
    del start_dt, end_dt
    from lynchpin.sources.polylogue import session_profiles_for_date

    result: list[WorkloadWindow] = []
    for profile in session_profiles_for_date(start=start, end=end):
        win_start = profile.first_message_at
        win_end = profile.last_message_at
        if win_start is None or win_end is None or win_end < win_start:
            continue
        result.append(WorkloadWindow(
            source="polylogue_session",
            window_id=profile.conversation_id,
            started_at=win_start,
            ended_at=win_end,
            projects=profile.work_event_projects,
            provider=profile.provider,
            work_kind=profile.work_event_kind,
            summary=profile.title,
            payload={
                "message_count": profile.message_count,
                "tool_use_count": profile.tool_use_count,
                "work_event_count": profile.work_event_count,
            },
        ))
    return result


def _terminal_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime) -> list[WorkloadWindow]:
    del start, end
    from lynchpin.sources.terminal import shell_sessions

    result: list[WorkloadWindow] = []
    for idx, session in enumerate(shell_sessions(start=start_dt, end=end_dt)):
        result.append(WorkloadWindow(
            source="terminal_session",
            window_id=f"{session.start.isoformat()}:{idx}",
            started_at=session.start,
            ended_at=session.end,
            projects=tuple(p for p in (session.project,) if p),
            provider=None,
            work_kind=session.category,
            summary=" ".join(session.commands_summary),
            payload={
                "cwd": session.cwd,
                "command_count": session.command_count,
                "error_count": session.error_count,
            },
        ))
    return result


def _git_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime) -> list[WorkloadWindow]:
    del start_dt, end_dt
    from lynchpin.sources.git import commit_sessions

    result: list[WorkloadWindow] = []
    for idx, session in enumerate(commit_sessions(start=start, end=end)):
        result.append(WorkloadWindow(
            source="git_commit_session",
            window_id=f"{session.repo}:{session.start.isoformat()}:{idx}",
            started_at=session.start,
            ended_at=session.end,
            projects=(session.repo,),
            provider=None,
            work_kind="commit_burst" if session.is_burst else "commit_session",
            summary=f"{session.commit_count} commits, {session.lines_changed} changed lines",
            payload={
                "commit_count": session.commit_count,
                "ai_fraction": round(session.ai_fraction, 4),
                "lines_changed": session.lines_changed,
            },
        ))
    return result


def _deep_work_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime) -> list[WorkloadWindow]:
    del start, end
    from lynchpin.sources.activitywatch import deep_work

    result: list[WorkloadWindow] = []
    for idx, block in enumerate(deep_work(start=start_dt, end=end_dt)):
        result.append(WorkloadWindow(
            source="activitywatch_deep_work",
            window_id=f"{block.start.isoformat()}:{idx}",
            started_at=block.start,
            ended_at=block.end,
            projects=tuple(p for p in (block.project,) if p),
            provider=None,
            work_kind=block.mode,
            summary=f"{block.duration_min} min deep work",
            payload={
                "focus_ratio": block.focus_ratio,
                "app_switches": block.app_switches,
            },
        ))
    return result


def _join_windows(windows: Iterable[WorkloadWindow], episodes: Iterable[MachineEpisode]) -> list[MachineContextWindow]:
    episode_rows = list(episodes)
    result: list[MachineContextWindow] = []
    for window in windows:
        overlaps = tuple(
            sorted(
                (
                    _episode_overlap(window, episode)
                    for episode in episode_rows
                    if _overlap_seconds(window.started_at, window.ended_at, episode.started_at, episode.ended_at) > 0
                ),
                key=lambda row: (-row.overlap_seconds, -row.severity, row.kind, row.host),
            )
        )
        duration = _duration_seconds(window.started_at, window.ended_at)
        overlap_seconds = round(_union_overlap_seconds(window, episode_rows), 3)
        caveats = _window_caveats(window, overlaps)
        result.append(MachineContextWindow(
            source=window.source,
            window_id=window.window_id,
            started_at=window.started_at,
            ended_at=window.ended_at,
            duration_seconds=round(duration, 3),
            projects=window.projects,
            provider=window.provider,
            work_kind=window.work_kind,
            summary=window.summary,
            overlap_seconds=overlap_seconds,
            episode_count=len(overlaps),
            episodes=overlaps,
            interpretation=_interpretation(overlaps),
            caveats=caveats,
        ))
    return result


def _episode_overlap(window: WorkloadWindow, episode: MachineEpisode) -> MachineEpisodeOverlap:
    return MachineEpisodeOverlap(
        kind=episode.kind,
        host=episode.host,
        started_at=episode.started_at,
        ended_at=episode.ended_at,
        overlap_seconds=round(_overlap_seconds(window.started_at, window.ended_at, episode.started_at, episode.ended_at), 3),
        severity=episode.severity,
        confidence=episode.confidence,
        subject=episode.subject,
        sources=episode.sources,
    )


def _union_overlap_seconds(window: WorkloadWindow, episodes: Iterable[MachineEpisode]) -> float:
    intervals: list[tuple[datetime, datetime]] = []
    window_start = _aware(window.started_at)
    window_end = _aware(window.ended_at)
    for episode in episodes:
        left = max(window_start, _aware(episode.started_at))
        right = min(window_end, _aware(episode.ended_at))
        if right > left:
            intervals.append((left, right))
    if not intervals:
        return 0.0
    intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for left, right in intervals:
        if not merged or left > merged[-1][1]:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
    return sum((right - left).total_seconds() for left, right in merged)


def _window_caveats(window: WorkloadWindow, overlaps: tuple[MachineEpisodeOverlap, ...]) -> tuple[str, ...]:
    caveats: list[str] = []
    if not window.projects:
        caveats.append("workload window has no project attribution")
    if not overlaps:
        caveats.append("no detected machine episode overlaps this window")
    else:
        caveats.append("episode overlap is observational unless joined to a manifest-backed experiment run")
    if any(row.kind in {"load_pressure", "memory_pressure", "io_pressure", "blocked_task_pressure"} for row in overlaps):
        caveats.append("pressure episode is unattributed without a bounded below/process window")
    return tuple(caveats)


def _interpretation(overlaps: tuple[MachineEpisodeOverlap, ...]) -> str:
    if not overlaps:
        return "no detected machine episode overlap"
    kinds = sorted({row.kind for row in overlaps})
    return "observed overlap with " + ", ".join(kinds)


def _source_counts(windows: Iterable[MachineContextWindow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for window in windows:
        counts[window.source] = counts.get(window.source, 0) + 1
    return dict(sorted(counts.items()))


def _episode_kind_counts(windows: Iterable[MachineContextWindow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for window in windows:
        for episode in window.episodes:
            counts[episode.kind] = counts.get(episode.kind, 0) + 1
    return dict(sorted(counts.items()))


def _overlap_seconds(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    left = max(_aware(a_start), _aware(b_start))
    right = min(_aware(a_end), _aware(b_end))
    return max(0.0, (right - left).total_seconds())


def _duration_seconds(started_at: datetime, ended_at: datetime) -> float:
    return max(0.0, (_aware(ended_at) - _aware(started_at)).total_seconds())


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return as_local(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)
