from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .config import get_config


@dataclass
class RepoDocument:
    """Generic Markdown document inside sinity-lynchpin worth surfacing to assistants."""

    path: Path
    title: str
    updated_at: datetime
    content: str


def read_analysis_log(limit_lines: int = 400) -> Optional[RepoDocument]:
    """Return the tail of docs/analysis-log.md so dashboards can embed it."""

    cfg = get_config()
    path = cfg.repo_root / "docs/analysis-log.md"
    return _read_markdown(path, limit_lines=limit_lines)


def read_backlog(limit_lines: int = 400) -> Optional[RepoDocument]:
    cfg = get_config()
    path = cfg.repo_root / "docs/backlog.md"
    return _read_markdown(path, limit_lines=limit_lines)


def iter_plan_docs(limit_lines: int = 200) -> Iterator[RepoDocument]:
    """Yield every Markdown plan under docs/plans/ (sorted by mtime desc)."""

    cfg = get_config()
    plans_root = cfg.repo_root / "docs/plans"
    if not plans_root.exists():
        return iter(())

    def generator() -> Iterator[RepoDocument]:
        plan_files = sorted(
            plans_root.glob("*.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in plan_files:
            doc = _read_markdown(path, limit_lines=limit_lines)
            if doc:
                yield doc

    return generator()


def _read_markdown(path: Path, limit_lines: int) -> Optional[RepoDocument]:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    if limit_lines > 0:
        lines = text.splitlines()
        text = "\n".join(lines[-limit_lines:])
    title = _first_heading(text) or path.stem
    updated = datetime.fromtimestamp(path.stat().st_mtime)
    return RepoDocument(path=path, title=title, updated_at=updated, content=text)


def _first_heading(text: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None
