"""Materialize canonical browser bookmarks from browser/profile exports."""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from ..core.config import get_config
from ..sources.bookmarks import BookmarkEvent, bookmarks_manifest_path, bookmarks_path
from ..sources.web import normalize_url

_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_BOOKMARK_SQL = """
    SELECT b.id, b.title, b.dateAdded, p.url, parent.title
    FROM moz_bookmarks b
    JOIN moz_places p ON b.fk = p.id
    LEFT JOIN moz_bookmarks parent ON b.parent = parent.id
    WHERE b.type = 1
    ORDER BY b.dateAdded
"""


def materialize_bookmarks(*, root: Path | None = None, output: Path | None = None) -> dict[str, Any]:
    cfg = get_config()
    root = root or cfg.browser_bookmarks_root
    output = output or bookmarks_path(root)
    raw_roots = _bookmark_roots(root)
    rows = list(_dedupe(_iter_all_bookmarks(raw_roots)))
    rows.sort(key=lambda row: (row.added_at or datetime.min.replace(tzinfo=timezone.utc), row.normalized_url, row.title))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            payload = asdict(row)
            payload["added_at"] = row.added_at.isoformat() if row.added_at else None
            payload["caveats"] = list(row.caveats)
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    first = next((row.added_at for row in rows if row.added_at), None)
    last = next((row.added_at for row in reversed(rows) if row.added_at), None)
    manifest = {
        "dataset": "browser.bookmarks",
        "materialized_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "materialized_path": str(output),
        "raw_roots": [str(path) for path in raw_roots],
        "row_count": len(rows),
        "first_date": first.date().isoformat() if first else None,
        "last_date": last.date().isoformat() if last else None,
        "input_files": [str(path) for path in _discover_bookmark_files(raw_roots)],
    }
    bookmarks_manifest_path(root).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _bookmark_roots(root: Path) -> tuple[Path, ...]:
    return (root,) if root.exists() else ()


def _discover_bookmark_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(root.rglob("*_bookmarks.json"))
        files.extend(root.rglob("Bookmarks"))
        files.extend(root.rglob("*Bookmarks.bak"))
        files.extend(root.rglob("places.sqlite"))
        files.extend(root.rglob("bookmarks.html"))
        files.extend(root.rglob("bookmarks-*.jsonlz4"))
    return sorted({path for path in files if path.is_file()})


def _iter_all_bookmarks(roots: tuple[Path, ...]) -> Iterator[BookmarkEvent]:
    for path in _discover_bookmark_files(roots):
        lower = path.name.lower()
        try:
            if lower == "places.sqlite":
                yield from _firefox_places(path)
            elif lower.endswith(".jsonlz4"):
                yield from _firefox_backup(path)
            elif lower == "bookmarks.html":
                yield from _bookmarks_html(path)
            else:
                yield from _chromium_json(path)
        except (OSError, sqlite3.Error, json.JSONDecodeError, UnicodeDecodeError):
            continue


def _chromium_json(path: Path) -> Iterator[BookmarkEvent]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    roots = payload.get("roots")
    if not isinstance(roots, dict):
        return
    browser = _browser_from_path(path)
    profile = _profile_from_path(path)
    for name, node in roots.items():
        yield from _chromium_node(browser, profile, str(name), node, path)


def _chromium_node(browser: str, profile: str, folder: str, node: object, path: Path) -> Iterator[BookmarkEvent]:
    if not isinstance(node, dict):
        return
    if node.get("type") == "url":
        yield _event(
            browser=browser,
            profile=profile,
            url=str(node.get("url") or ""),
            title=str(node.get("name") or ""),
            folder=folder,
            added_at=_chrome_time(node.get("date_added")),
            source_path=path,
            source="chromium_bookmarks",
        )
        return
    children = node.get("children")
    if not isinstance(children, list):
        return
    name = str(node.get("name") or folder)
    child_folder = folder if name == folder else f"{folder}/{name}"
    for child in children:
        yield from _chromium_node(browser, profile, child_folder, child, path)


def _firefox_places(path: Path) -> Iterator[BookmarkEvent]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for bookmark_id, title, date_added, url, folder in conn.execute(_BOOKMARK_SQL):
            yield _event(
                browser="firefox",
                profile=_profile_from_path(path),
                url=str(url or ""),
                title=str(title or ""),
                folder=str(folder or ""),
                added_at=_unix_micros(date_added),
                source_path=path,
                source=f"firefox_places:{bookmark_id}",
            )
    finally:
        conn.close()


def _firefox_backup(path: Path) -> Iterator[BookmarkEvent]:
    raw = path.read_bytes()
    try:
        import lz4.block
    except ImportError:
        return
    if raw.startswith(b"mozLz40\0"):
        raw = lz4.block.decompress(raw[8:])
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    yield from _firefox_backup_node(_profile_from_path(path), "", payload, path)


def _firefox_backup_node(profile: str, folder: str, node: object, path: Path) -> Iterator[BookmarkEvent]:
    if not isinstance(node, dict):
        return
    uri = node.get("uri")
    if isinstance(uri, str) and uri:
        yield _event(
            browser="firefox",
            profile=profile,
            url=uri,
            title=str(node.get("title") or ""),
            folder=folder,
            added_at=_unix_micros(node.get("dateAdded")),
            source_path=path,
            source="firefox_jsonlz4",
        )
        return
    name = str(node.get("title") or folder)
    child_folder = folder if not name else f"{folder}/{name}".strip("/")
    for child in node.get("children") or ():
        yield from _firefox_backup_node(profile, child_folder, child, path)


class _BookmarkHtmlParser(html.parser.HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.rows: list[BookmarkEvent] = []
        self._pending: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        data = {key.lower(): value or "" for key, value in attrs}
        if data.get("href"):
            self._pending = data

    def handle_data(self, data: str) -> None:
        if self._pending is None:
            return
        self.rows.append(
            _event(
                browser="firefox",
                profile=_profile_from_path(self.path),
                url=self._pending.get("href", ""),
                title=data.strip(),
                folder="",
                added_at=_unix_seconds(self._pending.get("add_date")),
                source_path=self.path,
                source="bookmarks_html",
            )
        )
        self._pending = None


def _bookmarks_html(path: Path) -> Iterator[BookmarkEvent]:
    parser = _BookmarkHtmlParser(path)
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    yield from parser.rows


def _dedupe(rows: Iterator[BookmarkEvent]) -> Iterator[BookmarkEvent]:
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        if not row.url:
            continue
        key = (row.normalized_url, row.title, row.added_at.isoformat() if row.added_at else "")
        if key in seen:
            continue
        seen.add(key)
        yield row


def _event(
    *,
    browser: str,
    profile: str,
    url: str,
    title: str,
    folder: str,
    added_at: datetime | None,
    source_path: Path,
    source: str,
) -> BookmarkEvent:
    norm = normalize_url(url)
    domain = urlparse(url).netloc.lower()
    digest = hashlib.sha1(f"{norm}\0{title}\0{added_at}".encode("utf-8", errors="replace")).hexdigest()
    caveats = () if added_at else ("missing_added_at",)
    return BookmarkEvent(
        bookmark_id=digest,
        source=source,
        browser=browser,
        profile=profile,
        url=url,
        normalized_url=norm,
        domain=domain,
        title=title,
        folder=folder,
        added_at=added_at,
        source_path=str(source_path),
        caveats=caveats,
    )


def _chrome_time(value: object) -> datetime | None:
    try:
        micros = int(str(value))
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    return _WEBKIT_EPOCH + (datetime.fromtimestamp(micros / 1_000_000, timezone.utc) - datetime.fromtimestamp(0, timezone.utc))


def _unix_micros(value: object) -> datetime | None:
    try:
        micros = int(str(value))
    except (TypeError, ValueError):
        return None
    if micros <= 0:
        return None
    return datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc)


def _unix_seconds(value: object) -> datetime | None:
    try:
        seconds = int(str(value))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _browser_from_path(path: Path) -> str:
    name = path.name.lower()
    text = str(path).lower()
    if "vivaldi" in name or "vivaldi" in text:
        return "vivaldi"
    if "edge" in name or "edge" in text:
        return "edge"
    if "firefox" in text:
        return "firefox"
    return "chrome"


def _profile_from_path(path: Path) -> str:
    parts = path.parts
    for marker in ("historical", "windows-profiles"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]
    return path.parent.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialize canonical browser bookmarks")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    sys.stdout.write(json.dumps(materialize_bookmarks(root=args.root, output=args.output), indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
