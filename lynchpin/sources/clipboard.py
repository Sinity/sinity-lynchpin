"""Clipboard history source.

Reads the live Clipse history file and archived exports. Clipboard entries are
high-signal retrospective evidence, so values are preserved verbatim with light
metadata instead of being summarized at source level.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from ..core.config import get_config
from ..core.parse import as_local

__all__ = [
    "ClipboardEntry",
    "source_files",
    "entries",
    "entries_in_range",
]


@dataclass(frozen=True)
class ClipboardEntry:
    recorded_at: datetime
    value: str
    source: str
    file_path: str | None
    pinned: bool

    @property
    def date(self) -> date:
        return self.recorded_at.date()

    @property
    def kind(self) -> str:
        if self.file_path and self.file_path != "null":
            return "file"
        if self.value.startswith("http://") or self.value.startswith("https://"):
            return "url"
        if self.value.startswith("📷"):
            return "image"
        return "text"


def source_files() -> list[Path]:
    cfg = get_config()
    paths = [cfg.clipboard_live_file, *cfg.clipboard_export_files]
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        result.append(path)
    return result


def entries(*, paths: Optional[tuple[Path, ...]] = None) -> Iterator[ClipboardEntry]:
    seen: set[tuple[str, str]] = set()
    for path in paths or tuple(source_files()):
        for entry in _entries_from_file(path):
            key = (entry.recorded_at.isoformat(), entry.value)
            if key in seen:
                continue
            seen.add(key)
            yield entry


def entries_in_range(*, start: date, end: date, paths: Optional[tuple[Path, ...]] = None) -> list[ClipboardEntry]:
    return [entry for entry in entries(paths=paths) if start <= entry.date <= end]


@lru_cache(maxsize=4)
def _entries_from_file(path: Path) -> tuple[ClipboardEntry, ...]:
    if path.suffix.lower() == ".md":
        return tuple(_entries_from_markdown(path))
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ()
    rows = payload.get("clipboardHistory") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return ()
    result: list[ClipboardEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = _parse_recorded(row.get("recorded"))
        if stamp is None:
            continue
        value = str(row.get("value") or "")
        if not value:
            continue
        file_path = row.get("filePath")
        result.append(ClipboardEntry(
            recorded_at=stamp,
            value=value,
            source=str(path),
            file_path=None if file_path in (None, "", "null") else str(file_path),
            pinned=bool(row.get("pinned", False)),
        ))
    result.sort(key=lambda item: item.recorded_at)
    return tuple(result)


def _entries_from_markdown(path: Path) -> Iterator[ClipboardEntry]:
    text = path.read_text(encoding="utf-8", errors="replace")
    generated = None
    match = re.search(r"^generated:\s*([^\n]+)", text, flags=re.MULTILINE)
    if match:
        generated = _parse_recorded(match.group(1))
    stamp = generated or as_local(datetime.fromtimestamp(path.stat().st_mtime))
    for match in re.finditer(r"```(?:[a-zA-Z0-9_-]+)?\n(.*?)\n```", text, flags=re.DOTALL):
        value = match.group(1).strip()
        if value:
            yield ClipboardEntry(
                recorded_at=stamp,
                value=value,
                source=str(path),
                file_path=None,
                pinned=False,
            )


def _parse_recorded(value: object) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return as_local(datetime.fromisoformat(raw))
    except ValueError:
        return None
