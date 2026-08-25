"""Raw ActivityWatch SQLite access."""

from __future__ import annotations

import json
import heapq
import sqlite3
import functools
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Literal, Optional, Sequence


from ..core.cache import file_signature
from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.parse import as_local
from ..core.primitives import logical_date
from .activitywatch_event_index import (
    activitywatch_event_index_manifest_path,
    iter_indexed_activitywatch_events,
)
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
    # activity/activitywatch/activitywatch since the 2026-08-17 subject
    # recut merged captures/activitywatch into the nested export-wave tree.
    return get_config().data_root / "activity/activitywatch/activitywatch/events.ndjson"


def events(
    bucket_prefix: str,
    *,
    start: datetime,
    end: datetime,
    db_path: Optional[Path] = None,
) -> Iterator[AWEvent]:
    start = as_local(start)
    end = as_local(end)
    if db_path is None:
        from ..materialization import ensure_materialized

        window = _datetime_window(start, end)
        indexed_last = _indexed_last_date()
        if indexed_last is not None and logical_date(start) > indexed_last:
            # The sparse index is intentionally refreshed separately from the
            # live tail. Reading that tail from SQLite avoids reparsing the
            # multi-million-row canonical NDJSON inside every derived pass.
            yield from events_from_activitywatch_dbs(
                bucket_prefix,
                start=start,
                end=end,
            )
            return
        index_result = ensure_materialized("activitywatch_event_index", window=window)
        if index_result.status in {"ready", "updated"}:
            yield from iter_indexed_activitywatch_events(
                bucket_prefix=bucket_prefix,
                start=start,
                end=end,
            )
            return
        canonical_result = ensure_materialized("activitywatch", window=window)
        if canonical_result.status not in {"ready", "updated"}:
            yield from events_from_activitywatch_dbs(bucket_prefix, start=start, end=end)
            return
        path = canonical_activitywatch_events_path()
        if not path.exists():
            raise FileNotFoundError(
                f"canonical ActivityWatch materialization is missing: {path}. "
                "Run python -m lynchpin.ingest.activitywatch_materialize."
            )
        try:
            yield from _events_from_ndjson(path, bucket_prefix=bucket_prefix, start=start, end=end)
        except MaterializationError:
            # An unindexed legacy carrier cannot be read in a bounded way.
            # Return to the owner-native SQLite source, whose indexed query is
            # physically bounded, instead of parsing all historical NDJSON.
            yield from events_from_activitywatch_dbs(bucket_prefix, start=start, end=end)
        return
    yield from events_from_activitywatch_dbs(bucket_prefix, start=start, end=end, db_path=db_path)


def _indexed_last_date() -> date | None:
    manifest = activitywatch_event_index_manifest_path()
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        value = payload.get("last_date")
        return date.fromisoformat(str(value)) if value else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def events_from_activitywatch_dbs(
    bucket_prefix: str | Sequence[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    db_path: Optional[Path] = None,
    order: Literal["start", "bucket"] = "start",
    dedupe: bool = True,
) -> Iterator[AWEvent]:
    if start is not None:
        start = as_local(start)
    if end is not None:
        end = as_local(end)
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
    if order == "bucket":
        query += " ORDER BY b.name, e.starttime, e.endtime, e.data"
    else:
        query += " ORDER BY e.starttime"
    streams: list[Iterator[AWEvent]] = []
    for candidate in _candidate_dbs(db_path):
        if not _candidate_may_overlap(candidate, prefixes=prefixes, start=start, end=end):
            continue
        streams.append(_iter_activitywatch_db_events(candidate, query, params))

    merged = heapq.merge(*streams, key=lambda event: _event_sort_key(event, order=order))
    seen: set[tuple[str, int, int, str]] = set()
    previous_key: tuple[str, int, int, str] | None = None
    for event in merged:
        key = _event_key(event)
        if dedupe:
            if key in seen:
                continue
            seen.add(key)
        elif order == "bucket" and key == previous_key:
            continue
        previous_key = key
        yield event


def _iter_activitywatch_db_events(
    candidate: Path,
    query: str,
    params: list[object],
) -> Iterator[AWEvent]:
    # Keep each connection alive for its cursor, but release it as soon as the
    # stream is exhausted. The merge above holds only one row per database.
    with closing(_connect(candidate)) as conn:
        for bucket, start_ns, end_ns, payload in conn.execute(query, params):
            if start_ns is None or end_ns is None or end_ns < start_ns:
                continue
            payload_text = payload if isinstance(payload, str) else payload.decode("utf-8") if payload else ""
            data: Dict[str, object] = {}
            if payload_text:
                try:
                    data = json.loads(payload_text)
                except json.JSONDecodeError:
                    pass
            s = datetime.fromtimestamp(start_ns / 1_000_000_000, tz=timezone.utc)
            e = datetime.fromtimestamp(end_ns / 1_000_000_000, tz=timezone.utc)
            yield AWEvent(bucket=bucket, start=s, end=e, data=data)


def _event_key(event: AWEvent) -> tuple[str, int, int, str]:
    return (
        event.bucket,
        int(event.start.timestamp() * 1_000_000_000),
        int(event.end.timestamp() * 1_000_000_000),
        json.dumps(event.data, ensure_ascii=False, sort_keys=True),
    )


def _event_sort_key(event: AWEvent, *, order: Literal["start", "bucket"]) -> tuple[object, ...]:
    key = _event_key(event)
    return key if order == "bucket" else (key[1],)


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
    if not isinstance(bounds, tuple):
        return bool(bounds)
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
    """Yield indexed canonical events matching a bounded window.

    A legacy carrier without a logical-day index is deliberately rejected.
    Falling back to a full parse here would make a small derived read scan all
    history; callers should use the owner-native bounded source or rebuild the
    carrier first.
    """
    from ..materializers.partition_store import ArtifactStore, ProductPartitionKey

    store = ArtifactStore(path.with_name(f".{path.stem}.partitions"))
    selected = store.logical_partitions()
    if selected:
        first = logical_date(start) - timedelta(days=1)
        last = logical_date(end - timedelta(microseconds=1)) if end > start else logical_date(start)
        cursor = first
        while cursor <= last:
            ref = selected.get(ProductPartitionKey.day("activitywatch.events", cursor))
            if ref is not None:
                for raw_line in store.read(ref).decode().splitlines():
                    if not raw_line.strip():
                        continue
                    payload = json.loads(raw_line)
                    if not isinstance(payload, dict):
                        continue
                    bucket = str(payload.get("bucket") or "")
                    if not bucket.startswith(bucket_prefix):
                        continue
                    event_start = datetime.fromisoformat(str(payload["start"]).replace("Z", "+00:00"))
                    event_end = datetime.fromisoformat(str(payload["end"]).replace("Z", "+00:00"))
                    zero_in_window = event_end == event_start and start <= event_start < end
                    if event_start < end and (event_end > start or zero_in_window):
                        data = payload.get("data")
                        yield AWEvent(bucket, event_start, event_end, data if isinstance(data, dict) else {})
            cursor += timedelta(days=1)
        return
    manifest = path.with_suffix(".manifest.json")
    indexed = _indexed_ndjson_window(path, manifest, bucket_prefix=bucket_prefix, start=start, end=end)
    if indexed is not None:
        yield from indexed
        return
    raise MaterializationError(
        "activitywatch.events",
        reason="canonical ActivityWatch carrier has no logical-day index; bounded reads require a rebuild",
    )


def _indexed_ndjson_window(
    path: Path,
    manifest: Path,
    *,
    bucket_prefix: str,
    start: datetime,
    end: datetime,
) -> Iterator[AWEvent] | None:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("row_order") != "logical_date":
        return None
    offsets = payload.get("row_offsets")
    if not isinstance(offsets, dict):
        return None
    parsed: list[tuple[date, int]] = []
    for raw_day, raw_offset in offsets.items():
        if not isinstance(raw_offset, int):
            continue
        try:
            parsed.append((date.fromisoformat(str(raw_day)), raw_offset))
        except ValueError:
            continue
    if not parsed:
        return None
    logical_start = logical_date(start)
    boundary_day = logical_start - timedelta(days=1)
    seek = max((offset for day, offset in parsed if day <= boundary_day), default=min(offset for _, offset in parsed))

    def iterator() -> Iterator[AWEvent]:
        with path.open("rb") as handle:
            handle.seek(seek)
            for raw_line in handle:
                if not raw_line.strip():
                    continue
                try:
                    row = json.loads(raw_line)
                    event_start = datetime.fromisoformat(str(row["start"]).replace("Z", "+00:00"))
                    event_end = datetime.fromisoformat(str(row["end"]).replace("Z", "+00:00"))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if event_start >= end:
                    break
                if not str(row.get("bucket") or "").startswith(bucket_prefix) or event_end <= start:
                    continue
                data = row.get("data")
                yield AWEvent(
                    bucket=str(row.get("bucket") or ""),
                    start=event_start,
                    end=event_end,
                    data=data if isinstance(data, dict) else {},
                )

    return iterator()


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
