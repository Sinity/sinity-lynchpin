"""Daily aggregate views over Samsung Health raw signal loaders."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Optional

from .health_loaders import (
    calorie_burns,
    daily_steps,
    daily_vitality,
    floors_climbed,
    heart_rate_measurements,
    hrv_measurements,
    respiratory_rate,
    skin_temperature,
    snoring_records,
    spo2_measurements,
    stress_measurements,
)
from .health_models import DailyHealthSummary, DailyHeartRateSummary, DailyStressSummary

__all__ = [
    "daily_stress",
    "daily_heart_rate",
    "daily_health_summary",
]


def daily_stress(*, start: Optional[date] = None, end: Optional[date] = None) -> list[DailyStressSummary]:
    """Aggregate stress measurements per day."""
    by_day: dict[date, list[int]] = defaultdict(list)
    for m in stress_measurements(start=start, end=end):
        if m.score is not None:
            by_day[m.timestamp.date()].append(m.score)
    result = []
    for d in sorted(by_day):
        scores = by_day[d]
        result.append(DailyStressSummary(
            date=d,
            measurement_count=len(scores),
            avg_score=sum(scores) / len(scores),
            min_score=min(scores),
            max_score=max(scores),
        ))
    return result


def daily_heart_rate(*, start: Optional[date] = None, end: Optional[date] = None) -> list[DailyHeartRateSummary]:
    """Aggregate heart rate per day. Resting HR estimated as minimum of hourly averages."""
    by_day: dict[date, list[float]] = defaultdict(list)
    for m in heart_rate_measurements(start=start, end=end):
        by_day[m.timestamp.date()].append(m.heart_rate)
    result = []
    for d in sorted(by_day):
        hrs = by_day[d]
        result.append(DailyHeartRateSummary(
            date=d,
            measurement_count=len(hrs),
            avg_hr=sum(hrs) / len(hrs),
            min_hr=min(hrs),
            max_hr=max(hrs),
            resting_hr=min(hrs),
        ))
    return result


def daily_health_summary(*, start: Optional[date] = None, end: Optional[date] = None) -> list[DailyHealthSummary]:
    """Combine all health signals into one record per day."""
    all_dates: set[date] = set()

    steps_by_day: dict[date, int] = {}
    for s in daily_steps(start=start, end=end):
        steps_by_day[s.date] = s.steps
        all_dates.add(s.date)

    stress_by_day: dict[date, list[int]] = defaultdict(list)
    for stress in stress_measurements(start=start, end=end):
        if stress.score is not None:
            stress_by_day[stress.timestamp.date()].append(stress.score)
            all_dates.add(stress.timestamp.date())

    hr_by_day: dict[date, list[float]] = defaultdict(list)
    for heart_rate in heart_rate_measurements(start=start, end=end):
        hr_by_day[heart_rate.timestamp.date()].append(heart_rate.heart_rate)
        all_dates.add(heart_rate.timestamp.date())

    hrv_by_day: dict[date, list[float]] = defaultdict(list)
    for hrv in hrv_measurements(start=start, end=end):
        if hrv.rmssd_avg is not None:
            hrv_by_day[hrv.timestamp.date()].append(hrv.rmssd_avg)
            all_dates.add(hrv.timestamp.date())

    spo2_by_day: dict[date, list[float]] = defaultdict(list)
    for spo2 in spo2_measurements(start=start, end=end):
        spo2_by_day[spo2.timestamp.date()].append(spo2.spo2)
        all_dates.add(spo2.timestamp.date())

    resp_by_day: dict[date, list[float]] = defaultdict(list)
    for respiratory in respiratory_rate(start=start, end=end):
        resp_by_day[respiratory.timestamp.date()].append(respiratory.avg_rate)
        all_dates.add(respiratory.timestamp.date())

    floors_by_day: dict[date, float] = defaultdict(float)
    for floor in floors_climbed(start=start, end=end):
        floors_by_day[floor.timestamp.date()] += floor.floors
        all_dates.add(floor.timestamp.date())

    skin_by_day: dict[date, list[float]] = defaultdict(list)
    for skin in skin_temperature(start=start, end=end):
        skin_by_day[skin.timestamp.date()].append(skin.temperature)
        all_dates.add(skin.timestamp.date())

    snoring_by_day: dict[date, int] = defaultdict(int)
    for snore in snoring_records(start=start, end=end):
        snoring_by_day[snore.start.date()] += snore.duration_s
        all_dates.add(snore.start.date())

    vitality_by_day: dict[date, float] = {}
    for vitality in daily_vitality(start=start, end=end):
        if vitality.activity_score is not None:
            vitality_by_day[vitality.date] = vitality.activity_score
            all_dates.add(vitality.date)

    cal_by_day: dict[date, float] = {}
    for calories in calorie_burns(start=start, end=end):
        cal_by_day[calories.date] = calories.calories
        all_dates.add(calories.date)

    result: list[DailyHealthSummary] = []
    for d in sorted(all_dates):
        hr_vals = hr_by_day.get(d, [])
        stress_vals = stress_by_day.get(d, [])
        hrv_vals = hrv_by_day.get(d, [])
        spo2_vals = spo2_by_day.get(d, [])
        resp_vals = resp_by_day.get(d, [])
        skin_vals = skin_by_day.get(d, [])

        result.append(DailyHealthSummary(
            date=d,
            steps=steps_by_day.get(d),
            stress_avg=_avg(stress_vals),
            stress_count=len(stress_vals),
            heart_rate_avg=_avg(hr_vals),
            heart_rate_resting=min(hr_vals) if hr_vals else None,
            hrv_rmssd_avg=_avg(hrv_vals),
            hrv_count=len(hrv_vals),
            spo2_avg=_avg(spo2_vals),
            spo2_count=len(spo2_vals),
            respiratory_avg=_avg(resp_vals),
            respiratory_count=len(resp_vals),
            floors=floors_by_day.get(d) or None,
            skin_temp_avg=_avg(skin_vals),
            snoring_duration_s=snoring_by_day.get(d, 0),
            vitality_score=vitality_by_day.get(d),
            calories=cal_by_day.get(d),
        ))
    return result


def _avg(vals: list[int] | list[float]) -> Optional[float]:
    return sum(vals) / len(vals) if vals else None
