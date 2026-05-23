"""Historical local datasets in canonical `/realm/data` homes.

These datasets came from old disk images or historical exports, but the source
API is organized by what the data is: bookmarks, Calibre metadata, cloud-file
inventories, page snapshots, software inventories, and legacy app logs.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Protocol

from ..core.config import get_config
from ..core.parse import parse_int

__all__ = [
    "HistoricalDatasetSummary",
    "BrowserBookmark",
    "CalibreBook",
    "OneDriveInventoryItem",
    "SingleFileSnapshot",
    "SoftwareInstall",
    "HistoricalLogFile",
    "source_summary",
    "browser_bookmarks",
    "calibre_books",
    "onedrive_inventory",
    "singlefile_snapshots",
    "software_installs",
    "legacy_log_files",
]


class HistoricalDatasetConfig(Protocol):
    @property
    def browser_bookmarks_root(self) -> Path: ...

    @property
    def arbtt_root(self) -> Path: ...

    @property
    def teams_root(self) -> Path: ...

    @property
    def tortoisesvn_root(self) -> Path: ...

    @property
    def software_inventory_root(self) -> Path: ...

    @property
    def calibre_root(self) -> Path: ...

    @property
    def onedrive_inventory_root(self) -> Path: ...

    @property
    def singlefile_root(self) -> Path: ...


@dataclass(frozen=True)
class HistoricalDatasetSummary:
    source: str
    path: str
    count: int
    first_date: date | None
    last_date: date | None


@dataclass(frozen=True)
class BrowserBookmark:
    browser: str
    title: str
    url: str
    folder: str
    added_at: datetime | None
    source: str


@dataclass(frozen=True)
class CalibreBook:
    book_id: int
    title: str
    authors: tuple[str, ...]
    tags: tuple[str, ...]
    added_at: datetime | None
    pubdate: datetime | None
    path: str
    formats: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class OneDriveInventoryItem:
    path: str
    size_bytes: int | None
    modified_at: datetime | None
    source: str


@dataclass(frozen=True)
class SingleFileSnapshot:
    title: str
    captured_at: datetime | None
    filename: str
    source: str


@dataclass(frozen=True)
class SoftwareInstall:
    machine: str
    name: str
    version: str
    publisher: str
    installed_on: date | None
    source: str


@dataclass(frozen=True)
class HistoricalLogFile:
    source_kind: str
    path: str
    size_bytes: int
    first_date: date | None
    last_date: date | None
    line_count: int


def browser_bookmarks(root: Path | None = None) -> Iterator[BrowserBookmark]:
    base = root or get_config().browser_bookmarks_root
    for path in sorted(base.rglob("*_bookmarks.json")):
        browser = path.name.removesuffix("_bookmarks.json")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        roots = payload.get("roots")
        if not isinstance(roots, dict):
            continue
        for root_name, node in roots.items():
            yield from _bookmark_node(browser, str(root_name), node, str(path))


def calibre_books(root: Path | None = None) -> Iterator[CalibreBook]:
    base = root or get_config().calibre_root
    for path in sorted(base.rglob("metadata.db")):
        yield from _calibre_db(path)


def onedrive_inventory(root: Path | None = None) -> Iterator[OneDriveInventoryItem]:
    base = root or get_config().onedrive_inventory_root
    for path in sorted(base.rglob("file_inventory.tsv")):
        with path.open(newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.reader(handle, delimiter="|"):
                if len(row) < 3:
                    continue
                yield OneDriveInventoryItem(
                    path=row[0],
                    size_bytes=parse_int(row[1]),
                    modified_at=_parse_datetime(row[2]),
                    source=str(path),
                )


def singlefile_snapshots(root: Path | None = None) -> Iterator[SingleFileSnapshot]:
    base = root or get_config().singlefile_root
    for path in sorted(base.rglob("singlefile_webarchive_filenames.txt")):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw == "./":
                    continue
                yield _singlefile_row(raw, str(path))


def software_installs(root: Path | None = None) -> Iterator[SoftwareInstall]:
    base = root or get_config().software_inventory_root
    for path in sorted(base.rglob("installed_software.txt")):
        machine = path.parent.name
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = _software_line(machine, line, str(path))
                if item is not None:
                    yield item


def legacy_log_files(cfg: HistoricalDatasetConfig | None = None) -> Iterator[HistoricalLogFile]:
    cfg = cfg or get_config()
    roots = (
        ("teams", cfg.teams_root, "*.txt"),
        ("tortoisesvn", cfg.tortoisesvn_root, "*.txt"),
        ("arbtt", cfg.arbtt_root, "capture.log"),
    )
    for source_kind, root, pattern in roots:
        for path in sorted(root.rglob(pattern)):
            yield _log_file(source_kind, path)


def source_summary(cfg: HistoricalDatasetConfig | None = None) -> tuple[HistoricalDatasetSummary, ...]:
    cfg = cfg or get_config()
    summaries = [
        _summary("browser_bookmarks", cfg.browser_bookmarks_root, browser_bookmarks(cfg.browser_bookmarks_root)),
        _summary("calibre_books", cfg.calibre_root, calibre_books(cfg.calibre_root)),
        _summary("onedrive_inventory", cfg.onedrive_inventory_root, onedrive_inventory(cfg.onedrive_inventory_root)),
        _summary("singlefile_snapshots", cfg.singlefile_root, singlefile_snapshots(cfg.singlefile_root)),
        _summary("software_installs", cfg.software_inventory_root, software_installs(cfg.software_inventory_root)),
    ]
    log_rows = tuple(legacy_log_files(cfg))
    if log_rows:
        first = min((row.first_date for row in log_rows if row.first_date), default=None)
        last = max((row.last_date for row in log_rows if row.last_date), default=None)
        summaries.append(HistoricalDatasetSummary(
            source="legacy_app_logs",
            path=str(get_config().captures_root),
            count=sum(row.line_count for row in log_rows),
            first_date=first,
            last_date=last,
        ))
    return tuple(summaries)


def _bookmark_node(browser: str, folder: str, node: object, source: str) -> Iterator[BrowserBookmark]:
    if not isinstance(node, dict):
        return
    if node.get("type") == "url":
        yield BrowserBookmark(
            browser=browser,
            title=str(node.get("name") or ""),
            url=str(node.get("url") or ""),
            folder=folder,
            added_at=_chrome_time(node.get("date_added")),
            source=source,
        )
        return
    children = node.get("children")
    if not isinstance(children, list):
        return
    name = str(node.get("name") or folder)
    child_folder = folder if name == folder else f"{folder}/{name}"
    for child in children:
        yield from _bookmark_node(browser, child_folder, child, source)


def _calibre_db(path: Path) -> Iterator[CalibreBook]:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return
    try:
        rows = conn.execute(
            """
            select b.id, b.title, b.timestamp, b.pubdate, b.path,
                   group_concat(distinct a.name),
                   group_concat(distinct t.name),
                   group_concat(distinct d.format)
            from books b
            left join books_authors_link bal on bal.book = b.id
            left join authors a on a.id = bal.author
            left join books_tags_link btl on btl.book = b.id
            left join tags t on t.id = btl.tag
            left join data d on d.book = b.id
            group by b.id
            order by b.id
            """
        ).fetchall()
    finally:
        conn.close()
    for book_id, title, added, pubdate, book_path, authors, tags, formats in rows:
        yield CalibreBook(
            book_id=int(book_id),
            title=str(title or ""),
            authors=_split_group(authors),
            tags=_split_group(tags),
            added_at=_parse_datetime(added),
            pubdate=_parse_datetime(pubdate),
            path=str(book_path or ""),
            formats=_split_group(formats),
            source=str(path),
        )


def _singlefile_row(raw: str, source: str) -> SingleFileSnapshot:
    name = raw.removeprefix("./")
    match = re.match(r"(?P<ms>\d{13})-(?P<title>.*)-\((?P<stamp>\d{4}-\d{2}-\d{2} \d{2}_\d{2}_\d{2}(?:\.\d+)?)\)\.html$", name)
    if not match:
        return SingleFileSnapshot(title=name, captured_at=None, filename=name, source=source)
    title = match.group("title")
    stamp = match.group("stamp").replace("_", ":")
    return SingleFileSnapshot(title=title, captured_at=_parse_datetime(stamp), filename=name, source=source)


def _software_line(machine: str, line: str, source: str) -> SoftwareInstall | None:
    text = line.strip()
    if not text or text.startswith("Loaded "):
        return None
    installed_on = None
    match = re.match(r"(?P<date>\d{8})\s+(?P<rest>.*)", text)
    if match:
        installed_on = _parse_date(match.group("date"))
        text = match.group("rest").strip()
    parts = re.match(r"(?P<name>.*?)\s+\((?P<version>[^)]*)\)\s+\[(?P<publisher>[^]]*)\]$", text)
    if parts:
        name = parts.group("name").strip()
        if not name:
            return None
        return SoftwareInstall(machine, name, parts.group("version").strip(), parts.group("publisher").strip(), installed_on, source)
    return SoftwareInstall(machine, text, "", "", installed_on, source)


def _log_file(source_kind: str, path: Path) -> HistoricalLogFile:
    first = None
    last = None
    lines = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines += 1
            value = _line_date(source_kind, line)
            if value is None:
                continue
            first = value if first is None or value < first else first
            last = value if last is None or value > last else last
    return HistoricalLogFile(source_kind, str(path), path.stat().st_size, first, last, lines)


def _line_date(source_kind: str, line: str) -> date | None:
    if source_kind == "tortoisesvn":
        match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", line)
        if match:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = re.match(r"\w{3} \w{3} +(\d{1,2}) (\d{4})", line)
    if match:
        month = datetime.strptime(line[:3 + 1 + 3], "%a %b").month
        return date(int(match.group(2)), month, int(match.group(1)))
    return None


def _summary(source: str, path: Path, rows: Iterator[object]) -> HistoricalDatasetSummary:
    count = 0
    first = None
    last = None
    for row in rows:
        count += 1
        value = getattr(row, "added_at", None) or getattr(row, "modified_at", None) or getattr(row, "captured_at", None) or getattr(row, "installed_on", None)
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            first = value if first is None or value < first else first
            last = value if last is None or value > last else last
    return HistoricalDatasetSummary(source, str(path), count, first, last)


def _chrome_time(value: object) -> datetime | None:
    try:
        raw = int(str(value))
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=raw)


def _parse_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.startswith(("0101-01-01", "2000-01-01")):
        return None
    for candidate in (text, text.replace(" ", "T"), text.replace(" ", "T") + "+00:00"):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _split_group(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in str(value).split(",") if item.strip())
