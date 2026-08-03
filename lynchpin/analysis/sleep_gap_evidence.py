"""Adversarial validation of the gaps between same-night sleep records.

``sleep_composite`` unions same-date records into episodes and treats the gaps
between segments as real wakefulness. That assumption is doing heavy lifting:
the wave-3 finding that composite SRI is 5-7 points *lower* than single-record
SRI rests entirely on those gaps being genuine sleep/wake transitions rather
than detection failures. If the watch simply stopped recording mid-night, the
two segments are one sleep block and counting a transition there manufactures
irregularity that did not happen.

This module classifies each gap against sensors that do not depend on the sleep
records existing:

``sensor_absent``
    No heart-rate minutes inside the gap. The watch was off, charging, or not
    reporting — a **detection artifact**. The flanking segments are plausibly
    one block; no transition is counted.
``stage_continuous``
    Samsung's own per-stage stream covers a substantial share of the gap even
    though no *session record* does. The scorer saw sleep there; the session
    boundary is a record-level artifact. No transition.
``confirmed_wake_pc``
    Keystrokes during the gap. Definitively awake — a real transition.
``genuine_wake``
    Heart rate present and elevated relative to the night's own baseline,
    and/or clear movement. Real transition.
``missed_sleep``
    Heart rate present and sleep-like, no movement, no PC activity. The
    operator was most likely asleep through the gap and the watch failed to
    score it. Not a real transition.
``undetermined``
    Heart rate present but the evidence does not separate the cases.
    Counted as a real transition (the conservative choice: it preserves the
    record-level reading rather than silently merging blocks).

Only ``confirmed_wake_pc``, ``genuine_wake`` and ``undetermined`` count as real
transitions; the rest are filled in as sleep for regularity purposes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

__all__ = [
    "GapEvidence",
    "GapReport",
    "classify_night_gaps",
    "evidence_validated_intervals",
    "SENSOR_ABSENT",
    "STAGE_CONTINUOUS",
    "CONFIRMED_WAKE_PC",
    "GENUINE_WAKE",
    "MISSED_SLEEP",
    "UNDETERMINED",
    "REAL_TRANSITION_CLASSES",
]

SENSOR_ABSENT = "sensor_absent"
STAGE_CONTINUOUS = "stage_continuous"
CONFIRMED_WAKE_PC = "confirmed_wake_pc"
GENUINE_WAKE = "genuine_wake"
MISSED_SLEEP = "missed_sleep"
UNDETERMINED = "undetermined"

#: Gap classes that represent a real sleep/wake transition.
REAL_TRANSITION_CLASSES = frozenset({CONFIRMED_WAKE_PC, GENUINE_WAKE, UNDETERMINED})

# A gap counts as stage-covered when Samsung's stage stream fills at least this
# share of it: partial coverage is common at record edges and is not evidence
# that the whole gap was slept through.
_STAGE_COVERAGE_FRACTION = 0.5
# HR at or above this multiple of the night's own baseline reads as arousal.
_WAKE_HR_RATIO = 1.15
_MIN_HR_SAMPLES = 5
_MOVEMENT_WAKE_LEVEL = 2.0


@dataclass(frozen=True)
class GapEvidence:
    night: date
    start: datetime
    end: datetime
    minutes: float
    verdict: str
    hr_minutes: int
    hr_mean: Optional[float]
    hr_ratio: Optional[float]
    movement_mean: Optional[float]
    keypresses: Optional[int]
    stage_minutes: float
    intra_episode: bool

    @property
    def real_transition(self) -> bool:
        return self.verdict in REAL_TRANSITION_CLASSES


@dataclass(frozen=True)
class GapReport:
    gaps: tuple[GapEvidence, ...]
    counts: dict[str, int]
    intra_counts: dict[str, int]
    caveats: tuple[str, ...]

    @property
    def real_transitions(self) -> int:
        return sum(1 for g in self.gaps if g.real_transition)

    @property
    def artifact_gaps(self) -> int:
        return len(self.gaps) - self.real_transitions


def _minute_series(filename: str, key: str, start: date, end: date) -> dict[datetime, float]:
    from .sleep_stage_model import _load_minute_series

    return _load_minute_series(filename, key, start, end)


def _keypress_minutes(start: date, end: date) -> set[datetime]:
    try:
        from ..sources.keylog import keypresses
    except ImportError:  # pragma: no cover - source always present in-tree
        return set()
    lo = datetime.combine(start, datetime.min.time())
    hi = datetime.combine(end + timedelta(days=2), datetime.min.time())
    out: set[datetime] = set()
    try:
        for event in keypresses(start=lo, end=hi):
            ts = event.ts.replace(tzinfo=None) if event.ts.tzinfo else event.ts
            out.add(ts.replace(second=0, microsecond=0))
    except Exception:  # noqa: BLE001 - keylog capture may be absent/partial
        return set()
    return out


def _stage_minutes(start: date, end: date) -> set[datetime]:
    """Minutes covered by ANY Samsung stage record (asleep or awake)."""
    from ..sources.sleep import sleep_stages

    out: set[datetime] = set()
    for record in sleep_stages(start=start, end=end):
        cursor = record.start.replace(tzinfo=None, second=0, microsecond=0)
        finish = record.end.replace(tzinfo=None)
        while cursor < finish:
            out.add(cursor)
            cursor += timedelta(minutes=1)
    return out


def _between(series: dict[datetime, float], lo: datetime, hi: datetime) -> list[float]:
    cursor = lo.replace(second=0, microsecond=0)
    values: list[float] = []
    while cursor < hi:
        value = series.get(cursor)
        if value is not None:
            values.append(value)
        cursor += timedelta(minutes=1)
    return values


def classify_night_gaps(
    *, start: date, end: date, gap_threshold_min: float = 90.0
) -> GapReport:
    """Classify every inter-segment gap on every multi-record night."""
    from ..sources.sleep_composite import night_composites

    composites = night_composites(
        start=start, end=end, gap_threshold_min=gap_threshold_min
    )
    if not composites:
        return GapReport((), {}, {}, ("no composites in range",))

    heart = _minute_series("health_heart_rate.jsonl", "heart_rate", start, end)
    movement = _minute_series("health_movement.jsonl", "activity_level", start, end)
    presses = _keypress_minutes(start, end)
    stages = _stage_minutes(start, end)

    gaps: list[GapEvidence] = []
    for night in composites:
        # Night HR baseline: 20th percentile across the whole logical night.
        night_lo = night.main.start - timedelta(hours=2)
        night_hi = night.main.end + timedelta(hours=2)
        night_hr = _between(heart, night_lo, night_hi)
        baseline = None
        if len(night_hr) >= _MIN_HR_SAMPLES:
            ordered = sorted(night_hr)
            baseline = ordered[max(int(len(ordered) * 0.2) - 1, 0)] or None

        # Intra-episode gaps (fragmentation) plus gaps to secondary episodes.
        boundaries: list[tuple[datetime, datetime, bool]] = []
        for a, b in zip(night.main.segments, night.main.segments[1:]):
            boundaries.append((a[1], b[0], True))
        episodes = sorted(
            [night.main, *night.secondary], key=lambda e: e.start
        )
        for a, b in zip(episodes, episodes[1:]):
            if b.start > a.end:
                boundaries.append((a.end, b.start, False))

        for gap_start, gap_end, intra in boundaries:
            minutes = (gap_end - gap_start).total_seconds() / 60.0
            if minutes <= 0:
                continue
            hr_values = _between(heart, gap_start, gap_end)
            mv_values = _between(movement, gap_start, gap_end)
            stage_hits = sum(
                1
                for m in _iter_minutes(gap_start, gap_end)
                if m in stages
            )
            press_hits = sum(
                1 for m in _iter_minutes(gap_start, gap_end) if m in presses
            )
            hr_mean = statistics.mean(hr_values) if hr_values else None
            hr_ratio = (
                hr_mean / baseline if hr_mean is not None and baseline else None
            )
            mv_mean = statistics.mean(mv_values) if mv_values else None

            verdict = _judge(
                hr_values=hr_values,
                hr_ratio=hr_ratio,
                mv_mean=mv_mean,
                press_hits=press_hits,
                stage_fraction=stage_hits / minutes if minutes else 0.0,
            )
            gaps.append(
                GapEvidence(
                    night=night.date,
                    start=gap_start,
                    end=gap_end,
                    minutes=round(minutes, 1),
                    verdict=verdict,
                    hr_minutes=len(hr_values),
                    hr_mean=round(hr_mean, 1) if hr_mean is not None else None,
                    hr_ratio=round(hr_ratio, 3) if hr_ratio is not None else None,
                    movement_mean=round(mv_mean, 3) if mv_mean is not None else None,
                    keypresses=press_hits,
                    stage_minutes=float(stage_hits),
                    intra_episode=intra,
                )
            )

    counts: dict[str, int] = {}
    intra_counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap.verdict] = counts.get(gap.verdict, 0) + 1
        if gap.intra_episode:
            intra_counts[gap.verdict] = intra_counts.get(gap.verdict, 0) + 1

    return GapReport(
        gaps=tuple(gaps),
        counts=counts,
        intra_counts=intra_counts,
        caveats=(
            "Gap classes are evidence-based but not ground truth: 'missed_sleep' "
            "means the sensors look like sleep, not that sleep is confirmed.",
            "undetermined gaps are counted AS real transitions, the conservative "
            "choice - it preserves the record-level reading instead of silently "
            "merging two blocks into one.",
            "Movement and stage coverage are bounded (movement 2025-05 onward), so "
            "older nights lean more on heart rate alone.",
        ),
    )


def _iter_minutes(lo: datetime, hi: datetime):
    cursor = lo.replace(second=0, microsecond=0)
    while cursor < hi:
        yield cursor
        cursor += timedelta(minutes=1)


def _judge(
    *,
    hr_values: list[float],
    hr_ratio: Optional[float],
    mv_mean: Optional[float],
    press_hits: int,
    stage_fraction: float,
) -> str:
    if press_hits > 0:
        return CONFIRMED_WAKE_PC
    if len(hr_values) < _MIN_HR_SAMPLES:
        return SENSOR_ABSENT
    if stage_fraction >= _STAGE_COVERAGE_FRACTION:
        return STAGE_CONTINUOUS
    if mv_mean is not None and mv_mean >= _MOVEMENT_WAKE_LEVEL:
        return GENUINE_WAKE
    if hr_ratio is not None and hr_ratio >= _WAKE_HR_RATIO:
        return GENUINE_WAKE
    if hr_ratio is not None and hr_ratio < _WAKE_HR_RATIO:
        return MISSED_SLEEP
    return UNDETERMINED


def evidence_validated_intervals(
    *, start: date, end: date, gap_threshold_min: float = 90.0
) -> list[tuple[datetime, datetime, date]]:
    """Night intervals with non-transition INTRA-episode gaps filled as sleep.

    Inter-episode gaps are never filled regardless of their evidence class: the
    composite's episode clustering already decided those separate distinct
    bouts, and overriding it here would manufacture implausible sleep spans.
    """
    from ..sources.sleep_composite import night_composites

    report = classify_night_gaps(
        start=start, end=end, gap_threshold_min=gap_threshold_min
    )
    # Only INTRA-episode gaps are eligible for filling. Episode clustering has
    # already ruled that inter-episode gaps (hours long) separate distinct sleep
    # bouts; filling one because its sensors look sleep-like would merge two
    # bouts into a single implausible 20-hour block. Their classification is
    # still reported, as evidence about whether the clustering threshold is
    # right, but it never feeds interval construction.
    fill: dict[date, list[tuple[datetime, datetime]]] = {}
    for gap in report.gaps:
        if gap.intra_episode and not gap.real_transition:
            fill.setdefault(gap.night, []).append((gap.start, gap.end))

    intervals: list[tuple[datetime, datetime, date]] = []
    for night in night_composites(
        start=start, end=end, gap_threshold_min=gap_threshold_min
    ):
        pieces = list(night.main.segments)
        pieces.extend(fill.get(night.date, []))
        merged: list[tuple[datetime, datetime]] = []
        for seg_start, seg_end in sorted(pieces):
            if merged and seg_start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], seg_end))
            else:
                merged.append((seg_start, seg_end))
        for seg_start, seg_end in merged:
            intervals.append((seg_start, seg_end, night.date))
    intervals.sort()
    return intervals
