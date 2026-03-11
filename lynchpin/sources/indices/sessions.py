from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config


@dataclass
class SessionRecord:
    date: date
    provider: str
    label: str
    doc_path: str
    highlights: str


@persistent_cache(
    "session_records",
    depends_on=lambda: files_signature(_session_doc_paths(get_config().session_docs_dir)),
)
def iter_sessions() -> Iterator[SessionRecord]:
    cfg = get_config()
    sessions_dir = cfg.session_docs_dir
    if not sessions_dir.exists():
        return iter(())

    def generator() -> Iterator[SessionRecord]:
        for path in _session_doc_paths(sessions_dir):
            record = parse_session_file(path)
            yield SessionRecord(
                date=record.date,
                provider=record.provider,
                label=record.label,
                doc_path=str(record.doc_path),
                highlights=(record.highlights[0] if record.highlights else ""),
            )

    return generator()


def sessions_by_date(target: date) -> List[SessionRecord]:
    return [record for record in iter_sessions() if record.date == target]


@dataclass
class SessionDocument:
    date: date
    provider: str
    label: str
    doc_path: Path
    source_files: List[str]
    highlights: List[str]
    next_actions: List[str]
    last_modified: datetime


def iter_session_documents() -> Iterator[SessionDocument]:
    cfg = get_config()
    return iter_session_documents_from(cfg.session_docs_dir)


def iter_session_documents_from(root: Path) -> Iterator[SessionDocument]:
    if not root.exists():
        return iter(())

    def generator() -> Iterator[SessionDocument]:
        for path in _session_doc_paths(root):
            yield parse_session_file(path)

    return generator()


def parse_session_file(path: Path) -> SessionDocument:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("#")), path.stem)

    parts = path.stem.split("-")
    date_str = "-".join(parts[:3]) if len(parts) >= 3 else ""
    provider = parts[-1] if len(parts) >= 1 else "unknown"

    sections = _parse_markdown_sections(text)
    source_files = _extract_bullets(sections.get("Source Files", []))
    highlights = _extract_bullets(sections.get("Highlights", []))
    next_actions = _extract_bullets(sections.get("Next Actions", []))

    stat = path.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)

    return SessionDocument(
        date=date.fromisoformat(date_str),
        provider=provider,
        label=title,
        doc_path=path,
        source_files=source_files,
        highlights=highlights,
        next_actions=next_actions,
        last_modified=last_modified,
    )


def _session_doc_paths(root: Path) -> List[Path]:
    return sorted(
        path
        for path in root.glob("*.md")
        if not path.name.startswith(".") and path.name.lower() != "readme.md"
    )


def _parse_markdown_sections(text: str) -> Dict[str, List[str]]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return sections


def _extract_bullets(lines: List[str]) -> List[str]:
    bullets: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("#", "```")):
            break
        if stripped.startswith(("- ", "* ")):
            bullets.append(_clean_inline(stripped[2:].strip()))
        elif stripped[:2].isdigit() and stripped[2:].lstrip().startswith("."):
            content = stripped.split(".", 1)[-1].strip()
            bullets.append(_clean_inline(content))
        elif stripped:
            if bullets:
                bullets[-1] = f"{bullets[-1]} {stripped}"
            else:
                bullets.append(_clean_inline(stripped))
    return [bullet for bullet in (bullet.strip() for bullet in bullets) if bullet]


def _clean_inline(value: str) -> str:
    cleaned = value.replace("`", "").replace("*", "")
    return " ".join(cleaned.split())
