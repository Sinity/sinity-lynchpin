"""Deep work detection: sustained same-context focus blocks from app sessions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator, Sequence

from .app_sessions import AppSession, iter_app_sessions
from .git_commit_facts import GitCommitFact, iter_git_commit_facts
from .shell_sessions import ShellSession, iter_shell_sessions

_PRODUCTIVE_MODES = {"coding", "research", "writing", "planning", "chat"}
_MAX_BLOCK_GAP_SECONDS = 600
_MAX_INTERRUPTION_SECONDS = 300


@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_minutes: float
    project: str | None
    mode: str
    app_switches: int
    git_lines_changed: int
    git_files_changed: int
    command_count: int
    interruption_minutes: float
    focus_ratio: float


def iter_deep_work(
    *,
    start: datetime,
    end: datetime,
    min_duration_minutes: float = 30,
    max_interruption_ratio: float = 0.15,
) -> Iterator[DeepWorkBlock]:
    """Yield per-day deep-work blocks from AFK-trimmed app sessions."""
    app_sessions = list(
        iter_app_sessions(
            start=start,
            end=end,
            min_duration_seconds=60,
        )
    )
    if not app_sessions:
        return

    shell_sessions = list(iter_shell_sessions(start=start, end=end))
    shell_by_day: dict[date, list[ShellSession]] = defaultdict(list)
    for session in shell_sessions:
        shell_by_day[session.start.date()].append(session)

    git_facts_by_day: dict[date, list[GitCommitFact]] = defaultdict(list)
    for fact in iter_git_commit_facts(start=start.date(), end=end.date()):
        git_facts_by_day[fact.authored_at.date()].append(fact)

    sessions_by_day: dict[date, list[AppSession]] = defaultdict(list)
    for session in app_sessions:
        sessions_by_day[session.start.date()].append(session)

    for day in sorted(sessions_by_day):
        for block in _merge_day_blocks(
            sessions=sessions_by_day[day],
            shell_sessions=shell_by_day.get(day, ()),
            git_facts=git_facts_by_day.get(day, ()),
            min_duration_minutes=min_duration_minutes,
            max_interruption_ratio=max_interruption_ratio,
        ):
            yield block


def _merge_day_blocks(
    *,
    sessions: Sequence[AppSession],
    shell_sessions: Sequence[ShellSession],
    git_facts: Sequence[GitCommitFact],
    min_duration_minutes: float,
    max_interruption_ratio: float,
) -> Iterator[DeepWorkBlock]:
    ordered = sorted(sessions, key=lambda session: (session.start, session.end, session.app))
    acc: _BlockAccumulator | None = None

    for session in ordered:
        productive = _is_productive(session)
        if acc is None:
            if productive:
                acc = _BlockAccumulator(session)
            continue

        gap_seconds = max((session.start - acc.end).total_seconds(), 0.0)
        if gap_seconds > _MAX_BLOCK_GAP_SECONDS:
            block = acc.finalize(shell_sessions=shell_sessions, git_facts=git_facts)
            if _passes_filter(block, min_duration_minutes, max_interruption_ratio):
                yield block
            acc = _BlockAccumulator(session) if productive else None
            continue

        if productive and _compatible(acc, session):
            if gap_seconds > 0:
                acc.interruption_seconds += gap_seconds
                acc.end = max(acc.end, session.start)
            acc.add_session(session)
            continue

        if gap_seconds + session.duration_seconds <= _MAX_INTERRUPTION_SECONDS:
            acc.note_interruption(session, gap_seconds)
            continue

        block = acc.finalize(shell_sessions=shell_sessions, git_facts=git_facts)
        if _passes_filter(block, min_duration_minutes, max_interruption_ratio):
            yield block
        acc = _BlockAccumulator(session) if productive else None

    if acc is not None:
        block = acc.finalize(shell_sessions=shell_sessions, git_facts=git_facts)
        if _passes_filter(block, min_duration_minutes, max_interruption_ratio):
            yield block


def _is_productive(session: AppSession) -> bool:
    return bool(session.project) or (session.mode or "") in _PRODUCTIVE_MODES


def _compatible(acc: "_BlockAccumulator", session: AppSession) -> bool:
    if acc.dominant_project and session.project:
        return acc.dominant_project == session.project
    if acc.dominant_mode and session.mode:
        return acc.dominant_mode == session.mode and acc.dominant_mode in _PRODUCTIVE_MODES
    return False


def _passes_filter(
    block: DeepWorkBlock,
    min_duration_minutes: float,
    max_interruption_ratio: float,
) -> bool:
    if block.duration_minutes < min_duration_minutes:
        return False
    return block.focus_ratio >= (1.0 - max_interruption_ratio)


class _BlockAccumulator:
    __slots__ = (
        "start",
        "end",
        "sessions",
        "interruption_seconds",
        "mode_durations",
        "project_durations",
        "productive_seconds",
    )

    def __init__(self, session: AppSession) -> None:
        self.start = session.start
        self.end = session.end
        self.sessions: list[AppSession] = [session]
        self.interruption_seconds = 0.0
        self.mode_durations: Counter[str] = Counter()
        self.project_durations: Counter[str] = Counter()
        self.productive_seconds = 0.0
        self._record_session(session)

    @property
    def dominant_mode(self) -> str | None:
        return self.mode_durations.most_common(1)[0][0] if self.mode_durations else None

    @property
    def dominant_project(self) -> str | None:
        return self.project_durations.most_common(1)[0][0] if self.project_durations else None

    def add_session(self, session: AppSession) -> None:
        self.sessions.append(session)
        self.end = max(self.end, session.end)
        self._record_session(session)

    def note_interruption(self, session: AppSession, gap_seconds: float) -> None:
        self.interruption_seconds += max(gap_seconds, 0.0) + session.duration_seconds
        self.end = max(self.end, session.end)

    def finalize(
        self,
        *,
        shell_sessions: Sequence[ShellSession],
        git_facts: Sequence[GitCommitFact],
    ) -> DeepWorkBlock:
        wall_seconds = max((self.end - self.start).total_seconds(), 0.0)
        duration_minutes = wall_seconds / 60.0
        focus_ratio = (
            max(0.0, min(1.0, self.productive_seconds / wall_seconds))
            if wall_seconds > 0
            else 0.0
        )

        command_count = sum(
            session.command_count
            for session in shell_sessions
            if _overlaps(self.start, self.end, session.start, session.end)
        )
        overlapping_git_facts = [
            fact
            for fact in git_facts
            if _contains_timestamp(self.start, self.end, fact.authored_at)
        ]
        git_lines_changed = sum(fact.lines_changed for fact in overlapping_git_facts)
        git_files_changed = sum(fact.files_changed for fact in overlapping_git_facts)
        app_switches = sum(
            1
            for left, right in zip(self.sessions, self.sessions[1:])
            if left.app != right.app
        )

        return DeepWorkBlock(
            start=self.start,
            end=self.end,
            duration_minutes=round(duration_minutes, 1),
            project=self.dominant_project,
            mode=self.dominant_mode or "unknown",
            app_switches=app_switches,
            git_lines_changed=git_lines_changed,
            git_files_changed=git_files_changed,
            command_count=command_count,
            interruption_minutes=round(self.interruption_seconds / 60.0, 1),
            focus_ratio=round(focus_ratio, 3),
        )

    def _record_session(self, session: AppSession) -> None:
        self.productive_seconds += session.duration_seconds
        if session.mode:
            self.mode_durations[session.mode] += session.duration_seconds
        if session.project:
            self.project_durations[session.project] += session.duration_seconds


def _overlaps(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> bool:
    return left_start < right_end and right_start < left_end


def _contains_timestamp(
    start: datetime,
    end: datetime,
    timestamp: datetime,
) -> bool:
    return start <= timestamp < end
