"""Shared parsing utilities: date/int/float parsing, month keys, date iteration."""

from __future__ import annotations

import os
from functools import lru_cache
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional
from datetime import tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_date(raw: str, *fmts: str) -> Optional[datetime]:
    """Try multiple date formats, return None on failure.

    If no formats given, tries common defaults then ISO fallback.
    """
    raw = raw.strip()
    if not raw:
        return None
    formats = fmts or ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S")
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None



def parse_int(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_float(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def month_key(dt: datetime) -> str:
    """Format a datetime as 'YYYY-MM' month key."""
    return f"{dt.year:04d}-{dt.month:02d}"


def in_month_range(key: str, start_month: str, end_month: str) -> bool:
    """Check if a month key falls within [start_month, end_month] inclusive."""
    return start_month <= key <= end_month


def iter_dates(start: date, end: date) -> Iterator[date]:
    """Yield each date from start to end inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


# ── Polymorphic parsers (accept any input type) ──────────────────────────────


def parse_datetime(value: object) -> Optional[datetime]:
    """Parse anything into a datetime. Handles str, datetime, None, Z-suffix, ' UTC' suffix."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif text.endswith(" UTC"):
        text = text[:-4] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_date_from_any(value: object) -> Optional[date]:
    """Parse anything into a date. Handles str, date, datetime, None."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        dt = parse_datetime(text)
        if dt:
            return dt.date()
        # Fallback: multi-format (e.g., Goodreads YYYY/MM/DD)
        dt = parse_date(text)
        return dt.date() if dt else None


def safe_float(value: object) -> Optional[float]:
    """Coerce anything to float, or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (ValueError, TypeError):
        return None


def safe_int(value: object) -> Optional[int]:
    """Coerce anything to int, or None."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# ── Timezone helpers ──────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def local_tz() -> tzinfo:
    """Get the local timezone with historical DST rules when available."""
    from datetime import timezone
    candidates = []
    if os.environ.get("TZ"):
        candidates.append(os.environ["TZ"])
    try:
        localtime = Path("/etc/localtime").resolve()
        parts = localtime.parts
        if "zoneinfo" in parts:
            idx = parts.index("zoneinfo")
            candidates.append("/".join(parts[idx + 1:]))
    except OSError:
        pass
    for name in candidates:
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            continue
    return datetime.now().astimezone().tzinfo or timezone.utc


def as_local(value: datetime | date) -> datetime:
    """Convert a datetime (or date) to local-timezone datetime.

    Accepts a plain ``date`` to interpret as midnight local time. Callers
    using a date as an end-of-window bound must call ``end_of_day_local``
    instead; otherwise ``date(d), date(d)`` collapses to a zero-width
    window and queries silently return nothing.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=local_tz())
        return value.astimezone(local_tz())
    return datetime(value.year, value.month, value.day, tzinfo=local_tz())


def end_of_day_local(value: datetime | date) -> datetime:
    """Convert a date/datetime to the exclusive end-of-day local datetime.

    For a date: returns midnight of the following day in local tz, which
    is the natural half-open ``[start, end)`` upper bound for day-inclusive
    queries. For a datetime: returns as_local(value) unchanged — caller
    already chose the bound semantics.
    """
    if isinstance(value, datetime):
        return as_local(value)
    next_day = value + timedelta(days=1)
    return datetime(next_day.year, next_day.month, next_day.day, tzinfo=local_tz())
