"""Detect AW data outages — distinct from operator AFK.

When `aw-watcher-afk` is silent for hours, two distinct things could be true:

  1. **Operator was AFK** — they walked away. The AFK bucket sees no
     new events because there's no input. This is the normal case.
  2. **AW server / watcher was DOWN** — the daemon crashed, the bucket
     itself stopped receiving events. "No data" is a service problem,
     not an operator-presence claim.

These are *fundamentally different* and downstream consumers must treat
them differently:

  - Focus-time analytics: AFK = operator wasn't typing; outage = unknown.
  - Sleep correlation: AFK = candidate sleep period; outage = ignore.
  - Productivity metrics: AFK reduces "active hours"; outage shouldn't.

Cross-bucket consistency catches this. The aw-watcher-afk and
aw-watcher-window daemons run inside the SAME awatcher process on
the operator's setup. The Chrome web-tab extension runs separately.
So:

  Pattern A — REAL OUTAGE: all three buckets (afk, window, web) silent
    Everything died — server outage or full daemon crash.

  Pattern B — AFK+WIN DOWN: afk + window silent, web still running
    awatcher process died (it provides both afk + window). Chrome
    extension kept going.

  Pattern C — AFK-ONLY DOWN: window + web running, only afk silent
    Watcher-internal bug or AFK detection got stuck. Most surprising
    pattern — observed multiple times in the operator's archive,
    including a 30-day stretch (2025-07-19 → 2025-08-18).

The user-visible distinction lynchpin should make: "no AFK data for
this period" must not silently become "operator was AFK". Tag the
gap with its pattern letter so analytics can decide policy.

Operator's archive sweep (2025-05-24 → 2026-05-23):

  32 AFK gaps > 6h. Breakdown:
    Pattern A (real outage):       4 gaps  (e.g. 2025-09-30 11.0h)
    Pattern B (AFK+window down):   3 gaps
    Pattern C (AFK-only down):    25 gaps (including the 30-day one)

Pattern C is the dominant pattern — and the one that's most misleading
without this detection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .activitywatch_models import AWEvent
from .activitywatch_raw import afk_events, web_events, window_events

__all__ = [
    "OUTAGE_THRESHOLD_S",
    "DataOutage",
    "detect_data_outages",
]


# Threshold for considering a bucket "silent enough" to indicate an outage.
# Below this, we treat the gap as ordinary operator AFK / inactivity. Above,
# we expect at least one heartbeat from a healthy watcher.
OUTAGE_THRESHOLD_S: float = 30 * 60  # 30 minutes


@dataclass(frozen=True)
class DataOutage:
    """A period during which AW data was unavailable for non-AFK reasons.

    ``pattern``:
      - ``"A"``: all three buckets (afk + window + web) silent
      - ``"B"``: afk + window silent, web watcher still emitted events
      - ``"C"``: only afk silent; window + web were running

    ``start`` / ``end`` bound the outage. ``other_bucket_event_counts``
    reports how many events each bucket had during this interval (zero
    for the silent ones, non-zero for the others).
    """
    start: datetime
    end: datetime
    pattern: str
    afk_events: int
    window_events: int
    web_events: int

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def _gaps_in_bucket(
    events: Iterable[AWEvent],
    *,
    threshold_s: float,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return gaps ≥ threshold_s between consecutive event endtimes and the
    next event's starttime, clipped to [start, end]."""
    sorted_events = sorted(events, key=lambda e: e.start)
    gaps: list[tuple[datetime, datetime]] = []
    prev_end = start
    for ev in sorted_events:
        if ev.start < prev_end:
            prev_end = max(prev_end, ev.end)
            continue
        if (ev.start - prev_end).total_seconds() >= threshold_s:
            gaps.append((prev_end, ev.start))
        prev_end = max(prev_end, ev.end)
    if (end - prev_end).total_seconds() >= threshold_s:
        gaps.append((prev_end, end))
    return gaps


def _count_events_in_window(
    events: Iterable[AWEvent],
    *,
    start: datetime,
    end: datetime,
) -> int:
    """Number of events whose starttime falls inside [start, end]."""
    return sum(1 for ev in events if start <= ev.start < end)


def detect_data_outages(
    *,
    start: datetime,
    end: datetime,
    threshold_s: float = OUTAGE_THRESHOLD_S,
) -> list[DataOutage]:
    """Yield AW data outages over [start, end).

    Algorithm:
      1. Find gaps ≥ threshold_s in afk-bucket coverage.
      2. For each gap, count events in window + web buckets during it.
      3. Classify by which buckets had events:
         all silent → pattern A (real outage)
         only web has events → pattern B (awatcher process died)
         window+web both have events → pattern C (afk-only down)
      4. Skip gaps shorter than threshold (operator AFK).
    """
    afk = list(afk_events(start=start, end=end))
    if not afk:
        # No AFK data at all → the entire window is an outage of some kind.
        # Determine which other buckets have data to classify.
        win = list(window_events(start=start, end=end))
        web = list(web_events(start=start, end=end))
        pattern = "A" if not win and not web else "B" if not win else "C"
        return [DataOutage(
            start=start, end=end, pattern=pattern,
            afk_events=0, window_events=len(win), web_events=len(web),
        )]

    afk_gaps = _gaps_in_bucket(
        afk, threshold_s=threshold_s, start=start, end=end,
    )
    if not afk_gaps:
        return []

    win_all = list(window_events(start=start, end=end))
    web_all = list(web_events(start=start, end=end))

    outages: list[DataOutage] = []
    for gap_start, gap_end in afk_gaps:
        win_n = _count_events_in_window(win_all, start=gap_start, end=gap_end)
        web_n = _count_events_in_window(web_all, start=gap_start, end=gap_end)
        if win_n == 0 and web_n == 0:
            pattern = "A"
        elif win_n == 0:
            pattern = "B"
        else:
            pattern = "C"
        outages.append(DataOutage(
            start=gap_start, end=gap_end, pattern=pattern,
            afk_events=0, window_events=win_n, web_events=web_n,
        ))
    return outages
