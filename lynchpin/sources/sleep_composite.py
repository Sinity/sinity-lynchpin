"""Night-level composites over the fused sleep dataset.

``canonical_entries()`` deliberately returns ONE record per logical date, chosen
by source priority. That is the right shape for per-night scalars (quality
score, stage percentages) which only exist on a single scored record, and the
wrong shape for anything time-based, for two measured reasons:

1. **Source priority outranks duration.** A 134-minute ``merged`` record beats a
   659-minute ``stage_derived`` record covering the same night, so the canonical
   duration can understate the night badly (2023-05-30 is exactly this).
2. **Genuine fragmentation.** Interrupted nights are recorded as several
   sequential records with gaps; canonical selection keeps one and drops the rest.

This module adds the complementary view: same-date records are unioned into
non-overlapping segments, then clustered into *episodes* separated by more than
``gap_threshold_min``. The longest episode is the night's main sleep; anything
else on that logical date is a secondary episode (an unflagged nap, a biphasic
second sleep, or an early-morning continuation).

Episode clustering matters: a logical date spans 06:00 to 06:00, so a naive
union of every same-date record can span 21 hours and is meaningless as a
"night". Measured gap structure over this dataset: inter-segment gaps are
strongly bimodal — 60 gaps under an hour (real fragmentation) against 232 of
three hours or more (distinct episodes) — so the default 90-minute threshold
sits in the empty middle rather than on a slope.

Use composites for duration, timing, and regularity; use the canonical entry
(carried on the composite) for scores and architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

__all__ = [
    "SleepEpisode",
    "NightComposite",
    "night_composites",
    "DEFAULT_GAP_THRESHOLD_MIN",
]

DEFAULT_GAP_THRESHOLD_MIN = 90.0


@dataclass(frozen=True)
class SleepEpisode:
    """One contiguous-ish bout: merged segments separated by short gaps only."""

    start: datetime
    end: datetime
    segments: tuple[tuple[datetime, datetime], ...]
    asleep_minutes: float  # summed segment minutes (gaps excluded)

    @property
    def span_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    @property
    def gap_minutes(self) -> float:
        return max(self.span_minutes - self.asleep_minutes, 0.0)

    @property
    def fragment_count(self) -> int:
        return len(self.segments)


@dataclass(frozen=True)
class NightComposite:
    """All non-nap sleep on one logical date, episode-clustered."""

    date: date
    main: SleepEpisode
    secondary: tuple[SleepEpisode, ...]
    record_count: int
    canonical_source: Optional[str]
    canonical_minutes: float
    # per-night scalars carried through from the canonical record
    score: Optional[float]
    score_estimated: bool
    deep_pct: Optional[float]
    rem_pct: Optional[float]
    hr_avg: Optional[float]

    @property
    def main_asleep_minutes(self) -> float:
        return self.main.asleep_minutes

    @property
    def total_asleep_minutes(self) -> float:
        return self.main.asleep_minutes + sum(e.asleep_minutes for e in self.secondary)

    @property
    def midpoint(self) -> datetime:
        return self.main.start + (self.main.end - self.main.start) / 2

    @property
    def understated_by_canonical_minutes(self) -> float:
        """Minutes of the main episode the canonical record does not represent."""
        return max(self.main.asleep_minutes - self.canonical_minutes, 0.0)

    @property
    def is_fragmented(self) -> bool:
        return self.main.fragment_count > 1


_SOURCE_PRIORITY = {
    "merged": 0,
    "combined_only": 1,
    "saa_only": 2,
    "samsung_only": 3,
    "stage_derived": 4,
    None: 5,
}


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _build_episodes(
    intervals: list[tuple[datetime, datetime]], gap_threshold_min: float
) -> list[SleepEpisode]:
    merged = _merge_intervals(intervals)
    if not merged:
        return []
    groups: list[list[tuple[datetime, datetime]]] = [[merged[0]]]
    for interval in merged[1:]:
        gap = (interval[0] - groups[-1][-1][1]).total_seconds() / 60.0
        if gap > gap_threshold_min:
            groups.append([interval])
        else:
            groups[-1].append(interval)
    return [
        SleepEpisode(
            start=group[0][0],
            end=group[-1][1],
            segments=tuple(group),
            asleep_minutes=sum((e - s).total_seconds() / 60.0 for s, e in group),
        )
        for group in groups
    ]


def night_composites(
    *,
    start: date,
    end: date,
    gap_threshold_min: float = DEFAULT_GAP_THRESHOLD_MIN,
) -> list[NightComposite]:
    """Episode-clustered night composites for each logical date in range."""
    from .sleep import entries_in_range

    by_date: dict[date, list] = {}
    for entry in entries_in_range(start=start, end=end, canonical=False):
        if entry.is_nap or not entry.segments:
            continue
        first = entry.segments[0].start
        last = entry.segments[-1].end
        if first == datetime.min or last <= first:
            continue
        by_date.setdefault(entry.date, []).append(entry)

    composites: list[NightComposite] = []
    for day in sorted(by_date):
        entries = by_date[day]
        intervals = [
            (
                entry.segments[0].start.replace(tzinfo=None),
                entry.segments[-1].end.replace(tzinfo=None),
            )
            for entry in entries
        ]
        episodes = _build_episodes(intervals, gap_threshold_min)
        if not episodes:
            continue
        main_index = max(
            range(len(episodes)), key=lambda i: episodes[i].asleep_minutes
        )
        main = episodes[main_index]
        secondary = tuple(e for i, e in enumerate(episodes) if i != main_index)

        canonical = min(
            entries,
            key=lambda e: (_SOURCE_PRIORITY.get(e.source, 5), -e.total_minutes),
        )
        composites.append(
            NightComposite(
                date=day,
                main=main,
                secondary=secondary,
                record_count=len(entries),
                canonical_source=canonical.source,
                canonical_minutes=canonical.total_minutes,
                score=canonical.effective_score,
                score_estimated=canonical.score_estimated,
                deep_pct=canonical.metrics.deep_pct if canonical.metrics else None,
                rem_pct=canonical.metrics.rem_pct if canonical.metrics else None,
                hr_avg=canonical.signals.hr_avg if canonical.signals else None,
            )
        )
    return composites
