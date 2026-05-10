"""Readers for export-style data sources and knowledgebase notes.

This module is the shared home for:

- Goodreads and Raindrop exports under `/realm/data/exports/...`,
- Facebook Messenger and Wykop processed/raw trees,
- Dendron-style note reads rooted at `/realm/project/knowledgebase/`.

It stays read-only. Refresh and acquisition workflows belong under
`lynchpin.cli.*`.
"""

from __future__ import annotations

import csv
import json
from datetime import timezone
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, TypeVar

import yaml

from ..core.cache import file_signature, persistent_cache
from ..core.cache import files_signature
from ..core.config import get_config
from ..core.parse import in_month_range, month_key, parse_date_from_any, parse_datetime, parse_float, parse_int, safe_int

__all__ = [
    "GoodreadsBook",
    "RaindropBookmark",
    "RaindropExport",
    "MessengerThread",
    "MessengerMessage",
    "WykopLinkComment",
    "WykopEntry",
    "WykopEntryComment",
    "WykopActivitySummary",
    "DendronNote",
    "iter_goodreads_books",
    "summarize_goodreads_library",
    "list_raindrop_exports",
    "iter_raindrop_bookmarks",
    "iter_raindrop_bookmarks_by_name",
    "iter_raindrop_bookmarks_all",
    "summarize_raindrop_bookmarks",
    "iter_fbmessenger_threads",
    "iter_fbmessenger_messages",
    "summarize_wykop_activity",
    "iter_wykop_link_comments",
    "iter_wykop_entries",
    "iter_wykop_entry_comments",
    "iter_dendron_notes",
    "MessengerDayActivity",
    "RaindropDayActivity",
    "daily_messenger_activity",
    "daily_raindrop_activity",
]

# =========================================================================
# Section 1: Goodreads (from sources/exports/goodreads.py)
# =========================================================================


@dataclass(frozen=True)
class GoodreadsBook:
    book_id: str
    title: str
    author: str
    additional_authors: str
    isbn: str
    isbn13: str
    my_rating: Optional[int]
    average_rating: Optional[float]
    publisher: str
    binding: str
    pages: Optional[int]
    year_published: Optional[int]
    original_year_published: Optional[int]
    date_read: Optional[date]
    date_added: Optional[date]
    shelves: list[str]
    exclusive_shelf: str
    my_review: str
    private_notes: str
    read_count: Optional[int]
    owned_copies: Optional[int]
    source: str


def _resolve_goodreads_library(path: Optional[Path]) -> Optional[Path]:
    if path is not None:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    cfg = get_config()
    candidate = cfg.goodreads_library
    return candidate if candidate.exists() else None


def _goodreads_library_signature(path: Optional[Path] = None) -> object:
    resolved = _resolve_goodreads_library(path)
    if not resolved:
        return ("", ("", None, None))
    return (str(resolved), file_signature(resolved))


@persistent_cache("goodreads_library", depends_on=_goodreads_library_signature)
def _load_goodreads_books(path: Optional[Path] = None) -> list[GoodreadsBook]:
    resolved = _resolve_goodreads_library(path)
    if resolved is None:
        return []
    books: list[GoodreadsBook] = []
    with resolved.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            books.append(_goodreads_row_to_book(row, resolved))
    return books


def iter_goodreads_books(path: Optional[Path] = None) -> Iterator[GoodreadsBook]:
    yield from _load_goodreads_books(path)


def summarize_goodreads_library(
    start_month: str,
    end_month: str,
    *,
    path: Optional[Path] = None,
) -> tuple[dict[str, int], dict[str, int], dict[str, Counter[str]], dict[str, Counter[str]]]:
    read_counts: dict[str, int] = defaultdict(int)
    added_counts: dict[str, int] = defaultdict(int)
    per_month_authors_read: dict[str, Counter[str]] = defaultdict(Counter)
    per_month_titles_read: dict[str, Counter[str]] = defaultdict(Counter)
    for book in iter_goodreads_books(path):
        if book.date_read is not None:
            month = _date_month_key(book.date_read)
            if in_month_range(month, start_month, end_month):
                read_counts[month] += 1
                if book.author:
                    per_month_authors_read[month][book.author] += 1
                if book.title:
                    per_month_titles_read[month][book.title] += 1

        if book.date_added is not None:
            month = _date_month_key(book.date_added)
            if in_month_range(month, start_month, end_month):
                added_counts[month] += 1

    return read_counts, added_counts, per_month_authors_read, per_month_titles_read


def _goodreads_row_to_book(row: dict[str, str], source: Path) -> GoodreadsBook:
    shelves = _goodreads_split_csv_field(row.get("Bookshelves") or "")
    return GoodreadsBook(
        book_id=(row.get("Book Id") or "").strip(),
        title=(row.get("Title") or "").strip(),
        author=(row.get("Author") or "").strip(),
        additional_authors=(row.get("Additional Authors") or "").strip(),
        isbn=_goodreads_normalize_isbn(row.get("ISBN") or ""),
        isbn13=_goodreads_normalize_isbn(row.get("ISBN13") or ""),
        my_rating=parse_int(row.get("My Rating")),
        average_rating=parse_float(row.get("Average Rating")),
        publisher=(row.get("Publisher") or "").strip(),
        binding=(row.get("Binding") or "").strip(),
        pages=parse_int(row.get("Number of Pages")),
        year_published=parse_int(row.get("Year Published")),
        original_year_published=parse_int(row.get("Original Publication Year")),
        date_read=parse_date(row.get("Date Read") or ""),
        date_added=parse_date(row.get("Date Added") or ""),
        shelves=shelves,
        exclusive_shelf=(row.get("Exclusive Shelf") or "").strip(),
        my_review=(row.get("My Review") or "").strip(),
        private_notes=(row.get("Private Notes") or "").strip(),
        read_count=parse_int(row.get("Read Count")),
        owned_copies=parse_int(row.get("Owned Copies")),
        source=str(source),
    )


parse_date = parse_date_from_any


def _goodreads_split_csv_field(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _goodreads_normalize_isbn(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("=\"") and cleaned.endswith("\""):
        cleaned = cleaned[2:-1]
    if cleaned.startswith("='") and cleaned.endswith("'"):
        cleaned = cleaned[2:-1]
    return cleaned.strip()


def _date_month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"








# =========================================================================
# Section 2: Raindrop (from sources/exports/raindrop.py)
# =========================================================================


@dataclass
class RaindropBookmark:
    id: int
    title: str
    url: str
    folder: str
    tags: list[str]
    created: Optional[datetime]
    note: str
    excerpt: str
    cover: Optional[str]
    favorite: bool
    raw: dict[str, object]


@dataclass(frozen=True)
class RaindropExport:
    label: str
    path: Path
    mtime: datetime
    is_default: bool


def list_raindrop_exports(root: Optional[Path] = None) -> list[RaindropExport]:
    cfg = get_config()
    base = Path(root) if root else cfg.raindrop_dir
    if not base.exists():
        return []
    exports: list[RaindropExport] = []
    for path in sorted(base.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        label = path.stem
        exports.append(
            RaindropExport(
                label=label,
                path=path,
                mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                is_default=cfg.raindrop_csv is not None and path == cfg.raindrop_csv,
            )
        )
    return exports


def iter_raindrop_bookmarks(csv_path: Optional[Path] = None) -> Iterator[RaindropBookmark]:
    cfg = get_config()
    target = Path(csv_path) if csv_path else cfg.raindrop_csv
    if not target or not target.exists():
        return iter(())

    def generator() -> Iterator[RaindropBookmark]:
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                tags = _raindrop_parse_tags(row.get("tags"))
                created = parse_datetime(row.get("created"))
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
                    cover=_raindrop_strip(row.get("cover")),
                    favorite=favorite,
                    raw=row,
                )

    return generator()


def iter_raindrop_bookmarks_by_name(name: str, root: Optional[Path] = None) -> Iterator[RaindropBookmark]:
    """Iterate bookmarks for exports whose filenames contain the given token."""
    token = name.lower()
    for export in list_raindrop_exports(root):
        if token in export.label.lower():
            yield from iter_raindrop_bookmarks(export.path)


def iter_raindrop_bookmarks_all(root: Optional[Path] = None) -> Iterator[tuple[RaindropExport, RaindropBookmark]]:
    """Iterate all exports, yielding (export, bookmark) pairs."""
    for export in list_raindrop_exports(root):
        for bookmark in iter_raindrop_bookmarks(export.path):
            yield export, bookmark


def summarize_raindrop_bookmarks(
    start_month: str,
    end_month: str,
    *,
    csv_path: Optional[Path] = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for bookmark in iter_raindrop_bookmarks(csv_path):
        if bookmark.created is None:
            continue
        month = f"{bookmark.created.year:04d}-{bookmark.created.month:02d}"
        if start_month <= month <= end_month:
            counts[month] += 1
    return dict(counts)


def _raindrop_parse_tags(raw: Optional[str]) -> list[str]:
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


def _raindrop_strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class RaindropDayActivity:
    date: date
    bookmarks_added: int
    unique_tags: int


def daily_raindrop_activity(*, start: date, end: date) -> list[RaindropDayActivity]:
    """Daily bookmark additions."""
    by_date: dict[date, tuple[int, set[str]]] = defaultdict(lambda: (0, set()))
    for _export, bookmark in iter_raindrop_bookmarks_all():
        if bookmark.created is None:
            continue
        d = bookmark.created.date()
        if d < start or d >= end:
            continue
        count, tags = by_date[d]
        tags.update(bookmark.tags)
        by_date[d] = (count + 1, tags)
    return sorted(
        [RaindropDayActivity(date=d, bookmarks_added=count, unique_tags=len(tags)) for d, (count, tags) in by_date.items()],
        key=lambda x: x.date,
    )


# =========================================================================
# Section 3: Facebook Messenger (from sources/exports/fbmessenger.py)
# =========================================================================


@dataclass(frozen=True)
class MessengerThread:
    thread_name: str
    participants: list[str]
    source: str


@dataclass(frozen=True)
class MessengerMessage:
    thread_name: str
    participants: list[str]
    sender: str
    timestamp: Optional[datetime]
    text: Optional[str]
    kind: str
    is_unsent: bool
    media_count: int
    reaction_count: int
    source: str


def _resolve_fbmessenger_export_dir(root: Path) -> Optional[Path]:
    if root.is_dir() and (root / "messages").exists():
        return root
    if not root.exists():
        return None
    subdirs = [child for child in root.iterdir() if child.is_dir() and child.name not in {"raw", "archive"}]
    if not subdirs:
        return None
    dated: list[tuple[datetime, Path]] = []
    fallback: list[Path] = []
    for path in subdirs:
        try:
            parsed = datetime.strptime(path.name, "%Y-%m-%d")
        except ValueError:
            fallback.append(path)
            continue
        dated.append((parsed, path))
    if dated:
        dated.sort(key=lambda item: item[0], reverse=True)
        return dated[0][1]
    fallback.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return fallback[0]


def _fbmessenger_thread_files(paths: Optional[list[Path]]) -> list[Path]:
    if paths is not None:
        return [Path(path) for path in paths if Path(path).is_file()]
    cfg = get_config()
    export_dir = _resolve_fbmessenger_export_dir(cfg.fbmessenger_gdpr_root)
    if not export_dir:
        return []
    messages_dir = export_dir / "messages"
    if not messages_dir.exists():
        return []
    return sorted(messages_dir.glob("*.json"))


def _fbmessenger_thread_signature(paths: Optional[list[Path]] = None) -> object:
    resolved = _fbmessenger_thread_files(paths)
    return tuple(str(path) for path in resolved), files_signature(resolved)


@persistent_cache("fbmessenger_threads", depends_on=_fbmessenger_thread_signature)
def _load_fbmessenger_threads(paths: Optional[list[Path]] = None) -> list[MessengerThread]:
    threads: list[MessengerThread] = []
    for path in _fbmessenger_thread_files(paths):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            continue
        participants = [_fbmessenger_clean_text(p) for p in list(data.get("participants", []) or [])]
        thread_name = _fbmessenger_clean_text(data.get("threadName") or path.stem)
        threads.append(
            MessengerThread(
                thread_name=thread_name,
                participants=participants,
                source=_fbmessenger_clean_path(path),
            )
        )
    return threads


def iter_fbmessenger_threads(paths: Optional[list[Path]] = None) -> Iterator[MessengerThread]:
    yield from _load_fbmessenger_threads(paths)


@persistent_cache("fbmessenger_messages", depends_on=_fbmessenger_thread_signature)
def _load_fbmessenger_messages(paths: Optional[list[Path]] = None) -> list[MessengerMessage]:
    messages: list[MessengerMessage] = []
    for path in _fbmessenger_thread_files(paths):
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            continue
        participants = [_fbmessenger_clean_text(p) for p in list(data.get("participants", []) or [])]
        thread_name = _fbmessenger_clean_text(data.get("threadName") or path.stem)
        for message in data.get("messages", []) or []:
            ts_raw = message.get("timestamp")
            timestamp = None
            if isinstance(ts_raw, (int, float)):
                timestamp = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
            messages.append(
                MessengerMessage(
                    thread_name=thread_name,
                    participants=participants,
                    sender=_fbmessenger_clean_text(message.get("senderName") or ""),
                    timestamp=timestamp,
                    text=_fbmessenger_clean_text(message.get("text")),
                    kind=_fbmessenger_clean_text(message.get("type") or ""),
                    is_unsent=bool(message.get("isUnsent")),
                    media_count=len(message.get("media") or []),
                    reaction_count=len(message.get("reactions") or []),
                    source=_fbmessenger_clean_path(path),
                )
            )
    return messages


def iter_fbmessenger_messages(paths: Optional[list[Path]] = None) -> Iterator[MessengerMessage]:
    yield from _load_fbmessenger_messages(paths)


def _fbmessenger_clean_text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return value.encode("utf-8", "replace").decode("utf-8")


def _fbmessenger_clean_path(path: Path) -> str:
    text = str(path)
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


@dataclass(frozen=True)
class MessengerDayActivity:
    date: date
    message_count: int
    thread_count: int
    sent_count: int


def daily_messenger_activity(*, start: date, end: date) -> list[MessengerDayActivity]:
    """Daily messenger message counts."""
    # First pass: determine primary user (most frequent sender = export owner)
    sender_counts: Counter[str] = Counter()
    messages = list(iter_fbmessenger_messages())
    for msg in messages:
        if msg.sender:
            sender_counts[msg.sender] += 1
    primary_user = sender_counts.most_common(1)[0][0] if sender_counts else ""

    # Second pass: aggregate by date
    day_messages: dict[date, list[MessengerMessage]] = defaultdict(list)
    for msg in messages:
        if msg.timestamp is None:
            continue
        d = msg.timestamp.date()
        if d < start or d >= end:
            continue
        day_messages[d].append(msg)

    return sorted(
        [
            MessengerDayActivity(
                date=d,
                message_count=len(msgs),
                thread_count=len({m.thread_name for m in msgs}),
                sent_count=sum(1 for m in msgs if m.sender == primary_user),
            )
            for d, msgs in day_messages.items()
        ],
        key=lambda x: x.date,
    )


# =========================================================================
# Section 4: Wykop (from sources/exports/wykop.py)
# =========================================================================

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
TextTokenizer = Callable[[str], Iterable[str]]
T = TypeVar("T")


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


@dataclass
class WykopEntry:
    id: int
    created_at: Optional[datetime]
    url: str
    content: str
    tags: list[str]
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

    for comment in iter_wykop_link_comments(username=username, path=link_comments_path):
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

    for entry in iter_wykop_entries(username=username, path=entries_path):
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

    for entry_comment in iter_wykop_entry_comments(username=username, path=entry_comments_path):
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


def iter_wykop_link_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopLinkComment]:
    path = path or _wykop_profile_file("wykop_links_commented.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_wykop_link_comments(path))


def iter_wykop_entries(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopEntry]:
    path = path or _wykop_profile_file("wykop_entries_added.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_wykop_entries(path))


def iter_wykop_entry_comments(
    username: Optional[str] = None,
    path: Optional[Path] = None,
) -> Iterator[WykopEntryComment]:
    path = path or _wykop_profile_file("wykop_entry_comments.jsonl", username)
    if not path:
        return iter(())
    return iter(_load_wykop_entry_comments(path))


def _wykop_file_signature(path: Path) -> object:
    return file_signature(path)


@persistent_cache("wykop_link_comments", depends_on=_wykop_file_signature)
def _load_wykop_link_comments(path: Path) -> list[WykopLinkComment]:
    return _wykop_read_jsonl(path, _wykop_parse_link_comment)


@persistent_cache("wykop_entries", depends_on=_wykop_file_signature)
def _load_wykop_entries(path: Path) -> list[WykopEntry]:
    return _wykop_read_jsonl(path, _wykop_parse_entry)


@persistent_cache("wykop_entry_comments", depends_on=_wykop_file_signature)
def _load_wykop_entry_comments(path: Path) -> list[WykopEntryComment]:
    return _wykop_read_jsonl(path, _wykop_parse_entry_comment)


def _wykop_read_jsonl(path: Path, mapper: Callable[[dict[str, object]], T | None]) -> list[T]:
    if not path.exists():
        return []
    rows: list[T] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            mapped = mapper(payload)
            if mapped is not None:
                rows.append(mapped)
    return rows


def _wykop_parse_link_comment(payload: dict[str, object]) -> Optional[WykopLinkComment]:
    comment_id = safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopLinkComment(
        id=comment_id,
        created_at=parse_datetime(payload.get("comment_created_at")),
        url=_wykop_as_str(payload.get("comment_url")),
        content=_wykop_as_str(payload.get("comment_content")),
        rating=safe_int(payload.get("comment_rating")),
        link_id=safe_int(payload.get("link_id")),
        link_title=_wykop_as_str(payload.get("link_title")),
        link_url=_wykop_as_str(payload.get("link_url")),
        tags=_wykop_as_list(payload.get("link_tags")),
    )


def _wykop_parse_entry(payload: dict[str, object]) -> Optional[WykopEntry]:
    entry_id = safe_int(payload.get("entry_id"))
    if entry_id is None:
        return None
    return WykopEntry(
        id=entry_id,
        created_at=parse_datetime(payload.get("entry_created_at")),
        url=_wykop_as_str(payload.get("entry_url")),
        content=_wykop_as_str(payload.get("entry_content")),
        tags=_wykop_as_list(payload.get("entry_tags")),
        votes_up=safe_int(payload.get("votes_up")),
        votes_down=safe_int(payload.get("votes_down")),
    )


def _wykop_parse_entry_comment(payload: dict[str, object]) -> Optional[WykopEntryComment]:
    comment_id = safe_int(payload.get("comment_id"))
    if comment_id is None:
        return None
    return WykopEntryComment(
        id=comment_id,
        created_at=parse_datetime(payload.get("comment_created_at")),
        entry_id=safe_int(payload.get("entry_id")),
        url=_wykop_as_str(payload.get("entry_url")),
        content=_wykop_as_str(payload.get("comment_content")),
        rating=safe_int(payload.get("comment_rating")),
    )


def _wykop_profile_file(name: str, username: Optional[str]) -> Optional[Path]:
    cfg = get_config()
    user = username or cfg.wykop_username
    if not user:
        return None
    profile_dir = cfg.wykop_root / user
    return profile_dir / name


def _wykop_as_str(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _wykop_as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


# =========================================================================
# Section 5: Dendron (from sources/libraries/dendron.py)
# =========================================================================


@dataclass
class DendronNote:
    """Representation of a Dendron/Markdown note inside the knowledgebase."""

    path: Path  # Relative path from the vault root
    id: Optional[str]
    title: str
    tags: list[str]
    frontmatter: dict[str, object]
    body: str


def iter_dendron_notes(root: Optional[Path] = None) -> Iterator[DendronNote]:
    """Yield every Markdown note in the Dendron vault."""

    cfg = get_config()
    vault_root = Path(root) if root else cfg.dendron_root
    if not vault_root.exists():
        return iter(())

    def generator() -> Iterator[DendronNote]:
        for path in sorted(vault_root.rglob("*.md")):
            if not path.is_file():
                continue
            rel = path.relative_to(vault_root)
            if any(part.startswith(".") for part in rel.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            frontmatter, body = _dendron_split_frontmatter(text)
            tags = _dendron_normalise_tags(frontmatter)
            title = _dendron_derive_title(frontmatter, body, rel)
            yield DendronNote(
                path=rel,
                id=_dendron_safe_str(frontmatter.get("id")),
                title=title,
                tags=tags,
                frontmatter=frontmatter,
                body=body,
            )

    return generator()


def _dendron_split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            yaml_text = "\n".join(lines[1:idx]).strip()
            body = "\n".join(lines[idx + 1 :]).lstrip("\n")
            if not yaml_text:
                return {}, body
            try:
                frontmatter = yaml.safe_load(yaml_text) or {}
                if not isinstance(frontmatter, dict):
                    return {}, body
                return frontmatter, body
            except yaml.YAMLError:
                return {}, body
    return {}, text


def _dendron_derive_title(frontmatter: dict[str, object], body: str, rel: Path) -> str:
    for key in ("title", "id", "aliases"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    heading = _dendron_first_heading(body)
    if heading:
        return heading
    return rel.stem.replace("_", " ")


def _dendron_first_heading(body: str) -> Optional[str]:
    for line in body.splitlines():
        import re
        match = re.match(r"^\s*#+\s+(.*)", line)
        if match:
            return match.group(1).strip()
    return None


def _dendron_normalise_tags(frontmatter: dict[str, object]) -> list[str]:
    tags = frontmatter.get("tags")
    if isinstance(tags, str):
        return [tag.strip() for tag in tags.split() if tag.strip()]
    if isinstance(tags, list):
        out: list[str] = []
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                out.append(tag.strip())
        return out
    return []


def _dendron_safe_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
