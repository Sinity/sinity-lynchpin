"""Wykop export reader and activity summary."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, TypeVar

from ..core.cache import file_signature, persistent_cache
from ..core.config import get_config
from ..core.parse import in_month_range, month_key, parse_datetime, safe_int
from ..core.primitives import logical_date
from ..core.source import read_jsonl_with

TextTokenizer = Callable[[str], Iterable[str]]
T = TypeVar("T")

__all__ = [
    "WykopLinkComment",
    "WykopEntry",
    "WykopEntryComment",
    "WykopActivitySummary",
    "summarize_wykop_activity",
    "iter_wykop_link_comments",
    "iter_wykop_entries",
    "iter_wykop_entry_comments",
]


@dataclass(frozen=True)
class WykopLinkComment:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    rating: Optional[int]
    link_id: Optional[int]
    link_title: str
    link_url: str
    tags: list[str]


@dataclass(frozen=True)
class WykopEntry:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    tags: list[str]
    votes_up: Optional[int]
    votes_down: Optional[int]


@dataclass(frozen=True)
class WykopEntryComment:
    id: int
    created_at: Optional[datetime]
    entry_id: Optional[int]
    url: str
    content: str
    rating: Optional[int]


@dataclass(frozen=True)
class WykopActivitySummary:
    link_comment_counts: dict[str, int]
    link_comment_tags: dict[str, Counter[str]]
    link_comment_tokens: dict[str, Counter[str]]
    entry_counts: dict[str, int]
    entry_tags: dict[str, Counter[str]]
    entry_tokens: dict[str, Counter[str]]
    entry_comment_counts: dict[str, int]
    entry_comment_tokens: dict[str, Counter[str]]


def summarize_wykop_activity(
    start_month: str,
    end_month: str,
    *,
    username: Optional[str] = None,
    link_comments_path: Optional[Path] = None,
    entries_path: Optional[Path] = None,
    entry_comments_path: Optional[Path] = None,
    tokenize_text: TextTokenizer | None = None,
) -> WykopActivitySummary:
    link_comment_counts: dict[str, int] = defaultdict(int)
    link_comment_tags: dict[str, Counter[str]] = defaultdict(Counter)
    link_comment_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    entry_counts: dict[str, int] = defaultdict(int)
    entry_tags: dict[str, Counter[str]] = defaultdict(Counter)
    entry_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    entry_comment_counts: dict[str, int] = defaultdict(int)
    entry_comment_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    start, end = _month_window(start_month, end_month)

    for comment in iter_wykop_link_comments(username=username, path=link_comments_path, start=start, end=end):
        if comment.created_at is None:
            continue
        month = month_key(comment.created_at)
        if not in_month_range(month, start_month, end_month):
            continue
        link_comment_counts[month] += 1
        for tag in comment.tags:
            link_comment_tags[month][tag] += 1
        if tokenize_text and comment.content:
            for token in tokenize_text(comment.content):
                link_comment_tokens[month][token] += 1

    for entry in iter_wykop_entries(username=username, path=entries_path, start=start, end=end):
        if entry.created_at is None:
            continue
        month = month_key(entry.created_at)
        if not in_month_range(month, start_month, end_month):
            continue
        entry_counts[month] += 1
        for tag in entry.tags:
            entry_tags[month][tag] += 1
        if tokenize_text and entry.content:
            for token in tokenize_text(entry.content):
                entry_tokens[month][token] += 1

    for entry_comment in iter_wykop_entry_comments(username=username, path=entry_comments_path, start=start, end=end):
        if entry_comment.created_at is None:
            continue
        month = month_key(entry_comment.created_at)
        if not in_month_range(month, start_month, end_month):
            continue
        entry_comment_counts[month] += 1
        if tokenize_text and entry_comment.content:
            for token in tokenize_text(entry_comment.content):
                entry_comment_tokens[month][token] += 1

    return WykopActivitySummary(
        link_comment_counts=dict(link_comment_counts),
        link_comment_tags=dict(link_comment_tags),
        link_comment_tokens=dict(link_comment_tokens),
        entry_counts=dict(entry_counts),
        entry_tags=dict(entry_tags),
        entry_tokens=dict(entry_tokens),
        entry_comment_counts=dict(entry_comment_counts),
        entry_comment_tokens=dict(entry_comment_tokens),
    )


def _month_window(start_month: str, end_month: str) -> tuple[date, date]:
    start_year, start_month_num = (int(part) for part in start_month.split("-", 1))
    end_year, end_month_num = (int(part) for part in end_month.split("-", 1))
    start = date(start_year, start_month_num, 1)
    end = (
        date(end_year + 1, 1, 1)
        if end_month_num == 12
        else date(end_year, end_month_num + 1, 1)
    )
    if end <= start:
        raise ValueError("end_month must be after or equal to start_month")
    return start, end


def iter_wykop_link_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[WykopLinkComment]:
    path = path or _profile_file("wykop_links_commented.jsonl", username)
    if not path:
        return iter(())
    return _bounded_rows(_load_link_comments(path), start=start, end=end)


def iter_wykop_entries(
    username: Optional[str] = None,
    path: Optional[Path] = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[WykopEntry]:
    path = path or _profile_file("wykop_entries_added.jsonl", username)
    if not path:
        return iter(())
    return _bounded_rows(_load_entries(path), start=start, end=end)


def iter_wykop_entry_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Iterator[WykopEntryComment]:
    path = path or _profile_file("wykop_entry_comments.jsonl", username)
    if not path:
        return iter(())
    return _bounded_rows(_load_entry_comments(path), start=start, end=end)


def _bounded_rows(
    rows: Iterable[T],
    *,
    start: date | None,
    end: date | None,
) -> Iterator[T]:
    for row in rows:
        created_at = getattr(row, "created_at", None)
        if created_at is not None and (start is not None or end is not None):
            d = logical_date(created_at)
            if start is not None and d < start:
                continue
            if end is not None and d >= end:
                continue
        yield row


def _file_signature(path: Path) -> object:
    return file_signature(path)


@persistent_cache("wykop_link_comments", depends_on=_file_signature)
def _load_link_comments(path: Path) -> list[WykopLinkComment]:
    return _read_jsonl(path, _parse_link_comment)


@persistent_cache("wykop_entries", depends_on=_file_signature)
def _load_entries(path: Path) -> list[WykopEntry]:
    return _read_jsonl(path, _parse_entry)


@persistent_cache("wykop_entry_comments", depends_on=_file_signature)
def _load_entry_comments(path: Path) -> list[WykopEntryComment]:
    return _read_jsonl(path, _parse_entry_comment)


def _read_jsonl(path: Path, mapper: Callable[[dict[str, object]], T | None]) -> list[T]:
    return list(read_jsonl_with(path, mapper, source_name=path.name))


def _parse_link_comment(payload: dict[str, object]) -> Optional[WykopLinkComment]:
    comment_id = safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopLinkComment(
        id=comment_id,
        created_at=parse_datetime(payload.get("comment_created_at")),
        url=_as_str(payload.get("comment_url")),
        content=_as_str(payload.get("comment_content")),
        rating=safe_int(payload.get("comment_rating")),
        link_id=safe_int(payload.get("link_id")),
        link_title=_as_str(payload.get("link_title")),
        link_url=_as_str(payload.get("link_url")),
        tags=_as_list(payload.get("link_tags")),
    )


def _parse_entry(payload: dict[str, object]) -> Optional[WykopEntry]:
    entry_id = safe_int(payload.get("entry_id"))
    if entry_id is None:
        return None
    return WykopEntry(
        id=entry_id,
        created_at=parse_datetime(payload.get("entry_created_at")),
        url=_as_str(payload.get("entry_url")),
        content=_as_str(payload.get("entry_content")),
        tags=_as_list(payload.get("entry_tags")),
        votes_up=safe_int(payload.get("votes_up")),
        votes_down=safe_int(payload.get("votes_down")),
    )


def _parse_entry_comment(payload: dict[str, object]) -> Optional[WykopEntryComment]:
    comment_id = safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopEntryComment(
        id=comment_id,
        created_at=parse_datetime(payload.get("comment_created_at")),
        entry_id=safe_int(payload.get("entry_id")),
        url=_as_str(payload.get("entry_url")),
        content=_as_str(payload.get("comment_content")),
        rating=safe_int(payload.get("comment_rating")),
    )


def _profile_file(name: str, username: Optional[str]) -> Optional[Path]:
    cfg = get_config()
    user = username or cfg.wykop_username
    if not user:
        return None
    profile_dir = cfg.wykop_root / user
    return profile_dir / name


def _as_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
