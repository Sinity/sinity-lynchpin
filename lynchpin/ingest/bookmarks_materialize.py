"""Materialize canonical browser bookmarks from browser/profile exports."""

from __future__ import annotations

import argparse
import hashlib
import html.parser
import json
import logging
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from ..core.config import get_config
from ..core.errors import MaterializationError
from ..core.io import latest_mtime_iso
from ..sources.bookmarks import BookmarkEvent, bookmarks_manifest_path, bookmarks_path
from ..sources.chrome_profile import discover_profile_history_dbs
from ..sources.web import normalize_url
from ._manifest import atomic_write_ndjson, write_manifest

logger = logging.getLogger(__name__)

_WEBKIT_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_BOOKMARK_SQL = """
    SELECT b.id, b.guid, b.title, b.dateAdded, p.url, parent.title
    FROM moz_bookmarks b
    JOIN moz_places p ON b.fk = p.id
    LEFT JOIN moz_bookmarks parent ON b.parent = parent.id
    WHERE b.type = 1
    ORDER BY b.dateAdded
"""
BOOKMARK_EVENTS_SCHEMA_VERSION = 2


def materialize_bookmarks(*, root: Path | None = None, output: Path | None = None) -> dict[str, Any]:
    cfg = get_config()
    root = root or cfg.browser_bookmarks_root
    output = output or bookmarks_path(root)
    raw_roots = _bookmark_roots(root)
    input_files = _discover_bookmark_files(raw_roots)
    unreadable: list[dict[str, str]] = []
    rows = list(_dedupe(_iter_all_bookmarks(input_files, unreadable=unreadable)))
    rows.sort(key=lambda row: (row.added_at or datetime.min.replace(tzinfo=timezone.utc), row.url, row.source_path, row.bookmark_id))
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_ndjson(
        output,
        (
            {
                **asdict(row),
                "added_at": row.added_at.isoformat() if row.added_at else None,
                "caveats": list(row.caveats),
            }
            for row in rows
        ),
    )

    first = next((row.added_at for row in rows if row.added_at), None)
    last = next((row.added_at for row in reversed(rows) if row.added_at), None)
    manifest = {
        "dataset": "browser.bookmarks",
        "schema_version": BOOKMARK_EVENTS_SCHEMA_VERSION,
        "materialized_path": str(output),
        "raw_roots": [str(path) for path in raw_roots],
        "row_count": len(rows),
        "first_date": first.date().isoformat() if first else None,
        "last_date": last.date().isoformat() if last else None,
        "input_files": [str(path) for path in input_files],
        "input_file_count": len(input_files),
        "input_latest_mtime": latest_mtime_iso(input_files),
        "unreadable_input_files": unreadable,
    }
    write_manifest(bookmarks_manifest_path(root), manifest)
    return manifest


def _bookmark_roots(root: Path) -> tuple[Path, ...]:
    return (root,) if root.exists() else ()


def _discover_bookmark_files(roots: tuple[Path, ...]) -> list[Path]:
    files: set[Path] = set()
    for root in roots:
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False) and _is_bookmark_file(entry.name):
                                files.add(Path(entry.path))
                        except OSError:
                            continue
            except OSError:
                continue
    for history, _label in discover_profile_history_dbs():
        active = history.parent / "Bookmarks"
        # A profile with no Bookmarks file yet (fresh profile, or one that
        # has never bookmarked anything) is not an error; only a Bookmarks
        # file that exists but cannot be read/parsed is (handled below).
        if active.is_file():
            files.add(active)
    return sorted(files)


def _is_bookmark_file(name: str) -> bool:
    return (
        name.endswith("_bookmarks.json")
        or name == "Bookmarks"
        or name.endswith("Bookmarks.bak")
        or name == "places.sqlite"
        or name == "bookmarks.html"
        or (name.startswith("bookmarks-") and name.endswith(".jsonlz4"))
    )


def _iter_all_bookmarks(paths: list[Path], *, unreadable: list[dict[str, str]]) -> Iterator[BookmarkEvent]:
    """Yield every readable file's bookmarks; record each unreadable one.

    An active profile is the current state of a live browser, so an
    unreadable one refuses the build: publishing without it would read as
    deletions. An archived export is one historical observation among many,
    so it is recorded in ``unreadable`` (published as the manifest's
    ``unreadable_input_files``) and the rest of the product is built. Each
    file is parsed completely before any of its rows are yielded, so a file
    that fails midway contributes nothing rather than a partial tree.
    """
    # An active profile is named by its live-profile label (``chrome-ws``,
    # ``chrome-ws/Profile 1``), as its history is, rather than by its
    # directory name, which every browser's default profile shares.
    active_labels = {history.parent / "Bookmarks": label for history, label in discover_profile_history_dbs()}
    for path in paths:
        lower = path.name.lower()
        active_label = active_labels.get(path)
        active = active_label is not None
        try:
            if lower == "places.sqlite":
                rows = list(_firefox_places(path))
            elif lower.endswith(".jsonlz4"):
                rows = list(_firefox_backup(path))
            elif lower == "bookmarks.html":
                rows = list(_bookmarks_html(path))
            else:
                rows = list(_chromium_json(path, active_label=active_label))
        except (OSError, sqlite3.Error, ValueError) as exc:
            if active:
                raise MaterializationError("browser_bookmarks", reason=f"active profile bookmarks unreadable: {path}") from exc
            logger.warning("browser_bookmarks: skipping unreadable export %s: %s", path, exc)
            unreadable.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        yield from rows


def _chromium_json(path: Path, *, active_label: str | None = None) -> Iterator[BookmarkEvent]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    roots = payload.get("roots") if isinstance(payload, dict) else None
    if not isinstance(roots, dict):
        raise ValueError(f"bookmark roots missing: {path}")
    browser = _browser_from_path(path)
    profile = active_label or _profile_from_path(path)
    for name, node in roots.items():
        yield from _chromium_node(browser, profile, str(name), node, path, active=active_label is not None)


def _chromium_node(browser: str, profile: str, folder: str, node: object, path: Path, *, active: bool = False) -> Iterator[BookmarkEvent]:
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
            source="active_chromium_bookmarks" if active else "chromium_bookmarks",
            native_id=str(node.get("guid") or node.get("id") or ""),
        )
        return
    children = node.get("children")
    if not isinstance(children, list):
        return
    name = str(node.get("name") or folder)
    child_folder = folder if name == folder else f"{folder}/{name}"
    for child in children:
        yield from _chromium_node(browser, profile, child_folder, child, path, active=active)


def _firefox_places(path: Path) -> Iterator[BookmarkEvent]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for bookmark_id, guid, title, date_added, url, folder in conn.execute(_BOOKMARK_SQL):
            yield _event(
                browser="firefox",
                profile=_profile_from_path(path),
                url=str(url or ""),
                title=str(title or ""),
                folder=str(folder or ""),
                added_at=_unix_micros(date_added),
                source_path=path,
                source=f"firefox_places:{bookmark_id}",
                native_id=str(guid or ""),
            )
    finally:
        conn.close()


def _firefox_backup(path: Path) -> Iterator[BookmarkEvent]:
    raw = path.read_bytes()
    if raw.startswith(b"mozLz40\0"):
        try:
            import lz4.block
        except ImportError as exc:
            raise ValueError(f"lz4 is required to read {path}") from exc
        try:
            raw = lz4.block.decompress(raw[8:])
        except lz4.block.LZ4BlockError as exc:
            raise ValueError(f"corrupt mozLz4 block: {path}") from exc
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
            native_id=str(node.get("guid") or node.get("id") or ""),
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
    """Keep the first observation of each bookmark occurrence (``bookmark_id``)."""
    seen: set[str] = set()
    for row in rows:
        if not row.url:
            continue
        key = row.bookmark_id
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
    native_id: str = "",
) -> BookmarkEvent:
    norm = normalize_url(url)
    try:
        domain = (urlparse(url).hostname or "").lower()
    except ValueError:
        domain = ""
    # Occurrence identity: one bookmark object in one browser profile. The
    # file it was read from is not part of it, so repeated copies of one
    # profile (Bookmarks and Bookmarks.bak, successive Firefox backups, a
    # backup beside places.sqlite) collapse, while the same URL in another
    # folder, profile, or browser stays a distinct occurrence. The native
    # GUID identifies the object where the format has one; otherwise its
    # folder, exact URL, title, and added time do.
    if native_id:
        identity: tuple[str, ...] = ("native", browser, profile, native_id, url)
    else:
        identity = ("observed", browser, profile, folder, url, title, added_at.isoformat() if added_at else "")
    digest = hashlib.sha1(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
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
    # Firefox keeps its JSON backups in <profile>/bookmarkbackups/.
    if path.parent.name == "bookmarkbackups":
        return path.parent.parent.name
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
