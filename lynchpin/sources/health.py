"""Samsung Health source: steps, stress, HRV, vitality, weight, respiratory rate, SpO2,
heart rate, skin temperature, floors, mood, snoring, and daily aggregates.

Reads from processed JSONL files under /realm/data/exports/health/processed/.
Run `python -m lynchpin.scripts.process_health` to refresh from raw exports.

Sleep data is in the separate `sleep` module (richer, with SAA fusion + AW inference).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from ..core.parse import parse_datetime as _parse_dt

__all__ = [
    # Data types
    "StepDay",
    "StressMeasurement",
    "HRVMeasurement",
    "VitalityDay",
    "HeartRateMeasurement",
    "SpO2Measurement",
    "WeightMeasurement",
    "SkinTemperature",
    "FloorClimbed",
    "MoodEntry",
    "SnoringRecord",
    "RespiratoryMeasurement",
    "DailyStressSummary",
    "DailyHeartRateSummary",
    "DailyHealthSummary",
    "ActivityDaySummary",
    "MovementRecord",
    "CalorieBurn",
    "NapSession",
    # Loaders
    "daily_steps",
    "stress_measurements",
    "hrv_measurements",
    "daily_vitality",
    "heart_rate_measurements",
    "spo2_measurements",
    "weight_measurements",
    "skin_temperature",
    "floors_climbed",
    "mood_entries",
    "snoring_records",
    "respiratory_rate",
    "activity_summaries",
    "movement_records",
    "calorie_burns",
    "nap_sessions",
    "daily_stress",
    "daily_heart_rate",
    "daily_health_summary",
]

_PROCESSED = Path("/realm/data/exports/health/processed")


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StepDay:
    date: date
    steps: int
    distance_m: Optional[float]
    speed_mps: Optional[float]


@dataclass(frozen=True)
class StressMeasurement:
    timestamp: datetime
    score: Optional[int]


@dataclass(frozen=True)
class HRVMeasurement:
    timestamp: datetime
    sdnn_avg: Optional[float] = None
    rmssd_avg: Optional[float] = None
    n_windows: Optional[int] = None


@dataclass(frozen=True)
class VitalityDay:
    date: date
    activity_score: Optional[float]
    activity_level: str


@dataclass(frozen=True)
class HeartRateMeasurement:
    timestamp: datetime
    heart_rate: float
    min: Optional[float] = None
    max: Optional[float] = None


@dataclass(frozen=True)
class SpO2Measurement:
    timestamp: datetime
    spo2: float
    min: Optional[float] = None
    max: Optional[float] = None
    low_duration: Optional[float] = None


@dataclass(frozen=True)
class WeightMeasurement:
    date: date
    weight_kg: float
    body_fat_pct: Optional[float] = None
    muscle_mass_kg: Optional[float] = None
    skeletal_muscle_pct: Optional[float] = None
    basal_metabolic_rate: Optional[float] = None
    body_fat_mass_kg: Optional[float] = None
    total_body_water_pct: Optional[float] = None


@dataclass(frozen=True)
class SkinTemperature:
    timestamp: datetime
    temperature: float
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None


@dataclass(frozen=True)
class FloorClimbed:
    timestamp: datetime
    floors: float


@dataclass(frozen=True)
class MoodEntry:
    timestamp: datetime
    mood_type: int


@dataclass(frozen=True)
class SnoringRecord:
    start: datetime
    end: datetime
    duration_s: int


@dataclass(frozen=True)
class RespiratoryMeasurement:
    timestamp: datetime
    avg_rate: float
    lower_limit: Optional[float] = None


@dataclass(frozen=True)
class DailyStressSummary:
    date: date
    measurement_count: int
    avg_score: float
    min_score: int
    max_score: int


@dataclass(frozen=True)
class DailyHeartRateSummary:
    date: date
    measurement_count: int
    avg_hr: float
    min_hr: float
    max_hr: float
    resting_hr: float


@dataclass(frozen=True)
class DailyHealthSummary:
    date: date
    steps: Optional[int] = None
    stress_avg: Optional[float] = None
    stress_count: int = 0
    heart_rate_avg: Optional[float] = None
    heart_rate_resting: Optional[float] = None
    hrv_rmssd_avg: Optional[float] = None
    hrv_count: int = 0
    spo2_avg: Optional[float] = None
    spo2_count: int = 0
    respiratory_avg: Optional[float] = None
    respiratory_count: int = 0
    floors: Optional[float] = None
    skin_temp_avg: Optional[float] = None
    snoring_duration_s: int = 0
    vitality_score: Optional[float] = None
    calories: Optional[float] = None


@dataclass(frozen=True)
class ActivityDaySummary:
    date: date
    active_time_min: float
    calories: Optional[float] = None
    step_count: Optional[int] = None


@dataclass(frozen=True)
class MovementRecord:
    start: datetime
    end: datetime
    movement_type: Optional[str] = None
    duration_min: float = 0.0


@dataclass(frozen=True)
class CalorieBurn:
    date: date
    calories: float


@dataclass(frozen=True)
class NapSession:
    start: datetime
    end: datetime
    duration_min: float
    before_vitality: Optional[float] = None
    after_vitality: Optional[float] = None


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def _load_jsonl(filename: str):
    path = _PROCESSED / filename
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _in_range(d: date, start: Optional[date], end: Optional[date]) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Loaders — existing
# ══════════════════════════════════════════════════════════════════════════════


def daily_steps(*, start: Optional[date] = None, end: Optional[date] = None) -> list[StepDay]:
    """Daily step counts from Samsung Health."""
    result = []
    for r in _load_jsonl("health_steps.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if not _in_range(d_date, start, end):
            continue
        result.append(StepDay(
            date=d_date,
            steps=r.get("steps", 0),
            distance_m=r.get("distance_m"),
            speed_mps=r.get("speed_mps"),
        ))
    return result


def stress_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[StressMeasurement]:
    """Stress score measurements from Samsung Health."""
    result = []
    for r in _load_jsonl("health_stress.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        result.append(StressMeasurement(timestamp=ts, score=r.get("score")))
    return result


def hrv_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[HRVMeasurement]:
    """Heart rate variability measurements from Samsung Health."""
    result = []
    for r in _load_jsonl("health_hrv.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        result.append(HRVMeasurement(
            timestamp=ts,
            sdnn_avg=r.get("sdnn_avg"),
            rmssd_avg=r.get("rmssd_avg"),
            n_windows=r.get("n_windows"),
        ))
    return result


def daily_vitality(*, start: Optional[date] = None, end: Optional[date] = None) -> list[VitalityDay]:
    """Daily vitality/activity scores from Samsung Health."""
    result = []
    for r in _load_jsonl("health_vitality.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if not _in_range(d_date, start, end):
            continue
        result.append(VitalityDay(
            date=d_date,
            activity_score=r.get("activity_score"),
            activity_level=r.get("activity_level", ""),
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Loaders — new raw signals
# ══════════════════════════════════════════════════════════════════════════════


def heart_rate_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[HeartRateMeasurement]:
    """Heart rate measurements from Samsung Health (hourly bins)."""
    result = []
    for r in _load_jsonl("health_heart_rate.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        hr = r.get("heart_rate")
        if hr is None:
            continue
        result.append(HeartRateMeasurement(
            timestamp=ts,
            heart_rate=float(hr),
            min=r.get("min"),
            max=r.get("max"),
        ))
    return result


def spo2_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[SpO2Measurement]:
    """Blood oxygen (SpO2) measurements from Samsung Health."""
    result = []
    for r in _load_jsonl("health_spo2.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        spo2 = r.get("spo2")
        if spo2 is None:
            continue
        result.append(SpO2Measurement(
            timestamp=ts,
            spo2=float(spo2),
            min=r.get("min"),
            max=r.get("max"),
            low_duration=r.get("low_duration"),
        ))
    return result


def weight_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[WeightMeasurement]:
    """Weight and body composition from Samsung Health."""
    result = []
    for r in _load_jsonl("health_weight.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        d = ts.date()
        if not _in_range(d, start, end):
            continue
        w = r.get("weight_kg")
        if w is None:
            continue
        result.append(WeightMeasurement(
            date=d,
            weight_kg=float(w),
            body_fat_pct=r.get("body_fat_pct"),
            muscle_mass_kg=r.get("muscle_mass_kg"),
            skeletal_muscle_pct=r.get("skeletal_muscle_pct"),
            basal_metabolic_rate=r.get("basal_metabolic_rate"),
            body_fat_mass_kg=r.get("body_fat_mass_kg"),
            total_body_water_pct=r.get("total_body_water_pct"),
        ))
    return result


def skin_temperature(*, start: Optional[date] = None, end: Optional[date] = None) -> list[SkinTemperature]:
    """Skin temperature readings from Samsung Health."""
    result = []
    for r in _load_jsonl("health_skin_temperature.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        temp = r.get("temperature")
        if temp is None:
            continue
        result.append(SkinTemperature(
            timestamp=ts,
            temperature=float(temp),
            min_temp=r.get("min"),
            max_temp=r.get("max"),
        ))
    return result


def floors_climbed(*, start: Optional[date] = None, end: Optional[date] = None) -> list[FloorClimbed]:
    """Floor climbing events from Samsung Health."""
    result = []
    for r in _load_jsonl("health_floors.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        fl = r.get("floor")
        if fl is None:
            continue
        result.append(FloorClimbed(
            timestamp=ts,
            floors=float(fl),
        ))
    return result


def mood_entries(*, start: Optional[date] = None, end: Optional[date] = None) -> list[MoodEntry]:
    """Mood log entries from Samsung Health."""
    result = []
    for r in _load_jsonl("health_mood.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        mt = r.get("mood_type")
        if mt is None:
            continue
        result.append(MoodEntry(
            timestamp=ts,
            mood_type=int(mt),
        ))
    return result


def snoring_records(*, start: Optional[date] = None, end: Optional[date] = None) -> list[SnoringRecord]:
    """Snoring tracking records from Samsung Health."""
    result = []
    for r in _load_jsonl("health_snoring.jsonl"):
        st = _parse_dt(r.get("start_time"))
        et = _parse_dt(r.get("end_time"))
        if st is None or et is None:
            continue
        if not _in_range(st.date(), start, end):
            continue
        result.append(SnoringRecord(
            start=st,
            end=et,
            duration_s=int(r.get("duration", 0)),
        ))
    return result


def respiratory_rate(*, start: Optional[date] = None, end: Optional[date] = None) -> list[RespiratoryMeasurement]:
    """Respiratory rate measurements from Samsung Health."""
    result = []
    for r in _load_jsonl("health_respiratory.jsonl"):
        ts = _parse_dt(r.get("start_time"))
        if ts is None:
            continue
        if not _in_range(ts.date(), start, end):
            continue
        avg = r.get("avg_rate")
        if avg is None:
            continue
        result.append(RespiratoryMeasurement(
            timestamp=ts,
            avg_rate=float(avg),
            lower_limit=r.get("lower_limit"),
        ))
    return result


def activity_summaries(*, start: Optional[date] = None, end: Optional[date] = None) -> list[ActivityDaySummary]:
    """Daily activity summary from Samsung Health."""
    result = []
    for r in _load_jsonl("health_activity_summary.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if not _in_range(d_date, start, end):
            continue
        active_ms = r.get("active_time_ms", 0)
        result.append(ActivityDaySummary(
            date=d_date,
            active_time_min=float(active_ms) / 60_000 if active_ms else 0.0,
            calories=r.get("calories"),
            step_count=r.get("step_count"),
        ))
    return result


def movement_records(*, start: Optional[date] = None, end: Optional[date] = None) -> list[MovementRecord]:
    """Movement events from Samsung Health."""
    result = []
    for r in _load_jsonl("health_movement.jsonl"):
        st = _parse_dt(r.get("start_time"))
        et = _parse_dt(r.get("end_time"))
        if st is None or et is None:
            continue
        if not _in_range(st.date(), start, end):
            continue
        dur_ms = r.get("duration_ms", 0)
        result.append(MovementRecord(
            start=st,
            end=et,
            movement_type=r.get("movement_type"),
            duration_min=float(dur_ms) / 60_000 if dur_ms else 0.0,
        ))
    return result


def calorie_burns(*, start: Optional[date] = None, end: Optional[date] = None) -> list[CalorieBurn]:
    """Daily calorie burn totals from Samsung Health."""
    result = []
    for r in _load_jsonl("health_calories.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if not _in_range(d_date, start, end):
            continue
        active = r.get("active_calorie") or 0
        rest = r.get("rest_calorie") or 0
        tef = r.get("tef_calorie") or 0
        cal = float(active) + float(rest) + float(tef)
        if cal <= 0:
            continue
        result.append(CalorieBurn(date=d_date, calories=round(cal, 1)))
    return result


def nap_sessions(*, start: Optional[date] = None, end: Optional[date] = None) -> list[NapSession]:
    """Nap sessions from Samsung Health."""
    result = []
    for r in _load_jsonl("health_naps.jsonl"):
        st = _parse_dt(r.get("start_time"))
        et = _parse_dt(r.get("end_time"))
        if st is None or et is None:
            continue
        if not _in_range(st.date(), start, end):
            continue
        dur = r.get("duration_min")
        if dur is None:
            continue
        result.append(NapSession(
            start=st,
            end=et,
            duration_min=float(dur),
            before_vitality=r.get("before_vitality"),
            after_vitality=r.get("after_vitality"),
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Loaders — daily aggregates
# ══════════════════════════════════════════════════════════════════════════════


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
    """Combine ALL health signals into one record per day."""
    # Collect all dates that have any data
    all_dates: set[date] = set()

    # Steps (date-keyed already)
    steps_by_day: dict[date, int] = {}
    for s in daily_steps(start=start, end=end):
        steps_by_day[s.date] = s.steps
        all_dates.add(s.date)

    # Stress
    stress_by_day: dict[date, list[int]] = defaultdict(list)
    for m in stress_measurements(start=start, end=end):
        if m.score is not None:
            stress_by_day[m.timestamp.date()].append(m.score)
            all_dates.add(m.timestamp.date())

    # Heart rate
    hr_by_day: dict[date, list[float]] = defaultdict(list)
    for m in heart_rate_measurements(start=start, end=end):
        hr_by_day[m.timestamp.date()].append(m.heart_rate)
        all_dates.add(m.timestamp.date())

    # HRV
    hrv_by_day: dict[date, list[float]] = defaultdict(list)
    for m in hrv_measurements(start=start, end=end):
        if m.rmssd_avg is not None:
            hrv_by_day[m.timestamp.date()].append(m.rmssd_avg)
            all_dates.add(m.timestamp.date())

    # SpO2
    spo2_by_day: dict[date, list[float]] = defaultdict(list)
    for m in spo2_measurements(start=start, end=end):
        spo2_by_day[m.timestamp.date()].append(m.spo2)
        all_dates.add(m.timestamp.date())

    # Respiratory
    resp_by_day: dict[date, list[float]] = defaultdict(list)
    for m in respiratory_rate(start=start, end=end):
        resp_by_day[m.timestamp.date()].append(m.avg_rate)
        all_dates.add(m.timestamp.date())

    # Floors
    floors_by_day: dict[date, float] = defaultdict(float)
    for m in floors_climbed(start=start, end=end):
        floors_by_day[m.timestamp.date()] += m.floors
        all_dates.add(m.timestamp.date())

    # Skin temperature
    skin_by_day: dict[date, list[float]] = defaultdict(list)
    for m in skin_temperature(start=start, end=end):
        skin_by_day[m.timestamp.date()].append(m.temperature)
        all_dates.add(m.timestamp.date())

    # Snoring
    snoring_by_day: dict[date, int] = defaultdict(int)
    for m in snoring_records(start=start, end=end):
        snoring_by_day[m.start.date()] += m.duration_s
        all_dates.add(m.start.date())

    # Vitality
    vitality_by_day: dict[date, float] = {}
    for v in daily_vitality(start=start, end=end):
        if v.activity_score is not None:
            vitality_by_day[v.date] = v.activity_score
            all_dates.add(v.date)

    # Calories
    cal_by_day: dict[date, float] = {}
    for c in calorie_burns(start=start, end=end):
        cal_by_day[c.date] = c.calories
        all_dates.add(c.date)

    def _avg(vals: list) -> Optional[float]:
        return sum(vals) / len(vals) if vals else None

    result = []
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
