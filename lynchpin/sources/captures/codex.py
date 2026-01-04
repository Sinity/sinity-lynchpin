from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

from ...core.cache import files_signature, persistent_cache
from ...core.config import get_config


@dataclass
class CodexSession:
    start: datetime
    source: Path


@dataclass
class _CodexSessionRow:
    start: datetime
    source: str


def _session_files(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.jsonl"))


def _signature_key(root: Optional[Path]) -> Tuple[str, Tuple[str, ...]]:
    base = root or get_config().codex_sessions_root
    return (str(base), files_signature(_session_files(base)))


@persistent_cache(
    "codex_sessions",
    depends_on=lambda root=None: _signature_key(root),
)
def _load_sessions(root: Optional[Path]) -> List[_CodexSessionRow]:
    sessions_root = (root or get_config().codex_sessions_root).expanduser()
    rows: List[_CodexSessionRow] = []
    for session_path in _session_files(sessions_root):
        try:
            with session_path.open("r", encoding="utf-8") as fh:
                first_line = fh.readline()
            if not first_line:
                continue
            meta = json.loads(first_line)
        except (OSError, json.JSONDecodeError):
            continue
        timestamp = (
            meta.get("timestamp")
            or meta.get("start")
            or meta.get("created_at")
        )
        if not timestamp:
            continue
        try:
            start = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        rows.append(_CodexSessionRow(start=start, source=str(session_path)))
    rows.sort(key=lambda row: row.start)
    return rows


def iter_sessions(
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    root: Optional[Path] = None,
) -> Iterator[CodexSession]:
    for session in _load_sessions(root):
        if start and session.start < start:
            continue
        if end and session.start >= end:
            continue
        yield CodexSession(start=session.start, source=Path(session.source))
