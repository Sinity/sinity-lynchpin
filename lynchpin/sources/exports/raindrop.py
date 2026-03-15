from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from ...core.config import get_config


@dataclass
class RaindropBookmark:
    id: int
    title: str
    url: str
    folder: str
    tags: List[str]
    created: Optional[datetime]
    note: str
    excerpt: str
    cover: Optional[str]
    favorite: bool
    raw: dict


@dataclass(frozen=True)
class RaindropExport:
    label: str
    path: Path
    mtime: datetime
    is_default: bool


def list_exports(root: Optional[Path] = None) -> List[RaindropExport]:
    cfg = get_config()
    base = Path(root) if root else cfg.raindrop_dir
    if not base.exists():
        return []
    exports: List[RaindropExport] = []
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


def iter_bookmarks(csv_path: Optional[Path] = None) -> Iterator[RaindropBookmark]:
    cfg = get_config()
    target = Path(csv_path) if csv_path else cfg.raindrop_csv
    if not target or not target.exists():
        return iter(())

    def generator() -> Iterator[RaindropBookmark]:
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                tags = _parse_tags(row.get("tags"))
                created = _parse_datetime(row.get("created"))
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
                    cover=_strip(row.get("cover")),
                    favorite=favorite,
                    raw=row,
                )

    return generator()


def iter_bookmarks_by_name(name: str, root: Optional[Path] = None) -> Iterator[RaindropBookmark]:
    """Iterate bookmarks for exports whose filenames contain the given token."""
    token = name.lower()
    for export in list_exports(root):
        if token in export.label.lower():
            yield from iter_bookmarks(export.path)


def iter_bookmarks_all(root: Optional[Path] = None) -> Iterator[Tuple[RaindropExport, RaindropBookmark]]:
    """Iterate all exports, yielding (export, bookmark) pairs."""
    for export in list_exports(root):
        for bookmark in iter_bookmarks(export.path):
            yield export, bookmark


def summarize_bookmarks(
    start_month: str,
    end_month: str,
    *,
    csv_path: Optional[Path] = None,
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for bookmark in iter_bookmarks(csv_path):
        if bookmark.created is None:
            continue
        month = f"{bookmark.created.year:04d}-{bookmark.created.month:02d}"
        if start_month <= month <= end_month:
            counts[month] += 1
    return dict(counts)


def _parse_tags(raw: Optional[str]) -> List[str]:
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


def _parse_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
