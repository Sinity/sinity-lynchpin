from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterator


def parse_iso_dateish(value: str) -> date:
    text = value.strip()
    if not text:
        raise ValueError("Date value cannot be empty")
    if len(text) <= 10:
        return date.fromisoformat(text)
    return datetime.fromisoformat(text.replace("Z", "+00:00")).date()


def iter_dates(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
