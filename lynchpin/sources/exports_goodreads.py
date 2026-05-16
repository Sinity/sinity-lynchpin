"""Goodreads library export reader."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, Optional

from ..core.cache import file_signature, persistent_cache
from ..core.config import get_config
from ..core.parse import in_month_range, parse_date_from_any, parse_float, parse_int

__all__ = [
    "GoodreadsBook",
    "iter_goodreads_books",
    "summarize_goodreads_library",
]


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


def _resolve_library(path: Optional[Path]) -> Optional[Path]:
    if path is not None:
        candidate = Path(path)
        return candidate if candidate.exists() else None
    cfg = get_config()
    candidate = cfg.goodreads_library
    return candidate if candidate.exists() else None


def _library_signature(path: Optional[Path] = None) -> object:
    resolved = _resolve_library(path)
    if not resolved:
        return ("", ("", None, None))
    return (str(resolved), file_signature(resolved))


@persistent_cache("goodreads_library", depends_on=_library_signature)
def _load_goodreads_books(path: Optional[Path] = None) -> list[GoodreadsBook]:
    resolved = _resolve_library(path)
    if resolved is None:
        return []
    books: list[GoodreadsBook] = []
    with resolved.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            books.append(_row_to_book(row, resolved))
    return books


def _row_to_book(row: dict[str, str], source: Path) -> GoodreadsBook:
    shelves = _split_csv_field(row.get("Bookshelves") or "")
    return GoodreadsBook(
        book_id=(row.get("Book Id") or "").strip(),
        title=(row.get("Title") or "").strip(),
        author=(row.get("Author") or "").strip(),
        additional_authors=(row.get("Additional Authors") or "").strip(),
        isbn=_normalize_isbn(row.get("ISBN") or ""),
        isbn13=_normalize_isbn(row.get("ISBN13") or ""),
        my_rating=parse_int(row.get("My Rating")),
        average_rating=parse_float(row.get("Average Rating")),
        publisher=(row.get("Publisher") or "").strip(),
        binding=(row.get("Binding") or "").strip(),
        pages=parse_int(row.get("Number of Pages")),
        year_published=parse_int(row.get("Year Published")),
        original_year_published=parse_int(row.get("Original Publication Year")),
        date_read=parse_date_from_any(row.get("Date Read") or ""),
        date_added=parse_date_from_any(row.get("Date Added") or ""),
        shelves=shelves,
        exclusive_shelf=(row.get("Exclusive Shelf") or "").strip(),
        my_review=(row.get("My Review") or "").strip(),
        private_notes=(row.get("Private Notes") or "").strip(),
        read_count=parse_int(row.get("Read Count")),
        owned_copies=parse_int(row.get("Owned Copies")),
        source=str(source),
    )


def _split_csv_field(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _normalize_isbn(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("=\"") and cleaned.endswith("\""):
        cleaned = cleaned[2:-1]
    if cleaned.startswith("='") and cleaned.endswith("'"):
        cleaned = cleaned[2:-1]
    return cleaned.strip()


def _date_month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"
