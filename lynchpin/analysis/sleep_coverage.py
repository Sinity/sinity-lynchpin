"""Per-date sleep-coverage classification: absence is not zero.

A missing sleep record can mean three very different things, and collapsing
them into "untracked" silently corrupts any rate, mean, or regularity index
computed over the window:

- the watch was off or uncharged, so nothing is known about that date;
- the operator was awake through it (a real all-nighter), which is genuine
  behavioural signal;
- the operator slept and the watch simply failed to score it.

This module separates them using evidence that does not depend on sleep
records existing at all:

``hr_minutes``
    Count of per-minute heart-rate samples on the date. The watch only emits
    these while worn, so **zero HR minutes is the watch-off detector** — the
    date is UNKNOWN and must be excluded, never counted as an unslept night.

``pc_quiet_minutes``
    Longest continuous block with no ActivityWatch activity and no keystrokes.
    Per the operator's own stated prior — essentially all available awake time
    is spent at the PC — a multi-hour PC-silent block is strong evidence of
    sleep, and continuous overnight PC activity is strong evidence against it.

Verdicts:

=========================== ==================================================
``tracked``                 a sleep record exists
``unknown_no_sensor``       no record, no HR minutes: watch off, EXCLUDE
``unknown_no_pc_evidence``  no record, HR present, but the date predates PC
                            evidence coverage
``evidenced_sleep_unrecorded`` HR present, long PC-silent block: slept, unscored
``evidenced_awake``         HR present, PC active throughout: genuinely awake
``ambiguous``               HR present, PC evidence inconclusive
=========================== ==================================================

Every evidence source is coverage-bounded and the bounds differ by years, so
``classify_coverage`` reports them per source rather than assuming absence
means silence.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

__all__ = [
    "CoverageVerdict",
    "CoverageSummary",
    "classify_coverage",
    "TRACKED",
    "UNKNOWN_NO_SENSOR",
    "UNKNOWN_NO_PC_EVIDENCE",
    "EVIDENCED_SLEEP_UNRECORDED",
    "EVIDENCED_AWAKE",
    "AMBIGUOUS",
    "UNKNOWN_CLASSES",
]

TRACKED = "tracked"
UNKNOWN_NO_SENSOR = "unknown_no_sensor"
UNKNOWN_NO_PC_EVIDENCE = "unknown_no_pc_evidence"
EVIDENCED_SLEEP_UNRECORDED = "evidenced_sleep_unrecorded"
EVIDENCED_AWAKE = "evidenced_awake"
AMBIGUOUS = "ambiguous"

#: Classes that must be EXCLUDED from denominators, not counted as unslept.
UNKNOWN_CLASSES = frozenset({UNKNOWN_NO_SENSOR, UNKNOWN_NO_PC_EVIDENCE, AMBIGUOUS})

# A logical day runs 06:00 -> 06:00. The operator's measured sleep midpoint is
# ~07:26 with a 3.3 h SD, so sleep frequently runs morning-into-afternoon;
# a conventional 22:00-08:00 "overnight" window would miss it entirely.
_DAY_START_HOUR = 6

_SLEEP_QUIET_MINUTES = 240.0   # >=4 h PC-silent => slept
_AWAKE_ACTIVE_MINUTES = 240.0  # >=4 h PC-active with no long quiet => awake
# Below this much PC activity across a whole logical day, PC silence tells us
# nothing: the operator was away from the computer, not necessarily asleep.
# Measured 2026-08-03: 2025-10-18 and 2025-10-21 both showed HR present with
# pc_active=0 and pc_quiet=1440, which the first version of this classifier
# read as a 24-hour sleep. Silence only implies sleep against a backdrop of
# ordinary computer use.
_MIN_DAY_PC_ACTIVITY = 30.0


@dataclass(frozen=True)
class CoverageVerdict:
    date: date
    verdict: str
    sleep_minutes: Optional[float]
    hr_minutes: int
    pc_active_minutes: Optional[float]
    pc_quiet_minutes: Optional[float]
    keypresses: Optional[int]
    evidence: tuple[str, ...]

    @property
    def is_unknown(self) -> bool:
        return self.verdict in UNKNOWN_CLASSES


@dataclass(frozen=True)
class CoverageSummary:
    start: date
    end: date
    verdicts: tuple[CoverageVerdict, ...]
    counts: dict[str, int]
    evidence_windows: dict[str, Optional[tuple[date, date]]]
    caveats: tuple[str, ...]

    @property
    def analyzable_days(self) -> int:
        """Days that may legitimately enter a denominator."""
        return sum(1 for v in self.verdicts if not v.is_unknown)

    @property
    def excluded_days(self) -> int:
        return sum(1 for v in self.verdicts if v.is_unknown)


def _logical_day(moment: datetime) -> date:
    day = moment.date()
    if moment.hour < _DAY_START_HOUR:
        day -= timedelta(days=1)
    return day


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(_DAY_START_HOUR))
    return start, start + timedelta(days=1)


def _hr_minute_index(start: datetime, end: datetime) -> list[datetime]:
    """Sorted naive-local timestamps of every per-minute HR sample in range."""
    from ..core.parse import parse_datetime as _parse_dt, safe_float
    from ..sources.sleep import _load_jsonl

    stamps: list[datetime] = []
    for row in _load_jsonl("health_heart_rate.jsonl"):
        row_start = _parse_dt(row.get("start_time"))
        if row_start is None:
            continue
        row_end = _parse_dt(row.get("end_time")) or row_start
        naive_start = row_start.replace(tzinfo=None)
        naive_end = row_end.replace(tzinfo=None)
        if naive_end < start or naive_start > end:
            continue
        bins = row.get("binning_data")
        if isinstance(bins, list) and bins:
            tz = row_start.tzinfo
            for entry in bins:
                if not isinstance(entry, dict):
                    continue
                ts = entry.get("start_time")
                if not isinstance(ts, (int, float)):
                    continue
                when = datetime.fromtimestamp(ts / 1000.0, tz=tz).replace(tzinfo=None)
                if start <= when <= end:
                    stamps.append(when)
        elif safe_float(row.get("heart_rate")) is not None:
            mid = naive_start + (naive_end - naive_start) / 2
            if start <= mid <= end:
                stamps.append(mid)
    stamps.sort()
    return stamps


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def _longest_quiet(
    busy: list[tuple[datetime, datetime]], window: tuple[datetime, datetime]
) -> float:
    """Longest gap (minutes) inside ``window`` not covered by ``busy``."""
    lo, hi = window
    clipped = [
        (max(s, lo), min(e, hi)) for s, e in busy if e > lo and s < hi
    ]
    merged = _merge(clipped)
    longest = 0.0
    cursor = lo
    for s, e in merged:
        longest = max(longest, (s - cursor).total_seconds() / 60.0)
        cursor = max(cursor, e)
    longest = max(longest, (hi - cursor).total_seconds() / 60.0)
    return longest


def classify_coverage(
    *,
    start: date,
    end: date,
    sleep_quiet_minutes: float = _SLEEP_QUIET_MINUTES,
    awake_active_minutes: float = _AWAKE_ACTIVE_MINUTES,
    include_activitywatch: bool = False,
) -> CoverageSummary:
    """Classify every logical date in range by what is actually known about it.

    PC-activity evidence defaults to keystroke timestamps, which are event-level
    and cheap. ``include_activitywatch=True`` additionally folds in AW active
    intervals for a broader definition of "at the computer" (mouse-only or
    media-only activity with no typing), at the cost of a slow cold scan over
    the AW store — avoid it while a materialization run holds that data.
    """
    from ..sources.sleep_composite import night_composites

    composites = {c.date: c for c in night_composites(start=start, end=end)}

    win_start, _ = _day_bounds(start)
    _, win_end = _day_bounds(end)

    hr_stamps = _hr_minute_index(win_start, win_end)

    # ---- PC-activity evidence, coverage-bounded.
    # Keystroke events are the primary signal: event-level, cheap, and already
    # the thing AW's own AFK repair trusts over awatcher's fabricated activity.
    busy_intervals: list[tuple[datetime, datetime]] = []
    key_by_date: dict[date, int] = {}
    key_window: Optional[tuple[date, date]] = None
    press_times: list[datetime] = []
    evidence_error: Optional[str] = None
    try:
        from ..sources.keylog import coverage_bounds as kl_bounds, keypresses

        bounds = kl_bounds()
        if bounds is not None:
            key_window = (bounds.first, bounds.last)
        for event in keypresses(start=win_start, end=win_end):
            ts = event.ts.replace(tzinfo=None) if event.ts.tzinfo else event.ts
            press_times.append(ts)
            key_by_date[_logical_day(ts)] = key_by_date.get(_logical_day(ts), 0) + 1
        press_times.sort()
        # A keystroke marks its minute as "at the computer".
        busy_intervals.extend(
            (t, t + timedelta(minutes=1)) for t in press_times
        )
    except Exception as exc:  # noqa: BLE001 - keylog capture may be absent
        evidence_error = f"keylog {type(exc).__name__}: {exc}"

    aw_window: Optional[tuple[date, date]] = None
    if include_activitywatch:
        try:
            from ..core.parse import as_local
            from ..sources.activitywatch import (
                active_intervals,
                coverage_bounds as aw_bounds,
            )

            bounds = aw_bounds()
            if bounds is not None:
                aw_window = (bounds.first, bounds.last)
            busy_intervals.extend(
                (as_local(a).replace(tzinfo=None), as_local(b).replace(tzinfo=None))
                for a, b in active_intervals(start=win_start, end=win_end)
            )
        except Exception as exc:  # noqa: BLE001 - AW may be mid-materialization
            evidence_error = f"{evidence_error or ''} activitywatch {type(exc).__name__}".strip()

    busy_intervals.sort()

    hr_window: Optional[tuple[date, date]] = None
    if hr_stamps:
        hr_window = (hr_stamps[0].date(), hr_stamps[-1].date())

    verdicts: list[CoverageVerdict] = []
    day = start
    while day <= end:
        lo, hi = _day_bounds(day)
        composite = composites.get(day)

        hr_count = bisect_left(hr_stamps, hi) - bisect_left(hr_stamps, lo)

        pc_known = (
            (aw_window is not None and aw_window[0] <= day <= aw_window[1])
            or (key_window is not None and key_window[0] <= day <= key_window[1])
        )
        pc_active: Optional[float] = None
        pc_quiet: Optional[float] = None
        if pc_known:
            clipped = [(s, e) for s, e in busy_intervals if e > lo and s < hi]
            pc_active = sum(
                (min(e, hi) - max(s, lo)).total_seconds() / 60.0 for s, e in _merge(clipped)
            )
            pc_quiet = _longest_quiet(clipped, (lo, hi))

        keypresses = key_by_date.get(day)
        evidence: list[str] = [f"hr_minutes={hr_count}"]
        if pc_active is not None:
            evidence.append(f"pc_active={pc_active:.0f}m")
            evidence.append(f"pc_quiet={pc_quiet:.0f}m")
        if keypresses is not None:
            evidence.append(f"keypresses={keypresses}")

        if composite is not None:
            verdict = TRACKED
        elif hr_count == 0:
            verdict = UNKNOWN_NO_SENSOR
            evidence.append("no HR samples: watch off/uncharged")
        elif not pc_known:
            verdict = UNKNOWN_NO_PC_EVIDENCE
            evidence.append("date predates PC-activity coverage")
        elif pc_active is not None and pc_active < _MIN_DAY_PC_ACTIVITY:
            verdict = AMBIGUOUS
            evidence.append(
                f"PC evidence unusable: only {pc_active:.0f}m computer use all day "
                "(away from PC, so silence is not sleep evidence)"
            )
        elif pc_quiet is not None and pc_quiet >= sleep_quiet_minutes:
            verdict = EVIDENCED_SLEEP_UNRECORDED
            evidence.append(f"PC silent {pc_quiet:.0f}m >= {sleep_quiet_minutes:.0f}m")
        elif pc_active is not None and pc_active >= awake_active_minutes:
            verdict = EVIDENCED_AWAKE
            evidence.append(f"PC active {pc_active:.0f}m, no long quiet block")
        else:
            verdict = AMBIGUOUS
            evidence.append("PC evidence inconclusive")

        verdicts.append(
            CoverageVerdict(
                date=day,
                verdict=verdict,
                sleep_minutes=composite.main_asleep_minutes if composite else None,
                hr_minutes=hr_count,
                pc_active_minutes=round(pc_active, 1) if pc_active is not None else None,
                pc_quiet_minutes=round(pc_quiet, 1) if pc_quiet is not None else None,
                keypresses=keypresses,
                evidence=tuple(evidence),
            )
        )
        day += timedelta(days=1)

    counts: dict[str, int] = {}
    for v in verdicts:
        counts[v.verdict] = counts.get(v.verdict, 0) + 1

    caveats = [
        "Zero HR minutes means the watch was not worn: the date is UNKNOWN and is "
        "excluded from denominators, never counted as an unslept night.",
        "PC-silence as a sleep indicator rests on the operator's stated prior that "
        "essentially all awake time is spent at the computer; it is strong here and "
        "would not transfer to someone else.",
        "Evidence sources have very different coverage windows (see evidence_windows); "
        "a date outside PC coverage is unknown, not evidence of anything.",
        "A day with almost no computer use at all is AMBIGUOUS, not slept: silence "
        "only implies sleep against a backdrop of ordinary PC activity.",
    ]
    if evidence_error:
        caveats.append(f"PC evidence degraded ({evidence_error}).")
    if not include_activitywatch:
        caveats.append(
            "PC activity measured from keystrokes only; mouse-only or media-only "
            "computer use reads as quiet. Pass include_activitywatch=True to fold "
            "in AW active intervals (slow cold scan)."
        )

    return CoverageSummary(
        start=start,
        end=end,
        verdicts=tuple(verdicts),
        counts=counts,
        evidence_windows={
            "heart_rate": hr_window,
            "activitywatch": aw_window,
            "keylog": key_window,
        },
        caveats=tuple(caveats),
    )
