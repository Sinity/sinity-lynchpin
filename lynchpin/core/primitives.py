"""Shared data manipulation primitives: TopN, group_by_gap, interval arithmetic."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, date
from typing import Callable, Generic, Iterable, Iterator, Sequence, TypeVar

T = TypeVar("T")


# ── TopN: ranked accumulator ─────────────────────────────────────────────────


class TopN:
    """Accumulate weighted keys, expose top-N by weight.

    Replaces the ubiquitous Counter → sorted(items, key=-value)[:5] pattern.
    """

    __slots__ = ("_n", "_counts")

    def __init__(self, n: int = 5) -> None:
        self._n = n
        self._counts: defaultdict[str, float] = defaultdict(float)

    def add(self, key: str, weight: float = 1.0) -> None:
        self._counts[key] += weight

    def merge(self, other: TopN) -> TopN:
        result = TopN(self._n)
        result._counts = defaultdict(float, self._counts)
        for key, value in other._counts.items():
            result._counts[key] += value
        return result

    @property
    def dominant(self) -> str | None:
        top = self.items[:1]
        return top[0][0] if top else None

    @property
    def items(self) -> tuple[tuple[str, float], ...]:
        return tuple(sorted(self._counts.items(), key=lambda item: item[1], reverse=True)[: self._n])

    @property
    def total(self) -> float:
        return sum(self._counts.values())

    def __bool__(self) -> bool:
        return bool(self._counts)


# ── group_by_gap: universal session merge ─────────────────────────────────────


@dataclass
class Group(Generic[T]):
    items: list[T]
    start: datetime
    end: datetime
    interruptions: int


def group_by_gap(
    items: Iterable[T],
    *,
    start_of: Callable[[T], datetime],
    end_of: Callable[[T], datetime],
    max_gap: float,
    compatible: Callable[[T, T], bool] = lambda a, b: True,
    absorb_interruption: float = 0.0,
) -> Iterator[Group[T]]:
    """Yield groups of consecutive compatible items with gaps < max_gap seconds.

    If absorb_interruption > 0, incompatible items shorter than that duration
    are absorbed as interruptions rather than breaking the group.
    """
    current: list[T] = []
    current_start: datetime | None = None
    current_end: datetime | None = None
    interruptions = 0

    for item in items:
        item_start = start_of(item)
        item_end = end_of(item)

        if not current:
            current = [item]
            current_start = item_start
            current_end = item_end
            interruptions = 0
            continue

        gap = (item_start - current_end).total_seconds() if current_end else 0.0

        if gap <= max_gap and compatible(current[-1], item):
            current.append(item)
            if current_end is None or item_end > current_end:
                current_end = item_end
        elif (
            absorb_interruption > 0
            and (item_end - item_start).total_seconds() <= absorb_interruption
        ):
            current.append(item)
            interruptions += 1
            if current_end is None or item_end > current_end:
                current_end = item_end
        else:
            if current_start is None or current_end is None:
                continue
            yield Group(items=current, start=current_start, end=current_end, interruptions=interruptions)
            current = [item]
            current_start = item_start
            current_end = item_end
            interruptions = 0

    if current:
        if current_start is None or current_end is None:
            return
        yield Group(items=current, start=current_start, end=current_end, interruptions=interruptions)


# ── Interval arithmetic ──────────────────────────────────────────────────────

Interval = tuple[datetime, datetime]


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Merge overlapping or adjacent intervals."""
    sorted_ivs = sorted(intervals, key=lambda iv: (iv[0], iv[1]))
    if not sorted_ivs:
        return []
    merged: list[list[datetime]] = [[sorted_ivs[0][0], sorted_ivs[0][1]]]
    for start, end in sorted_ivs[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def intersect_intervals(
    span_start: datetime,
    span_end: datetime,
    timeline: Sequence[Interval],
    start_index: int = 0,
) -> tuple[list[Interval], int]:
    """Find overlapping portions of span with sorted timeline intervals.

    Returns (overlaps, new_start_index) for efficient sequential calls.
    """
    idx = start_index
    while idx < len(timeline) and timeline[idx][1] <= span_start:
        idx += 1

    overlaps: list[Interval] = []
    cur = idx
    while cur < len(timeline) and timeline[cur][0] < span_end:
        active_start, active_end = timeline[cur]
        overlap_start = max(span_start, active_start)
        overlap_end = min(span_end, active_end)
        if overlap_end > overlap_start:
            overlaps.append((overlap_start, overlap_end))
        if active_end >= span_end:
            break
        cur += 1
    return overlaps, idx


DAY_BOUNDARY_HOUR: int = 6
"""Hour at which a new 'logical day' begins (default 6 AM).

Activity at 3 AM on March 15 belongs to 'March 14' because the person
hasn't slept yet. This matches delayed sleep schedules where bedtime
is typically 3-5 AM.
"""


def logical_date(dt: datetime) -> date:
    """Map a datetime to its logical date, using DAY_BOUNDARY_HOUR.

    Before the boundary hour, the datetime belongs to the previous calendar date.
    """
    if dt.hour < DAY_BOUNDARY_HOUR:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def split_by_day(start: datetime, end: datetime) -> Iterator[tuple[date, Interval]]:
    """Split an interval into per-day segments using DAY_BOUNDARY_HOUR."""
    boundary = time(hour=DAY_BOUNDARY_HOUR)
    cursor = start
    while cursor < end:
        # Next boundary: today's boundary if before it, else tomorrow's
        today_boundary = datetime.combine(cursor.date(), boundary, tzinfo=cursor.tzinfo)
        if cursor >= today_boundary:
            next_boundary = datetime.combine(cursor.date() + timedelta(days=1), boundary, tzinfo=cursor.tzinfo)
        else:
            next_boundary = today_boundary
        segment_end = min(end, next_boundary)
        if segment_end > cursor:
            yield logical_date(cursor), (cursor, segment_end)
        cursor = segment_end


def split_by_hour(start: datetime, end: datetime) -> Iterator[tuple[int, Interval]]:
    """Split an interval into per-hour segments."""
    cursor = start
    while cursor < end:
        next_hour = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        segment_end = min(end, next_hour)
        if segment_end > cursor:
            yield cursor.hour, (cursor, segment_end)
        cursor = segment_end


def duration_s(interval: Interval) -> float:
    return max((interval[1] - interval[0]).total_seconds(), 0.0)






def date_to_dt_range(start: date, end: date) -> tuple[datetime, datetime]:
    """Convert a date range to datetime range using DAY_BOUNDARY_HOUR.

    A logical day runs from 06:00 to 06:00 next day (with default boundary).
    So date_to_dt_range(Mar 14, Mar 14) → [Mar 14 06:00, Mar 15 06:00).
    """
    boundary = time(hour=DAY_BOUNDARY_HOUR)
    return (
        datetime.combine(start, boundary),
        datetime.combine(end + timedelta(days=1), boundary),
    )
