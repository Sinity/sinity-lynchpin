from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional

from ...core.config import get_config


@dataclass
class ActivityWatchEvent:
    bucket: str
    start: datetime
    end: datetime
    data: Dict[str, object]


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    cfg = get_config()
    path = Path(db_path).expanduser() if db_path else cfg.activitywatch_db
    return sqlite3.connect(str(path))


def _time_range(day: Optional[date], start: Optional[datetime], end: Optional[datetime]) -> tuple[datetime, datetime]:
    if start and end:
        return start, end
    if day is None:
        raise ValueError("Either day or start/end must be provided")
    start_dt = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    return start_dt, start_dt + timedelta(days=1)


def iter_events(
    bucket_prefix: str,
    *,
    day: Optional[date] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db_path: Optional[Path] = None,
) -> Iterator[ActivityWatchEvent]:
    since, until = _time_range(day, start, end)
    since_ns = int(since.timestamp() * 1_000_000_000)
    until_ns = int(until.timestamp() * 1_000_000_000)
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow "
        "WHERE b.name LIKE ? AND e.starttime < ? AND e.endtime > ? ORDER BY e.starttime"
    )
    pattern = f"{bucket_prefix}%"
    with _connect(db_path=db_path) as conn:
        for bucket, start_ns, end_ns, payload in conn.execute(query, (pattern, until_ns, since_ns)):
            if start_ns is None or end_ns is None:
                continue
            start_dt = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
            data: Dict[str, object] = {}
            if payload:
                try:
                    decoded = payload if isinstance(payload, str) else payload.decode("utf-8")
                    data = json.loads(decoded)
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    data = {}
            yield ActivityWatchEvent(bucket=bucket, start=start_dt, end=end_dt, data=data)


def iter_events_all(
    bucket_prefix: str,
    *,
    db_path: Optional[Path] = None,
) -> Iterator[ActivityWatchEvent]:
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow "
        "WHERE b.name LIKE ? ORDER BY e.starttime"
    )
    pattern = f"{bucket_prefix}%"
    with _connect(db_path=db_path) as conn:
        for bucket, start_ns, end_ns, payload in conn.execute(query, (pattern,)):
            if start_ns is None or end_ns is None:
                continue
            start_dt = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
            data: Dict[str, object] = {}
            if payload:
                try:
                    decoded = payload if isinstance(payload, str) else payload.decode("utf-8")
                    data = json.loads(decoded)
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    data = {}
            yield ActivityWatchEvent(bucket=bucket, start=start_dt, end=end_dt, data=data)


def window_events(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events("aw-watcher-window_", **kwargs)


def afk_events(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events("aw-watcher-afk_", **kwargs)


def web_events(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events("aw-watcher-web_", **kwargs)


def window_events_all(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events_all("aw-watcher-window_", **kwargs)


def afk_events_all(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events_all("aw-watcher-afk_", **kwargs)


def web_events_all(**kwargs) -> Iterator[ActivityWatchEvent]:
    return iter_events_all("aw-watcher-web_", **kwargs)
