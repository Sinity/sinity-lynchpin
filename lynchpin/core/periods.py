"""Period parsing, hierarchy navigation, and narrative path derivation.

Canonical place for period key semantics across 6 scales:
day, week, month, quarter, half, year.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCALE_ORDER: tuple[str, ...] = ("day", "week", "month", "quarter", "half", "year")


@dataclass(frozen=True)
class Period:
    scale: str
    key: str
    start: date
    end: date

    def to_dict(self) -> dict[str, str]:
        return {"scale": self.scale, "key": self.key, "start": self.start.isoformat(), "end": self.end.isoformat()}


def normalize_scale(scale: Any) -> str:
    value = getattr(scale, "value", scale)
    if not isinstance(value, str):
        raise ValueError(f"Unsupported period scale: {scale!r}")
    normalized = value.strip().lower()
    return {"half-year": "half", "halfyear": "half"}.get(normalized, normalized)


def child_scale(scale: Any) -> str | None:
    return {"week": "day", "month": "week", "quarter": "month", "half": "quarter", "year": "half"}.get(normalize_scale(scale))


def parse_period(scale: Any, key: str) -> Period | None:
    normalized = normalize_scale(scale)
    try:
        if normalized == "day":
            parsed = date.fromisoformat(key)
            return Period(normalized, key, parsed, parsed)
        if normalized == "week":
            year, week = _parse_week_key(key)
            return Period(normalized, key, date.fromisocalendar(year, week, 1), date.fromisocalendar(year, week, 7))
        if normalized == "month":
            year, month = _parse_month_key(key)
            return Period(normalized, key, date(year, month, 1), _month_end(year, month))
        if normalized == "quarter":
            year, quarter = _parse_quarter_key(key)
            sm = (quarter - 1) * 3 + 1
            return Period(normalized, key, date(year, sm, 1), _month_end(year, sm + 2))
        if normalized == "half":
            year, half = _parse_half_key(key)
            sm, em = (1, 6) if half == "H1" else (7, 12)
            return Period(normalized, key, date(year, sm, 1), _month_end(year, em))
        if normalized == "year":
            year = _parse_year_key(key)
            return Period(normalized, key, date(year, 1, 1), date(year, 12, 31))
    except ValueError:
        return None
    return None


def child_keys(scale: Any, key: str) -> list[str]:
    period = parse_period(scale, key)
    if period is None:
        return []
    if period.scale == "week":
        return [date.fromisocalendar(period.start.isocalendar().year, period.start.isocalendar().week, d).isoformat() for d in range(1, 8)]
    if period.scale == "month":
        weeks, seen, current = [], set(), period.start
        while current <= period.end:
            iso = current.isocalendar()
            wk = f"{iso.year}-W{iso.week:02d}"
            if wk not in seen:
                seen.add(wk)
                weeks.append(wk)
            current += timedelta(days=1)
        return weeks
    if period.scale == "quarter":
        return [f"{period.start.year}-{m:02d}" for m in range(period.start.month, period.start.month + 3)]
    if period.scale == "half":
        sq = 1 if period.start.month == 1 else 3
        return [f"{period.start.year}-Q{q}" for q in range(sq, sq + 2)]
    if period.scale == "year":
        return [f"{period.start.year}-H1", f"{period.start.year}-H2"]
    return []


def prior_key(scale: Any, key: str) -> str | None:
    p = parse_period(scale, key)
    if p is None:
        return None
    if p.scale == "day":
        return (p.start - timedelta(days=1)).isoformat()
    if p.scale == "week":
        iso = (p.start - timedelta(days=7)).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if p.scale == "month":
        return f"{p.start.year}-{p.start.month - 1:02d}" if p.start.month > 1 else f"{p.start.year - 1}-12"
    if p.scale == "quarter":
        _, q = _parse_quarter_key(key)
        return f"{p.start.year}-Q{q - 1}" if q > 1 else f"{p.start.year - 1}-Q4"
    if p.scale == "half":
        _, h = _parse_half_key(key)
        return f"{p.start.year}-H1" if h == "H2" else f"{p.start.year - 1}-H2"
    if p.scale == "year":
        return str(p.start.year - 1)
    return None


def next_key(scale: Any, key: str) -> str | None:
    p = parse_period(scale, key)
    if p is None:
        return None
    if p.scale == "day":
        return (p.start + timedelta(days=1)).isoformat()
    if p.scale == "week":
        iso = (p.start + timedelta(days=7)).isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if p.scale == "month":
        return f"{p.start.year}-{p.start.month + 1:02d}" if p.start.month < 12 else f"{p.start.year + 1}-01"
    if p.scale == "quarter":
        _, q = _parse_quarter_key(key)
        return f"{p.start.year}-Q{q + 1}" if q < 4 else f"{p.start.year + 1}-Q1"
    if p.scale == "half":
        _, h = _parse_half_key(key)
        return f"{p.start.year}-H2" if h == "H1" else f"{p.start.year + 1}-H1"
    if p.scale == "year":
        return str(p.start.year + 1)
    return None


def hierarchical_relpath(scale: Any, key: str) -> Path | None:
    p = parse_period(scale, key)
    if p is None:
        return None
    year = p.start.year
    half = "H1" if p.start.month <= 6 else "H2"
    quarter = ((p.start.month - 1) // 3) + 1
    if p.scale == "day":
        return Path(str(year)) / half / f"Q{quarter}" / calendar.month_name[p.start.month] / f"{_ordinal(p.start.day)}.md"
    if p.scale == "week":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if p.scale == "month":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if p.scale == "quarter":
        return Path(str(year)) / half / f"Q{quarter}" / f"{key}.md"
    if p.scale == "half":
        return Path(str(year)) / half / f"{key}.md"
    if p.scale == "year":
        return Path(str(year)) / f"{key}.md"
    return None


def period_label(scale: Any, key: str) -> str:
    p = parse_period(scale, key)
    if p is None:
        return key
    return p.start.strftime("%A, %Y-%m-%d") if p.scale == "day" else key


def key_for_date(scale: Any, value: date) -> str:
    n = normalize_scale(scale)
    if n == "day": return value.isoformat()
    if n == "week":
        iso = value.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if n == "month": return value.strftime("%Y-%m")
    if n == "quarter": return f"{value.year}-Q{((value.month - 1) // 3) + 1}"
    if n == "half": return f"{value.year}-H{'1' if value.month <= 6 else '2'}"
    if n == "year": return str(value.year)
    raise ValueError(f"Unsupported period scale: {scale!r}")


def period_keys_in_range(scale: Any, start: date, end: date) -> list[str]:
    if end < start:
        raise ValueError("end must be on or after start")
    n = normalize_scale(scale)
    keys, seen, current = [], set(), start
    while current <= end:
        k = key_for_date(n, current)
        if k not in seen:
            seen.add(k)
            keys.append(k)
        current += timedelta(days=1)
    return keys


# ── Internal parsers ──────────────────────────────────────────────────────────

def _parse_week_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-W(\d{1,2})", key)
    if not m: raise ValueError(f"Invalid week key: {key}")
    year, week = int(m.group(1)), int(m.group(2))
    date.fromisocalendar(year, week, 1)
    return year, week

def _parse_month_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-(\d{2})", key)
    if not m: raise ValueError(f"Invalid month key: {key}")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12: raise ValueError(f"Invalid month key: {key}")
    return year, month

def _parse_quarter_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{4})-Q([1-4])", key)
    if not m: raise ValueError(f"Invalid quarter key: {key}")
    return int(m.group(1)), int(m.group(2))

def _parse_half_key(key: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d{4})-(H[12])", key)
    if not m: raise ValueError(f"Invalid half key: {key}")
    return int(m.group(1)), m.group(2)

def _parse_year_key(key: str) -> int:
    if not re.fullmatch(r"\d{4}", key): raise ValueError(f"Invalid year key: {key}")
    return int(key)

def _month_end(year: int, month: int) -> date:
    return date(year, 12, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)

def _ordinal(day: int) -> str:
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"
