"""Deep work detection: identify sustained focus blocks from cross-source signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator, Optional

from .app_sessions import iter_app_sessions
from .shell_sessions import iter_shell_sessions
from ..indices.gitstats import iter_commits


@dataclass(frozen=True)
class DeepWorkBlock:
    start: datetime
    end: datetime
    duration_minutes: float
    project: str | None
    mode: str
    app_switches: int
    commit_count: int
    command_count: int
    interruption_minutes: float
    focus_ratio: float  # (duration - interruption) / duration


def iter_deep_work(
    *,
    start: datetime,
    end: datetime,
    min_duration_minutes: float = 30,
    max_interruption_ratio: float = 0.15,
) -> Iterator[DeepWorkBlock]:
    """Yield deep work blocks by merging app sessions, shell sessions, and git commits.

    A deep work block is a continuous stretch where the same project or mode
    persists, with only brief interruptions (<2 min) allowed and gaps >5 min
    breaking the block.
    """
    # ---- collect timeline events ----
    events = _build_timeline(start, end)
    if not events:
        return

    # ---- collect git commits in range for enrichment ----
    commits_in_range = [
        c for c in iter_commits()
        if start.date() <= c.date <= end.date()
    ]

    # ---- merge into candidate blocks ----
    for block in _merge_blocks(events, commits_in_range, min_duration_minutes, max_interruption_ratio):
        yield block


# ---------------------------------------------------------------------------
# Internal types and helpers
# ---------------------------------------------------------------------------

@dataclass
class _TimelineEvent:
    start: datetime
    end: datetime
    project: str | None
    mode: str | None
    app: str | None
    is_shell: bool
    command_count: int


def _build_timeline(start: datetime, end: datetime) -> list[_TimelineEvent]:
    """Merge app sessions and shell sessions into a sorted timeline."""
    events: list[_TimelineEvent] = []

    for app_sess in iter_app_sessions(start=start, end=end, min_duration_seconds=10):
        events.append(_TimelineEvent(
            start=app_sess.start,
            end=app_sess.end,
            project=app_sess.project,
            mode=app_sess.mode,
            app=app_sess.app,
            is_shell=False,
            command_count=0,
        ))

    for sh_sess in iter_shell_sessions(start=start, end=end):
        events.append(_TimelineEvent(
            start=sh_sess.start,
            end=sh_sess.end,
            project=sh_sess.project,
            mode="coding",
            app=None,
            is_shell=True,
            command_count=sh_sess.command_count,
        ))

    events.sort(key=lambda e: e.start)
    return events


def _merge_blocks(
    events: list[_TimelineEvent],
    commits: list[object],
    min_duration_minutes: float,
    max_interruption_ratio: float,
) -> Iterator[DeepWorkBlock]:
    """Walk timeline events, coalescing into deep work blocks."""
    acc: _BlockAccumulator | None = None

    for event in events:
        if acc is not None:
            gap = (event.start - acc.end).total_seconds()
            if gap > 300:  # >5 min gap breaks the block
                block = acc.finalize(commits)
                if block is not None and _passes_filter(block, min_duration_minutes, max_interruption_ratio):
                    yield block
                acc = None
            elif _compatible(acc, event):
                if gap > 0 and not _is_same_context(acc, event):
                    # Brief switch to different context: count as interruption
                    acc.interruption_seconds += min(gap, 120)
                acc.add(event)
                continue
            else:
                # Context switch: check if it's brief enough to be an interruption
                switch_duration = (event.end - event.start).total_seconds()
                if switch_duration < 120 and gap <= 300:
                    acc.interruption_seconds += switch_duration
                    acc.end = max(acc.end, event.end)
                    acc.events.append(event)
                    continue
                else:
                    block = acc.finalize(commits)
                    if block is not None and _passes_filter(block, min_duration_minutes, max_interruption_ratio):
                        yield block
                    acc = None

        if acc is None:
            acc = _BlockAccumulator(event)

    if acc is not None:
        block = acc.finalize(commits)
        if block is not None and _passes_filter(block, min_duration_minutes, max_interruption_ratio):
            yield block


def _compatible(acc: _BlockAccumulator, event: _TimelineEvent) -> bool:
    """Check if event continues the same deep work context."""
    # Same project is the strongest signal
    if acc.dominant_project and event.project and acc.dominant_project == event.project:
        return True
    # Same mode (e.g. both "coding") is acceptable
    if acc.dominant_mode and event.mode and acc.dominant_mode == event.mode:
        return True
    # If the event has no project/mode, accept it as continuation
    if not event.project and not event.mode:
        return True
    return False


def _is_same_context(acc: _BlockAccumulator, event: _TimelineEvent) -> bool:
    """Strict check: same project or same app."""
    if acc.dominant_project and event.project == acc.dominant_project:
        return True
    if acc.last_app and event.app == acc.last_app:
        return True
    return False


def _passes_filter(block: DeepWorkBlock, min_duration: float, max_interruption_ratio: float) -> bool:
    if block.duration_minutes < min_duration:
        return False
    if block.focus_ratio < (1.0 - max_interruption_ratio):
        return False
    return True


class _BlockAccumulator:
    __slots__ = (
        "start", "end", "events", "interruption_seconds",
        "project_counter", "mode_counter", "app_counter",
        "command_count", "last_app",
    )

    def __init__(self, event: _TimelineEvent) -> None:
        self.start = event.start
        self.end = event.end
        self.events: list[_TimelineEvent] = [event]
        self.interruption_seconds: float = 0.0
        self.project_counter: Counter[str] = Counter()
        self.mode_counter: Counter[str] = Counter()
        self.app_counter: Counter[str] = Counter()
        self.command_count: int = 0
        self.last_app: str | None = None
        self._record(event)

    def add(self, event: _TimelineEvent) -> None:
        self.events.append(event)
        if event.end > self.end:
            self.end = event.end
        self._record(event)

    def _record(self, event: _TimelineEvent) -> None:
        dur = max((event.end - event.start).total_seconds(), 1.0)
        if event.project:
            self.project_counter[event.project] += dur
        if event.mode:
            self.mode_counter[event.mode] += dur
        if event.app:
            self.app_counter[event.app] += dur
            self.last_app = event.app
        self.command_count += event.command_count

    @property
    def dominant_project(self) -> str | None:
        return self.project_counter.most_common(1)[0][0] if self.project_counter else None

    @property
    def dominant_mode(self) -> str | None:
        return self.mode_counter.most_common(1)[0][0] if self.mode_counter else None

    def finalize(self, commits: list[object]) -> DeepWorkBlock | None:
        duration_seconds = max((self.end - self.start).total_seconds(), 0.0)
        if duration_seconds <= 0:
            return None
        duration_minutes = duration_seconds / 60.0
        interruption_minutes = self.interruption_seconds / 60.0

        # Count app switches
        app_switches = 0
        prev_app: str | None = None
        for ev in self.events:
            if ev.app and ev.app != prev_app:
                if prev_app is not None:
                    app_switches += 1
                prev_app = ev.app

        # Count commits within the block time range
        commit_count = 0
        for c in commits:
            # GitCommit has .date (date) — we compare against the block's date range
            if hasattr(c, "date") and self.start.date() <= c.date <= self.end.date():
                commit_count += 1

        focus_ratio = max(0.0, min(1.0, 1.0 - (interruption_minutes / duration_minutes))) if duration_minutes > 0 else 0.0

        return DeepWorkBlock(
            start=self.start,
            end=self.end,
            duration_minutes=round(duration_minutes, 1),
            project=self.dominant_project,
            mode=self.dominant_mode or "unknown",
            app_switches=app_switches,
            commit_count=commit_count,
            command_count=self.command_count,
            interruption_minutes=round(interruption_minutes, 1),
            focus_ratio=round(focus_ratio, 3),
        )
