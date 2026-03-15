from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterator


def summarize_onenote_journal_entries(
    path: Path,
    start_month: str,
    end_month: str,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    header_re = re.compile(r"^###\s+(\d{2})\.(\d{2})\.(\d{4})")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            match = header_re.match(line.strip())
            if not match:
                continue
            _, month_i, year = (int(part) for part in match.groups())
            month = f"{year:04d}-{month_i:02d}"
            if _month_in_range(month, start_month, end_month):
                counts[month] += 1
    return dict(counts)


def summarize_substance_log_headings(
    path: Path,
    start_month: str,
    end_month: str,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    heading_re = re.compile(r"^####\s+(\d{2}\.\d{2}\.\d{4})(?:\s+to\s+(\d{2}\.\d{2}\.\d{4}))?")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            match = heading_re.match(line.strip())
            if not match:
                continue
            start = _parse_day(match.group(1))
            end = _parse_day(match.group(2)) if match.group(2) else start
            for month in _iter_months(_month_key(start), _month_key(end)):
                if _month_in_range(month, start_month, end_month):
                    counts[month] += 1
    return dict(counts)


def _parse_day(raw: str) -> date:
    return datetime.strptime(raw, "%d.%m.%Y").date()


def _month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _month_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def _iter_months(start_month: str, end_month: str) -> Iterator[str]:
    year, month = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_i = (int(part) for part in end_month.split("-", 1))
    while (year, month) <= (end_year, end_month_i):
        yield f"{year:04d}-{month:02d}"
        month += 1
        if month == 13:
            month = 1
            year += 1
