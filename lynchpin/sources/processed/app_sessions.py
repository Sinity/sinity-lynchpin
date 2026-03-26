"""Processed app sessions: merged focused spans derived from the canonical focus timeline."""

from __future__ import annotations

import functools
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional

from .focus_spans import FocusSpan, iter_focus_spans


@dataclass(frozen=True)
class AppSession:
    app: str
    start: datetime
    end: datetime
    duration_seconds: float
    title_dominant: str
    title_count: int
    titles: tuple[str, ...]
    mode: Optional[str]
    project: Optional[str]
    interruptions: int


def iter_app_sessions(
    *,
    start: datetime,
    end: datetime,
    min_duration_seconds: float = 60,
    merge_gap_seconds: float = 120,
) -> Iterator[AppSession]:
    """Yield AFK-trimmed same-app sessions, split at day boundaries."""
    yield from _app_sessions_cached(
        start=start,
        end=end,
        min_duration_seconds=min_duration_seconds,
        merge_gap_seconds=merge_gap_seconds,
    )


@functools.lru_cache(maxsize=16)
def _app_sessions_cached(
    *,
    start: datetime,
    end: datetime,
    min_duration_seconds: float,
    merge_gap_seconds: float,
) -> tuple[AppSession, ...]:
    spans = [
        span
        for span in iter_focus_spans(
            start=start,
            end=end,
            min_duration_seconds=min(10.0, min_duration_seconds),
            include_keyboard=False,
        )
        if span.span_kind == "focused" and span.app and span.title
    ]
    if not spans:
        return ()

    groups: list[_SessionAccumulator] = []
    acc = _SessionAccumulator(spans[0])
    index = 1
    while index < len(spans):
        span = spans[index]
        if _should_merge(acc, span, merge_gap_seconds):
            acc.extend(span)
            index += 1
            continue

        if _is_brief_interruption(acc, spans, index, merge_gap_seconds):
            acc.interruptions += 1
            acc.extend(spans[index + 1])
            index += 2
            continue

        groups.append(acc)
        acc = _SessionAccumulator(span)
        index += 1

    groups.append(acc)

    sessions: list[AppSession] = []
    for group in groups:
        duration = group.duration_seconds
        if duration < min_duration_seconds:
            continue
        dominant_title, unique_titles = _compute_title_stats(group.title_durations)
        sessions.append(AppSession(
            app=group.app,
            start=group.start,
            end=group.end,
            duration_seconds=round(duration, 3),
            title_dominant=dominant_title,
            title_count=len(unique_titles),
            titles=unique_titles,
            mode=group.dominant_mode,
            project=group.dominant_project,
            interruptions=group.interruptions,
        ))
    return tuple(sessions)


class _SessionAccumulator:
    __slots__ = (
        "app",
        "start",
        "end",
        "interruptions",
        "title_durations",
        "mode_durations",
        "project_durations",
    )

    def __init__(self, span: FocusSpan) -> None:
        self.app = span.app
        self.start = span.start
        self.end = span.end
        self.interruptions = 0
        self.title_durations: dict[str, float] = defaultdict(float)
        self.mode_durations: Counter[str] = Counter()
        self.project_durations: Counter[str] = Counter()
        self._record(span)

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)

    @property
    def dominant_mode(self) -> str | None:
        return self.mode_durations.most_common(1)[0][0] if self.mode_durations else None

    @property
    def dominant_project(self) -> str | None:
        return self.project_durations.most_common(1)[0][0] if self.project_durations else None

    def extend(self, span: FocusSpan) -> None:
        if span.end > self.end:
            self.end = span.end
        self._record(span)

    def _record(self, span: FocusSpan) -> None:
        duration = max(span.duration_seconds, 1.0)
        self.title_durations[span.title] += duration
        if span.mode:
            self.mode_durations[span.mode] += duration
        if span.project:
            self.project_durations[span.project] += duration


def _should_merge(acc: _SessionAccumulator, span: FocusSpan, merge_gap_seconds: float) -> bool:
    if span.app != acc.app:
        return False
    if span.start.date() != acc.start.date():
        return False
    gap = (span.start - acc.end).total_seconds()
    return gap <= merge_gap_seconds


def _is_brief_interruption(
    acc: _SessionAccumulator,
    spans: list[FocusSpan],
    index: int,
    merge_gap_seconds: float,
) -> bool:
    if index + 1 >= len(spans):
        return False
    interruption = spans[index]
    follow = spans[index + 1]
    if follow.app != acc.app:
        return False
    if interruption.app == acc.app:
        return False
    if interruption.start.date() != acc.start.date():
        return False
    if interruption.duration_seconds > 30:
        return False
    gap_before = (interruption.start - acc.end).total_seconds()
    gap_after = (follow.start - interruption.end).total_seconds()
    return gap_before <= merge_gap_seconds and gap_after <= merge_gap_seconds


def _compute_title_stats(
    title_durations: dict[str, float],
) -> tuple[str, tuple[str, ...]]:
    if not title_durations:
        return "(untitled)", ()
    sorted_titles = sorted(title_durations.items(), key=lambda item: (-item[1], item[0]))
    return sorted_titles[0][0], tuple(title for title, _ in sorted_titles)
