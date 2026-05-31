"""Typed records emitted by the Samsung Health source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass(frozen=True)
class StepDay:
    date: date
    steps: int
    distance_m: Optional[float]
    speed_mps: Optional[float]

    def __post_init__(self) -> None:
        if self.steps < 0:
            raise ValueError(
                f"StepDay.steps ({self.steps}) must be >= 0"
            )


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

    def __post_init__(self) -> None:
        if not (1 <= self.mood_type <= 5):
            raise ValueError(
                f"MoodEntry.mood_type ({self.mood_type}) must be in [1, 5]"
            )


@dataclass(frozen=True)
class SnoringRecord:
    start: datetime
    end: datetime
    duration_s: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"SnoringRecord.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_s < 0:
            raise ValueError(
                f"SnoringRecord.duration_s ({self.duration_s}) must be >= 0"
            )


@dataclass(frozen=True)
class RespiratoryMeasurement:
    timestamp: datetime
    avg_rate: float
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None


@dataclass(frozen=True)
class ECGMeasurement:
    start: datetime
    end: Optional[datetime]
    mean_heart_rate: Optional[float] = None
    sample_count: Optional[int] = None
    sample_frequency: Optional[float] = None
    data_key: Optional[str] = None
    data_mime: Optional[str] = None


@dataclass(frozen=True)
class DailyStressSummary:
    date: date
    measurement_count: int
    avg_score: float
    min_score: int
    max_score: int

    def __post_init__(self) -> None:
        if self.measurement_count < 0:
            raise ValueError(
                f"DailyStressSummary.measurement_count ({self.measurement_count}) must be >= 0"
            )


@dataclass(frozen=True)
class DailyHeartRateSummary:
    date: date
    measurement_count: int
    avg_hr: float
    min_hr: float
    max_hr: float
    resting_hr: float

    def __post_init__(self) -> None:
        if self.measurement_count < 0:
            raise ValueError(
                f"DailyHeartRateSummary.measurement_count ({self.measurement_count}) must be >= 0"
            )
        if self.max_hr < self.min_hr:
            raise ValueError(
                f"DailyHeartRateSummary.max_hr ({self.max_hr}) is less than min_hr ({self.min_hr})"
            )


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

    def __post_init__(self) -> None:
        if self.active_time_min < 0:
            raise ValueError(
                f"ActivityDaySummary.active_time_min ({self.active_time_min}) must be >= 0"
            )


@dataclass(frozen=True)
class MovementRecord:
    start: datetime
    end: datetime
    movement_type: Optional[str] = None
    duration_min: float = 0.0

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"MovementRecord.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"MovementRecord.duration_min ({self.duration_min}) must be >= 0"
            )


@dataclass(frozen=True)
class CalorieBurn:
    date: date
    calories: float

    def __post_init__(self) -> None:
        if self.calories < 0:
            raise ValueError(
                f"CalorieBurn.calories ({self.calories}) must be >= 0"
            )


@dataclass(frozen=True)
class NapSession:
    start: datetime
    end: datetime
    duration_min: float
    before_vitality: Optional[float] = None
    after_vitality: Optional[float] = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"NapSession.end ({self.end}) precedes start ({self.start})"
            )
        if self.duration_min < 0:
            raise ValueError(
                f"NapSession.duration_min ({self.duration_min}) must be >= 0"
            )
