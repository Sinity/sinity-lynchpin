"""Steering store: commitments, activities, and reviews.

The steering store (`/realm/state/steering/steering.sqlite`, `commitments`
/ `activities` / `reviews` tables) drives the operator's daily
commit-forecast-review ritual. It nightly-exports each table to JSONL at
``captures/steering/YYYY-MM-DD.jsonl``, one row per record, tagged with a
``_table`` field. The JSONL export is the lake artifact and the preferred
source of record here; the live sqlite is read directly only if the export
proves insufficient for a given consumer.

Three record kinds:

* ``commitments`` — a dated commitment with ``forecast_p`` (the operator's
  stated confidence) and ``status`` (open/done/missed/retired). This is the
  calibration substrate: forecast_p vs. eventual status is what a Brier-score
  or reliability-diagram analysis would consume. No such analysis exists yet
  in this module — it is future work, not built here.
* ``activities`` — the standing catalogue of things a commitment can point
  at (task/practice/experiment/leisure). Undated; a snapshot of the catalogue
  as it stood on the export's day.
* ``reviews`` — the generated morning/evening review text for a window.

``commitments`` and ``reviews`` carry their own timestamp fields
(``created_at``, ``ts``) and are bucketed by those, falling back to the
export file's date when the timestamp is missing or unparseable.
``activities`` have no timestamp field at all; they are always bucketed by
export date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from ..core.config import LynchpinConfig
from ..core.parse import parse_datetime as _parse_dt, safe_int
from ..core.source import SourceReadiness, read_jsonl_with

__all__ = [
    "Commitment",
    "Activity",
    "Review",
    "readiness",
    "commitments",
    "activities",
    "reviews",
]


@dataclass(frozen=True)
class Commitment:
    export_date: date
    id: str
    text: str
    created_at: Optional[datetime]
    window_start: Optional[str]
    window_end: Optional[str]
    forecast_p: Optional[float]
    status: str
    outcome_at: Optional[datetime]
    outcome_note: Optional[str]
    activity_id: Optional[str]
    review_id: Optional[str]

    @property
    def date(self) -> date:
        return self.created_at.date() if self.created_at is not None else self.export_date


@dataclass(frozen=True)
class Activity:
    export_date: date
    id: str
    name: str
    kind: str
    est_minutes: Optional[int]
    energy_tier: str
    standing_notes: Optional[str]
    hypothesis: Optional[str]
    prereg_prediction: Optional[str]
    metric_ref: Optional[str]
    experiment_status: Optional[str]


@dataclass(frozen=True)
class Review:
    export_date: date
    id: str
    ts: Optional[datetime]
    window: str
    generated_summary: Optional[str]
    operator_notes: Optional[str]

    @property
    def date(self) -> date:
        return self.ts.date() if self.ts is not None else self.export_date


def _day_files(root: Path, start: Optional[date], end: Optional[date]) -> list[Path]:
    if not root.exists():
        return []
    dated: list[tuple[date, Path]] = []
    for path in root.glob("*.jsonl"):
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start is not None and file_date < start:
            continue
        if end is not None and file_date > end:
            continue
        dated.append((file_date, path))
    return [path for _day, path in sorted(dated)]


def readiness(root: Path | None = None) -> SourceReadiness:
    """Aggregate readiness across every ``YYYY-MM-DD.jsonl`` export file."""
    base = root or LynchpinConfig.from_env().steering_jsonl_dir
    if not base.exists():
        return SourceReadiness(
            status="missing", reason=f"{base} does not exist", path=base, row_count=0,
        )
    files = sorted(base.glob("*.jsonl"))
    if not files:
        return SourceReadiness(
            status="empty", reason="directory present but no export files yet",
            path=base, row_count=0,
        )
    total = 0
    for f in files:
        try:
            with f.open(encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.strip())
        except OSError:
            continue
    if total == 0:
        return SourceReadiness(
            status="empty", reason="export files present but no rows yet",
            path=base, row_count=0,
        )
    return SourceReadiness(status="ok", reason="", path=base, row_count=total)


def _hydrate_commitment(payload: dict[str, Any], export_date: date) -> Optional[Commitment]:
    record_id = payload.get("id")
    if not record_id:
        return None
    return Commitment(
        export_date=export_date,
        id=str(record_id),
        text=str(payload.get("text") or ""),
        created_at=_parse_dt(payload.get("created_at")),
        window_start=payload.get("window_start"),
        window_end=payload.get("window_end"),
        forecast_p=(
            float(payload["forecast_p"]) if isinstance(payload.get("forecast_p"), (int, float)) else None
        ),
        status=str(payload.get("status") or "open"),
        outcome_at=_parse_dt(payload.get("outcome_at")),
        outcome_note=payload.get("outcome_note"),
        activity_id=payload.get("activity_id"),
        review_id=payload.get("review_id"),
    )


def _hydrate_activity(payload: dict[str, Any], export_date: date) -> Optional[Activity]:
    record_id = payload.get("id")
    if not record_id:
        return None
    return Activity(
        export_date=export_date,
        id=str(record_id),
        name=str(payload.get("name") or ""),
        kind=str(payload.get("kind") or ""),
        est_minutes=safe_int(payload.get("est_minutes")),
        energy_tier=str(payload.get("energy_tier") or ""),
        standing_notes=payload.get("standing_notes"),
        hypothesis=payload.get("hypothesis"),
        prereg_prediction=payload.get("prereg_prediction"),
        metric_ref=payload.get("metric_ref"),
        experiment_status=payload.get("experiment_status"),
    )


def _hydrate_review(payload: dict[str, Any], export_date: date) -> Optional[Review]:
    record_id = payload.get("id")
    if not record_id:
        return None
    return Review(
        export_date=export_date,
        id=str(record_id),
        ts=_parse_dt(payload.get("ts")),
        window=str(payload.get("window") or ""),
        generated_summary=payload.get("generated_summary"),
        operator_notes=payload.get("operator_notes"),
    )


def _iter_rows(
    root: Path, start: Optional[date], end: Optional[date], table: str,
) -> Iterator[tuple[dict[str, Any], date]]:
    for path in _day_files(root, start, end):
        export_date = date.fromisoformat(path.stem)
        for payload in read_jsonl_with(path, lambda p: p, source_name=path.name):
            if payload.get("_table") != table:
                continue
            yield payload, export_date


def commitments(
    *, start: Optional[date] = None, end: Optional[date] = None, root: Optional[Path] = None,
) -> Iterator[Commitment]:
    base = root or LynchpinConfig.from_env().steering_jsonl_dir
    for payload, export_date in _iter_rows(base, start, end, "commitments"):
        record = _hydrate_commitment(payload, export_date)
        if record is None:
            continue
        if start is not None and record.date < start:
            continue
        if end is not None and record.date > end:
            continue
        yield record


def activities(
    *, start: Optional[date] = None, end: Optional[date] = None, root: Optional[Path] = None,
) -> Iterator[Activity]:
    """Undated catalogue rows, bucketed by export date only."""
    base = root or LynchpinConfig.from_env().steering_jsonl_dir
    for payload, export_date in _iter_rows(base, start, end, "activities"):
        record = _hydrate_activity(payload, export_date)
        if record is not None:
            yield record


def reviews(
    *, start: Optional[date] = None, end: Optional[date] = None, root: Optional[Path] = None,
) -> Iterator[Review]:
    base = root or LynchpinConfig.from_env().steering_jsonl_dir
    for payload, export_date in _iter_rows(base, start, end, "reviews"):
        record = _hydrate_review(payload, export_date)
        if record is None:
            continue
        if start is not None and record.date < start:
            continue
        if end is not None and record.date > end:
            continue
        yield record
