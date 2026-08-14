"""Correlate phone psychometric instrument runs against operator-day signals.

Cross-source: daily phone-instrument metrics (``sources.phone_events.
daily_instrument_metrics``) vs. health/productivity signals from
``operator_daily_matrix``, plus a dedicated sleep-regularity pairing against
the PVT reaction-time instrument specifically.

STATISTICAL INTEGRITY CONTRACT
-------------------------------
This mirrors ``mood_correlations.py`` (itself following ``substance_health.py``
/ ``lifestyle_correlations.py``):

* **Multiple comparisons + autocorrelation** — every reported association
  carries an autocorrelation-corrected two-tailed Pearson ``p_value``
  (Quenouille/Bartlett effective n; the uncorrected p stays in ``p_naive``)
  and a Benjamini-Hochberg FDR-corrected ``q_value`` computed across the
  *entire* test family (every instrument × signal pairing AND the
  sleep × PVT pairing) in one ``_benjamini_hochberg`` call. ``significant``
  is derived from ``q_value < 0.05``.

* **Missing != zero** — a (date, instrument) with no run that day is absent
  from the instrument's daily series (``daily_instrument_metrics`` never
  synthesizes a zero-run row); a day where a health/sleep signal was not
  observed is excluded from that pairing, not coerced to 0. ``n`` on each
  result is the count of genuine paired observations after both absence
  filters.

* **Association, not causation** — reaction-time and staircase/counting
  performance are influenced by dozens of unmeasured factors (caffeine,
  time pressure, motivation, practice effects on repeated instruments).
  These are same-day (lag 0) associations only; no causal direction is
  implied by a reported r.

Sleep experiment note: the steering store deliberately holds a PLACEHOLDER
pre-registered prediction for the operator's active sleep-window experiment.
This module does not write, infer, or fill in that prediction — the
sleep x PVT pairing below is exploratory correlation, not a test of that
experiment's (still unwritten) hypothesis.

Method: same-day (lag 0) Pearson correlation, autocorrelation-corrected,
Benjamini-Hochberg FDR correction across the full family.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

from ..core.analytics import _benjamini_hochberg, autocorr_corrected_pearson
from ..core.parse import safe_float

logger = logging.getLogger(__name__)

#: Minimum paired observations before a pairing is computed.
MIN_PAIRS = 10

#: FDR target for the Benjamini-Hochberg correction.
FDR_TARGET = 0.05

#: Trailing window (nights) for the wake-time regularity signal.
WAKE_REGULARITY_WINDOW_DAYS = 7
WAKE_REGULARITY_MIN_NIGHTS = 3

# Health/productivity outcomes tested against each instrument's primary
# metric. (name, accessor, required OperatorDay.sources_present label.)
_HEALTH_SIGNALS: list[tuple[str, Callable[[Any], Any], str]] = [
    ("hrv_rmssd", lambda r: r.hrv_rmssd, "health"),
    ("stress_mean", lambda r: r.stress_mean, "health"),
    ("sleep_hours", lambda r: r.sleep_hours, "sleep"),
    ("sleep_score", lambda r: r.sleep_score, "sleep"),
    ("aw_deep_work_min", lambda r: r.aw_deep_work_min, "activitywatch"),
]


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PsychometricCorrelation:
    """One (predictor -> outcome) same-day association.

    ``predictor`` is either ``"<instrument>.primary_metric"`` (see
    ``phone_events.daily_instrument_metrics`` for what "primary metric"
    means per engine) or a sleep signal name (``sleep_hours``,
    ``wake_time_regularity_sd_min``). ``outcome`` is a health/productivity
    signal name or a PVT metric (``pvt.median_rt_ms``, ``pvt.lapses``).
    """

    predictor: str
    outcome: str
    lag_days: int   # always 0 in this analysis; kept for shape parity with mood_correlations
    r: float
    n: int
    label: str
    p_value: float = 1.0
    q_value: float = 1.0
    significant: bool = False
    n_eff: float = 0.0
    p_naive: float = 1.0


@dataclass
class PsychometricReport:
    window_start: date
    window_end: date
    instrument_days_in_window: dict[str, int] = field(default_factory=dict)
    n_tests: int = 0
    correlations: list[PsychometricCorrelation] = field(default_factory=list)
    summary: str = ""
    caveats: list[str] = field(default_factory=list)


@dataclass
class _Raw:
    predictor: str
    outcome: str
    lag: int
    r: float
    n: int
    p: float
    n_eff: float
    p_naive: float


# ── Sleep-regularity helpers ──────────────────────────────────────────────────


def _wake_time_hour_series(start: date, end: date) -> dict[date, float]:
    """Per-night wake time (decimal local hour, may exceed 24) by sleep date."""
    from ..sources.sleep import entries_in_range

    series: dict[date, float] = {}
    for entry in entries_in_range(start=start, end=end, canonical=True):
        if entry.is_nap or not entry.segments:
            continue
        wake = max(seg.end for seg in entry.segments)
        series[entry.date] = wake.hour + wake.minute / 60.0
    return series


def _circular_sd_minutes(hours: list[float]) -> Optional[float]:
    """Circular SD (minutes) of a list of decimal clock hours."""
    if not hours:
        return None
    angles = [h / 24.0 * 2 * math.pi for h in hours]
    sin_sum = sum(math.sin(a) for a in angles) / len(angles)
    cos_sum = sum(math.cos(a) for a in angles) / len(angles)
    r = math.hypot(sin_sum, cos_sum)
    if r <= 0:
        return None
    if r >= 1:
        return 0.0
    sd_hours = math.sqrt(-2 * math.log(r)) / (2 * math.pi) * 24.0
    return round(sd_hours * 60.0, 1)


def _wake_regularity_series(
    start: date, end: date,
    *, window: int = WAKE_REGULARITY_WINDOW_DAYS, min_nights: int = WAKE_REGULARITY_MIN_NIGHTS,
) -> dict[date, float]:
    """Trailing wake-time variability (SD, minutes) going into each date.

    For date D, uses wake times from up to ``window`` nights STRICTLY BEFORE
    D — never D's own night — so the signal reflects regularity already
    established before that day's PVT run rather than being contaminated by
    it. A date with fewer than ``min_nights`` prior tracked nights within the
    lookback is absent from the result (missing != zero variability).
    """
    lookback_start = start - timedelta(days=window + 3)
    wake_hours = _wake_time_hour_series(lookback_start, end)
    dates_sorted = sorted(wake_hours)

    out: dict[date, float] = {}
    total_days = (end - start).days + 1
    for i in range(total_days):
        d = start + timedelta(days=i)
        prior = [wake_hours[wd] for wd in dates_sorted if wd < d][-window:]
        if len(prior) < min_nights:
            continue
        sd_minutes = _circular_sd_minutes(prior)
        if sd_minutes is not None:
            out[d] = sd_minutes
    return out


def _daily_metric_series(
    instrument: str, metric_key: str, *, start: date, end: date,
) -> dict[date, float]:
    """Mean daily value of one raw ``InstrumentRun.metrics`` field.

    Unlike ``daily_instrument_metrics``'s "primary metric", this reaches into
    the raw per-run metrics dict for a specific field (e.g. PVT's
    ``lapses``) that isn't necessarily the engine's primary metric.
    """
    from ..sources.phone_events import instrument_runs

    buckets: dict[date, list[float]] = defaultdict(list)
    for run in instrument_runs(start=start, end=end):
        if run.instrument != instrument:
            continue
        value = safe_float(run.metrics.get(metric_key))
        if value is not None:
            buckets[run.date].append(value)
    return {d: sum(vals) / len(vals) for d, vals in buckets.items()}


# ── Core analysis ─────────────────────────────────────────────────────────────


def psychometric_correlation(
    start: date, end: date, *, min_pairs: int = MIN_PAIRS,
) -> PsychometricReport:
    """Correlate daily phone-instrument metrics against operator-day signals.

    Two test families, combined into one FDR correction:

    1. Every instrument with a computable daily primary metric (see
       ``phone_events.daily_instrument_metrics``) vs. the health/productivity
       signals in ``_HEALTH_SIGNALS``, same day.
    2. Sleep duration and trailing wake-time regularity vs. the PVT
       instrument's ``median_rt_ms`` and ``lapses``, same day — the pairing
       the operator's sleep-window experiment needs, though this function
       does not itself carry any pre-registered prediction for that
       experiment.

    Args:
        start, end: Inclusive analysis window.
        min_pairs: Minimum paired observations required per pairing.

    Returns:
        ``PsychometricReport`` with FDR-corrected correlations + caveats.
    """
    from ..sources.phone_events import daily_instrument_metrics
    from .operator_daily import operator_daily_matrix

    instrument_days = daily_instrument_metrics(start=start, end=end)
    by_instrument: dict[str, dict[date, float]] = defaultdict(dict)
    counts_by_instrument: dict[str, int] = defaultdict(int)
    for row in instrument_days:
        counts_by_instrument[row.instrument] += 1
        if row.primary_metric_value is not None:
            by_instrument[row.instrument][row.date] = row.primary_metric_value

    op_rows = operator_daily_matrix(start, end)
    rows_by_date = {r.date: r for r in op_rows}

    report = PsychometricReport(
        window_start=start, window_end=end,
        instrument_days_in_window=dict(counts_by_instrument),
    )

    raw_all: list[_Raw] = []

    # Family 1: instrument primary metric vs. health/productivity signals.
    for instrument, series in sorted(by_instrument.items()):
        for signal_name, accessor_fn, presence_key in _HEALTH_SIGNALS:
            xs: list[float] = []
            ys: list[float] = []
            for d, x_val in sorted(series.items()):
                op_row = rows_by_date.get(d)
                if op_row is None or presence_key not in op_row.sources_present:
                    continue
                y_val = accessor_fn(op_row)
                if y_val is None:
                    continue
                xs.append(x_val)
                ys.append(float(y_val))
            if len(xs) < min_pairs:
                continue
            stat = autocorr_corrected_pearson(xs, ys)
            if stat is None or not math.isfinite(stat.r):
                continue
            raw_all.append(_Raw(
                predictor=f"{instrument}.primary_metric", outcome=signal_name, lag=0,
                r=stat.r, n=stat.n, p=stat.p_value, n_eff=stat.n_eff, p_naive=stat.p_naive,
            ))

    # Family 2: sleep signals vs. PVT reaction-time performance.
    pvt_median_rt = by_instrument.get("pvt", {})
    pvt_lapses = _daily_metric_series("pvt", "lapses", start=start, end=end)
    wake_regularity = _wake_regularity_series(start, end)
    sleep_hours = {
        r.date: float(r.sleep_hours)
        for r in rows_by_date.values()
        if "sleep" in r.sources_present and r.sleep_hours is not None
    }

    sleep_predictors: list[tuple[str, dict[date, float]]] = [
        ("sleep_hours", sleep_hours),
        ("wake_time_regularity_sd_min", wake_regularity),
    ]
    pvt_outcomes: list[tuple[str, dict[date, float]]] = [
        ("pvt.median_rt_ms", pvt_median_rt),
        ("pvt.lapses", pvt_lapses),
    ]
    for predictor_name, predictor_series in sleep_predictors:
        for outcome_name, outcome_series in pvt_outcomes:
            shared_dates = sorted(set(predictor_series) & set(outcome_series))
            xs = [predictor_series[d] for d in shared_dates]
            ys = [outcome_series[d] for d in shared_dates]
            if len(xs) < min_pairs:
                continue
            stat = autocorr_corrected_pearson(xs, ys)
            if stat is None or not math.isfinite(stat.r):
                continue
            raw_all.append(_Raw(
                predictor=predictor_name, outcome=outcome_name, lag=0,
                r=stat.r, n=stat.n, p=stat.p_value, n_eff=stat.n_eff, p_naive=stat.p_naive,
            ))

    # FDR correction across the entire family.
    report.n_tests = len(raw_all)
    if raw_all:
        pvals_map: dict[int, float] = {i: raw.p for i, raw in enumerate(raw_all)}
        q_map = _benjamini_hochberg(pvals_map)
        for i, raw in enumerate(raw_all):
            q = q_map[i]
            report.correlations.append(
                PsychometricCorrelation(
                    predictor=raw.predictor,
                    outcome=raw.outcome,
                    lag_days=raw.lag,
                    r=round(raw.r, 4),
                    n=raw.n,
                    label=f"{raw.predictor} → {raw.outcome} (lag={raw.lag}d)",
                    p_value=round(raw.p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                    n_eff=round(raw.n_eff, 1),
                    p_naive=round(raw.p_naive, 4),
                )
            )

    report.caveats = _build_caveats(start, end, counts_by_instrument)
    report.summary = _build_summary(report)
    return report


# ── Summary / caveats ─────────────────────────────────────────────────────────


def _build_caveats(
    start: date, end: date, counts_by_instrument: dict[str, int],
) -> list[str]:
    instruments_desc = (
        ", ".join(f"{name} ({n}d)" for name, n in sorted(counts_by_instrument.items()))
        if counts_by_instrument else "none"
    )
    return [
        "Same-day (lag 0) associations only; performance-test scores are "
        "influenced by unmeasured factors (caffeine, time pressure, "
        "motivation, practice effects on repeated instruments) that can "
        "produce a correlation with no causal link to the paired signal.",
        "A day with no run of a given instrument contributes NO row for that "
        "instrument (missing != zero); a day where a health/sleep signal was "
        "not observed is excluded from that pairing, not coerced to 0.",
        "The 'primary metric' per instrument is a same-module heuristic "
        "(median_rt_ms for reaction runs, threshold for staircases, accuracy "
        "for forced-choice runs, a derived cycles_correct/unaware_miscounts "
        "ratio for counting runs) reconstructed from the persisted event "
        "fields — the app itself does not persist a canonical headline "
        "metric per run.",
        "wake_time_regularity_sd_min is a TRAILING signal: it summarizes the "
        f"{WAKE_REGULARITY_WINDOW_DAYS} tracked nights strictly BEFORE the "
        "paired date, never that date's own night, and requires at least "
        f"{WAKE_REGULARITY_MIN_NIGHTS} tracked nights in that lookback.",
        "No pre-registered prediction for the operator's sleep-window "
        "experiment is written or implied here; the steering store holds "
        "that as the operator's own placeholder to fill in. This pairing is "
        "exploratory correlation, not a test of that experiment.",
        f"Window: {start} → {end}. Instrument-days observed: {instruments_desc}.",
        "p-values are autocorrelation-corrected (Quenouille/Bartlett "
        "effective n; day series are not independent observations) and then "
        "FDR-corrected (Benjamini-Hochberg) across every pairing in this call.",
    ]


def _build_summary(report: PsychometricReport) -> str:
    lines = [
        f"Psychometric Correlation Report: {report.window_start} → {report.window_end}",
        f"  Instrument-days in window: {report.instrument_days_in_window}",
        f"  Total tests (instrument/sleep x outcome): {report.n_tests}",
        "",
    ]

    significant = [c for c in report.correlations if c.significant]
    significant.sort(key=lambda c: -abs(c.r))

    if significant:
        lines.append(
            f"FDR-significant associations "
            f"(Benjamini-Hochberg q<{FDR_TARGET:g} across {report.n_tests} tests):"
        )
        for c in significant:
            direction = "↑" if c.r > 0 else "↓"
            lines.append(
                f"  r={c.r:+.3f} {direction}  {c.label} "
                f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
            )
    else:
        lines.append(
            f"No associations survive FDR correction (q<{FDR_TARGET:g}) "
            f"across {report.n_tests} tests."
        )

    exploratory = [
        c for c in report.correlations if not c.significant and abs(c.r) > 0.2
    ]
    exploratory.sort(key=lambda c: -abs(c.r))
    if exploratory:
        lines.append("")
        lines.append(
            "Exploratory only (|r|>0.2 but NOT FDR-significant — "
            "likely noise, do not report as findings):"
        )
        for c in exploratory[:8]:
            direction = "↑" if c.r > 0 else "↓"
            lines.append(
                f"  r={c.r:+.3f} {direction}  {c.label} "
                f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
            )

    lines.append("")
    lines.append("CAVEATS (inline, cannot be stripped):")
    for caveat in report.caveats:
        lines.append(f"  • {caveat}")

    return "\n".join(lines)


__all__ = [
    "FDR_TARGET",
    "MIN_PAIRS",
    "WAKE_REGULARITY_MIN_NIGHTS",
    "WAKE_REGULARITY_WINDOW_DAYS",
    "PsychometricCorrelation",
    "PsychometricReport",
    "psychometric_correlation",
]
