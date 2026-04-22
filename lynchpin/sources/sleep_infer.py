"""Sleep inference from AW activity gaps + Samsung Health watch data.

The watch (Samsung Health) captures sleep stages but misses:
- Pre-sleep time in bed (avg 115min before watch detects sleep)
- Post-sleep time in bed (avg 173min after watch ends detection)
- Entire nights when watch wasn't worn

AW's AFK gaps are the true "in bed" window. This module:
1. Extends watch sleep records with AW AFK boundaries
2. Infers sleep for AFK gaps ≥3h with no watch data
3. Produces a unified sleep timeline combining both sources
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

from ..core.primitives import date_to_dt_range, logical_date

__all__ = [
    "InferredSleep",
    "infer_sleep",
]


@dataclass(frozen=True)
class InferredSleep:
    """A sleep period combining watch data with AW AFK boundaries."""
    date: date                    # logical date (6AM boundary)
    bed_start: datetime           # AFK start (true in-bed time)
    bed_end: datetime             # AFK end (true out-of-bed time)
    sleep_start: datetime | None  # watch-detected sleep start (None if inferred only)
    sleep_end: datetime | None    # watch-detected sleep end
    bed_duration_min: float       # total in-bed time (from AFK)
    sleep_duration_min: float     # watch-detected sleep (0 if inferred only)
    pre_sleep_min: float          # time in bed before sleep detected
    post_sleep_min: float         # time in bed after sleep ended
    source: str                   # "watch+aw", "aw_only"
    sleep_score: float | None     # from watch, if available
    sleep_stages: dict[str, float] | None  # stage → minutes, if available


def infer_sleep(
    *, start: date, end: date, min_gap_hours: float = 3.0,
) -> list[InferredSleep]:
    """Combine AW AFK gaps with Samsung Health watch data to produce complete sleep records.

    For each AFK gap ≥ min_gap_hours:
    - If watch data overlaps: extend with AFK boundaries, keep watch metrics
    - If no watch data: infer as sleep from AFK alone
    """
    from .activitywatch import active_intervals
    from .sleep import entries_in_range, sleep_architecture

    s_dt, e_dt = date_to_dt_range(start, end)
    active = active_intervals(start=s_dt, end=e_dt)

    def strip_tz(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    active_naive = sorted((strip_tz(a), strip_tz(b)) for a, b in active)

    # Build AFK gaps
    gaps: list[tuple[datetime, datetime, float]] = []
    for i in range(len(active_naive) - 1):
        _, prev_end = active_naive[i]
        next_start, _ = active_naive[i + 1]
        gap_h = (next_start - prev_end).total_seconds() / 3600
        if gap_h >= min_gap_hours:
            gaps.append((prev_end, next_start, gap_h))

    # Load watch sleep data
    watch_entries = list(entries_in_range(start - timedelta(days=1), end + timedelta(days=1)))
    architecture_by_date = {
        arch.date: arch
        for arch in sleep_architecture(start=start - timedelta(days=1), end=end)
    }

    # Parse watch entries into (start, end, entry) tuples
    watch_spans: list[tuple[datetime, datetime, object]] = []
    for e in watch_entries:
        if not e.segments or e.segments[0].start == datetime.min:
            continue
        w_start = strip_tz(e.segments[0].start)
        w_end = strip_tz(e.segments[-1].end)
        watch_spans.append((w_start, w_end, e))

    result: list[InferredSleep] = []
    used_watch: set[int] = set()

    for gap_start, gap_end, gap_h in gaps:
        if gap_h > 16:
            continue  # skip multi-day AFK (computer off, travel, etc.)

        # Find watch sleep that overlaps this AFK gap
        best_watch = None
        best_overlap = 0
        for idx, (w_start, w_end, entry) in enumerate(watch_spans):
            if idx in used_watch:
                continue
            # Check overlap
            o_start = max(gap_start, w_start)
            o_end = min(gap_end, w_end)
            if o_end > o_start:
                overlap = (o_end - o_start).total_seconds()
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_watch = (idx, w_start, w_end, entry)

        if best_watch is not None:
            idx, w_start, w_end, entry = best_watch
            used_watch.add(idx)
            pre = max(0, (w_start - gap_start).total_seconds() / 60)
            post = max(0, (gap_end - w_end).total_seconds() / 60)

            sleep_date = logical_date(gap_start)
            arch = architecture_by_date.get(sleep_date)
            stages = None
            if arch is not None:
                stages = {
                    "awake": arch.awake_min,
                    "light": arch.light_min,
                    "deep": arch.deep_min,
                    "rem": arch.rem_min,
                }

            result.append(InferredSleep(
                date=sleep_date,
                bed_start=gap_start,
                bed_end=gap_end,
                sleep_start=w_start,
                sleep_end=w_end,
                bed_duration_min=round(gap_h * 60, 1),
                sleep_duration_min=round(entry.total_minutes, 1),
                pre_sleep_min=round(pre, 1),
                post_sleep_min=round(post, 1),
                source="watch+aw",
                sleep_score=entry.avg_score,
                sleep_stages=stages,
            ))
        else:
            # No watch data — infer from AFK alone
            result.append(InferredSleep(
                date=logical_date(gap_start),
                bed_start=gap_start,
                bed_end=gap_end,
                sleep_start=None,
                sleep_end=None,
                bed_duration_min=round(gap_h * 60, 1),
                sleep_duration_min=0,
                pre_sleep_min=0,
                post_sleep_min=0,
                source="aw_only",
                sleep_score=None,
                sleep_stages=None,
            ))

    result.sort(key=lambda s: s.bed_start)
    return result
