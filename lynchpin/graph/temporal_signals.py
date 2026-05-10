"""Temporal signal detection over daily aggregates.

Wires the existing ``core.analytics`` primitives — change points, trend,
anomaly score, periodicity — into typed evidence nodes. Each tracked signal
becomes a daily series, and detected events surface as one of:

- ``temporal_changepoint`` — a date where the series mean shifts
- ``temporal_trend`` — a sustained rising/falling direction over the window
- ``temporal_anomaly`` — a day whose value is an outlier vs prior history
- ``temporal_rhythm`` — a periodic component (weekly, biweekly, monthly...)

The detectors are deterministic and use only the source modules already
exposed by lynchpin. No LLM, no external services.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..core.analytics import (
    anomaly_score,
    detect_changepoints,
    detect_periodicity,
    detect_trend,
)

ANOMALY_BASELINE_DAYS = 28
ANOMALY_MIN_HISTORY = 14
ANOMALY_SCORE_THRESHOLD = 1.5
TREND_MIN_SAMPLES = 14
PERIODICITY_MIN_SAMPLES = 21


@dataclass(frozen=True)
class TemporalEvent:
    kind: str
    signal: str
    event_date: date
    summary: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SignalSpec:
    name: str
    description: str
    loader: Callable[[date, date], dict[date, float]]


def detect_temporal_signals(
    *,
    start: date,
    end: date,
    specs: Sequence[SignalSpec] | None = None,
) -> tuple[TemporalEvent, ...]:
    """Run all detectors over each signal and return typed events.

    Detection runs over a baseline-extended window: anomaly detection needs
    historical context (default 28 days) before ``start`` to compare against.
    Change-point and trend run within ``[start, end]`` only — they describe
    the analyzed window, not history.
    """
    specs = tuple(specs) if specs else default_signal_specs()
    events: list[TemporalEvent] = []

    history_start = start - timedelta(days=ANOMALY_BASELINE_DAYS)
    for spec in specs:
        try:
            series_full = spec.loader(history_start, end)
        except Exception:
            continue
        if not series_full:
            continue
        events.extend(_detect_for_signal(spec, series_full, start=start, end=end))

    return tuple(events)


def _detect_for_signal(
    spec: SignalSpec,
    full_series: dict[date, float],
    *,
    start: date,
    end: date,
) -> list[TemporalEvent]:
    events: list[TemporalEvent] = []

    in_window_dates = sorted(d for d in full_series if start <= d <= end)
    in_window_values = [full_series[d] for d in in_window_dates]

    # Change points within the analyzed window.
    if len(in_window_values) >= 10:
        for cp in detect_changepoints(in_window_values, min_segment=5, max_changepoints=3):
            cp_date = in_window_dates[cp.index]
            events.append(
                TemporalEvent(
                    kind="temporal_changepoint",
                    signal=spec.name,
                    event_date=cp_date,
                    summary=(
                        f"{spec.name} shifted from {cp.before_mean:g} to "
                        f"{cp.after_mean:g} ({cp.magnitude * 100:+.0f}%)"
                    ),
                    payload={
                        "signal": spec.name,
                        "before_mean": cp.before_mean,
                        "after_mean": cp.after_mean,
                        "magnitude": cp.magnitude,
                        "direction": "up" if cp.after_mean > cp.before_mean else "down",
                    },
                )
            )

    # Trend over the window.
    if len(in_window_values) >= TREND_MIN_SAMPLES:
        trend = detect_trend(in_window_values, min_samples=TREND_MIN_SAMPLES)
        if trend.direction != "stable" and trend.significant:
            events.append(
                TemporalEvent(
                    kind="temporal_trend",
                    signal=spec.name,
                    event_date=in_window_dates[-1],
                    summary=(
                        f"{spec.name} is {trend.direction} over "
                        f"{trend.n} days (slope {trend.slope:+.3g}/day, "
                        f"p={trend.p_value:.3f})"
                    ),
                    payload={
                        "signal": spec.name,
                        "direction": trend.direction,
                        "slope": trend.slope,
                        "p_value": trend.p_value,
                        "n": trend.n,
                        "window_start": in_window_dates[0].isoformat(),
                        "window_end": in_window_dates[-1].isoformat(),
                    },
                )
            )

    # Anomalies — each in-window day vs its prior baseline.
    sorted_full = sorted(full_series.items())
    for i, (day, value) in enumerate(sorted_full):
        if not (start <= day <= end):
            continue
        prior = [v for _, v in sorted_full[:i] if v is not None]
        if len(prior) < ANOMALY_MIN_HISTORY:
            continue
        baseline = prior[-ANOMALY_BASELINE_DAYS:] if len(prior) > ANOMALY_BASELINE_DAYS else prior
        result = anomaly_score(value, baseline, method="iqr")
        if result.is_anomaly and result.score >= ANOMALY_SCORE_THRESHOLD:
            events.append(
                TemporalEvent(
                    kind="temporal_anomaly",
                    signal=spec.name,
                    event_date=day,
                    summary=(
                        f"{spec.name}={value:g} is {result.direction} "
                        f"(score {result.score:.2f}, threshold {result.threshold:g})"
                    ),
                    payload={
                        "signal": spec.name,
                        "value": value,
                        "score": result.score,
                        "threshold": result.threshold,
                        "direction": result.direction,
                        "baseline_n": len(baseline),
                    },
                )
            )

    # Periodicity — one summary node per signal if a strong rhythm exists.
    if len(in_window_values) >= PERIODICITY_MIN_SAMPLES:
        components = detect_periodicity(in_window_values, min_period=2, max_period=len(in_window_values) / 2)
        for comp in components[:2]:
            events.append(
                TemporalEvent(
                    kind="temporal_rhythm",
                    signal=spec.name,
                    event_date=in_window_dates[-1],
                    summary=(
                        f"{spec.name} shows a {comp.label} rhythm "
                        f"(period {comp.period:.1f} days, amplitude {comp.amplitude:.3g})"
                    ),
                    payload={
                        "signal": spec.name,
                        "period_days": comp.period,
                        "amplitude": comp.amplitude,
                        "power": comp.power,
                        "label": comp.label,
                    },
                )
            )

    return events


def default_signal_specs() -> tuple[SignalSpec, ...]:
    """Built-in signal loaders. Each returns a date→float series."""
    return (
        SignalSpec("deep_work_min", "ActivityWatch deep work minutes per day", _load_deep_work),
        SignalSpec("active_hours", "ActivityWatch active hours per day", _load_active_hours),
        SignalSpec("fragmentation_score", "AW fragmentation score per day", _load_fragmentation),
        SignalSpec("commits_per_day", "Git commits across active repos", _load_commits),
        SignalSpec("terminal_error_rate", "Shell error rate per day", _load_error_rate),
        SignalSpec("terminal_command_count", "Shell command volume per day", _load_command_count),
        SignalSpec("ai_session_count", "Polylogue AI sessions per day", _load_ai_sessions),
        SignalSpec("ai_engaged_minutes", "Polylogue engaged minutes per day", _load_ai_engaged),
        SignalSpec("sleep_hours", "Wearable sleep duration per day", _load_sleep_hours),
        SignalSpec("sleep_score", "Wearable sleep score per day", _load_sleep_score),
        SignalSpec("hrv_rmssd", "HRV RMSSD per day", _load_hrv),
        SignalSpec("resting_hr", "Resting heart rate per day", _load_resting_hr),
    )


def _load_deep_work(start: date, end: date) -> dict[date, float]:
    from ..sources.activitywatch import daily_activity

    return {row.date: row.deep_work_min for row in daily_activity(start=start, end=end)}


def _load_active_hours(start: date, end: date) -> dict[date, float]:
    from ..sources.activitywatch import daily_activity

    return {row.date: row.active_hours for row in daily_activity(start=start, end=end)}


def _load_fragmentation(start: date, end: date) -> dict[date, float]:
    from ..sources.activitywatch import daily_activity

    return {row.date: row.fragmentation_score for row in daily_activity(start=start, end=end)}


def _load_commits(start: date, end: date) -> dict[date, float]:
    from collections import defaultdict

    from ..sources.git import daily_activity

    by_day: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        by_day[row.date] += row.commit_count
    return dict(by_day)


def _load_error_rate(start: date, end: date) -> dict[date, float]:
    from ..sources.terminal import daily_terminal_activity

    return {row.date: row.error_rate for row in daily_terminal_activity(start=start, end=end)}


def _load_command_count(start: date, end: date) -> dict[date, float]:
    from ..sources.terminal import daily_terminal_activity

    return {row.date: float(row.command_count) for row in daily_terminal_activity(start=start, end=end)}


def _load_ai_sessions(start: date, end: date) -> dict[date, float]:
    from collections import defaultdict

    from ..sources.polylogue import daily_activity

    by_day: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        by_day[row.date] += row.session_count
    return dict(by_day)


def _load_ai_engaged(start: date, end: date) -> dict[date, float]:
    from collections import defaultdict

    from ..sources.polylogue import daily_activity

    by_day: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        by_day[row.date] += row.engaged_minutes
    return dict(by_day)


def _load_sleep_hours(start: date, end: date) -> dict[date, float]:
    from ..sources.sleep import entries_in_range

    out: dict[date, float] = {}
    for entry in entries_in_range(start=start, end=end):
        if entry.total_minutes:
            out[entry.date] = entry.total_minutes / 60.0
    return out


def _load_sleep_score(start: date, end: date) -> dict[date, float]:
    from ..sources.sleep import entries_in_range

    out: dict[date, float] = {}
    for entry in entries_in_range(start=start, end=end):
        if entry.avg_score is not None:
            out[entry.date] = float(entry.avg_score)
    return out


def _load_hrv(start: date, end: date) -> dict[date, float]:
    from ..sources.health import daily_health_summary

    out: dict[date, float] = {}
    for row in daily_health_summary(start=start, end=end):
        if row.hrv_rmssd_avg is not None:
            out[row.date] = row.hrv_rmssd_avg
    return out


def _load_resting_hr(start: date, end: date) -> dict[date, float]:
    from ..sources.health import daily_health_summary

    out: dict[date, float] = {}
    for row in daily_health_summary(start=start, end=end):
        if row.heart_rate_resting is not None:
            out[row.date] = row.heart_rate_resting
    return out
