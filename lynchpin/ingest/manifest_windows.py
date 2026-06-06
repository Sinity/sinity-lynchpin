"""Shared helpers for half-open materialization-window manifests."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


def half_open_dates(start: date, end: date) -> tuple[date, ...]:
    days: list[date] = []
    cursor = start
    while cursor < end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def read_manifest_covered_dates(path: Path) -> tuple[date, ...]:
    payload = _read_manifest(path)
    raw_dates = payload.get("covered_dates")
    if not isinstance(raw_dates, list):
        return ()
    dates: set[date] = set()
    for raw in raw_dates:
        if isinstance(raw, str):
            try:
                dates.add(date.fromisoformat(raw))
            except ValueError:
                continue
    return tuple(sorted(dates))


def merge_manifest_covered_dates(
    *,
    manifest: Path,
    start: date,
    end: date,
    observed_dates: Iterable[date] = (),
    fallback_to_bounds: bool = True,
) -> tuple[date, ...]:
    existing = {
        day
        for day in read_manifest_covered_dates(manifest)
        if not (start <= day < end)
    }
    if not existing and fallback_to_bounds:
        existing.update(
            day
            for day in _manifest_bound_dates(manifest)
            if not (start <= day < end)
        )
    existing.update(day for day in observed_dates if not (start <= day < end))
    existing.update(half_open_dates(start, end))
    return tuple(sorted(existing))


def _manifest_bound_dates(path: Path) -> tuple[date, ...]:
    payload = _read_manifest(path)
    first_raw = payload.get("first_date")
    last_raw = payload.get("last_date")
    if not first_raw or not last_raw:
        return ()
    try:
        first = date.fromisoformat(str(first_raw))
        last = date.fromisoformat(str(last_raw))
    except ValueError:
        return ()
    days: list[date] = []
    cursor = first
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
