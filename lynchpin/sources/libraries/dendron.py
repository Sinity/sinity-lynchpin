from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import yaml

from ...core.config import get_config


@dataclass
class DendronNote:
    """Representation of a Dendron/Markdown note inside the knowledgebase."""

    path: Path  # Relative path from the vault root
    id: Optional[str]
    title: str
    tags: List[str]
    frontmatter: Dict[str, object]
    body: str


def iter_notes(root: Optional[Path] = None) -> Iterator[DendronNote]:
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


def _split_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
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


def _derive_title(frontmatter: Dict[str, object], body: str, rel: Path) -> str:
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


def _normalise_tags(frontmatter: Dict[str, object]) -> List[str]:
    tags = frontmatter.get("tags")
    if isinstance(tags, str):
        return [tag.strip() for tag in tags.split() if tag.strip()]
    if isinstance(tags, list):
        out: List[str] = []
        for tag in tags:
            if isinstance(tag, str) and tag.strip():
                out.append(tag.strip())
        return out
    return []


def _safe_str(value: object) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
