"""Hour-level dose → activity response curves (within-day design).

Day-level dose ↔ activity correlations (daytime_correlates) cannot separate
"stimulants extend activity" from "long days invite dosing". This module uses
the within-day contrast the bead (lynchpin-9bo) specifies: for each first
timed dose of a day, compare activity in the response window (+1..+6 h after
the dose hour) against the same day's pre-dose baseline (−3..−1 h), then
against the same clock-hours on non-dose days. The day is its own control,
which removes between-day level differences; the matched-clock-hour control
removes circadian shape.

Honesty notes:
- Only the FIRST timed dose per day enters (later doses sit inside the first
  dose's response window and would contaminate both windows). Untimed doses
  exclude the day from the event set but keep it out of controls too.
- Non-dose control days carry the operator's stated logging caveat: some
  "no use" days are unlogged use. Contaminated controls shrink the contrast,
  so reported uplifts are conservative; nulls are weaker than their p-values
  suggest.
- The design controls the "dosed because the day was going to be long" level
  confounder, not reverse causation within the day (dosing *because* about to
  start working). The pre-dose baseline dips visible in the curve speak to
  that directly — read the curve, not just the scalar.
- Hourly activity comes from the AW-derived circadian product, read as-is
  (never triggers materialization); rows before 2024-02-15 are dropped
  (lynchpin-3yp phantom guard).
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import date
from math import sqrt
from pathlib import Path
from typing import Optional


#: Relative hours reported in the response curve (dose hour = 0).
CURVE_HOURS = tuple(range(-3, 9))
BASELINE_HOURS = (-3, -2, -1)
RESPONSE_HOURS = (1, 2, 3, 4, 5, 6)
#: Guard against lynchpin-3yp phantom rows in the circadian product.
MIN_PRODUCT_DATE = date(2024, 2, 15)


@dataclass(frozen=True)
class DoseEvent:
    day: date
    hour: int
    substance: str
    amount_mg: Optional[float]


@dataclass(frozen=True)
class DoseResponseReport:
    substance_filter: Optional[str]
    n_events: int
    n_control_days: int
    n_days_excluded_untimed: int
    n_events_dropped_edge: int
    #: mean(response) − mean(baseline) on dose days, minutes-active per hour.
    mean_within_day_uplift: Optional[float]
    #: the same contrast on control days at the matched dose hours.
    mean_control_uplift: Optional[float]
    #: uplift minus control uplift — the headline number.
    net_uplift_min_per_h: Optional[float]
    #: two-tailed t p-value across per-event net effects.
    p_value: Optional[float]
    #: mean activity by hour relative to dose, dose days vs matched controls.
    curve_dose: dict[int, float] = field(default_factory=dict)
    curve_control: dict[int, float] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()


def _circadian_paths() -> tuple[Path, ...]:
    from ..sources.activitywatch_derived import (
        activitywatch_derived_manifest_path,
        activitywatch_derived_path,
        activitywatch_derived_product_paths,
    )

    partitioned = activitywatch_derived_product_paths("circadian")
    manifest_path = activitywatch_derived_manifest_path()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    raw_products = manifest.get("product_paths") if isinstance(manifest, dict) else None
    if isinstance(raw_products, dict) and isinstance(raw_products.get("circadian"), dict):
        return tuple(path for _, path in sorted(partitioned.items()))
    return (activitywatch_derived_path("circadian"),)


def load_hourly_activity(path: Optional[Path] = None) -> dict[date, dict[int, float]]:
    """{day: {hour: active_min}} from the circadian product, phantom-guarded.

    A day present in the product with a missing hour row genuinely had zero
    active minutes that hour (the product emits rows for active hours only),
    so consumers treat absent hours on covered days as 0.0.
    """
    paths = (path,) if path is not None else _circadian_paths()
    out: dict[date, dict[int, float]] = {}
    for src in paths:
        if not src.exists():
            continue
        with src.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    day = date.fromisoformat(str(row["date"]))
                    hour = int(row["hour"])
                    active = float(row.get("active_min") or 0.0)
                except (KeyError, TypeError, ValueError):
                    continue
                if day < MIN_PRODUCT_DATE:
                    continue
                out.setdefault(day, {})[hour] = active
    return out


def first_timed_doses(
    *,
    start: date,
    end: date,
    substance: Optional[str] = None,
) -> tuple[list[DoseEvent], set[date], int]:
    """(first timed dose per day, all dose days, days excluded for untimed).

    ``substance`` filters by exact name; None = any entry. Dose *days* are
    returned in full (timed or not) so control selection can exclude them.
    """
    from ..sources.substance import SubstanceEntry, entries_in_range

    by_day: dict[date, list[SubstanceEntry]] = {}
    for entry in entries_in_range(start=start, end=end):
        if substance is not None and entry.substance != substance:
            continue
        by_day.setdefault(entry.date, []).append(entry)

    events: list[DoseEvent] = []
    untimed_days = 0
    for day, day_entries in sorted(by_day.items()):
        timed = sorted(
            ((e.time, e) for e in day_entries if e.time is not None),
            key=lambda pair: pair[0],
        )
        if not timed:
            untimed_days += 1
            continue
        first_time, first = timed[0]
        events.append(
            DoseEvent(
                day=day,
                hour=first_time.hour,
                substance=first.substance,
                amount_mg=first.amount_mg,
            )
        )
    return events, set(by_day), untimed_days


def _window_mean(day_hours: dict[int, float], hours: tuple[int, ...], base: int) -> Optional[float]:
    values = []
    for offset in hours:
        h = base + offset
        if not 0 <= h <= 23:
            return None  # window crosses the day edge — caller drops the event
        values.append(day_hours.get(h, 0.0))
    return statistics.mean(values)


def analyze_dose_response(
    *,
    start: date,
    end: date,
    substance: Optional[str] = None,
    activity: Optional[dict[date, dict[int, float]]] = None,
) -> DoseResponseReport:
    hourly = activity if activity is not None else load_hourly_activity()
    events, dose_days, untimed_days = first_timed_doses(
        start=start, end=end, substance=substance
    )

    # Controls must be trustworthy zeros: only days inside comprehensive-
    # logging periods qualify (operator logs in regimes; a quiet day outside
    # a regime may be unlogged use). Synthetic-activity tests pass their own
    # activity dict and dose entries whose days all fall inside one period.
    from ..sources.substance import in_logging_period, logging_periods

    periods = logging_periods()
    control_days = [
        day
        for day in sorted(hourly)
        if start <= day <= end
        and day not in dose_days
        and in_logging_period(day, periods)
    ]

    per_event_net: list[float] = []
    dropped_edge = 0
    curve_dose_acc: dict[int, list[float]] = {k: [] for k in CURVE_HOURS}
    curve_ctrl_acc: dict[int, list[float]] = {k: [] for k in CURVE_HOURS}
    uplifts_dose: list[float] = []
    uplifts_ctrl: list[float] = []

    for event in events:
        day_hours = hourly.get(event.day)
        if day_hours is None:
            continue  # day not covered by the activity product
        baseline = _window_mean(day_hours, BASELINE_HOURS, event.hour)
        response = _window_mean(day_hours, RESPONSE_HOURS, event.hour)
        if baseline is None or response is None:
            dropped_edge += 1
            continue
        uplift = response - baseline
        uplifts_dose.append(uplift)

        ctrl_uplifts: list[float] = []
        for ctrl_day in control_days:
            ctrl_hours = hourly[ctrl_day]
            ctrl_base = _window_mean(ctrl_hours, BASELINE_HOURS, event.hour)
            ctrl_resp = _window_mean(ctrl_hours, RESPONSE_HOURS, event.hour)
            if ctrl_base is None or ctrl_resp is None:
                continue
            ctrl_uplifts.append(ctrl_resp - ctrl_base)
        if ctrl_uplifts:
            ctrl_mean = statistics.mean(ctrl_uplifts)
            uplifts_ctrl.append(ctrl_mean)
            per_event_net.append(uplift - ctrl_mean)

        for offset in CURVE_HOURS:
            h = event.hour + offset
            if 0 <= h <= 23:
                curve_dose_acc[offset].append(day_hours.get(h, 0.0))
                ctrl_vals = [hourly[d].get(h, 0.0) for d in control_days]
                if ctrl_vals:
                    curve_ctrl_acc[offset].append(statistics.mean(ctrl_vals))

    p_value: Optional[float] = None
    if len(per_event_net) >= 8:
        mean_net = statistics.mean(per_event_net)
        sd = statistics.stdev(per_event_net)
        if sd > 0:
            t = mean_net / (sd / sqrt(len(per_event_net)))
            # normal approximation is adequate at these n; two-tailed
            from statistics import NormalDist

            p_value = 2.0 * (1.0 - NormalDist().cdf(abs(t)))
        else:
            # degenerate zero-variance case (synthetic data): the sign of the
            # mean is the whole story
            p_value = 0.0 if mean_net != 0 else 1.0

    return DoseResponseReport(
        substance_filter=substance,
        n_events=len(per_event_net),
        n_control_days=len(control_days),
        n_days_excluded_untimed=untimed_days,
        n_events_dropped_edge=dropped_edge,
        mean_within_day_uplift=(statistics.mean(uplifts_dose) if uplifts_dose else None),
        mean_control_uplift=(statistics.mean(uplifts_ctrl) if uplifts_ctrl else None),
        net_uplift_min_per_h=(statistics.mean(per_event_net) if per_event_net else None),
        p_value=p_value,
        curve_dose={k: round(statistics.mean(v), 2) for k, v in curve_dose_acc.items() if v},
        curve_control={k: round(statistics.mean(v), 2) for k, v in curve_ctrl_acc.items() if v},
        caveats=(
            "controls carry the operator's logging caveat: some non-dose days are "
            "unlogged use, which attenuates the contrast (uplift is conservative)",
            "within-day design controls day-level confounding, not within-day "
            "reverse causation; read the pre-dose baseline shape in the curve",
            "first timed dose per day only; hourly activity read as-is from the "
            "circadian product (staleness follows that product)",
        ),
    )


__all__ = [
    "BASELINE_HOURS",
    "CURVE_HOURS",
    "DoseEvent",
    "DoseResponseReport",
    "MIN_PRODUCT_DATE",
    "RESPONSE_HOURS",
    "analyze_dose_response",
    "first_timed_doses",
    "load_hourly_activity",
]
