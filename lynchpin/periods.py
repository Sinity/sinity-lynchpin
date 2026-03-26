"""Shared period parsing and hierarchy utilities.

This module is the canonical place for:
- period key parsing (`day`, `week`, `month`, `quarter`, `half`, `year`)
- date-range resolution
- hierarchy navigation
- hierarchical narrative/evidence path derivation

It is intentionally independent from `trajectory` and from retrospective file
I/O so both `context` and `retrospective` can rely on the same semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import calendar
import re
from typing import Any

SCALE_ORDER: tuple[str, ...] = ("day", "week", "month", "quarter", "half", "year")


@dataclass(frozen=True)
class Period:
    scale: str
    key: str
    start: date
    end: date

    def to_dict(self) -> dict[str, str]:
        return {
            "scale": self.scale,
            "key": self.key,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


def normalize_scale(scale: Any) -> str:
    value = getattr(scale, "value", scale)
    if not isinstance(value, str):
        raise ValueError(f"Unsupported period scale: {scale!r}")
    normalized = value.strip().lower()
    aliases = {
        "half-year": "half",
        "halfyear": "half",
    }
    return aliases.get(normalized, normalized)


def child_scale(scale: Any) -> str | None:
    normalized = normalize_scale(scale)
    mapping = {
        "week": "day",
        "month": "week",
        "quarter": "month",
        "half": "quarter",
        "year": "half",
    }
    return mapping.get(normalized)


def parse_period(scale: Any, key: str) -> Period | None:
    normalized = normalize_scale(scale)
    try:
        if normalized == "day":
            parsed = date.fromisoformat(key)
            return Period(normalized, key, parsed, parsed)
        if normalized == "week":
            year, week = _parse_week_key(key)
            start = date.fromisocalendar(year, week, 1)
            end = date.fromisocalendar(year, week, 7)
            return Period(normalized, key, start, end)
        if normalized == "month":
            year, month = _parse_month_key(key)
            start = date(year, month, 1)
            end = _month_end(year, month)
            return Period(normalized, key, start, end)
        if normalized == "quarter":
            year, quarter = _parse_quarter_key(key)
            start_month = (quarter - 1) * 3 + 1
            start = date(year, start_month, 1)
            end = _month_end(year, start_month + 2)
            return Period(normalized, key, start, end)
        if normalized == "half":
            year, half = _parse_half_key(key)
            start_month = 1 if half == "H1" else 7
            end_month = 6 if half == "H1" else 12
            start = date(year, start_month, 1)
            end = _month_end(year, end_month)
            return Period(normalized, key, start, end)
        if normalized == "year":
            year = _parse_year_key(key)
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            return Period(normalized, key, start, end)
    except ValueError:
        return None
    return None


def child_keys(scale: Any, key: str) -> list[str]:
    period = parse_period(scale, key)
    if period is None:
        return []
    if period.scale == "week":
        return [
            date.fromisocalendar(period.start.isocalendar().year, period.start.isocalendar().week, day).isoformat()
            for day in range(1, 8)
        ]
    if period.scale == "month":
        weeks: list[str] = []
        seen: set[str] = set()
        current = period.start
        while current <= period.end:
            iso = current.isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            if week_key not in seen:
                seen.add(week_key)
                weeks.append(week_key)
            current += timedelta(days=1)
        return weeks
    if period.scale == "quarter":
        return [f"{period.start.year}-{month:02d}" for month in range(period.start.month, period.start.month + 3)]
    if period.scale == "half":
        start_quarter = 1 if period.start.month == 1 else 3
        return [f"{period.start.year}-Q{quarter}" for quarter in range(start_quarter, start_quarter + 2)]
    if period.scale == "year":
        return [f"{period.start.year}-H1", f"{period.start.year}-H2"]
    return []


def prior_key(scale: Any, key: str) -> str | None:
    period = parse_period(scale, key)
    if period is None:
        return None
    if period.scale == "day":
        return (period.start - timedelta(days=1)).isoformat()
    if period.scale == "week":
        prior = period.start - timedelta(days=7)
        iso = prior.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period.scale == "month":
        if period.start.month > 1:
            return f"{period.start.year}-{period.start.month - 1:02d}"
        return f"{period.start.year - 1}-12"
    if period.scale == "quarter":
        _, quarter = _parse_quarter_key(key)
        if quarter > 1:
            return f"{period.start.year}-Q{quarter - 1}"
        return f"{period.start.year - 1}-Q4"
    if period.scale == "half":
        _, half = _parse_half_key(key)
        if half == "H2":
            return f"{period.start.year}-H1"
        return f"{period.start.year - 1}-H2"
    if period.scale == "year":
        return str(period.start.year - 1)
    return None


def next_key(scale: Any, key: str) -> str | None:
    period = parse_period(scale, key)
    if period is None:
        return None
    if period.scale == "day":
        return (period.start + timedelta(days=1)).isoformat()
    if period.scale == "week":
        nxt = period.start + timedelta(days=7)
        iso = nxt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period.scale == "month":
        if period.start.month < 12:
            return f"{period.start.year}-{period.start.month + 1:02d}"
        return f"{period.start.year + 1}-01"
    if period.scale == "quarter":
        _, quarter = _parse_quarter_key(key)
        if quarter < 4:
            return f"{period.start.year}-Q{quarter + 1}"
        return f"{period.start.year + 1}-Q1"
    if period.scale == "half":
        _, half = _parse_half_key(key)
        if half == "H1":
            return f"{period.start.year}-H2"
        return f"{period.start.year + 1}-H1"
    if period.scale == "year":
        return str(period.start.year + 1)
    return None


def hierarchical_relpath(scale: Any, key: str) -> Path | None:
    period = parse_period(scale, key)
    if period is None:
        return None

    year = period.start.year
    half = "H1" if period.start.month <= 6 else "H2"
    quarter = ((period.start.month - 1) // 3) + 1

    if period.scale == "day":
        return (
            Path(str(year))
            / half
            / f"Q{quarter}"
            / calendar.month_name[period.start.month]
            / f"{_ordinal(period.start.day)}.md"
        )
    if period.scale == "week":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if period.scale == "month":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if period.scale == "quarter":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if period.scale == "half":
        return Path(str(year)) / half / f"{key}.md"
    if period.scale == "year":
        return Path(str(year)) / f"{key}.md"
    return None


def period_label(scale: Any, key: str) -> str:
    period = parse_period(scale, key)
    if period is None:
        return key
    if period.scale == "day":
        return period.start.strftime("%A, %Y-%m-%d")
    return key


def key_for_date(scale: Any, value: date) -> str:
    normalized = normalize_scale(scale)
    if normalized == "day":
        return value.isoformat()
    if normalized == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if normalized == "month":
        return value.strftime("%Y-%m")
    if normalized == "quarter":
        quarter = ((value.month - 1) // 3) + 1
        return f"{value.year}-Q{quarter}"
    if normalized == "half":
        return f"{value.year}-H{'1' if value.month <= 6 else '2'}"
    if normalized == "year":
        return str(value.year)
    raise ValueError(f"Unsupported period scale: {scale!r}")


def period_keys_in_range(scale: Any, start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError("end must be on or after start")

    normalized = normalize_scale(scale)
    keys: list[str] = []
    seen: set[str] = set()
    current = start
    while current <= end:
        key = key_for_date(normalized, current)
        if key not in seen:
            seen.add(key)
            keys.append(key)
        current += timedelta(days=1)
    return keys


def _parse_week_key(key: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", key)
    if not match:
        raise ValueError(f"Invalid week key: {key}")
    year = int(match.group(1))
    week = int(match.group(2))
    date.fromisocalendar(year, week, 1)
    return year, week


def _parse_month_key(key: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", key)
    if not match:
        raise ValueError(f"Invalid month key: {key}")
    year = int(match.group(1))
    month = int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid month key: {key}")
    return year, month


def _parse_quarter_key(key: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", key)
    if not match:
        raise ValueError(f"Invalid quarter key: {key}")
    return int(match.group(1)), int(match.group(2))


def _parse_half_key(key: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d{4})-(H[12])", key)
    if not match:
        raise ValueError(f"Invalid half key: {key}")
    return int(match.group(1)), match.group(2)


def _parse_year_key(key: str) -> int:
    if not re.fullmatch(r"\d{4}", key):
        raise ValueError(f"Invalid year key: {key}")
    return int(key)


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
