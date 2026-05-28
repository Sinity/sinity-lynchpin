"""Sleep source: Samsung Health + Sleep As Android → sleep entries + quality + productivity correlation.

Absorbs: exports/health, exports/sleep, processed/sleep_correlation, metrics/health.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import get_config
from ..core.parse import parse_datetime as _parse_dt, safe_float as _safe_float
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with

__all__ = [
    "SleepSegment",
    "SleepMetrics",
    "SleepEntry",
    "SleepStageRecord",
    "SleepArchitecture",
    "SleepProductivity",
    "entries",
    "sleep_for_date",
    "entries_in_range",
    "sleep_stages",
    "sleep_architecture",
    "sleep_productivity",
]

# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SleepSegment:
    start: datetime
    end: datetime
    duration_minutes: float
    score: Optional[float]
    device: Optional[str]
    comment: Optional[str]


@dataclass(frozen=True)
class SleepMetrics:
    sleep_score: Optional[float]
    sleep_duration: Optional[float]
    sleep_efficiency: Optional[float]
    sleep_cycle: Optional[float]
    physical_recovery: Optional[float]
    mental_recovery: Optional[float]
    movement_awakening: Optional[float]
    total_awake_duration: Optional[float]
    total_light_duration: Optional[float]
    total_deep_duration: Optional[float]
    total_rem_duration: Optional[float]
    awake_pct: Optional[float]
    light_pct: Optional[float]
    deep_pct: Optional[float]
    rem_pct: Optional[float]
    stage_count: Optional[int]


@dataclass(frozen=True)
class SleepEntry:
    date: date
    total_minutes: float
    segments: tuple[SleepSegment, ...]
    avg_score: Optional[float]
    metrics: Optional[SleepMetrics] = None
    source: Optional[str] = None  # 'merged' | 'combined_only' | 'saa_only' | 'samsung_only' | 'stage_derived'

    @property
    def quality_label(self) -> str:
        if self.avg_score is None:
            return "unknown"
        if self.avg_score >= 80:
            return "good"
        if self.avg_score >= 60:
            return "fair"
        return "poor"


@dataclass(frozen=True)
class SleepStageRecord:
    start: datetime
    end: datetime
    stage: str  # "awake", "light", "deep", "rem"
    sleep_id: str
    duration_min: float


@dataclass(frozen=True)
class SleepArchitecture:
    """Per-night sleep stage breakdown."""
    date: date
    sleep_id: str
    total_min: float
    awake_min: float
    light_min: float
    deep_min: float
    rem_min: float
    awake_pct: float
    light_pct: float
    deep_pct: float
    rem_pct: float
    stage_transitions: int
    first_rem_min: Optional[float] = None  # minutes from sleep onset to first REM


_PROCESSED = Path("/realm/data/exports/health/processed")


def _load_jsonl(filename: str) -> Iterator[dict[str, object]]:
    yield from read_jsonl_with(_PROCESSED / filename, lambda p: p, source_name=filename)


def _in_range(d: date, start: Optional[date], end: Optional[date]) -> bool:
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Raw access: merged sleep JSONL
# ══════════════════════════════════════════════════════════════════════════════


def _hydrate_entry(rec: dict[str, Any]) -> SleepEntry | None:
    metrics = rec.get("sleep_metrics")
    if not isinstance(metrics, dict):
        return None

    start_dt = _parse_dt(rec.get("start_local"))
    end_dt = _parse_dt(rec.get("end_local"))

    total_min = float(metrics.get("sleep_duration") or 0)
    if total_min == 0 and start_dt and end_dt:
        total_min = max((end_dt - start_dt).total_seconds() / 60, 0)

    score = _safe_float(metrics.get("sleep_score"))

    if start_dt is None:
        return None
    d = start_dt.date()

    # Build segments (Samsung format has one implicit segment per record)
    segments: list[SleepSegment] = []
    raw_segments = rec.get("segments") or []
    if raw_segments:
        for seg in raw_segments:
            segments.append(SleepSegment(
                start=_parse_dt(seg.get("start")) or datetime.min,
                end=_parse_dt(seg.get("end")) or datetime.min,
                duration_minutes=float(seg.get("duration_minutes") or 0),
                score=_safe_float(seg.get("score")),
                device=seg.get("device") or rec.get("device_name"),
                comment=seg.get("comment"),
            ))
    else:
        segments.append(SleepSegment(
            start=start_dt or datetime.min,
            end=end_dt or datetime.min,
            duration_minutes=total_min,
            score=score,
            device=rec.get("device_name"),
            comment=None,
        ))

    if not segments:
        return None
    scores = [s.score for s in segments if s.score is not None]
    avg = sum(scores) / len(scores) if scores else score
    sleep_metrics = SleepMetrics(
        sleep_score=score,
        sleep_duration=_safe_float(metrics.get("sleep_duration")),
        sleep_efficiency=_safe_float(metrics.get("sleep_efficiency")),
        sleep_cycle=_safe_float(metrics.get("sleep_cycle")),
        physical_recovery=_safe_float(metrics.get("physical_recovery")),
        mental_recovery=_safe_float(metrics.get("mental_recovery")),
        movement_awakening=_safe_float(metrics.get("movement_awakening")),
        total_awake_duration=_safe_float(metrics.get("total_awake_duration")),
        total_light_duration=_safe_float(metrics.get("total_light_duration")),
        total_deep_duration=_safe_float(metrics.get("total_deep_duration")),
        total_rem_duration=_safe_float(metrics.get("total_rem_duration")),
        awake_pct=_safe_float(metrics.get("awake_pct")),
        light_pct=_safe_float(metrics.get("light_pct")),
        deep_pct=_safe_float(metrics.get("deep_pct")),
        rem_pct=_safe_float(metrics.get("rem_pct")),
        stage_count=rec.get("stage_count"),
    )
    return SleepEntry(
        date=d, total_minutes=total_min, segments=tuple(segments),
        avg_score=avg, metrics=sleep_metrics,
        source=rec.get("source") if isinstance(rec.get("source"), str) else None,
    )


# Source priority for picking one canonical entry per date.
# 'merged' is the most authoritative (paired SAA+SH), 'stage_derived' the least
# (raw from individual stage events; multiple per night common).
_SOURCE_PRIORITY = {
    "merged": 0,
    "combined_only": 1,
    "saa_only": 2,
    "samsung_only": 3,
    "stage_derived": 4,
    None: 5,
}


def entries() -> Iterator[SleepEntry]:
    """Yield every sleep row from sleep_merged.jsonl, including same-date duplicates.

    The merged JSONL contains multiple rows per date by design (SAA + SH +
    per-stage derivations). Use ``canonical_entries`` to collapse to one per
    date.
    """
    cfg = get_config()
    yield from read_jsonl_with(cfg.sleep_jsonl, _hydrate_entry, source_name="sleep_merged")


def canonical_entries() -> Iterator[SleepEntry]:
    """Yield one representative SleepEntry per date.

    Picks the highest-priority source for each date (``merged`` > ``combined_only``
    > ``saa_only`` > ``samsung_only`` > ``stage_derived``). Ties broken by
    longest ``total_minutes``. Resolves the 56% same-date duplication observed
    in 2017-2025 sleep records caused by overlapping device exports.
    """
    by_date: dict[date, SleepEntry] = {}
    for e in entries():
        prev = by_date.get(e.date)
        if prev is None:
            by_date[e.date] = e
            continue
        prio_new = _SOURCE_PRIORITY.get(e.source, 5)
        prio_old = _SOURCE_PRIORITY.get(prev.source, 5)
        if prio_new < prio_old:
            by_date[e.date] = e
        elif prio_new == prio_old and e.total_minutes > prev.total_minutes:
            by_date[e.date] = e
    for d in sorted(by_date.keys()):
        yield by_date[d]


def sleep_for_date(target: date) -> Optional[SleepEntry]:
    """Return the canonical (one-per-date) sleep entry for ``target``."""
    return next((e for e in canonical_entries() if e.date == target), None)


def entries_in_range(start: date, end: date, *, canonical: bool = True) -> list[SleepEntry]:
    """List sleep entries between ``start`` and ``end`` inclusive.

    Default ``canonical=True`` returns one entry per date. Set ``canonical=False``
    to get the raw multi-row stream (useful for cross-device comparison).
    """
    source = canonical_entries() if canonical else entries()
    return [e for e in source if start <= e.date <= end]


@dataclass(frozen=True)
class SleepDayActivity:
    date: date
    total_hours: Optional[float] = None
    score: Optional[float] = None
    quality: Optional[str] = None
    deep_sleep_hours: Optional[float] = None
    rem_hours: Optional[float] = None
    light_sleep_hours: Optional[float] = None
    awake_hours: Optional[float] = None
    hr_min_bpm: Optional[float] = None
    hr_max_bpm: Optional[float] = None
    hr_avg_bpm: Optional[float] = None
    respiratory_rate: Optional[float] = None
    snoring_seconds: Optional[float] = None
    skin_temp_c: Optional[float] = None


def daily_activity(*, start: date, end: date) -> list[SleepDayActivity]:
    """Per-day sleep activity summary."""
    result: list[SleepDayActivity] = []
    for entry in entries_in_range(start, end, canonical=True):
        result.append(SleepDayActivity(
            date=entry.date,
            total_hours=round(entry.total_minutes / 60, 2) if entry.total_minutes else None,
            score=round(entry.avg_score, 2) if entry.avg_score else None,
            quality=entry.quality_label,
        ))
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Sleep stage analysis
# ══════════════════════════════════════════════════════════════════════════════


def sleep_stages(*, start: Optional[date] = None, end: Optional[date] = None) -> list[SleepStageRecord]:
    """Sleep stage records from Samsung Health GDPR export."""
    result: list[SleepStageRecord] = []
    for r in _load_jsonl("health_sleep_stages.jsonl"):
        st = _parse_dt(r.get("start_time"))
        et = _parse_dt(r.get("end_time"))
        if st is None or et is None:
            continue
        if not _in_range(st.date(), start, end):
            continue
        stage = r.get("stage")
        sleep_id = r.get("sleep_id")
        if not stage or not sleep_id:
            continue
        dur = r.get("duration_minutes")
        duration_min = _safe_float(dur)
        result.append(SleepStageRecord(
            start=st,
            end=et,
            stage=str(stage),
            sleep_id=str(sleep_id),
            duration_min=duration_min if duration_min is not None else max((et - st).total_seconds() / 60, 0),
        ))
    return result


def sleep_architecture(*, start: Optional[date] = None, end: Optional[date] = None) -> list[SleepArchitecture]:
    """Per-night sleep stage architecture from Samsung Health.

    Groups stage records by sleep_id, computes duration breakdown, percentages,
    stage transition count, and time-to-first-REM.
    """
    stage_start = start - timedelta(days=1) if start else None
    stage_end = end + timedelta(days=1) if end else None
    stages = sleep_stages(start=stage_start, end=stage_end)
    if not stages:
        return []

    # Group by sleep_id
    by_id: dict[str, list[SleepStageRecord]] = defaultdict(list)
    for s in stages:
        by_id[s.sleep_id].append(s)

    result = []
    for sleep_id, records in by_id.items():
        # Sort by start time
        records.sort(key=lambda r: r.start)

        # Sum durations by stage
        stage_min: dict[str, float] = defaultdict(float)
        for r in records:
            stage_min[r.stage] += r.duration_min

        awake = stage_min.get("awake", 0.0)
        light = stage_min.get("light", 0.0)
        deep = stage_min.get("deep", 0.0)
        rem = stage_min.get("rem", 0.0)
        total = awake + light + deep + rem

        if total <= 0:
            continue

        # Count stage transitions
        transitions = 0
        for i in range(1, len(records)):
            if records[i].stage != records[i - 1].stage:
                transitions += 1

        # Time to first REM (minutes from sleep onset)
        onset = records[0].start
        first_rem_min: Optional[float] = None
        for r in records:
            if r.stage == "rem":
                first_rem_min = max((r.start - onset).total_seconds() / 60, 0)
                break

        d = logical_date(records[0].start)
        if not _in_range(d, start, end):
            continue

        result.append(SleepArchitecture(
            date=d,
            sleep_id=sleep_id,
            total_min=round(total, 1),
            awake_min=round(awake, 1),
            light_min=round(light, 1),
            deep_min=round(deep, 1),
            rem_min=round(rem, 1),
            awake_pct=round(awake / total * 100, 1),
            light_pct=round(light / total * 100, 1),
            deep_pct=round(deep / total * 100, 1),
            rem_pct=round(rem / total * 100, 1),
            stage_transitions=transitions,
            first_rem_min=round(first_rem_min, 1) if first_rem_min is not None else None,
        ))

    result.sort(key=lambda a: a.date)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Sleep–productivity correlation
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SleepProductivity:
    sleep_date: date
    sleep_hours: float
    sleep_score: Optional[float]
    sleep_quality: str
    workday_active_hours: float
    workday_deep_work_min: float
    productivity_vs_baseline: float


def sleep_productivity(*, start: date, end: date) -> list[SleepProductivity]:
    """Join sleep data with next-day AW active hours and deep work. Lazy import to avoid circular."""
    sleep_data = entries_in_range(start, end)
    if not sleep_data:
        return []

    # Lazy import — AW is a peer source, not a dependency at module level
    from .activitywatch import active_seconds_by_date, deep_work
    from datetime import timedelta

    aw_start = min(e.date for e in sleep_data) + timedelta(days=1)
    aw_end = max(e.date for e in sleep_data) + timedelta(days=1)
    active_map = active_seconds_by_date(aw_start, aw_end)

    from datetime import time as time_cls
    dw_blocks = deep_work(start=datetime.combine(aw_start, time_cls.min), end=datetime.combine(aw_end + timedelta(days=1), time_cls.min))
    dw_by_day: dict[date, float] = {}
    for b in dw_blocks:
        dw_by_day[b.start.date()] = dw_by_day.get(b.start.date(), 0) + b.duration_min

    baseline_hours = sum(active_map.values()) / max(len(active_map), 1) / 3600 if active_map else 0

    result: list[SleepProductivity] = []
    for entry in sleep_data:
        workday = entry.date + timedelta(days=1)
        active_h = active_map.get(workday, 0) / 3600
        dw_min = dw_by_day.get(workday, 0)
        vs_baseline = active_h / baseline_hours if baseline_hours > 0 else 0
        result.append(SleepProductivity(
            sleep_date=entry.date, sleep_hours=round(entry.total_minutes / 60, 2),
            sleep_score=entry.avg_score, sleep_quality=entry.quality_label,
            workday_active_hours=round(active_h, 2),
            workday_deep_work_min=round(dw_min, 1),
            productivity_vs_baseline=round(vs_baseline, 2),
        ))
    return result
