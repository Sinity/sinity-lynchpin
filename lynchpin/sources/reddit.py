from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config

logger = logging.getLogger(__name__)


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


@dataclass
class RedditSavedItem:
    id: str
    permalink: str
    kind: str
    source: str


@dataclass
class RedditVote:
    id: str
    permalink: str
    direction: Optional[int]
    kind: str
    source: str


@dataclass
class RedditMessageHeader:
    id: str
    created: Optional[datetime]
    thread_id: str
    sender: str
    recipient: str
    permalink: str
    source: str


def _resolve_paths(paths: Optional[Sequence[Path]], filename: str) -> List[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).exists()]
    cfg = get_config()
    export_dir = cfg.reddit_export_dir
    if not export_dir:
        return []
    target = export_dir / filename
    return [target] if target.exists() else []


def _comment_paths(paths: Optional[Sequence[Path]] = None) -> List[Path]:
    return _resolve_paths(paths, "comments.csv")


def _comment_signature(paths: Optional[Sequence[Path]]) -> Tuple[Tuple[str, ...], str]:
    resolved = _comment_paths(paths)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache("reddit_comments", depends_on=lambda paths=None: _comment_signature(paths))
def _load_comments(paths: Optional[Sequence[Path]]) -> List[RedditComment]:
    comments: List[RedditComment] = []
    for path in _comment_paths(paths):
        comments.extend(_read_comment_csv(path))
    return comments


def iter_comments(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditComment]:
    yield from _load_comments(paths)


def _post_paths(paths: Optional[Sequence[Path]] = None) -> List[Path]:
    return _resolve_paths(paths, "posts.csv")


def _post_signature(paths: Optional[Sequence[Path]]) -> Tuple[Tuple[str, ...], str]:
    resolved = _post_paths(paths)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache("reddit_posts", depends_on=lambda paths=None: _post_signature(paths))
def _load_posts(paths: Optional[Sequence[Path]]) -> List[RedditPost]:
    posts: List[RedditPost] = []
    for path in _post_paths(paths):
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                created = _parse_datetime(row.get("date"))
                gildings = _safe_int(row.get("gildings"))
                posts.append(
                    RedditPost(
                        id=row.get("id", ""),
                        created=created,
                        subreddit=row.get("subreddit", ""),
                        title=row.get("title", ""),
                        body=row.get("body", ""),
                        url=row.get("url") or row.get("permalink") or "",
                        gildings=gildings,
                        source=str(path),
                    )
                )
    return posts


def iter_posts(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditPost]:
    yield from _load_posts(paths)


def _saved_paths(paths: Optional[Sequence[Path]], filename: str) -> List[Path]:
    return _resolve_paths(paths, filename)


def _saved_signature(paths: Optional[Sequence[Path]], filename: str) -> Tuple[Tuple[str, ...], str]:
    resolved = _saved_paths(paths, filename)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache(
    "reddit_saved_posts",
    depends_on=lambda paths=None: _saved_signature(paths, "saved_posts.csv"),
)
def _load_saved_posts(paths: Optional[Sequence[Path]]) -> List[RedditSavedItem]:
    saved: List[RedditSavedItem] = []
    for path in _saved_paths(paths, "saved_posts.csv"):
        saved.extend(_read_saved_csv(path, "post"))
    return saved


@persistent_cache(
    "reddit_saved_comments",
    depends_on=lambda paths=None: _saved_signature(paths, "saved_comments.csv"),
)
def _load_saved_comments(paths: Optional[Sequence[Path]]) -> List[RedditSavedItem]:
    saved: List[RedditSavedItem] = []
    for path in _saved_paths(paths, "saved_comments.csv"):
        saved.extend(_read_saved_csv(path, "comment"))
    return saved


def iter_saved_posts(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditSavedItem]:
    yield from _load_saved_posts(paths)


def iter_saved_comments(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditSavedItem]:
    yield from _load_saved_comments(paths)


def _vote_paths(paths: Optional[Sequence[Path]], filename: str) -> List[Path]:
    return _resolve_paths(paths, filename)


def _vote_signature(paths: Optional[Sequence[Path]], filename: str) -> Tuple[Tuple[str, ...], str]:
    resolved = _vote_paths(paths, filename)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache(
    "reddit_comment_votes",
    depends_on=lambda paths=None: _vote_signature(paths, "comment_votes.csv"),
)
def _load_comment_votes(paths: Optional[Sequence[Path]]) -> List[RedditVote]:
    votes: List[RedditVote] = []
    for path in _vote_paths(paths, "comment_votes.csv"):
        votes.extend(_read_vote_csv(path, "comment"))
    return votes


@persistent_cache(
    "reddit_post_votes",
    depends_on=lambda paths=None: _vote_signature(paths, "post_votes.csv"),
)
def _load_post_votes(paths: Optional[Sequence[Path]]) -> List[RedditVote]:
    votes: List[RedditVote] = []
    for path in _vote_paths(paths, "post_votes.csv"):
        votes.extend(_read_vote_csv(path, "post"))
    return votes


def iter_comment_votes(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditVote]:
    yield from _load_comment_votes(paths)


def iter_post_votes(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditVote]:
    yield from _load_post_votes(paths)


def _message_paths(paths: Optional[Sequence[Path]] = None) -> List[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).exists()]
    cfg = get_config()
    export_dir = cfg.reddit_export_dir
    if not export_dir:
        return []
    candidates: List[Path] = []
    for filename in ("messages_archive_headers.csv", "message_headers.csv"):
        path = export_dir / filename
        if path.exists():
            candidates.append(path)
    return candidates


def _message_signature(paths: Optional[Sequence[Path]]) -> Tuple[Tuple[str, ...], str]:
    resolved = _message_paths(paths)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache(
    "reddit_message_headers",
    depends_on=lambda paths=None: _message_signature(paths),
)
def _load_message_headers(paths: Optional[Sequence[Path]]) -> List[RedditMessageHeader]:
    messages: List[RedditMessageHeader] = []
    for path in _message_paths(paths):
        messages.extend(_read_message_headers_csv(path))
    return messages


def iter_message_headers(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditMessageHeader]:
    yield from _load_message_headers(paths)


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


def _read_saved_csv(path: Path, kind: str) -> Iterator[RedditSavedItem]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            permalink = row.get("permalink", "")
            yield RedditSavedItem(
                id=row.get("id", ""),
                permalink=permalink,
                kind=kind,
                source=str(path),
            )


def _read_vote_csv(path: Path, kind: str) -> Iterator[RedditVote]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            permalink = row.get("permalink", "")
            yield RedditVote(
                id=row.get("id", ""),
                permalink=permalink,
                direction=_safe_int(row.get("direction")),
                kind=kind,
                source=str(path),
            )


def _read_message_headers_csv(path: Path) -> Iterator[RedditMessageHeader]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            created = _parse_datetime(row.get("date"))
            yield RedditMessageHeader(
                id=row.get("id", ""),
                created=created,
                thread_id=row.get("thread_id", ""),
                sender=row.get("from", ""),
                recipient=row.get("to", ""),
                permalink=row.get("permalink", ""),
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
