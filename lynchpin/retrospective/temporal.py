"""Temporal scale utilities for narrative hierarchy navigation.

Pure functions for working with the day → week → month → quarter scale
hierarchy: navigating between scales, enumerating child keys, and
stepping forward/backward within a scale.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .narrative import NarrativeKind

SCALE_HIERARCHY: list[NarrativeKind] = [
    NarrativeKind.day,
    NarrativeKind.week,
    NarrativeKind.month,
    NarrativeKind.quarter,
]


def child_scale(scale: NarrativeKind) -> Optional[NarrativeKind]:
    """Return the next finer scale, or ``None`` for day."""
    try:
        idx = SCALE_HIERARCHY.index(scale)
    except ValueError:
        return None
    return SCALE_HIERARCHY[idx - 1] if idx > 0 else None


def child_keys(scale: NarrativeKind, key: str) -> list[str]:
    """Return constituent keys at the child scale.

    Examples::

        child_keys(week, "2026-W11")  → ["2026-03-09", ..., "2026-03-15"]
        child_keys(month, "2026-03")  → ["2026-W09", "2026-W10", ...]
        child_keys(quarter, "2026-Q1") → ["2026-01", "2026-02", "2026-03"]
    """
    if scale is NarrativeKind.week:
        year, week_num = int(key[:4]), int(key.split("W")[1])
        return [
            date.fromisocalendar(year, week_num, d).isoformat()
            for d in range(1, 8)
        ]

    if scale is NarrativeKind.month:
        year, month = int(key[:4]), int(key[5:7])
        # Find all ISO weeks that overlap this month
        first = date(year, month, 1)
        last_day = (
            date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
        )
        weeks: list[str] = []
        d = first
        seen: set[str] = set()
        while d <= last_day:
            iso = d.isocalendar()
            wk = f"{iso[0]}-W{iso[1]:02d}"
            if wk not in seen:
                seen.add(wk)
                weeks.append(wk)
            d += timedelta(days=1)
        return weeks

    if scale is NarrativeKind.quarter:
        year, q = int(key[:4]), int(key[-1])
        return [f"{year}-{(q - 1) * 3 + m:02d}" for m in range(1, 4)]

    return []


def prior_key(scale: NarrativeKind, key: str) -> Optional[str]:
    """Return the key for the period immediately preceding *key*."""
    try:
        if scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            if week_num > 1:
                return f"{year}-W{week_num - 1:02d}"
            prior = date.fromisocalendar(year - 1, 52, 1)
            return f"{prior.isocalendar()[0]}-W{prior.isocalendar()[1]:02d}"
        if scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            if month > 1:
                return f"{year}-{month - 1:02d}"
            return f"{year - 1}-12"
        if scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            if q > 1:
                return f"{year}-Q{q - 1}"
            return f"{year - 1}-Q4"
    except (ValueError, IndexError):
        pass
    return None


def next_key(scale: NarrativeKind, key: str) -> Optional[str]:
    """Return the key for the period immediately following *key*."""
    try:
        if scale is NarrativeKind.week:
            year, week_num = int(key[:4]), int(key.split("W")[1])
            if week_num < 52:
                return f"{year}-W{week_num + 1:02d}"
            next_year = year + 1
            return f"{next_year}-W01"
        if scale is NarrativeKind.month:
            year, month = int(key[:4]), int(key[5:7])
            if month < 12:
                return f"{year}-{month + 1:02d}"
            return f"{year + 1}-01"
        if scale is NarrativeKind.quarter:
            year, q = int(key[:4]), int(key[-1])
            if q < 4:
                return f"{year}-Q{q + 1}"
            return f"{year + 1}-Q1"
    except (ValueError, IndexError):
        pass
    return None
