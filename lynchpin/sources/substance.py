"""Substance log source over the processed health export CSV."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.parse import parse_date_from_any, safe_float

__all__ = [
    "SubstanceEntry",
    "SubstanceDaySummary",
    "SubstanceMonthlySummary",
    "entries",
    "entries_for_date",
    "entries_in_range",
    "daily_summary",
    "monthly_summary",
]


@dataclass(frozen=True)
class SubstanceEntry:
    date: date
    time: time | None
    substance: str
    amount_mg: float | None
    source: str
    note: str


@dataclass(frozen=True)
class SubstanceDaySummary:
    date: date
    dose_count: int
    substances: tuple[str, ...]
    total_mg: float
    by_substance_mg: dict[str, float]


@dataclass(frozen=True)
class SubstanceMonthlySummary:
    month: str
    dose_count: int
    dose_days: int
    substances: tuple[str, ...]
    by_substance_mg: dict[str, float]


def _substance_csv() -> Path:
    return get_config().exports_root / "health/processed/substance_log_unified.csv"


def _load_entries() -> list[SubstanceEntry]:
    path = _substance_csv()
    if not path.exists():
        return []

    rows: list[SubstanceEntry] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            day = parse_date_from_any(raw.get("date"))
            substance = (raw.get("substance") or "").strip()
            if day is None or not substance:
                continue
            rows.append(
                SubstanceEntry(
                    date=day,
                    time=_parse_time(raw.get("time")),
                    substance=substance,
                    amount_mg=safe_float(raw.get("amount_mg")),
                    source=(raw.get("source") or "").strip(),
                    note=(raw.get("note") or "").strip(),
                )
            )
    return rows


def _parse_time(value: object) -> time | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def entries() -> Iterator[SubstanceEntry]:
    yield from _load_entries()


def entries_for_date(day: date) -> list[SubstanceEntry]:
    return [entry for entry in entries() if entry.date == day]


def entries_in_range(*, start: date, end: date) -> list[SubstanceEntry]:
    return [entry for entry in entries() if start <= entry.date <= end]


def daily_summary(*, start: date, end: date) -> list[SubstanceDaySummary]:
    by_day: dict[date, list[SubstanceEntry]] = defaultdict(list)
    for entry in entries_in_range(start=start, end=end):
        by_day[entry.date].append(entry)

    summaries: list[SubstanceDaySummary] = []
    for day in sorted(by_day):
        rows = by_day[day]
        totals: dict[str, float] = defaultdict(float)
        for row in rows:
            if row.amount_mg is not None:
                totals[row.substance] += row.amount_mg
        summaries.append(
            SubstanceDaySummary(
                date=day,
                dose_count=len(rows),
                substances=tuple(sorted({row.substance for row in rows})),
                total_mg=sum(totals.values()),
                by_substance_mg=dict(sorted(totals.items())),
            )
        )
    return summaries


def monthly_summary(*, start: date, end: date) -> list[SubstanceMonthlySummary]:
    by_month: dict[str, list[SubstanceEntry]] = defaultdict(list)
    for entry in entries_in_range(start=start, end=end):
        by_month[f"{entry.date.year:04d}-{entry.date.month:02d}"].append(entry)

    summaries: list[SubstanceMonthlySummary] = []
    for month in sorted(by_month):
        rows = by_month[month]
        totals: dict[str, float] = defaultdict(float)
        for row in rows:
            if row.amount_mg is not None:
                totals[row.substance] += row.amount_mg
        summaries.append(
            SubstanceMonthlySummary(
                month=month,
                dose_count=len(rows),
                dose_days=len({row.date for row in rows}),
                substances=tuple(sorted({row.substance for row in rows})),
                by_substance_mg=dict(sorted(totals.items())),
            )
        )
    return summaries
