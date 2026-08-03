"""Evening computer activity vs the SAME night's sleep.

``sleep_productivity`` looks forward (last night's sleep vs today's output).
This is the reverse join: how late and how much the operator was at the
computer in the hours before bed, against that night's onset, duration,
architecture, and quality.

Exposures per night, measured over a configurable pre-onset window:

- ``active_min`` — ActivityWatch active minutes in the window
- ``last_active_gap_min`` — minutes between the last active moment and sleep
  onset (the "screens down to sleep" gap; small values are the interesting ones)
- ``late_active_min`` — active minutes after a late-hour cutoff (default 23:00)

Outcomes: onset hour, duration, effective score, deep/REM share, nocturnal HR.

Association, not causation, and confounded in an obvious direction: a late
night at the computer and a late bedtime are largely the same event.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

__all__ = [
    "EveningNight",
    "evening_nights",
    "evening_activity_correlation",
]

_OUTCOMES = ("onset_hour", "duration_min", "score", "deep_pct", "rem_pct", "hr_avg")
_EXPOSURES = ("active_min", "last_active_gap_min", "late_active_min")


@dataclass(frozen=True)
class EveningNight:
    date: date
    onset: datetime
    active_min: float
    last_active_gap_min: Optional[float]
    late_active_min: float
    onset_hour: float
    duration_min: Optional[float]
    score: Optional[float]
    deep_pct: Optional[float]
    rem_pct: Optional[float]
    hr_avg: Optional[float]


def evening_nights(
    *,
    start: date,
    end: date,
    window_hours: float = 4.0,
    late_cutoff_hour: int = 23,
) -> list[EveningNight]:
    """Per-night evening-activity exposures joined to same-night sleep."""
    from ..core.parse import as_local
    from ..sources.activitywatch import active_intervals
    from ..sources.sleep import entries_in_range

    nights = [
        entry
        for entry in entries_in_range(start=start, end=end, canonical=True)
        if not entry.is_nap and entry.segments
        and entry.segments[0].start != datetime.min
    ]
    if not nights:
        return []

    span_start = min(n.segments[0].start for n in nights) - timedelta(days=1)
    span_end = max(n.segments[-1].end for n in nights) + timedelta(days=1)
    intervals = sorted(
        (as_local(a).replace(tzinfo=None), as_local(b).replace(tzinfo=None))
        for a, b in active_intervals(start=span_start, end=span_end)
    )

    rows: list[EveningNight] = []
    for night in nights:
        onset = night.segments[0].start.replace(tzinfo=None)
        window_start = onset - timedelta(hours=window_hours)
        late_start = datetime.combine(
            (onset - timedelta(hours=window_hours)).date(), time(late_cutoff_hour)
        )
        if late_start > onset:
            late_start -= timedelta(days=1)

        active = 0.0
        late = 0.0
        last_active: Optional[datetime] = None
        for begin, finish in intervals:
            if finish <= window_start or begin >= onset:
                continue
            overlap_start = max(begin, window_start)
            overlap_end = min(finish, onset)
            minutes = (overlap_end - overlap_start).total_seconds() / 60.0
            if minutes <= 0:
                continue
            active += minutes
            last_active = overlap_end if last_active is None else max(last_active, overlap_end)
            late_overlap_start = max(overlap_start, late_start)
            if overlap_end > late_overlap_start:
                late += (overlap_end - late_overlap_start).total_seconds() / 60.0

        metrics = night.metrics
        rows.append(
            EveningNight(
                date=night.date,
                onset=onset,
                active_min=round(active, 1),
                last_active_gap_min=(
                    round((onset - last_active).total_seconds() / 60.0, 1)
                    if last_active
                    else None
                ),
                late_active_min=round(late, 1),
                onset_hour=round(onset.hour + onset.minute / 60.0, 3),
                duration_min=night.total_minutes or None,
                score=night.effective_score,
                deep_pct=metrics.deep_pct if metrics else None,
                rem_pct=metrics.rem_pct if metrics else None,
                hr_avg=night.signals.hr_avg if night.signals else None,
            )
        )
    return rows


def evening_activity_correlation(
    *,
    start: date,
    end: date,
    window_hours: float = 4.0,
    min_nights: int = 15,
) -> dict[str, object]:
    """Correlate evening-activity exposures with same-night sleep outcomes."""
    from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p

    rows = evening_nights(start=start, end=end, window_hours=window_hours)
    findings: list[dict[str, object]] = []
    pvals: dict[int, float] = {}

    for exposure in _EXPOSURES:
        for outcome in _OUTCOMES:
            pairs = [
                (float(getattr(r, exposure)), float(getattr(r, outcome)))
                for r in rows
                if getattr(r, exposure) is not None and getattr(r, outcome) is not None
            ]
            if len(pairs) < min_nights:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            r = _pearson_r(xs, ys)
            if r is None or not math.isfinite(r):
                continue
            n = len(pairs)
            p = 0.0 if abs(r) >= 0.99999 else _t_test_p(
                r * math.sqrt((n - 2) / (1.0 - r * r)), n - 2
            )
            pvals[len(findings)] = p
            findings.append(
                {"exposure": exposure, "outcome": outcome, "r": round(r, 4), "n": n}
            )

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    for idx, finding in enumerate(findings):
        finding["p_value"] = round(pvals[idx], 4)
        finding["q_value"] = round(qmap[idx], 4)
        finding["significant"] = qmap[idx] < 0.05
    findings.sort(key=lambda f: abs(float(f["r"])), reverse=True)  # type: ignore[arg-type]

    return {
        "findings": findings,
        "nights": len(rows),
        "window_hours": window_hours,
        "caveats": [
            "Same-night association, NOT causation: a late computer session and a "
            "late bedtime are largely the same event, so onset_hour correlations "
            "are near-tautological and included only as a sanity anchor.",
            "ActivityWatch coverage gaps are not zero activity; nights without AW "
            "data still appear with active_min=0 and must be read with the AW "
            "coverage window in mind.",
            "p-values FDR-corrected across the exposure x outcome family.",
        ],
    }
