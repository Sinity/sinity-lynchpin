from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, Optional

from .config import get_config

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


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
    tags: list[str]
    raw: Dict[str, object]


@dataclass
class WykopEntry:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    tags: list[str]
    votes_up: Optional[int]
    votes_down: Optional[int]
    raw: Dict[str, object]


@dataclass
class WykopEntryComment:
    id: int
    created_at: Optional[datetime]
    entry_id: Optional[int]
    url: str
    content: str
    rating: Optional[int]
    raw: Dict[str, object]


def iter_link_comments(username: Optional[str] = None) -> Iterator[WykopLinkComment]:
    path = _profile_file("wykop_links_commented.jsonl", username)
    return _iter_jsonl(path, _map_link_comment)


def iter_entries(username: Optional[str] = None) -> Iterator[WykopEntry]:
    path = _profile_file("wykop_entries_added.jsonl", username)
    return _iter_jsonl(path, _map_entry)


def iter_entry_comments(username: Optional[str] = None) -> Iterator[WykopEntryComment]:
    path = _profile_file("wykop_entry_comments.jsonl", username)
    return _iter_jsonl(path, _map_entry_comment)


def _iter_jsonl(path: Optional[Path], mapper):
    if not path or not path.exists():
        return iter(())

    def generator():
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
                    yield mapped

    return generator()


def _map_link_comment(payload: Dict[str, object]) -> Optional[WykopLinkComment]:
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
        raw=payload,
    )


def _map_entry(payload: Dict[str, object]) -> Optional[WykopEntry]:
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
        raw=payload,
    )


def _map_entry_comment(payload: Dict[str, object]) -> Optional[WykopEntryComment]:
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
        raw=payload,
    )


def _profile_file(name: str, username: Optional[str]) -> Optional[Path]:
    cfg = get_config()
    user = username or cfg.wykop_username
    if not user:
        return None
    profile_dir = cfg.wykop_root / user
    return profile_dir / name


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
