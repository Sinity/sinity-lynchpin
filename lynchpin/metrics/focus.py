"""AFK-adjusted focus spans and app category classification."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

FALSE_ACTIVE_APPS = {"gcr-prompter"}


def duration_minutes(event) -> float:
    """Compute event duration in minutes from start/end attributes."""
    start = getattr(event, "start", None)
    end = getattr(event, "end", None)
    if not start or not end:
        return 0.0
    delta = end - start
    if not isinstance(delta, timedelta):
        return 0.0
    return max(delta.total_seconds() / 60.0, 0.0)


def window_label(data: Dict[str, object]) -> str:
    """Extract human-readable app label from ActivityWatch event data."""
    for key in ("app", "application", "appname", "bundle"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    title = data.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:80]
    return "unknown"


def focus_minutes(events: Sequence) -> Counter:
    """Compute per-app focus minutes from window events."""
    counter: Counter = Counter()
    for event in events:
        minutes = duration_minutes(event)
        if minutes <= 0:
            continue
        data = getattr(event, "data", {}) or {}
        label = window_label(data) or "unknown"
        counter[label] += minutes
    return counter


def _calculate_false_active_minutes(
    active_intervals: List[Tuple[datetime, datetime]],
    windows: Sequence,
) -> float:
    """Calculate minutes where 'active' status was caused by false-positive apps."""
    total_false_active = 0.0
    bad_windows = [
        w for w in windows
        if (window_label(w.data or {}) in FALSE_ACTIVE_APPS
            or (w.data or {}).get("app") in FALSE_ACTIVE_APPS)
    ]
    if not bad_windows:
        return 0.0
    for w in bad_windows:
        w_start = getattr(w, "start", None)
        w_end = getattr(w, "end", None)
        if not w_start or not w_end:
            continue
        for (a_start, a_end) in active_intervals:
            latest_start = max(w_start, a_start)
            earliest_end = min(w_end, a_end)
            if earliest_end > latest_start:
                duration = (earliest_end - latest_start).total_seconds() / 60.0
                total_false_active += duration
    return total_false_active


def afk_split(events: Sequence, windows: Sequence = ()) -> Tuple[float, float]:
    """Split AFK events into (active_hours, afk_hours), correcting for false actives."""
    active_minutes = 0.0
    afk_minutes = 0.0
    active_intervals: List[Tuple[datetime, datetime]] = []

    for event in events:
        minutes = duration_minutes(event)
        data = getattr(event, "data", {}) or {}
        status = str(data.get("status") or "").lower()

        is_afk = False
        if status in {"afk", "away"}:
            is_afk = True
        elif status in {"not-afk", "active", "present"}:
            is_afk = False
        else:
            flag = data.get("afk")
            if isinstance(flag, bool):
                is_afk = flag
            elif isinstance(flag, str):
                is_afk = flag.lower() == "true"

        if is_afk:
            afk_minutes += minutes
        else:
            active_minutes += minutes
            start = getattr(event, "start", None)
            end = getattr(event, "end", None)
            if start and end:
                active_intervals.append((start, end))

    if windows and active_intervals:
        false_active = _calculate_false_active_minutes(active_intervals, windows)
        false_active = min(false_active, active_minutes)
        active_minutes -= false_active
        afk_minutes += false_active

    return active_minutes / 60.0, afk_minutes / 60.0
