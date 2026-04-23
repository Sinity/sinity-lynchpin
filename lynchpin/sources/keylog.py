"""Keylog metadata source.

This module intentionally exposes only timing/count metadata. Raw snapshot
buffers can contain typed text, so callers should derive activity evidence from
event timestamps, keycodes, and session/window metadata instead of reading
captured content.
"""

from __future__ import annotations

import json
from functools import lru_cache
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.parse import as_local, iter_dates, parse_datetime

__all__ = [
    "KeylogEvent",
    "KeylogDayActivity",
    "log_files",
    "events",
    "keypresses",
    "keypress_count",
    "has_coverage",
    "daily_activity",
]


@dataclass(frozen=True)
class KeylogEvent:
    ts: datetime
    event: str
    session: str | None
    window: str | None
    keycode: str | None
    changed: bool | None


@dataclass(frozen=True)
class KeylogDayActivity:
    date: date
    event_count: int
    keypress_count: int
    changed_keypress_count: int
    session_count: int
    first_ts: datetime | None
    last_ts: datetime | None


def _logs_root() -> Path:
    return get_config().keylog_root / "logs"


def _date_from_name(path: Path) -> date | None:
    try:
        return date.fromisoformat(path.stem)
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _indexed_log_files(root: str) -> tuple[tuple[date, Path], ...]:
    path = Path(root)
    if not path.exists():
        return ()
    rows = []
    for item in path.glob("*.jsonl"):
        d = _date_from_name(item)
        if d is not None:
            rows.append((d, item))
    return tuple(sorted(rows, key=lambda row: row[0]))


def log_files(*, start: Optional[date] = None, end: Optional[date] = None) -> list[Path]:
    root = _logs_root()
    files = []
    for d, path in _indexed_log_files(str(root)):
        if start and d < start:
            continue
        if end and d > end:
            continue
        files.append(path)
    return sorted(files)


def _candidate_files(start: datetime, end: datetime) -> list[Path]:
    # Files are named by the UTC log date, while callers usually pass local
    # datetimes. Pad by one day so midnight-adjacent local intervals still see
    # the corresponding UTC file.
    s = as_local(start).date() - timedelta(days=1)
    e = as_local(end).date() + timedelta(days=1)
    return log_files(start=s, end=e)


def events(
    *, start: datetime, end: datetime, kinds: Optional[set[str]] = None,
) -> Iterator[KeylogEvent]:
    start_local = as_local(start)
    end_local = as_local(end)
    for path in _candidate_files(start_local, end_local):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = str(rec.get("event") or "")
                if kinds is not None and kind not in kinds:
                    continue
                ts = parse_datetime(rec.get("ts"))
                if ts is None:
                    continue
                ts_local = as_local(ts)
                if ts_local < start_local or ts_local > end_local:
                    continue
                yield KeylogEvent(
                    ts=ts_local,
                    event=kind,
                    session=rec.get("session"),
                    window=rec.get("window"),
                    keycode=rec.get("keycode"),
                    changed=rec.get("changed") if isinstance(rec.get("changed"), bool) else None,
                )


def keypresses(*, start: datetime, end: datetime) -> list[KeylogEvent]:
    return list(events(start=start, end=end, kinds={"press"}))


def keypress_count(*, start: datetime, end: datetime) -> int:
    start_local = as_local(start)
    end_local = as_local(end)
    total = 0
    for path in _candidate_files(start_local, end_local):
        try:
            stat = path.stat()
        except OSError:
            continue
        for ts in _press_timestamps(str(path), stat.st_mtime_ns, stat.st_size):
            if start_local <= ts <= end_local:
                total += 1
    return total


@lru_cache(maxsize=512)
def _press_timestamps(path: str, mtime_ns: int, size: int) -> tuple[datetime, ...]:
    _ = (mtime_ns, size)
    result = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") != "press":
                continue
            ts = parse_datetime(rec.get("ts"))
            if ts is not None:
                result.append(as_local(ts))
    return tuple(result)


def has_coverage(*, start: datetime, end: datetime) -> bool:
    return bool(_candidate_files(start, end))


def daily_activity(*, start: date, end: date) -> list[KeylogDayActivity]:
    result = []
    for d in iter_dates(start, end):
        s = datetime.combine(d, datetime.min.time())
        e = datetime.combine(d + timedelta(days=1), datetime.min.time())
        day_events = list(events(start=s, end=e))
        keypress_events = [ev for ev in day_events if ev.event == "press"]
        changed_events = [ev for ev in keypress_events if ev.changed is True]
        sessions = {ev.session for ev in day_events if ev.session}
        timestamps = [ev.ts for ev in day_events]
        result.append(KeylogDayActivity(
            date=d,
            event_count=len(day_events),
            keypress_count=len(keypress_events),
            changed_keypress_count=len(changed_events),
            session_count=len(sessions),
            first_ts=min(timestamps) if timestamps else None,
            last_ts=max(timestamps) if timestamps else None,
        ))
    return result
