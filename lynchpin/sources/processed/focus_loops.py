"""Semantic detection of alternating focus loops from the canonical focus timeline."""

from __future__ import annotations

import functools
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator

from .focus_spans import FocusSpan, iter_focus_spans


@dataclass(frozen=True)
class FocusLoop:
    date: date
    start: datetime
    end: datetime
    duration_minutes: float
    span_count: int
    switch_count: int
    cycle_count: int
    context_a_app: str
    context_a_title: str
    context_b_app: str
    context_b_title: str
    dominant_project: str | None
    dominant_mode: str | None


def iter_focus_loops(
    *,
    start: datetime,
    end: datetime,
    min_span_count: int = 4,
    max_gap_seconds: float = 180.0,
    max_dwell_minutes: float = 20.0,
    min_duration_minutes: float = 8.0,
) -> Iterator[FocusLoop]:
    yield from _focus_loops_cached(
        start=start,
        end=end,
        min_span_count=min_span_count,
        max_gap_seconds=max_gap_seconds,
        max_dwell_minutes=max_dwell_minutes,
        min_duration_minutes=min_duration_minutes,
    )


@functools.lru_cache(maxsize=16)
def _focus_loops_cached(
    *,
    start: datetime,
    end: datetime,
    min_span_count: int,
    max_gap_seconds: float,
    max_dwell_minutes: float,
    min_duration_minutes: float,
) -> tuple[FocusLoop, ...]:
    spans = [
        span
        for span in iter_focus_spans(
            start=start,
            end=end,
            min_duration_seconds=60.0,
            include_keyboard=False,
        )
        if span.span_kind == "focused" and span.app and span.title
    ]
    if len(spans) < min_span_count:
        return ()

    spans_by_day: dict[date, list[FocusSpan]] = {}
    for span in spans:
        spans_by_day.setdefault(span.start.date(), []).append(span)

    loops: list[FocusLoop] = []
    for day in sorted(spans_by_day):
        day_spans = spans_by_day[day]
        index = 0
        while index <= len(day_spans) - min_span_count:
            match = _consume_loop(
                day_spans=day_spans,
                start_index=index,
                min_span_count=min_span_count,
                max_gap_seconds=max_gap_seconds,
                max_dwell_minutes=max_dwell_minutes,
                min_duration_minutes=min_duration_minutes,
            )
            if match is None:
                index += 1
                continue
            loops.append(match)
            index = match.span_count + index
    return tuple(loops)


def _consume_loop(
    *,
    day_spans: list[FocusSpan],
    start_index: int,
    min_span_count: int,
    max_gap_seconds: float,
    max_dwell_minutes: float,
    min_duration_minutes: float,
) -> FocusLoop | None:
    first = day_spans[start_index]
    second = day_spans[start_index + 1]
    if _context_key(first) == _context_key(second):
        return None
    if not _eligible_span(first, max_dwell_minutes) or not _eligible_span(second, max_dwell_minutes):
        return None
    if _gap_seconds(first, second) > max_gap_seconds:
        return None

    context_a = _context_key(first)
    context_b = _context_key(second)
    collected = [first, second]
    previous = second
    expected = context_a
    index = start_index + 2

    while index < len(day_spans):
        span = day_spans[index]
        if not _eligible_span(span, max_dwell_minutes):
            break
        if _gap_seconds(previous, span) > max_gap_seconds:
            break
        context = _context_key(span)
        if context not in {context_a, context_b}:
            break
        if context != expected:
            break
        collected.append(span)
        previous = span
        expected = context_b if expected == context_a else context_a
        index += 1

    if len(collected) < min_span_count:
        return None

    duration_minutes = max((collected[-1].end - collected[0].start).total_seconds(), 0.0) / 60.0
    if duration_minutes < min_duration_minutes:
        return None

    mode_durations: Counter[str] = Counter()
    project_durations: Counter[str] = Counter()
    for span in collected:
        seconds = max(span.duration_seconds, 1.0)
        if span.mode:
            mode_durations[span.mode] += seconds
        if span.project:
            project_durations[span.project] += seconds

    return FocusLoop(
        date=collected[0].start.date(),
        start=collected[0].start,
        end=collected[-1].end,
        duration_minutes=round(duration_minutes, 3),
        span_count=len(collected),
        switch_count=len(collected) - 1,
        cycle_count=min(
            sum(1 for span in collected if _context_key(span) == context_a),
            sum(1 for span in collected if _context_key(span) == context_b),
        ),
        context_a_app=first.app or "(unknown)",
        context_a_title=first.title or "(untitled)",
        context_b_app=second.app or "(unknown)",
        context_b_title=second.title or "(untitled)",
        dominant_project=project_durations.most_common(1)[0][0] if project_durations else None,
        dominant_mode=mode_durations.most_common(1)[0][0] if mode_durations else None,
    )


def _context_key(span: FocusSpan) -> tuple[str | None, str | None]:
    return span.app, span.title


def _eligible_span(span: FocusSpan, max_dwell_minutes: float) -> bool:
    return span.duration_seconds <= (max_dwell_minutes * 60.0)


def _gap_seconds(left: FocusSpan, right: FocusSpan) -> float:
    return max((right.start - left.end).total_seconds(), 0.0)
