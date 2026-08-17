"""Sleep source: Samsung Health + Sleep As Android → sleep entries + quality + productivity correlation.

Absorbs: exports/health, exports/sleep, processed/sleep_correlation, metrics/health.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import get_config
from ..core.coverage import CoverageBounds
from ..core.parse import parse_datetime as _parse_dt, safe_float as _safe_float, in_date_range
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with

__all__ = [
    "SleepSegment",
    "SleepMetrics",
    "NightSignals",
    "SleepEntry",
    "SleepStageRecord",
    "SleepArchitecture",
    "SleepProductivity",
    "entries",
    "sleep_for_date",
    "entries_in_range",
    "sleep_stages",
    "sleep_architecture",
    "StageMovementCheck",
    "sleep_stage_movement",
    "sleep_productivity",
    "daily_activity",
    "coverage_bounds",
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
class NightSignals:
    """Adjacent-sensor summary for one sleep record's window.

    Materialized by the sleep fusion processor from the normalized signal
    products (heart rate, HRV, respiratory, SpO2, skin temperature, snoring,
    movement) overlapping the record's start/end window.
    """

    hr_avg: Optional[float] = None
    hr_min: Optional[float] = None
    hr_max: Optional[float] = None
    hr_samples: Optional[int] = None
    hrv_rmssd: Optional[float] = None
    hrv_sdnn: Optional[float] = None
    respiratory_rate: Optional[float] = None
    spo2_avg: Optional[float] = None
    spo2_min: Optional[float] = None
    skin_temp_c: Optional[float] = None
    snoring_seconds: Optional[float] = None
    movement_min: Optional[float] = None


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
    proxy_score: Optional[float] = None  # heuristic score for unscored nights


@dataclass(frozen=True)
class SleepEntry:
    date: date
    total_minutes: float
    segments: tuple[SleepSegment, ...]
    avg_score: Optional[float]
    metrics: Optional[SleepMetrics] = None
    source: Optional[str] = None  # 'merged' | 'combined_only' | 'saa_only' | 'samsung_only' | 'stage_derived'
    nap_evidence: Optional[str] = None  # 'vitality_nap' | 'short_daytime'
    signals: Optional[NightSignals] = None
    saa_relation: Optional[str] = None  # 'mirror' | 'independent' (merged only)

    @property
    def is_nap(self) -> bool:
        return self.nap_evidence is not None

    @property
    def effective_score(self) -> Optional[float]:
        """Samsung's real score when present, else the fusion proxy score."""
        if self.avg_score is not None:
            return self.avg_score
        if self.metrics is not None:
            return self.metrics.proxy_score
        return None

    @property
    def score_estimated(self) -> bool:
        return self.avg_score is None and self.effective_score is not None

    @property
    def quality_label(self) -> str:
        score = self.effective_score
        if score is None:
            return "unknown"
        if score >= 80:
            return "good"
        if score >= 60:
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


_PROCESSED = Path("/realm/data/health/processed")


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
        proxy_score=_safe_float(metrics.get("proxy_score")),
    )
    raw_signals = rec.get("signals")
    signals: NightSignals | None = None
    if isinstance(raw_signals, dict):
        signals = NightSignals(
            hr_avg=_safe_float(raw_signals.get("hr_avg")),
            hr_min=_safe_float(raw_signals.get("hr_min")),
            hr_max=_safe_float(raw_signals.get("hr_max")),
            hr_samples=raw_signals.get("hr_samples") if isinstance(raw_signals.get("hr_samples"), int) else None,
            hrv_rmssd=_safe_float(raw_signals.get("hrv_rmssd")),
            hrv_sdnn=_safe_float(raw_signals.get("hrv_sdnn")),
            respiratory_rate=_safe_float(raw_signals.get("respiratory_rate")),
            spo2_avg=_safe_float(raw_signals.get("spo2_avg")),
            spo2_min=_safe_float(raw_signals.get("spo2_min")),
            skin_temp_c=_safe_float(raw_signals.get("skin_temp_c")),
            snoring_seconds=_safe_float(raw_signals.get("snoring_seconds")),
            movement_min=_safe_float(raw_signals.get("movement_min")),
        )
    return SleepEntry(
        date=d, total_minutes=total_min, segments=tuple(segments),
        avg_score=avg, metrics=sleep_metrics,
        source=rec.get("source") if isinstance(rec.get("source"), str) else None,
        nap_evidence=rec.get("nap_evidence") if isinstance(rec.get("nap_evidence"), str) else None,
        signals=signals,
        saa_relation=rec.get("saa_relation") if isinstance(rec.get("saa_relation"), str) else None,
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
        # A full night beats a nap regardless of source tier: a merged midday
        # nap must not displace the actual night's record for that date.
        if prev.is_nap != e.is_nap:
            if prev.is_nap:
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


def entries_in_range(*, start: date, end: date, canonical: bool = True) -> list[SleepEntry]:
    """List sleep entries between ``start`` and ``end`` inclusive.

    Default ``canonical=True`` returns one entry per date. Set ``canonical=False``
    to get the raw multi-row stream (useful for cross-device comparison).
    """
    source = canonical_entries() if canonical else entries()
    return [e for e in source if in_date_range(e.date, start, end)]


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
    for entry in entries_in_range(start=start, end=end, canonical=True):
        sig = entry.signals
        score = entry.effective_score
        result.append(SleepDayActivity(
            date=entry.date,
            total_hours=round(entry.total_minutes / 60, 2) if entry.total_minutes else None,
            score=round(score, 2) if score is not None else None,
            quality=entry.quality_label,
            hr_min_bpm=sig.hr_min if sig else None,
            hr_max_bpm=sig.hr_max if sig else None,
            hr_avg_bpm=sig.hr_avg if sig else None,
            respiratory_rate=sig.respiratory_rate if sig else None,
            snoring_seconds=sig.snoring_seconds if sig else None,
            skin_temp_c=sig.skin_temp_c if sig else None,
        ))
    return result


def coverage_bounds() -> CoverageBounds | None:
    if not get_config().sleep_jsonl.exists():
        return None
    dates = [e.date for e in entries()]
    if not dates:
        return None
    return CoverageBounds(source="sleep", first=min(dates), last=max(dates), kind="export")


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
        if not _in_range(logical_date(st), start, end):
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
# Stage-call consistency against per-minute movement
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StageMovementCheck:
    """One stage record judged against the watch's per-minute activity bins.

    This is a consistency check, not independent fusion: the movement bins
    come from the same worn device whose accelerometer fed Samsung's stage
    classifier. It still catches implausible calls (a "deep" interval with
    awake-level movement) introduced by Samsung's scoring/export pipeline.
    Measured basis (2026-08-03, 11,127 stage records with movement coverage):
    mean activity awake 3.39 / rem 1.46 / light 1.26 / deep 0.47; awake-vs-deep
    AUC 0.773.
    """

    record: SleepStageRecord
    movement_mean: Optional[float]  # mean activity_level; None = no coverage
    contradicted: bool


# Above this mean activity_level, a deep/light sleep call is implausible:
# awake intervals measure median 1.24 / p90 8.96, deep p90 is 1.07.
STAGE_MOVEMENT_CONTRADICTION_LEVEL = 2.0


def sleep_stage_movement(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[StageMovementCheck]:
    """Judge Samsung stage calls against per-minute movement activity bins.

    Coverage is bounded by the movement product (2025-05 onward); records
    without overlapping bins get ``movement_mean=None`` and are never flagged.
    """
    from bisect import bisect_left

    stages = sleep_stages(start=start, end=end)
    if not stages:
        return []

    bins: list[tuple[float, float]] = []
    for row in _load_jsonl("health_movement.jsonl"):
        raw = row.get("binning_data")
        if not isinstance(raw, list):
            continue
        for b in raw:
            if not isinstance(b, dict):
                continue
            t = b.get("start_time")
            v = b.get("activity_level")
            if isinstance(t, (int, float)) and isinstance(v, (int, float)):
                bins.append((t / 1000.0, float(v)))
    bins.sort()
    times = [t for t, _ in bins]

    checks: list[StageMovementCheck] = []
    for rec in stages:
        lo = bisect_left(times, rec.start.timestamp())
        # bisect_left on the end: a bin starting exactly at the record end
        # belongs to the next interval, not this one.
        hi = bisect_left(times, rec.end.timestamp())
        values = [bins[i][1] for i in range(lo, hi)]
        mean = sum(values) / len(values) if values else None
        contradicted = (
            mean is not None
            and rec.stage in ("deep", "light")
            and mean >= STAGE_MOVEMENT_CONTRADICTION_LEVEL
        )
        checks.append(
            StageMovementCheck(record=rec, movement_mean=(
                round(mean, 3) if mean is not None else None
            ), contradicted=contradicted)
        )
    return checks


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


SLEEP_PRODUCTIVITY_CHUNK_DAYS = 30


def sleep_productivity(
    *, start: date, end: date, chunk_days: int = SLEEP_PRODUCTIVITY_CHUNK_DAYS
) -> list[SleepProductivity]:
    """Join sleep data with next-day ActivityWatch activity and deep work.

    ActivityWatch reads are bounded by ``chunk_days``. The baseline is
    accumulated across all chunks so the output retains the full-range
    denominator used by the original implementation.
    """
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    sleep_data = entries_in_range(start=start, end=end)
    if not sleep_data:
        return []

    # Lazy import — ActivityWatch is a peer source, not a module-level
    # dependency. Prefer its bounded canonical products when available.
    from .activitywatch import active_seconds_by_date, deep_work
    from .activitywatch_derived import (
        iter_derived_daily_activity,
        iter_derived_deep_work,
    )
    from datetime import timedelta

    aw_start = min(e.date for e in sleep_data) + timedelta(days=1)
    aw_end = max(e.date for e in sleep_data) + timedelta(days=2)
    derived_first, derived_last = _activitywatch_derived_bounds()
    active_map: dict[date, float] = {}
    cursor = aw_start
    active_total = 0.0
    active_days = 0
    while cursor < aw_end:
        chunk_end = min(cursor + timedelta(days=chunk_days), aw_end)
        if derived_first is not None and derived_last is not None and cursor <= derived_last:
            if chunk_end <= derived_first:
                chunk_active = {}
            else:
                derived_start = max(cursor, derived_first)
                derived_end = min(chunk_end, derived_last + timedelta(days=1))
                chunk_active = {
                    row.date: row.active_hours * 3600
                    for row in iter_derived_daily_activity(
                        start=derived_start,
                        end=derived_end - timedelta(days=1),
                        ensure=False,
                    )
                }
        else:
            chunk_active = active_seconds_by_date(cursor, chunk_end)
        active_map.update(chunk_active)
        active_total += sum(chunk_active.values())
        active_days += len(chunk_active)
        cursor = chunk_end

    from datetime import time as time_cls
    dw_by_day: dict[date, float] = {}
    derived_deep_start = max(aw_start, derived_first) if derived_first is not None else aw_start
    derived_deep_end = min(aw_end, derived_last + timedelta(days=1)) if derived_last is not None else aw_start
    if derived_deep_start < derived_deep_end:
        for block in iter_derived_deep_work(
            start=datetime.combine(derived_deep_start, time_cls.min),
            end=datetime.combine(derived_deep_end, time_cls.min),
            ensure=False,
        ):
            day = logical_date(block.start)
            dw_by_day[day] = dw_by_day.get(day, 0) + block.duration_min

    cursor = max(aw_start, derived_last + timedelta(days=1)) if derived_last is not None else aw_start
    while cursor < aw_end:
        chunk_end = min(cursor + timedelta(days=chunk_days), aw_end)
        dw_blocks = deep_work(
            start=datetime.combine(cursor, time_cls.min),
            end=datetime.combine(chunk_end, time_cls.min),
        )
        for b in dw_blocks:
            day = logical_date(b.start)
            dw_by_day[day] = dw_by_day.get(day, 0) + b.duration_min
        cursor = chunk_end

    baseline_hours = active_total / max(active_days, 1) / 3600 if active_map else 0

    result: list[SleepProductivity] = []
    for entry in sleep_data:
        workday = entry.date + timedelta(days=1)
        active_h = active_map.get(workday, 0) / 3600
        dw_min = dw_by_day.get(workday, 0)
        vs_baseline = active_h / baseline_hours if baseline_hours > 0 else 0
        result.append(SleepProductivity(
            sleep_date=entry.date, sleep_hours=round(entry.total_minutes / 60, 2),
            sleep_score=entry.effective_score, sleep_quality=entry.quality_label,
            workday_active_hours=round(active_h, 2),
            workday_deep_work_min=round(dw_min, 1),
            productivity_vs_baseline=round(vs_baseline, 2),
        ))
    return result


def _activitywatch_derived_bounds() -> tuple[date | None, date | None]:
    from .activitywatch_derived import activitywatch_derived_manifest_path

    manifest = activitywatch_derived_manifest_path()
    if not manifest.exists():
        return None, None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        first = date.fromisoformat(str(payload["first_date"]))
        last = date.fromisoformat(str(payload["last_date"]))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None, None
    return first, last
