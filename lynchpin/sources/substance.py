"""Substance log source over the processed health export CSV."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterator, Sequence

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
    return get_config().health_root / "processed/substance_log_unified.csv"


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


@dataclass(frozen=True)
class LoggingPeriod:
    """A maximal span of continuous substance logging.

    Consecutive dose-days ≤ ``max_gap_days`` apart belong to one period. The
    gap histogram is decisively bimodal (360/372 gaps ≤ 4 days, nothing in
    5-6 or 8, every gap ≥ 9 a singleton era boundary), so the default sits in
    the empty region rather than on a judgment call. Within a period the
    operator logs comprehensively (their stated practice), so a day with no
    entry is a trustworthy zero; days OUTSIDE any period are indeterminate
    (possibly unlogged use) and must not serve as abstinence controls.
    """

    start: date
    end: date
    dose_days: int


def logging_periods(
    *,
    max_gap_days: int = 8,
    min_dose_days: int = 5,
) -> list[LoggingPeriod]:
    """Maximal comprehensive-logging spans inferred from dose-day gaps.

    ``min_dose_days`` drops isolated ancient singletons (2020-06, 2022-01)
    that are not logging regimes.
    """
    days = sorted({e.date for e in entries()})
    periods: list[LoggingPeriod] = []
    if not days:
        return periods
    run_start = days[0]
    run_days = 1
    for prev, cur in zip(days, days[1:]):
        if (cur - prev).days <= max_gap_days:
            run_days += 1
            continue
        if run_days >= min_dose_days:
            periods.append(LoggingPeriod(start=run_start, end=prev, dose_days=run_days))
        run_start = cur
        run_days = 1
    if run_days >= min_dose_days:
        periods.append(LoggingPeriod(start=run_start, end=days[-1], dose_days=run_days))
    return periods


def in_logging_period(
    day: date,
    periods: Sequence[LoggingPeriod] | None = None,
) -> bool:
    """True when ``day`` falls inside a comprehensive-logging span."""
    for period in periods if periods is not None else logging_periods():
        if period.start <= day <= period.end:
            return True
    return False
