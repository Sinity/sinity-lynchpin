"""Personal baselines for stress/HR, and vitality as an external check.

Two products (Ref lynchpin-04j):

``stress_baselines`` — Samsung's stress score is only interpretable against the
operator's own history, so this computes a trailing-median baseline and a
robust deviation (median absolute deviation) per day, flagging days that sit
far from personal normal. Absolute stress values are not comparable between
people and barely comparable across firmware versions; deviations are.

``vitality_validation`` — Samsung's daily "vitality" record carries its own
``sleep_score``, ``shr_score`` (sleep heart rate) and ``shrv_score`` (sleep
HRV) sub-scores, computed by Samsung from the same night but through a
*different* pipeline than the ``sleep_combined`` score. That makes it the one
external yardstick available for the fitted proxy score: nights where Samsung
never produced a ``sleep_combined`` score but did produce a vitality
``sleep_score`` are exactly the population the proxy exists to serve.

Circularity warning, stated once and loudly: Samsung's stress score is derived
from heart rate and HRV, so correlating it against nocturnal HR features is
partly self-referential. Vitality's sub-scores are likewise Samsung-computed.
Neither is an independent ground truth; both are *different summaries of
overlapping sensor data*, which makes them useful cross-checks and invalid as
proof.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

__all__ = [
    "StressDay",
    "VitalityCheck",
    "stress_baselines",
    "vitality_validation",
]

_MAD_TO_SD = 1.4826  # makes MAD comparable to a standard deviation


@dataclass(frozen=True)
class StressDay:
    date: date
    mean_score: float
    max_score: float
    sample_count: int
    baseline: Optional[float]       # trailing median
    deviation: Optional[float]      # robust z against trailing MAD
    flagged: bool                   # |deviation| >= 2


@dataclass(frozen=True)
class VitalityCheck:
    metric: str
    vitality_field: str
    r: float
    n: int
    p_value: float
    scored_subset: str  # 'all' | 'proxy_only' | 'samsung_scored'


def _logical_day(moment: datetime) -> date:
    day = moment.date()
    if moment.hour < 6:
        day -= timedelta(days=1)
    return day


def stress_baselines(
    *, start: date, end: date, window_days: int = 28, min_history: int = 7
) -> list[StressDay]:
    """Daily stress aggregates with a trailing personal baseline."""
    from ..core.parse import parse_datetime as _parse_dt, safe_float
    from ..sources.sleep import _load_jsonl

    by_day: dict[date, list[float]] = {}
    for row in _load_jsonl("health_stress.jsonl"):
        when = _parse_dt(row.get("start_time"))
        score = safe_float(row.get("score"))
        if when is None or score is None:
            continue
        day = _logical_day(when.replace(tzinfo=None))
        if day < start - timedelta(days=window_days) or day > end:
            continue
        by_day.setdefault(day, []).append(score)

    days = sorted(by_day)
    result: list[StressDay] = []
    for day in days:
        if day < start:
            continue
        values = by_day[day]
        history: list[float] = []
        for prior in days:
            if prior >= day:
                break
            if prior >= day - timedelta(days=window_days):
                history.extend(by_day[prior])
        baseline = deviation = None
        flagged = False
        if len(history) >= min_history:
            baseline = statistics.median(history)
            mad = statistics.median([abs(v - baseline) for v in history]) * _MAD_TO_SD
            mean_score = statistics.mean(values)
            if mad > 0:
                deviation = (mean_score - baseline) / mad
                flagged = abs(deviation) >= 2.0
        result.append(
            StressDay(
                date=day,
                mean_score=round(statistics.mean(values), 2),
                max_score=round(max(values), 2),
                sample_count=len(values),
                baseline=round(baseline, 2) if baseline is not None else None,
                deviation=round(deviation, 2) if deviation is not None else None,
                flagged=flagged,
            )
        )
    return result


def vitality_validation(*, start: date, end: date, min_days: int = 15) -> dict[str, object]:
    """Check sleep metrics — especially the proxy score — against Samsung vitality."""
    from ..core.analytics import _pearson_r, _t_test_p
    from ..core.parse import safe_float
    from ..sources.sleep import _load_jsonl
    from ..sources.sleep_composite import night_composites

    vitality: dict[date, dict[str, float]] = {}
    for row in _load_jsonl("health_vitality.jsonl"):
        raw = row.get("date")
        if not isinstance(raw, str):
            continue
        try:
            day = date.fromisoformat(raw[:10])
        except ValueError:
            continue
        if day < start or day > end:
            continue
        fields = {}
        for key in ("total_score", "sleep_score", "shr_score", "shrv_score"):
            value = safe_float(row.get(key))
            if value is not None:
                fields[key] = value
        if fields:
            vitality[day] = fields

    nights = {c.date: c for c in night_composites(start=start, end=end)}
    # Samsung's vitality is stamped on the calendar day the night ENDED, while
    # composites use the 06:00 logical night; align by trying same-day first
    # and then the following day.
    aligned: list[tuple[object, dict[str, float]]] = []
    for day, composite in nights.items():
        row = vitality.get(day) or vitality.get(day + timedelta(days=1))
        if row:
            aligned.append((composite, row))

    metrics = {
        "effective_score": lambda c: c.score,
        "main_asleep_minutes": lambda c: c.main_asleep_minutes,
        "deep_pct": lambda c: c.deep_pct,
        "hr_avg": lambda c: c.hr_avg,
    }
    subsets = {
        "all": lambda c: True,
        "proxy_only": lambda c: c.score_estimated,
        "samsung_scored": lambda c: c.score is not None and not c.score_estimated,
    }

    checks: list[VitalityCheck] = []
    for subset_name, keep in subsets.items():
        for metric_name, getter in metrics.items():
            for field in ("sleep_score", "total_score", "shr_score", "shrv_score"):
                pairs = [
                    (float(getter(c)), row[field])  # type: ignore[arg-type]
                    for c, row in aligned
                    if keep(c) and getter(c) is not None and field in row
                ]
                if len(pairs) < min_days:
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
                checks.append(
                    VitalityCheck(
                        metric=metric_name, vitality_field=field, r=round(r, 4),
                        n=n, p_value=round(p, 4), scored_subset=subset_name,
                    )
                )
    checks.sort(key=lambda c: abs(c.r), reverse=True)

    return {
        "checks": checks,
        "aligned_nights": len(aligned),
        "vitality_days": len(vitality),
        "caveats": [
            "Vitality sub-scores are computed BY SAMSUNG from overlapping sensor "
            "data, so this is a cross-check between two Samsung pipelines, not "
            "validation against independent ground truth.",
            "Vitality coverage is short (2025-05 onward, ~115 days), so subset "
            "correlations run on small n; read them as directional.",
            "Alignment tries the same logical day then the following calendar day, "
            "because vitality is stamped when the night ended.",
        ],
    }
