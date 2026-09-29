"""Live Raindrop source — polls the Raindrop REST API for new bookmarks.

Supplements the export-backed ``exports_raindrop.py`` with live API polling.
Bookmarks are deduplicated by URL + creation timestamp against the existing
export data. Requires ``RAINDROP_API_TOKEN`` in the environment (read via
``get_config()``).

Graduated API:
  L0: raw API response iterator (cursor-based pagination)
  L1: typed RaindropBookmarkLive stream (compatible with exports_raindrop schema)
  L2: merged view (live + export, deduplicated)
  Daily: daily_raindrop_live_activity(start, end) — shape-compatible with
         ``RaindropDayActivity`` from exports_raindrop
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterator

from ..core.config import get_config
from ..core.parse import parse_datetime
from .exports_raindrop import (
    RaindropBookmark,
    RaindropDayActivity,
    iter_raindrop_bookmarks,
)
from ..ingest.raindrop_archive import iter_retained_highlights, iter_retained_metadata

__all__ = [
    "RaindropBookmarkLive",
    "RaindropPollCursor",
    "LAST_CURSOR_FILE",
    "raindrop_api_token",
    "iter_live_bookmarks",
    "iter_merged_bookmarks",
    "daily_raindrop_live_activity",
    "poll_raindrop",
    "iter_retained_bookmarks",
]

# ── Constants ──────────────────────────────────────────────────────────────────

RAINDROP_API_BASE = "https://api.raindrop.io/rest/v1"
LAST_CURSOR_FILE = ".lynchpin/raindrop_last_cursor.json"


@dataclass(frozen=True)
class RaindropBookmarkLive:
    """A bookmark from the live Raindrop API, shaped to match exports schema."""
    id: int
    title: str
    url: str
    folder: str
    tags: list[str]
    created: datetime | None
    note: str
    excerpt: str
    cover: str | None
    favorite: bool
    collection: str  # Raindrop collection ID or title

    def to_legacy(self) -> RaindropBookmark:
        return RaindropBookmark(
            id=self.id,
            title=self.title,
            url=self.url,
            folder=self.folder,
            tags=self.tags,
            created=self.created,
            note=self.note,
            excerpt=self.excerpt,
            cover=self.cover,
            favorite=self.favorite,
            raw={"source": "raindrop_live", "collection": self.collection},
        )


@dataclass(frozen=True)
class RaindropPollCursor:
    last_id: int
    last_polled: str  # ISO datetime


@dataclass(frozen=True)
class RetainedRaindrop:
    """Complete bookmark record from a retained API snapshot."""
    id: int
    created: datetime | None
    updated: datetime | None
    state: str
    metadata: dict
    highlights: tuple[dict, ...]


def iter_retained_bookmarks(*, raw_root=None) -> Iterator[RetainedRaindrop]:
    highlights_by_id: dict[int, list[dict]] = defaultdict(list)
    for highlight in iter_retained_highlights(raw_root):
        item = highlight.get("item") or {}
        rid = highlight.get("raindropId") or highlight.get("itemId") or item.get("_id") or highlight.get("_id")
        if rid is not None:
            highlights_by_id[int(rid)].append(highlight)
    for item in iter_retained_metadata(raw_root):
        created_raw = item.get("created")
        updated_raw = item.get("lastUpdate")
        yield RetainedRaindrop(
            id=int(item.get("_id") or 0),
            created=parse_datetime(created_raw) if created_raw else None,
            updated=parse_datetime(updated_raw) if updated_raw else None,
            state=str(item.get("_lynchpin_state") or "active"),
            metadata=item,
            highlights=tuple(highlights_by_id.get(int(item.get("_id") or 0), ())),
        )

# ── Auth ───────────────────────────────────────────────────────────────────────


def raindrop_api_token() -> str | None:
    env_val = os.environ.get("RAINDROP_API_TOKEN", "").strip()
    if env_val:
        return env_val
    # Check config for a file path
    cfg = get_config()
    token_file = cfg.local_root / "raindrop_token"
    if token_file.exists():
        return token_file.read_text().strip()
    return None

# ── API polling ────────────────────────────────────────────────────────────────


def _api_get(path: str, token: str) -> dict:
    """Make a GET request to the Raindrop API."""
    url = f"{RAINDROP_API_BASE}/{path.lstrip('/')}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "lynchpin-raindrop-live/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _parse_raindrop_item(item: dict) -> RaindropBookmarkLive:
    created_raw = item.get("created") or item.get("lastUpdate")
    created = parse_datetime(created_raw) if created_raw else None
    tags_raw = item.get("tags", [])
    tags = tags_raw if isinstance(tags_raw, list) else []
    return RaindropBookmarkLive(
        id=int(item.get("_id") or 0),
        title=str(item.get("title") or ""),
        url=str(item.get("link") or ""),
        folder=str(item.get("folder") or ""),
        tags=[str(t) for t in tags],
        created=created,
        note=str(item.get("note") or item.get("excerpt") or ""),
        excerpt=str(item.get("excerpt") or ""),
        cover=item.get("cover"),
        favorite=bool(item.get("favorite") or item.get("important")),
        collection=str(item.get("collection") or ""),
    )


def iter_live_bookmarks(
    *,
    since: datetime | None = None,
    per_page: int = 50,
    max_pages: int | None = None,
    token: str | None = None,
) -> Iterator[RaindropBookmarkLive]:
    """Yield bookmarks from the Raindrop API, newest first.

    Args:
        since: only yield bookmarks created/updated after this time.
        per_page: items per API page.
        max_pages: safety cap on pagination.
        token: API token; read from env/config if not provided.

    Yields:
        RaindropBookmarkLive for each returned item.
    """
    api_token = token or raindrop_api_token()
    if not api_token:
        return

    page = 0
    while max_pages is None or page < max_pages:
        page_str = f"perpage={per_page}&page={page}"
        try:
            data = _api_get(f"raindrops/0?{page_str}", api_token)
        except Exception:
            raise

        items = data.get("items", [])
        if not items:
            break

        for item in items:
            try:
                bm = _parse_raindrop_item(item)
            except Exception:
                continue
            if since is not None and bm.created is not None and bm.created < since:
                # Items are newest-first; stop iteration
                return
            yield bm

        if len(items) < per_page:
            break
        page += 1
        # Rate-limit courtesy
        time.sleep(0.5)

# ── Merged view ────────────────────────────────────────────────────────────────


def iter_merged_bookmarks(
    *,
    start: date | None = None,
    end: date | None = None,
    token: str | None = None,
) -> Iterator[RaindropBookmark]:
    """Yield export + live bookmarks, deduplicated by URL.

    Export data is authoritative for historical coverage; live data fills
    the gap since the last export. Duplicates are resolved by keeping the
    live version (which may have updated tags/notes).
    """
    seen_urls: set[str] = set()
    since = (
        datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        if start is not None
        else None
    )

    # Live first (takes precedence on collision)
    for live in iter_live_bookmarks(since=since, token=token):
        if live.created is not None:
            d = live.created.date()
            if start is not None and d < start:
                continue
            if end is not None and d >= end:
                continue
        seen_urls.add(live.url)
        yield live.to_legacy()

    # Export data for historical coverage
    try:
        for bm in iter_raindrop_bookmarks(start=start, end=end):
            if bm.url not in seen_urls:
                yield bm
    except FileNotFoundError:
        pass

# ── Daily rollup ───────────────────────────────────────────────────────────────


def daily_raindrop_live_activity(
    *,
    start: date,
    end: date,
    token: str | None = None,
) -> list[RaindropDayActivity]:
    """Daily bookmark rollup, shape-compatible with ``exports_raindrop.daily_raindrop_activity``.

    Merges live + export data.
    """
    by_date: dict[date, tuple[int, set[str]]] = defaultdict(lambda: (0, set()))
    for bm in iter_merged_bookmarks(start=start, end=end, token=token):
        if bm.created is None:
            continue
        d = bm.created.date()
        if d < start or d >= end:
            continue
        count, tags = by_date[d]
        tags.update(bm.tags)
        by_date[d] = (count + 1, tags)
    return sorted(
        [
            RaindropDayActivity(date=d, bookmarks_added=count, unique_tags=len(tags))
            for d, (count, tags) in by_date.items()
        ],
        key=lambda x: x.date,
    )

# ── Poll convenience ───────────────────────────────────────────────────────────


def poll_raindrop(token: str | None = None) -> tuple[int, datetime]:
    """Poll Raindrop API and return (new_count, last_polled_at).

    Updates the cursor file at ``LAST_CURSOR_FILE``.
    """
    api_token = token or raindrop_api_token()
    if not api_token:
        return 0, datetime.now(timezone.utc)

    cursor_path = get_config().repo_root / LAST_CURSOR_FILE
    since: datetime | None = None
    if cursor_path.exists():
        try:
            cursor_data = json.loads(cursor_path.read_text())
            since = datetime.fromisoformat(cursor_data.get("last_polled", ""))
        except Exception:
            pass

    count = 0
    newest: datetime | None = None
    now = datetime.now(timezone.utc)
    # Consume every page successfully before advancing progress. A failed page
    # leaves the prior cursor intact so the next run retries the same window.
    for bm in iter_live_bookmarks(since=since, token=api_token):
        count += 1
        if newest is None or (bm.created is not None and bm.created > newest):
            newest = bm.created

    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cursor_path.with_suffix(cursor_path.suffix + ".tmp")
    temporary.write_text(json.dumps({"last_polled": now.isoformat()}), encoding="utf-8")
    temporary.replace(cursor_path)
    return count, now
