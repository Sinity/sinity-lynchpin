"""Dendron-style Markdown note reader for the knowledgebase vault."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import yaml

from ..core.config import get_config

__all__ = [
    "DendronNote",
    "iter_dendron_notes",
    "search_notes",
    "read_note",
]


@dataclass(frozen=True)
class DendronNote:
    """Representation of a Dendron/Markdown note inside the knowledgebase."""

    path: Path
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
            frontmatter, body = _split_frontmatter(text)
            tags = _normalise_tags(frontmatter)
            title = _derive_title(frontmatter, body, rel)
            yield DendronNote(
                path=rel,
                id=_safe_str(frontmatter.get("id")),
                title=title,
                tags=tags,
                frontmatter=frontmatter,
                body=body,
            )

    return generator()


def search_notes(
    query: str,
    *,
    root: Optional[Path] = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, object]:
    """Search the owner note tree and return addressable, paged matches."""
    if offset < 0 or not 1 <= limit <= 200:
        raise ValueError("offset must be nonnegative and limit must be 1..200")
    vault = Path(root) if root is not None else get_config().dendron_root
    if not vault.is_dir():
        return {"source": "dendron", "status": "unavailable", "reason": "note root is unavailable", "total": 0, "notes": [], "omissions": []}
    needle = query.casefold()
    matches: list[dict[str, object]] = []
    omissions: list[dict[str, str]] = []
    for path in sorted(vault.rglob("*.md")):
        rel = path.relative_to(vault)
        if any(part.startswith(".") for part in rel.parts) or path.is_symlink() or not path.is_file():
            continue
        try:
            note = _read_note_file(path, rel)
            modified_at = path.stat().st_mtime_ns
        except (OSError, UnicodeError) as error:
            omissions.append({"path": rel.as_posix(), "reason": type(error).__name__})
            continue
        if needle not in f"{note.title}\n{note.id or ''}\n{' '.join(note.tags)}\n{note.body}".casefold():
            continue
        matches.append({"path": rel.as_posix(), "id": note.id, "title": note.title, "tags": note.tags,
                        "source_mtime_ns": modified_at})
    return {"source": "dendron", "status": "complete" if not omissions else "partial",
            "root": str(vault), "query": query, "offset": offset, "limit": limit,
            "total": len(matches), "next_offset": offset + limit if offset + limit < len(matches) else None,
            "notes": matches[offset:offset + limit], "omissions": omissions,
            "coverage": "current local note tree; no provider acquisition claim"}


def read_note(path: str, *, root: Optional[Path] = None) -> dict[str, object]:
    """Read one note by its relative path, retaining its local provenance."""
    vault = Path(root) if root is not None else get_config().dendron_root
    relative = Path(path)
    if relative.is_absolute() or not relative.parts or any(part in {".", ".."} or part.startswith(".") for part in relative.parts) or relative.suffix != ".md":
        raise ValueError("note path must be a visible relative Markdown path")
    file = vault / relative
    if any((vault / parent).is_symlink() for parent in (relative, *relative.parents)) or not file.resolve().is_relative_to(vault.resolve()) or not file.is_file():
        raise FileNotFoundError(path)
    note = _read_note_file(file, relative)
    return {"source": "dendron", "path": relative.as_posix(), "id": note.id,
            "title": note.title, "tags": note.tags, "frontmatter": note.frontmatter,
            "body": note.body, "source_mtime_ns": file.stat().st_mtime_ns,
            "coverage": "current local note tree; no provider acquisition claim"}


def _read_note_file(path: Path, relative: Path) -> DendronNote:
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return DendronNote(path=relative, id=_safe_str(frontmatter.get("id")),
                       title=_derive_title(frontmatter, body, relative),
                       tags=_normalise_tags(frontmatter), frontmatter=frontmatter, body=body)


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
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


def _derive_title(frontmatter: dict[str, object], body: str, rel: Path) -> str:
    for key in ("title", "id", "aliases"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    heading = _first_heading(body)
    if heading:
        return heading
    return rel.stem.replace("_", " ")


def _first_heading(body: str) -> Optional[str]:
    for line in body.splitlines():
        match = re.match(r"^\s*#+\s+(.*)", line)
        if match:
            return match.group(1).strip()
    return None


def _normalise_tags(frontmatter: dict[str, object]) -> list[str]:
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


def _safe_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
