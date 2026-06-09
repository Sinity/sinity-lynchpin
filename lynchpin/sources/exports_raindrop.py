"""Raindrop bookmark export reader."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.coverage import CoverageBounds
from ..core.parse import parse_datetime
from ..core.primitives import logical_date

__all__ = [
    "RaindropBookmark",
    "RaindropExport",
    "RaindropDayActivity",
    "list_raindrop_exports",
    "iter_raindrop_bookmarks",
    "iter_raindrop_bookmarks_by_name",
    "iter_raindrop_bookmarks_all",
    "summarize_raindrop_bookmarks",
    "daily_raindrop_activity",
    "coverage_bounds",
]


@dataclass(frozen=True)
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


@dataclass(frozen=True)
class RaindropDayActivity:
    date: date
    bookmarks_added: int
    unique_tags: int


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


def iter_raindrop_bookmarks(
    csv_path: Optional[Path] = None,
    *,
    start: date | None = None,
    end: date | None = None,
    ensure: bool = True,
) -> Iterator[RaindropBookmark]:
    """Iterate Raindrop bookmarks, optionally bounded by half-open logical dates."""
    cfg = get_config()
    canonical = cfg.exports_root / "raindrop/processed/bookmarks.csv"
    if csv_path is None:
        if ensure:
            from ..materialization import ensure_materialized

            ensure_materialized("raindrop", window=(start, end) if start and end else None)
        target = canonical
    else:
        target = Path(csv_path)
    if not target or not target.exists():
        raise FileNotFoundError(
            f"canonical Raindrop materialization is missing: {target}. "
            "Run python -m lynchpin.ingest.exports_materialize raindrop."
        )

    def generator() -> Iterator[RaindropBookmark]:
        with target.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                tags = _parse_tags(row.get("tags"))
                created = parse_datetime(row.get("created"))
                if created is not None and (start is not None or end is not None):
                    d = logical_date(created)
                    if start is not None and d < start:
                        continue
                    if end is not None and d >= end:
                        continue
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


def daily_raindrop_activity(*, start: date, end: date, ensure: bool = True) -> list[RaindropDayActivity]:
    """Daily bookmark additions."""

    if ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("raindrop", window=(start, end))

    by_date: dict[date, tuple[int, set[str]]] = defaultdict(lambda: (0, set()))
    for bookmark in iter_raindrop_bookmarks(start=start, end=end, ensure=False):
        if bookmark.created is None:
            continue
        d = logical_date(bookmark.created)
        count, tags = by_date[d]
        tags.update(bookmark.tags)
        by_date[d] = (count + 1, tags)
    return sorted(
        [RaindropDayActivity(date=d, bookmarks_added=count, unique_tags=len(tags)) for d, (count, tags) in by_date.items()],
        key=lambda x: x.date,
    )


def coverage_bounds() -> CoverageBounds | None:
    """Return observed date range from the raindrop materialization manifest."""
    cfg = get_config()
    manifest_path = cfg.exports_root / "raindrop/processed/bookmarks.manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    first_raw = manifest.get("first_date")
    last_raw = manifest.get("last_date")
    if not first_raw or not last_raw:
        return None
    return CoverageBounds(
        source="raindrop",
        first=date.fromisoformat(first_raw),
        last=date.fromisoformat(last_raw),
        kind="export",
    )


def _parse_tags(raw: Optional[str]) -> list[str]:
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


def _strip(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
