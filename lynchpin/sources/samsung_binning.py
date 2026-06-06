"""Per-minute / per-window Samsung Health binning data.

The processed JSONL files at /realm/data/exports/health/processed/health_stress.jsonl
and health_hrv.jsonl give 1-hour-window summaries. The raw GDPR export at
/realm/data/exports/samsung/processed/2026-03-30-gdpr-extracted/.../Stress Internal Data/
and Health HRV/ contains finer-grained binning_data fields:

  - Stress Internal Data: per-MINUTE stress scores (60s bins), 2022-08 onwards
  - Health HRV: per-30s rolling 5-min window sdnn/rmssd, 2025-05 onwards
  - Heart Rate: per-window HR readings, 2022-08 onwards (rich binning since ~2025)

This module exposes typed iterators for time-aligned analysis. Useful for:
  - Reverse-engineering Samsung's stress score formula (see analysis.health_modeling)
  - Cross-correlating with AW activity, polylogue, substance log, etc.
  - Back-projection of unmeasured signals where one stands in for another
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional
import csv
import json


SAMSUNG_GDPR_ROOT_DEFAULT = Path(
    "/realm/data/exports/samsung/processed/2026-03-30-gdpr-extracted/"
    "samsungcloud_gk000066110879_20260329_access"
)


def _gdpr_root() -> Path:
    """Locate the Samsung Cloud GDPR extracted directory.

    Override via env LYNCHPIN_SAMSUNG_GDPR_ROOT if needed.
    """
    import os
    env = os.environ.get("LYNCHPIN_SAMSUNG_GDPR_ROOT")
    if env:
        return Path(env)
    return SAMSUNG_GDPR_ROOT_DEFAULT


@dataclass(frozen=True)
class StressBin:
    """Per-minute stress score from Stress Internal Data binning_data."""
    ts: datetime          # bin start
    duration_s: int       # bin duration (typically 60s)
    score: float          # stress score 0-100
    score_min: float
    score_max: float
    flag: int             # Samsung-internal validity flag
    level: int            # Samsung-internal stress level


@dataclass(frozen=True)
class HRVBin:
    """Per-window HRV sdnn/rmssd from Health HRV binning_data."""
    ts: datetime
    end_ts: datetime
    sdnn: float
    rmssd: float


@dataclass(frozen=True)
class HRBin:
    """Per-window heart rate from Heart Rate binning_data (when available)."""
    ts: datetime
    heart_rate: float
    heart_rate_min: Optional[float] = None
    heart_rate_max: Optional[float] = None


def _parse_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def iter_stress_bins(
    root: Optional[Path] = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[StressBin]:
    """Yield per-minute stress bins from Stress Internal Data CSVs.

    Coverage: 2022-08-30 → 2026-03-29 (varies by export).
    Roughly 640k bins per typical export.
    """
    base = (root or _gdpr_root()) / "Stress Internal Data"
    if not base.exists():
        return
    for fn in sorted(base.glob("Stress Internal Data*.csv")):
        with open(fn, newline="") as f:
            for row in csv.DictReader(f):
                bd_raw = row.get("binning_data", "")
                if not bd_raw:
                    continue
                try:
                    bd = json.loads(bd_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                for b in bd:
                    st = b.get("start_time")
                    et = b.get("end_time")
                    sc = b.get("score")
                    if st is None or sc is None:
                        continue
                    ts = _parse_ms(st)
                    if start is not None and ts < start:
                        continue
                    if end is not None and ts >= end:
                        continue
                    duration = int((et - st) / 1000) if et else 60
                    yield StressBin(
                        ts=ts,
                        duration_s=duration,
                        score=float(sc),
                        score_min=float(b.get("score_min", sc)),
                        score_max=float(b.get("score_max", sc)),
                        flag=int(b.get("flag", 1)),
                        level=int(b.get("level", 1)),
                    )


def iter_hrv_bins(
    root: Optional[Path] = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[HRVBin]:
    """Yield per-window HRV bins (~5min rolling, 30s step).

    Coverage: 2025-05-21 onwards (post Galaxy Watch firmware update that
    enabled raw HRV export — see raw-log 2025-05-24 entry).
    """
    base = (root or _gdpr_root()) / "Health HRV"
    if not base.exists():
        return
    for fn in sorted(base.glob("Health HRV*.csv")):
        with open(fn, newline="") as f:
            for row in csv.DictReader(f):
                bd_raw = row.get("binning_data", "")
                if not bd_raw:
                    continue
                try:
                    bd = json.loads(bd_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                for b in bd:
                    s = b.get("start_time")
                    e = b.get("end_time")
                    sd = b.get("sdnn")
                    rm = b.get("rmssd")
                    if None in (s, e, sd, rm):
                        continue
                    ts = _parse_ms(s)
                    if start is not None and ts < start:
                        continue
                    if end is not None and ts >= end:
                        continue
                    yield HRVBin(
                        ts=ts,
                        end_ts=_parse_ms(e),
                        sdnn=float(sd),
                        rmssd=float(rm),
                    )


def iter_hr_bins(
    root: Optional[Path] = None,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Iterator[HRBin]:
    """Yield per-window heart-rate bins.

    Coverage: 2022-08 onwards. Rich binning_data with min/max appears
    from ~2025-03 onwards; before that bins have heart_rate only.
    """
    base = (root or _gdpr_root()) / "Heart Rate"
    if not base.exists():
        return
    for fn in sorted(base.glob("Heart Rate*.csv")):
        with open(fn, newline="") as f:
            for row in csv.DictReader(f):
                bd_raw = row.get("binning_data", "")
                if not bd_raw:
                    continue
                try:
                    bd = json.loads(bd_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                for b in bd:
                    hr = b.get("heart_rate")
                    st = b.get("start_time") or b.get("end_time")
                    if hr is None or st is None:
                        continue
                    ts = _parse_ms(st)
                    if start is not None and ts < start:
                        continue
                    if end is not None and ts >= end:
                        continue
                    yield HRBin(
                        ts=ts,
                        heart_rate=float(hr),
                        heart_rate_min=float(b["heart_rate_min"]) if b.get("heart_rate_min") is not None else None,
                        heart_rate_max=float(b["heart_rate_max"]) if b.get("heart_rate_max") is not None else None,
                    )


__all__ = [
    "StressBin",
    "HRVBin",
    "HRBin",
    "iter_stress_bins",
    "iter_hrv_bins",
    "iter_hr_bins",
]
