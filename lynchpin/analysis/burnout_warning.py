"""Burnout / overwork early-warning signal via multi-signal risk composite.

Fuses stress, sleep, focus-fragmentation, resting heart rate, and HRV into a
daily risk indicator. Anomaly and changepoint detection flag emerging risk windows.

Methodology
-----------
This is a **heuristic early-warning tool**, not a clinical diagnostic. The
composite score is an association-based signal derived from a small personal
dataset. Limitations:

  - Small N: individual-level health data is noisy; even a clear "risk window"
    may reflect a bad sleep patch rather than true burnout accumulation.
  - Causal direction is assumed, not established: high stress co-occurs with
    poor sleep and high fragmentation, but causation is unknown.
  - Signal coverage varies significantly: HRV data may be sparse; sleep exports
    end 2026-03-28; stress ends 2026-03-29. Windows with <2 signals per day
    produce no score, not a misleading low score.
  - Resting HR ≥ baseline is a known stress marker, but day-to-day HR
    fluctuation has many causes (illness, exercise, caffeine, posture).

Coverage semantics (missing ≠ zero)
-------------------------------------
Each signal is tied to a data source with observed coverage. A day's composite
includes a signal **only** when that day falls inside the signal's observed
coverage range. Out-of-coverage days are ABSENT (excluded), never coerced to 0
or imputed to the mean. Days with fewer than ``MIN_SIGNALS_PER_DAY`` present
signals yield ``risk_score = None``.

Sign conventions (all signals contribute positively to risk when high):
  - stress_mean:     high stress → higher risk (direct)
  - aw_fragmentation: high fragmentation → higher risk (direct)
  - hr_resting_bpm:  elevated resting HR → higher risk (direct)
  - sleep_hours:     short sleep → higher risk (sign-flipped: low sleep = high risk)
  - hrv_sdnn:        suppressed HRV → higher risk (sign-flipped: low HRV = high risk)
  - hrv_rmssd:       suppressed HRV → higher risk (sign-flipped: low rmssd = high risk)
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from datetime import date as Date
from pathlib import Path
from typing import Any, Optional

from ..core.analytics import detect_changepoints, detect_trend
from ..core.coverage import CoverageBounds, partition_by_coverage
from ..sources.source_observations import coverage_bounds
from .operator_daily import OperatorDay, operator_daily_matrix

# Minimum number of signals that must be present on a day to produce a score.
# Below this threshold the score is None (insufficient evidence).
MIN_SIGNALS_PER_DAY: int = 2


# ── Signal definitions ────────────────────────────────────────────────────────
# Each _Signal: (name, accessor, weight, coverage_key, higher_is_riskier)
# ``higher_is_riskier=True``: raw value increases → risk score increases.
# ``higher_is_riskier=False``: raw value decreases → risk score increases
#   (the signal is negated before z-normalization so the z-scores are uniform
#   in their risk direction — large positive z = higher risk contribution).

@dataclass(frozen=True)
class _Signal:
    name: str
    weight: float
    coverage_key: str
    higher_is_riskier: bool
    # Accessor returns None if the field is absent that day.
    # Named accessor (lambda stored separately to avoid frozen-class issues).


# Accessors as module-level functions for clarity and testability.
def _get_stress(r: OperatorDay) -> Optional[float]:
    return r.stress_mean


def _get_fragmentation(r: OperatorDay) -> Optional[float]:
    return r.aw_fragmentation


def _get_hr_resting(r: OperatorDay) -> Optional[float]:
    return r.hr_resting_bpm


def _get_sleep(r: OperatorDay) -> Optional[float]:
    return r.sleep_hours


def _get_hrv_sdnn(r: OperatorDay) -> Optional[float]:
    return r.hrv_sdnn


def _get_hrv_rmssd(r: OperatorDay) -> Optional[float]:
    return r.hrv_rmssd


_SIGNALS: tuple[_Signal, ...] = (
    # Stress score (Samsung Health; higher = more stress = higher burnout risk)
    _Signal("stress", weight=2.0, coverage_key="health", higher_is_riskier=True),
    # Focus fragmentation (AW; high context-switch rate = higher risk)
    _Signal("fragmentation", weight=1.5, coverage_key="activitywatch", higher_is_riskier=True),
    # Resting HR (elevated = physiological stress marker = higher risk)
    _Signal("hr_resting", weight=1.0, coverage_key="health", higher_is_riskier=True),
    # Sleep hours (short sleep = higher risk; sign-flipped)
    _Signal("sleep", weight=2.0, coverage_key="sleep", higher_is_riskier=False),
    # HRV SDNN (suppressed = autonomic stress = higher risk; sign-flipped)
    _Signal("hrv_sdnn", weight=1.0, coverage_key="health", higher_is_riskier=False),
    # HRV RMSSD (suppressed = parasympathetic withdrawal = higher risk; sign-flipped)
    _Signal("hrv_rmssd", weight=1.0, coverage_key="health", higher_is_riskier=False),
)

# Map signal name → accessor function.
_SIGNAL_ACCESSORS: dict[str, object] = {
    "stress": _get_stress,
    "fragmentation": _get_fragmentation,
    "hr_resting": _get_hr_resting,
    "sleep": _get_sleep,
    "hrv_sdnn": _get_hrv_sdnn,
    "hrv_rmssd": _get_hrv_rmssd,
}

# Coverage key → coverage_bounds() key mapping for sources not directly in
# available_sources(): "health" and "sleep" are materialized export datasets.
_COVERAGE_KEY_MAP: dict[str, str] = {
    "health": "health",
    "activitywatch": "activitywatch",
    "sleep": "sleep",
}


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BurnoutDayRisk:
    """Burnout risk assessment for one calendar date.

    ``risk_score`` is a weighted-average z-score over contributing signals, all
    rotated so that positive values indicate higher risk. ``None`` means too few
    signals (<``MIN_SIGNALS_PER_DAY``) were in-coverage to produce a meaningful
    estimate.

    ``contributing_signals`` names the signals that were both in-coverage and
    non-null on this day. ``n_signals`` is ``len(contributing_signals)``.
    """

    date: Date
    risk_score: Optional[float]
    contributing_signals: tuple[str, ...]
    n_signals: int


@dataclass(frozen=True)
class RiskWindow:
    """A flagged elevated-risk window — either a changepoint or anomaly cluster.

    ``kind`` is one of ``"changepoint"`` or ``"anomaly_streak"``.
    ``peak_score`` is the maximum daily risk score within the window.
    """

    start: Date
    end: Date
    kind: str  # "changepoint" | "anomaly_streak"
    peak_score: float
    n_days: int


@dataclass
class BurnoutReport:
    """Complete burnout early-warning analysis over a date range.

    Per-day risk rows include days with ``risk_score = None`` (insufficient
    signal coverage). ``risk_windows`` are the flagged elevated-risk periods.
    ``signal_coverage`` records the observed date range of each signal for
    narrative transparency.
    """

    window_start: Date
    window_end: Date
    n_days: int

    # Per-day risk, including days with None score.
    daily_risk: list[BurnoutDayRisk] = field(default_factory=list)

    # Detected elevated-risk windows (changepoints + anomaly streaks).
    risk_windows: list[RiskWindow] = field(default_factory=list)

    # Per-signal coverage provenance for transparency.
    signal_coverage: list[str] = field(default_factory=list)

    # Overall trend on the scored subset ("rising" / "falling" / "stable" / "insufficient").
    trend: str = "insufficient"

    summary: str = ""


# ── Core implementation ───────────────────────────────────────────────────────

def analyze(*, start: date, end: date) -> BurnoutReport:
    """Compute daily burnout-risk composite and flag emerging risk windows.

    Fuses stress, sleep, focus-fragmentation, resting HR, and HRV into a
    weighted-average risk score per day. Each signal is included only when
    the day falls within that signal's observed coverage. Days with fewer than
    ``MIN_SIGNALS_PER_DAY`` contributing signals yield ``risk_score = None``.

    Anomaly/changepoint detection is applied to the scored subset to surface
    emerging risk windows.

    Returns
    -------
    BurnoutReport
        Per-day risk rows, flagged risk windows, coverage provenance, and
        optional trend direction on the scored subset.

    Caveats
    -------
    - Heuristic early-warning only; not a clinical burnout diagnosis.
    - Scores are relative (z-normalized within the analysis window).
    - Small-N: with <30 scored days, trend detection is unreliable.
    - Association only; causation is not established.
    """
    rows = operator_daily_matrix(start, end)
    report = BurnoutReport(window_start=start, window_end=end, n_days=len(rows))

    if not rows:
        report.summary = "No data in requested range."
        return report

    # Resolve coverage bounds for each unique coverage_key used.
    src_bounds = coverage_bounds()
    signal_bounds = _resolve_signal_bounds(src_bounds)
    report.signal_coverage = [signal_bounds[s.name].provenance() for s in _SIGNALS]

    # Build coverage sets: for each signal, which dates are in-coverage?
    all_dates = [r.date for r in rows]
    covered_dates: dict[str, set[date]] = {}
    for sig in _SIGNALS:
        in_cov, _ = partition_by_coverage(all_dates, signal_bounds[sig.name])
        covered_dates[sig.name] = set(in_cov)

    # Collect raw covered values per signal for z-normalization.
    raw_values: dict[str, list[float]] = defaultdict(list)
    raw_per_row: list[dict[str, float]] = []
    for r in rows:
        row_raw: dict[str, float] = {}
        for sig in _SIGNALS:
            if r.date not in covered_dates[sig.name]:
                continue
            accessor = _SIGNAL_ACCESSORS[sig.name]
            val = accessor(r)  # type: ignore[operator]
            if val is None:
                continue
            # Negate sign-flipped signals so that large positive = high risk.
            oriented = float(val) if sig.higher_is_riskier else -float(val)
            raw_values[sig.name].append(oriented)
            row_raw[sig.name] = oriented
        raw_per_row.append(row_raw)

    # Z-normalize each signal over its covered distribution.
    z_stats: dict[str, tuple[float, float]] = {}
    for sig_name, vals in raw_values.items():
        if len(vals) < 5:
            # Too few covered points to compute meaningful z-stats; skip.
            z_stats[sig_name] = (0.0, 1.0)
        else:
            mean = statistics.mean(vals)
            stdev = statistics.stdev(vals)
            z_stats[sig_name] = (mean, stdev if stdev > 0 else 1.0)

    weight_of = {sig.name: sig.weight for sig in _SIGNALS}

    # Build per-day risk rows.
    for r, row_raw in zip(rows, raw_per_row):
        if not row_raw:
            report.daily_risk.append(BurnoutDayRisk(
                date=r.date,
                risk_score=None,
                contributing_signals=(),
                n_signals=0,
            ))
            continue

        total_weight = 0.0
        total_z = 0.0
        contributing: list[str] = []

        for sig_name, oriented_val in row_raw.items():
            if sig_name not in z_stats:
                continue
            mean, stdev = z_stats[sig_name]
            z = (oriented_val - mean) / stdev
            w = weight_of[sig_name]
            total_z += z * w
            total_weight += w
            contributing.append(sig_name)

        n_sigs = len(contributing)
        if n_sigs < MIN_SIGNALS_PER_DAY:
            report.daily_risk.append(BurnoutDayRisk(
                date=r.date,
                risk_score=None,
                contributing_signals=tuple(contributing),
                n_signals=n_sigs,
            ))
        else:
            score = total_z / total_weight
            report.daily_risk.append(BurnoutDayRisk(
                date=r.date,
                risk_score=score,
                contributing_signals=tuple(contributing),
                n_signals=n_sigs,
            ))

    # Extract scored subset (days with non-None scores) for detection.
    scored = [(dr.date, dr.risk_score) for dr in report.daily_risk if dr.risk_score is not None]

    if len(scored) >= 10:
        scores_only = [s for _, s in scored]
        report.risk_windows = _detect_risk_windows(scored, scores_only)
        report.trend = _compute_trend(scores_only)
    else:
        report.trend = "insufficient"

    report.summary = _build_summary(report)
    return report


def write_report(out: Path, *, start: date, end: date) -> dict[str, Any]:
    import json
    from datetime import datetime, timezone
    from dataclasses import asdict
    from lynchpin.core.io import save_json
    report = analyze(start=start, end=end)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        **asdict(report),
    }
    save_json(out, json.loads(json.dumps(payload, default=str)))
    return payload


def _resolve_signal_bounds(src_bounds: dict[str, CoverageBounds]) -> dict[str, CoverageBounds]:
    """Return a CoverageBounds for every signal.

    ``health`` and ``sleep`` are materialized export datasets not always present
    in ``coverage_bounds()`` directly. Fall back to an unknown-coverage bound
    when absent so downstream ``covers()`` returns False (absent = excluded).
    """
    out: dict[str, CoverageBounds] = {}
    for sig in _SIGNALS:
        key = sig.coverage_key
        bound = src_bounds.get(key)
        if bound is not None:
            out[sig.name] = CoverageBounds(
                source=sig.name, first=bound.first, last=bound.last, kind=bound.kind
            )
        else:
            # Coverage unknown: all dates will be out-of-coverage → signal absent.
            out[sig.name] = CoverageBounds(source=sig.name, first=None, last=None, kind="export")
    return out


def _detect_risk_windows(
    scored: list[tuple[date, float]],
    scores: list[float],
) -> list[RiskWindow]:
    """Detect risk windows via changepoints and high-anomaly streaks."""
    windows: list[RiskWindow] = []

    # --- Changepoint detection on the risk series ---
    changepoints = detect_changepoints(scores, min_segment=5, max_changepoints=5)
    for cp in changepoints:
        # Only flag changepoints that represent a shift *upward* in risk.
        if cp.after_mean <= cp.before_mean:
            continue
        # The risk window runs from the changepoint to the next changepoint (or end).
        cp_date = scored[cp.index][0]
        end_idx = cp.index + 1
        while end_idx < len(scored):
            end_idx += 1
        end_date = scored[-1][0]
        window_scores = [s for _, s in scored[cp.index:]]
        peak = max(window_scores) if window_scores else cp.after_mean
        windows.append(RiskWindow(
            start=cp_date,
            end=end_date,
            kind="changepoint",
            peak_score=round(peak, 3),
            n_days=len(window_scores),
        ))

    # --- Anomaly streak detection: consecutive days above threshold ---
    # Use IQR-based anomaly scoring on the risk series.
    threshold_score = 1.0  # z-score threshold for "high-risk day"
    streak_start: Optional[date] = None
    streak_scores: list[float] = []

    for i, (d, s) in enumerate(scored):
        if s >= threshold_score:
            if streak_start is None:
                streak_start = d
            streak_scores.append(s)
        else:
            if streak_start is not None and len(streak_scores) >= 3:
                streak_end = scored[i - 1][0]
                windows.append(RiskWindow(
                    start=streak_start,
                    end=streak_end,
                    kind="anomaly_streak",
                    peak_score=round(max(streak_scores), 3),
                    n_days=len(streak_scores),
                ))
            streak_start = None
            streak_scores = []

    # Close any open streak at end of series.
    if streak_start is not None and len(streak_scores) >= 3:
        windows.append(RiskWindow(
            start=streak_start,
            end=scored[-1][0],
            kind="anomaly_streak",
            peak_score=round(max(streak_scores), 3),
            n_days=len(streak_scores),
        ))

    # Sort by start date.
    windows.sort(key=lambda w: w.start)
    return windows


def _compute_trend(scores: list[float]) -> str:
    """Simple trend direction on the scored subset."""
    if len(scores) < 10:
        return "insufficient"
    result = detect_trend(scores)
    return result.direction


def _build_summary(report: BurnoutReport) -> str:
    scored_days = [dr for dr in report.daily_risk if dr.risk_score is not None]
    lines = [
        f"Burnout Early-Warning: {report.window_start} → {report.window_end}",
        f"  Total days: {report.n_days}",
        f"  Scored days (≥{MIN_SIGNALS_PER_DAY} signals): {len(scored_days)}",
        f"  Risk windows flagged: {len(report.risk_windows)}",
        f"  Trend on scored subset: {report.trend}",
        "",
        "Signal coverage (missing ≠ zero):",
    ]
    for prov in report.signal_coverage:
        lines.append(f"  {prov}")

    if report.risk_windows:
        lines += ["", "Risk windows:"]
        for w in report.risk_windows:
            lines.append(
                f"  [{w.kind}] {w.start} → {w.end} ({w.n_days}d) peak={w.peak_score:.2f}"
            )

    if scored_days:
        top_risk = sorted(scored_days, key=lambda dr: dr.risk_score or 0, reverse=True)[:5]
        lines += ["", "Top-risk days:"]
        for dr in top_risk:
            lines.append(
                f"  {dr.date}  score={dr.risk_score:.2f}  signals={list(dr.contributing_signals)}"
            )

    lines += [
        "",
        "Caveats: heuristic early-warning only; not a diagnosis; association only;",
        "scores are relative within this analysis window; small-N personal data.",
    ]
    return "\n".join(lines)


__all__ = [
    "MIN_SIGNALS_PER_DAY",
    "BurnoutDayRisk",
    "RiskWindow",
    "BurnoutReport",
    "analyze",
]
