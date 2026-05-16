"""Raw ActivityWatch SQLite access."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..core.config import get_config
from .activitywatch_models import AWEvent


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path else get_config().activitywatch_db
    return sqlite3.connect(str(path))


def events(
    bucket_prefix: str,
    *,
    start: datetime,
    end: datetime,
    db_path: Optional[Path] = None,
) -> Iterator[AWEvent]:
    since_ns = int(start.timestamp() * 1_000_000_000)
    until_ns = int(end.timestamp() * 1_000_000_000)
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow "
        "WHERE b.name LIKE ? AND e.starttime < ? AND e.endtime > ? ORDER BY e.starttime"
    )
    with _connect(db_path) as conn:
        for bucket, start_ns, end_ns, payload in conn.execute(
            query, (f"{bucket_prefix}%", until_ns, since_ns)
        ):
            if start_ns is None or end_ns is None:
                continue
            s = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            e = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
            data: Dict[str, object] = {}
            if payload:
                try:
                    data = json.loads(
                        payload if isinstance(payload, str) else payload.decode("utf-8")
                    )
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    pass
            yield AWEvent(bucket=bucket, start=s, end=e, data=data)


def window_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-window_", **kw)


def afk_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-afk_", **kw)


def web_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-web_", **kw)
