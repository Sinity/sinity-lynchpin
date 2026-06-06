"""Wykop social platform source — operator's comments, votes, and entries.

Wykop is a Polish link-aggregation / social-news platform (similar to
Reddit/Digg). The operator's export lives at /realm/data/exports/wykop/raw/Sinity/.

Key signals:
  - links_commented: 11,534 operator comments on shared links (18 MB)
  - entry_comments:   1,118 operator comments on user entries (960 KB)
  - actions:            432 votes/saves (4.4 MB)
  - entries_plusowane:  761 entries the operator upvoted
  - links_wykopane:   1,148 links the operator "dug up" (upvoted)

The large ~1 GB files (observed_all, observed_tags_stream) are observer-feed
and are intentionally skipped — they contain mostly other people's content.

Comment content preserves @mentions and > quoted text blocks. The distinction
between own-text and quoted-text matters: quoted text is what the operator
was responding to, not what the operator wrote.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from ..core.errors import SourceUnavailableError

WYKOP_ROOT = Path("/realm/data/exports/wykop/raw/Sinity")


@dataclass(frozen=True)
class WykopComment:
    """One operator comment (links_commented or entry_comments)."""

    kind: str  # "link_comment" | "entry_comment"
    comment_id: int
    created_at: datetime
    content: str  # raw markdown-ish text with @mentions and > quotes
    rating: int  # vote score
    url: str

    # Parent link/entry context
    parent_id: int  # link_id or entry_id
    parent_title: str  # link_title or entry preview
    parent_url: str
    parent_tags: tuple[str, ...]
    parent_created_at: Optional[datetime] = None

    @property
    def own_text(self) -> str:
        """Comment content with quoted text (> lines) removed."""
        return _strip_quotes(self.content)

    @property
    def own_length(self) -> int:
        return len(self.own_text)

    @property
    def quoted_length(self) -> int:
        return len(self.content) - len(self.own_text)

    @property
    def mentions(self) -> tuple[str, ...]:
        """@usernames mentioned in the comment (own text only)."""
        return tuple(set(re.findall(r"@([\w-]+)", self.own_text)))


@dataclass(frozen=True)
class WykopAction:
    """One operator action (vote, save, etc.)."""

    kind: str  # "upvote" | "downvote" | "save" | ...
    created_at: datetime
    target_id: int
    target_title: str
    target_url: str


@dataclass(frozen=True)
class WykopDayActivity:
    """Per-day Wykop activity summary."""

    date: str  # YYYY-MM-DD
    comments: int
    own_chars: int  # characters in own text (excl quotes)
    total_chars: int  # including quoted text
    upvotes: int
    downvotes: int
    comment_ids: tuple[int, ...]


def _strip_quotes(text: str) -> str:
    """Remove > quoted lines from comment text."""
    lines = text.split("\n")
    own = [line for line in lines if not line.lstrip().startswith(">")]
    return "\n".join(own).strip()


def _parse_wykop_datetime(s: str) -> datetime:
    """Parse Wykop datetime format: '2024-05-19 00:58:21' (Europe/Warsaw)."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def iter_comments(root: Optional[Path] = None) -> Iterator[WykopComment]:
    """Iterate all operator comments (links + entries).

    Yields in file order (roughly reverse-chronological within each file).
    """
    base = root or WYKOP_ROOT

    # links_commented — the main source
    links_path = base / "wykop_links_commented.jsonl"
    if links_path.exists():
        with open(links_path) as f:
            for line in f:
                row = json.loads(line)
                tags_raw = row.get("link_tags")
                if isinstance(tags_raw, str):
                    tags_raw = [t.strip() for t in tags_raw.split(",")]
                tags_raw = tags_raw or []

                parent_created = row.get("link_created_at")
                if parent_created:
                    parent_created = _parse_wykop_datetime(parent_created)

                yield WykopComment(
                    kind="link_comment",
                    comment_id=int(row["comment_id"]),
                    created_at=_parse_wykop_datetime(row["comment_created_at"]),
                    content=row.get("comment_content", ""),
                    rating=int(row.get("comment_rating", 0)),
                    url=row.get("comment_url", ""),
                    parent_id=int(row.get("link_id", 0)),
                    parent_title=row.get("link_title", ""),
                    parent_url=row.get("link_url", ""),
                    parent_tags=tuple(tags_raw),
                    parent_created_at=parent_created,
                )

    # entry_comments — smaller, different parent type
    entries_path = base / "wykop_entry_comments.jsonl"
    if entries_path.exists():
        with open(entries_path) as f:
            for line in f:
                row = json.loads(line)
                yield WykopComment(
                    kind="entry_comment",
                    comment_id=int(row.get("comment_id", 0)),
                    created_at=_parse_wykop_datetime(row["comment_created_at"]),
                    content=row.get("comment_content", ""),
                    rating=int(row.get("comment_rating", 0)),
                    url=row.get("comment_url", ""),
                    parent_id=int(row.get("entry_id", 0)),
                    parent_title=row.get("entry_preview", row.get("entry_content", ""))[:200],
                    parent_url=f'https://wykop.pl/wpis/{row.get("entry_id", "")}',
                    parent_tags=(),
                )


def iter_actions(root: Optional[Path] = None) -> Iterator[WykopAction]:
    """Iterate operator actions (votes/saves)."""
    base = root or WYKOP_ROOT
    actions_path = base / "wykop_actions.jsonl"
    if not actions_path.exists():
        return

    with open(actions_path) as f:
        for line in f:
            row = json.loads(line)
            # Determine kind from the data
            action_type = row.get("type", row.get("action_type", "unknown"))
            created = row.get("created_at") or row.get("action_created_at")
            if not created:
                continue
            yield WykopAction(
                kind=action_type,
                created_at=_parse_wykop_datetime(created),
                target_id=int(row.get("link_id", row.get("entry_id", 0))),
                target_title=row.get("link_title", row.get("entry_preview", ""))[:200],
                target_url=row.get("link_url", row.get("entry_url", "")),
            )


def daily_activity(
    start: Optional[str] = None,
    end: Optional[str] = None,
    root: Optional[Path] = None,
) -> list[WykopDayActivity]:
    """Per-day Wykop activity with comment counts and character volumes.

    Args:
        start, end: ISO date strings (YYYY-MM-DD) for filtering.
        root: override data root.
    """
    buckets: dict = defaultdict(  # type: ignore[type-arg]
        lambda: {
            "comments": 0,
            "own_chars": 0,
            "total_chars": 0,
            "upvotes": 0,
            "downvotes": 0,
            "comment_ids": [],
        }
    )

    for c in iter_comments(root=root):
        day = c.created_at.strftime("%Y-%m-%d")
        if start and day < start:
            continue
        if end and day > end:
            continue
        b = buckets[day]
        b["comments"] += 1
        b["own_chars"] += c.own_length
        b["total_chars"] += len(c.content)
        b["comment_ids"].append(c.comment_id)

    for a in iter_actions(root=root):
        day = a.created_at.strftime("%Y-%m-%d")
        if start and day < start:
            continue
        if end and day > end:
            continue
        b = buckets[day]
        if a.kind in ("upvote", "plus"):
            b["upvotes"] += 1
        elif a.kind in ("downvote", "minus"):
            b["downvotes"] += 1

    result = []
    for day in sorted(buckets):
        b = buckets[day]
        result.append(
            WykopDayActivity(
                date=day,
                comments=b["comments"],
                own_chars=b["own_chars"],
                total_chars=b["total_chars"],
                upvotes=b["upvotes"],
                downvotes=b["downvotes"],
                comment_ids=tuple(b["comment_ids"]),
            )
        )

    return result


def topic_distribution(
    top_n: int = 30,
    root: Optional[Path] = None,
) -> list[tuple[str, int]]:
    """Top link tags the operator comments on."""
    counts: dict[str, int] = defaultdict(int)
    for c in iter_comments(root=root):
        if c.kind == "link_comment":
            for tag in c.parent_tags:
                counts[tag] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:top_n]


def _comment_files(root: Optional[Path] = None) -> tuple[Path, ...]:
    base = root or WYKOP_ROOT
    return (
        base / "wykop_links_commented.jsonl",
        base / "wykop_entry_comments.jsonl",
    )


def date_range(root: Optional[Path] = None) -> tuple[datetime, datetime]:
    """Oldest and newest comment dates."""
    base = root or WYKOP_ROOT
    return _date_range_cached(str(base), _comment_files_signature(base))


@lru_cache(maxsize=64)
def _date_range_cached(
    root: str,
    signature: tuple[tuple[str, int, int], ...],
) -> tuple[datetime, datetime]:
    oldest = None
    newest = None
    for path in _comment_files(Path(root)):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                created_raw = row.get("comment_created_at")
                if not created_raw:
                    continue
                created = _parse_wykop_datetime(created_raw)
                if oldest is None or created < oldest:
                    oldest = created
                if newest is None or created > newest:
                    newest = created
    if oldest is None or newest is None:
        raise SourceUnavailableError("wykop", reason="No comments found")
    return oldest, newest


def _comment_files_signature(root: Path) -> tuple[tuple[str, int, int], ...]:
    signature = []
    for path in _comment_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


__all__ = [
    "WykopComment",
    "WykopAction",
    "WykopDayActivity",
    "iter_comments",
    "iter_actions",
    "daily_activity",
    "topic_distribution",
    "date_range",
]
