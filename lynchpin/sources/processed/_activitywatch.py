"""Shared ActivityWatch normalization for processed focus-oriented views."""

from __future__ import annotations

import functools
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Iterator, Sequence

from ...signals import _as_local
from ...signals.rules import classify_signal
from ...signals.sources import _window_signals
from ...sources.captures import activitywatch

_ACTIVE_STATUSES = {"not-afk", "active", "present"}
_AFK_STATUSES = {"afk", "away"}
_WINDOW_NOISE_TITLES = {"application not responding"}


@dataclass(frozen=True)
class WindowSpan:
    start: datetime
    end: datetime
    app: str
    title: str
    mode: str | None
    project: str | None

    @property
    def duration_seconds(self) -> float:
        return max((self.end - self.start).total_seconds(), 0.0)


def load_active_intervals(
    *,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    start_local = _as_local(start)
    end_local = _as_local(end)
    return list(_active_intervals_cached(start_local, end_local))


def load_afk_intervals(
    *,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    start_local = _as_local(start)
    end_local = _as_local(end)
    return list(_afk_intervals_cached(start_local, end_local))


def active_seconds_by_date(
    *,
    start: date,
    end: date,
) -> dict[date, float]:
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end + timedelta(days=1), time.min)
    totals: dict[date, float] = {}
    for interval_start, interval_end in load_active_intervals(start=start_dt, end=end_dt):
        for seg_start, seg_end in split_by_day(interval_start, interval_end):
            totals[seg_start.date()] = totals.get(seg_start.date(), 0.0) + (
                seg_end - seg_start
            ).total_seconds()
    return totals


def iter_attributed_window_spans(
    *,
    start: datetime,
    end: datetime,
    min_duration_seconds: float = 10.0,
) -> Iterator[WindowSpan]:
    yield from _window_spans_cached(_as_local(start), _as_local(end), min_duration_seconds)


@functools.lru_cache(maxsize=16)
def _window_spans_cached(
    start: datetime,
    end: datetime,
    min_duration_seconds: float,
) -> tuple[WindowSpan, ...]:
    window_start = _as_local(start)
    window_end = _as_local(end)
    active_intervals = load_active_intervals(start=window_start, end=window_end)
    if not active_intervals:
        return ()

    spans: list[WindowSpan] = []
    interval_idx = 0
    for attributed in (
        classify_signal(signal)
        for signal in _window_signals(window_start, window_end)
        if signal.app
    ):
        title = (attributed.title or "(untitled)").strip()
        if title.lower() in _WINDOW_NOISE_TITLES:
            continue
        overlaps, interval_idx = intersect_intervals(
            interval_start=attributed.start,
            interval_end=attributed.end,
            intervals=active_intervals,
            start_index=interval_idx,
        )
        for overlap_start, overlap_end in overlaps:
            for seg_start, seg_end in split_by_day(overlap_start, overlap_end):
                span = WindowSpan(
                    start=seg_start,
                    end=seg_end,
                    app=attributed.app or "(unknown)",
                    title=title,
                    mode=attributed.mode if attributed.mode != "unknown" else None,
                    project=attributed.project,
                )
                if span.duration_seconds >= min_duration_seconds:
                    spans.append(span)

    return tuple(_linearize_window_spans(spans))


def intersect_intervals(
    *,
    interval_start: datetime,
    interval_end: datetime,
    intervals: Sequence[tuple[datetime, datetime]],
    start_index: int = 0,
) -> tuple[list[tuple[datetime, datetime]], int]:
    idx = start_index
    while idx < len(intervals) and intervals[idx][1] <= interval_start:
        idx += 1

    overlaps: list[tuple[datetime, datetime]] = []
    cur = idx
    while cur < len(intervals) and intervals[cur][0] < interval_end:
        active_start, active_end = intervals[cur]
        overlap_start = max(interval_start, active_start)
        overlap_end = min(interval_end, active_end)
        if overlap_end > overlap_start:
            overlaps.append((overlap_start, overlap_end))
        if active_end >= interval_end:
            break
        cur += 1
    return overlaps, idx


def split_by_day(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        next_midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, tzinfo=cursor.tzinfo)
        segment_end = min(end, next_midnight)
        if segment_end > cursor:
            yield cursor, segment_end
        cursor = segment_end


def split_by_hour(start: datetime, end: datetime) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        segment_end = min(end, next_hour)
        if segment_end > cursor:
            yield cursor, segment_end
        cursor = segment_end


def _load_status_intervals(
    *,
    start: datetime,
    end: datetime,
    statuses: set[str],
) -> list[tuple[datetime, datetime]]:
    start_local = _as_local(start)
    end_local = _as_local(end)
    intervals: list[tuple[datetime, datetime]] = []
    for event in activitywatch.afk_events(start=start_local, end=end_local):
        status = str((event.data or {}).get("status") or "").strip().lower()
        if status not in statuses:
            continue
        event_start = max(_as_local(event.start), start_local)
        event_end = min(_as_local(event.end), end_local)
        if event_end <= event_start:
            continue
        intervals.append((event_start, event_end))
    return _merge_intervals(intervals)


@functools.lru_cache(maxsize=16)
def _active_intervals_cached(
    start: datetime,
    end: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    return tuple(_load_status_intervals(start=start, end=end, statuses=_ACTIVE_STATUSES))


@functools.lru_cache(maxsize=16)
def _afk_intervals_cached(
    start: datetime,
    end: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    return tuple(_load_status_intervals(start=start, end=end, statuses=_AFK_STATUSES))


def _merge_intervals(
    intervals: Sequence[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged: list[list[datetime]] = [[sorted_intervals[0][0], sorted_intervals[0][1]]]
    for start, end in sorted_intervals[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
            continue
        merged.append([start, end])
    return [(start, end) for start, end in merged]


def _linearize_window_spans(spans: Sequence[WindowSpan]) -> Iterator[WindowSpan]:
    if not spans:
        return

    ordered = sorted(spans, key=lambda span: (span.start, span.end, span.app, span.title))
    emitted: list[WindowSpan] = []
    current = ordered[0]
    for span in ordered[1:]:
        if span.start >= current.end:
            _append_or_merge(emitted, current)
            current = span
            continue

        if span.start > current.start:
            clipped = replace(current, end=span.start)
            if clipped.end > clipped.start:
                _append_or_merge(emitted, clipped)
        current = _prefer_span(current, span)

    _append_or_merge(emitted, current)
    yield from emitted


def _append_or_merge(target: list[WindowSpan], span: WindowSpan) -> None:
    if span.end <= span.start:
        return
    if target:
        previous = target[-1]
        if (
            previous.app == span.app
            and previous.title == span.title
            and previous.mode == span.mode
            and previous.project == span.project
            and previous.start.date() == span.start.date()
            and previous.end >= span.start
        ):
            target[-1] = replace(previous, end=max(previous.end, span.end))
            return
    target.append(span)


def _prefer_span(left: WindowSpan, right: WindowSpan) -> WindowSpan:
    def score(span: WindowSpan) -> tuple[int, int, float]:
        return (
            1 if span.project else 0,
            1 if span.mode else 0,
            span.duration_seconds,
        )

    return right if score(right) >= score(left) else left
