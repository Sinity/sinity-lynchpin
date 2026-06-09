"""Canonical derived temporal signal events and detection logic.

The detection functions (``detect_temporal_signals``, ``ANOMALY_BASELINE_DAYS``,
etc.) live here so that ``ingest/`` modules can import them without crossing into
the ``graph/`` layer.  ``graph/temporal_signals`` re-exports these symbols for
callers that already depend on the graph-layer path.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from ..core.analytics import (
    anomaly_score,
    detect_changepoints,
    detect_periodicity,
    detect_trend,
)
from ..core.config import get_config


@dataclass(frozen=True)
class TemporalSignalEvent:
    kind: str
    signal: str
    event_date: date
    summary: str
    payload: dict[str, Any]


def temporal_signals_path(root: Path | None = None) -> Path:
    base = root or get_config().derived_root
    return base / "temporal/signals.ndjson"


def temporal_signals_manifest_path(root: Path | None = None) -> Path:
    return temporal_signals_path(root).with_suffix(".manifest.json")


def iter_temporal_signals(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[TemporalSignalEvent]:
    target = path or temporal_signals_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("temporal_signals", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical temporal signal product is missing: {target}. "
            "Run python -m lynchpin.ingest.temporal_signals_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            event_date = date.fromisoformat(str(payload["event_date"]))
            if start is not None and event_date < start:
                continue
            if end is not None and event_date >= end:
                continue
            event_payload = payload.get("payload")
            yield TemporalSignalEvent(
                kind=str(payload.get("kind") or ""),
                signal=str(payload.get("signal") or ""),
                event_date=event_date,
                summary=str(payload.get("summary") or ""),
                payload=event_payload if isinstance(event_payload, dict) else {},
            )


# ---------------------------------------------------------------------------
# Detection engine — moved here from graph/temporal_signals so that ingest
# modules can call these without crossing the graph/ layer boundary.
# graph/temporal_signals re-exports these for existing callers.
# ---------------------------------------------------------------------------

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
    ensure_inputs: bool = True,
) -> tuple[TemporalEvent, ...]:
    """Run all detectors over each signal and return typed events.

    Detection runs over a baseline-extended window: anomaly detection needs
    historical context (default 28 days) before ``start`` to compare against.
    Change-point and trend run within ``[start, end]`` only — they describe
    the analyzed window, not history.
    """
    specs = tuple(specs) if specs else default_signal_specs(ensure_inputs=ensure_inputs)
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


def default_signal_specs(*, ensure_inputs: bool = True) -> tuple[SignalSpec, ...]:
    """Built-in signal loaders. Each returns a date→float series."""
    return (
        SignalSpec(
            "deep_work_min",
            "ActivityWatch deep work minutes per day",
            lambda start, end: _load_deep_work(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "active_hours",
            "ActivityWatch active hours per day",
            lambda start, end: _load_active_hours(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "fragmentation_score",
            "AW fragmentation score per day",
            lambda start, end: _load_fragmentation(start, end, ensure=ensure_inputs),
        ),
        SignalSpec("commits_per_day", "Git commits across active repos", _load_commits),
        SignalSpec(
            "terminal_error_rate",
            "Shell error rate per day",
            lambda start, end: _load_error_rate(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "terminal_command_count",
            "Shell command volume per day",
            lambda start, end: _load_command_count(start, end, ensure=ensure_inputs),
        ),
        SignalSpec("ai_session_count", "Polylogue AI sessions per day", _load_ai_sessions),
        SignalSpec("ai_engaged_minutes", "Polylogue engaged minutes per day", _load_ai_engaged),
        SignalSpec(
            "web_visit_count",
            "Canonical browser history visits per day",
            lambda start, end: _load_web_visits(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "bookmark_added_count",
            "Browser bookmarks added per day",
            lambda start, end: _load_bookmarks(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "communication_event_count",
            "Communication events per day",
            lambda start, end: _load_communications(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "arbtt_active_minutes",
            "ARBTT active minutes per day",
            lambda start, end: _load_arbtt_minutes(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "google_activity_count",
            "Timestamped Google Takeout activity rows per day",
            lambda start, end: _load_google_activity(start, end, ensure=ensure_inputs),
        ),
        SignalSpec(
            "youtube_activity_count",
            "Google Takeout My Activity rows from YouTube services per day",
            lambda start, end: _load_youtube_activity(start, end, ensure=ensure_inputs),
        ),
        SignalSpec("sleep_hours", "Wearable sleep duration per day", _load_sleep_hours),
        SignalSpec("sleep_score", "Wearable sleep score per day", _load_sleep_score),
        SignalSpec("hrv_rmssd", "HRV RMSSD per day", _load_hrv),
        SignalSpec("resting_hr", "Resting heart rate per day", _load_resting_hr),
    )


def _load_deep_work(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from ..core.primitives import logical_date
    from .activitywatch_derived import iter_derived_deep_work

    by_day: dict[date, float] = defaultdict(float)
    for row in iter_derived_deep_work(start=_start_dt(start), end=_end_dt(end), ensure=ensure):
        day = logical_date(row.start)
        if start <= day <= end:
            by_day[day] += row.duration_min
    return dict(by_day)


def _load_active_hours(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .activitywatch_derived import iter_derived_circadian

    by_day: dict[date, float] = defaultdict(float)
    for row in iter_derived_circadian(start=start, end=end, ensure=ensure):
        by_day[row.date] += row.active_min / 60.0
    return dict(by_day)


def _load_fragmentation(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .activitywatch_derived import iter_derived_fragmentation

    return {
        row.date: row.fragmentation
        for row in iter_derived_fragmentation(start=start, end=end, ensure=ensure)
    }


def _load_commits(start: date, end: date) -> dict[date, float]:
    # Sources layer: use live git log directly.  The substrate-optimized path
    # (reading commit_fact from DuckDB) lives in graph/temporal_signals, which
    # is allowed to import substrate.  Here we keep the pure-sources fallback.
    from .git import daily_activity

    by_day: dict[date, float] = defaultdict(float)
    for row in daily_activity(start=start, end=end):
        by_day[row.date] += row.commit_count
    return dict(by_day)


def _load_error_rate(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    return {row.date: row.error_rate for row in _terminal_daily_rows(start, end, ensure=ensure)}


def _load_command_count(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    return {
        row.date: float(row.command_count)
        for row in _terminal_daily_rows(start, end, ensure=ensure)
    }


def _load_ai_sessions(start: date, end: date) -> dict[date, float]:
    by_day: dict[date, float] = defaultdict(float)
    for row in _polylogue_daily_rows(start, end):
        by_day[row.date] += row.session_count
    return dict(by_day)


def _load_ai_engaged(start: date, end: date) -> dict[date, float]:
    by_day: dict[date, float] = defaultdict(float)
    for row in _polylogue_daily_rows(start, end):
        by_day[row.date] += row.engaged_minutes
    return dict(by_day)


def _load_web_visits(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .web import daily_browsing

    return {row.date: float(row.visit_count) for row in daily_browsing(start=start, end=end, ensure=ensure)}


def _load_bookmarks(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .bookmarks import daily_bookmark_activity

    return {row.date: float(row.bookmark_count) for row in daily_bookmark_activity(start=start, end=end, ensure=ensure)}


def _load_communications(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .communications import daily_communication_activity

    return {row.date: float(row.event_count) for row in daily_communication_activity(start=start, end=end, ensure=ensure)}


def _load_arbtt_minutes(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .arbtt import daily_arbtt_activity

    return {row.date: row.active_minutes for row in daily_arbtt_activity(start=start, end=end, ensure=ensure)}


def _load_google_activity(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .google_takeout_products import iter_daily_activity

    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("google_takeout", window=(start, end))
    by_day: dict[date, float] = defaultdict(float)
    for row in iter_daily_activity(start=start, end=end, ensure=False):
        by_day[row.date] += row.event_count
    return dict(by_day)


def _load_youtube_activity(start: date, end: date, *, ensure: bool = True) -> dict[date, float]:
    from .google_takeout_products import iter_events

    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("google_takeout", window=(start, end))
    by_day: dict[date, float] = defaultdict(float)
    for row in iter_events(product="my_activity", start=start, end=end, ensure=False):
        service = (row.service or "").lower()
        if "youtube" not in service:
            continue
        by_day[row.timestamp.date()] += 1
    return dict(by_day)


def _load_sleep_hours(start: date, end: date) -> dict[date, float]:
    from .sleep import entries_in_range

    out: dict[date, float] = {}
    for entry in entries_in_range(start=start, end=end):
        if entry.total_minutes:
            out[entry.date] = entry.total_minutes / 60.0
    return out


def _load_sleep_score(start: date, end: date) -> dict[date, float]:
    from .sleep import entries_in_range

    out: dict[date, float] = {}
    for entry in entries_in_range(start=start, end=end):
        if entry.avg_score is not None:
            out[entry.date] = float(entry.avg_score)
    return out


def _load_hrv(start: date, end: date) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in _health_daily_rows(start, end):
        if row.hrv_rmssd_avg is not None:
            out[row.date] = row.hrv_rmssd_avg
    return out


def _load_resting_hr(start: date, end: date) -> dict[date, float]:
    out: dict[date, float] = {}
    for row in _health_daily_rows(start, end):
        if row.heart_rate_resting is not None:
            out[row.date] = row.heart_rate_resting
    return out


@lru_cache(maxsize=16)
def _terminal_daily_rows(start: date, end: date, ensure: bool = True) -> tuple[Any, ...]:
    from .terminal import daily_terminal_activity

    return tuple(daily_terminal_activity(start=start, end=end, ensure=ensure))


@lru_cache(maxsize=16)
def _polylogue_daily_rows(start: date, end: date) -> tuple[Any, ...]:
    from .polylogue import daily_activity

    return tuple(daily_activity(start=start, end=end))


@lru_cache(maxsize=16)
def _health_daily_rows(start: date, end: date) -> tuple[Any, ...]:
    from .health import daily_health_summary

    return tuple(daily_health_summary(start=start, end=end))


def _start_dt(day: date) -> datetime:
    from ..core.primitives import date_to_dt_range

    return date_to_dt_range(day, day)[0]


def _end_dt(day: date) -> datetime:
    from ..core.primitives import date_to_dt_range

    return date_to_dt_range(day, day)[1]
