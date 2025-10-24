"""Substance × health cross-correlation analysis.

Uses the OperatorDay daily matrix to answer:
  - Does a given substance's use predict next-day stress? sleep quality?
  - Does abstinence correlate with HRV changes? (with proper time lags)
  - What's the dose-response curve? (mg → stress/sleep/HR)
  - Are there withdrawal signatures in health data?

STATISTICAL INTEGRITY CONTRACT
------------------------------
These outputs feed LLM-written narratives, so every reported association must
carry the machinery needed to avoid false claims:

* **Multiple comparisons.** ``analyze`` evaluates ~|substances| × |signals| ×
  (max_lag+1) correlations. Reporting raw ``|r| > 0.2`` over that family inflates
  false positives. Every ``LagCorrelation`` now carries a raw two-tailed
  ``p_value``, a Benjamini-Hochberg FDR ``q_value`` computed across the *entire*
  test family, and an ``significant`` flag derived from that q-value. The summary
  separates FDR-significant associations from exploratory ones.

* **Missing ≠ zero.** Substance mg columns default to ``0.0`` on absent days, so a
  window past the substance log's coverage would fabricate "abstinence"
  correlated against real physiology. Before correlating we clamp to the
  intersection of substance and health coverage (``coverage_bounds`` /
  materialization manifests) and exclude out-of-coverage days via
  ``partition_by_coverage`` rather than treating them as 0 mg. The covered range
  is emitted as provenance.

* **Dose-response n.** Buckets carry their observation count; buckets below
  ``MIN_BUCKET_N`` are flagged and excluded from the headline curve.

* **Association, not causation.** The summary frames results as lagged
  *association* with the n + covered-range caveat inline, not just in a docstring.

Methods:
  - Cross-correlation with lag windows (0-7 days), FDR-corrected
  - Change-point detection at dose boundaries
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
from ..core.coverage import CoverageBounds, partition_by_coverage
from .operator_daily import OperatorDay, operator_daily_matrix

#: Minimum paired observations before a lag correlation is computed at all.
MIN_PAIRS = 10

#: Minimum observations in a dose bucket before its mean is trusted; below this
#: the bucket mean is statistical noise (n=1-2) and is flagged / excluded.
MIN_BUCKET_N = 3

#: FDR target for the Benjamini-Hochberg correction across the full test family.
FDR_TARGET = 0.05

#: Materialized-dataset name that backs each health signal. ``sleep_hours`` comes
#: from the sleep export; the per-minute physiology signals come from the Samsung
#: health export. Used to look up real coverage bounds (missing ≠ zero).
_HEALTH_DATASET = {
    "stress_mean": "health",
    "hr_mean_bpm": "health",
    "hrv_sdnn": "health",
    "sleep_hours": "sleep",
}


@dataclass(frozen=True)
class LagCorrelation:
    """Correlation at a specific time lag, with significance machinery.

    ``p_value`` is the raw two-tailed t-test p-value at this lag. ``q_value`` is
    the Benjamini-Hochberg FDR-adjusted p-value across the *entire* family of
    correlations evaluated by a single ``analyze`` call (all substance × signal ×
    lag combinations), and ``significant`` is ``q_value < FDR_TARGET``. ``n`` is
    the number of paired, in-coverage observations behind ``r``.
    """

    lag_days: int  # 0 = same day, 1 = substance today → health tomorrow, etc.
    r: float  # Pearson correlation coefficient
    n: int  # number of paired observations
    label: str  # e.g. "caffeine mg → next-day stress"
    p_value: float = 1.0  # raw two-tailed t-test p-value at this lag
    q_value: float = 1.0  # BH FDR-adjusted p across the full test family
    significant: bool = False  # q_value < FDR_TARGET


@dataclass(frozen=True)
class DoseBucket:
    """One dose-response bucket with its observation count.

    ``reliable`` is ``False`` when ``n < MIN_BUCKET_N``; such buckets are flagged
    rather than dropped silently, because a mean over n=1-2 days is noise.
    """

    dose_mg: float
    n: int
    stress_mean: Optional[float]
    sleep_hours_mean: Optional[float]
    reliable: bool


@dataclass
class SubstanceHealthReport:
    """Full substance-health correlation analysis."""

    window_start: date
    window_end: date
    n_days: int
    n_substance_days: int

    # Coverage-clamped analysis window actually used for correlations. ``None``
    # when no substance×health overlap exists; correlations are then empty.
    covered_start: Optional[date] = None
    covered_end: Optional[date] = None
    # Human-readable provenance lines for substance + each health signal.
    coverage_provenance: list[str] = field(default_factory=list)

    # Total number of correlations in the FDR test family (for transparency).
    n_tests: int = 0

    # Lag correlations for each substance→health pair (every one carries p/q/n).
    lag_correlations: list[LagCorrelation] = field(default_factory=list)

    # Dose-response: for each substance, ordered buckets carrying per-bucket n.
    dose_response: dict[str, list[DoseBucket]] = field(default_factory=dict)

    # Abstinence periods: contiguous blocks of zero-dose days (in-coverage only).
    abstinence_periods: list[tuple[date, date, int]] = field(default_factory=list)

    # Summary text
    summary: str = ""


def analyze(
    start: date,
    end: date,
    *,
    substances: Sequence[str] | None = None,
    health_signals: Sequence[str] = ("stress_mean", "sleep_hours", "hr_mean_bpm", "hrv_sdnn"),
    max_lag: int = 7,
) -> SubstanceHealthReport:
    """Run the full substance-health correlation analysis.

    Args:
        start, end: date range
        substances: which substances to analyze. Defaults to every substance
            name actually logged in the window (from OperatorDay.substance_mg_by_name)
            rather than a fixed list -- so this covers whatever's being tracked
            without needing source changes when that set changes.
        health_signals: which health signals to correlate against
        max_lag: maximum lag in days (0-7)

    Returns:
        SubstanceHealthReport with FDR-corrected correlations, per-bucket n,
        coverage-clamped window, and an association (not causation) summary.
    """
    rows = operator_daily_matrix(start, end, skip_slow=True)
    rows_by_date = {r.date: r for r in rows}

    if substances is None:
        substances = sorted({name for r in rows for name in r.substance_mg_by_name})

    # ── Coverage: missing ≠ zero ──
    # Substance mg defaults to 0.0 on absent days; correlating those fabricates
    # abstinence. Clamp the window to substance coverage intersected with the
    # health-signal coverage actually in play, and restrict every correlation to
    # in-coverage dates via partition_by_coverage.
    bounds = _coverage_bounds_for(substances, health_signals)
    substance_bounds = bounds["__substance__"]
    covered = _intersect_window(start, end, substance_bounds, bounds, health_signals)

    substance_days = [r for r in rows if r.substance_doses > 0]
    report = SubstanceHealthReport(
        window_start=start,
        window_end=end,
        n_days=len(rows),
        n_substance_days=len(substance_days),
        coverage_provenance=_provenance_lines(substance_bounds, bounds, health_signals),
    )

    substance_map: dict[str, Callable[[OperatorDay], Optional[float]]] = {
        name: (lambda r, name=name: r.substance_mg_by_name.get(name)) for name in substances
    }
    health_map: dict[str, Callable[[OperatorDay], Optional[float]]] = {
        "stress_mean": lambda r: r.stress_mean,
        "sleep_hours": lambda r: r.sleep_hours,
        "hr_mean_bpm": lambda r: r.hr_mean_bpm,
        "hrv_sdnn": lambda r: r.hrv_sdnn,
    }

    if covered is None:
        # No substance×health overlap: refuse to fabricate correlations.
        report.covered_start = None
        report.covered_end = None
        report.summary = _build_summary(report)
        return report

    covered_start, covered_end = covered
    report.covered_start = covered_start
    report.covered_end = covered_end

    # ── Lag correlations (collect raw r/p, then one FDR pass over the family) ──
    raw: list[tuple[str, str, int, float, int, float]] = []  # label-parts + r/n/p
    for sub_name in substances:
        sub_fn = substance_map.get(sub_name)
        if sub_fn is None:
            continue
        sub_cov = substance_bounds
        for health_name in health_signals:
            health_fn = health_map.get(health_name)
            if health_fn is None:
                continue
            health_cov = bounds.get(health_name)
            for lag in range(max_lag + 1):
                stat = _lag_correlation(
                    rows_by_date,
                    covered_start,
                    covered_end,
                    sub_fn,
                    health_fn,
                    lag,
                    sub_cov,
                    health_cov,
                )
                if stat is not None:
                    r, n, p = stat
                    raw.append((sub_name, health_name, lag, r, n, p))

    report.n_tests = len(raw)
    if raw:
        # Benjamini-Hochberg FDR across the ENTIRE family (all sub×signal×lag).
        q_by_idx = _benjamini_hochberg({i: row[5] for i, row in enumerate(raw)})
        for i, (sub_name, health_name, lag, r, n, p) in enumerate(raw):
            q = q_by_idx[i]
            report.lag_correlations.append(
                LagCorrelation(
                    lag_days=lag,
                    r=round(r, 4),
                    n=n,
                    label=f"{sub_name} mg → {health_name} (lag={lag}d)",
                    p_value=round(p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                )
            )

    # ── Dose-response (in-coverage days only; per-bucket n) ──
    covered_rows = [
        rows_by_date[d]
        for d in _covered_dates(covered_start, covered_end, substance_bounds)
        if d in rows_by_date
    ]
    for sub_name in substances:
        sub_fn = substance_map.get(sub_name)
        if sub_fn is None:
            continue
        dose_buckets: dict[int, list[OperatorDay]] = defaultdict(list)
        for row in covered_rows:
            mg = sub_fn(row)
            if mg is not None and mg > 0:
                bucket = min(int(mg / 50) * 50, 300)
                dose_buckets[bucket].append(row)

        dr: list[DoseBucket] = []
        for dose in sorted(dose_buckets):
            members = dose_buckets[dose]
            stresses = [m.stress_mean for m in members if m.stress_mean is not None]
            sleeps = [m.sleep_hours for m in members if m.sleep_hours is not None]
            dr.append(
                DoseBucket(
                    dose_mg=float(dose),
                    n=len(members),
                    stress_mean=statistics.mean(stresses) if stresses else None,
                    sleep_hours_mean=statistics.mean(sleeps) if sleeps else None,
                    reliable=len(members) >= MIN_BUCKET_N,
                )
            )
        report.dose_response[sub_name] = dr

    # ── Abstinence periods (in-coverage only; absent days are NOT zero-dose) ──
    report.abstinence_periods = _find_abstinence_periods(covered_rows)

    # ── Summary ──
    report.summary = _build_summary(report)

    return report


def write_report(out: Path, *, start: date, end: date) -> dict[str, Any]:
    import json
    from datetime import datetime, timezone
    from dataclasses import asdict
    from lynchpin.core.io import save_json
    report = analyze(start, end)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        **asdict(report),
    }
    save_json(out, json.loads(json.dumps(payload, default=str)))
    return payload


def _lag_correlation(
    rows_by_date: dict[date, OperatorDay],
    start: date,
    end: date,
    substance_fn: Callable[[OperatorDay], Optional[float]],
    health_fn: Callable[[OperatorDay], Optional[float]],
    lag: int,
    substance_bounds: CoverageBounds,
    health_bounds: Optional[CoverageBounds],
) -> Optional[tuple[float, int, float]]:
    """Pearson r + raw two-tailed p between substance day D and health day D+lag.

    Only pairs where the substance day is within substance coverage AND the
    health day is within that signal's coverage are used — absent days are
    excluded rather than coerced to 0 (missing ≠ zero). Returns ``(r, n, p)`` or
    ``None`` when fewer than ``MIN_PAIRS`` valid pairs survive.
    """
    xs: list[float] = []
    ys: list[float] = []

    cursor = start
    while cursor + timedelta(days=lag) <= end:
        health_day = cursor + timedelta(days=lag)
        # Coverage gate: substance day and health day must both be observed.
        if not substance_bounds.covers(cursor):
            cursor += timedelta(days=1)
            continue
        if health_bounds is not None and not health_bounds.covers(health_day):
            cursor += timedelta(days=1)
            continue

        sub_row = rows_by_date.get(cursor)
        health_row = rows_by_date.get(health_day)
        if sub_row is None or health_row is None:
            cursor += timedelta(days=1)
            continue

        x = substance_fn(sub_row)
        y = health_fn(health_row)
        if x is None or y is None:
            cursor += timedelta(days=1)
            continue
        xs.append(x)
        ys.append(y)
        cursor += timedelta(days=1)

    if len(xs) < MIN_PAIRS:
        return None

    r = _pearson_r(xs, ys)
    if r is None:
        return None

    n = len(xs)
    # Two-tailed t-test p-value (Student's t via scipy; see analytics._t_test_p).
    import math

    if abs(r) >= 1.0:
        p = 0.0
    else:
        t_stat = r * math.sqrt((n - 2) / (1 - r ** 2))
        p = _t_test_p(t_stat, n - 2)
    return (r, n, p)


def _coverage_bounds_for(
    substances: Sequence[str],
    health_signals: Sequence[str],
) -> dict[str, CoverageBounds]:
    """Resolve real coverage bounds for substance + each requested health signal.

    Substance and health/sleep are materialized datasets that are NOT keys in
    ``available_sources()`` (and hence absent from ``coverage_bounds()``), so we
    read their first/last dates from the materialization audit — the authority
    for those datasets. Keyed by health-signal name plus ``"__substance__"``.
    """
    from lynchpin.materialization import audit_materialization

    audit = {row.name: row for row in audit_materialization()}

    def _bounds(dataset: str, source_label: str) -> CoverageBounds:
        row = audit.get(dataset)
        first = row.first_date if row is not None else None
        last = row.last_date if row is not None else None
        return CoverageBounds(source=source_label, first=first, last=last, kind="export")

    out: dict[str, CoverageBounds] = {"__substance__": _bounds("substance", "substance")}
    for signal in health_signals:
        dataset = _HEALTH_DATASET.get(signal)
        if dataset is None:
            continue
        out[signal] = _bounds(dataset, signal)
    return out


def _intersect_window(
    start: date,
    end: date,
    substance_bounds: CoverageBounds,
    bounds: dict[str, CoverageBounds],
    health_signals: Sequence[str],
) -> Optional[tuple[date, date]]:
    """Clamp [start, end] to substance coverage ∩ union of health coverage.

    Per-pair coverage gating in ``_lag_correlation`` enforces exact bounds for
    each signal; this outer clamp bounds the scan to the widest plausible
    overlap (substance ∩ the most permissive health signal). Returns ``None``
    when there is no overlap at all.
    """
    clamped = substance_bounds.clamp(start, end)
    if clamped is None:
        return None
    lo, hi = clamped

    # Widest health coverage among requested signals (a pair survives only if its
    # own signal also covers the day, checked per-pair).
    health_first: Optional[date] = None
    health_last: Optional[date] = None
    for signal in health_signals:
        b = bounds.get(signal)
        if b is None or b.first is None or b.last is None:
            continue
        health_first = b.first if health_first is None else min(health_first, b.first)
        health_last = b.last if health_last is None else max(health_last, b.last)

    if health_first is None or health_last is None:
        return None
    lo = max(lo, health_first)
    hi = min(hi, health_last)
    if lo > hi:
        return None
    return lo, hi


def _covered_dates(
    start: date,
    end: date,
    bounds: CoverageBounds,
) -> list[date]:
    """Dates in [start, end] that fall within substance coverage."""
    from ..core.coverage import date_range

    in_cov, _out = partition_by_coverage(date_range(start, end), bounds)
    return in_cov


def _provenance_lines(
    substance_bounds: CoverageBounds,
    bounds: dict[str, CoverageBounds],
    health_signals: Sequence[str],
) -> list[str]:
    """Human-readable coverage provenance for substance + each health signal."""
    lines = [substance_bounds.provenance()]
    for signal in health_signals:
        b = bounds.get(signal)
        if b is not None:
            lines.append(b.provenance())
    return lines


def _find_abstinence_periods(
    rows: list[OperatorDay],
) -> list[tuple[date, date, int]]:
    """Find contiguous blocks of zero-dose days within the supplied rows.

    Callers MUST pass in-coverage rows only — outside substance coverage a 0.0 mg
    day is "not observed", not "abstinence", and would fabricate withdrawal-period
    signals.
    """
    periods: list[tuple[date, date, int]] = []
    ordered = sorted(rows, key=lambda r: r.date)
    start: Optional[date] = None
    for r in ordered:
        if r.substance_doses == 0:
            if start is None:
                start = r.date
        else:
            if start is not None:
                days = (r.date - start).days
                if days >= 3:  # at least 3 days to count as a period
                    periods.append((start, r.date - timedelta(days=1), days))
                start = None
    if start is not None and ordered:
        days = (ordered[-1].date - start).days + 1
        if days >= 3:
            periods.append((start, ordered[-1].date, days))
    return periods


def _build_summary(report: SubstanceHealthReport) -> str:
    """Build a human-readable summary of findings.

    Frames results as lagged ASSOCIATION (not causation), and carries the
    coverage window + per-correlation n inline so an LLM copying this text cannot
    drop the caveats.
    """
    lines = [
        f"Substance × Health Report: {report.window_start} → {report.window_end}",
        f"  Days in window: {report.n_days} ({report.n_substance_days} with substance use)",
    ]

    # Coverage provenance (missing ≠ zero).
    if report.covered_start is not None and report.covered_end is not None:
        lines.append(
            f"  Analysis clamped to substance∩health coverage: "
            f"{report.covered_start} → {report.covered_end}"
        )
    else:
        lines.append(
            "  No substance∩health coverage overlap in window — "
            "no correlations computed (absent days are NOT treated as 0 mg)."
        )
    for prov in report.coverage_provenance:
        lines.append(f"    {prov}")
    lines.append("")

    if report.lag_correlations:
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
                f"No associations survive Benjamini-Hochberg FDR correction "
                f"(q<{FDR_TARGET:g}) across {report.n_tests} tests."
            )

        # Exploratory (large |r| but NOT FDR-significant) — clearly separated.
        exploratory = [
            c
            for c in report.lag_correlations
            if not c.significant and abs(c.r) > 0.2
        ]
        exploratory.sort(key=lambda c: -abs(c.r))
        if exploratory:
            lines.append("")
            lines.append(
                "Exploratory only (|r|>0.2 but NOT FDR-significant — likely noise, "
                "do not report as findings):"
            )
            for c in exploratory[:10]:
                direction = "↑" if c.r > 0 else "↓"
                lines.append(
                    f"  r={c.r:+.3f} {direction}  {c.label} "
                    f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
                )

    # Dose-response reliability note.
    suppressed = sum(
        1
        for buckets in report.dose_response.values()
        for b in buckets
        if not b.reliable
    )
    if suppressed:
        lines.append("")
        lines.append(
            f"Dose-response: {suppressed} bucket(s) below n={MIN_BUCKET_N} flagged "
            "as unreliable (means over 1-2 days are noise, excluded from the curve)."
        )

    # Abstinence periods.
    if report.abstinence_periods:
        lines.append("")
        lines.append(
            f"Abstinence periods (≥3 in-coverage days): {len(report.abstinence_periods)}"
        )
        for a_start, a_end, days in report.abstinence_periods[:10]:
            lines.append(f"  {a_start} → {a_end} ({days}d)")

    lines.append("")
    lines.append(
        "CAVEAT: these are lagged ASSOCIATIONS, not causation. A surviving "
        "correlation does not establish that the substance drives the physiology "
        "signal — confounders, common trends, and autocorrelation can all produce "
        "it. Interpret only within the covered range above and with the reported "
        "per-correlation n; absent days are excluded, not counted as zero dose."
    )

    return "\n".join(lines)


__all__ = [
    "DoseBucket",
    "LagCorrelation",
    "SubstanceHealthReport",
    "analyze",
]
