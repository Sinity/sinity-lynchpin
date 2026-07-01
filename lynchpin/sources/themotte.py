"""TheMotte raw export reader.

The sync command writes authenticated private messages and notification rows
under ``/realm/data/exports/themotte/raw/<username>/``. This source keeps the
browser scrape out of ordinary read paths; readers only parse local JSONL.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from ..core.config import get_config
from ..core.errors import SourceUnavailableError
from ..core.primitives import logical_date

MESSAGE_FILENAME = "themotte_messages.jsonl"
NOTIFICATION_FILENAME = "themotte_notifications.jsonl"
SYNC_MANIFEST_FILENAME = "sync_manifest.json"
T = TypeVar("T")


@dataclass(frozen=True)
class TheMotteMessage:
    id: str
    created_at: datetime
    author: str
    recipient: str
    peer: str
    body: str
    url: str
    relative_time: str


@dataclass(frozen=True)
class TheMotteNotification:
    id: str
    created_at: datetime | None
    kind: str
    actor: str
    title: str
    text: str
    url: str
    relative_time: str
    unread: bool


@dataclass(frozen=True)
class TheMotteDayActivity:
    date: date
    messages: int
    outbound_messages: int
    notifications: int
    peers: tuple[str, ...]


def profile_root(root: Path | None = None, username: str | None = None) -> Path:
    cfg = get_config()
    return (root or cfg.themotte_root) / (username or cfg.themotte_username)


def message_path(root: Path | None = None, username: str | None = None) -> Path:
    return profile_root(root=root, username=username) / MESSAGE_FILENAME


def notification_path(root: Path | None = None, username: str | None = None) -> Path:
    return profile_root(root=root, username=username) / NOTIFICATION_FILENAME


def sync_manifest_path(root: Path | None = None, username: str | None = None) -> Path:
    return profile_root(root=root, username=username) / SYNC_MANIFEST_FILENAME


def input_files(root: Path | None = None, username: str | None = None) -> tuple[Path, ...]:
    candidates = (
        message_path(root=root, username=username),
        notification_path(root=root, username=username),
        sync_manifest_path(root=root, username=username),
    )
    return tuple(path for path in candidates if path.exists())


def iter_messages(
    root: Path | None = None,
    *,
    username: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[TheMotteMessage]:
    path = message_path(root=root, username=username)
    for row in _load_jsonl(path, _parse_message):
        day = logical_date(row.created_at)
        if start is not None and day < start:
            continue
        if end is not None and day >= end:
            continue
        yield row


def iter_notifications(
    root: Path | None = None,
    *,
    username: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[TheMotteNotification]:
    path = notification_path(root=root, username=username)
    for row in _load_jsonl(path, _parse_notification):
        if row.created_at is not None:
            day = logical_date(row.created_at)
            if start is not None and day < start:
                continue
            if end is not None and day >= end:
                continue
        elif start is not None or end is not None:
            continue
        yield row


def daily_activity(
    *,
    root: Path | None = None,
    username: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[TheMotteDayActivity]:
    buckets: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"messages": 0, "outbound": 0, "notifications": 0, "peers": set()}
    )
    operator = username or get_config().themotte_username
    for msg in iter_messages(root=root, username=username, start=start, end=end):
        day = logical_date(msg.created_at)
        bucket = buckets[day]
        bucket["messages"] += 1
        if msg.author == operator:
            bucket["outbound"] += 1
        if msg.peer:
            bucket["peers"].add(msg.peer)
    for notif in iter_notifications(root=root, username=username, start=start, end=end):
        if notif.created_at is None:
            continue
        buckets[logical_date(notif.created_at)]["notifications"] += 1
    return [
        TheMotteDayActivity(
            date=day,
            messages=int(values["messages"]),
            outbound_messages=int(values["outbound"]),
            notifications=int(values["notifications"]),
            peers=tuple(sorted(values["peers"])),
        )
        for day, values in sorted(buckets.items())
    ]


def date_range(root: Path | None = None, username: str | None = None) -> tuple[datetime, datetime]:
    base = profile_root(root=root, username=username)
    return _date_range_cached(str(base), _signature(input_files(root=root, username=username)))


@lru_cache(maxsize=64)
def _date_range_cached(root: str, signature: tuple[tuple[str, int, int], ...]) -> tuple[datetime, datetime]:
    base = Path(root)
    oldest: datetime | None = None
    newest: datetime | None = None
    for msg in iter_messages(root=base.parent, username=base.name):
        if oldest is None or msg.created_at < oldest:
            oldest = msg.created_at
        if newest is None or msg.created_at > newest:
            newest = msg.created_at
    for notif in iter_notifications(root=base.parent, username=base.name):
        if notif.created_at is None:
            continue
        if oldest is None or notif.created_at < oldest:
            oldest = notif.created_at
        if newest is None or notif.created_at > newest:
            newest = notif.created_at
    if oldest is None or newest is None:
        raise SourceUnavailableError("themotte", reason="No dated TheMotte rows found")
    return oldest, newest


def _load_jsonl(path: Path, parser: Callable[[dict[str, Any]], T | None]) -> Iterator[T]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            parsed = parser(json.loads(line))
            if parsed is not None:
                yield parsed


def _parse_message(row: dict[str, Any]) -> TheMotteMessage | None:
    created_at = _parse_datetime(row.get("created_at"))
    if created_at is None:
        return None
    return TheMotteMessage(
        id=str(row.get("id") or ""),
        created_at=created_at,
        author=str(row.get("author") or ""),
        recipient=str(row.get("recipient") or ""),
        peer=str(row.get("peer") or ""),
        body=str(row.get("body") or ""),
        url=str(row.get("url") or ""),
        relative_time=str(row.get("relative_time") or ""),
    )


def _parse_notification(row: dict[str, Any]) -> TheMotteNotification | None:
    notification_id = str(row.get("id") or "")
    if not notification_id:
        return None
    return TheMotteNotification(
        id=notification_id,
        created_at=_parse_datetime(row.get("created_at")),
        kind=str(row.get("kind") or "notification"),
        actor=str(row.get("actor") or ""),
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        url=str(row.get("url") or ""),
        relative_time=str(row.get("relative_time") or ""),
        unread=bool(row.get("unread")),
    )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _signature(paths: tuple[Path, ...]) -> tuple[tuple[str, int, int], ...]:
    signature = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


__all__ = [
    "MESSAGE_FILENAME",
    "NOTIFICATION_FILENAME",
    "SYNC_MANIFEST_FILENAME",
    "TheMotteMessage",
    "TheMotteNotification",
    "TheMotteDayActivity",
    "profile_root",
    "message_path",
    "notification_path",
    "sync_manifest_path",
    "input_files",
    "iter_messages",
    "iter_notifications",
    "daily_activity",
    "date_range",
]
