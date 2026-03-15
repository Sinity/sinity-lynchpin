from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
TextTokenizer = Callable[[str], Iterable[str]]


@dataclass
class WykopLinkComment:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    rating: Optional[int]
    link_id: Optional[int]
    link_title: str
    link_url: str
    tags: List[str]


@dataclass
class WykopEntry:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    tags: List[str]
    votes_up: Optional[int]
    votes_down: Optional[int]


@dataclass
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


def summarize_activity(
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

    for comment in iter_link_comments(username=username, path=link_comments_path):
        if comment.created_at is None:
            continue
        month = _month_key(comment.created_at)
        if not _month_in_range(month, start_month, end_month):
            continue
        link_comment_counts[month] += 1
        for tag in comment.tags:
            link_comment_tags[month][tag] += 1
        if tokenize_text and comment.content:
            for token in tokenize_text(comment.content):
                link_comment_tokens[month][token] += 1

    for entry in iter_entries(username=username, path=entries_path):
        if entry.created_at is None:
            continue
        month = _month_key(entry.created_at)
        if not _month_in_range(month, start_month, end_month):
            continue
        entry_counts[month] += 1
        for tag in entry.tags:
            entry_tags[month][tag] += 1
        if tokenize_text and entry.content:
            for token in tokenize_text(entry.content):
                entry_tokens[month][token] += 1

    for comment in iter_entry_comments(username=username, path=entry_comments_path):
        if comment.created_at is None:
            continue
        month = _month_key(comment.created_at)
        if not _month_in_range(month, start_month, end_month):
            continue
        entry_comment_counts[month] += 1
        if tokenize_text and comment.content:
            for token in tokenize_text(comment.content):
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


def iter_link_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopLinkComment]:
    path = path or _profile_file("wykop_links_commented.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_link_comments(path))


def iter_entries(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopEntry]:
    path = path or _profile_file("wykop_entries_added.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_entries(path))


def iter_entry_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopEntryComment]:
    path = path or _profile_file("wykop_entry_comments.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_entry_comments(path))


@persistent_cache("wykop_link_comments", depends_on=lambda path: file_signature(path))
def _load_link_comments(path: Path) -> List[WykopLinkComment]:
    return _read_jsonl(path, _parse_link_comment)


@persistent_cache("wykop_entries", depends_on=lambda path: file_signature(path))
def _load_entries(path: Path) -> List[WykopEntry]:
    return _read_jsonl(path, _parse_entry)


@persistent_cache("wykop_entry_comments", depends_on=lambda path: file_signature(path))
def _load_entry_comments(path: Path) -> List[WykopEntryComment]:
    return _read_jsonl(path, _parse_entry_comment)


def _read_jsonl(path: Path, mapper) -> List:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            mapped = mapper(payload)
            if mapped is not None:
                rows.append(mapped)
    return rows


def _parse_link_comment(payload: Dict[str, object]) -> Optional[WykopLinkComment]:
    comment_id = _safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopLinkComment(
        id=comment_id,
        created_at=_parse_datetime(payload.get("comment_created_at")),
        url=_as_str(payload.get("comment_url")),
        content=_as_str(payload.get("comment_content")),
        rating=_safe_int(payload.get("comment_rating")),
        link_id=_safe_int(payload.get("link_id")),
        link_title=_as_str(payload.get("link_title")),
        link_url=_as_str(payload.get("link_url")),
        tags=_as_list(payload.get("link_tags")),
    )


def _parse_entry(payload: Dict[str, object]) -> Optional[WykopEntry]:
    entry_id = _safe_int(payload.get("entry_id"))
    if entry_id is None:
        return None
    return WykopEntry(
        id=entry_id,
        created_at=_parse_datetime(payload.get("entry_created_at")),
        url=_as_str(payload.get("entry_url")),
        content=_as_str(payload.get("entry_content")),
        tags=_as_list(payload.get("entry_tags")),
        votes_up=_safe_int(payload.get("votes_up")),
        votes_down=_safe_int(payload.get("votes_down")),
    )


def _parse_entry_comment(payload: Dict[str, object]) -> Optional[WykopEntryComment]:
    comment_id = _safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopEntryComment(
        id=comment_id,
        created_at=_parse_datetime(payload.get("comment_created_at")),
        entry_id=_safe_int(payload.get("entry_id")),
        url=_as_str(payload.get("entry_url")),
        content=_as_str(payload.get("comment_content")),
        rating=_safe_int(payload.get("comment_rating")),
    )


def _profile_file(name: str, username: Optional[str]) -> Optional[Path]:
    cfg = get_config()
    user = username or cfg.wykop_username
    if not user:
        return None
    profile_dir = cfg.wykop_root / user
    return profile_dir / name


def _month_key(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def _month_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month


def _parse_datetime(raw: object) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), DATE_FORMAT)
    except ValueError:
        return None


def _safe_int(value: object) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


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
