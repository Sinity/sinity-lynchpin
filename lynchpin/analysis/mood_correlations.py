"""Correlate the operator's daily text-derived mood signal against physiology.

Cross-source: ``text_sentiment.daily_mood`` (sentiment extracted from own
writing) vs health signals (HRV, stress, sleep) and productivity (deep-work)
from ``operator_daily_matrix``.

STATISTICAL INTEGRITY CONTRACT
-------------------------------
This mirrors the established pattern in ``substance_health.py`` and
``lifestyle_correlations.py`` (the gold-standard templates):

* **Multiple comparisons** — every reported association carries a raw two-tailed
  Pearson ``p_value`` and a Benjamini-Hochberg FDR-corrected ``q_value``
  computed across the *entire* test family (all signal × lag combinations in
  one ``mood_health_correlation`` call). The ``significant`` flag is derived
  from ``q_value < 0.05``.

* **Missing ≠ zero** — days with no operator text are absent from ``daily_mood``
  and thus absent from all correlations. Days where health signals are ``None``
  (sensor gaps, export cutoffs) are excluded per signal, not coerced to 0.
  The ``n`` field on each result records how many genuine paired observations
  remain after both absence filters are applied.

* **Association, not causation** — sentiment of one's own writing is a noisy
  proxy for ground-truth mood. Selection bias is structural (only days with
  writing contribute). Lag-1 causality claims are not warranted; the results
  frame every association with caveats inline.

Method: lagged Pearson correlation, Benjamini-Hochberg FDR correction.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p

logger = logging.getLogger(__name__)

#: Minimum paired observations before a lag is computed.
MIN_PAIRS = 10

#: FDR target for the Benjamini-Hochberg correction.
FDR_TARGET = 0.05

#: Lags computed: 0 = same day, +1 = sentiment today → signal tomorrow.
DEFAULT_LAGS: tuple[int, ...] = (0, 1)


# ── Result types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MoodLagCorrelation:
    """One (mood predictor → physiological/productivity outcome) at a lag.

    ``predictor`` is always "mean_sentiment" (the only numeric mood signal
    from ``MoodDay``). ``outcome`` is the health or productivity signal name.
    ``lag_days`` is the temporal offset: 0 = same day, 1 = next day.
    ``p_value`` is raw two-tailed; ``q_value`` is BH FDR-corrected across the
    full test family; ``significant`` := q_value < FDR_TARGET.
    """

    predictor: str      # "mean_sentiment"
    outcome: str        # e.g. "hrv_rmssd"
    lag_days: int       # 0 = same day; +1 = predictor D → outcome D+lag
    r: float            # Pearson r
    n: int              # paired observations
    label: str          # human-readable
    p_value: float = 1.0
    q_value: float = 1.0
    significant: bool = False


@dataclass
class MoodHealthReport:
    """Full mood × health / productivity correlation analysis.

    ``lag_correlations`` carries every (outcome × lag) correlation computed.
    ``n_tests`` is the total family size (for FDR transparency).
    ``mood_days_in_window`` is how many days had scored text in the window.
    ``caveats`` are always emitted inline so an LLM copying this report
    cannot drop them.
    """

    window_start: date
    window_end: date
    mood_days_in_window: int
    n_tests: int = 0
    lag_correlations: list[MoodLagCorrelation] = field(default_factory=list)
    summary: str = ""
    caveats: list[str] = field(default_factory=list)


# ── Core analysis ─────────────────────────────────────────────────────────────


def mood_health_correlation(
    start: date,
    end: date,
    *,
    lags: tuple[int, ...] = DEFAULT_LAGS,
    min_pairs: int = MIN_PAIRS,
    corpora: Optional[object] = None,   # passed through to daily_mood
) -> MoodHealthReport:
    """Correlate daily mean-sentiment against next-day physiology and focus.

    Signals examined (all from ``operator_daily_matrix``):
      * ``hrv_rmssd``    — same-day and next-day heart-rate variability (ms)
      * ``stress_mean``  — same-day and next-day Samsung stress score
      * ``sleep_hours``  — same-day and next-day sleep duration
      * ``sleep_score``  — same-day and next-day sleep quality score
      * ``aw_deep_work_min`` — same-day and next-day deep-work (minutes)

    The lag-1 association (today's sentiment → tomorrow's physiology) is the
    headline; lag-0 (same-day co-movement) is included as a control.

    Args:
        start, end: Inclusive analysis window.
        lags: Which temporal offsets to test. Default: (0, 1).
        min_pairs: Minimum paired observations required per lag correlation.
        corpora: Optional corpus list passed to ``daily_mood``. ``None`` uses
            the default corpora (reddit, wykop, messenger, sms).

    Returns:
        ``MoodHealthReport`` with FDR-corrected correlations + caveats.

    Raises:
        ``SourceUnavailableError``: propagated from ``daily_mood`` when
            transformers/torch are unavailable.
    """
    from .operator_daily import OperatorDay, operator_daily_matrix
    from .text_sentiment import MoodDay, daily_mood

    # Build kwargs for daily_mood; corpora=None means use default.
    mood_kwargs: dict[str, object] = {}
    if corpora is not None:
        mood_kwargs["corpora"] = corpora

    mood_days_raw = daily_mood(start, end, **mood_kwargs)  # type: ignore[arg-type]
    mood_by_date: dict[date, MoodDay] = {m.date: m for m in mood_days_raw}

    op_rows = operator_daily_matrix(start, end)
    rows_by_date: dict[date, OperatorDay] = {r.date: r for r in op_rows}

    report = MoodHealthReport(
        window_start=start,
        window_end=end,
        mood_days_in_window=len(mood_by_date),
    )

    # Signal accessors: (name, accessor_fn, required_source_label)
    # required_source_label must be in OperatorDay.sources_present for the day
    # to contribute (missing != zero for health signals).
    signal_defs: list[tuple[str, Callable[[Any], Any], str]] = [
        ("hrv_rmssd",       lambda r: r.hrv_rmssd,       "health"),
        ("stress_mean",     lambda r: r.stress_mean,     "health"),
        ("sleep_hours",     lambda r: r.sleep_hours,     "sleep"),
        ("sleep_score",     lambda r: r.sleep_score,     "sleep"),
        ("aw_deep_work_min", lambda r: r.aw_deep_work_min, "activitywatch"),
    ]

    # Raw correlations before FDR.
    @dataclass
    class _Raw:
        outcome: str
        lag: int
        r: float
        n: int
        p: float

    raw_all: list[_Raw] = []

    for signal_name, accessor_fn, presence_key in signal_defs:
        for lag in lags:
            xs: list[float] = []
            ys: list[float] = []

            for mood_date, mood_day in mood_by_date.items():
                outcome_date = mood_date + timedelta(days=lag)
                outcome_row = rows_by_date.get(outcome_date)
                if outcome_row is None:
                    continue
                # Gate on presence: health/sleep/aw must be observed that day.
                if presence_key not in outcome_row.sources_present:
                    continue
                y_val = accessor_fn(outcome_row)
                if y_val is None:
                    continue
                xs.append(mood_day.mean_sentiment)
                ys.append(float(y_val))

            if len(xs) < min_pairs:
                continue

            r = _pearson_r(xs, ys)
            if r is None or not math.isfinite(r):
                continue

            n = len(xs)
            if abs(r) >= 1.0:
                p = 0.0
            else:
                t_stat = r * math.sqrt((n - 2) / (1.0 - r * r))
                p = _t_test_p(t_stat, n - 2)

            raw_all.append(_Raw(outcome=signal_name, lag=lag, r=r, n=n, p=p))

    # FDR correction across the entire family.
    report.n_tests = len(raw_all)
    if raw_all:
        pvals_map: dict[int, float] = {i: raw.p for i, raw in enumerate(raw_all)}
        q_map = _benjamini_hochberg(pvals_map)
        for i, raw in enumerate(raw_all):
            q = q_map[i]
            report.lag_correlations.append(
                MoodLagCorrelation(
                    predictor="mean_sentiment",
                    outcome=raw.outcome,
                    lag_days=raw.lag,
                    r=round(raw.r, 4),
                    n=raw.n,
                    label=f"mean_sentiment → {raw.outcome} (lag={raw.lag}d)",
                    p_value=round(raw.p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                )
            )

    report.caveats = _build_caveats(start, end, mood_by_date)
    report.summary = _build_summary(report)
    return report


# ── Summary / caveats ─────────────────────────────────────────────────────────


def _build_caveats(
    start: date,
    end: date,
    mood_by_date: dict[date, Any],
) -> list[str]:
    return [
        "Sentiment of the operator's OWN writing is a noisy proxy for "
        "ground-truth mood; it captures affect expressed in text, not "
        "felt affect.",
        "Selection bias: only days WITH written text contribute; "
        "prolific writing days (high engagement) may not represent low-mood "
        "days (withdrawal).",
        "Lag-1 associations (sentiment today → physiology tomorrow) are NOT "
        "causal; confounders (workload, circadian phase, substance use) can "
        "produce the same signal.",
        "Health signals (HRV, stress, sleep) have export cutoffs; "
        "the available overlap window may be short.",
        f"Window: {start} → {end}. Days with text: {len(mood_by_date)}.",
        "p-values FDR-corrected (Benjamini-Hochberg) across all "
        "(signal × lag) pairs in this call.",
        "The twitter-roberta model was trained on English tweets; "
        "multilingual text (Polish on Wykop) will score less reliably.",
    ]


def _build_summary(report: MoodHealthReport) -> str:
    lines = [
        f"Mood × Health Correlation Report: {report.window_start} → {report.window_end}",
        f"  Mood days with text: {report.mood_days_in_window}",
        f"  Total tests (signal × lag): {report.n_tests}",
        "",
    ]

    significant = [c for c in report.lag_correlations if c.significant]
    significant.sort(key=lambda c: -abs(c.r))

    if significant:
        lines.append(
            f"FDR-significant lagged associations "
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
        c for c in report.lag_correlations
        if not c.significant and abs(c.r) > 0.2
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
    "DEFAULT_LAGS",
    "FDR_TARGET",
    "MIN_PAIRS",
    "MoodHealthReport",
    "MoodLagCorrelation",
    "mood_health_correlation",
]
