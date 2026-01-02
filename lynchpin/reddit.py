from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import get_config


@dataclass
class RedditComment:
    id: str
    created: Optional[datetime]
    subreddit: str
    body: str
    permalink: str
    parent: str
    gildings: Optional[int]
    source: str


@dataclass
class RedditPost:
    id: str
    created: Optional[datetime]
    subreddit: str
    title: str
    body: str
    url: str
    gildings: Optional[int]
    source: str


def iter_comments() -> Iterator[RedditComment]:
    cfg = get_config()
    paths = []
    if cfg.reddit_comments_csv.exists():
        paths.append(cfg.reddit_comments_csv)
    export_dir = cfg.reddit_export_dir
    if export_dir:
        export_comments = export_dir / "comments.csv"
        if export_comments.exists():
            paths.append(export_comments)
    for path in paths:
        yield from _read_comment_csv(path)


def iter_posts() -> Iterator[RedditPost]:
    cfg = get_config()
    export_dir = cfg.reddit_export_dir
    if not export_dir:
        return iter(())

    def generator() -> Iterator[RedditPost]:
        path = export_dir / "posts.csv"
        if not path.exists():
            return
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                created = _parse_datetime(row.get("date"))
                gildings = _safe_int(row.get("gildings"))
                yield RedditPost(
                    id=row.get("id", ""),
                    created=created,
                    subreddit=row.get("subreddit", ""),
                    title=row.get("title", ""),
                    body=row.get("body", ""),
                    url=row.get("url") or row.get("permalink") or "",
                    gildings=gildings,
                    source=str(path),
                )

    return generator()


def _read_comment_csv(path: Path) -> Iterator[RedditComment]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            created = _parse_datetime(row.get("date"))
            yield RedditComment(
                id=row.get("id", ""),
                created=created,
                subreddit=row.get("subreddit", ""),
                body=row.get("body", ""),
                permalink=row.get("permalink", ""),
                parent=row.get("parent", ""),
                gildings=_safe_int(row.get("gildings")),
                source=str(path),
            )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    replacements = value.replace(" UTC", "+00:00")
    try:
        return datetime.fromisoformat(replacements)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _safe_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None
