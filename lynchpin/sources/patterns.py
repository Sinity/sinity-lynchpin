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
from typing import Optional, Sequence

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
    # AW
    active_hours: Optional[float] = None
    deep_work_min: Optional[float] = None
    sustained_focus_min: Optional[float] = None
    fragmentation: Optional[float] = None
    project_count: Optional[int] = None
    # Git
    commit_count: Optional[int] = None
    # Terminal
    command_count: Optional[int] = None
    # Polylogue
    chat_sessions: Optional[int] = None
    # Sleep
    sleep_hours: Optional[float] = None
    sleep_score: Optional[float] = None
    # Spotify
    listening_hours: Optional[float] = None
    # Reddit
    reddit_comments: Optional[int] = None
    # Health — basic
    daily_steps: Optional[int] = None
    vitality_score: Optional[float] = None
    # Health — expanded
    stress_avg: Optional[float] = None
    heart_rate_avg: Optional[float] = None
    heart_rate_resting: Optional[float] = None
    hrv_rmssd: Optional[float] = None
    spo2_avg: Optional[float] = None
    floors_climbed: Optional[float] = None
    skin_temp_avg: Optional[float] = None
    snoring_duration_min: Optional[float] = None
    # Web
    browsing_visits: Optional[int] = None
    browsing_domains: Optional[int] = None
    # Social
    messenger_messages: Optional[int] = None
    bookmarks_added: Optional[int] = None
    # Substance
    substance_doses: Optional[int] = None
    # Categorical
    dominant_mode: str = "unknown"
    dominant_project: str = ""

    def as_numeric_dict(self) -> dict[str, float]:
        """All numeric fields as {name: float} — skips None values."""
        result = {}
        for name in _NUMERIC_FIELDS:
            val = getattr(self, name)
            if val is not None:
                result[name] = float(val)
        return result


_NUMERIC_FIELDS = tuple(
    f.name for f in fields(DayFeatures)
    if f.name not in ("date", "dominant_mode", "dominant_project")
)


def _extract(features: Sequence[DayFeatures], field: str) -> list[float]:
    """Extract one numeric field as a float series — skips None values."""
    return [float(v) for f in features if (v := getattr(f, field)) is not None]


def _safe_fetch(fn, *args, default=None, **kwargs):
    """Call a source function; return default on failure."""
    try:
        result = fn(*args, **kwargs)
        if hasattr(result, '__next__'):
            result = list(result)
        return result
    except Exception:
        return default if default is not None else []


def build_day_features(start: date, end: date) -> list[DayFeatures]:
    """Assemble per-day feature vectors from all available sources (31 numeric fields)."""
    from .activitywatch import active_seconds_by_date, deep_work, sustained_focus, fragmentation, attention, app_sessions
    from .git import daily_activity
    from .terminal import shell_sessions
    from .polylogue import daily_activity as chat_daily
    from .sleep import entries as sleep_entries
    from .spotify import daily_listening
    from .reddit import daily_activity as reddit_daily
    from .health import daily_steps, daily_vitality, daily_health_summary
    from .web import daily_browsing
    from .exports import daily_messenger_activity, daily_raindrop_activity
    from .substance import daily_summary as substance_daily

    s_dt, e_dt = date_to_dt_range(start, end)

    # ── AW ──
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

    # ── Git ──
    git_by_day: dict[date, int] = {}
    for g in daily_activity(start=start, end=end):
        git_by_day[g.date] = git_by_day.get(g.date, 0) + g.commit_count

    # ── Terminal ──
    shell_by_day: dict[date, int] = {}
    for s in shell_sessions(start=s_dt, end=e_dt):
        shell_by_day[s.start.date()] = shell_by_day.get(s.start.date(), 0) + s.command_count

    # ── Polylogue ──
    chat_by_day: dict[date, int] = {}
    for c in _safe_fetch(chat_daily, start=start, end=end):
        chat_by_day[c.date] = chat_by_day.get(c.date, 0) + c.session_count

    # ── Sleep ──
    sleep_data = {e.date: e for e in sleep_entries()}

    # ── Spotify ──
    spotify_by_day = {s.date: s.hours for s in _safe_fetch(daily_listening, start=start, end=end)}

    # ── Reddit ──
    reddit_by_day = {r.date: r.comment_count for r in _safe_fetch(reddit_daily, start=start, end=end)}

    # ── Health: basic ──
    steps_by_day = {s.date: s.steps for s in _safe_fetch(daily_steps, start=start, end=end)}
    vitality_by_day = {v.date: v.activity_score for v in _safe_fetch(daily_vitality, start=start, end=end) if v.activity_score is not None}

    # ── Health: expanded (daily summary joins all health signals) ──
    health_by_day = {h.date: h for h in _safe_fetch(daily_health_summary, start=start, end=end)}

    # ── Web ──
    web_by_day = {w.date: w for w in _safe_fetch(daily_browsing, start=start, end=end)}

    # ── Messenger ──
    msg_by_day = {m.date: m.message_count for m in _safe_fetch(daily_messenger_activity, start=start, end=end)}

    # ── Raindrop ──
    bm_by_day = {r.date: r.bookmarks_added for r in _safe_fetch(daily_raindrop_activity, start=start, end=end)}

    # ── Substance ──
    sub_by_day = {s.date: s.dose_count for s in _safe_fetch(substance_daily, start=start, end=end)}

    # ── AW mode/project ──
    sessions = app_sessions(start=s_dt, end=e_dt)
    mode_by_day: dict[date, str] = {}
    proj_by_day: dict[date, str] = {}
    for sess in sessions:
        d = sess.start.date()
        if d not in mode_by_day and sess.mode:
            mode_by_day[d] = sess.mode
        if d not in proj_by_day and sess.project:
            proj_by_day[d] = sess.project

    # ── Collect all dates that have data from ANY source ──
    data_dates: set[date] = set()
    data_dates.update(d for d, v in aw_active.items() if v > 0)
    data_dates.update(dw_by_day)
    data_dates.update(sf_by_day)
    data_dates.update(frag_by_day)
    data_dates.update(att_by_day)
    data_dates.update(git_by_day)
    data_dates.update(shell_by_day)
    data_dates.update(chat_by_day)
    data_dates.update(sleep_data)
    data_dates.update(spotify_by_day)
    data_dates.update(reddit_by_day)
    data_dates.update(steps_by_day)
    data_dates.update(vitality_by_day)
    data_dates.update(health_by_day)
    data_dates.update(web_by_day)
    data_dates.update(msg_by_day)
    data_dates.update(bm_by_day)
    data_dates.update(sub_by_day)
    data_dates.update(mode_by_day)

    # ── Assemble — only dates with data from at least one source ──
    result: list[DayFeatures] = []
    for d in iter_dates(start, end):
        if d not in data_dates:
            continue
        sleep_entry = sleep_data.get(d)
        health = health_by_day.get(d)
        web = web_by_day.get(d)
        aw_has_data = d in aw_active and aw_active[d] > 0
        result.append(DayFeatures(
            date=d,
            # AW — None if no AW data for this day
            active_hours=round(aw_active[d] / 3600, 2) if aw_has_data else None,
            deep_work_min=round(dw_by_day[d], 1) if d in dw_by_day else None,
            sustained_focus_min=round(sf_by_day[d], 1) if d in sf_by_day else None,
            fragmentation=round(frag_by_day[d], 3) if d in frag_by_day else None,
            project_count=att_by_day[d] if d in att_by_day else None,
            # Git
            commit_count=git_by_day[d] if d in git_by_day else None,
            # Terminal
            command_count=shell_by_day[d] if d in shell_by_day else None,
            # Polylogue
            chat_sessions=chat_by_day[d] if d in chat_by_day else None,
            # Sleep
            sleep_hours=round(sleep_entry.total_minutes / 60, 2) if sleep_entry else None,
            sleep_score=round(sleep_entry.avg_score, 1) if sleep_entry and sleep_entry.avg_score is not None else None,
            # Spotify
            listening_hours=round(spotify_by_day[d], 2) if d in spotify_by_day else None,
            # Reddit
            reddit_comments=reddit_by_day[d] if d in reddit_by_day else None,
            # Health basic
            daily_steps=steps_by_day[d] if d in steps_by_day else None,
            vitality_score=vitality_by_day[d] if d in vitality_by_day else None,
            # Health expanded — only if health summary exists for this date
            stress_avg=round(health.stress_avg, 1) if health and health.stress_avg is not None else None,
            heart_rate_avg=round(health.heart_rate_avg, 1) if health and health.heart_rate_avg is not None else None,
            heart_rate_resting=round(health.heart_rate_resting, 1) if health and health.heart_rate_resting is not None else None,
            hrv_rmssd=round(health.hrv_rmssd_avg, 2) if health and health.hrv_rmssd_avg is not None else None,
            spo2_avg=round(health.spo2_avg, 1) if health and health.spo2_avg is not None else None,
            floors_climbed=round(health.floors, 1) if health and health.floors is not None else None,
            skin_temp_avg=round(health.skin_temp_avg, 2) if health and health.skin_temp_avg is not None else None,
            snoring_duration_min=round(health.snoring_duration_s / 60, 1) if health and health.snoring_duration_s else None,
            # Web
            browsing_visits=web.visit_count if web else None,
            browsing_domains=web.unique_domains if web else None,
            # Social
            messenger_messages=msg_by_day[d] if d in msg_by_day else None,
            bookmarks_added=bm_by_day[d] if d in bm_by_day else None,
            # Substance
            substance_doses=sub_by_day[d] if d in sub_by_day else None,
            # Categorical
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
        if f.active_hours is not None:
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

    results: list[tuple[str, CorrelationResult]] = []
    for name in _NUMERIC_FIELDS:
        if name == target_field:
            continue
        # Extract paired values where both fields are non-None
        vals = []
        targets = []
        for f in features:
            v = getattr(f, name)
            t = getattr(f, target_field)
            if v is not None and t is not None:
                vals.append(float(v))
                targets.append(float(t))
        if len(vals) < 10 or all(v == 0 for v in vals):
            continue
        corrs = cross_correlate(vals, targets, max_lag=max_lag)
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
    feature_dicts = [{name: float(v) for name in use_fields if (v := getattr(f, name)) is not None} for f in features]
    return cluster_days(feature_dicts, k=k)


def activity_trends(
    features: Sequence[DayFeatures],
    *,
    metrics: Sequence[str] = (
        "active_hours", "deep_work_min", "fragmentation", "commit_count", "command_count",
        "stress_avg", "heart_rate_avg", "hrv_rmssd", "floors_climbed", "browsing_visits",
    ),
) -> dict[str, TrendResult]:
    """Trend analysis for specified metrics over the period."""
    if len(features) < 7:
        return {}
    return {name: detect_trend(_extract(features, name)) for name in metrics}


def day_anomalies(
    features: Sequence[DayFeatures],
    *,
    metrics: Sequence[str] = (
        "active_hours", "deep_work_min", "fragmentation", "commit_count",
        "stress_avg", "heart_rate_avg", "hrv_rmssd",
    ),
) -> dict[str, AnomalyResult]:
    """Is the last day anomalous compared to the rest as history?"""
    if len(features) < 10:
        return {}
    today = features[-1]
    history = features[:-1]
    result = {}
    for name in metrics:
        val = getattr(today, name)
        if val is None:
            continue
        hist = _extract(history, name)
        if not hist:
            continue
        result[name] = anomaly_score(float(val), hist)
    return result


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


# ══════════════════════════════════════════════════════════════════════════════
# Cross-source health analytics
# ══════════════════════════════════════════════════════════════════════════════


def health_correlations(features: Sequence[DayFeatures]) -> dict[str, list[CorrelationResult]]:
    """Cross-source health correlations: sleep×stress, HRV×productivity, substance×health.

    Returns {pair_label: [CorrelationResult...]} for significant lagged correlations.
    """
    if len(features) < 14:
        return {}

    pairs = [
        ("sleep_hours", "stress_avg", "sleep→stress"),
        ("sleep_hours", "heart_rate_avg", "sleep→heart_rate"),
        ("sleep_hours", "active_hours", "sleep→productivity"),
        ("sleep_score", "fragmentation", "sleep_quality→fragmentation"),
        ("hrv_rmssd", "active_hours", "hrv→productivity"),
        ("hrv_rmssd", "deep_work_min", "hrv→deep_work"),
        ("stress_avg", "fragmentation", "stress→fragmentation"),
        ("stress_avg", "commit_count", "stress→commits"),
        ("substance_doses", "stress_avg", "substance→stress"),
        ("substance_doses", "heart_rate_avg", "substance→heart_rate"),
        ("substance_doses", "active_hours", "substance→productivity"),
        ("substance_doses", "fragmentation", "substance→fragmentation"),
        ("floors_climbed", "stress_avg", "activity→stress"),
        ("daily_steps", "sleep_hours", "steps→sleep"),
    ]

    results: dict[str, list[CorrelationResult]] = {}
    for cause_field, effect_field, label in pairs:
        # Extract paired values where both fields are non-None
        cause_vals = []
        effect_vals = []
        for f in features:
            c = getattr(f, cause_field)
            e = getattr(f, effect_field)
            if c is not None and e is not None:
                cause_vals.append(float(c))
                effect_vals.append(float(e))
        # Need enough paired data points
        if len(cause_vals) < 14:
            continue
        # Skip if either series is all zeros
        if all(v == 0 for v in cause_vals) or all(v == 0 for v in effect_vals):
            continue
        corrs = cross_correlate(cause_vals, effect_vals, max_lag=3)
        significant = [c for c in corrs if c.significant]
        if significant:
            results[label] = significant

    return results
