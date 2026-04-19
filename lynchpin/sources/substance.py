"""Substance tracking source: dose timing, amounts, and cross-analysis.

Reads from unified CSV at /realm/data/exports/health/processed/substance_log_unified.csv.
Covers nootropics, stimulants, and other tracked substances with timestamps and dosage.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Iterator, Optional, Sequence

from ..core.config import get_config

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

_CSV_PATH = Path("/realm/data/exports/health/processed/substance_log_unified.csv")


@dataclass(frozen=True)
class SubstanceEntry:
    date: date
    time: Optional[str]       # HH:MM or empty
    substance: str
    amount_mg: Optional[float]
    source: str
    note: str


@dataclass(frozen=True)
class SubstanceDaySummary:
    date: date
    entries: tuple[SubstanceEntry, ...]
    total_mg: float
    substances: tuple[str, ...]   # unique substances taken
    dose_count: int


@dataclass(frozen=True)
class SubstanceMonthlySummary:
    month: str  # YYYY-MM
    by_substance_mg: dict[str, float]
    total_doses: int
    dose_days: int    # days with at least one dose
    total_days: int   # days in the range


def entries() -> list[SubstanceEntry]:
    """Load all substance entries from the unified CSV."""
    if not _CSV_PATH.exists():
        return []
    result = []
    with open(_CSV_PATH) as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat(row["date"])
            except (ValueError, KeyError):
                continue
            result.append(SubstanceEntry(
                date=d,
                time=row.get("time", "") or None,
                substance=row.get("substance", ""),
                amount_mg=float(row["amount_mg"]) if row.get("amount_mg") else None,
                source=row.get("source", ""),
                note=row.get("note", ""),
            ))
    return result


def entries_for_date(d: date) -> list[SubstanceEntry]:
    """All substance entries for a specific date."""
    return [e for e in entries() if e.date == d]


def entries_in_range(start: date, end: date) -> list[SubstanceEntry]:
    """All substance entries within a date range (inclusive)."""
    return [e for e in entries() if start <= e.date <= end]


def daily_summary(*, start: date, end: date) -> list[SubstanceDaySummary]:
    """Per-day substance summaries for a date range."""
    by_date: dict[date, list[SubstanceEntry]] = defaultdict(list)
    for e in entries_in_range(start, end):
        by_date[e.date].append(e)

    result = []
    for d in sorted(by_date):
        day_entries = by_date[d]
        result.append(SubstanceDaySummary(
            date=d,
            entries=tuple(day_entries),
            total_mg=sum(e.amount_mg or 0 for e in day_entries),
            substances=tuple(sorted({e.substance for e in day_entries})),
            dose_count=len(day_entries),
        ))
    return result


def monthly_summary(*, start: date, end: date) -> list[SubstanceMonthlySummary]:
    """Per-month substance totals and dose frequency."""
    all_entries = entries_in_range(start, end)

    monthly: dict[str, list[SubstanceEntry]] = defaultdict(list)
    for e in all_entries:
        mk = e.date.strftime("%Y-%m")
        monthly[mk].append(e)

    result = []
    for mk in sorted(monthly):
        month_entries = monthly[mk]
        by_sub: dict[str, float] = defaultdict(float)
        dose_dates: set[date] = set()
        for e in month_entries:
            by_sub[e.substance] += e.amount_mg or 0
            dose_dates.add(e.date)

        # Count total days in month
        year, month = int(mk[:4]), int(mk[5:])
        import calendar
        total_days = calendar.monthrange(year, month)[1]

        result.append(SubstanceMonthlySummary(
            month=mk,
            by_substance_mg=dict(by_sub),
            total_doses=len(month_entries),
            dose_days=len(dose_dates),
            total_days=total_days,
        ))
    return result
