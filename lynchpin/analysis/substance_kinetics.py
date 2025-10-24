"""Pharmacokinetic active-level modeling over the substance dose log.

``substance_health`` correlates dose EVENTS. This models estimated ACTIVE LEVEL
over time via one-compartment exponential decay (instantaneous absorption), so we
can correlate *exposure* — peak / time-averaged / AUC per logical day — against
HRV/stress/sleep instead of raw dose spikes.

CAVEAT: half-lives are approximate. Well-characterized for caffeine / modafinil /
amphetamine; UNCERTAIN for the research chemicals that dominate this log (see
substance_half_lives.json). Active levels are a *relative exposure proxy*, not a
clinical measurement.
Doses missing a clock time are assumed to occur midday (12:00); doses missing an
amount are skipped. Override via ``HALF_LIVES_HOURS`` or the ``half_lives`` arg.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from ..core.config import get_config

# substance (lowercased) -> (half_life_hours, provenance/uncertainty note).
# Only substances with no personal-use signal (universal, legal, over-the-
# counter pharmacology) are seeded here. Which other substances someone
# tracks doses for is inherently personal, so the rest of the table -- the
# research chemicals and prescription-specific entries -- is loaded from an
# optional external override (see _load_half_lives), same pattern as
# life_phase.py's KNOWN_EVENTS.
_GENERIC_HALF_LIVES_HOURS: dict[str, tuple[float, str]] = {
    "caffeine": (5.0, "plasma t1/2 ~3-7h, CYP1A2-dependent"),
}


def _load_half_lives() -> dict[str, tuple[float, str]]:
    merged = dict(_GENERIC_HALF_LIVES_HOURS)
    path = get_config().derived_root / "local-config" / "substance_half_lives.json"
    try:
        if not path.exists():
            return merged
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for name, entry in raw.items():
                if isinstance(entry, list) and len(entry) == 2:
                    merged[str(name)] = (float(entry[0]), str(entry[1]))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return merged


HALF_LIVES_HOURS: dict[str, tuple[float, str]] = _load_half_lives()


@dataclass(frozen=True)
class SubstanceDay:
    date: date
    substance: str
    peak_mg: float      # max modeled active level during the logical day
    mean_mg: float      # time-averaged active level over the day window
    auc_mg_h: float     # exposure: area under the active-level curve (mg*h)
    dosed_mg: float     # total amount dosed within the day (reference)


def _active_mg(doses: list[tuple[datetime, float]], t: datetime, k: float) -> float:
    """Summed one-compartment decay at time t. doses must precede or equal t."""
    total = 0.0
    for dose_time, mg in doses:
        hours = (t - dose_time).total_seconds() / 3600.0
        if hours >= 0:
            total += mg * math.exp(-k * hours)
    return total


def daily_active_levels(
    start: date,
    end: date,
    *,
    half_lives: Optional[dict[str, tuple[float, str]]] = None,
    step_minutes: int = 30,
    lookback_days: int = 3,
) -> list[SubstanceDay]:
    """Per logical day and substance, modeled peak / mean / AUC active level.

    Doses from ``lookback_days`` before ``start`` are included so a prior dose's
    decaying tail still counts on the day it carries into. Substances absent from
    the half-life table are skipped (with no silent zero). Missing != zero: a day
    with no modeled active level for a substance simply does not appear.
    """
    from collections import defaultdict

    from ..core.primitives import date_to_dt_range, logical_date
    from ..sources.substance import entries_in_range

    table = {k.lower(): v for k, v in (half_lives or HALF_LIVES_HOURS).items()}

    # Collect dose events (with datetimes) per substance, over the padded range.
    doses: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for entry in entries_in_range(start=start - timedelta(days=lookback_days), end=end):
        if entry.amount_mg is None:
            continue
        key = entry.substance.strip().lower()
        if key not in table:
            continue
        clock = entry.time or dtime(12, 0)  # midday default when time unlogged
        # Naive local wall-clock, consistent with date_to_dt_range's sampling grid.
        doses[key].append((datetime.combine(entry.date, clock), float(entry.amount_mg)))

    step = timedelta(minutes=step_minutes)
    step_h = step_minutes / 60.0
    result: list[SubstanceDay] = []

    for substance, events in doses.items():
        if not events:
            continue
        k = math.log(2.0) / table[substance][0]
        events.sort()
        # Walk each logical day in [start, end].
        day = start
        while day <= end:
            win_start, win_end = date_to_dt_range(day, day)
            samples: list[float] = []
            t = win_start
            while t < win_end:
                samples.append(_active_mg(events, t, k))
                t += step
            peak = max(samples) if samples else 0.0
            auc = sum(samples) * step_h
            window_h = (win_end - win_start).total_seconds() / 3600.0
            mean = auc / window_h if window_h else 0.0
            dosed = sum(mg for dt, mg in events if logical_date(dt) == day)
            if peak > 1e-9:  # only emit days with real modeled exposure (missing != zero)
                result.append(SubstanceDay(day, substance, peak, mean, auc, dosed))
            day += timedelta(days=1)

    result.sort(key=lambda r: (r.date, r.substance))
    return result


def active_level_health_correlation(
    start: date,
    end: date,
    *,
    half_lives: Optional[dict[str, tuple[float, str]]] = None,
    min_days: int = 10,
) -> dict[str, object]:
    """Correlate each substance's daily exposure (AUC active level) against
    next-day HRV / stress / sleep, over days both are present (missing != zero).

    Lag-1 (today's exposure -> tomorrow's physiology) via Pearson r, FDR-corrected
    across the (substance x signal) family. Association, not causation; the
    half-life estimates for research chemicals (see substance_half_lives.json)
    make this a coarse exposure proxy.
    """
    import math as _math

    from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
    from .operator_daily import operator_daily_matrix

    auc: dict[tuple[str, date], float] = {
        (d.substance, d.date): d.auc_mg_h
        for d in daily_active_levels(start, end, half_lives=half_lives)
    }
    substances = sorted({s for (s, _) in auc})

    rows = operator_daily_matrix(start, end)
    signals = {
        "hrv_rmssd": {r.date: r.hrv_rmssd for r in rows if r.hrv_rmssd is not None and "health" in r.sources_present},
        "stress_mean": {r.date: r.stress_mean for r in rows if r.stress_mean is not None and "health" in r.sources_present},
        "sleep_hours": {r.date: r.sleep_hours for r in rows if r.sleep_hours is not None and "sleep" in r.sources_present},
    }

    findings: list[dict[str, object]] = []
    pvals: dict[int, float] = {}
    for substance in substances:
        for signal, by_date in signals.items():
            # today's exposure (date d) vs next-day signal (d+1)
            pairs = [
                (auc[(substance, d)], by_date[d + timedelta(days=1)])
                for d in [k[1] for k in auc if k[0] == substance]
                if (d + timedelta(days=1)) in by_date
            ]
            if len(pairs) < min_days:
                continue
            xs = [p[0] for p in pairs]
            ys = [float(p[1]) for p in pairs]
            r = _pearson_r(xs, ys)
            if r is None or not _math.isfinite(r):
                continue
            n = len(pairs)
            p = 0.0 if abs(r) >= 0.99999 else _t_test_p(r * _math.sqrt((n - 2) / (1.0 - r * r)), n - 2)
            pvals[len(findings)] = p
            findings.append({"substance": substance, "signal": signal, "r": round(r, 4), "n": n})

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    for idx, finding in enumerate(findings):
        finding["p_value"] = round(pvals[idx], 4)
        finding["q_value"] = round(qmap[idx], 4)
        finding["significant"] = qmap[idx] < 0.05
    findings.sort(key=lambda f: abs(float(f["r"])), reverse=True)  # type: ignore[arg-type]

    return {
        "findings": findings,
        "caveats": [
            "Lag-1 association (today's exposure vs next-day signal), NOT causation.",
            "Exposure = AUC of a one-compartment decay model; half-lives are estimates "
            "(research chemicals especially uncertain) — relative proxy, not clinical.",
            "Only days with both exposure and the health signal present (missing != zero).",
            "p-values FDR-corrected across the substance x signal family.",
        ],
    }
