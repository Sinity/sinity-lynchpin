from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Sequence

from ..core.config import get_config


@dataclass
class SubstackPost:
    """Representation of an archived Substack post."""

    source: str
    path: Path
    published_at: Optional[datetime]
    slug: str
    title: str
    format: str
    content: str


def iter_posts(sources: Optional[Sequence[str]] = None) -> Iterator[SubstackPost]:
    """Iterate over every archived Substack post under `/realm/data/doc/substack/`."""

    cfg = get_config()
    root = cfg.substack_root
    if not root.exists():
        return iter(())

    selected = {name.lower() for name in sources} if sources else None

    def generator() -> Iterator[SubstackPost]:
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            name = entry.name
            if name in {"sbstck-dl"}:
                continue
            if selected and name.lower() not in selected:
                continue
            for post in _iter_source(entry):
                yield post

    return generator()


def _iter_source(directory: Path) -> Iterator[SubstackPost]:
    source = directory.name
    for path in sorted(directory.glob("**/*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".html", ".htm", ".md", ".markdown"}:
            continue
        slug, published_at = _parse_filename(path.name)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        title = _derive_title(text, slug, path.suffix.lower())
        yield SubstackPost(
            source=source,
            path=path,
            published_at=published_at,
            slug=slug,
            title=title,
            format=path.suffix.lower().lstrip("."),
            content=text,
        )


def _parse_filename(filename: str) -> tuple[str, Optional[datetime]]:
    """Best-effort parse of Substack filenames: `YYYYmmdd_HHMMSS_slug.ext`."""
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_", 2)
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        timestamp = f"{parts[0]}{parts[1]}"
        try:
            dt = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            dt = None
        slug = parts[2] if len(parts) > 2 else stem
        return slug, dt
    return stem, None


def _derive_title(text: str, slug: str, suffix: str) -> str:
    if suffix in {".html", ".htm"}:
        match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    lines: List[str] = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return slug.replace("-", " ")
