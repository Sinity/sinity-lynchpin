"""Samsung Health processed-export loaders."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.parse import parse_datetime as _parse_dt
from ..core.source import read_jsonl_with
from .health_models import (
    ActivityDaySummary,
    CalorieBurn,
    ECGMeasurement,
    FloorClimbed,
    HRVMeasurement,
    HeartRateMeasurement,
    MoodEntry,
    MovementRecord,
    NapSession,
    RespiratoryMeasurement,
    SkinTemperature,
    SnoringRecord,
    SpO2Measurement,
    StepDay,
    StressMeasurement,
    VitalityDay,
    WeightMeasurement,
)

__all__ = [
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
    "ecg_measurements",
    "activity_summaries",
    "movement_records",
    "calorie_burns",
    "nap_sessions",
]

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
            upper_limit=r.get("upper_limit"),
        ))
    return result


def ecg_measurements(*, start: Optional[date] = None, end: Optional[date] = None) -> list[ECGMeasurement]:
    """ECG readings from Samsung Health."""
    result = []
    for r in _load_jsonl("health_ecg.jsonl"):
        st = _parse_dt(r.get("start_time"))
        if st is None:
            continue
        if not _in_range(st.date(), start, end):
            continue
        result.append(ECGMeasurement(
            start=st,
            end=_parse_dt(r.get("end_time")),
            mean_heart_rate=r.get("mean_heart_rate"),
            sample_count=r.get("sample_count"),
            sample_frequency=r.get("sample_frequency"),
            data_key=r.get("data_key"),
            data_mime=r.get("data_mime"),
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


# ── Internal helpers (previously health_reader.py) ──────────────────────────

_PROCESSED = Path("/realm/data/exports/health/processed")


def load_jsonl(filename: str) -> Iterator[dict[str, Any]]:
    """Public, patchable entry point for loading a processed-health JSONL file."""
    yield from read_jsonl_with(_PROCESSED / filename, lambda p: p, source_name=filename)


def _load_jsonl(filename: str) -> Iterator[dict[str, Any]]:
    yield from load_jsonl(filename)


def _in_range(d: date, start: Optional[date], end: Optional[date]) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True
