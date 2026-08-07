"""Read-only access to the canonical downloaded Substack archive."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Sequence

from ..core.config import get_config
from ..core.primitives import logical_date

__all__ = [
    "SubstackPost",
    "iter_raw_posts",
    "iter_posts",
    "substack_input_files",
    "substack_manifest_path",
    "substack_path",
]

_SUPPORTED_SUFFIXES = {".html", ".htm", ".md", ".markdown", ".txt"}
_FORMAT_PRIORITY = {"html": 0, "htm": 1, "md": 2, "markdown": 3, "txt": 4}


@dataclass(frozen=True)
class SubstackPost:
    """A normalized archived post with its raw source preserved."""

    publication: str
    source_publication: str
    slug: str
    title: str
    format: str
    published_at: datetime | None
    source_path: Path
    content: str
    content_sha256: str


def substack_path(root: Path | None = None) -> Path:
    """Return the derived canonical post index path."""
    del root
    return get_config().derived_root / "substack/posts.ndjson"


def substack_manifest_path(root: Path | None = None) -> Path:
    return substack_path(root).with_suffix(".manifest.json")


def substack_input_files(root: Path | None = None) -> tuple[Path, ...]:
    base = root or get_config().substack_root
    if not base.exists():
        return ()
    return tuple(
        sorted(
            path
            for path in base.rglob("*")
            if path.is_file()
            and path.suffix.lower() in _SUPPORTED_SUFFIXES
            and len(path.relative_to(base).parts) >= 2
            and path.relative_to(base).parts[0].casefold() != "sbstck-dl"
            and not any(part.startswith(".") for part in path.relative_to(base).parts)
        )
    )


def iter_raw_posts(
    root: Path | None = None,
    *,
    publications: Sequence[str] | None = None,
) -> Iterator[SubstackPost]:
    """Yield posts directly from downloaded files without materializing."""
    base = root or get_config().substack_root
    selected = {name.casefold() for name in publications} if publications else None
    for path in substack_input_files(base):
        relative = path.relative_to(base)
        if not relative.parts:
            continue
        source_publication = relative.parts[0]
        if source_publication.casefold() == "sbstck-dl":
            continue
        if selected and source_publication.casefold() not in selected:
            continue
        slug, published_at = _parse_filename(path.name)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        format_name = path.suffix.lower().lstrip(".")
        yield SubstackPost(
            publication=_publication_key(source_publication),
            source_publication=source_publication,
            slug=slug,
            title=_derive_title(content, slug, format_name),
            format=format_name,
            published_at=published_at,
            source_path=path,
            content=content,
            content_sha256=sha256(content.encode("utf-8")).hexdigest(),
        )


def iter_posts(
    path: Path | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    publications: Sequence[str] | None = None,
    ensure: bool = True,
) -> Iterator[SubstackPost]:
    """Yield canonical posts, optionally bounded by logical publication date."""
    target = path or substack_path()
    if path is None and ensure:
        from ..materialization import ensure_materialized

        ensure_materialized("substack")
    if not target.exists():
        raise FileNotFoundError(
            f"canonical Substack materialization is missing: {target}. "
            "Run python -m lynchpin.cli.substack materialize."
        )
    selected = {name.casefold() for name in publications} if publications else None
    with target.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            published_at = _parse_iso_datetime(payload.get("published_at"))
            if published_at is not None and (start is not None or end is not None):
                day = logical_date(published_at)
                if start is not None and day < start:
                    continue
                if end is not None and day >= end:
                    continue
            publication = str(payload.get("publication") or "")
            if selected and publication.casefold() not in selected:
                continue
            yield SubstackPost(
                publication=publication,
                source_publication=str(payload.get("source_publication") or publication),
                slug=str(payload.get("slug") or ""),
                title=str(payload.get("title") or ""),
                format=str(payload.get("format") or ""),
                published_at=published_at,
                source_path=Path(str(payload.get("source_path") or "")),
                content=str(payload.get("content") or ""),
                content_sha256=str(payload.get("content_sha256") or ""),
            )


def _publication_key(name: str) -> str:
    return name[:-3] if name.casefold().endswith("_md") else name


def _parse_filename(filename: str) -> tuple[str, datetime | None]:
    stem = Path(filename).stem
    parts = stem.split("_", 2)
    if len(parts) >= 3 and len(parts[0]) == 8 and len(parts[1]) == 6:
        try:
            return parts[2], datetime.strptime(parts[0] + parts[1], "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return stem, None


def _derive_title(content: str, slug: str, format_name: str) -> str:
    if format_name in {"html", "htm"}:
        parser = _TitleParser()
        parser.feed(content)
        if parser.heading:
            return parser.heading
        if parser.title:
            return parser.title
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return slug.replace("-", " ")


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_priority(format_name: str) -> int:
    """Return the stable preference order used by the materializer."""
    return _FORMAT_PRIORITY.get(format_name, 99)


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.heading = ""
        self.title = ""
        self._active: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"h1", "title"} and self._active is None:
            self._active = tag
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._active:
            return
        value = " ".join(" ".join(self._parts).split())
        if tag == "h1" and not self.heading:
            self.heading = value
        elif tag == "title" and not self.title:
            self.title = value
        self._active = None
        self._parts = []
