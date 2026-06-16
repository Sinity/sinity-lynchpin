"""Raw ActivityWatch SQLite access."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from bisect import bisect_left
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Sequence

import functools

from ..core.cache import file_signature
from ..core.config import get_config

log = logging.getLogger(__name__)
from .activitywatch_models import AWEvent
from .activitywatch_event_index import iter_indexed_activitywatch_events


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
        from ..materialization import ensure_materialized

        window = _datetime_window(start, end)
        canonical_result = ensure_materialized("activitywatch", window=window)
        index_result = ensure_materialized("activitywatch_event_index", window=window)
        if index_result.status in {"ready", "updated"}:
            yield from iter_indexed_activitywatch_events(
                bucket_prefix=bucket_prefix,
                start=start,
                end=end,
            )
            return
        if canonical_result.status not in {"ready", "updated"}:
            yield from events_from_activitywatch_dbs(bucket_prefix, start=start, end=end)
            return
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
    bucket_prefix: str | Sequence[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    db_path: Optional[Path] = None,
) -> Iterator[AWEvent]:
    prefixes = (bucket_prefix,) if isinstance(bucket_prefix, str) else tuple(bucket_prefix)
    if not prefixes:
        return
    prefix_clause = " OR ".join("b.name LIKE ?" for _prefix in prefixes)
    query = (
        "SELECT b.name, e.starttime, e.endtime, e.data "
        f"FROM events e JOIN buckets b ON b.id = e.bucketrow WHERE ({prefix_clause})"
    )
    params: list[object] = [f"{prefix}%" for prefix in prefixes]
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
        if not _candidate_may_overlap(candidate, prefixes=prefixes, start=start, end=end):
            continue
        # `with conn:` only manages the transaction, not the handle; closing()
        # guarantees the sqlite connection is released per archive DB so we do
        # not leak file handles across the (potentially many) candidate DBs.
        with closing(_connect(candidate)) as conn:
            cursor = conn.execute(query, params)
            for bucket, start_ns, end_ns, payload in cursor:
                if start_ns is None or end_ns is None:
                    continue
                # Window-watcher events from aw-server-rust are stored
                # zero-duration: end == start. The effective duration is
                # implicit (until the next event for the same bucket).
                # _window_spans handles this downstream. Dropping these
                # silently destroyed ~4M events in Feb-May 2026 alone.
                # Keep zero-duration; only drop genuinely-invalid (end < start).
                if end_ns < start_ns:
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


def _candidate_may_overlap(
    path: Path,
    *,
    prefixes: tuple[str, ...],
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if start is None and end is None:
        return True
    bounds = _db_event_bounds(path, file_signature(path), prefixes)
    if bounds is False:
        return False
    if bounds is None:
        return True
    first_ns, last_ns = bounds
    if end is not None and first_ns >= int(end.timestamp() * 1_000_000_000):
        return False
    if start is not None and last_ns <= int(start.timestamp() * 1_000_000_000):
        return False
    return True


@functools.lru_cache(maxsize=64)
def _db_event_bounds(
    path: Path,
    signature: object,
    prefixes: tuple[str, ...],
) -> tuple[int, int] | bool | None:
    del signature
    if not prefixes:
        return None
    try:
        with closing(_connect(path)) as conn:
            bucket_ids = _matching_bucket_ids(conn, prefixes)
            if not bucket_ids:
                return False
            placeholders = ",".join("?" for _bucket_id in bucket_ids)
            row = conn.execute(
                f"SELECT MIN(starttime), MAX(endtime) FROM events WHERE bucketrow IN ({placeholders})",
                list(bucket_ids),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or row[0] is None or row[1] is None:
        return False
    return int(row[0]), int(row[1])


def _matching_bucket_ids(conn: sqlite3.Connection, prefixes: tuple[str, ...]) -> tuple[int, ...]:
    clauses = " OR ".join("name LIKE ?" for _prefix in prefixes)
    rows = conn.execute(
        f"SELECT id FROM buckets WHERE {clauses}",
        [f"{prefix}%" for prefix in prefixes],
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _events_from_ndjson(
    path: Path,
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
) -> Iterator[AWEvent]:
    """Yield AW events matching ``bucket_prefix`` and overlapping [start, end).

    Backed by a process-cached full parse — the 450MB NDJSON is parsed
    exactly once per process, then sliced in memory per call. The cache
    is keyed by the file path and refreshes when ``path.stat()`` changes
    (mtime + size), so a re-materialize is observed automatically.
    """
    by_bucket = _load_events_by_bucket(path)
    for bucket, bucket_events in by_bucket.items():
        if not bucket.startswith(bucket_prefix):
            continue
        starts = tuple(event.start for event in bucket_events)
        idx = bisect_left(starts, start)
        # Non-window events can overlap the left edge. Step back until events no
        # longer cross the requested start; zero-duration window events stay at
        # idx and keep their existing implicit-duration behavior downstream.
        while idx > 0 and bucket_events[idx - 1].end > start:
            idx -= 1
        for event in bucket_events[idx:]:
            if event.start >= end:
                break
            if event.end <= start:
                continue
            yield event


def _load_events_by_bucket(path: Path) -> dict[str, tuple[AWEvent, ...]]:
    """Return cached AW events grouped by bucket and sorted by start time.

    ``activitywatch.daily_activity`` calls multiple derived helpers over the
    same window. Each helper queries one bucket prefix; scanning the full parsed
    450MB event list for every prefix made one-shot refreshes spend tens of
    seconds before the first daily row. Bucket indexing keeps the full-file parse
    cost once per process, then slices only the matching bucket.
    """
    return _load_events_by_bucket_keyed(path, file_signature(path))


@functools.lru_cache(maxsize=2)
def _load_events_by_bucket_keyed(
    path: Path,
    signature: object,
) -> dict[str, tuple[AWEvent, ...]]:
    del signature
    buckets: dict[str, list[AWEvent]] = {}
    for event in _load_all_events(path):
        buckets.setdefault(event.bucket, []).append(event)
    return {bucket: tuple(events) for bucket, events in buckets.items()}


def _load_all_events(path: Path) -> list[AWEvent]:
    """Parse the entire AW NDJSON once per process. Caller filters.

    Cached process-level keyed by ``(path, file_signature(path))`` so a
    re-materialize invalidates the cache automatically within the same
    process AND a stale process serving an older parse is bounded — first
    call after the materialize sees the new signature, prior entries
    drop on LRU eviction.

    Persistent caching via cachew was attempted but rejected:
    ``AWEvent.data: Dict[str, object]`` is not a cacheable schema. The
    process-level layer is the layer we get. Long-running MCP server /
    substrate promote benefit; one-shot CLIs pay the full parse each
    invocation.

    Returns events sorted by ``start`` so callers can stop scanning early.
    """
    return _load_all_events_keyed(path, file_signature(path))


@functools.lru_cache(maxsize=2)
def _load_all_events_keyed(
    path: Path, signature: object,
) -> list[AWEvent]:
    """Inner cache target. The signature parameter participates in the
    LRU key so re-materialize forces a re-parse on next call."""
    del signature  # not used by the body, just the cache key
    rows: list[AWEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                log.warning("activitywatch_raw: skipping corrupted NDJSON line in %s", path)
                continue
            bucket = str(payload.get("bucket") or "")
            if not bucket:
                continue
            event_start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
            event_end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
            data = payload.get("data")
            rows.append(
                AWEvent(
                    bucket=bucket,
                    start=event_start,
                    end=event_end,
                    data=data if isinstance(data, dict) else {},
                )
            )
    rows.sort(key=lambda event: event.start)
    return rows


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
        # closing() releases the handle even though `with conn:` (transaction
        # context) would not — otherwise scanning every archive DB leaks one
        # sqlite handle per candidate.
        with closing(conn):
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
    """Yield events from any aw-watcher-web browser-tab tracker.

    The bucket-name convention for web watchers is
    ``aw-watcher-web-<browser>_<hostname>`` (hyphen separator between
    ``web`` and the browser name, then underscore for hostname). E.g.
    ``aw-watcher-web-chrome_desktop``, ``aw-watcher-web-firefox``.

    The old prefix here (``aw-watcher-web_``) matched none of the
    5 buckets that actually exist in the operator's archive, so all
    372k web-tab events were silently invisible to lynchpin. Use the
    hyphen prefix to catch all per-browser variants.
    """
    return events("aw-watcher-web-", **kw)


def _datetime_window(start: datetime, end: datetime) -> tuple[date, date]:
    end_date = end.date()
    if (end.hour, end.minute, end.second, end.microsecond) != (0, 0, 0, 0):
        end_date += timedelta(days=1)
    return (start.date(), end_date)
