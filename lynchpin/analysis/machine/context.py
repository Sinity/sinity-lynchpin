"""Join machine-state episodes onto bounded work/activity windows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable

from lynchpin.core.io import save_json
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


@dataclass(frozen=True)
class _EpisodeBounds:
    episode: MachineEpisode
    started_at: datetime
    ended_at: datetime


def analyze_machine_context_windows(
    *,
    start: date | None = None,
    end: date | None = None,
    path: Path | None = None,
    windows: Iterable[WorkloadWindow] | None = None,
    max_windows: int = 500,
    include_polylogue: bool = False,
    include_ambient_sources: bool = False,
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
        workload_windows, source_caveats = _collect_workload_windows(
            start=bounded_start,
            end=bounded_end,
            path=path,
            include_polylogue=include_polylogue,
            include_ambient_sources=include_ambient_sources,
        )
        caveats.extend(source_caveats)
    else:
        workload_windows = list(windows)

    workload_windows.sort(key=lambda row: (row.started_at, row.source, row.window_id))
    if max_windows > 0 and len(workload_windows) > max_windows:
        workload_windows = workload_windows[-max_windows:]
        caveats.append(f"machine context windows truncated to latest {max_windows} rows")

    joined = _join_windows(workload_windows, episode_analysis.episodes)
    joined.sort(key=lambda row: (row.started_at, row.source, row.window_id))

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
    include_polylogue: bool = False,
    include_ambient_sources: bool = False,
) -> MachineContextAnalysis:
    analysis = analyze_machine_context_windows(
        start=start,
        end=end,
        path=path,
        max_windows=max_windows,
        include_polylogue=include_polylogue,
        include_ambient_sources=include_ambient_sources,
    )
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


def _collect_workload_windows(
    *,
    start: date,
    end: date,
    path: Path | None = None,
    include_polylogue: bool = False,
    include_ambient_sources: bool = False,
) -> tuple[list[WorkloadWindow], list[str]]:
    windows: list[WorkloadWindow] = []
    caveats: list[str] = []
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc)

    collectors = [_work_observation_windows]
    if include_ambient_sources:
        collectors.extend([_terminal_windows, _git_windows, _deep_work_windows])
    else:
        caveats.append(
            "ambient terminal/git/ActivityWatch windows skipped by default; pass include_ambient_sources=True to opt in"
        )
    if include_polylogue:
        collectors.insert(1, _polylogue_windows)
    else:
        caveats.append("polylogue session windows skipped by default; pass include_polylogue=True to opt in")

    for collector in collectors:
        try:
            windows.extend(collector(start=start, end=end, start_dt=start_dt, end_dt=end_dt, path=path))
        except Exception as exc:
            caveats.append(f"{collector.__name__.removeprefix('_').removesuffix('_windows')} windows unavailable: {exc}")

    if not windows:
        caveats.append("no workload windows found for machine context join")
    return windows, caveats


def _work_observation_windows(
    *,
    start: date,
    end: date,
    start_dt: datetime,
    end_dt: datetime,
    path: Path | None = None,
) -> list[WorkloadWindow]:
    del start, end
    from lynchpin.analysis.machine.sql import latest_machine_rows
    from lynchpin.substrate.connection import connect, substrate_path

    sql = f"""
        SELECT source, source_id, work_kind, project, command, cwd, started_at, ended_at,
               duration_s, status, exit_code, host, git_commit, git_dirty,
               live_stage, args
        FROM ({latest_machine_rows("work_observation")})
        WHERE started_at >= ? AND started_at <= ?
        ORDER BY started_at, source_id
    """
    result: list[WorkloadWindow] = []
    with connect(path or substrate_path(), read_only=True) as conn:
        rows = conn.execute(sql, [start_dt, end_dt]).fetchall()
    for row in rows:
        ended_at = row[7]
        if ended_at is None:
            duration_s = float(row[8] or 0.0)
            ended_at = row[6] if duration_s <= 0 else row[6] + timedelta(seconds=duration_s)
        command = tuple(str(part) for part in (row[4] or ()))
        summary = " ".join(command) if command else str(row[0])
        if row[9] and row[9] != "success":
            summary = f"{summary} ({row[9]})"
        result.append(WorkloadWindow(
            source=str(row[0]),
            window_id=str(row[1]),
            started_at=row[6],
            ended_at=ended_at,
            projects=tuple(p for p in (row[3],) if p),
            provider=None,
            work_kind=str(row[2]) if row[2] else None,
            summary=summary,
            payload={
                "cwd": row[5],
                "status": row[9],
                "exit_code": row[10],
                "host": row[11],
                "git_commit": row[12],
                "git_dirty": row[13],
                "live_stage": row[14],
                "args": row[15],
            },
        ))
    return result


def _polylogue_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime, path: Path | None = None) -> list[WorkloadWindow]:
    del path
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


def _terminal_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime, path: Path | None = None) -> list[WorkloadWindow]:
    del path
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


def _git_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime, path: Path | None = None) -> list[WorkloadWindow]:
    del path
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


def _deep_work_windows(*, start: date, end: date, start_dt: datetime, end_dt: datetime, path: Path | None = None) -> list[WorkloadWindow]:
    del path
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
    episode_rows = tuple(
        _EpisodeBounds(
            episode=episode,
            started_at=_aware(episode.started_at),
            ended_at=_aware(episode.ended_at),
        )
        for episode in episodes
    )
    result: list[MachineContextWindow] = []
    for window in windows:
        window_start = _aware(window.started_at)
        window_end = _aware(window.ended_at)
        overlap_rows: list[MachineEpisodeOverlap] = []
        intervals: list[tuple[datetime, datetime]] = []
        for row in episode_rows:
            left = max(window_start, row.started_at)
            right = min(window_end, row.ended_at)
            if right <= left:
                continue
            overlap_seconds = round((right - left).total_seconds(), 3)
            intervals.append((left, right))
            overlap_rows.append(_episode_overlap(row.episode, overlap_seconds=overlap_seconds))
        overlaps = tuple(
            sorted(
                overlap_rows,
                key=lambda row: (-row.overlap_seconds, -row.severity, row.kind, row.host),
            )
        )
        duration = max(0.0, (window_end - window_start).total_seconds())
        total_overlap_seconds = round(_merged_interval_seconds(intervals), 3)
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
            overlap_seconds=total_overlap_seconds,
            episode_count=len(overlaps),
            episodes=overlaps,
            interpretation=_interpretation(overlaps),
            caveats=caveats,
        ))
    return result


def _episode_overlap(episode: MachineEpisode, *, overlap_seconds: float) -> MachineEpisodeOverlap:
    return MachineEpisodeOverlap(
        kind=episode.kind,
        host=episode.host,
        started_at=episode.started_at,
        ended_at=episode.ended_at,
        overlap_seconds=overlap_seconds,
        severity=episode.severity,
        confidence=episode.confidence,
        subject=episode.subject,
        sources=episode.sources,
    )


def _merged_interval_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return as_local(value).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)
