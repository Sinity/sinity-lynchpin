from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from ...core.cache import file_signature, persistent_cache
from ...core.config import get_config


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
    date_read: Optional[datetime]
    date_added: Optional[datetime]
    shelves: List[str]
    exclusive_shelf: str
    my_review: str
    private_notes: str
    read_count: Optional[int]
    owned_copies: Optional[int]
    source: str


def _resolve_library(path: Optional[Path]) -> Optional[Path]:
    if path is not None:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    cfg = get_config()
    candidate = cfg.goodreads_library
    return candidate if candidate.exists() else None


def _library_signature(path: Optional[Path]) -> Tuple[str, Tuple[str, int | None, int | None]]:
    resolved = _resolve_library(path)
    if not resolved:
        return ("", ("", None, None))
    return (str(resolved), file_signature(resolved))


@persistent_cache("goodreads_library", depends_on=lambda path=None: _library_signature(path))
def _load_books(path: Optional[Path]) -> List[GoodreadsBook]:
    resolved = _resolve_library(path)
    if resolved is None:
        return []
    books: List[GoodreadsBook] = []
    with resolved.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            books.append(_row_to_book(row, resolved))
    return books


def iter_books(path: Optional[Path] = None) -> Iterator[GoodreadsBook]:
    yield from _load_books(path)


def summarize_library(
    start_month: str,
    end_month: str,
    *,
    path: Optional[Path] = None,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Counter[str]], Dict[str, Counter[str]]]:
    read_counts: Dict[str, int] = defaultdict(int)
    added_counts: Dict[str, int] = defaultdict(int)
    per_month_authors_read: Dict[str, Counter[str]] = defaultdict(Counter)
    per_month_titles_read: Dict[str, Counter[str]] = defaultdict(Counter)
    for book in iter_books(path):
        if book.date_read is not None:
            month = month_key_from_dt(book.date_read)
            if month_key_in_range(month, start_month, end_month):
                read_counts[month] += 1
                if book.author:
                    per_month_authors_read[month][book.author] += 1
                if book.title:
                    per_month_titles_read[month][book.title] += 1

        if book.date_added is not None:
            month = month_key_from_dt(book.date_added)
            if month_key_in_range(month, start_month, end_month):
                added_counts[month] += 1

    return read_counts, added_counts, per_month_authors_read, per_month_titles_read


def _row_to_book(row: Dict[str, str], source: Path) -> GoodreadsBook:
    shelves = _split_csv_field(row.get("Bookshelves") or "")
    return GoodreadsBook(
        book_id=(row.get("Book Id") or "").strip(),
        title=(row.get("Title") or "").strip(),
        author=(row.get("Author") or "").strip(),
        additional_authors=(row.get("Additional Authors") or "").strip(),
        isbn=_normalize_isbn(row.get("ISBN") or ""),
        isbn13=_normalize_isbn(row.get("ISBN13") or ""),
        my_rating=_parse_int(row.get("My Rating")),
        average_rating=_parse_float(row.get("Average Rating")),
        publisher=(row.get("Publisher") or "").strip(),
        binding=(row.get("Binding") or "").strip(),
        pages=_parse_int(row.get("Number of Pages")),
        year_published=_parse_int(row.get("Year Published")),
        original_year_published=_parse_int(row.get("Original Publication Year")),
        date_read=_parse_date(row.get("Date Read") or ""),
        date_added=_parse_date(row.get("Date Added") or ""),
        shelves=shelves,
        exclusive_shelf=(row.get("Exclusive Shelf") or "").strip(),
        my_review=(row.get("My Review") or "").strip(),
        private_notes=(row.get("Private Notes") or "").strip(),
        read_count=_parse_int(row.get("Read Count")),
        owned_copies=_parse_int(row.get("Owned Copies")),
        source=str(source),
    )


def _parse_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_int(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_float(raw: Optional[str]) -> Optional[float]:
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _split_csv_field(raw: str) -> List[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_isbn(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("=\"") and cleaned.endswith("\""):
        cleaned = cleaned[2:-1]
    if cleaned.startswith("='") and cleaned.endswith("'"):
        cleaned = cleaned[2:-1]
    return cleaned.strip()


def month_key_from_date(d: datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_key_from_dt(dt: datetime) -> str:
    return month_key_from_date(dt)


def month_key_in_range(month: str, start_month: str, end_month: str) -> bool:
    return start_month <= month <= end_month
