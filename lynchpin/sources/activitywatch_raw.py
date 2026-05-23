"""Raw ActivityWatch SQLite access."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..core.config import get_config
from .activitywatch_models import AWEvent


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser() if db_path else get_config().activitywatch_db
    return sqlite3.connect(str(path))


def _candidate_dbs(db_path: Optional[Path] = None) -> tuple[Path, ...]:
    if db_path is not None:
        return (Path(db_path).expanduser(),)
    cfg = get_config()
    paths = [cfg.activitywatch_db]
    archive_dir = getattr(cfg, "activitywatch_archive_db_dir", None)
    if isinstance(archive_dir, Path) and archive_dir.exists():
        paths.extend(sorted(path for path in archive_dir.glob("*.db") if path.is_file()))
        paths.extend(sorted(path for path in archive_dir.glob("*.sqlite") if path.is_file()))
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)


def canonical_activitywatch_events_path() -> Path:
    return get_config().captures_root / "activitywatch/events.ndjson"


def events(
    bucket_prefix: str,
    *,
    start: datetime,
    end: datetime,
    db_path: Optional[Path] = None,
) -> Iterator[AWEvent]:
    if db_path is None:
        path = canonical_activitywatch_events_path()
        if not path.exists():
            raise FileNotFoundError(
                f"canonical ActivityWatch materialization is missing: {path}. "
                "Run python -m lynchpin.ingest.activitywatch_materialize."
            )
        yield from _events_from_ndjson(path, bucket_prefix=bucket_prefix, start=start, end=end)
        return
    yield from events_from_activitywatch_dbs(bucket_prefix, start=start, end=end, db_path=db_path)


def events_from_activitywatch_dbs(
    bucket_prefix: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    db_path: Optional[Path] = None,
) -> Iterator[AWEvent]:
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow WHERE b.name LIKE ?"
    )
    params: list[object] = [f"{bucket_prefix}%"]
    if end is not None:
        until_ns = int(end.timestamp() * 1_000_000_000)
        query += " AND e.starttime < ?"
        params.append(until_ns)
    if start is not None:
        since_ns = int(start.timestamp() * 1_000_000_000)
        query += " AND e.endtime > ?"
        params.append(since_ns)
    query += " ORDER BY e.starttime"
    seen: set[tuple[str, int, int, str]] = set()
    rows: list[AWEvent] = []
    for candidate in _candidate_dbs(db_path):
        with _connect(candidate) as conn:
            cursor = conn.execute(query, params)
            for bucket, start_ns, end_ns, payload in cursor:
                if start_ns is None or end_ns is None:
                    continue
                if end_ns <= start_ns:
                    continue
                payload_text = payload if isinstance(payload, str) else payload.decode("utf-8") if payload else ""
                key = (str(bucket), int(start_ns), int(end_ns), payload_text)
                if key in seen:
                    continue
                seen.add(key)
                data: Dict[str, object] = {}
                if payload_text:
                    try:
                        data = json.loads(payload_text)
                    except json.JSONDecodeError:
                        pass
                s = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
                e = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
                rows.append(AWEvent(bucket=bucket, start=s, end=e, data=data))
    yield from sorted(rows, key=lambda event: event.start)


def _events_from_ndjson(
    path: Path,
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
) -> Iterator[AWEvent]:
    rows: list[AWEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            bucket = str(payload.get("bucket") or "")
            if not bucket.startswith(bucket_prefix):
                continue
            event_start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            event_end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
            if event_start >= end or event_end <= start:
                continue
            data = payload.get("data")
            rows.append(
                AWEvent(
                    bucket=bucket,
                    start=event_start,
                    end=event_end,
                    data=data if isinstance(data, dict) else {},
                )
            )
    yield from sorted(rows, key=lambda event: event.start)


def event_bounds(bucket_prefix: str, *, db_path: Optional[Path] = None) -> tuple[date | None, date | None, int]:
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        "FROM events e JOIN buckets b ON b.id = e.bucketrow "
        "WHERE b.name LIKE ?"
    )
    seen: set[tuple[str, int, int, str]] = set()
    first: date | None = None
    last: date | None = None
    count = 0
    for candidate in _candidate_dbs(db_path):
        try:
            conn = _connect(candidate)
        except sqlite3.Error:
            continue
        with conn:
            try:
                rows = conn.execute(query, (f"{bucket_prefix}%",))
            except sqlite3.Error:
                continue
            for bucket, start_ns, end_ns, payload in rows:
                if start_ns is None or end_ns is None or end_ns <= start_ns:
                    continue
                payload_text = payload if isinstance(payload, str) else payload.decode("utf-8") if payload else ""
                key = (str(bucket), int(start_ns), int(end_ns), payload_text)
                if key in seen:
                    continue
                seen.add(key)
                day = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc).date()
                first = day if first is None or day < first else first
                last = day if last is None or day > last else last
                count += 1
    return first, last, count


def window_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-window_", **kw)


def afk_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-afk_", **kw)


def web_events(**kw: Any) -> Iterator[AWEvent]:
    return events("aw-watcher-web_", **kw)
