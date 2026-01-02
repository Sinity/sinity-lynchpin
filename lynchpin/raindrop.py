from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .config import get_config


@dataclass
class RaindropBookmark:
    id: int
    title: str
    url: str
    folder: str
    tags: List[str]
    created: Optional[datetime]
    note: str
    excerpt: str
    cover: Optional[str]
    favorite: bool
    raw: dict


def iter_bookmarks(csv_path: Optional[Path] = None) -> Iterator[RaindropBookmark]:
    cfg = get_config()
    target = Path(csv_path) if csv_path else cfg.raindrop_csv
    if not target or not target.exists():
        return iter(())

    def generator() -> Iterator[RaindropBookmark]:
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                tags = _parse_tags(row.get("tags"))
                created = _parse_datetime(row.get("created"))
                favorite = str(row.get("favorite") or "").strip().lower() in {"1", "true", "yes"}
                try:
                    bookmark_id = int(row.get("id") or 0)
                except ValueError:
                    continue
                yield RaindropBookmark(
                    id=bookmark_id,
                    title=(row.get("title") or "").strip(),
                    url=(row.get("url") or "").strip(),
                    folder=(row.get("folder") or "").strip(),
                    tags=tags,
                    created=created,
                    note=(row.get("note") or "").strip(),
                    excerpt=(row.get("excerpt") or "").strip(),
                    cover=_strip(row.get("cover")),
                    favorite=favorite,
                    raw=row,
                )

    return generator()


def _parse_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    separators = [",", ";"]
    values = [raw]
    for sep in separators:
        tokens = []
        for value in values:
            tokens.extend(value.split(sep))
        values = tokens
    return [value.strip() for value in values if value.strip()]


def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
