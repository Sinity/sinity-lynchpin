"""Canonical browser bookmark materialization reader."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from ..core.config import get_config
from ..core.primitives import logical_date

__all__ = [
    "BookmarkEvent",
    "BookmarkDayActivity",
    "bookmarks_path",
    "bookmarks_manifest_path",
    "iter_bookmarks",
    "daily_bookmark_activity",
]


@dataclass(frozen=True)
class BookmarkEvent:
    bookmark_id: str
    source: str
    browser: str
    profile: str
    url: str
    normalized_url: str
    domain: str
    title: str
    folder: str
    added_at: datetime | None
    source_path: str
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookmarkDayActivity:
    date: date
    bookmark_count: int
    domain_count: int
    top_domain: str


def bookmarks_path(root: Path | None = None) -> Path:
    base = root or get_config().browser_bookmarks_root
    return base / "processed/bookmarks.ndjson"


def bookmarks_manifest_path(root: Path | None = None) -> Path:
    return bookmarks_path(root).with_suffix(".manifest.json")


def iter_bookmarks(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[BookmarkEvent]:
    target = path or bookmarks_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("browser_bookmarks", window=(start, end) if start is not None and end is not None else None)
    if not target.exists():
        raise FileNotFoundError(
            f"canonical bookmark materialization is missing: {target}. "
            "Run python -m lynchpin.ingest.bookmarks_materialize."
        )
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            added_raw = payload.get("added_at")
            added_at = datetime.fromisoformat(added_raw) if isinstance(added_raw, str) and added_raw else None
            if start is not None or end is not None:
                if added_at is None:
                    continue
                day = logical_date(added_at)
                if start is not None and day < start:
                    continue
                if end is not None and day >= end:
                    continue
            yield BookmarkEvent(
                bookmark_id=str(payload.get("bookmark_id") or ""),
                source=str(payload.get("source") or ""),
                browser=str(payload.get("browser") or ""),
                profile=str(payload.get("profile") or ""),
                url=str(payload.get("url") or ""),
                normalized_url=str(payload.get("normalized_url") or ""),
                domain=str(payload.get("domain") or ""),
                title=str(payload.get("title") or ""),
                folder=str(payload.get("folder") or ""),
                added_at=added_at,
                source_path=str(payload.get("source_path") or ""),
                caveats=tuple(str(item) for item in payload.get("caveats") or ()),
            )


def daily_bookmark_activity(*, start: date, end: date, ensure: bool = True) -> list[BookmarkDayActivity]:
    by_day: dict[date, list[BookmarkEvent]] = defaultdict(list)
    for row in iter_bookmarks(start=start, end=end, ensure=ensure):
        if row.added_at is None:
            continue
        day = logical_date(row.added_at)
        by_day[day].append(row)
    out: list[BookmarkDayActivity] = []
    for day, rows in by_day.items():
        counts = Counter(row.domain for row in rows if row.domain)
        top_domain = counts.most_common(1)[0][0] if counts else ""
        out.append(
            BookmarkDayActivity(
                date=day,
                bookmark_count=len(rows),
                domain_count=len(counts),
                top_domain=top_domain,
            )
        )
    return sorted(out, key=lambda row: row.date)
