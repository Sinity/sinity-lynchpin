"""Sleep architecture × next-day productivity correlation analysis.

Uses raw ``SleepArchitecture`` stage records (deep%, REM%, efficiency,
first-REM latency) joined to the following day's ``OperatorDay`` productivity
signals (deep-work minutes, focus fragmentation, active hours).  The repo holds
1858 nights of rich sleep architecture that are unused downstream — this module
surfaces those latent signals.

STATISTICAL INTEGRITY CONTRACT
-------------------------------
These outputs feed LLM-written narratives, so every reported association must
carry the machinery needed to avoid false claims:

* **Day-offset join.** Sleep date *d* → productivity date *d+1* (the following
  calendar day).  The offset is explicit, never implicit.

* **Missing ≠ zero.** ``aw_deep_work_min``, ``aw_fragmentation``, and
  ``aw_active_hours`` are ``Optional[float]`` and are ``None`` when
  ActivityWatch did not observe that date.  ``None`` is excluded from every
  correlation — never treated as 0.

* **Coverage clamping.** The analysis window is clamped to the intersection of
  sleep-architecture coverage and ActivityWatch capture coverage.
  ``CoverageBounds.covers()`` / ``partition_by_coverage()`` enforce this.

* **Multiple comparisons.** All pairwise correlations share a single
  Benjamini-Hochberg FDR pass; every ``ArchitecturePairCorrelation`` carries
  ``p_value``, ``q_value``, ``significant``, and ``n``.

* **Association, not causation.** The summary frames results as cross-source
  *associations* with coverage window and per-correlation n inline.  Deep %
  predicting next-day deep work does not establish causality; common confounders
  (recovery state, workday type, seasonal effects) can produce the same signal.

Methods
-------
- Pairwise Pearson r at lag-1 (sleep → next-day), FDR-corrected.
- Architecture pairs: deep_pct, rem_pct, efficiency, first_rem_min,
  total_min → aw_deep_work_min, aw_fragmentation, aw_active_hours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
from ..core.coverage import CoverageBounds, date_range, partition_by_coverage
from ..sources.sleep import SleepArchitecture, sleep_architecture

#: Minimum paired (both in-coverage + both non-None) observations to compute r.
MIN_PAIRS = 10

#: FDR target for the Benjamini-Hochberg correction across the full test family.
FDR_TARGET = 0.05

# ---------------------------------------------------------------------------
# Sleep efficiency derived from SleepArchitecture
# ---------------------------------------------------------------------------

# SleepArchitecture does not carry an efficiency field directly.  We derive
# sleep efficiency as: (total_min - awake_min) / total_min * 100  — the
# standard Rechtschaffen-Kales definition (time asleep / time in bed × 100).
# ``total_min`` here already includes the awake stage in the stage-sequence
# total, which matches the denominator used by Samsung Health metrics.
# (SleepMetrics.sleep_efficiency from merged sleep JSONL is the authoritative
# scalar when available, but SleepArchitecture comes from stage records and
# does not join to SleepMetrics in this module.)


def _efficiency(arch: SleepArchitecture) -> Optional[float]:
    """Derive sleep efficiency % from stage record totals (0-100)."""
    if arch.total_min <= 0:
        return None
    eff = (arch.total_min - arch.awake_min) / arch.total_min * 100.0
    return round(max(0.0, min(100.0, eff)), 2)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchitecturePairCorrelation:
    """Pearson r between one sleep-architecture predictor and one next-day outcome.

    ``p_value`` is the raw two-tailed t-test p.  ``q_value`` is the
    Benjamini-Hochberg FDR-adjusted p across the *entire* test family evaluated
    by a single ``analyze`` call.  ``significant`` is ``q_value < FDR_TARGET``.
    ``n`` is the number of paired, in-coverage, non-None observations.
    """

    predictor: str    # e.g. "deep_pct", "rem_pct", "efficiency", ...
    outcome: str      # e.g. "aw_deep_work_min", "aw_fragmentation", ...
    r: float          # Pearson correlation coefficient
    n: int            # paired in-coverage observations
    p_value: float    # raw two-tailed t-test p-value
    q_value: float    # BH FDR-adjusted p across the full test family
    significant: bool  # q_value < FDR_TARGET
    label: str        # human-readable: "deep_pct → next-day aw_deep_work_min"


@dataclass(frozen=True)
class SleepArchitectureProductivityReport:
    """Full sleep-architecture × next-day productivity analysis.

    Attributes
    ----------
    window_start, window_end:
        Requested analysis window.
    covered_start, covered_end:
        Effective window after clamping to sleep∩AW coverage.  ``None`` when
        there is no overlap — all correlation lists will then be empty.
    n_sleep_nights:
        Sleep-architecture nights in the covered window.
    n_aw_days:
        ActivityWatch days (with at least one non-None AW field) in the covered
        window.
    n_tests:
        Total correlations in the FDR test family (for transparency).
    correlations:
        All pairwise correlations (every one carries p/q/n).
    sleep_coverage_provenance:
        Human-readable coverage line for the sleep source.
    aw_coverage_provenance:
        Human-readable coverage line for ActivityWatch.
    summary:
        Plain-text summary framing results as association, not causation, with
        coverage window and per-correlation n inline.
    """

    window_start: date
    window_end: date
    covered_start: Optional[date]
    covered_end: Optional[date]
    n_sleep_nights: int
    n_aw_days: int
    n_tests: int
    correlations: list[ArchitecturePairCorrelation]
    sleep_coverage_provenance: str
    aw_coverage_provenance: str
    summary: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze(*, start: date, end: date) -> SleepArchitectureProductivityReport:
    """Correlate sleep architecture (night N) with next-day productivity (day N+1).

    Sleep predictors: ``deep_pct``, ``rem_pct``, derived ``efficiency``,
    ``first_rem_min``, ``total_min``.

    Productivity outcomes: ``aw_deep_work_min``, ``aw_fragmentation``,
    ``aw_active_hours`` — all ``Optional[float]``; ``None`` days are excluded,
    never coerced to 0.

    The analysis window is clamped to the intersection of sleep-architecture
    coverage and ActivityWatch coverage.  Only paired observations where both
    sides are present and non-None enter the Pearson r.

    Returns
    -------
    SleepArchitectureProductivityReport
        Typed report with FDR-corrected correlations, coverage provenance,
        paired n per correlation, and an association-not-causation summary.
    """
    # ── Step 1: load sleep architecture (night d) ──
    arch_list = sleep_architecture(start=start, end=end)

    # ── Step 2: load OperatorDay productivity signals (day d+1) ──
    # We need the day AFTER the last sleep night, so widen the AW query by 1.
    from .operator_daily import OperatorDay, operator_daily_matrix

    prod_start = start + timedelta(days=1)
    prod_end = end + timedelta(days=1)
    prod_rows = operator_daily_matrix(prod_start, prod_end, skip_slow=True)
    prod_by_date: dict[date, OperatorDay] = {r.date: r for r in prod_rows}

    # ── Step 3: coverage bounds ──
    sleep_bounds, aw_bounds = _resolve_coverage_bounds(start, end, arch_list, prod_rows)

    # ── Step 4: intersect windows ──
    covered = _intersect(start, end, sleep_bounds, aw_bounds)

    # ── Step 5: build report skeleton ──
    n_sleep = sum(1 for a in arch_list if sleep_bounds.covers(a.date)) if covered else 0
    n_aw = (
        sum(
            1 for r in prod_rows
            if aw_bounds.covers(r.date)
            and any(
                v is not None
                for v in (r.aw_deep_work_min, r.aw_fragmentation, r.aw_active_hours)
            )
        )
        if covered
        else 0
    )

    if covered is None:
        return SleepArchitectureProductivityReport(
            window_start=start,
            window_end=end,
            covered_start=None,
            covered_end=None,
            n_sleep_nights=0,
            n_aw_days=0,
            n_tests=0,
            correlations=[],
            sleep_coverage_provenance=sleep_bounds.provenance(),
            aw_coverage_provenance=aw_bounds.provenance(),
            summary=_build_summary_empty(start, end, sleep_bounds, aw_bounds),
        )

    covered_start, covered_end = covered

    # ── Step 6: build paired observations ──
    arch_by_date: dict[date, SleepArchitecture] = {a.date: a for a in arch_list}

    # Predictor → extractor
    predictors: dict[str, object] = {
        "deep_pct": lambda a: a.deep_pct,
        "rem_pct": lambda a: a.rem_pct,
        "efficiency": _efficiency,
        "first_rem_min": lambda a: a.first_rem_min,
        "total_min": lambda a: a.total_min,
    }
    # Outcome → extractor (from OperatorDay)
    outcomes: dict[str, object] = {
        "aw_deep_work_min": lambda r: r.aw_deep_work_min,
        "aw_fragmentation": lambda r: r.aw_fragmentation,
        "aw_active_hours": lambda r: r.aw_active_hours,
    }

    # ── Step 7: compute raw correlations, then BH FDR ──
    raw: list[tuple[str, str, float, int, float]] = []  # (predictor, outcome, r, n, p)

    all_sleep_dates = date_range(covered_start, covered_end)
    in_sleep_cov, _ = partition_by_coverage(all_sleep_dates, sleep_bounds)

    for pred_name, pred_fn in predictors.items():
        for out_name, out_fn in outcomes.items():
            stat = _paired_correlation(
                sleep_dates=in_sleep_cov,
                arch_by_date=arch_by_date,
                prod_by_date=prod_by_date,
                pred_fn=pred_fn,  # type: ignore[arg-type]
                out_fn=out_fn,    # type: ignore[arg-type]
                aw_bounds=aw_bounds,
            )
            if stat is not None:
                r, n, p = stat
                raw.append((pred_name, out_name, r, n, p))

    n_tests = len(raw)
    correlations: list[ArchitecturePairCorrelation] = []
    if raw:
        q_by_idx = _benjamini_hochberg({i: row[4] for i, row in enumerate(raw)})
        for i, (pred_name, out_name, r, n, p) in enumerate(raw):
            q = q_by_idx[i]
            correlations.append(
                ArchitecturePairCorrelation(
                    predictor=pred_name,
                    outcome=out_name,
                    r=round(r, 4),
                    n=n,
                    p_value=round(p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                    label=f"{pred_name} → next-day {out_name}",
                )
            )

    return SleepArchitectureProductivityReport(
        window_start=start,
        window_end=end,
        covered_start=covered_start,
        covered_end=covered_end,
        n_sleep_nights=n_sleep,
        n_aw_days=n_aw,
        n_tests=n_tests,
        correlations=correlations,
        sleep_coverage_provenance=sleep_bounds.provenance(),
        aw_coverage_provenance=aw_bounds.provenance(),
        summary=_build_summary(
            start=start,
            end=end,
            covered_start=covered_start,
            covered_end=covered_end,
            n_sleep=n_sleep,
            n_aw=n_aw,
            n_tests=n_tests,
            correlations=correlations,
            sleep_prov=sleep_bounds.provenance(),
            aw_prov=aw_bounds.provenance(),
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_coverage_bounds(
    start: date,
    end: date,
    arch_list: list[SleepArchitecture],
    prod_rows: list,  # list[OperatorDay]
) -> tuple[CoverageBounds, CoverageBounds]:
    """Derive CoverageBounds from actual loaded data.

    Sleep architecture comes from Samsung Health stage records, not a
    materialization audit.  We derive first/last from the loaded records.
    ActivityWatch is a continuous capture; we derive bounds from the loaded
    OperatorDay rows that have any non-None AW field.

    Falling back to materialization audits for AW would require importing
    the audit path, which introduces coupling.  The data-driven approach is
    more direct and testable via monkeypatching.
    """
    # Sleep architecture bounds
    if arch_list:
        s_first = min(a.date for a in arch_list)
        s_last = max(a.date for a in arch_list)
    else:
        s_first = s_last = None

    sleep_bounds = CoverageBounds(
        source="sleep_architecture",
        first=s_first,
        last=s_last,
        kind="export",
    )

    # AW bounds: only days with at least one non-None AW field count
    aw_days = [
        r.date
        for r in prod_rows
        if any(
            v is not None
            for v in (r.aw_deep_work_min, r.aw_fragmentation, r.aw_active_hours)
        )
    ]
    if aw_days:
        aw_first = min(aw_days)
        aw_last = max(aw_days)
    else:
        aw_first = aw_last = None

    aw_bounds = CoverageBounds(
        source="activitywatch",
        first=aw_first,
        last=aw_last,
        kind="capture",
    )
    return sleep_bounds, aw_bounds


def _intersect(
    start: date,
    end: date,
    sleep_bounds: CoverageBounds,
    aw_bounds: CoverageBounds,
) -> Optional[tuple[date, date]]:
    """Clamp [start, end] to sleep∩AW coverage.

    Sleep date *d* joins to AW date *d+1*.  The AW bounds are already over
    the d+1 space (loaded as prod_start = start+1 … prod_end = end+1), so
    we intersect sleep bounds against AW bounds shifted back by 1 day for the
    sleep side: a sleep night at date *d* is usable only when *d+1* is within
    AW coverage.  We implement this by computing the sleep date range that
    maps to in-AW-coverage next days.
    """
    # Sleep side: clamp to sleep_bounds directly
    sleep_clamped = sleep_bounds.clamp(start, end)
    if sleep_clamped is None:
        return None
    s_lo, s_hi = sleep_clamped

    # AW side covers d+1; translate back: aw_first → sleep d = aw_first - 1
    if aw_bounds.first is None or aw_bounds.last is None:
        return None
    aw_sleep_lo = aw_bounds.first - timedelta(days=1)
    aw_sleep_hi = aw_bounds.last - timedelta(days=1)

    lo = max(s_lo, aw_sleep_lo)
    hi = min(s_hi, aw_sleep_hi)
    if lo > hi:
        return None
    return lo, hi


def _paired_correlation(
    sleep_dates: list[date],
    arch_by_date: dict[date, SleepArchitecture],
    prod_by_date: dict,  # dict[date, OperatorDay]
    pred_fn: object,
    out_fn: object,
    aw_bounds: CoverageBounds,
) -> Optional[tuple[float, int, float]]:
    """Compute Pearson r between predictor (night d) and outcome (day d+1).

    Only pairs where:
    - architecture record exists for date d
    - predictor value is non-None
    - d+1 is within AW coverage
    - OperatorDay exists for d+1
    - outcome value is non-None (never treat missing AW as 0)

    Returns (r, n, p) or None when fewer than MIN_PAIRS valid pairs survive.
    """
    xs: list[float] = []
    ys: list[float] = []

    for sleep_d in sleep_dates:
        prod_d = sleep_d + timedelta(days=1)

        # AW coverage gate: the outcome day must be in-coverage
        if not aw_bounds.covers(prod_d):
            continue

        arch = arch_by_date.get(sleep_d)
        if arch is None:
            continue

        x = pred_fn(arch)  # type: ignore[operator]
        if x is None:
            continue

        prod_row = prod_by_date.get(prod_d)
        if prod_row is None:
            continue

        y = out_fn(prod_row)  # type: ignore[operator]
        if y is None:
            continue

        xs.append(float(x))
        ys.append(float(y))

    if len(xs) < MIN_PAIRS:
        return None

    r = _pearson_r(xs, ys)
    if r is None:
        return None

    n = len(xs)
    if abs(r) >= 1.0:
        p = 0.0
    else:
        t_stat = r * math.sqrt((n - 2) / (1 - r ** 2))
        p = _t_test_p(t_stat, n - 2)
    return (r, n, p)


def _build_summary_empty(
    start: date,
    end: date,
    sleep_bounds: CoverageBounds,
    aw_bounds: CoverageBounds,
) -> str:
    lines = [
        f"Sleep Architecture × Productivity Report: {start} → {end}",
        "",
        "No sleep∩AW coverage overlap in window — no correlations computed.",
        f"  {sleep_bounds.provenance()}",
        f"  {aw_bounds.provenance()}",
        "",
        "CAVEAT: absent days are never treated as zeros (missing ≠ zero).",
    ]
    return "\n".join(lines)


def _build_summary(
    *,
    start: date,
    end: date,
    covered_start: date,
    covered_end: date,
    n_sleep: int,
    n_aw: int,
    n_tests: int,
    correlations: list[ArchitecturePairCorrelation],
    sleep_prov: str,
    aw_prov: str,
) -> str:
    """Human-readable summary.

    Frames results as ASSOCIATIONS, not causation, with per-correlation n and
    the coverage window inline so an LLM copying this text cannot drop caveats.
    """
    lines = [
        f"Sleep Architecture × Productivity Report: {start} → {end}",
        f"  Analysis window (sleep∩AW coverage): {covered_start} → {covered_end}",
        f"  Sleep architecture nights: {n_sleep} | AW days with data: {n_aw}",
        f"  {sleep_prov}",
        f"  {aw_prov}",
        "",
    ]

    if correlations:
        significant = [c for c in correlations if c.significant]
        significant.sort(key=lambda c: -abs(c.r))
        if significant:
            lines.append(
                f"FDR-significant associations "
                f"(Benjamini-Hochberg q<{FDR_TARGET:g} across {n_tests} tests):"
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
                f"(q<{FDR_TARGET:g}) across {n_tests} tests."
            )

        exploratory = [c for c in correlations if not c.significant and abs(c.r) > 0.2]
        exploratory.sort(key=lambda c: -abs(c.r))
        if exploratory:
            lines.append("")
            lines.append(
                "Exploratory only (|r|>0.2 but NOT FDR-significant — "
                "do not report as findings):"
            )
            for c in exploratory[:10]:
                direction = "↑" if c.r > 0 else "↓"
                lines.append(
                    f"  r={c.r:+.3f} {direction}  {c.label} "
                    f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
                )
    else:
        lines.append(f"No correlations computed (n_tests={n_tests}).")

    lines.append("")
    lines.append(
        "CAVEAT: these are next-day ASSOCIATIONS between sleep architecture "
        "and productivity metrics, not causal effects. Common confounders "
        "(workday type, stress load, seasonal patterns, recovery state) can "
        "produce the same signal. Deep%/REM% predicting next-day deep-work "
        "minutes does not establish that sleep architecture drives productivity. "
        "Interpret only within the covered window above and with the reported "
        "per-correlation n; absent AW days are excluded, never counted as 0."
    )

    return "\n".join(lines)


__all__ = [
    "ArchitecturePairCorrelation",
    "SleepArchitectureProductivityReport",
    "analyze",
]
