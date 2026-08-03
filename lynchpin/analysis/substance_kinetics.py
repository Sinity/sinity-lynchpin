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


def substance_sleep_night_correlation(
    start: date,
    end: date,
    *,
    half_lives: Optional[dict[str, tuple[float, str]]] = None,
    min_nights: int = 10,
) -> dict[str, object]:
    """Modeled active level at sleep onset vs that same night's sleep.

    For each canonical non-nap night, computes the one-compartment active
    level (mg) of every modeled substance at the moment of sleep onset, then
    correlates it against same-night outcomes: effective quality score
    (real or proxy), deep/REM share, sleep duration, nocturnal mean HR, and
    onset clock hour. Complements ``active_level_health_correlation``, which
    only looks at day-level lag-1 effects.

    Association, not causation; nights and doses share obvious confounders
    (a late dose and a late night have a common cause more often than not).
    """
    import math as _math

    from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
    from ..sources.sleep import entries_in_range as sleep_entries_in_range
    from ..sources.substance import entries_in_range as dose_entries_in_range

    table = {k.lower(): v for k, v in (half_lives or HALF_LIVES_HOURS).items()}

    doses: dict[str, list[tuple[datetime, float]]] = {}
    skipped_untimed = 0
    for entry in dose_entries_in_range(start=start - timedelta(days=3), end=end):
        if entry.amount_mg is None:
            continue
        key = entry.substance.strip().lower()
        if key not in table:
            continue
        if entry.time is None:
            skipped_untimed += 1
            continue
        doses.setdefault(key, []).append(
            (datetime.combine(entry.date, entry.time), float(entry.amount_mg))
        )
    for events in doses.values():
        events.sort()

    nights: list[dict[str, object]] = []
    for night in sleep_entries_in_range(start=start, end=end, canonical=True):
        if night.is_nap or not night.segments:
            continue
        onset = night.segments[0].start
        if onset == datetime.min:
            continue
        onset_naive = onset.replace(tzinfo=None)
        metrics = night.metrics
        row: dict[str, object] = {
            "onset": onset_naive,
            "score": night.effective_score,
            "deep_pct": metrics.deep_pct if metrics else None,
            "rem_pct": metrics.rem_pct if metrics else None,
            "duration_min": night.total_minutes or None,
            "hr_avg": night.signals.hr_avg if night.signals else None,
            "onset_hour": onset_naive.hour + onset_naive.minute / 60.0,
        }
        nights.append(row)

    outcome_names = ("score", "deep_pct", "rem_pct", "duration_min", "hr_avg", "onset_hour")
    findings: list[dict[str, object]] = []
    pvals: dict[int, float] = {}
    group_pvals: dict[int, float] = {}
    for substance, events in doses.items():
        k = _math.log(2.0) / table[substance][0]
        active = [
            (_active_mg(events, row["onset"], k), row)  # type: ignore[arg-type]
            for row in nights
        ]
        for outcome in outcome_names:
            pairs = [
                (mg, float(row[outcome]))  # type: ignore[arg-type]
                for mg, row in active
                if row[outcome] is not None
            ]
            if len(pairs) < min_nights:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            r = _pearson_r(xs, ys)
            if r is None or not _math.isfinite(r):
                continue
            n = len(pairs)
            p = 0.0 if abs(r) >= 0.99999 else _t_test_p(
                r * _math.sqrt((n - 2) / (1.0 - r * r)), n - 2
            )
            active_vals = [y for x, y in pairs if x > 1.0]
            sober_vals = [y for x, y in pairs if x <= 1.0]
            # A monotone dose-response r can be near zero while the
            # active-vs-sober contrast is real (threshold effects), so report
            # Welch's t and Hedges' g for the split as well as the correlation.
            group_p, hedges_g = _welch_group_test(active_vals, sober_vals)
            pvals[len(findings)] = p
            findings.append(
                {
                    "substance": substance,
                    "outcome": outcome,
                    "r": round(r, 4),
                    "n": n,
                    "active_nights": len(active_vals),
                    "active_mean": round(sum(active_vals) / len(active_vals), 2)
                    if active_vals
                    else None,
                    "sober_mean": round(sum(sober_vals) / len(sober_vals), 2)
                    if sober_vals
                    else None,
                    "group_p_value": round(group_p, 4) if group_p is not None else None,
                    "hedges_g": round(hedges_g, 3) if hedges_g is not None else None,
                }
            )
            if group_p is not None:
                group_pvals[len(findings) - 1] = group_p

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    gqmap = _benjamini_hochberg(group_pvals) if group_pvals else {}
    for idx, finding in enumerate(findings):
        finding["p_value"] = round(pvals[idx], 4)
        finding["q_value"] = round(qmap[idx], 4)
        finding["significant"] = qmap[idx] < 0.05
        if idx in gqmap:
            finding["group_q_value"] = round(gqmap[idx], 4)
            finding["group_significant"] = gqmap[idx] < 0.05
    findings.sort(key=lambda f: abs(float(f["r"])), reverse=True)  # type: ignore[arg-type]

    return {
        "findings": findings,
        "nights": len(nights),
        "skipped_untimed_doses": skipped_untimed,
        "caveats": [
            "Same-night association at sleep onset, NOT causation; dose timing and "
            "bedtime share confounders.",
            "Active level = one-compartment decay with estimated half-lives "
            "(research chemicals especially uncertain).",
            "active/sober means split at 1 mg modeled active level; group_p/hedges_g "
            "test that contrast (Welch) because a threshold effect can show a real "
            "group difference with a near-zero dose-response correlation.",
            "p-values FDR-corrected across the substance x outcome family.",
        ],
    }


def _welch_group_test(
    a: list[float], b: list[float], *, min_group: int = 5
) -> tuple[Optional[float], Optional[float]]:
    """Welch's unequal-variance t-test p-value and Hedges' g for two samples."""
    import math as _math

    from ..core.analytics import _t_test_p

    if len(a) < min_group or len(b) < min_group:
        return None, None
    na, nb = len(a), len(b)
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    if va <= 0 and vb <= 0:
        return None, None
    se2 = va / na + vb / nb
    if se2 <= 0:
        return None, None
    t = (ma - mb) / _math.sqrt(se2)
    df_num = se2 ** 2
    df_den = (va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1)
    df = df_num / df_den if df_den > 0 else float(na + nb - 2)
    p = _t_test_p(t, max(int(df), 1))
    pooled_sd = _math.sqrt(
        ((na - 1) * va + (nb - 1) * vb) / max(na + nb - 2, 1)
    )
    if pooled_sd <= 0:
        return p, None
    d = (ma - mb) / pooled_sd
    # Hedges' small-sample correction
    g = d * (1 - 3 / (4 * (na + nb) - 9)) if (na + nb) > 3 else d
    return p, g
