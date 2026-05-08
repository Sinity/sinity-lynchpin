"""Calendar event source (M.12).

Parses calendar events from a flat JSONL "processed" file produced by an
upstream ingestion pass over Google Takeout / iCal exports / future
sinex-side calendar capture. The processed schema is the durable contract;
the upstream parser is intentionally not in this module so the source
stays cheap and focused.

Processed file format (one JSON object per line):

    {
      "uid": "abc123@google.com",
      "calendar": "Personal",
      "summary": "Meeting with Scott",
      "start_at": "2026-05-08T15:00:00+02:00",
      "end_at":   "2026-05-08T16:00:00+02:00",
      "all_day":  false,
      "location": "Zoom",
      "attendees": ["scott@example.com"],
      "description": "Quarterly sync",
      "status": "confirmed",
      "created_at": "2026-05-01T10:00:00+02:00",
      "updated_at": "2026-05-07T20:00:00+02:00"
    }

Path resolved from ``LynchpinConfig.calendar_jsonl`` when the file exists.
When absent, all readers return an empty iterator (degraded mode); the
context-pack readiness layer surfaces this via ``source_readiness``.

Composite consumers can use this source for capacity-vs-commitments
reconciliation: which days had heavy meeting load? did focus blocks
overlap calendar holds?
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from ..core.parse import parse_datetime as _parse_dt
from ..core.primitives import logical_date

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalendarEvent:
    """One calendar event from any source (Google, iCal, future sinex)."""
    uid: str
    calendar: str
    summary: str
    start_at: Optional[datetime]
    end_at: Optional[datetime]
    all_day: bool
    location: str
    attendees: tuple[str, ...]
    description: str
    status: str
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @property
    def duration_minutes(self) -> float:
        """Approximate duration; 0 for all-day or undated events."""
        if not self.start_at or not self.end_at:
            return 0.0
        return max(0.0, (self.end_at - self.start_at).total_seconds() / 60.0)

    @property
    def date(self) -> date | None:
        """Logical day for windowing; uses 06:00 boundary."""
        if self.start_at is None:
            return None
        return logical_date(self.start_at)


@dataclass(frozen=True)
class DailyCalendarLoad:
    """Per-day commitment summary."""
    date: date
    event_count: int
    total_minutes: float
    timed_minutes: float          # excludes all-day events
    all_day_count: int
    calendars: dict[str, int]      # calendar name → event count
    busy_window_minutes: float     # union of timed-event windows on this day


# ── public API ───────────────────────────────────────────────────────────────


def _calendar_jsonl_path() -> Path | None:
    from ..core.config import get_config

    config = get_config()
    path = getattr(config, "calendar_jsonl", None)
    if isinstance(path, Path):
        return path
    if isinstance(path, str):
        return Path(path)
    return None


def _calendar_path_or_default() -> Path | None:
    """Resolve the processed JSONL path; fall back to the conventional
    ``/realm/data/exports/google/processed/calendar.jsonl`` location
    so a manual ingestion pass writes there without code changes."""
    explicit = _calendar_jsonl_path()
    if explicit is not None and explicit.exists():
        return explicit
    fallback = Path("/realm/data/exports/google/processed/calendar.jsonl")
    if fallback.exists():
        return fallback
    return explicit if explicit is not None else fallback


def iter_events(
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[CalendarEvent]:
    """Yield calendar events from the processed JSONL file.

    Filters to ``[start, end]`` (logical-date inclusive) when bounds are
    supplied. Empty iterator when the processed file is absent.
    """
    path = _calendar_path_or_default()
    if path is None or not path.exists():
        return
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = _coerce(raw)
                if event is None:
                    continue
                event_day = event.date
                if event_day is None:
                    continue
                if start is not None and event_day < start:
                    continue
                if end is not None and event_day > end:
                    continue
                yield event
    except OSError as exc:
        logger.warning("calendar source read failed: %s", exc)


def daily_load(*, start: date, end: date) -> list[DailyCalendarLoad]:
    """Per-day commitment rollup for capacity-vs-load analysis."""
    by_day: dict[date, list[CalendarEvent]] = defaultdict(list)
    for event in iter_events(start=start, end=end):
        if event.date is None:
            continue
        by_day[event.date].append(event)

    results: list[DailyCalendarLoad] = []
    for day in sorted(by_day):
        events = by_day[day]
        timed = [e for e in events if not e.all_day and e.start_at and e.end_at]
        all_day = sum(1 for e in events if e.all_day)
        total_min = sum(e.duration_minutes for e in events)
        timed_min = sum(e.duration_minutes for e in timed)
        calendars: Counter[str] = Counter(e.calendar for e in events)
        busy_window = _busy_window_minutes(timed)
        results.append(DailyCalendarLoad(
            date=day,
            event_count=len(events),
            total_minutes=round(total_min, 1),
            timed_minutes=round(timed_min, 1),
            all_day_count=all_day,
            calendars=dict(calendars),
            busy_window_minutes=round(busy_window, 1),
        ))
    return results


# ── helpers ───────────────────────────────────────────────────────────────────


def _coerce(raw: dict) -> CalendarEvent | None:
    uid = str(raw.get("uid") or "").strip()
    if not uid:
        return None
    return CalendarEvent(
        uid=uid,
        calendar=str(raw.get("calendar") or "default"),
        summary=str(raw.get("summary") or ""),
        start_at=_parse_dt(raw.get("start_at")),
        end_at=_parse_dt(raw.get("end_at")),
        all_day=bool(raw.get("all_day") or False),
        location=str(raw.get("location") or ""),
        attendees=tuple(str(a) for a in (raw.get("attendees") or []) if a),
        description=str(raw.get("description") or ""),
        status=str(raw.get("status") or "confirmed"),
        created_at=_parse_dt(raw.get("created_at")),
        updated_at=_parse_dt(raw.get("updated_at")),
    )


def _busy_window_minutes(timed_events: list[CalendarEvent]) -> float:
    """Union duration of overlapping intervals on a single day.

    Two back-to-back 60-min meetings → 120 min busy window. Two
    overlapping 60-min meetings → 60 min busy window. Lets capacity
    analysis distinguish "fully booked but parallel" from "actually
    spent 4h in meetings."
    """
    intervals = sorted(
        (e.start_at, e.end_at) for e in timed_events
        if e.start_at and e.end_at and e.end_at > e.start_at
    )
    if not intervals:
        return 0.0
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += (cur_end - cur_start).total_seconds() / 60.0
            cur_start, cur_end = start, end
    total += (cur_end - cur_start).total_seconds() / 60.0
    return total


__all__ = [
    "CalendarEvent",
    "DailyCalendarLoad",
    "daily_load",
    "iter_events",
]
