"""Sleep regularity and circadian-phase analysis.

Two products over the fused sleep dataset and the per-minute heart-rate bins:

``sleep_regularity`` — Sleep Regularity Index (Phillips et al. 2017: the
percentage of minute pairs 24 h apart in the same sleep/wake state), midpoint
drift, and a weekday/weekend social-jetlag estimate. Regularity is a distinct
axis from duration and often the better predictor.

``circadian_phase`` — per-night nocturnal heart-rate minimum time, the classic
non-invasive proxy for circadian phase, plus its offset from the sleep
midpoint. A stable HR-minimum-to-midpoint offset means sleep is aligned with
internal phase; drift means it is not.

Both are read APIs: no materialized product is written, and coverage gaps are
reported rather than filled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

__all__ = [
    "RegularityResult",
    "NightPhase",
    "PhaseResult",
    "sleep_regularity",
    "circadian_phase",
]

_MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class RegularityResult:
    start: date
    end: date
    sri: Optional[float]              # 0-100; 100 = perfectly regular
    sri_minute_pairs: int
    midpoint_mean_hour: Optional[float]   # decimal hour, may exceed 24 (post-midnight)
    midpoint_sd_minutes: Optional[float]  # circular SD of sleep midpoint
    social_jetlag_minutes: Optional[float]  # |free-day midpoint - work-day midpoint|
    nights: int
    covered_days: int
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class NightPhase:
    date: date
    sleep_midpoint: datetime
    hr_min_time: Optional[datetime]
    hr_min_bpm: Optional[float]
    offset_minutes: Optional[float]  # hr_min_time - midpoint; negative = HR min earlier
    hr_samples: int


@dataclass(frozen=True)
class PhaseResult:
    nights: tuple[NightPhase, ...]
    offset_mean_minutes: Optional[float]
    offset_sd_minutes: Optional[float]
    resting_hr_p05: Optional[float]  # 5th percentile of nocturnal HR across window
    caveats: tuple[str, ...]


# ── Sleep regularity ─────────────────────────────────────────────────────────


def _night_intervals(start: date, end: date) -> list[tuple[datetime, datetime, date]]:
    from ..sources.sleep import entries_in_range

    intervals: list[tuple[datetime, datetime, date]] = []
    for entry in entries_in_range(start=start, end=end, canonical=True):
        if entry.is_nap or not entry.segments:
            continue
        first = entry.segments[0].start
        last = entry.segments[-1].end
        if first == datetime.min or last <= first:
            continue
        intervals.append((first.replace(tzinfo=None), last.replace(tzinfo=None), entry.date))
    intervals.sort()
    return intervals


def _asleep_minute_set(intervals: Iterable[tuple[datetime, datetime, date]]) -> set[int]:
    """Absolute minute indices (epoch-minute) marked asleep."""
    asleep: set[int] = set()
    for begin, finish, _ in intervals:
        b = int(begin.timestamp() // 60)
        f = int(finish.timestamp() // 60)
        asleep.update(range(b, f))
    return asleep


def _circular_stats(hours: list[float]) -> tuple[Optional[float], Optional[float]]:
    """Circular mean (decimal hour 0-24) and SD (minutes) of clock times."""
    if not hours:
        return None, None
    angles = [h / 24.0 * 2 * math.pi for h in hours]
    sin_sum = sum(math.sin(a) for a in angles) / len(angles)
    cos_sum = sum(math.cos(a) for a in angles) / len(angles)
    mean_angle = math.atan2(sin_sum, cos_sum)
    mean_hour = (mean_angle / (2 * math.pi) * 24.0) % 24.0
    r = math.sqrt(sin_sum**2 + cos_sum**2)
    if r <= 0 or r >= 1:
        return round(mean_hour, 2), 0.0 if r >= 1 else None
    sd_hours = math.sqrt(-2 * math.log(r)) / (2 * math.pi) * 24.0
    return round(mean_hour, 2), round(sd_hours * 60.0, 1)


def sleep_regularity(*, start: date, end: date) -> RegularityResult:
    """Sleep Regularity Index, midpoint stability, and social jetlag."""
    intervals = _night_intervals(start, end)
    if not intervals:
        return RegularityResult(
            start=start, end=end, sri=None, sri_minute_pairs=0,
            midpoint_mean_hour=None, midpoint_sd_minutes=None,
            social_jetlag_minutes=None, nights=0, covered_days=0,
            caveats=("no canonical non-nap nights in range",),
        )

    asleep = _asleep_minute_set(intervals)
    covered_days = len({d for _, _, d in intervals})

    # SRI: only compare minute m and m+1440 when BOTH days have a tracked
    # night, so absent tracking is never scored as "awake" (missing != wake).
    tracked_days = {d for _, _, d in intervals}
    day_start = {
        d: int(datetime.combine(d, datetime.min.time()).timestamp() // 60)
        for d in tracked_days
    }
    agree = 0
    total = 0
    for d in sorted(tracked_days):
        nxt = d + timedelta(days=1)
        if nxt not in tracked_days:
            continue
        base = day_start[d]
        # Compare the 24 h starting at noon (keeps a night intact in one window)
        for offset in range(720, 720 + _MINUTES_PER_DAY):
            m = base + offset
            total += 1
            if (m in asleep) == ((m + _MINUTES_PER_DAY) in asleep):
                agree += 1
    sri = round(200.0 * (agree / total) - 100.0, 1) if total else None

    midpoints: list[tuple[date, float]] = []
    for begin, finish, d in intervals:
        mid = begin + (finish - begin) / 2
        midpoints.append((d, mid.hour + mid.minute / 60.0))
    mean_hour, sd_minutes = _circular_stats([h for _, h in midpoints])

    # Social jetlag: free days (Sat/Sun night-of) vs work days.
    free = [h for d, h in midpoints if d.weekday() >= 4]  # Fri/Sat nights
    work = [h for d, h in midpoints if d.weekday() < 4]
    jetlag: Optional[float] = None
    if len(free) >= 3 and len(work) >= 3:
        fm, _ = _circular_stats(free)
        wm, _ = _circular_stats(work)
        if fm is not None and wm is not None:
            diff = (fm - wm + 12) % 24 - 12
            jetlag = round(abs(diff) * 60.0, 1)

    caveats = [
        "SRI compares only day pairs where BOTH days have a tracked night; "
        "untracked days are excluded, not scored as wake.",
        "Sleep intervals come from canonical (one-per-date) non-nap records, so "
        "unlogged naps and untracked nights are invisible.",
    ]
    if covered_days < (end - start).days * 0.5:
        caveats.append(
            f"sparse coverage: {covered_days} tracked days over a "
            f"{(end - start).days}-day window — treat SRI as indicative only."
        )
    return RegularityResult(
        start=start, end=end, sri=sri, sri_minute_pairs=total,
        midpoint_mean_hour=mean_hour, midpoint_sd_minutes=sd_minutes,
        social_jetlag_minutes=jetlag, nights=len(intervals),
        covered_days=covered_days, caveats=tuple(caveats),
    )


# ── Circadian phase from nocturnal heart rate ────────────────────────────────


def _hr_minute_samples(start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Per-minute HR samples (expanded from binning_data) in a time range."""
    from ..core.parse import parse_datetime as _parse_dt, safe_float
    from ..sources.sleep import _load_jsonl

    # Sleep intervals are naive local wall-clock (see _night_intervals), so
    # every HR timestamp is reduced to the same naive local representation
    # before comparison — mixing aware and naive datetimes raises.
    def _local_naive(value: datetime) -> datetime:
        return value.replace(tzinfo=None)

    samples: list[tuple[datetime, float]] = []
    for row in _load_jsonl("health_heart_rate.jsonl"):
        row_start = _parse_dt(row.get("start_time"))
        if row_start is None:
            continue
        row_end = _parse_dt(row.get("end_time")) or row_start
        naive_start = _local_naive(row_start)
        naive_end = _local_naive(row_end)
        if naive_end < start or naive_start > end:
            continue
        bins = row.get("binning_data")
        if isinstance(bins, list) and bins:
            tz = row_start.tzinfo
            for entry in bins:
                if not isinstance(entry, dict):
                    continue
                bpm = entry.get("heart_rate")
                ts = entry.get("start_time")
                if not isinstance(bpm, (int, float)) or not isinstance(ts, (int, float)):
                    continue
                when = datetime.fromtimestamp(ts / 1000.0, tz=tz)
                naive_when = _local_naive(when)
                if start <= naive_when <= end:
                    samples.append((naive_when, float(bpm)))
        else:
            bpm = safe_float(row.get("heart_rate"))
            if bpm is not None:
                mid = naive_start + (naive_end - naive_start) / 2
                if start <= mid <= end:
                    samples.append((mid, bpm))
    samples.sort()
    return samples


def circadian_phase(
    *, start: date, end: date, smooth_minutes: int = 30
) -> PhaseResult:
    """Nocturnal HR minimum time per night and its offset from sleep midpoint.

    The smoothed nocturnal HR minimum is a standard non-invasive circadian
    phase proxy. A stable offset from the sleep midpoint means sleep timing
    tracks internal phase; a drifting one means it does not.
    """
    intervals = _night_intervals(start, end)
    if not intervals:
        return PhaseResult((), None, None, None, ("no canonical non-nap nights in range",))

    window_start = min(b for b, _, _ in intervals) - timedelta(hours=2)
    window_end = max(f for _, f, _ in intervals) + timedelta(hours=2)
    samples = _hr_minute_samples(window_start, window_end)

    from bisect import bisect_left

    times = [t for t, _ in samples]
    nights: list[NightPhase] = []
    all_values: list[float] = []
    offsets: list[float] = []

    for begin, finish, d in intervals:
        lo = bisect_left(times, begin)
        hi = bisect_left(times, finish)
        window = samples[lo:hi]
        midpoint = begin + (finish - begin) / 2
        if len(window) < smooth_minutes:
            nights.append(NightPhase(d, midpoint, None, None, None, len(window)))
            continue
        values = [v for _, v in window]
        all_values.extend(values)
        # Moving average over smooth_minutes samples; take the argmin centre.
        best_mean = None
        best_idx = 0
        run = sum(values[:smooth_minutes])
        best_mean = run
        for i in range(1, len(values) - smooth_minutes + 1):
            run += values[i + smooth_minutes - 1] - values[i - 1]
            if run < best_mean:
                best_mean = run
                best_idx = i
        centre = window[best_idx + smooth_minutes // 2][0]
        bpm = round(best_mean / smooth_minutes, 1)
        offset = (centre - midpoint).total_seconds() / 60.0
        offsets.append(offset)
        nights.append(
            NightPhase(d, midpoint, centre, bpm, round(offset, 1), len(window))
        )

    mean_off = sd_off = None
    if len(offsets) >= 2:
        mean_off = round(sum(offsets) / len(offsets), 1)
        var = sum((o - mean_off) ** 2 for o in offsets) / (len(offsets) - 1)
        sd_off = round(math.sqrt(var), 1)
    resting = None
    if all_values:
        ordered = sorted(all_values)
        resting = round(ordered[max(int(len(ordered) * 0.05) - 1, 0)], 1)

    return PhaseResult(
        nights=tuple(nights),
        offset_mean_minutes=mean_off,
        offset_sd_minutes=sd_off,
        resting_hr_p05=resting,
        caveats=(
            "HR minimum is a phase PROXY, not melatonin/core-temperature phase.",
            "Coverage is bounded by heart-rate binning_data (2022-08 onward, and "
            "only nights the watch was worn); nights without enough samples report "
            "hr_min_time=None rather than a guess.",
            f"minimum located by a {smooth_minutes}-minute moving average.",
        ),
    )
