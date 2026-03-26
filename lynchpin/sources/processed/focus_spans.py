"""Canonical focus timeline with explicit AFK overrides and keylog enrichment."""

from __future__ import annotations

import functools
from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterator, Sequence

from ...signals import _as_local
from ..captures.keylog import iter_key_press_samples, keylog_coverage_by_date
from ._activitywatch import (
    iter_attributed_window_spans,
    load_active_intervals,
    load_afk_intervals,
)


@dataclass(frozen=True)
class FocusSpan:
    start: datetime
    end: datetime
    span_kind: str
    source_kind: str
    app: str | None
    title: str | None
    mode: str | None
    project: str | None
    keypress_count: int
    changed_keypress_count: int
    keylog_state: str

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)

    @property
    def date(self) -> date:
        return self.start.date()


def iter_focus_spans(
    *,
    start: datetime,
    end: datetime,
    min_duration_seconds: float = 10.0,
    include_keyboard: bool = True,
) -> Iterator[FocusSpan]:
    yield from _focus_spans_cached(
        _as_local(start),
        _as_local(end),
        min_duration_seconds,
        include_keyboard,
    )


@functools.lru_cache(maxsize=16)
def _focus_spans_cached(
    start: datetime,
    end: datetime,
    min_duration_seconds: float,
    include_keyboard: bool,
) -> tuple[FocusSpan, ...]:
    active_intervals = [
        (_as_local(interval_start), _as_local(interval_end))
        for interval_start, interval_end in load_active_intervals(start=start, end=end)
    ]
    afk_intervals = [
        (_as_local(interval_start), _as_local(interval_end))
        for interval_start, interval_end in load_afk_intervals(start=start, end=end)
    ]
    window_spans = list(
        FocusSpanWindow(
            start=_as_local(span.start),
            end=_as_local(span.end),
            app=span.app,
            title=span.title,
            mode=span.mode,
            project=span.project,
        )
        for span in iter_attributed_window_spans(
            start=start,
            end=end,
            min_duration_seconds=min(10.0, min_duration_seconds),
        )
    )
    if include_keyboard:
        coverage = keylog_coverage_by_date(
            start=start.date(),
            end=(end - timedelta(microseconds=1)).date(),
        )
        key_press_times: list[int] = []
        changed_prefix = [0]
        for timestamp_us, changed in iter_key_press_samples(start=start, end=end):
            key_press_times.append(timestamp_us)
            changed_prefix.append(changed_prefix[-1] + int(changed))
    else:
        coverage = {}
        key_press_times = []
        changed_prefix = [0]

    boundaries = {start, end}
    for interval_start, interval_end in active_intervals:
        boundaries.add(max(interval_start, start))
        boundaries.add(min(interval_end, end))
    for interval_start, interval_end in afk_intervals:
        boundaries.add(max(interval_start, start))
        boundaries.add(min(interval_end, end))
    for span in window_spans:
        boundaries.add(max(span.start, start))
        boundaries.add(min(span.end, end))
    cursor = datetime.combine(start.date(), time.min, tzinfo=start.tzinfo) + timedelta(days=1)
    while cursor < end:
        boundaries.add(cursor)
        cursor += timedelta(days=1)

    ordered = sorted(boundaries)
    spans: list[FocusSpan] = []
    active_idx = 0
    afk_idx = 0
    window_idx = 0

    for left, right in zip(ordered, ordered[1:]):
        if right <= left:
            continue
        active_idx = _advance_past(active_intervals, active_idx, left)
        afk_idx = _advance_past(afk_intervals, afk_idx, left)
        window_idx = _advance_past_window(window_spans, window_idx, left)
        if _covers_interval(afk_intervals, afk_idx, left, right):
            spans.append(_build_focus_span(
                start=left,
                end=right,
                span_kind="afk",
                source_kind="activitywatch.afk",
                app=None,
                title=None,
                mode=None,
                project=None,
                include_keyboard=include_keyboard,
                coverage=coverage,
                key_press_times=key_press_times,
                changed_prefix=changed_prefix,
            ))
            continue
        window = _covering_window(window_spans, window_idx, left, right)
        if window is not None:
            spans.append(_build_focus_span(
                start=left,
                end=right,
                span_kind="focused",
                source_kind="activitywatch.window",
                app=window.app,
                title=window.title,
                mode=window.mode,
                project=window.project,
                include_keyboard=include_keyboard,
                coverage=coverage,
                key_press_times=key_press_times,
                changed_prefix=changed_prefix,
            ))
            continue
        if _covers_interval(active_intervals, active_idx, left, right):
            spans.append(_build_focus_span(
                start=left,
                end=right,
                span_kind="active_unknown",
                source_kind="activitywatch.active",
                app=None,
                title=None,
                mode=None,
                project=None,
                include_keyboard=include_keyboard,
                coverage=coverage,
                key_press_times=key_press_times,
                changed_prefix=changed_prefix,
            ))

    return tuple(
        span for span in _merge_focus_spans(spans)
        if span.duration_seconds >= min_duration_seconds
    )


def _merge_focus_spans(spans: Sequence[FocusSpan]) -> Iterator[FocusSpan]:
    if not spans:
        return
    current = spans[0]
    for span in spans[1:]:
        if _same_shape(current, span) and current.end >= span.start:
            current = FocusSpan(
                start=current.start,
                end=max(current.end, span.end),
                span_kind=current.span_kind,
                source_kind=current.source_kind,
                app=current.app,
                title=current.title,
                mode=current.mode,
                project=current.project,
                keypress_count=current.keypress_count + span.keypress_count,
                changed_keypress_count=current.changed_keypress_count + span.changed_keypress_count,
                keylog_state=_merge_keylog_state(current.keylog_state, span.keylog_state),
            )
            continue
        yield current
        current = span
    yield current


def _same_shape(left: FocusSpan, right: FocusSpan) -> bool:
    return (
        left.span_kind == right.span_kind
        and left.source_kind == right.source_kind
        and left.app == right.app
        and left.title == right.title
        and left.mode == right.mode
        and left.project == right.project
        and left.date == right.date
    )


def _merge_keylog_state(left: str, right: str) -> str:
    if "keyboard_active" in {left, right}:
        return "keyboard_active"
    if left == right:
        return left
    if "keyboard_silent" in {left, right} and "unobserved" in {left, right}:
        return "keyboard_silent"
    return right


def _build_focus_span(
    *,
    start: datetime,
    end: datetime,
    span_kind: str,
    source_kind: str,
    app: str | None,
    title: str | None,
    mode: str | None,
    project: str | None,
    include_keyboard: bool,
    coverage: dict[date, bool],
    key_press_times: Sequence[int],
    changed_prefix: Sequence[int],
) -> FocusSpan:
    if not include_keyboard:
        return FocusSpan(
            start=start,
            end=end,
            span_kind=span_kind,
            source_kind=source_kind,
            app=app,
            title=title,
            mode=mode,
            project=project,
            keypress_count=0,
            changed_keypress_count=0,
            keylog_state="not_requested",
        )

    start_us = _datetime_to_epoch_us(start)
    end_us = _datetime_to_epoch_us(end)
    left_idx = bisect_left(key_press_times, start_us)
    right_idx = bisect_left(key_press_times, end_us)
    keypress_count = right_idx - left_idx
    changed_keypress_count = changed_prefix[right_idx] - changed_prefix[left_idx]
    covered = coverage.get(start.date(), False)
    if not covered:
        keylog_state = "unobserved"
    elif keypress_count > 0:
        keylog_state = "keyboard_active"
    else:
        keylog_state = "keyboard_silent"
    return FocusSpan(
        start=start,
        end=end,
        span_kind=span_kind,
        source_kind=source_kind,
        app=app,
        title=title,
        mode=mode,
        project=project,
        keypress_count=keypress_count,
        changed_keypress_count=changed_keypress_count,
        keylog_state=keylog_state,
    )


def _advance_past(
    intervals: Sequence[tuple[datetime, datetime]],
    start_index: int,
    left: datetime,
) -> int:
    idx = start_index
    while idx < len(intervals) and intervals[idx][1] <= left:
        idx += 1
    return idx


def _advance_past_window(
    windows: Sequence["FocusSpanWindow"],
    start_index: int,
    left: datetime,
) -> int:
    idx = start_index
    while idx < len(windows) and windows[idx].end <= left:
        idx += 1
    return idx


def _covers_interval(
    intervals: Sequence[tuple[datetime, datetime]],
    start_index: int,
    left: datetime,
    right: datetime,
) -> bool:
    return (
        start_index < len(intervals)
        and intervals[start_index][0] <= left
        and intervals[start_index][1] >= right
    )


def _covering_window(
    windows: Sequence["FocusSpanWindow"],
    start_index: int,
    left: datetime,
    right: datetime,
) -> "FocusSpanWindow" | None:
    if start_index >= len(windows):
        return None
    window = windows[start_index]
    if window.start <= left and window.end >= right:
        return window
    return None


@dataclass(frozen=True)
class FocusSpanWindow:
    start: datetime
    end: datetime
    app: str
    title: str
    mode: str | None
    project: str | None


def _datetime_to_epoch_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)
