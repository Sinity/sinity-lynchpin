from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..core.cache import files_signature, persistent_cache
from ..core.config import get_config
from ..core.parse import parse_datetime, month_key as _month_key, in_month_range as _month_in_range, safe_int as _safe_int

logger = logging.getLogger(__name__)
TextTokenizer = Callable[[str], Iterable[str]]

__all__ = [
    "RedditComment",
    "RedditPost",
    "RedditSavedItem",
    "RedditVote",
    "RedditMessageHeader",
    "RedditActivitySummary",
    "RedditDayActivity",
    "summarize_activity",
    "iter_comments",
    "iter_posts",
    "iter_saved_posts",
    "iter_saved_comments",
    "iter_comment_votes",
    "iter_post_votes",
    "iter_message_headers",
    "daily_activity",
    "subreddit_distribution",
]

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


@dataclass(frozen=True)
class RedditActivitySummary:
    comment_counts: dict[str, int]
    comment_subreddits: dict[str, Counter[str]]
    comment_tokens: dict[str, Counter[str]]
    post_counts: dict[str, int]
    message_counts: dict[str, int]


def summarize_activity(
    start_month: str,
    end_month: str,
    *,
    comments_paths: Optional[Sequence[Path]] = None,
    posts_paths: Optional[Sequence[Path]] = None,
    message_paths: Optional[Sequence[Path]] = None,
    tokenize_text: TextTokenizer | None = None,
) -> RedditActivitySummary:
    comment_counts: dict[str, int] = defaultdict(int)
    comment_subreddits: dict[str, Counter[str]] = defaultdict(Counter)
    comment_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    post_counts: dict[str, int] = defaultdict(int)
    message_counts: dict[str, int] = defaultdict(int)

    for comment in iter_comments(paths=comments_paths):
        if comment.created is None:
            continue
        month = _month_key(comment.created)
        if not _month_in_range(month, start_month, end_month):
            continue
        comment_counts[month] += 1
        comment_subreddits[month][comment.subreddit.strip() or "<unknown>"] += 1
        if tokenize_text and comment.body:
            for token in tokenize_text(comment.body):
                comment_tokens[month][token] += 1

    for post in iter_posts(paths=posts_paths):
        if post.created is None:
            continue
        month = _month_key(post.created)
        if not _month_in_range(month, start_month, end_month):
            continue
        post_counts[month] += 1

    for message in iter_message_headers(paths=message_paths):
        if message.created is None:
            continue
        month = _month_key(message.created)
        if not _month_in_range(month, start_month, end_month):
            continue
        message_counts[month] += 1

    return RedditActivitySummary(
        comment_counts=dict(comment_counts),
        comment_subreddits=dict(comment_subreddits),
        comment_tokens=dict(comment_tokens),
        post_counts=dict(post_counts),
        message_counts=dict(message_counts),
    )


def _resolve_paths(paths: Optional[Sequence[Path]], filename: str) -> List[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).exists()]
    cfg = get_config()
    export_dir = cfg.reddit_export_dir
    if not export_dir:
        return []
    target = export_dir / filename
    return [target] if target.exists() else []


def _path_sig(paths: Optional[Sequence[Path]], filename: str) -> Tuple[Tuple[str, ...], str]:
    resolved = _resolve_paths(paths, filename)
    return tuple(str(p) for p in resolved), files_signature(resolved)


@persistent_cache("reddit_comments", depends_on=lambda paths=None: _path_sig(paths, "comments.csv"))
def _load_comments(paths: Optional[Sequence[Path]]) -> List[RedditComment]:
    comments: List[RedditComment] = []
    for path in _resolve_paths(paths, "comments.csv"):
        comments.extend(_read_comment_csv(path))
    return comments


def iter_comments(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditComment]:
    yield from _load_comments(paths)


@persistent_cache("reddit_posts", depends_on=lambda paths=None: _path_sig(paths, "posts.csv"))
def _load_posts(paths: Optional[Sequence[Path]]) -> List[RedditPost]:
    posts: List[RedditPost] = []
    for path in _resolve_paths(paths, "posts.csv"):
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


@persistent_cache(
    "reddit_saved_posts",
    depends_on=lambda paths=None: _path_sig(paths, "saved_posts.csv"),
)
def _load_saved_posts(paths: Optional[Sequence[Path]]) -> List[RedditSavedItem]:
    saved: List[RedditSavedItem] = []
    for path in _resolve_paths(paths, "saved_posts.csv"):
        saved.extend(_read_saved_csv(path, "post"))
    return saved


@persistent_cache(
    "reddit_saved_comments",
    depends_on=lambda paths=None: _path_sig(paths, "saved_comments.csv"),
)
def _load_saved_comments(paths: Optional[Sequence[Path]]) -> List[RedditSavedItem]:
    saved: List[RedditSavedItem] = []
    for path in _resolve_paths(paths, "saved_comments.csv"):
        saved.extend(_read_saved_csv(path, "comment"))
    return saved


def iter_saved_posts(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditSavedItem]:
    yield from _load_saved_posts(paths)


def iter_saved_comments(paths: Optional[Sequence[Path]] = None) -> Iterator[RedditSavedItem]:
    yield from _load_saved_comments(paths)


@persistent_cache(
    "reddit_comment_votes",
    depends_on=lambda paths=None: _path_sig(paths, "comment_votes.csv"),
)
def _load_comment_votes(paths: Optional[Sequence[Path]]) -> List[RedditVote]:
    votes: List[RedditVote] = []
    for path in _resolve_paths(paths, "comment_votes.csv"):
        votes.extend(_read_vote_csv(path, "comment"))
    return votes


@persistent_cache(
    "reddit_post_votes",
    depends_on=lambda paths=None: _path_sig(paths, "post_votes.csv"),
)
def _load_post_votes(paths: Optional[Sequence[Path]]) -> List[RedditVote]:
    votes: List[RedditVote] = []
    for path in _resolve_paths(paths, "post_votes.csv"):
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


@persistent_cache(
    "reddit_message_headers",
    depends_on=lambda paths=None: (
        tuple(str(p) for p in _message_paths(paths)),
        files_signature(_message_paths(paths)),
    ),
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


# _month_key imported from core.parse


# _month_in_range imported from core.parse


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


_parse_datetime = parse_datetime  # from core.parse (handles Z, UTC suffixes)


# _safe_int imported from core.parse


# ══════════════════════════════════════════════════════════════════════════════
# Derived analytics
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RedditDayActivity:
    date: date
    comment_count: int
    post_count: int
    top_subreddits: tuple[str, ...]
    total_words: int


def daily_activity(*, start: date, end: date) -> list[RedditDayActivity]:
    """Daily Reddit engagement: comments, posts, subreddits."""
    from collections import defaultdict
    from ..core.primitives import TopN

    by_day: dict[date, dict] = defaultdict(lambda: {"comments": 0, "posts": 0, "subs": TopN(5), "words": 0})
    for comment in iter_comments():
        if comment.created is None: continue
        d = comment.created.date()
        if d < start or d > end: continue
        by_day[d]["comments"] += 1
        by_day[d]["subs"].add(comment.subreddit or "unknown", 1)
        by_day[d]["words"] += len(comment.body.split()) if comment.body else 0
    for post in iter_posts():
        if post.created is None: continue
        d = post.created.date()
        if d < start or d > end: continue
        by_day[d]["posts"] += 1

    return [
        RedditDayActivity(
            date=d, comment_count=v["comments"], post_count=v["posts"],
            top_subreddits=tuple(s for s, _ in v["subs"].items), total_words=v["words"],
        )
        for d, v in sorted(by_day.items())
    ]


def subreddit_distribution(*, start: date, end: date) -> list[tuple[str, int, float]]:
    """Subreddit engagement distribution: (subreddit, comment_count, pct)."""
    from collections import Counter
    subs: Counter[str] = Counter()
    for comment in iter_comments():
        if comment.created is None: continue
        d = comment.created.date()
        if d < start or d > end: continue
        subs[comment.subreddit or "unknown"] += 1
    total = sum(subs.values())
    return [(s, c, round(c / total * 100, 1)) for s, c in subs.most_common(20)] if total else []
