"""Samsung Health source: steps, stress, HRV, vitality, weight, respiratory rate, SpO2.

Reads from processed JSONL files under /realm/data/exports/health/processed/.
Run `python -m lynchpin.scripts.process_health` to refresh from raw exports.

Sleep data is in the separate `sleep` module (richer, with SAA fusion + AW inference).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.parse import parse_datetime as _parse_dt

__all__ = [
    "StepDay",
    "StressMeasurement",
    "HRVMeasurement",
    "VitalityDay",
    "daily_steps",
    "stress_measurements",
    "hrv_measurements",
    "daily_vitality",
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


@dataclass(frozen=True)
class VitalityDay:
    date: date
    activity_score: Optional[float]
    activity_level: str


# ══════════════════════════════════════════════════════════════════════════════
# Loaders
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


def daily_steps(*, start: Optional[date] = None, end: Optional[date] = None) -> list[StepDay]:
    """Daily step counts from Samsung Health."""
    result = []
    for r in _load_jsonl("health_steps.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if start and d_date < start:
            continue
        if end and d_date > end:
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
        d = ts.date()
        if start and d < start:
            continue
        if end and d > end:
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
        d = ts.date()
        if start and d < start:
            continue
        if end and d > end:
            continue
        result.append(HRVMeasurement(timestamp=ts))
    return result


def daily_vitality(*, start: Optional[date] = None, end: Optional[date] = None) -> list[VitalityDay]:
    """Daily vitality/activity scores from Samsung Health."""
    result = []
    for r in _load_jsonl("health_vitality.jsonl"):
        d = r.get("date")
        if not d or d < "2000":
            continue
        d_date = date.fromisoformat(d)
        if start and d_date < start:
            continue
        if end and d_date > end:
            continue
        result.append(VitalityDay(
            date=d_date,
            activity_score=r.get("activity_score"),
            activity_level=r.get("activity_level", ""),
        ))
    return result
