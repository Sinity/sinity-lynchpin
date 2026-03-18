from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from .anomaly import detect_anomalies
from .chains import TrajectoryChain, build_chains_from_attributed
from .day import TrajectoryDay, summarize_days
from .month import TrajectoryMonth, summarize_months
from .quarter import TrajectoryQuarter, summarize_quarters
from .rules import AttributedSignal, classify_signals
from .signal import DEFAULT_LOOKBACK_DAYS, TrajectorySignal, load_signals, resolve_window
from .week import TrajectoryWeek, summarize_weeks
from .year import TrajectoryYear, summarize_years


@dataclass(frozen=True)
class TrajectoryWindow:
    start: datetime
    end: datetime
    span_days: int
    signals: tuple[TrajectorySignal, ...]
    attributed: tuple[AttributedSignal, ...]
    chains: tuple[TrajectoryChain, ...]
    days: tuple[TrajectoryDay, ...]

    def day_map(self) -> dict[date, TrajectoryDay]:
        return {day.date: day for day in self.days}


def load_trajectory_window(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    days: Optional[int] = None,
    annotate_anomalies: bool = False,
) -> TrajectoryWindow:
    requested_days = days or DEFAULT_LOOKBACK_DAYS
    window_start, window_end = resolve_window(start=start, end=end, days=requested_days)
    span_days = _span_days(window_start, window_end)
    signal_list = tuple(load_signals(start=window_start, end=window_end, days=span_days))
    attributed = tuple(classify_signals(signal_list))
    chains = tuple(build_chains_from_attributed(attributed))
    day_list = tuple(
        summarize_days(
            signals=signal_list,
            chains=chains,
            start=window_start,
            end=window_end,
            days=span_days,
        )
    )
    if annotate_anomalies:
        day_list = _annotate_anomalies(day_list)
    return TrajectoryWindow(
        start=window_start,
        end=window_end,
        span_days=span_days,
        signals=signal_list,
        attributed=attributed,
        chains=chains,
        days=day_list,
    )


def load_date_window(
    start_date: date,
    end_date: date,
    *,
    annotate_anomalies: bool = False,
    local_tz=None,
) -> TrajectoryWindow:
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    tz = local_tz or datetime.now().astimezone().tzinfo or timezone.utc
    window_start = datetime.combine(start_date, time.min, tzinfo=tz)
    window_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return load_trajectory_window(
        start=window_start,
        end=window_end,
        days=(end_date - start_date).days + 1,
        annotate_anomalies=annotate_anomalies,
    )


def summarize_window_weeks(window: TrajectoryWindow) -> list[TrajectoryWeek]:
    return summarize_weeks(list(window.days))


def summarize_window_months(window: TrajectoryWindow) -> list[TrajectoryMonth]:
    return summarize_months(list(window.days), signals=list(window.signals))


def summarize_window_quarters(window: TrajectoryWindow) -> list[TrajectoryQuarter]:
    return summarize_quarters(summarize_window_months(window))


def summarize_window_years(window: TrajectoryWindow) -> list[TrajectoryYear]:
    return summarize_years(summarize_window_quarters(window))


def _annotate_anomalies(days: tuple[TrajectoryDay, ...]) -> tuple[TrajectoryDay, ...]:
    anomaly_by_date: dict[date, list[str]] = {}
    for anomaly in detect_anomalies(days):
        anomaly_by_date.setdefault(anomaly.date, []).append(anomaly.description)
    annotated: list[TrajectoryDay] = []
    for day in days:
        descriptions = anomaly_by_date.get(day.date)
        if descriptions:
            annotated.append(dataclasses.replace(day, anomalies=tuple(descriptions)))
        else:
            annotated.append(day)
    return tuple(annotated)


def _span_days(start: datetime, end: datetime) -> int:
    last_date = (end - timedelta(microseconds=1)).date()
    return max((last_date - start.date()).days + 1, 1)
