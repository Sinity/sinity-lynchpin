from __future__ import annotations

from pathlib import Path
from typing import Optional


def write_text_if_changed(path: Path, text: str, *, encoding: str = "utf-8") -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: Optional[str] = None
    if path.exists():
        try:
            existing = path.read_text(encoding=encoding)
        except OSError:
            existing = None
    if existing == text:
        return False
    path.write_text(text, encoding=encoding)
    return True


def write_bytes_if_changed(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: Optional[bytes] = None
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError:
            existing = None
    if existing == payload:
        return False
    path.write_bytes(payload)
    return True
