"""Cross-source intelligence: apply statistical analytics across data sources.

This module answers questions like:
- What are my weekly work rhythms?
- What correlates with productive days?
- When did my work patterns fundamentally change?
- What type of day was today?

Usage: call build_day_features() once, then pass to any analytics function.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta
from typing import Sequence

from ..core.analytics import (
    TrendResult, ChangePoint, CorrelationResult, PeriodicComponent, DayCluster, AnomalyResult,
    detect_trend, detect_changepoints, detect_periodicity,
    cross_correlate, cluster_days, anomaly_score,
)
from ..core.parse import iter_dates
from ..core.primitives import date_to_dt_range

__all__ = [
    "DayFeatures",
    "WeeklyRhythm",
    "build_day_features",
    "weekly_rhythm",
    "productivity_drivers",
    "work_regime_changes",
    "day_type_clusters",
    "activity_trends",
    "day_anomalies",
    "full_analysis",
]

# ══════════════════════════════════════════════════════════════════════════════
# Day feature vector: the foundation for all cross-source analytics
# ══════════════════════════════════════════════════════════════════════════════

# Numeric field names for generic iteration (excludes date, dominant_mode, dominant_project)
_NUMERIC_FIELDS: tuple[str, ...] = ()  # populated after DayFeatures is defined


@dataclass(frozen=True)
class DayFeatures:
    date: date
    active_hours: float
    deep_work_min: float
    sustained_focus_min: float
    fragmentation: float
    commit_count: int
    command_count: int
    project_count: int
    chat_sessions: int
    sleep_hours: float
    sleep_score: float
    listening_hours: float
    reddit_comments: int
    daily_steps: int
    vitality_score: float
    dominant_mode: str
    dominant_project: str

    def as_numeric_dict(self) -> dict[str, float]:
        """All numeric fields as {name: float} — ready for analytics functions."""
        return {name: float(getattr(self, name)) for name in _NUMERIC_FIELDS}


_NUMERIC_FIELDS = tuple(
    f.name for f in fields(DayFeatures)
    if f.name not in ("date", "dominant_mode", "dominant_project")
)


def _extract(features: Sequence[DayFeatures], field: str) -> list[float]:
    """Extract one numeric field as a float series."""
    return [float(getattr(f, field)) for f in features]


def build_day_features(start: date, end: date) -> list[DayFeatures]:
    """Assemble per-day feature vectors from all available sources."""
    from .activitywatch import active_seconds_by_date, deep_work, sustained_focus, fragmentation, attention, app_sessions
    from .git import daily_activity
    from .terminal import shell_sessions
    from .polylogue import daily_activity as chat_daily
    from .sleep import entries as sleep_entries
    from .spotify import daily_listening
    from .reddit import daily_activity as reddit_daily
    from .health import daily_steps, daily_vitality

    s_dt, e_dt = date_to_dt_range(start, end)

    aw_active = active_seconds_by_date(start, end)
    dw_blocks = deep_work(start=s_dt, end=e_dt)
    dw_by_day: dict[date, float] = {}
    for b in dw_blocks:
        dw_by_day[b.start.date()] = dw_by_day.get(b.start.date(), 0) + b.duration_min

    sf_blocks = sustained_focus(start=s_dt, end=e_dt)
    sf_by_day: dict[date, float] = {}
    for b in sf_blocks:
        sf_by_day[b.start.date()] = sf_by_day.get(b.start.date(), 0) + b.duration_min

    frag_by_day = {f.date: f.fragmentation for f in fragmentation(start=start, end=end)}
    att_by_day = {a.date: a.project_count for a in attention(start=start, end=end)}

    git_by_day: dict[date, int] = {}
    for g in daily_activity(start=start, end=end):
        git_by_day[g.date] = git_by_day.get(g.date, 0) + g.commit_count

    shell_by_day: dict[date, int] = {}
    for s in shell_sessions(start=s_dt, end=e_dt):
        shell_by_day[s.start.date()] = shell_by_day.get(s.start.date(), 0) + s.command_count

    chat_by_day: dict[date, int] = {}
    for c in chat_daily(start=start, end=end):
        chat_by_day[c.date] = chat_by_day.get(c.date, 0) + c.session_count

    sleep_data = {e.date: e for e in sleep_entries()}

    try:
        spotify_by_day = {s.date: s.hours for s in daily_listening(start=start, end=end)}
    except Exception:
        spotify_by_day = {}

    try:
        reddit_by_day = {r.date: r.comment_count for r in reddit_daily(start=start, end=end)}
    except Exception:
        reddit_by_day = {}

    # Health: steps and vitality
    try:
        steps_data = daily_steps(start=start, end=end)
        steps_by_day = {s.date: s.steps for s in steps_data}
    except Exception:
        steps_by_day = {}

    try:
        vitality_data = daily_vitality(start=start, end=end)
        vitality_by_day = {v.date: v.activity_score or 0 for v in vitality_data}
    except Exception:
        vitality_by_day = {}

    sessions = app_sessions(start=s_dt, end=e_dt)
    mode_by_day: dict[date, str] = {}
    proj_by_day: dict[date, str] = {}
    for sess in sessions:
        d = sess.start.date()
        if d not in mode_by_day and sess.mode:
            mode_by_day[d] = sess.mode
        if d not in proj_by_day and sess.project:
            proj_by_day[d] = sess.project

    result: list[DayFeatures] = []
    for d in iter_dates(start, end):
        sleep_entry = sleep_data.get(d)
        result.append(DayFeatures(
            date=d,
            active_hours=round(aw_active.get(d, 0) / 3600, 2),
            deep_work_min=round(dw_by_day.get(d, 0), 1),
            sustained_focus_min=round(sf_by_day.get(d, 0), 1),
            fragmentation=round(frag_by_day.get(d, 0), 3),
            commit_count=git_by_day.get(d, 0),
            command_count=shell_by_day.get(d, 0),
            project_count=att_by_day.get(d, 0),
            chat_sessions=chat_by_day.get(d, 0),
            sleep_hours=round(sleep_entry.total_minutes / 60, 2) if sleep_entry else 0,
            sleep_score=round(sleep_entry.avg_score or 0, 1) if sleep_entry else 0,
            listening_hours=round(spotify_by_day.get(d, 0), 2),
            reddit_comments=reddit_by_day.get(d, 0),
            daily_steps=steps_by_day.get(d, 0),
            vitality_score=vitality_by_day.get(d, 0),
            dominant_mode=mode_by_day.get(d, "unknown"),
            dominant_project=proj_by_day.get(d, ""),
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Cross-source analytics — all accept pre-built features
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class WeeklyRhythm:
    periodicity: list[PeriodicComponent]
    weekday_means: dict[str, float]  # Mon-Sun → avg active hours
    best_day: str
    worst_day: str
    consistency: float  # 0=erratic, 1=perfectly regular


def weekly_rhythm(features: Sequence[DayFeatures]) -> WeeklyRhythm:
    """Detect weekly activity patterns using periodicity + day-of-week analysis."""
    if not features:
        return WeeklyRhythm([], {}, "", "", 0)

    active = _extract(features, "active_hours")
    periodicity = detect_periodicity(active, min_period=5, max_period=14)

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow: dict[int, list[float]] = defaultdict(list)
    for f in features:
        by_dow[f.date.weekday()].append(f.active_hours)
    means = {day_names[i]: round(sum(by_dow.get(i, [0])) / max(len(by_dow.get(i, [1])), 1), 2) for i in range(7)}

    best = max(means, key=means.get)
    worst = min(means, key=means.get)

    consistency = 0.0
    if len(active) > 7:
        weekly_totals = [sum(active[i:i + 7]) for i in range(0, len(active) - 6, 7)]
        if weekly_totals:
            mean_w = sum(weekly_totals) / len(weekly_totals)
            if mean_w > 0:
                cv = (sum((w - mean_w) ** 2 for w in weekly_totals) / len(weekly_totals)) ** 0.5 / mean_w
                consistency = round(max(0, 1 - cv), 3)

    return WeeklyRhythm(periodicity=periodicity, weekday_means=means, best_day=best, worst_day=worst, consistency=consistency)


def productivity_drivers(
    features: Sequence[DayFeatures], *, target_field: str = "active_hours", max_lag: int = 2,
) -> list[tuple[str, CorrelationResult]]:
    """What correlates with productive days? Returns (factor_name, correlation) pairs.

    target_field: which metric to treat as "productivity" (default: active_hours).
    Correlates all other numeric fields against it at various lags.
    """
    if len(features) < 10:
        return []

    target = _extract(features, target_field)
    results: list[tuple[str, CorrelationResult]] = []
    for name in _NUMERIC_FIELDS:
        if name == target_field:
            continue
        values = _extract(features, name)
        if all(v == 0 for v in values):
            continue
        corrs = cross_correlate(values, target, max_lag=max_lag)
        significant = [c for c in corrs if c.significant]
        if significant:
            best = max(significant, key=lambda c: abs(c.r))
            results.append((name, best))

    results.sort(key=lambda x: abs(x[1].r), reverse=True)
    return results


def work_regime_changes(
    features: Sequence[DayFeatures],
    *,
    metrics: Sequence[str] = ("active_hours", "deep_work_min", "fragmentation", "commit_count"),
) -> list[tuple[str, ChangePoint]]:
    """When did fundamental work patterns shift? Returns (metric_name, changepoint) pairs."""
    if len(features) < 20:
        return []

    results: list[tuple[str, ChangePoint]] = []
    for name in metrics:
        values = _extract(features, name)
        if all(v == 0 for v in values):
            continue
        for cp in detect_changepoints(values, min_segment=7):
            results.append((name, cp))

    results.sort(key=lambda x: abs(x[1].magnitude), reverse=True)
    return results


def day_type_clusters(
    features: Sequence[DayFeatures],
    *,
    k: int | None = None,
    cluster_fields: Sequence[str] | None = None,
) -> list[DayCluster]:
    """What types of work days do I have? Cluster by feature vector."""
    if len(features) < 8:
        return []
    use_fields = cluster_fields or ("active_hours", "deep_work_min", "fragmentation",
                                     "commit_count", "command_count", "project_count",
                                     "chat_sessions", "listening_hours", "daily_steps")
    feature_dicts = [{name: float(getattr(f, name)) for name in use_fields} for f in features]
    return cluster_days(feature_dicts, k=k)


def activity_trends(
    features: Sequence[DayFeatures],
    *,
    metrics: Sequence[str] = ("active_hours", "deep_work_min", "fragmentation", "commit_count", "command_count"),
) -> dict[str, TrendResult]:
    """Trend analysis for specified metrics over the period."""
    if len(features) < 7:
        return {}
    return {name: detect_trend(_extract(features, name)) for name in metrics}


def day_anomalies(
    features: Sequence[DayFeatures],
    *,
    metrics: Sequence[str] = ("active_hours", "deep_work_min", "fragmentation", "commit_count"),
) -> dict[str, AnomalyResult]:
    """Is the last day anomalous compared to the rest as history?"""
    if len(features) < 10:
        return {}
    today = features[-1]
    history = features[:-1]
    return {
        name: anomaly_score(float(getattr(today, name)), _extract(history, name))
        for name in metrics
    }


# ══════════════════════════════════════════════════════════════════════════════
# Composite: run all analytics in one pass
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class FullAnalysis:
    features: list[DayFeatures]
    rhythm: WeeklyRhythm
    drivers: list[tuple[str, CorrelationResult]]
    regime_changes: list[tuple[str, ChangePoint]]
    clusters: list[DayCluster]
    trends: dict[str, TrendResult]
    anomalies: dict[str, AnomalyResult]


def full_analysis(start: date, end: date) -> FullAnalysis:
    """Run all analytics over a date range in one pass (one data fetch)."""
    features = build_day_features(start, end)
    return FullAnalysis(
        features=features,
        rhythm=weekly_rhythm(features),
        drivers=productivity_drivers(features),
        regime_changes=work_regime_changes(features),
        clusters=day_type_clusters(features),
        trends=activity_trends(features),
        anomalies=day_anomalies(features),
    )
