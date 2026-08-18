"""Daily exposures (keystroke volume, weather) vs that night's sleep.

Deliberately exploratory territory: neither keystroke dynamics nor weather is
an established predictor of anything in this dataset, and both are wide-open
scans rather than pre-registered hypotheses. That is exactly why every result
here carries the multiple-comparison correction AND the minimum effect the
sample could have detected — a null at n=80 means "underpowered", not "absent",
and the two must never be confused.

Exposures:

- ``keylog.*`` — daily keystroke/session volume from the Wayland keystroke
  mirror (capture-bounded, 2025-10 onward). A behavioural load proxy: this is
  volume, not typing *dynamics* (inter-key latency), which would need the raw
  event stream and is not attempted here.
- ``weather.*`` — daily temperature/precipitation/pressure/sunshine for the
  operator's locale.

Outcomes are that night's effective score, duration, deep share, and nocturnal
mean heart rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

__all__ = [
    "ExposureFinding",
    "min_detectable_r",
    "daily_exposure_correlation",
]

_OUTCOMES = ("score", "duration_min", "deep_pct", "hr_avg")


@dataclass(frozen=True)
class ExposureFinding:
    exposure: str
    outcome: str
    r: float
    n: int
    p_value: float
    q_value: float
    significant: bool
    min_detectable_r: float

    @property
    def underpowered(self) -> bool:
        """True when the sample could not have detected an effect this small."""
        return abs(self.r) < self.min_detectable_r


def min_detectable_r(n: int, *, alpha_z: float = 1.96, power_z: float = 0.84) -> float:
    """Smallest |r| detectable at alpha=.05 two-sided with 80% power, via Fisher z."""
    if n < 4:
        return float("nan")
    z = (alpha_z + power_z) / math.sqrt(n - 3)
    return (math.exp(2 * z) - 1) / (math.exp(2 * z) + 1)


def _night_outcomes(start: date, end: date) -> dict[date, dict[str, Optional[float]]]:
    from ..sources.sleep import entries_in_range
    from ..sources.sleep_composite import composite_minutes_by_date

    composite_minutes = composite_minutes_by_date(start=start, end=end)
    out: dict[date, dict[str, Optional[float]]] = {}
    for entry in entries_in_range(start=start, end=end, canonical=True):
        if entry.is_nap:
            continue
        metrics = entry.metrics
        out[entry.date] = {
            "score": entry.effective_score,
            "duration_min": composite_minutes.get(entry.date) or None,
            "deep_pct": metrics.deep_pct if metrics else None,
            "hr_avg": entry.signals.hr_avg if entry.signals else None,
        }
    return out


def _numeric_fields(obj: Any) -> list[str]:
    return [
        name
        for name, value in vars(obj).items()
        if name != "date" and isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _keylog_exposures(start: date, end: date) -> dict[str, dict[date, float]]:
    try:
        from ..sources.keylog import daily_activity
    except ImportError:  # pragma: no cover - source always present in-tree
        return {}
    rows = list(daily_activity(start=start, end=end))
    if not rows:
        return {}
    result: dict[str, dict[date, float]] = {}
    for field in _numeric_fields(rows[0]):
        result[f"keylog.{field}"] = {
            row.date: float(getattr(row, field))
            for row in rows
            if isinstance(getattr(row, field, None), (int, float))
        }
    return result


def _weather_exposures(start: date, end: date) -> dict[str, dict[date, float]]:
    from ..sources.weather import daily_weather

    rows = list(daily_weather(start, end))
    if not rows:
        return {}
    result: dict[str, dict[date, float]] = {}
    for field in _numeric_fields(rows[0]):
        result[f"weather.{field}"] = {
            row.date: float(getattr(row, field))
            for row in rows
            if isinstance(getattr(row, field, None), (int, float))
        }
    return result


def daily_exposure_correlation(
    *,
    start: date,
    end: date,
    include_keylog: bool = True,
    include_weather: bool = True,
    min_days: int = 25,
) -> dict[str, object]:
    """Scan daily exposures against same-night sleep outcomes, FDR-corrected."""
    from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p

    nights = _night_outcomes(start, end)
    exposures: dict[str, dict[date, float]] = {}
    unavailable: list[str] = []
    if include_keylog:
        try:
            exposures.update(_keylog_exposures(start, end))
        except Exception as exc:  # noqa: BLE001 - source may be uncaptured
            unavailable.append(f"keylog: {type(exc).__name__}")
    if include_weather:
        try:
            exposures.update(_weather_exposures(start, end))
        except Exception as exc:  # noqa: BLE001 - weather needs network/cache
            unavailable.append(f"weather: {type(exc).__name__}")

    raw: list[dict[str, object]] = []
    pvals: dict[int, float] = {}
    for name, by_date in sorted(exposures.items()):
        for outcome in _OUTCOMES:
            pairs = [
                (value, float(nights[day][outcome]))  # type: ignore[arg-type]
                for day, value in by_date.items()
                if day in nights and nights[day][outcome] is not None
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
            pvals[len(raw)] = p
            raw.append({"exposure": name, "outcome": outcome, "r": r, "n": n, "p": p})

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    findings = [
        ExposureFinding(
            exposure=str(item["exposure"]),
            outcome=str(item["outcome"]),
            r=round(float(item["r"]), 4),
            n=int(item["n"]),
            p_value=round(float(item["p"]), 4),
            q_value=round(qmap.get(idx, 1.0), 4),
            significant=qmap.get(idx, 1.0) < 0.05,
            min_detectable_r=round(min_detectable_r(int(item["n"])), 3),
        )
        for idx, item in enumerate(raw)
    ]
    findings.sort(key=lambda f: abs(f.r), reverse=True)

    return {
        "findings": findings,
        "nights": len(nights),
        "tests": len(findings),
        "unavailable": unavailable,
        "caveats": [
            "EXPLORATORY SCAN, not a pre-registered hypothesis: every exposure x "
            "outcome pair is tested, so read q_value (FDR) and never the raw p.",
            "min_detectable_r is the smallest |r| this sample could find at 80% "
            "power; a null with |r| below it is underpowered, not absent.",
            "Association, not causation, and heavily confounded: a high-keystroke "
            "day and a short night are both downstream of a busy or late day.",
            "keylog exposures measure keystroke VOLUME, not typing dynamics "
            "(inter-key latency), and are bounded by keystroke-capture coverage.",
        ],
    }
