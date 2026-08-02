"""Best-estimate sleep boundaries for independently dual-recorded nights.

The fused sleep dataset keeps the Samsung boundary as the record boundary and
stores the Sleep-as-Android disagreement in ``deltas``. For the minority of
``merged`` nights whose SAA session is an independent phone-side recording
(``saa_relation == "independent"``, not a synced mirror), the two devices can
disagree by 1-2 hours on session start/end. This module adjudicates each
disputed boundary read-side, without touching the materialized dataset.

Method, per disputed boundary (start and end judged independently):

1. If the devices agree within ``AGREE_MINUTES``, keep the Samsung boundary
   (``basis="agree"``).
2. Otherwise look at per-minute heart-rate samples inside the disputed
   interval (the span between the two devices' boundaries). Heart-rate rows
   carry ``binning_data`` — per-minute avg/min/max bins — which this module
   expands; hourly summary rows without bins contribute their midpoint.
3. Compare the disputed-interval HR mean against the night's core sleeping HR
   (samples inside the interval both devices agree was sleep). A disputed
   interval with sleep-like HR belongs inside the night (take the wider
   boundary, ``basis="hr_asleep"``); clearly elevated HR means awake (take the
   narrower boundary, ``basis="hr_awake"``).
4. With too few samples or an ambiguous ratio, fall back to a weighted
   compromise biased toward the watch (worn-device actigraphy beats
   phone-side detection): ``samsung + SAA_WEIGHT * delta``
   (``basis="weighted"``).

Everything here is a read API over already-materialized products
(``sleep_all_nights.jsonl`` / ``health_heart_rate.jsonl``).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import get_config
from ..core.parse import parse_datetime as _parse_dt
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with

__all__ = [
    "BoundaryEstimate",
    "boundary_estimates",
]

_PROCESSED = Path("/realm/data/exports/health/processed")
_HEART_RATE_FILE = "health_heart_rate.jsonl"

AGREE_MINUTES = 10.0
MIN_HR_SAMPLES = 5
ASLEEP_RATIO = 1.10
AWAKE_RATIO = 1.25
SAA_WEIGHT = 0.3  # weight of the SAA boundary in the no-evidence fallback
# Never move a boundary further than this from the Samsung boundary on HR
# evidence alone: quiet wakefulness (reading in bed, morning phone use) can sit
# below ASLEEP_RATIO x core HR, and unbounded trust in low HR produced
# multi-hour extensions from runaway SAA sessions in live validation.
MAX_EXTEND_MINUTES = 120.0


@dataclass(frozen=True)
class BoundaryEstimate:
    """Adjudicated boundaries for one independently dual-recorded night."""

    date: date
    canonical_id: str
    samsung_start: datetime
    samsung_end: datetime
    saa_start: datetime
    saa_end: datetime
    best_start: datetime
    best_end: datetime
    start_basis: str  # 'agree' | 'hr_asleep' | 'hr_awake' | 'weighted'
    end_basis: str
    start_disputed_min: float
    end_disputed_min: float
    start_hr_samples: int
    end_hr_samples: int
    core_hr_avg: Optional[float]

    @property
    def best_duration_min(self) -> float:
        return max((self.best_end - self.best_start).total_seconds() / 60.0, 0.0)

    @property
    def adjustment_min(self) -> float:
        """Total minutes moved relative to the Samsung boundaries."""
        return abs(
            (self.best_start - self.samsung_start).total_seconds() / 60.0
        ) + abs((self.best_end - self.samsung_end).total_seconds() / 60.0)


# ── Heart-rate sample index ──────────────────────────────────────────────────


class _HrIndex:
    """Sorted per-minute heart-rate samples over a bounded time range."""

    def __init__(self, samples: list[tuple[datetime, float]]) -> None:
        samples.sort(key=lambda s: s[0])
        self._times = [s[0] for s in samples]
        self._values = [s[1] for s in samples]

    def between(self, start: datetime, end: datetime) -> list[float]:
        lo = bisect_left(self._times, start)
        hi = bisect_right(self._times, end)
        return self._values[lo:hi]


def _load_hr_index(start: datetime, end: datetime) -> _HrIndex:
    samples: list[tuple[datetime, float]] = []
    path = _PROCESSED / _HEART_RATE_FILE

    def hydrate(row: dict[str, Any]) -> None:
        row_start = _parse_dt(row.get("start_time"))
        if row_start is None:
            return None
        row_end = _parse_dt(row.get("end_time")) or row_start
        if row_end < start or row_start > end:
            return None
        bins = row.get("binning_data")
        if isinstance(bins, list) and bins:
            tz = row_start.tzinfo
            for entry in bins:
                if not isinstance(entry, dict):
                    continue
                bpm = entry.get("heart_rate")
                ts = entry.get("start_time")
                if not isinstance(bpm, (int, float)) or not isinstance(ts, (int, float)):
                    continue
                when = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                if tz is not None:
                    when = when.astimezone(tz)
                if start <= when <= end:
                    samples.append((when, float(bpm)))
        else:
            bpm_raw = row.get("heart_rate")
            if isinstance(bpm_raw, (int, float)):
                midpoint = row_start + (row_end - row_start) / 2
                if start <= midpoint <= end:
                    samples.append((midpoint, float(bpm_raw)))
        return None

    for _ in read_jsonl_with(path, hydrate, source_name=_HEART_RATE_FILE):
        pass  # hydrate() accumulates; read_jsonl_with skips the returned Nones
    return _HrIndex(samples)


# ── Record access ────────────────────────────────────────────────────────────


def _independent_records(
    start: Optional[date], end: Optional[date]
) -> Iterator[dict[str, Any]]:
    def hydrate(rec: dict[str, Any]) -> dict[str, Any] | None:
        if rec.get("saa_relation") != "independent":
            return None
        deltas = rec.get("deltas")
        if not isinstance(deltas, dict):
            return None
        sh_start = _parse_dt(rec.get("start_local"))
        sh_end = _parse_dt(rec.get("end_local"))
        if sh_start is None or sh_end is None or sh_end <= sh_start:
            return None
        d = logical_date(sh_start)
        if start is not None and d < start:
            return None
        if end is not None and d > end:
            return None
        rec["_sh_start"] = sh_start
        rec["_sh_end"] = sh_end
        rec["_date"] = d
        return rec

    yield from read_jsonl_with(
        get_config().sleep_jsonl, hydrate, source_name="sleep_merged"
    )


# ── Adjudication ─────────────────────────────────────────────────────────────


def _judge_boundary(
    *,
    kind: str,  # 'start' | 'end'
    samsung: datetime,
    saa: datetime,
    hr: _HrIndex,
    core_avg: Optional[float],
) -> tuple[datetime, str, float, int]:
    """Return (best, basis, disputed_minutes, sample_count) for one boundary."""
    delta_min = (saa - samsung).total_seconds() / 60.0
    disputed = abs(delta_min)
    if disputed < AGREE_MINUTES:
        return samsung, "agree", disputed, 0

    window_start = min(samsung, saa)
    window_end = max(samsung, saa)
    # The boundary choice that makes the night WIDER (includes the disputed
    # span): for a start that is the earlier candidate, for an end the later.
    wider = window_start if kind == "start" else window_end
    narrower = window_end if kind == "start" else window_start

    values = hr.between(window_start, window_end)
    candidate: datetime
    basis: str
    if core_avg is not None and len(values) >= MIN_HR_SAMPLES:
        mean = sum(values) / len(values)
        if mean <= core_avg * ASLEEP_RATIO:
            # Sleep-like HR inside the disputed span: it was part of the night.
            candidate, basis = wider, "hr_asleep"
        elif mean >= core_avg * AWAKE_RATIO:
            # Clearly elevated HR: the disputed span was wakefulness.
            candidate, basis = narrower, "hr_awake"
        else:
            candidate, basis = samsung + timedelta(minutes=delta_min * SAA_WEIGHT), "weighted"
    else:
        candidate, basis = samsung + timedelta(minutes=delta_min * SAA_WEIGHT), "weighted"

    # Uniform plausibility clamp: never move a boundary further than
    # MAX_EXTEND_MINUTES from the watch's boundary, whatever the basis — an
    # SAA session hours adrift is a runaway recording or weak pairing, and
    # quiet wakefulness can fool the HR ratio in either direction.
    shift_min = (candidate - samsung).total_seconds() / 60.0
    if abs(shift_min) > MAX_EXTEND_MINUTES:
        candidate = samsung + timedelta(
            minutes=MAX_EXTEND_MINUTES if shift_min > 0 else -MAX_EXTEND_MINUTES
        )
        basis += "_capped"
    return candidate, basis, disputed, len(values)


def boundary_estimates(
    *, start: Optional[date] = None, end: Optional[date] = None
) -> list[BoundaryEstimate]:
    """Adjudicated best-estimate boundaries for independent dual-recorded nights.

    Returns one estimate per ``merged`` record with
    ``saa_relation == "independent"`` whose logical date falls in the range.
    """
    records = list(_independent_records(start, end))
    if not records:
        return []

    horizon_start = min(r["_sh_start"] for r in records) - timedelta(hours=6)
    horizon_end = max(r["_sh_end"] for r in records) + timedelta(hours=6)
    hr = _load_hr_index(horizon_start, horizon_end)

    estimates: list[BoundaryEstimate] = []
    for rec in records:
        sh_start: datetime = rec["_sh_start"]
        sh_end: datetime = rec["_sh_end"]
        deltas = rec["deltas"]
        try:
            saa_start = sh_start + timedelta(minutes=float(deltas["start_minutes"]))
            saa_end = sh_end + timedelta(minutes=float(deltas["end_minutes"]))
        except (KeyError, TypeError, ValueError):
            continue

        # Core sleep window: the span both devices agree was sleep.
        core_start = max(sh_start, saa_start)
        core_end = min(sh_end, saa_end)
        core_values = (
            hr.between(core_start, core_end) if core_end > core_start else []
        )
        core_avg = (
            sum(core_values) / len(core_values)
            if len(core_values) >= MIN_HR_SAMPLES
            else None
        )

        best_start, start_basis, start_disputed, start_n = _judge_boundary(
            kind="start", samsung=sh_start, saa=saa_start, hr=hr, core_avg=core_avg
        )
        best_end, end_basis, end_disputed, end_n = _judge_boundary(
            kind="end", samsung=sh_end, saa=saa_end, hr=hr, core_avg=core_avg
        )
        if best_end <= best_start:
            best_start, best_end = sh_start, sh_end
            start_basis = end_basis = "agree"

        estimates.append(
            BoundaryEstimate(
                date=rec["_date"],
                canonical_id=str(rec.get("canonical_id", "")),
                samsung_start=sh_start,
                samsung_end=sh_end,
                saa_start=saa_start,
                saa_end=saa_end,
                best_start=best_start,
                best_end=best_end,
                start_basis=start_basis,
                end_basis=end_basis,
                start_disputed_min=round(start_disputed, 1),
                end_disputed_min=round(end_disputed, 1),
                start_hr_samples=start_n,
                end_hr_samples=end_n,
                core_hr_avg=round(core_avg, 1) if core_avg is not None else None,
            )
        )
    estimates.sort(key=lambda e: e.date)
    return estimates
