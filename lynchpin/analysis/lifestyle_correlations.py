"""Lifestyle × productivity / physiology cross-correlation analysis.

Three high-signal cross-source correlations the operator matrix now supports but
no module computed, plus arbitrary (multi-year) window support:

  #2  music × focus     — does same-day / lagged Spotify listening track
                          deep-work minutes and git commit volume?
  #3  web-distraction    — does a day's social-browsing *ratio*
      × productivity      (web_social_visits / max(web_visits, 1)) track that
                          day's deep-work minutes and git commit volume?
  #5  HRV / stress       — does today's HRV (rmssd) or stress predict the NEXT
      × next-day code      day's git commit volume and code churn (lines added +
                          deleted)?
  #11 multi-year         — ``analyze`` accepts arbitrary windows; with
                          ``full_history=True`` the window is derived from the
                          intersection of the relevant sources' coverage bounds
                          (data spans 2013+ for music/web, 2017+ for health).

STATISTICAL INTEGRITY CONTRACT
------------------------------
This mirrors ``substance_health.py`` (the hardened gold-standard pattern), and
every reported association carries the machinery to avoid false LLM claims:

* **Multiple comparisons.** ``analyze`` evaluates many (pair × lag) correlations
  across three correlation families. Reporting raw ``p < 0.05`` over that family
  inflates false positives. Every ``LagCorrelation`` carries a raw two-tailed
  ``p_value``, a Benjamini-Hochberg FDR ``q_value`` computed across the *entire*
  test family (all pairs × all lags in one ``analyze`` call), and a
  ``significant`` flag derived from that q-value. The summary surfaces only
  FDR-significant associations, separating exploratory ``|r|`` from findings.

* **Missing ≠ zero.** ``spotify_hours``/``hrv_rmssd``/``stress_mean`` default to
  ``None`` on absent days, but ``web_*`` and ``git_*`` default to numeric ``0``,
  which would fabricate "no listening / no browsing / no commits" outside a
  source's coverage. Each (x, y) pair is gated by BOTH operands' coverage: a day
  contributes only when the day is inside the x-source's bounds AND the lagged
  day is inside the y-source's bounds, AND both source labels are in the
  corresponding ``OperatorDay.sources_present`` set. Absent days are excluded,
  never coerced to 0. The covered range per family is emitted as provenance.

* **Association, not causation.** The summary frames every result as a lagged
  *association* with the per-correlation ``n`` + covered-range caveat inline,
  not just in a docstring. Multi-year windows additionally inherit the
  analytics' even-sampling / autocorrelation contiguity caveats.

Method: lagged Pearson cross-correlation, FDR-corrected (Benjamini-Hochberg).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional

from ..core.analytics import _benjamini_hochberg, _pearson_r, _t_test_p
from ..core.coverage import CoverageBounds
from .operator_daily import OperatorDay, operator_daily_matrix

logger = logging.getLogger(__name__)

#: Minimum paired, in-coverage observations before a lag correlation is computed.
MIN_PAIRS = 10

#: FDR target for the Benjamini-Hochberg correction across the full test family.
FDR_TARGET = 0.05

#: |r| floor for surfacing a NON-significant correlation as "exploratory" noise.
EXPLORATORY_R = 0.2

#: coverage_bounds() key per OperatorDay source label that backs each signal.
#: ``git`` has no materialized bounds (live source: git_baseline is None/None),
#: so it is treated as always-in-coverage — matching operator_daily, which marks
#: git present for any observed commit day rather than clamping a live source.
_SPOTIFY_KEY = "spotify"
_WEB_KEY = "webhistory"
_AW_KEY = "activitywatch"
_HEALTH_KEY = "health"
_GIT_KEY: Optional[str] = None  # live source; not clamped.

# Presence labels passed alongside each accessor below are the exact strings
# operator_daily writes into ``OperatorDay.sources_present`` ("spotify", "web",
# "git", "activitywatch", "health") — a 0 with no presence label is "not
# observed", never a genuine zero (missing ≠ zero).


@dataclass(frozen=True)
class LagCorrelation:
    """One (predictor → outcome) correlation at a specific lag.

    ``p_value`` is the raw two-tailed t-test p-value at this lag. ``q_value`` is
    the Benjamini-Hochberg FDR-adjusted p-value across the *entire* family of
    correlations evaluated by a single ``analyze`` call (all pairs × all lags),
    and ``significant`` is ``q_value < FDR_TARGET``. ``n`` is the number of
    paired, in-coverage observations behind ``r``.
    """

    family: str  # "music_focus" | "web_productivity" | "hrv_stress_code"
    predictor: str  # e.g. "spotify_hours"
    outcome: str  # e.g. "aw_deep_work_min"
    lag_days: int  # 0 = same day; +1 = predictor day D → outcome day D+1, …
    r: float  # Pearson correlation coefficient
    n: int  # number of paired observations
    label: str  # human-readable, e.g. "spotify_hours → aw_deep_work_min (lag=0d)"
    p_value: float = 1.0  # raw two-tailed t-test p-value at this lag
    q_value: float = 1.0  # BH FDR-adjusted p across the full test family
    significant: bool = False  # q_value < FDR_TARGET


@dataclass(frozen=True)
class CorrelationFamily:
    """Provenance for one correlation family (a covered window + its sources)."""

    name: str  # "music_focus" | "web_productivity" | "hrv_stress_code"
    covered_start: Optional[date]
    covered_end: Optional[date]
    coverage_provenance: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class LifestyleCorrelationReport:
    """Full lifestyle × productivity / physiology correlation analysis.

    Frozen result members (``LagCorrelation``, ``CorrelationFamily``) are
    immutable; the report container itself is mutable so ``analyze`` can build it
    incrementally, matching ``SubstanceHealthReport``.
    """

    window_start: date
    window_end: date
    n_days: int
    full_history: bool = False

    # Per-family covered windows + coverage provenance (missing ≠ zero).
    families: list[CorrelationFamily] = field(default_factory=list)

    # Total number of correlations in the FDR test family (transparency).
    n_tests: int = 0

    # All lag correlations across all three families (each carries p/q/n).
    lag_correlations: list[LagCorrelation] = field(default_factory=list)

    # Summary text (association-not-causation; carries n + covered range inline).
    summary: str = ""


# Predictor / outcome accessors. Numeric defaults that are NOT None (web_*, git_*)
# rely on coverage + presence gating to avoid treating absent days as zero.
def _distraction_ratio(r: OperatorDay) -> Optional[float]:
    """Social-browsing share of a day's web visits.

    ``None`` when the day recorded no web visits at all (ratio undefined; the
    presence/coverage gate already excludes out-of-coverage days, so a real
    in-coverage zero-visit day carries no distraction signal worth correlating).
    """
    if r.web_visits <= 0:
        return None
    return r.web_social_visits / float(r.web_visits)


def _git_churn(r: OperatorDay) -> Optional[float]:
    """Code churn = lines added + lines deleted (git exposes both; no net field)."""
    return float(r.git_lines_added + r.git_lines_deleted)


def analyze(
    start: date,
    end: date,
    *,
    full_history: bool = False,
    max_lag: int = 3,
) -> LifestyleCorrelationReport:
    """Run the three lifestyle cross-correlations over [start, end].

    Args:
        start, end: inclusive analysis window. Arbitrary (multi-year) windows are
            supported (#11).
        full_history: when True, the window is widened to the intersection of the
            relevant sources' ``coverage_bounds()`` (data spans 2013+); the passed
            ``start``/``end`` then act only as outer clamps. See ``_full_window``.
        max_lag: maximum positive lag in days for the lead-lag scan (default 3).

    Returns:
        LifestyleCorrelationReport with FDR-corrected correlations (p/q/n per
        pair), per-family covered windows + provenance, and an
        association-not-causation summary carrying the caveats inline.
    """
    bounds = _load_bounds()

    if full_history:
        widened = _full_window(start, end, bounds)
        if widened is not None:
            start, end = widened

    rows = operator_daily_matrix(start, end)
    rows_by_date = {r.date: r for r in rows}

    report = LifestyleCorrelationReport(
        window_start=start,
        window_end=end,
        n_days=len(rows),
        full_history=full_history,
    )

    # ── Define the three families: predictor/outcome pairs + their coverage. ──
    # Each entry: (family, predictor_name, predictor_fn, predictor_keys,
    #              outcome_name, outcome_fn, outcome_keys, lags)
    music_focus = _build_family(
        "music_focus",
        rows_by_date,
        bounds,
        predictors=[("spotify_hours", lambda r: r.spotify_hours, (_SPOTIFY_KEY, "spotify"))],
        outcomes=[
            ("aw_deep_work_min", lambda r: r.aw_deep_work_min, (_AW_KEY, "activitywatch")),
            ("git_commits", lambda r: float(r.git_commits), (_GIT_KEY, "git")),
        ],
        lags=range(0, max_lag + 1),
        note=(
            "Spotify listening hours vs same-day/lagged focus. Spotify coverage "
            "ends 2025-12-18 (export); deep-work needs ActivityWatch presence."
        ),
    )
    web_productivity = _build_family(
        "web_productivity",
        rows_by_date,
        bounds,
        predictors=[("web_distraction_ratio", _distraction_ratio, (_WEB_KEY, "web"))],
        outcomes=[
            ("aw_deep_work_min", lambda r: r.aw_deep_work_min, (_AW_KEY, "activitywatch")),
            ("git_commits", lambda r: float(r.git_commits), (_GIT_KEY, "git")),
        ],
        lags=range(0, max_lag + 1),
        note=(
            "Distraction ratio = web_social_visits / max(web_visits, 1); only "
            "in-coverage days with web visits contribute (ratio else undefined)."
        ),
    )
    hrv_stress_code = _build_family(
        "hrv_stress_code",
        rows_by_date,
        bounds,
        predictors=[
            ("hrv_rmssd", lambda r: r.hrv_rmssd, (_HEALTH_KEY, "health")),
            ("stress_mean", lambda r: r.stress_mean, (_HEALTH_KEY, "health")),
        ],
        outcomes=[
            ("git_commits", lambda r: float(r.git_commits), (_GIT_KEY, "git")),
            ("git_churn", _git_churn, (_GIT_KEY, "git")),
        ],
        # Lag-1 is the headline (today's physiology → tomorrow's code); lag-0 is
        # kept as a same-day control. No churn-unavailable note needed: git
        # exposes lines_added/deleted, so churn is git_lines_added + deleted.
        lags=(0, 1),
        note=(
            "Today's HRV (rmssd) / stress vs NEXT-day git activity (lag=1 is the "
            "headline). Health (HRV/stress) coverage ends 2026-03-29 (export); "
            "git is a live source (no coverage clamp). Churn = lines added+deleted."
        ),
    )

    # ── One FDR pass over the ENTIRE family (all pairs × all lags). ──
    raw = music_focus.raw + web_productivity.raw + hrv_stress_code.raw
    report.families = [music_focus.family, web_productivity.family, hrv_stress_code.family]
    report.n_tests = len(raw)

    if raw:
        q_by_idx = _benjamini_hochberg({i: row.p for i, row in enumerate(raw)})
        for i, rc in enumerate(raw):
            q = q_by_idx[i]
            report.lag_correlations.append(
                LagCorrelation(
                    family=rc.family,
                    predictor=rc.predictor,
                    outcome=rc.outcome,
                    lag_days=rc.lag,
                    r=round(rc.r, 4),
                    n=rc.n,
                    label=f"{rc.predictor} → {rc.outcome} (lag={rc.lag}d)",
                    p_value=round(rc.p, 4),
                    q_value=round(q, 4),
                    significant=q < FDR_TARGET,
                )
            )

    report.summary = _build_summary(report)
    return report


# ══════════════════════════════════════════════════════════════════════════════
# Family construction
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _RawCorr:
    """Pre-FDR correlation: identity + r/n/p, before the family-wide BH pass."""

    family: str
    predictor: str
    outcome: str
    lag: int
    r: float
    n: int
    p: float


@dataclass
class _FamilyBuild:
    """A family's pre-FDR correlations plus its provenance record."""

    raw: list[_RawCorr]
    family: CorrelationFamily


_Accessor = Callable[[OperatorDay], Optional[float]]


def _build_family(
    name: str,
    rows_by_date: dict[date, OperatorDay],
    bounds: dict[str, CoverageBounds],
    *,
    predictors: list[tuple[str, _Accessor, tuple[Optional[str], str]]],
    outcomes: list[tuple[str, _Accessor, tuple[Optional[str], str]]],
    lags: Iterable[int],
    note: str,
) -> _FamilyBuild:
    """Build all (predictor × outcome × lag) correlations for one family.

    Each tuple is ``(name, accessor, (coverage_key, presence_label))``. The
    correlation for a pair only uses days where the predictor day is in the
    predictor source's coverage + presence AND the (lagged) outcome day is in the
    outcome source's coverage + presence. Absent days are excluded (missing ≠
    zero). The covered window reported for the family is the union of the
    predictor/outcome source bounds intersected with the available dates.
    """
    raw: list[_RawCorr] = []
    used_keys: set[Optional[str]] = set()
    for pred_name, pred_fn, (pred_key, pred_present) in predictors:
        used_keys.add(pred_key)
        pred_bounds = _bounds_for(pred_key, bounds)
        for out_name, out_fn, (out_key, out_present) in outcomes:
            used_keys.add(out_key)
            out_bounds = _bounds_for(out_key, bounds)
            for lag in lags:
                stat = _lag_correlation(
                    rows_by_date,
                    pred_fn,
                    out_fn,
                    lag,
                    pred_bounds,
                    out_bounds,
                    pred_present,
                    out_present,
                )
                if stat is not None:
                    r, n, p = stat
                    raw.append(_RawCorr(name, pred_name, out_name, lag, r, n, p))

    covered_start, covered_end = _family_window(rows_by_date, bounds, used_keys)
    provenance = _provenance_lines(bounds, used_keys)
    family = CorrelationFamily(
        name=name,
        covered_start=covered_start,
        covered_end=covered_end,
        coverage_provenance=provenance,
        note=note,
    )
    return _FamilyBuild(raw=raw, family=family)


def _lag_correlation(
    rows_by_date: dict[date, OperatorDay],
    pred_fn: _Accessor,
    out_fn: _Accessor,
    lag: int,
    pred_bounds: Optional[CoverageBounds],
    out_bounds: Optional[CoverageBounds],
    pred_present: str,
    out_present: str,
) -> Optional[tuple[float, int, float]]:
    """Pearson r + raw two-tailed p between predictor day D and outcome day D+lag.

    A day pair contributes only when:
      * the predictor day is in predictor coverage AND carries the predictor's
        presence label (so a numeric 0 is a genuine zero, not an absent day);
      * the outcome day (D+lag) is in outcome coverage AND carries the outcome's
        presence label;
      * both accessors return a non-None value.
    ``None`` coverage bounds (live git) skip the coverage gate but the presence
    gate still applies. Returns ``(r, n, p)`` or ``None`` when fewer than
    ``MIN_PAIRS`` valid pairs survive.
    """
    xs: list[float] = []
    ys: list[float] = []

    for d, pred_row in rows_by_date.items():
        out_day = d + timedelta(days=lag)
        out_row = rows_by_date.get(out_day)
        if out_row is None:
            continue
        if not _observed(pred_row, d, pred_bounds, pred_present):
            continue
        if not _observed(out_row, out_day, out_bounds, out_present):
            continue
        x = pred_fn(pred_row)
        y = out_fn(out_row)
        if x is None or y is None:
            continue
        xs.append(x)
        ys.append(y)

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


def _observed(
    row: OperatorDay,
    day: date,
    cov: Optional[CoverageBounds],
    presence_label: str,
) -> bool:
    """True when *day* is observed for the source: in coverage AND present.

    Coverage gate: if ``cov`` is known (has bounds), the day must be inside it;
    a live source with no bounds (git) skips this gate. Presence gate: the
    source label must be in ``row.sources_present`` so a numeric 0 from an
    absent/failed source is never read as a genuine zero (missing ≠ zero).
    """
    if cov is not None and (cov.first is not None or cov.last is not None):
        if not cov.covers(day):
            return False
    return presence_label in row.sources_present


# ══════════════════════════════════════════════════════════════════════════════
# Coverage helpers
# ══════════════════════════════════════════════════════════════════════════════


def _load_bounds() -> dict[str, CoverageBounds]:
    """Fetch coverage bounds once; degrade to empty mapping on failure."""
    try:
        from ..sources.source_observations import coverage_bounds

        return coverage_bounds()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "lifestyle_correlations: coverage_bounds() failed — "
            "cross-source analysis will operate without coverage clamping: %s",
            exc,
        )
        return {}


def _bounds_for(key: Optional[str], bounds: dict[str, CoverageBounds]) -> Optional[CoverageBounds]:
    """CoverageBounds for a coverage key, or ``None`` for live/unkeyed sources."""
    if key is None:
        return None
    return bounds.get(key)


def _full_window(
    start: date,
    end: date,
    bounds: dict[str, CoverageBounds],
) -> Optional[tuple[date, date]]:
    """Derive a multi-year window from the union of relevant source coverage.

    For ``full_history=True``, widen to the span across the sources this module
    correlates (spotify, webhistory, activitywatch, health) — git is live and
    carries no bounds, so it does not bound the window. The passed ``start``/
    ``end`` act as outer clamps so callers can still cap an otherwise-unbounded
    full-history scan. Returns ``None`` when no source has observable bounds.

    Trend / correlation over the resulting multi-year window inherits the
    analytics' contiguity caveats (even-sampling, autocorrelation); the summary
    records this.
    """
    keys = (_SPOTIFY_KEY, _WEB_KEY, _AW_KEY, _HEALTH_KEY)
    firsts: list[date] = []
    lasts: list[date] = []
    for k in keys:
        b = bounds.get(k)
        if b is None:
            continue
        if b.first is not None:
            firsts.append(b.first)
        if b.last is not None:
            lasts.append(b.last)
    if not firsts or not lasts:
        return None
    lo = max(start, min(firsts))
    hi = min(end, max(lasts))
    if lo > hi:
        return None
    return lo, hi


def _family_window(
    rows_by_date: dict[date, OperatorDay],
    bounds: dict[str, CoverageBounds],
    keys: set[Optional[str]],
) -> tuple[Optional[date], Optional[date]]:
    """Covered window for a family: INTERSECTION of its keyed source bounds ∩ dates.

    A family's correlations are only meaningful where every keyed source it
    touches is simultaneously in coverage, so this clamps to the intersection
    (``max(firsts) … min(lasts)``) rather than the union — reporting a window
    wider than where the data overlaps would overstate the covered range. Live
    sources (key ``None``) carry no bounds and do not constrain the window
    (per-day presence gating still applies). Returns ``(None, None)`` when no
    dates are available or the keyed bounds do not overlap.
    """
    if not rows_by_date:
        return (None, None)
    req_lo = min(rows_by_date)
    req_hi = max(rows_by_date)

    firsts: list[date] = []
    lasts: list[date] = []
    has_live = False
    for k in keys:
        if k is None:
            has_live = True
            continue
        b = bounds.get(k)
        if b is None or b.first is None or b.last is None:
            continue
        firsts.append(b.first)
        lasts.append(b.last)

    if not firsts or not lasts:
        # Only live / unbounded sources in play: window is the available range.
        return (req_lo, req_hi) if has_live else (None, None)

    lo = max(req_lo, max(firsts))
    hi = min(req_hi, min(lasts))
    if lo > hi:
        return (None, None)
    return (lo, hi)


def _provenance_lines(
    bounds: dict[str, CoverageBounds],
    keys: set[Optional[str]],
) -> list[str]:
    """Human-readable coverage provenance for each keyed source in a family."""
    lines: list[str] = []
    for k in sorted(key for key in keys if key is not None):
        b = bounds.get(k)
        if b is not None:
            lines.append(b.provenance())
    if None in keys:
        lines.append("git: live source (no coverage clamp; present-day gated)")
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════


def _build_summary(report: LifestyleCorrelationReport) -> str:
    """Human-readable summary; frames results as lagged ASSOCIATION not causation.

    Carries the per-family covered window + per-correlation n inline so an LLM
    copying this text cannot drop the caveats.
    """
    lines = [
        f"Lifestyle Correlation Report: {report.window_start} → {report.window_end}",
        f"  Days in window: {report.n_days}"
        + ("  (full_history: window widened to source coverage)" if report.full_history else ""),
        "",
    ]

    # Per-family covered windows + provenance (missing ≠ zero).
    for fam in report.families:
        if fam.covered_start is not None and fam.covered_end is not None:
            lines.append(
                f"[{fam.name}] covered {fam.covered_start} → {fam.covered_end}"
            )
        else:
            lines.append(
                f"[{fam.name}] no source-coverage overlap in window — "
                "no correlations (absent days are NOT treated as zero)."
            )
        lines.append(f"    {fam.note}")
        for prov in fam.coverage_provenance:
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
                    f"  r={c.r:+.3f} {direction}  [{c.family}] {c.label} "
                    f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
                )
        else:
            lines.append(
                f"No associations survive Benjamini-Hochberg FDR correction "
                f"(q<{FDR_TARGET:g}) across {report.n_tests} tests."
            )

        exploratory = [
            c
            for c in report.lag_correlations
            if not c.significant and abs(c.r) > EXPLORATORY_R
        ]
        exploratory.sort(key=lambda c: -abs(c.r))
        if exploratory:
            lines.append("")
            lines.append(
                f"Exploratory only (|r|>{EXPLORATORY_R:g} but NOT FDR-significant — "
                "likely noise, do not report as findings):"
            )
            for c in exploratory[:10]:
                direction = "↑" if c.r > 0 else "↓"
                lines.append(
                    f"  r={c.r:+.3f} {direction}  [{c.family}] {c.label} "
                    f"(n={c.n}, p={c.p_value:.4f}, q={c.q_value:.4f})"
                )

    lines.append("")
    lines.append(
        "CAVEAT: these are lagged ASSOCIATIONS, not causation. A surviving "
        "correlation does not establish that music/browsing/physiology drives "
        "focus or code activity — confounders, common trends, and "
        "autocorrelation can all produce it. Interpret only within each family's "
        "covered range above and with the reported per-correlation n; absent "
        "days are excluded, not counted as zero."
    )
    if report.full_history:
        lines.append(
            "  Multi-year window: trend/correlation over multi-year spans assumes "
            "the analytics' contiguity caveats (even daily sampling, "
            "autocorrelation inflates significance); gaps across years are not "
            "interpolated and only in-coverage days contribute."
        )

    return "\n".join(lines)


def genre_deep_work_correlation(
    start: date,
    end: date,
    *,
    min_listen_days: int = 8,
    genre_cache_path: Optional[Path] = None,
) -> dict[str, object]:
    """Same-day correlation between minutes-per-genre and deep-work hours (#14).

    Joins ``spotify.daily_genre_minutes`` (genres resolved via the Spotify catalog
    API) with ``OperatorDay.aw_deep_work_min`` over days where ActivityWatch is
    present (missing != zero). For each genre listened on at least
    ``min_listen_days`` covered days, computes Pearson r with a Benjamini-Hochberg
    FDR-corrected p-value across the genre family. Same-day ASSOCIATION, not
    causation. Requires SPOTIFY_CLIENT_ID/SECRET (raises SourceUnavailableError).
    """
    import math

    from ..sources.spotify import daily_genre_minutes

    genre_days = daily_genre_minutes(start, end, cache_path=genre_cache_path)
    rows = operator_daily_matrix(start, end)
    deep = {
        r.date: float(r.aw_deep_work_min)
        for r in rows
        if r.aw_deep_work_min is not None and "activitywatch" in r.sources_present
    }
    common = sorted(set(deep) & set(genre_days))
    all_genres = sorted({g for gd in genre_days.values() for g in gd})

    findings: list[dict[str, object]] = []
    pvals: dict[int, float] = {}
    for genre in all_genres:
        xs = [genre_days[d].get(genre, 0.0) for d in common]
        listen_days = sum(1 for x in xs if x > 0.0)
        if listen_days < min_listen_days or len(common) < 3:
            continue
        ys = [deep[d] for d in common]
        r = _pearson_r(xs, ys)
        if r is None or not math.isfinite(r):
            continue
        n = len(common)
        if abs(r) >= 0.99999:
            p = 0.0
        else:
            p = _t_test_p(r * math.sqrt((n - 2) / (1.0 - r * r)), n - 2)
        pvals[len(findings)] = p
        findings.append({"genre": genre, "listen_days": listen_days, "r": round(r, 4), "n": n})

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    for idx, finding in enumerate(findings):
        finding["p_value"] = round(pvals[idx], 4)
        finding["q_value"] = round(qmap[idx], 4)
        finding["significant"] = qmap[idx] < 0.05
    findings.sort(key=lambda f: abs(float(f["r"])), reverse=True)  # type: ignore[arg-type]

    return {
        "covered_days": len(common),
        "genres_tested": len(findings),
        "findings": findings,
        "caveats": [
            "Same-day association, NOT causation.",
            "Days without ActivityWatch coverage excluded (missing != zero).",
            "p-values FDR-corrected across the genre family.",
            f"Only genres listened on >= {min_listen_days} covered days included.",
        ],
    }


def audio_feature_deep_work_correlation(
    start: date,
    end: date,
    *,
    min_days: int = 10,
    dataset_path: Optional[Path] = None,
) -> dict[str, object]:
    """Correlate each daily audio-feature mean (energy/valence/tempo…) with deep-work.

    Joins ``audio_features.daily_audio_features`` with ``OperatorDay.aw_deep_work_min``
    over days where ActivityWatch is present AND at least one stream matched the
    (frozen, public) audio-features dataset — missing != zero. Pearson r per feature,
    FDR-corrected across the feature family. Same-day association, not causation. This
    resurrects the energy/mood analysis Spotify's deprecated Audio Features endpoint
    would have enabled. Raises SourceUnavailableError if the dataset is absent.
    """
    import math

    from ..sources.audio_features import NUMERIC_FEATURES, daily_audio_features

    feature_days = {d.date: d.means for d in daily_audio_features(start, end, path=dataset_path)}
    rows = operator_daily_matrix(start, end)
    deep = {
        r.date: float(r.aw_deep_work_min)
        for r in rows
        if r.aw_deep_work_min is not None and "activitywatch" in r.sources_present
    }
    common = sorted(set(deep) & set(feature_days))

    findings: list[dict[str, object]] = []
    pvals: dict[int, float] = {}
    if len(common) >= min_days:
        ys = [deep[d] for d in common]
        for feature in NUMERIC_FEATURES:
            xs = [feature_days[d][feature] for d in common]
            r = _pearson_r(xs, ys)
            if r is None or not math.isfinite(r):
                continue
            n = len(common)
            p = 0.0 if abs(r) >= 0.99999 else _t_test_p(r * math.sqrt((n - 2) / (1.0 - r * r)), n - 2)
            pvals[len(findings)] = p
            findings.append({"feature": feature, "r": round(r, 4), "n": n})

    qmap = _benjamini_hochberg(pvals) if pvals else {}
    for idx, finding in enumerate(findings):
        finding["p_value"] = round(pvals[idx], 4)
        finding["q_value"] = round(qmap[idx], 4)
        finding["significant"] = qmap[idx] < 0.05
    findings.sort(key=lambda f: abs(float(f["r"])), reverse=True)  # type: ignore[arg-type]

    return {
        "covered_days": len(common),
        "findings": findings,
        "caveats": [
            "Same-day association, NOT causation.",
            "Days require ActivityWatch presence AND >=1 dataset-matched stream "
            "(missing != zero); unmatched tracks contribute nothing.",
            "Audio features from a frozen public dataset (Spotify's live endpoint "
            "is deprecated); coverage is partial — obscure tracks are absent.",
            "p-values FDR-corrected across the feature family.",
        ],
    }


__all__ = [
    "CorrelationFamily",
    "LagCorrelation",
    "LifestyleCorrelationReport",
    "analyze",
    "genre_deep_work_correlation",
    "audio_feature_deep_work_correlation",
]
