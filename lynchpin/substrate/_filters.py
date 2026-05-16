"""Shared SQL filter helpers for substrate readers."""

from __future__ import annotations

from datetime import date
from typing import Any


def build_where(clauses: list[str], params: list[Any]) -> str:
    """Return a WHERE clause string including the keyword, or empty string."""
    if not clauses:
        return ""
    return "WHERE " + " AND ".join(clauses)


def add_date_filter(
    column: str,
    start: date | None,
    end: date | None,
    clauses: list[str],
    params: list[Any],
    *,
    nullable: bool = False,
) -> None:
    """Append date range clauses using ``column::DATE`` comparisons."""
    if start is None and end is None:
        return
    if nullable:
        clauses.append(f"{column} IS NOT NULL")
    if start is not None and end is not None:
        clauses.append(f"{column}::DATE BETWEEN ? AND ?")
        params.extend([start, end])
    elif start is not None:
        clauses.append(f"{column}::DATE >= ?")
        params.append(start)
    else:
        clauses.append(f"{column}::DATE <= ?")
        params.append(end)


def add_in_filter(
    column: str,
    values: tuple[str, ...] | None,
    clauses: list[str],
    params: list[Any],
) -> None:
    """Append an IN clause for a string tuple filter."""
    if not values:
        return
    placeholders = ", ".join("?" * len(values))
    clauses.append(f"{column} IN ({placeholders})")
    params.extend(values)
