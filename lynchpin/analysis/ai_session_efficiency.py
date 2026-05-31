"""AI session efficiency meta-analysis.

Aggregates polylogue SessionProfile metadata temporally to surface how the
operator's working style WITH AI tools evolves over a date range.

WHAT IS MEASURED
----------------
* **Engagement efficiency** — ``engaged_duration_ms / wall_duration_ms``.
  Polylogue's inference layer computes ``engaged_duration_ms`` by summing
  heuristically-detected active-work segments within a session.
  ``wall_duration_ms`` is the clock span from first to last message.  A ratio
  close to 1.0 means nearly all wall time was active; 0.2–0.4 is typical for
  sessions with large thinking pauses or overnight continuation.

  CAVEAT: ``engaged_duration_ms`` is an inference product that falls back to
  session-total estimates for some sessions (flagged as "degraded" in the
  Polylogue readiness report).  For providers without per-message timestamps
  (e.g. legacy ChatGPT exports), ``wall_duration_ms`` may also be 0 or near-0.
  Efficiency is computed only on sessions where both values are positive and
  ``wall_duration_ms > 0``. Sessions without valid ms fields are counted in
  the denominator for the report but excluded from efficiency statistics.

* **Abandonment rate** — fraction of sessions whose ``terminal_state``
  indicates a non-productive ending.  Terminal states treated as abandoned:
  ``"abandoned"``, ``"stuck"``, ``"error_exit"``.  ``None``, ``"completed"``,
  and ``"partial"`` are NOT counted as abandonment.  Sessions with unknown
  ``terminal_state`` are placed in a separate "unknown" bucket, not in either
  numerator or denominator for the abandonment fraction.

* **AI-reliance trend** — daily session count and engaged-minutes tracked as
  a time series with a Mann-Kendall verdict (rising / falling / stable).

* **Workflow-shape distribution** — how sessions cluster by shape (e.g.
  "implement_verify_loop", "research_exploration", ...) and whether early vs
  late halves of the window differ.

* **Tool-use intensity** — ``tool_use_count`` per session over time.

ISOLATION CONTRACT
------------------
The module patches out the polylogue accessor for tests; see the ``_profiler``
module-level override hook used by the test suite.

"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterator, Optional

from ..core.analytics import TrendResult, detect_trend
from ..sources.polylogue_models import SessionProfile


# ── Abandonment classification ──────────────────────────────────────────────
# Update this set if Polylogue adds new terminal_state values that indicate
# non-productive session endings.  "partial" is intentionally excluded — a
# partial completion represents real work done, not a stuck session.

ABANDONED_STATES: frozenset[str] = frozenset({"abandoned", "stuck", "error_exit"})

#: Minimum sessions per day before the daily efficiency mean is included
#: in the trend input (single-session days are high-noise outliers).
MIN_DAY_SESSIONS_FOR_TREND = 1

#: Mann-Kendall requires at least this many data-bearing days.
MIN_TREND_DAYS = 7


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EfficiencyDay:
    """Per-day efficiency snapshot.

    Days with no AI sessions carry ``session_count=0`` and ``None`` for every
    computed metric — they are NOT treated as 0% efficiency or 0% abandonment.
    Callers must guard on ``session_count > 0`` before interpreting ratios.
    """

    date: date
    session_count: int
    # Efficiency (engaged/wall)
    efficiency_numerator_ms: int  # sum of engaged_ms for sessions with valid wall
    efficiency_denominator_ms: int  # sum of wall_ms for sessions with valid wall
    efficiency_eligible_n: int  # sessions contributing to efficiency
    efficiency_mean: Optional[float]  # mean per-session ratio; None if no eligible sessions
    # Abandonment (numerator / denominator; excludes sessions with unknown terminal_state)
    abandoned_n: int
    terminal_known_n: int  # sessions with a non-None terminal_state
    abandonment_rate: Optional[float]  # abandoned_n / terminal_known_n; None if no known states
    # Engagement volume
    engaged_minutes: float  # sum of engaged_ms / 60_000 across all sessions for the day
    tool_use_total: int  # sum of tool_use_count across all sessions
    tool_use_per_session: Optional[float]  # None if session_count == 0
    # Provider breakdown
    providers: dict[str, int]  # provider → session count

    @property
    def has_data(self) -> bool:
        """True when at least one session falls on this date."""
        return self.session_count > 0


@dataclass(frozen=True)
class WorkflowShapeWindow:
    """Workflow-shape distribution for a sub-window (e.g. early vs late half).

    ``distribution`` maps shape label → session count.  ``unknown_n`` are
    sessions with ``workflow_shape=None`` (no inference available).
    """

    label: str  # "early_half" | "late_half" | "full_window"
    start: date
    end: date
    session_count: int
    distribution: dict[str, int]  # shape → count
    unknown_n: int  # sessions with no workflow_shape

    @property
    def top_shape(self) -> Optional[str]:
        """Most common known shape, or None if no labelled sessions."""
        if not self.distribution:
            return None
        return max(self.distribution, key=lambda s: self.distribution[s])


@dataclass(frozen=True)
class ProviderEfficiencyRow:
    """Efficiency metrics broken out per provider over the full window.

    Efficiency is computed only on sessions with valid wall_duration_ms > 0
    and engaged_duration_ms >= 0. The covered subset may be a strict fraction
    of all sessions for that provider (e.g. legacy providers with missing ms).
    """

    provider: str
    session_count: int  # all sessions for this provider
    efficiency_eligible_n: int  # sessions with valid wall_ms
    efficiency_mean: Optional[float]  # mean engaged/wall ratio; None if 0 eligible
    abandonment_numerator: int  # sessions with abandoned terminal_state
    abandonment_denominator: int  # sessions with any known terminal_state
    abandonment_rate: Optional[float]  # None if denominator == 0
    engaged_hours: float  # total engaged_ms / 3_600_000
    tool_use_mean: Optional[float]  # mean tool_use_count; None if no sessions


@dataclass
class AiSessionEfficiencyReport:
    """Full AI session efficiency analysis over a date range.

    All per-day rows are present even for days with no AI sessions (they carry
    ``has_data=False``), so callers can distinguish "no sessions" from
    "missing data".  Trend verdicts are built from data-bearing days only.
    """

    window_start: date
    window_end: date
    n_calendar_days: int
    n_days_with_sessions: int

    # Aggregate session counts
    total_sessions: int
    efficiency_eligible_sessions: int  # sessions with valid wall_ms > 0
    efficiency_overall_mean: Optional[float]  # mean engaged/wall across eligible sessions

    # Abandonment (window-total)
    abandoned_n: int  # sessions with an abandoned terminal_state
    terminal_known_n: int  # sessions with any non-None terminal_state
    abandonment_rate: Optional[float]  # None if terminal_known_n == 0

    # Per-day series (includes no-data days as zero-session rows)
    daily: list[EfficiencyDay] = field(default_factory=list)

    # Workflow-shape windows
    shape_full: Optional[WorkflowShapeWindow] = None
    shape_early: Optional[WorkflowShapeWindow] = None
    shape_late: Optional[WorkflowShapeWindow] = None

    # Per-provider summary
    by_provider: list[ProviderEfficiencyRow] = field(default_factory=list)

    # Mann-Kendall trends (built from data-bearing days only)
    trend_sessions_per_day: Optional[TrendResult] = None
    trend_engaged_minutes_per_day: Optional[TrendResult] = None
    trend_tool_use_per_session: Optional[TrendResult] = None

    # Human-readable summary
    summary: str = ""


# ── Internal hook ────────────────────────────────────────────────────────────

#: Override this in tests to inject a custom profile iterator.
_profile_source: Optional[Callable[[], Iterator[SessionProfile]]] = None


def _iter_profiles() -> Iterator[SessionProfile]:
    if _profile_source is not None:
        yield from _profile_source()
    else:
        from ..sources.polylogue import iter_session_profiles

        yield from iter_session_profiles()


# ── Core computation ─────────────────────────────────────────────────────────


def analyze(*, start: date, end: date) -> AiSessionEfficiencyReport:
    """Run the full AI session efficiency analysis.

    Args:
        start: inclusive start date.
        end: inclusive end date.

    Returns:
        ``AiSessionEfficiencyReport`` with per-day series, per-provider rows,
        workflow-shape windows (full/early/late), and Mann-Kendall trend
        verdicts.  Days with no AI sessions are present in the daily series
        with ``has_data=False`` — callers must not treat them as zero efficiency.

    Caveats (inline in ``summary``):
        - engaged/wall ms may be absent for older providers (pre-2024 ChatGPT
          exports); efficiency is computed on the covered subset only.
        - terminal_state is an inference product; sessions without inference
          (``terminal_state=None``) are placed in the "unknown" bucket and
          excluded from the abandonment fraction.
        - Tool-use counts come from evidence-layer message parsing; zero may
          mean a genuinely tool-free session or a provider whose tool blocks
          are not parsed.
    """
    # ── Load profiles in the date range ──────────────────────────────────────
    in_window: list[SessionProfile] = []
    for p in _iter_profiles():
        d = _profile_date(p)
        if d is None:
            continue
        if d < start or d > end:
            continue
        in_window.append(p)

    # ── Build per-day buckets ─────────────────────────────────────────────────
    by_date: dict[date, list[SessionProfile]] = defaultdict(list)
    for p in in_window:
        d = _profile_date(p)
        assert d is not None  # already filtered above
        by_date[d].append(p)

    daily: list[EfficiencyDay] = []
    cursor = start
    while cursor <= end:
        profiles = by_date.get(cursor, [])
        daily.append(_build_day(cursor, profiles))
        cursor += timedelta(days=1)

    # ── Aggregate totals ─────────────────────────────────────────────────────
    total_sessions = len(in_window)
    n_days_with_sessions = sum(1 for d in daily if d.has_data)

    # Efficiency: collect per-session ratios for eligible sessions
    eff_ratios: list[float] = []
    eff_eligible = 0
    for p in in_window:
        r = _session_efficiency(p)
        if r is not None:
            eff_ratios.append(r)
            eff_eligible += 1

    eff_overall: Optional[float] = (
        statistics.mean(eff_ratios) if eff_ratios else None
    )

    # Abandonment totals
    aband_n = sum(1 for p in in_window if _is_abandoned(p))
    known_n = sum(1 for p in in_window if p.terminal_state is not None)
    aband_rate: Optional[float] = (
        aband_n / known_n if known_n > 0 else None
    )

    # ── Workflow-shape windows ───────────────────────────────────────────────
    shape_full = _shape_window(in_window, start, end, "full_window")
    mid = start + timedelta(days=(end - start).days // 2)
    shape_early = _shape_window(in_window, start, mid, "early_half")
    shape_late = _shape_window(
        in_window, mid + timedelta(days=1), end, "late_half"
    )

    # ── Per-provider rows ────────────────────────────────────────────────────
    by_provider: dict[str, list[SessionProfile]] = defaultdict(list)
    for p in in_window:
        by_provider[p.provider].append(p)

    provider_rows = [
        _build_provider_row(prov, profiles)
        for prov, profiles in sorted(by_provider.items())
    ]

    # ── Mann-Kendall trends (data-bearing days only) ─────────────────────────
    active_days = [d for d in daily if d.has_data]
    trend_sessions = _trend_from_days(
        active_days, lambda d: float(d.session_count)
    )
    trend_engaged = _trend_from_days(
        active_days, lambda d: d.engaged_minutes
    )
    # Tool-use per session: only days with sessions and valid count
    tool_days = [d for d in active_days if d.tool_use_per_session is not None]
    trend_tools = _trend_from_days(
        tool_days,
        lambda d: d.tool_use_per_session,  # type: ignore[arg-type]
    )

    report = AiSessionEfficiencyReport(
        window_start=start,
        window_end=end,
        n_calendar_days=(end - start).days + 1,
        n_days_with_sessions=n_days_with_sessions,
        total_sessions=total_sessions,
        efficiency_eligible_sessions=eff_eligible,
        efficiency_overall_mean=round(eff_overall, 4) if eff_overall is not None else None,
        abandoned_n=aband_n,
        terminal_known_n=known_n,
        abandonment_rate=round(aband_rate, 4) if aband_rate is not None else None,
        daily=daily,
        shape_full=shape_full,
        shape_early=shape_early,
        shape_late=shape_late,
        by_provider=provider_rows,
        trend_sessions_per_day=trend_sessions,
        trend_engaged_minutes_per_day=trend_engaged,
        trend_tool_use_per_session=trend_tools,
    )
    report.summary = _build_summary(report)
    return report


# ── Helpers ──────────────────────────────────────────────────────────────────


def _profile_date(p: SessionProfile) -> Optional[date]:
    """Canonical date for a session: prefer canonical_session_date, then last, then first."""
    if p.canonical_session_date is not None:
        return p.canonical_session_date
    if p.last_message_at is not None:
        return p.last_message_at.date()
    if p.first_message_at is not None:
        return p.first_message_at.date()
    return None


def _session_efficiency(p: SessionProfile) -> Optional[float]:
    """Engaged/wall ratio for a single session.

    Returns ``None`` when either value is absent or wall_duration_ms == 0.
    A zero-wall session can arise when first == last message timestamp;
    including it would produce a meaningless +inf or 0/0 ratio.
    """
    if p.wall_duration_ms <= 0:
        return None
    return p.engaged_duration_ms / p.wall_duration_ms


def _is_abandoned(p: SessionProfile) -> bool:
    """True when the session's terminal_state indicates an unproductive ending."""
    return (p.terminal_state or "").lower() in ABANDONED_STATES


def _build_day(d: date, profiles: list[SessionProfile]) -> EfficiencyDay:
    """Build a single EfficiencyDay from the profiles on that date."""
    n = len(profiles)
    if n == 0:
        return EfficiencyDay(
            date=d,
            session_count=0,
            efficiency_numerator_ms=0,
            efficiency_denominator_ms=0,
            efficiency_eligible_n=0,
            efficiency_mean=None,
            abandoned_n=0,
            terminal_known_n=0,
            abandonment_rate=None,
            engaged_minutes=0.0,
            tool_use_total=0,
            tool_use_per_session=None,
            providers={},
        )

    eff_ratios: list[float] = []
    eff_num = 0
    eff_den = 0
    aband = 0
    known = 0
    engaged_ms_total = 0
    tool_total = 0
    provider_counts: Counter[str] = Counter()

    for p in profiles:
        provider_counts[p.provider] += 1
        engaged_ms_total += p.engaged_duration_ms
        tool_total += p.tool_use_count
        r = _session_efficiency(p)
        if r is not None:
            eff_ratios.append(r)
            eff_num += p.engaged_duration_ms
            eff_den += p.wall_duration_ms
        if p.terminal_state is not None:
            known += 1
            if _is_abandoned(p):
                aband += 1

    eff_mean: Optional[float] = (
        statistics.mean(eff_ratios) if eff_ratios else None
    )
    aband_rate: Optional[float] = (
        round(aband / known, 4) if known > 0 else None
    )

    return EfficiencyDay(
        date=d,
        session_count=n,
        efficiency_numerator_ms=eff_num,
        efficiency_denominator_ms=eff_den,
        efficiency_eligible_n=len(eff_ratios),
        efficiency_mean=round(eff_mean, 4) if eff_mean is not None else None,
        abandoned_n=aband,
        terminal_known_n=known,
        abandonment_rate=aband_rate,
        engaged_minutes=round(engaged_ms_total / 60_000, 2),
        tool_use_total=tool_total,
        tool_use_per_session=round(tool_total / n, 2),
        providers=dict(provider_counts),
    )


def _shape_window(
    profiles: list[SessionProfile],
    start: date,
    end: date,
    label: str,
) -> WorkflowShapeWindow:
    """Build a WorkflowShapeWindow for the given sub-window."""
    subset = [p for p in profiles if _in_range(p, start, end)]
    dist: Counter[str] = Counter()
    unknown = 0
    for p in subset:
        if p.workflow_shape:
            dist[p.workflow_shape] += 1
        else:
            unknown += 1
    return WorkflowShapeWindow(
        label=label,
        start=start,
        end=end,
        session_count=len(subset),
        distribution=dict(dist),
        unknown_n=unknown,
    )


def _in_range(p: SessionProfile, start: date, end: date) -> bool:
    d = _profile_date(p)
    return d is not None and start <= d <= end


def _build_provider_row(
    provider: str, profiles: list[SessionProfile]
) -> ProviderEfficiencyRow:
    n = len(profiles)
    ratios: list[float] = []
    aband = 0
    known = 0
    engaged_ms = 0
    tool_counts: list[float] = []

    for p in profiles:
        r = _session_efficiency(p)
        if r is not None:
            ratios.append(r)
        if p.terminal_state is not None:
            known += 1
            if _is_abandoned(p):
                aband += 1
        engaged_ms += p.engaged_duration_ms
        tool_counts.append(float(p.tool_use_count))

    return ProviderEfficiencyRow(
        provider=provider,
        session_count=n,
        efficiency_eligible_n=len(ratios),
        efficiency_mean=round(statistics.mean(ratios), 4) if ratios else None,
        abandonment_numerator=aband,
        abandonment_denominator=known,
        abandonment_rate=round(aband / known, 4) if known > 0 else None,
        engaged_hours=round(engaged_ms / 3_600_000, 3),
        tool_use_mean=round(statistics.mean(tool_counts), 2) if tool_counts else None,
    )


def _trend_from_days(
    days: list[EfficiencyDay],
    value_fn: Callable[[EfficiencyDay], float],
) -> Optional[TrendResult]:
    """Run Mann-Kendall on the supplied day series.

    Returns ``None`` when fewer than ``MIN_TREND_DAYS`` data points are
    available (the normal approximation is unreliable below ~7-10 points).
    """
    if len(days) < MIN_TREND_DAYS:
        return None
    values = [value_fn(d) for d in days]
    return detect_trend(values, min_samples=MIN_TREND_DAYS)


def _build_summary(report: AiSessionEfficiencyReport) -> str:
    """Human-readable summary carrying caveats inline.

    Frames metrics as measurements with coverage notes so that an LLM copying
    this text cannot silently drop the caveats.
    """
    lines = [
        f"AI Session Efficiency Report: {report.window_start} → {report.window_end}",
        f"  {report.n_calendar_days} calendar days | "
        f"{report.n_days_with_sessions} days with AI sessions | "
        f"{report.total_sessions} total sessions",
        "",
    ]

    # ── Efficiency ────────────────────────────────────────────────────────────
    if report.efficiency_overall_mean is not None:
        lines.append(
            f"Engagement efficiency (engaged/wall ms): "
            f"mean={report.efficiency_overall_mean:.3f} "
            f"({report.efficiency_eligible_sessions}/{report.total_sessions} "
            f"sessions had valid wall_ms)."
        )
    else:
        lines.append(
            "Engagement efficiency: no eligible sessions "
            "(all sessions have wall_duration_ms=0 or missing)."
        )
    lines.append(
        "  CAVEAT: engaged_ms is an inference product and may be underestimated "
        "for sessions Polylogue marks as 'degraded'. "
        "wall_ms=0 arises for single-message sessions or providers without "
        "per-message timestamps; those sessions are excluded from the ratio."
    )
    lines.append("")

    # ── Abandonment ───────────────────────────────────────────────────────────
    if report.abandonment_rate is not None:
        lines.append(
            f"Abandonment: {report.abandoned_n}/{report.terminal_known_n} sessions "
            f"with known terminal_state ({report.abandonment_rate:.1%}). "
            f"Abandoned states: {sorted(ABANDONED_STATES)}. "
            f"{report.total_sessions - report.terminal_known_n} sessions have "
            f"terminal_state=None and are excluded from this fraction."
        )
    else:
        lines.append(
            "Abandonment: no sessions have a known terminal_state; "
            "cannot compute abandonment rate."
        )
    lines.append("")

    # ── Trends ────────────────────────────────────────────────────────────────
    for label, tr in [
        ("Sessions/day", report.trend_sessions_per_day),
        ("Engaged-minutes/day", report.trend_engaged_minutes_per_day),
        ("Tool-use/session", report.trend_tool_use_per_session),
    ]:
        if tr is not None:
            sig = "significant" if tr.significant else "not significant"
            lines.append(
                f"{label} trend: {tr.direction} "
                f"(slope={tr.slope:+.4f}, p={tr.p_value:.4f}, {sig}, n={tr.n} days)."
            )
        else:
            lines.append(
                f"{label} trend: insufficient data "
                f"(need ≥{MIN_TREND_DAYS} data-bearing days)."
            )
    lines.append(
        "  CAVEAT: Mann-Kendall assumes IID, evenly-spaced samples; "
        "trend is built from data-bearing days only (days without sessions are excluded), "
        "so the per-sample slope is NOT per-calendar-day."
    )
    lines.append("")

    # ── Workflow-shape shift ──────────────────────────────────────────────────
    if report.shape_early and report.shape_late:
        top_e = report.shape_early.top_shape or "unknown"
        top_l = report.shape_late.top_shape or "unknown"
        shift = top_e != top_l
        lines.append(
            f"Workflow-shape shift: early top='{top_e}' "
            f"→ late top='{top_l}' "
            f"({'changed' if shift else 'stable'})."
        )
        if report.shape_early.unknown_n or report.shape_late.unknown_n:
            total_unknown = (
                (report.shape_full.unknown_n if report.shape_full else 0)
            )
            lines.append(
                f"  {total_unknown} session(s) across the full window have no "
                f"workflow_shape (inference not available)."
            )
    lines.append("")

    # ── Per-provider ──────────────────────────────────────────────────────────
    if report.by_provider:
        lines.append("Per-provider summary:")
        for row in report.by_provider:
            eff_str = (
                f"{row.efficiency_mean:.3f}"
                if row.efficiency_mean is not None
                else "N/A"
            )
            aband_str = (
                f"{row.abandonment_rate:.1%}"
                if row.abandonment_rate is not None
                else "N/A"
            )
            lines.append(
                f"  {row.provider}: {row.session_count} sessions, "
                f"efficiency={eff_str} (n={row.efficiency_eligible_n}), "
                f"abandonment={aband_str} ({row.abandonment_numerator}/{row.abandonment_denominator}), "
                f"engaged={row.engaged_hours:.1f}h."
            )

    return "\n".join(lines)


__all__ = [
    "ABANDONED_STATES",
    "AiSessionEfficiencyReport",
    "EfficiencyDay",
    "ProviderEfficiencyRow",
    "WorkflowShapeWindow",
    "analyze",
]
